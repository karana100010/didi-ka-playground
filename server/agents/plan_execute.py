"""Plan-and-Execute: a planner lists every field the answer needs, one consent card asks for all of them,
code fetches and summarises (exact totals, so nobody adds numbers in their head), and a writer answers.

Two LLM calls for a records question, one for a general one. Fewest interruptions for the didi.
"""
import json

import tools

from . import facts, prompts
from .context import clean_answer

NAME = 'Plan-and-Execute'

PLANNER = """You plan how to answer the didi's question. Do not answer it yourself.
Reply with JSON only:
{"follow_up": null or "<one short question to ask her first, in her language and its own script (Devanagari for Hindi), even if she typed in English letters>",
 "needs_records": true or false,
 "decision": null or "<one of: %s>",
 "business_plan": null or "<one of: dairy, kirana, tailoring>",
 "plan_changes": {"<input name>": <number>, ...},
 "purpose": "<one of: %s>",
 "items": [{"source": "<source id>", "table": "<table>", "fields": ["<field>", ...]}],
 "scheme_topics": ["<zero or more of: %s>"],
 "steps": ["<short step>", ...]}

List in "items" every field the answer needs and nothing more; she will be asked once for all of them.
Most questions need one table and two to four fields. Never add a table "just in case": asking for more than
the answer needs is a privacy failure.
If the question is about Lakhpati status or whether the SHG is ready for a bank loan, set "decision" to that
decision table; its fields are added for you, and the table (not you) decides the outcome.
If she asks for a business plan, set "business_plan" to the enterprise type and put anything she said about it
in "plan_changes" (e.g. {"cows_to_add": 2}); the plan's fields are added for you.
If the question is general and needs none of her records, set needs_records to false and items to [].
Use the conversation so far. If her request is too vague to know what she needs (for example "मुझे पैसे
चाहिए": for what, how much?), set "follow_up" to one short, kind question in her language, set needs_records
to false and items to []. If she is answering your earlier follow-up, plan from her answer; do not ask again.
Questions about what something is or how a scheme works ("what is Panchsutra?", "what is a Lakhpati Didi?")
are general: answer them from scheme_topics, never from her registers.
Her registers (source id, table, fields):
%s"""

WRITER = """
Answer her question in 2 to 5 short spoken sentences, with no lists, headings or line breaks. Continue the
conversation: use what she said in earlier turns, and you may end with one short question about a next step. Use the figures
in the facts below exactly as they are and do not work out new totals; say which register they came from.
For a general question, use the scheme notes in the facts and what you know about NRLM, simply.
If something could not be read (she refused, or it was blocked), say kindly what you could not check.
Never mention table names, field names, tool names, or words like "facts", "registers: decision" or "JSON".

Facts:
%s"""


def run(ctx):
    from consent import catalog
    planner = PLANNER % (', '.join(tools.DECISIONS), ', '.join(catalog.PURPOSES), ', '.join(catalog.SCHEME_INFO),
                         tools.schema_text(ctx))
    history = [m for q, a in ctx.history for m in ({'role': 'user', 'content': q}, {'role': 'assistant', 'content': a})]
    plan, problem = None, ''
    for _ in range(2):
        text = ctx.llm([{'role': 'system', 'content': planner}, *history,
                        {'role': 'user', 'content': ctx.question + problem}], max_tokens=600, temperature=0)['content']
        plan, problem = _parse(ctx, text)
        if plan:
            break
    if not plan:
        plan = {'needs_records': False, 'items': [], 'scheme_topics': [], 'steps': ['Answer without records']}
    if plan.get('follow_up') and not plan['items'] and not plan.get('decision') and not plan.get('business_plan'):
        ctx.emit('plan', steps=['Ask her a follow-up question first'], needs_records=False, follow_up=plan['follow_up'])
        return clean_answer(plan['follow_up'])  # one short question; her next turn continues from it
    ctx.emit('plan', steps=plan.get('steps') or [], needs_records=bool(plan['items']), decision=plan.get('decision'),
             items=[{'table': i['table'], 'fields': i['fields']} for i in plan['items']], purpose=plan.get('purpose'))
    found = execute(ctx, plan)
    if found.get('business_plan') and not found['could_not_read']:
        return plan_answer(ctx, found['business_plan'])  # the plan's own figures, worded by code: nothing to garble
    if found.get('decision') and not found['registers'] and not found['could_not_read']:
        return decision_answer(ctx, found['decision'])  # the table's outcome and reasons, worded by code
    writer = prompts.system(ctx.didi, ctx.llm_language, WRITER % json.dumps(found, ensure_ascii=False, default=str, indent=1))
    answer = ctx.llm([{'role': 'system', 'content': writer}, *history, {'role': 'user', 'content': ctx.question}])['content']
    return clean_answer(answer)


