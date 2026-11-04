"""The Laya planner: Plan-and-Execute's planning step done by the local Laya classifier instead of the LLM.

Laya (convaiinnovations/laya) answers typed questions about her message: what kind of request it is, which
register answers it and, for a business plan, which enterprise. Code turns those labels into the same plan the
LLM planner writes (purpose, tables, fields), so consent, fetching, decision tables and the writer are unchanged.

Laya is a zero-shot classifier and its "lakhpati" label attracts unrelated messages, so a decision or a business
plan is only taken when Laya is very sure; otherwise the register label decides. Measured on 20 Oct 2026 on the
demo questions: savings, income, loans, land, wages, Lakhpati, bank readiness and plans all planned correctly.
"""
import re

from llm import laya

QUESTIONS = {
    'intent': {'type': 'choice', 'instructions': 'What does she want?', 'criteria': {
        'meaning': 'what a scheme or word means (क्या होता है)',
        'my_numbers': 'her own savings, income, loans, land or wages',
        'lakhpati': 'whether she has become a Lakhpati Didi',
        'bank_loan_ready': 'whether her group is ready for a bank loan',
        'business_plan': 'a business plan',
        'needs_money': 'she needs money or a loan (पैसे चाहिए)',
        'help': 'greeting or asking for help'}},
    'register': {'type': 'choice', 'instructions': 'Which of her records would answer this?', 'criteria': {
        'savings': 'savings, money saved, बचत', 'income': 'income and expenses, आमदनी, कमाई',
        'loans': 'loans, debt, instalments, कर्ज़, लोन', 'land': 'land, farm, crops, ज़मीन, खेती',
        'wages': 'wage work days and wages, मज़दूरी', 'enterprise': 'her business or enterprise, धंधा',
        'none': 'no personal record needed'}},
    'enterprise': {'type': 'choice', 'instructions': 'Which enterprise is it about?', 'criteria': {
        'dairy': 'cows, buffaloes, milk, dairy (गाय, भैंस, दूध, डेयरी)', 'kirana': 'grocery or general shop (किराना, दुकान)',
        'tailoring': 'sewing, stitching, tailoring (सिलाई, कपड़े)',
        'none': 'not about a business (घर, इलाज, पढ़ाई, खेती)'}},
}
SURE = {'lakhpati': 0.95, 'bank_loan_ready': 0.9, 'business_plan': 0.9}
DECISION = {'lakhpati': 'lakhpati_status', 'bank_loan_ready': 'bank_linkage_readiness'}

# register label -> (table, purpose, fields read for a typical question)
REGISTER = {
    'savings': ('savings', 'savings_question', ['month', 'amount', 'cumulative']),
    'income': ('dar', 'income_question', ['month', 'activity', 'income', 'expense']),
    'loans': ('loans', 'loan_question', ['source', 'amount', 'outstanding', 'emi', 'overdue_days']),
    'land': ('agristack', 'farm_question', ['land_acres', 'irrigated', 'crop_kharif', 'crop_rabi']),
    'wages': ('wages_vbg', 'wages_question', ['month', 'days_worked', 'wages_paid', 'wages_pending']),
    'enterprise': ('enterprise', 'enterprise_question', ['type', 'monthly_turnover', 'monthly_cost']),
}
FOLLOW_UP = {
    'needs_money': ('{name} जी, आपको पैसे किस काम के लिए चाहिए, और लगभग कितने? जैसे धंधा बढ़ाने, खेती या घर के किसी खर्च के लिए?',
                    '{name}, what do you need the money for, and about how much? For your business, your farm or a household cost?'),
    'help': ('{name} जी, बताइए मैं आपकी क्या मदद करूँ? आप अपनी बचत, कर्ज़, आमदनी, लखपति दीदी या बिज़नेस प्लान के बारे में पूछ सकती हैं।',
             '{name}, how can I help? You can ask about your savings, loans, income, Lakhpati Didi status or a business plan.'),
}
NUMBER_WORDS = {'एक': 1, 'दो': 2, 'तीन': 3, 'चार': 4, 'पाँच': 5, 'पांच': 5, 'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5,
                'ek': 1, 'do': 2, 'teen': 3, 'char': 4, 'paanch': 5}
ADDS = {'dairy': ('cows_to_add', r'(?:गाय|भैंस|cows?|buffalo(?:es)?|gaay|gay)'),
        'tailoring': ('machines_to_add', r'(?:मशीन|machines?)')}


