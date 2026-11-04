"""ReWOO: a planner writes every step with placeholders (#E1, #E2.income), the tools run with no LLM in
between, and a solver answers from the evidence. Two LLM calls, but no adapting mid-way."""
import json
import re

import tools

from . import facts, prompts
from .context import clean_answer
from .writer import asks_instead_of_consent, plain_answer, write

NAME = 'ReWOO'

PLANNER = """Plan every step needed to answer the didi's question. Do not answer it. Reply with JSON only:
{"steps": [{"id": "E1", "tool": "<tool>", "args": {...}, "why": "<short>"}, ...]}

Tools and arguments:
- request_consent {"purpose": "<one of: %s>", "items": [{"source": "<source id>", "table": "<table>", "fields": [...]}]}
  Ask once, for every field you will need.
- fetch_records {"consent_id": "#E1", "source": "<source id>", "table": "<table>", "fields": [...]}
- calculate {"expression": "sum(income) - sum(expense)", "variables": {"income": "#E2.income", "expense": "#E2.expense"}}
  "#E2.income" means the income column of step E2's rows; "#E2.income[-12:]" means its last 12 values.
- scheme_info {"topic": "<one of: %s>"}
- decide {"decision_id": "<lakhpati_status or bank_linkage_readiness>", "consent_id": "#E1"}
  For Lakhpati status or bank-linkage readiness, ask consent for the fields listed under the decision below, then
  decide; the table decides the outcome. Lakhpati needs dar: month, income, expense, cycle_no. Bank-linkage
  readiness needs meetings: date, held, savings_collected, loans_given, repayments_due, repayments_made, books_updated.
Use "#E1" wherever a later step needs the consent id from step E1.
If the question needs none of her records, reply {"steps": []}.

Her registers (source id, table, fields):
%s"""

SOLVER = """
Answer her question using only the evidence below. Any consent shown was already given and the records were
already read: never ask for permission again. The figures are exact; use them as they are. Say which
register they came from. If a step failed or she refused, say kindly what you could not check.

Plan and evidence:
%s"""

_REF = re.compile(r'^#(E\d+)(?:\.([A-Za-z_][A-Za-z0-9_]*))?(?:\[(-?\d*):(-?\d*)\])?$')


REPLAN = ('\n\n(Your last plan read none of her records, so the answer asked her permission in words. She cannot '
          'answer that: the app shows her a consent card only for a request_consent step. Plan request_consent for '
          'the fields the answer needs, then fetch_records. Reply with the JSON plan only.)')
NO_EVIDENCE = """
None of her records were read for this question. Never state any figure about her (savings, income, loans, land)."""


def run(ctx):
    from consent import catalog
    planner = PLANNER % (', '.join(catalog.PURPOSES), ', '.join(catalog.SCHEME_INFO), tools.schema_text(ctx))
    history = [m for q, a in ctx.history for m in ({'role': 'user', 'content': q}, {'role': 'assistant', 'content': a})]
    note = ''
    for attempt in range(2):
        answer = _plan_and_solve(ctx, planner, history, note)
        if attempt or not asks_instead_of_consent(ctx, answer):
            return answer
        # A small model sometimes plans nothing and then asks her in words; plan again, for the consent card.
        ctx.emit('writer_rejected', reason='asked for permission instead of requesting consent', sample=answer[:200])
        note = REPLAN


def _plan_and_solve(ctx, planner, history, note):
    steps = None
    for _ in range(2):
        text = ctx.llm([{'role': 'system', 'content': planner}, *history, {'role': 'user', 'content': ctx.question + note}],
                       max_tokens=800, temperature=0)['content']
        steps = _steps(text)
        if steps is not None:
            break
        note = '\n\n(Your last reply was not valid JSON. Reply with the JSON plan only.)'
    steps = steps or []
    ctx.emit('plan', steps=[f'{s.get("id")}: {s.get("tool")} — {s.get("why", "")}' for s in steps])
    evidence = {}
    for step in steps[:8]:
        args = _substitute(step.get('args') or {}, evidence)
        result = tools.call(ctx, step.get('tool', ''), args)
        if step.get('tool') == 'fetch_records' and 'rows' in result:
            result = {**result, 'summary': ctx.tool('summarise', {'table': args.get('table')},
                                                    lambda r=result, t=args.get('table'): facts.summarise(t, r['rows']),
                                                    summary=lambda s: 'summarised')}
        evidence[str(step.get('id'))] = result
    _fetch_what_was_granted(ctx, steps, evidence)
    shown = [{'step': s.get('id'), 'tool': s.get('tool'), 'why': s.get('why'),
              'result': _brief(evidence.get(str(s.get('id'))))} for s in steps[:8]]
    solver = prompts.system(ctx.didi, ctx.llm_language, SOLVER % json.dumps(shown, ensure_ascii=False, default=str, indent=1)
                            + ('' if ctx.fetched else NO_EVIDENCE))
    registers = [{'register': r.get('register', ''), **r['summary']} for r in evidence.values()
                 if isinstance(r, dict) and isinstance(r.get('summary'), dict)]
    return write(ctx, solver, history, lambda: plain_answer(ctx, registers))


def _steps(text):
    """The plan's steps, or None if the reply was not valid JSON."""
    try:
        data = json.loads(text[text.index('{'):text.rindex('}') + 1])
    except ValueError:
        return None
    return [s for s in data.get('steps') or [] if isinstance(s, dict)] if isinstance(data, dict) else None


def _substitute(value, evidence):
    if isinstance(value, dict):
        return {k: _substitute(v, evidence) for k, v in value.items()}
    if isinstance(value, list):
        return [_substitute(v, evidence) for v in value]
    if isinstance(value, str):
        match = _REF.match(value.strip())
        if match:
            result = evidence.get(match.group(1)) or {}
            if not match.group(2):
                return result.get('consent_id', result.get('result', value))
            column = [r.get(match.group(2)) for r in result.get('rows', []) if r.get(match.group(2)) is not None]
            if match.group(3) or match.group(4):
                start = int(match.group(3)) if match.group(3) else None
                stop = int(match.group(4)) if match.group(4) else None
                column = column[start:stop]
            return column
    return value


def _brief(result):
    """Evidence for the solver: summaries instead of every row."""
    if not isinstance(result, dict):
        return result
    if 'rows' in result:
        return {k: v for k, v in result.items() if k != 'rows'}
    return result


def _fetch_what_was_granted(ctx, steps, evidence):
    """If the plan asked for consent but never read the tables, read what she allowed, so the solver answers from
    her records instead of guessing."""
    fetched = {(a.get('source'), a.get('table')) for s in steps if s.get('tool') == 'fetch_records' for a in [s.get('args') or {}]}
    for step in steps:
        result = evidence.get(str(step.get('id'))) or {}
        if step.get('tool') != 'request_consent' or result.get('decision') not in ('granted', 'partial'):
            continue
        for grant in result.get('granted') or []:
            if (grant['source_id'], grant['table']) in fetched:
                continue
            read = ctx.fetch(grant['source_id'], grant['table'], grant['fields'], result['consent_id'])
            if 'rows' in read:
                read = {**read, 'summary': ctx.tool('summarise', {'table': grant['table']},
                                                    lambda r=read, t=grant['table']: facts.summarise(t, r['rows']),
                                                    summary=lambda s: 'summarised')}
            evidence[f'auto:{grant["table"]}'] = read
            steps.append({'id': f'auto:{grant["table"]}', 'tool': 'fetch_records', 'why': 'read what she allowed'})
