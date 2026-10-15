"""RunContext: what an agent uses to call the LLM, tools and the consent service, with record and replay.

On Vercel a run may need several function calls: it stops when it asks the didi for consent, or when its
time budget runs out, and continues in a later call. Every LLM call, tool call and consent result is
recorded (store.run_steps) under a sequence number. When the run continues, the agent's code runs again
from the top; steps already recorded return their recorded result instantly, so the code reaches the
point where it stopped and carries on. Agents stay plain Python loops.

Events follow the same rule: the nth event of a run is written once; a replay skips the ones it wrote.
Transient events (rate-limit waits) go only to the live stream.
"""
import hashlib
import json
import re
import time

import settings
from core import store
from llm import nvidia

from . import facts


class Pause(Exception):
    """Stop this call. reason: 'consent' (waiting for her answer) or 'time' (budget used; continue next call)."""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


class RunContext:
    def __init__(self, run, turn, session, workspace, history, sink=None):
        self.run, self.turn, self.session, self.workspace = run, turn, session, workspace
        self.didi, self.prefs = session['didi'], session['prefs']
        self.language = self.didi.get('language') or 'hi'
        # In pivot mode the agent works in English and Bhashini translates the question and the answer.
        self.llm_language = 'en' if self.prefs.get('language_mode') == 'pivot' and self.language != 'en' else self.language
        self.question = turn['question']
        self.history = history  # [(question, answer), ...] earlier in this login; never fetched records
        self.sink = sink
        self.recorded = store.recorded_steps(run['id'])
        self.seq = 0
        self.events_written = store.event_count(run['id'])
        self.event_seq = 0
        self.deadline = time.monotonic() + settings.RUN_BUDGET
        # consent_prompts counts the prompts this agent needed, including ones answered earlier in the turn
        # (consent_reused), so compare runs are measured fairly.
        self.metrics = dict(llm_calls=0, tool_calls=0, consent_prompts=0, consent_reused=0, fields_read=[], prompt_tokens=0,
                            completion_tokens=0, llm_ms=0, cached_llm_calls=0)
        self.evidence = []      # [{source_id, table, rows, fields, consent_id, ref}] for the answer's chips
        self.fetched = {}       # ref -> rows, held only for this run (in memory and the checkpoint)
        self.sources = {s['id']: s for s in store.list_sources(workspace)}
        self.known = set()      # every number fetched or calculated in this run, for the self-check
        self.summaries = []     # exact totals of each fetched table, labelled, for the self-check

    # --- events --------------------------------------------------------------------------------

    def emit(self, type_, transient=False, **data):
        if transient:
            if self.sink:
                self.sink({'id': None, 'turn_id': self.turn['id'], 'run_id': self.run['id'], 'type': type_, 'data': data})
            return
        self.event_seq += 1
        if self.event_seq <= self.events_written:
            return
        row = store.add_event(self.turn['id'], self.run['id'], self.event_seq, type_, data)
        if row and self.sink:
            self.sink(row)

    # --- recorded steps ------------------------------------------------------------------------

    @property
    def replaying(self):
        return (self.seq + 1) in self.recorded

    def step(self, kind, request, do):
        """Run `do()` once per run; on replay return what it returned the first time."""
        self.seq += 1
        digest = hashlib.sha256(json.dumps(request, sort_keys=True, default=str).encode()).hexdigest()[:32]
        recorded = self.recorded.get(self.seq)
        if recorded and recorded['digest'] == digest:
            return recorded['response']
        if recorded:  # the code took a different path than last time (e.g. it was redeployed): redo from here
            for seq in [s for s in self.recorded if s >= self.seq]:
                self.recorded.pop(seq)
        response = do()
        store.record_step(self.run['id'], self.seq, kind, digest, response)
        return response

    # --- LLM -----------------------------------------------------------------------------------

    def llm(self, messages, tools=None, max_tokens=900, temperature=0.2, model=None, stop=None):
        model = model or self.run['model']
        if not self.replaying and time.monotonic() > self.deadline - 10:
            raise Pause('time')
        key = hashlib.sha256(json.dumps([model, messages, tools, stop], sort_keys=True).encode()).hexdigest()

        def do():
            if self.prefs.get('demo_cache'):
                cached = store.cache_get(key)
                if cached:
                    return {**cached, 'cached': True}
            try:
                result = nvidia.chat(model, messages, tools=tools, max_tokens=max_tokens, temperature=temperature,
                                     deadline=self.deadline, on_wait=self._waiting, stop=stop)
            except nvidia.LlmError as error:
                cached = store.cache_get(key) if error.code in ('rate_limited', 'unavailable', 'timeout') else None
                if not cached:
                    raise
                return {**cached, 'cached': True}
            store.cache_put(key, model, result)
            return result

        result = self.step('llm', {'model': model, 'messages': messages, 'tools': tools, 'stop': stop}, do)
        usage = result.get('usage') or {}
        self.metrics['llm_calls'] += 1
        self.metrics['prompt_tokens'] += usage.get('prompt_tokens') or 0
        self.metrics['completion_tokens'] += usage.get('completion_tokens') or 0
        self.metrics['llm_ms'] += result.get('ms') or 0
        self.metrics['cached_llm_calls'] += 1 if result.get('cached') else 0
        self.emit('llm', model=model, ms=result.get('ms'), tokens=usage.get('total_tokens'), cached=bool(result.get('cached')),
                  tool_calls=[c['function']['name'] for c in result['message'].get('tool_calls') or []])
        return result['message']

    def _waiting(self, seconds):
        self.emit('status', transient=True, hi='थोड़ा रुकिए…', en=f'NVIDIA API is busy; retrying in {seconds:.0f} s')

    # --- consent and records --------------------------------------------------------------------

    def consent(self, items, purpose_id):
        """Ask her permission for `items` [{source_id, table, fields}]. Pauses the run until she answers.

        Two recorded steps: preparing the request (which creates it once) and her decision. The request event
        is emitted between them on every pass, so replays count it the same way.
        """
        from consent import ledger
        prep_seq = self.seq + 1

        def prepare():
            refused = ledger.refused_tables(self.session['id'])
            keep = [i for i in items if i['table'] not in refused]
            dropped = sorted({i['table'] for i in items if i['table'] in refused})
            if not keep:
                return {'mode': 'refused_before', 'dropped': dropped}
            reuse = ledger.covering(self.turn['id'], keep)
            if reuse:
                return {'mode': 'reuse', 'consent_id': reuse['id'], 'dropped': dropped}
            consent_id = 'con_' + hashlib.sha1(f'{self.run["id"]}:{prep_seq}'.encode()).hexdigest()[:12]
            stored, statement = ledger.build(self.sources, keep, purpose_id, self.language)
            ledger.ensure(consent_id, self, stored, purpose_id, statement)
            return {'mode': 'ask', 'consent_id': consent_id, 'dropped': dropped, 'items': stored, 'statement': statement}

        prep = self.step('consent_prep', {'items': items, 'purpose': purpose_id}, prepare)
        note = (f'She refused {", ".join(prep["dropped"])} earlier in this conversation; it was not asked again. '
                'Answer without it.') if prep['dropped'] else ''
        if prep['mode'] == 'refused_before':
            self.emit('consent_skipped', registers=prep['dropped'], position=self.run['position'])
            return {'decision': 'denied', 'granted': [], 'note': note}
        consent_id = prep['consent_id']
        self.metrics['consent_prompts'] += 1
        if prep['mode'] == 'reuse':
            self.metrics['consent_reused'] += 1
        if prep['mode'] == 'ask':
            self.emit('consent_request', consent_id=consent_id, purpose={'id': purpose_id, **{
                lang: catalog_purpose(purpose_id, lang) for lang in ('hi', 'en')}},
                items=[{'source_id': i['source_id'], 'table': i['table'], 'fields': i['fields'], 'register': i['register'],
                        'labels': i['labels']} for i in prep['items']],
                statement=prep['statement'], language=self.language, position=self.run['position'],
                agent=self.run['agent'])

        def decided():
            row = store.get_consent(consent_id)
            if row['status'] == 'pending':
                raise Pause('consent')
            return ledger.result(row)

        result = self.step('consent', {'consent_id': consent_id}, decided)
        self.emit('consent_result', consent_id=consent_id, decision=result['decision'], reused=prep['mode'] == 'reuse',
                  summary=result.get('summary'), refused=result.get('refused_registers'), position=self.run['position'])
        return {**result, 'note': note} if note else result

    def fetch(self, source_id, table, fields, consent_id):
        """Read her rows through the gateway. Values stay in this run only."""
        import gateway
        from consent import ledger

        def read():
            try:
                return {'rows': gateway.fetch(self, source_id, table, fields, consent_id)}
            except gateway.Blocked as blocked:
                return {'blocked': blocked.code, 'error': blocked.message}

        result = self.step('tool', {'tool': 'fetch_records', 'source': source_id, 'table': table, 'fields': fields,
                                    'consent_id': consent_id}, read)
        self.metrics['tool_calls'] += 1
        args = {'source': source_id, 'table': table, 'fields': fields, 'consent_id': consent_id}
        if 'rows' not in result:
            self.emit('tool_call', tool='fetch_records', args=args, summary=f'blocked: {result["error"]}', blocked=result['blocked'])
            return {'error': result['error']}
        source = self.sources[source_id]
        ref = f'ev-{len(self.evidence) + 1}'
        names = {lang: ledger.register(source, table, lang) for lang in ('hi', 'en')}
        self.fetched[ref] = result['rows']
        # Her rows and their exact totals (yearly, monthly, per cycle) count as known figures for the self-check.
        totals = {k: v for k, v in facts.summarise(table, result['rows']).items() if k != 'rows'}
        self.summaries.append({'register': names['en'], **totals})
        self.known |= facts.numbers(result['rows']) | facts.numbers(totals)
        self.metrics['fields_read'] += [f'{table}.{f}' for f in fields]
        self.evidence.append({'ref': ref, 'source_id': source_id, 'table': table, 'register': names,
                              'rows': len(result['rows']), 'fields': fields, 'consent_id': consent_id})
        self.emit('tool_call', tool='fetch_records', args=args, summary=f'{len(result["rows"])} rows from {names["en"]}',
                  register=names, rows=len(result['rows']), ref=ref)
        return {'register': names['en'], 'count': len(result['rows']), 'exact_totals': totals, 'rows': result['rows']}

    def table_source(self, table):
        """The confirmed source that holds `table` for this didi (the users' source first)."""
        import gateway
        ordered = sorted(self.sources.values(), key=lambda s: (s['setup'].get('role') != 'primary', s['created_at']))
        for source in ordered:
            if source['setup'].get('confirmed') and table in source['tables']:
                try:
                    gateway.scope(source, table, self.didi)
                    return source['id']
                except gateway.Blocked:
                    continue
        return None

    def decide(self, decision_id, consent_id):
        """Run a decision table on her consented records. Code derives every input; the LLM only explains."""
        import decisions
        try:
            spec = decisions.describe(decision_id)
        except decisions.DecisionError as error:
            return {'error': str(error)}
        rows = {}
        for need in spec['needs']:
            source_id = self.table_source(need['table'])
            if not source_id:
                return {'error': f'No connected source has the {need["table"]} table for her.'}
            result = self.fetch(source_id, need['table'], need['fields'], consent_id)
            if 'error' in result:
                return {'error': f'{need["table"]}: {result["error"]}'}
            rows[need['table']] = result['rows']

        def run():
            try:
                return decisions.evaluate(decision_id, decisions.inputs_from_rows(decision_id, rows))
            except (decisions.DecisionError, ValueError) as error:
                return {'error': str(error)}

        result = self.tool('decide', {'decision_id': decision_id, 'consent_id': consent_id}, run,
                           summary=lambda r: r.get('error') or f'{r["outcome"]} (rule {", ".join(map(str, r["matched_rules"])) or "none"}, v{r["version"]})')
        if 'error' not in result:
            rule = ', '.join(map(str, result['matched_rules'])) or '–'
            self.metrics['decision'] = f'{decision_id}: {result["outcome"]}'
            self.emit('decision', decision_id=decision_id, outcome=result['outcome'], rules=result['matched_rules'],
                      version=result['version'], inputs=result['inputs'],
                      step={'hi': f'{spec["name"]["hi"]} का नियम लगाया (नियम {rule})', 'en': f'Applied the {spec["name"]["en"]} rule (rule {rule})'})
        return result

    def build_plan(self, enterprise, consent_id, changes=None):
        """Read the plan's registers with her consent and build the workbook. Returns the summary for the agent."""
        from plans import service, templates
        if enterprise not in templates.TEMPLATES:
            return {'error': f'No plan template for {enterprise!r}. Templates: {", ".join(templates.TEMPLATES)}.'}
        rows, problems = {}, []
        for need in templates.needs(enterprise):
            source_id = self.table_source(need['table'])
            result = self.fetch(source_id, need['table'], need['fields'], consent_id) if source_id else {'error': 'not connected'}
            if 'error' in result:
                problems.append(f'{need["table"]}: {result["error"]}')
                rows[need['table']] = []
            else:
                rows[need['table']] = result['rows']
        if not any(rows.values()):
            return {'error': 'None of her registers could be read for the plan. ' + ' '.join(problems)}
        read_at = store.now().isoformat(timespec='seconds')
        result = self.tool('build_business_plan', {'enterprise': enterprise, 'consent_id': consent_id, 'changes': changes or {}},
                           lambda: service.build(self, enterprise, rows, changes, consent_id, read_at),
                           summary=lambda r: f'{r["name"]}: profit {r["results"]["profit_now"]} → {r["results"]["profit_after"]}, '
                                             f'instalment {r["results"]["emi"]}, payback {r["results"]["payback_months"]} months')
        if problems:
            result = {**result, 'could_not_read': problems}
        self.metrics['plan'] = result['name']
        self.emit('file', file_id=result['file_id'], name=result['name'], kind='business_plan', enterprise=result['enterprise'],
                  results=result['results'], position=self.run['position'])
        return result

    # --- tools (no consent needed) -------------------------------------------------------------

    def tool(self, name, args, fn, summary=None):
        """A recorded tool call. `fn()` must be deterministic given its args (schema reads, arithmetic, decisions)."""
        result = self.step('tool', {'tool': name, 'args': args}, fn)
        self.metrics['tool_calls'] += 1
        if name in ('calculate', 'summarise', 'decide', 'build_business_plan'):
            self.known |= facts.numbers(result)
        self.emit('tool_call', tool=name, args=args, summary=summary(result) if summary else _short(result))
        return result


def catalog_purpose(purpose_id, lang):
    from consent import ledger
    return ledger.purpose_text(purpose_id, lang)


def _short(value, limit=240):
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= limit else text[:limit] + '…'


_THINK = re.compile(r'<think>.*?</think>', re.S)
_MARKDOWN = re.compile(r'(\*\*|__|^#+\s*|^\s*[-*]\s+)', re.M)


def clean_answer(text):
    """Text to show and speak: no reasoning tags, no markdown markers."""
    text = _THINK.sub('', text or '')
    return _MARKDOWN.sub('', text).strip()