def available():
    return laya.health().get('ok', False)


def plan(ctx, history):
    """A raw plan dict in the LLM planner's format, or None when Laya cannot be reached."""
    state = _state(ctx, history)
    try:
        result = ctx.tool('laya_plan', {'state': state}, lambda: laya.predict(state, QUESTIONS), summary=_summary)
    except laya.LayaError as error:
        ctx.emit('status', transient=True, hi='', en=f'Laya unavailable, the LLM plans instead: {error}')
        return None
    answers = result['answers']
    intent, sure = _top(answers['intent'])
    register, _ = _top(answers['register'])
    enterprise, _ = _top(answers['enterprise'])
    steps = [f'Laya: intent {intent} ({sure:.2f}), register {register}']

    if _just_asked(ctx, history):  # she is answering our follow-up: a business named clearly gets a plan
        enterprise, sure_enterprise = _top(answers['enterprise'])
        intent = 'business_plan' if enterprise != 'none' and sure_enterprise >= 0.8 else 'reply'
        steps.append(f'Her answer to our follow-up: enterprise {enterprise} ({sure_enterprise:.2f})')
    elif intent in SURE and sure < SURE[intent]:  # not sure enough for a decision or a plan: fall back on the register
        intent = 'my_numbers' if register != 'none' else 'meaning'
        steps.append('Not sure enough for a decision or a plan')
    if intent == 'business_plan' and enterprise == 'none':
        intent = 'reply'  # a plan, but for which business? The writer asks her.
    if intent in FOLLOW_UP:  # vague: ask what for before reading anything, whatever register Laya guessed
        hi, en = FOLLOW_UP[intent]
        name = str(ctx.didi.get('name_local' if ctx.llm_language == 'hi' else 'name') or '').split(' ')[0]
        return {'follow_up': (hi if ctx.llm_language == 'hi' else en).format(name=name), 'needs_records': False, 'items': [],
                'steps': steps + ['Ask her a follow-up question first']}
    out = {'follow_up': None, 'needs_records': False, 'decision': None, 'business_plan': None, 'plan_changes': {},
           'purpose': 'other_question', 'items': [], 'scheme_topics': [], 'steps': steps}
    if intent in DECISION:
        out.update(needs_records=True, decision=DECISION[intent], steps=steps + [f'Run the {DECISION[intent]} decision table'])
    elif intent == 'business_plan':
        out.update(needs_records=True, business_plan=enterprise, plan_changes=_changes(ctx.question, enterprise),
                   steps=steps + [f'Build a {enterprise} business plan'])
    elif register in REGISTER and intent != 'reply':
        table, purpose, fields = REGISTER[register]
        source = ctx.table_source(table)
        if source:
            have = ctx.sources[source]['tables'][table]
            out.update(needs_records=True, purpose=purpose,
                       items=[{'source': source, 'table': table, 'fields': [f for f in fields if f in have]}],
                       steps=steps + [f'Read her {table} register', 'Answer from it'])
    if not out['needs_records']:
        out['steps'] = steps + ['Answer without her records']
    return out


def _top(answer):
    choice = answer.get('choice')
    return choice, float((answer.get('probabilities') or {}).get(choice, answer.get('confidence') or 0))


def _summary(result):
    a = result['answers']
    parts = [f'{k}={_top(a[k])[0]} ({_top(a[k])[1]:.2f})' for k in ('intent', 'register', 'enterprise')]
    return ' · '.join(parts) + f' · {result.get("ms", "?")} ms on {result.get("routing", {}).get("model", "laya")}'


def _state(ctx, history):
    """Her message alone: Laya labels a follow-up reply better without the vague first message."""
    return ctx.question


def _just_asked(ctx, history):
    """True when our last answer was one of the follow-up questions above."""
    last = history[-1][1] if history else ''
    return any(text.split('}', 1)[1][:25] in last for pair in FOLLOW_UP.values() for text in pair)


def _changes(question, enterprise):
    if enterprise not in ADDS:
        return {}
    name, noun = ADDS[enterprise]
    number = r'(\d+|' + '|'.join(NUMBER_WORDS) + r')'
    match = re.search(number + r'\s*(?:और\s*|more\s*|aur\s*|नई\s*|new\s*)?' + noun, question, re.IGNORECASE)
    if not match:
        return {}
    word = match.group(1).lower()
    return {name: int(word) if word.isdigit() else NUMBER_WORDS[word]}