def _parse(ctx, text):
    try:
        start = text.index('{')
        plan = json.loads(text[start:text.rindex('}') + 1])
    except ValueError:
        return None, '\n\n(Your last reply was not valid JSON. Reply with the JSON plan only.)'
    from consent import catalog
    items = []
    if plan.get('needs_records') and plan.get('items'):
        items, problem = tools._items(ctx, plan['items'])
        if problem:
            return None, f'\n\n(Your last plan had a problem: {problem} Fix it and reply with the JSON plan only.)'
    plan['items'] = items or []
    plan['purpose'] = plan.get('purpose') if plan.get('purpose') in catalog.PURPOSES else 'other_question'
    plan['scheme_topics'] = [t for t in plan.get('scheme_topics') or [] if t in catalog.SCHEME_INFO]
    plan['follow_up'] = str(plan.get('follow_up') or '').strip()[:300] or None
    plan['decision'] = plan.get('decision') if plan.get('decision') in tools.DECISIONS else None
    plan['business_plan'] = plan.get('business_plan') if plan.get('business_plan') in tools.PLAN_TYPES else None
    plan['plan_changes'] = plan.get('plan_changes') if isinstance(plan.get('plan_changes'), dict) else {}
    if plan['business_plan']:
        from plans import templates
        wanted = [{'source': ctx.table_source(n['table']), 'table': n['table'], 'fields': n['fields']}
                  for n in templates.needs(plan['business_plan']) if ctx.table_source(n['table'])]
        needed, problem = tools._items(ctx, wanted)
        if problem:
            return None, f'\n\n(That plan cannot be built: {problem} Plan without it.)'
        plan['items'] = needed  # only what the plan reads
        plan['purpose'] = 'business_plan'
    if plan['decision']:  # the table's own fields replace whatever the planner listed for those tables
        described = tools.describe_decision(ctx, plan['decision'])
        if 'error' in described:
            return None, f'\n\n(That decision cannot run: {described["error"]} Plan without it.)'
        needed, _ = tools._items(ctx, described['consent_items'])
        plan['items'] = needed if not plan['business_plan'] else plan['items']  # only what the table reads
        plan['purpose'] = described['purpose'] if not plan['business_plan'] else plan['purpose']
    return plan, ''


def execute(ctx, plan):
    """Consent once, fetch what she allowed, summarise. Returns the writer's facts."""
    from consent import catalog
    found = {'schemes': dict(catalog.SCHEME_INFO), 'registers': [], 'could_not_read': []}  # short; always given
    if not plan['items']:
        return found
    consent = ctx.consent(plan['items'], plan['purpose'])
    if consent.get('note'):
        found['could_not_read'].append(consent['note'])
    if consent['decision'] not in ('granted', 'partial'):
        found['could_not_read'].append('She did not give permission to read her registers for this question.')
        return found
    decided_tables = set()
    if plan.get('business_plan'):
        result = ctx.build_plan(plan['business_plan'], consent['consent_id'], plan.get('plan_changes'))
        if 'error' in result:
            found['could_not_read'].append(f'business plan: {result["error"]}')
        else:
            found['business_plan'] = {k: v for k, v in result.items() if k != 'file_id'}
            from plans import templates
            decided_tables |= {n['table'] for n in templates.needs(plan['business_plan'])}
    if plan.get('decision'):
        result = ctx.decide(plan['decision'], consent['consent_id'])
        if 'error' in result:
            found['could_not_read'].append(f'decision {plan["decision"]}: {result["error"]}')
        else:
            found['decision'] = {k: result[k] for k in ('decision_id', 'version', 'outcome', 'outcome_label', 'matched_rules',
                                                         'findings', 'reasons', 'basis', 'inputs')}
            found['decision']['how_to_use'] = 'This outcome comes from the official decision table. Explain it; do not change it.'
            decided_tables = {n['table'] for n in decisions_needs(plan['decision'])}
    for item in plan['items']:
        if item['table'] in decided_tables:
            continue
        granted = next((g for g in consent['granted'] if g['source_id'] == item['source_id'] and g['table'] == item['table']), None)
        fields = [f for f in item['fields'] if granted and f in granted['fields']]
        if not fields:
            found['could_not_read'].append(f'{item["table"]}: not allowed by her')
            continue
        keep = [f for f in ('month', 'cycle_no') if f in ctx.sources[item['source_id']]['tables'][item['table']] and f not in fields]
        result = ctx.fetch(item['source_id'], item['table'], fields + keep if keep and _granted_all(granted, keep) else fields,
                           consent['consent_id'])
        if 'error' in result:
            found['could_not_read'].append(f'{item["table"]}: {result["error"]}')
            continue
        summary = ctx.tool('summarise', {'table': item['table']}, lambda r=result, t=item['table']: facts.summarise(t, r['rows']),
                           summary=lambda s: f'{s["rows"] if isinstance(s["rows"], int) else len(s["rows"])} rows summarised')
        found['registers'].append({'register': result['register'], **summary})
    return found


