"""Plan-and-Execute: a planner lists every field the answer needs, one consent card asks for all of them,
code fetches and summarises (exact totals, so nobody adds numbers in their head), and a writer answers.

Two LLM calls for a records question, one for a general one. Fewest interruptions for the didi.
"""
import json

import settings
import tools

from . import earnings, facts, projection, prompts, wallet
from .context import clean_answer
from .writer import plain_answer, write

NAME = 'Plan-and-Execute'

PLANNER = """You plan how to answer the didi's question. Do not answer it yourself.
Reply with JSON only:
{"follow_up": null or "<one short question to ask her first, in her language and its own script (Devanagari for Hindi), even if she typed in English letters>",
 "needs_records": true or false,
 "decision": null or "<one of: %s>",
 "business_plan": null or "<one of: dairy, kirana, tailoring>",
 "projection": false, true (her savings growing) or "earnings" (what a business would earn),
 "plan_changes": {"<input name>": <number>, ...},
 "purpose": "<one of: %s>",
 "items": [{"source": "<source id>", "table": "<table>", "fields": ["<field>", ...]}],
 "scheme_topics": ["<zero or more of: %s>"],
 "wallet": null or {"action": "list" | "share" | "save_plan", "kind": null or "<one of: %s>", "verifier": null or "<one of: %s>"},
 "steps": ["<short step>", ...]}

If she asks about her wallet or certificates (प्रमाणपत्र): to see them, wallet.action "list"; to send one to someone
("लखपति दीदी का प्रमाणपत्र बैंक को भेजो"), "share" with the kind and the verifier; to keep her business plan as a
certificate ("मेरा बिज़नेस प्लान वॉलेट में रखो"), "save_plan". Set needs_records false and items []: code asks her
and reads what is needed. Otherwise wallet is null.
List in "items" every field the answer needs and nothing more; she will be asked once for all of them.
Most questions need one table and two to four fields. Never add a table "just in case": asking for more than
the answer needs is a privacy failure.
If the question is about Lakhpati status or whether the SHG is ready for a bank loan, set "decision" to that
decision table; its fields are added for you, and the table (not you) decides the outcome.
If she asks for a business plan, set "business_plan" to the enterprise type; the plan's fields are added for you
and its inputs come from her registers. Put in "plan_changes" only a number she said herself (e.g. "दो गाय और" ->
{"cows_to_add": 2}); otherwise leave it {}. Never fill in sales, costs, savings, interest or tenure yourself.
If she asks how her savings would grow ("हर महीने 100 और जमा करूँ तो एक साल बाद कितनी होगी?", "₹20,000 तक कब
पहुँचेगी?"), set "projection" to true and purpose to "savings_question"; the savings fields are added for you, and code
works out the figures. Do not ask her a follow-up for it.
If she asks how much she could earn or make from a business, new or existing ("नया व्यवसाय शुरू करूँ तो एक साल में
कितना कमा सकती हूँ?", "दुकान से 6 महीने में कितनी कमाई होगी?"), that is not a savings question: set "projection" to
"earnings", needs_records to false and items to []; code asks her for the expected sales and costs and charts them.
Do not ask her a follow-up for it yourself. If she is answering that question about sales and costs, plan "earnings" again.
If the question is general and needs none of her records, set needs_records to false and items to [].
Never ask her for something her registers can show (her land, savings, loans, income, wages): plan to read it
instead. Land, irrigation and crops are in the AgriStack farmer record; wage days in the VB-G RAM G record.
Use the conversation so far. If her request is too vague to know what she needs (for example "मुझे पैसे
चाहिए": for what, how much?), set "follow_up" to one short, kind question in her language, set needs_records
to false and items to []. If she is answering your earlier follow-up, plan from her answer; do not ask again.
Questions about what something is or how a scheme works ("what is Panchsutra?", "what is a Lakhpati Didi?")
are general: answer them from scheme_topics, never from her registers.

When the question clearly specifies what data is needed (e.g., "monthly profit", "wages last year", "EMI amount"), do NOT ask a follow-up question. Instead, plan the exact tables and minimal fields required to answer. For income questions, use the dar table (income, expense fields). For wages questions, use the wages_vbg table (wages_paid, month fields). For loan EMI questions, use only the loans table fields directly asked for (emi, and at most loan_id, amount, outstanding, overdue_days, purpose, source) — do not include disbursed_on, rate_pct, or tenure_months unless she explicitly asks for them. For enterprise asset questions (cows/machines), use the enterprise table (units, unit_label).

**Critical Rules for Specific Keywords:**
- **"राम जी योजना" or "RAM G" or "wages"**: This refers to the VB-G RAM G wage scheme. Map to **purpose: "wages_question"**, **table: "wages_vbg"**, **fields: ["month", "wages_paid"]** (add "wages_pending" only if pending is implied). **Do NOT use savings or dar tables.**
- **"किस्त" or "EMI" or "loan instalment"**: Map to **purpose: "loan_question"**, **table: "loans"**, **fields: ["emi", "overdue_days", "outstanding"]** (minimal set). Do NOT set decision to bank_linkage_readiness unless she explicitly asks "is my SHG ready for a bank loan?".
- **"बचत" or "savings"**: Map to **purpose: "savings_question"**, **table: "savings"**, **fields: ["month", "amount"]** (add "cumulative" only if total savings asked).
- **"आय" or "income" or "profit"**: Map to **purpose: "income_question"**, **table: "dar"**, **fields: ["month", "income", "expense"]**.
- **"क्या मेरी कोई किस्त चूकी हुई है?" or "overdue"**: This is a loan_question needing loans table (overdue_days, outstanding, emi), NOT a bank_linkage_readiness decision.

**Privacy Enforcement:** Only fetch fields strictly required to answer the specific question. Fetching wages_vbg for a savings question, or savings for a wages question, is a privacy failure.

**General/Scheme Questions (CRITICAL):** If the question asks for a definition, explanation, or how a scheme works (e.g., "बैंक लिंकेज क्या होता है?", "पंचसूत्र क्या है?", "लखपति दीदी क्या है?"), this is a GENERAL QUESTION. You MUST:
- Set `needs_records: false`
- Set `items: []`
- Set `purpose` to "other_question"
- Set `scheme_topics` to include that topic
- Set `decision: null` (NEVER set decision to bank_linkage_readiness or lakhpati_status for definition questions)
- Answer from scheme_topics ONLY — reading her records for a definition question is a privacy failure.

**Field Minimization Rules (from feedback):**
- For farm questions (land/irrigation/crops), use agristack table with ONLY the fields needed: "irrigated" for irrigation status, add "crop_kharif", "crop_rabi", "land_acres" only if specifically asked. NEVER include "farmer_id" — it is never needed to answer her question.
- For savings questions: use "month", "amount" only; add "cumulative" ONLY if she asks for total savings.
- For wages questions: use "month", "wages_paid"; add "wages_pending" ONLY if pending/overdue is implied.
- For loan questions: use "emi", "overdue_days", "outstanding" as minimal set; never include disbursed_on, rate_pct, tenure_months unless explicitly asked.
- For income questions: use "month", "income", "expense" from dar table.

**Enterprise Expense Questions:** When she asks about business expenses ("धंधे में खर्च", "business expenses", "monthly cost"), this is an **enterprise_question**. Use the **enterprise** table with fields **"monthly_cost"** (and "units", "unit_label" if needed for context). Do NOT use the dar table for expense-only questions — dar is for income/expense pairs when she asks about profit or both income and expense together. Fetching dar for an expense-only question is a privacy failure.

**Decision Table Usage (CRITICAL):** Only set `decision` to `bank_linkage_readiness` or `lakhpati_status` when she EXPLICITLY asks:
- "Is my SHG ready for a bank loan?" / "क्या मेरा समूह बैंक लिंकेज के लिए तैयार है?"
- "Am I a Lakhpati Didi?" / "क्या मैं लखपति दीदी हूँ?"
NEVER set decision for: loan balance questions, EMI questions, overdue questions, savings questions, or general scheme explanations. Those are `loan_question`, `savings_question`, etc. with their own tables.
Her registers (source id, table, fields):
%s"""

