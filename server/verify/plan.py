"""Gate A: before a consent card, check the registers and fields an agent wants to read.

  justified tables   each table must be one her question (or the follow-up she is answering) asks about, or one
                     the plan's purpose needs. A table nothing asks for is dropped when another one answers her;
                     when none does (a vague or indirect question) it is only reported.
  no id fields       farmer_id, job_card_no, loan_id, enterprise_id never answer her question: dropped.
  policy             a source paused by its owner, or a purpose the department does not allow for a table, is
                     dropped before she is asked, not discovered after she said yes.

Repairs are made in code (no extra LLM call), so a run's cost and its consent card stay predictable.
"""
import re

from . import Finding

ID_FIELDS = ('farmer_id', 'job_card_no', 'loan_id', 'enterprise_id')
# A running balance in a monthly register means nothing without its month (which one is the latest?).
NEEDS_MONTH = {'savings': ('cumulative',)}

# Words in her question that point at a register (Devanagari, and Hindi typed in English letters).
INTENT = {
    'savings': ('बचत', 'जमा', 'सेविंग', 'saving', 'bachat', 'jama'),
    'loans': ('कर्ज़', 'कर्ज', 'क़र्ज़', 'लोन', 'ऋण', 'किस्त', 'क़िस्त', 'ब्याज', 'उधार', 'बकाया', 'loan', 'karz', 'kist', 'emi', 'byaj'),
    'dar': ('आमदनी', 'आय', 'कमाई', 'कमाया', 'कमाती', 'मुनाफ़ा', 'मुनाफा', 'फ़ायदा', 'लखपति', 'income', 'kamai',
            'aamdani', 'munafa', 'profit', 'lakhpati'),
    'wages_vbg': ('मज़दूरी', 'मजदूरी', 'राम जी', 'मनरेगा', 'दिन काम', 'जॉब कार्ड', 'mazdoori', 'majdoori', 'wages', 'nrega'),
    'agristack': ('ज़मीन', 'जमीन', 'खेत', 'सिंचाई', 'फसल', 'फ़सल', 'एकड़', 'zameen', 'khet', 'fasal', 'land', 'crop'),
    'enterprise': ('धंधे', 'धंधा', 'व्यापार', 'कारोबार', 'टर्नओवर', 'दुकान', 'गाय', 'मशीन', 'बकरी', 'मुर्गी', 'business',
                   'dhandha', 'turnover', 'dukan'),
    'meetings': ('बैठक', 'मीटिंग', 'समूह', 'meeting', 'samuh'),
}
PURPOSE_TABLES = {
    'income_question': {'dar'}, 'savings_question': {'savings'}, 'loan_question': {'loans'},
    'enterprise_question': {'enterprise'}, 'wages_question': {'wages_vbg'}, 'farm_question': {'agristack'},
    'group_question': {'meetings'},
}


def asked_about(text):
    """The registers her words point at."""
    low = (text or '').lower()
    return {table for table, words in INTENT.items() if any(w in low for w in words)}


def intent_text(question, history=()):
    """Her question, plus the question she is answering when the agent's last turn asked her something."""
    if history:
        earlier_q, earlier_a = history[-1]
        if re.search(r'\?\s*$', (earlier_a or '').strip()):
            return f'{earlier_q} {question}'
    return question


def check(text, items, purpose, sources=None):
    """(items to ask for, findings). items: [{'source_id', 'table', 'fields'}] as tools._items returns them."""
    findings, kept = [], []
    for item in items:
        ids = [f for f in item['fields'] if f in ID_FIELDS]
        if ids:
            findings.append(Finding('id_field', False, 'repair', f'{item["table"]}: {", ".join(ids)} dropped',
                                    f'{", ".join(ids)} never answers her question; do not ask for it.'))
            item = {**item, 'fields': [f for f in item['fields'] if f not in ID_FIELDS]}
        stock = [f for f in NEEDS_MONTH.get(item['table'], ()) if f in item['fields']]
        if stock and 'month' not in item['fields']:
            findings.append(Finding('needs_month', False, 'repair', f'{item["table"]}: month added to read {", ".join(stock)}',
                                    'A running balance needs its month to find the latest one.'))
            item = {**item, 'fields': ['month', *item['fields']]}
        if item['fields']:
            kept.append(item)
    findings.append(Finding('id_field', True))

    if sources is not None:
        import gateway
        allowed = []
        for item in kept:
            source = sources.get(item['source_id'])
            try:
                if source:
                    gateway.check_policy(source, item['table'], purpose)
                allowed.append(item)
            except gateway.Blocked as blocked:
                findings.append(Finding('policy', False, 'block', f'{item["table"]}: {blocked.message}'))
        kept = allowed
    findings.append(Finding('policy', True))

    asked = asked_about(text)
    justified = asked | PURPOSE_TABLES.get(purpose, set())
    extra = [i for i in kept if i['table'] not in justified]
    if extra and asked:
        if len(extra) < len(kept):
            kept = [i for i in kept if i['table'] in justified]
            findings.append(Finding('unjustified_table', False, 'repair',
                                    f'dropped {", ".join(i["table"] for i in extra)}: her question asks about '
                                    f'{", ".join(sorted(asked))}',
                                    f'Her question does not ask about {", ".join(i["table"] for i in extra)}; '
                                    'asking for more than the answer needs is a privacy failure.'))
        else:
            findings.append(Finding('unjustified_table', False, 'warn',
                                    f'{", ".join(i["table"] for i in extra)} not what her question names '
                                    f'({", ".join(sorted(asked))}); kept, nothing else answers it'))
    findings.append(Finding('unjustified_table', True))
    return kept, findings


def gate(ctx, items, purpose):
    """Run gate A for an agent and emit its verdict. Returns (items to ask consent for, failed findings)."""
    from . import emit, failed
    kept, findings = check(intent_text(ctx.question, ctx.history), items, purpose, ctx.sources)
    for f in failed(findings):
        if f.name == 'policy':
            table = f.detail.split(':', 1)[0]
            ctx.obligations.append({'table': table, 'reason': 'blocked'})
    bad = failed(findings)
    action = 'deliver' if not bad else 'warn' if all(f.severity == 'warn' for f in bad) else 'repaired'
    emit(ctx, 'plan', findings, action)
    return kept, bad