def decisions_needs(decision_id):
    import decisions
    return decisions.describe(decision_id)['needs']


def _granted_all(granted, fields):
    return granted and all(f in granted['fields'] for f in fields)


def plan_answer(ctx, plan):
    """The answer for a built plan, from its results. Hindi, or English for the pivot translation."""
    r, a = plan['results'], plan['assumptions']
    name = str(ctx.didi.get('name_local' if ctx.llm_language == 'hi' else 'name') or '').split(' ')[0]
    rupees = lambda n: '₹' + indian(n)  # noqa: E731
    added = next((a[k]['value'] for k in ('cows_to_add', 'machines_to_add') if k in a), None)
    if ctx.llm_language == 'hi':
        change = {'dairy': f'{added} गाय और जोड़ने', 'tailoring': f'{added} मशीन और जोड़ने', 'kirana': 'नया माल लाने'}
        kind = next(k for k in change if plan['enterprise']['en'].lower().startswith(k[:4]))
        text = (f'{name} जी, आपके {plan["enterprise"]["hi"]} का प्लान तैयार है। अभी हर महीने मुनाफ़ा {rupees(r["profit_now"])} है; '
                f'{change[kind]} के बाद {rupees(r["profit_after"])} होगा। इसके लिए {rupees(r["investment"])} लगेंगे')
        if r['loan_amount']:
            text += (f': {rupees(a["own_contribution"]["value"])} आपकी बचत से और {rupees(r["loan_amount"])} कर्ज़, जिसकी किस्त '
                     f'{rupees(r["emi"])} हर महीने {a["tenure_months"]["value"]} महीने तक होगी। किस्त के बाद भी हर महीने '
                     f'{rupees(r["extra_after_emi"])} ज़्यादा बचेंगे')
        if r.get('payback_months'):
            text += f', और लागत लगभग {r["payback_months"]} महीनों में वापस आ जाएगी'
        return text + '। इस प्लान की लागतें नकली (mock) हैं। पूरा प्लान Excel फ़ाइल में है, आप उसे डाउनलोड कर सकती हैं।'
    text = (f'{name}, your {plan["enterprise"]["en"].lower()} plan is ready. Your monthly profit is {rupees(r["profit_now"])} now '
            f'and will be {rupees(r["profit_after"])} after the change. It needs {rupees(r["investment"])}')
    if r['loan_amount']:
        text += (f': {rupees(a["own_contribution"]["value"])} from your savings and a loan of {rupees(r["loan_amount"])}, '
                 f'with an instalment of {rupees(r["emi"])} a month for {a["tenure_months"]["value"]} months. After the '
                 f'instalment you keep {rupees(r["extra_after_emi"])} more each month')
    if r.get('payback_months'):
        text += f', and the cost comes back in about {r["payback_months"]} months'
    return text + '. The costs in this plan are mock figures. The full plan is in the Excel file you can download.'


def indian(number):
    from decisions.engine import indian as group
    return group(number)


def decision_answer(ctx, d):
    """The answer for a decision table's result, in Hindi (or English for the pivot translation)."""
    hi = ctx.llm_language == 'hi'
    name = str(ctx.didi.get('name_local' if hi else 'name') or '').split(' ')[0]
    lang = 'hi' if hi else 'en'
    reasons = ' '.join(r[lang] for r in d['reasons'])
    label = d['outcome_label'].get(lang, d['outcome'])
    inputs = d['inputs']
    if d['decision_id'] == 'lakhpati_status':
        income = (f'आपके डिजिटल आजीविका रजिस्टर के अनुसार पिछले 12 महीनों में आपके परिवार की आमदनी ₹{indian(inputs["annual_income"])} रही, '
                  f'यानी हर महीने औसतन ₹{indian(inputs["avg_monthly_income"])}। ') if hi else (
                  f'Your Digital Aajeevika Register shows a household income of ₹{indian(inputs["annual_income"])} over the last '
                  f'12 months, about ₹{indian(inputs["avg_monthly_income"])} a month. ')
        verdict = f'नियम के अनुसार आप "{label}" हैं।' if hi else f'By the rule, you are "{label}".'
        return f'{name} {"जी, " if hi else ", "}{income}{reasons} {verdict}'
    verdict = f'नियम के अनुसार आपका समूह "{label}" है।' if hi else f'By the rule, your SHG is "{label}".'
    return f'{name} {"जी, " if hi else ", "}{verdict} {reasons}'.strip()