WRITER = """
Answer her question in 2 to 5 short spoken sentences, with no lists, headings or line breaks. Continue the
conversation: use what she said in earlier turns, and you may end with one short question about a next step. Use the figures
in the facts below exactly as they are and do not work out new totals; say which register they came from.
For a general question, use the scheme notes in the facts and what you know about NRLM, simply.
If something could not be read (she refused, or it was blocked), say kindly what you could not check.
Her permission has already been given and her records have already been read: never ask for permission or
say you need information. If a register shows no rows for her, say so simply.
Never mention table names, field names, tool names, or words like "facts", "registers: decision" or "JSON".

Facts:
%s"""


def run(ctx, review=None, on_no_plan=None):
    """review(ctx, plan, planner_prompt) -> plan: a second look at the plan before anything is asked (plan_review).
    on_no_plan(ctx) -> answer: what to do when no valid plan came back twice (hybrid); default, answer without records."""
    from consent import catalog
    from wallet import service as wallet_service
    intent = wallet.route(ctx) if settings.WALLET_ENABLED else None
    if intent:
        return wallet.answer(ctx, intent)  # her wallet: code reads the intent, asks her and words the answer
    planner = PLANNER % (', '.join(tools.DECISIONS), ', '.join(catalog.PURPOSES), ', '.join(catalog.SCHEME_INFO),
                         ', '.join(wallet_service.KINDS), ', '.join(wallet_service.VERIFIERS), tools.schema_text(ctx))
    history = [m for q, a in ctx.history for m in ({'role': 'user', 'content': q}, {'role': 'assistant', 'content': a})]
    plan, problem = None, ''
    if ctx.prefs.get('planner') == 'laya':
        from . import laya_planner
        raw = laya_planner.plan(ctx, ctx.history)
        if raw:
            plan, problem = _check(ctx, raw)
            if not plan:
                ctx.emit('status', transient=True, hi='', en=f'Laya plan rejected, the LLM plans instead: {problem.strip()}')
    for _ in range(0 if plan else 2):
        text = ctx.llm([{'role': 'system', 'content': planner}, *history,
                        {'role': 'user', 'content': ctx.question + problem}], max_tokens=600, temperature=0)['content']
        plan, problem = _parse(ctx, text)
        if plan:
            break
    if not plan and on_no_plan:
        return on_no_plan(ctx)
    if not plan:
        plan = {'needs_records': False, 'items': [], 'scheme_topics': [], 'steps': ['Answer without records']}
    elif review:
        plan = review(ctx, plan, planner)
    if plan.get('wallet'):
        return wallet.answer(ctx, plan['wallet'])
    if plan.get('follow_up') and not plan['items'] and not plan.get('decision') and not plan.get('business_plan'):
        ctx.emit('plan', steps=['Ask her a follow-up question first'], needs_records=False, follow_up=plan['follow_up'])
        return clean_answer(plan['follow_up'])  # one short question; her next turn continues from it
    if plan.get('projection') == 'earnings':
        ctx.emit('plan', steps=plan.get('steps') or ['Work out the business earnings'], needs_records=False, items=[],
                 purpose=plan.get('purpose'))
        return earnings.answer(ctx, store_month())  # her own figures, no registers: computed, charted and worded by code
    ctx.emit('plan', steps=plan.get('steps') or [], needs_records=bool(plan['items']), decision=plan.get('decision'),
             items=[{'table': i['table'], 'fields': i['fields']} for i in plan['items']], purpose=plan.get('purpose'))
    found = execute(ctx, plan)
    if plan.get('projection') and not found['could_not_read']:
        rows = next((ctx.fetched[e['ref']] for e in ctx.evidence if e['table'] == 'savings'), None)
        text = projection.answer(ctx, rows) if rows else None
        if text:
            return text  # the extractor's scenario, computed and worded by code (and charted), or its follow-up
    if found.get('business_plan') and not found['could_not_read']:
        ctx.worded_by_code = True
        return plan_answer(ctx, found['business_plan'])  # the plan's own figures, worded by code: nothing to garble
    if found.get('decision') and not found['registers'] and not found['could_not_read']:
        ctx.worded_by_code = True
        return decision_answer(ctx, found['decision'])  # the table's outcome and reasons, worded by code
    writer = prompts.system(ctx.didi, ctx.llm_language, WRITER % json.dumps(found, ensure_ascii=False, default=str, indent=1))
    ctx.code_fallback = lambda: plain_answer(ctx, found['registers'])  # gate C's last resort before a wrong answer
    return write(ctx, writer, history, ctx.code_fallback)


def _parse(ctx, text):
    try:
        start = text.index('{')
        plan = json.loads(text[start:text.rindex('}') + 1])
    except ValueError:
        return None, '\n\n(Your last reply was not valid JSON. Reply with the JSON plan only.)'
    return _check(ctx, plan)


def _check(ctx, plan):
    """Validate a plan (from the LLM or Laya) and add the fields a decision table or business plan reads."""
    from consent import catalog
    items = []
    # A business plan or projection reads its own fields (added below), so a planner's extra items must not block it.
    if plan.get('needs_records') and plan.get('items') and plan.get('business_plan') not in tools.PLAN_TYPES \
            and plan.get('projection') not in (True, 'earnings'):
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
    plan['wallet'] = wallet.merge(ctx, plan.get('wallet')) if settings.WALLET_ENABLED else None
    if plan.get('projection') == 'earnings' and not plan['business_plan'] and not plan['decision']:
        plan.update(projection='earnings', items=[], needs_records=False, follow_up=None, purpose='other_question')
        return plan, ''
    plan['projection'] = plan.get('projection') is True and not plan['business_plan'] and not plan['decision'] \
        and bool(ctx.table_source('savings'))
    if plan['projection']:  # only her savings register, whatever else the planner listed
        needed, problem = tools._items(ctx, [{'source': ctx.table_source('savings'), 'table': 'savings', 'fields': projection.FIELDS}])
        if problem:
            return None, f'\n\n(That projection cannot run: {problem} Plan without it.)'
        plan.update(items=needed, purpose='savings_question', needs_records=True, follow_up=None)
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
    if plan['items'] and not plan['decision'] and not plan['business_plan'] and not plan['projection'] \
            and ctx.prefs.get('verify', 'code') != 'off':
        from verify import plan as gate_a
        plan['items'], _ = gate_a.gate(ctx, plan['items'], plan['purpose'])  # registers she asked about, no ids, allowed
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
    """The answer for a built plan, from its results. Hindi, or English for the pivot translation. Says plainly when
    the business is new, when the change does not raise her profit, and when the instalment is more than it adds."""
    from plans import templates
    r, a = plan['results'], plan['assumptions']
    name = str(ctx.didi.get('name_local' if ctx.llm_language == 'hi' else 'name') or '').split(' ')[0]
    rupees = lambda n: '₹' + indian(abs(n))  # noqa: E731
    kind = next(k for k, t in templates.TEMPLATES.items() if t['name']['en'] == plan['enterprise']['en'])
    new = not (a.get(templates.TEMPLATES[kind]['now']) or {}).get('value')
    added = next((a[k]['value'] for k in ('cows_to_add', 'machines_to_add') if k in a), None)
    short = r['extra_after_emi'] < 0
    if ctx.llm_language == 'hi':
        change = {'dairy': f'{added} गाय और जोड़ने', 'tailoring': f'{added} मशीन और जोड़ने', 'kirana': 'नया माल लाने'}[kind]
        start = {'dairy': f'{added} गाय से डेयरी शुरू करने', 'tailoring': f'{added} मशीन से सिलाई शुरू करने',
                 'kirana': 'नई किराना दुकान खोलने'}[kind]
        text = f'{name} जी, आपके {plan["enterprise"]["hi"]} का प्लान तैयार है। '
        if new:
            text += f'अभी आपका यह काम नहीं है; {start} पर हर महीने लगभग {rupees(r["profit_after"])} मुनाफ़ा होगा, किस्त से पहले'
        elif r['extra_profit'] <= 0:
            text += (f'अभी हर महीने मुनाफ़ा {rupees(r["profit_now"])} है; {change} के बाद यह बढ़ता नहीं, '
                     f'{rupees(r["profit_after"])} रहेगा')
        else:
            text += f'अभी हर महीने मुनाफ़ा {rupees(r["profit_now"])} है; {change} के बाद {rupees(r["profit_after"])} होगा'
        text += f'। इसके लिए {rupees(r["investment"])} लगेंगे'
        if r['loan_amount']:
            text += (f': {rupees(a["own_contribution"]["value"])} आपकी बचत से और {rupees(r["loan_amount"])} कर्ज़, जिसकी किस्त '
                     f'{rupees(r["emi"])} हर महीने {a["tenure_months"]["value"]} महीने तक होगी।')
            text += (f' किस्त बढ़े हुए मुनाफ़े से ज़्यादा है: किस्त चलने तक हर महीने {rupees(r["extra_after_emi"])} कम पड़ेंगे, '
                     'इसलिए इस प्लान पर अपने समूह या सीआरपी से बात करके ही आगे बढ़िए।' if short else
                     f' किस्त के बाद भी हर महीने {rupees(r["extra_after_emi"])} ज़्यादा बचेंगे।')
        else:
            text += '।'
        if r.get('payback_months') and not short:
            text += f' लागत लगभग {r["payback_months"]} महीनों में वापस आ जाएगी।'
        return text + ' इस प्लान की लागतें नकली (mock) हैं। पूरा प्लान Excel फ़ाइल में है, आप उसे डाउनलोड कर सकती हैं।'
    change = {'dairy': f'adding {added} cows', 'tailoring': f'adding {added} machines', 'kirana': 'the new stock'}[kind]
    start = {'dairy': f'starting a dairy with {added} cows', 'tailoring': f'starting tailoring with {added} machines',
             'kirana': 'opening a new kirana shop'}[kind]
    text = f'{name}, your {plan["enterprise"]["en"].lower()} plan is ready. '
    if new:
        text += f'You do not run this business now; {start} would make about {rupees(r["profit_after"])} a month before the instalment'
    elif r['extra_profit'] <= 0:
        text += f'Your monthly profit is {rupees(r["profit_now"])} now, and {change} does not raise it: it would be {rupees(r["profit_after"])}'
    else:
        text += f'Your monthly profit is {rupees(r["profit_now"])} now and will be {rupees(r["profit_after"])} after {change}'
    text += f'. It needs {rupees(r["investment"])}'
    if r['loan_amount']:
        text += (f': {rupees(a["own_contribution"]["value"])} from your savings and a loan of {rupees(r["loan_amount"])}, '
                 f'with an instalment of {rupees(r["emi"])} a month for {a["tenure_months"]["value"]} months.')
        text += (f' The instalment is more than the plan adds: you would be {rupees(r["extra_after_emi"])} short each month '
                 'while repaying, so talk it over with your SHG or CRP before going ahead.' if short else
                 f' After the instalment you keep {rupees(r["extra_after_emi"])} more each month.')
    else:
        text += '.'
    if r.get('payback_months') and not short:
        text += f' The cost comes back in about {r["payback_months"]} months.'
    return text + ' The costs in this plan are mock figures. The full plan is in the Excel file you can download.'


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


def store_month():
    """This month, 'YYYY-MM': a new business's chart starts next month."""
    from core import store
    return store.now().strftime('%Y-%m')
