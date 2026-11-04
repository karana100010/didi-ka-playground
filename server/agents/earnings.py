"""Business earnings projections ("if I start a business, how much could I earn in a year?").

Unlike a savings projection this reads none of her registers: a new business has no records yet, so its figures
are the ones she says (monthly sales and costs, or monthly profit, and any money she puts in to start). One LLM call
extracts them, or asks her for them once; code computes every figure, the chart and the answer.

    earned(k) = (monthly income − monthly expense) × k − start-up investment
"""
from decisions.engine import indian

from . import facts
from .context import clean_answer
from .projection import DEFAULT_MONTHS, MAX_MONTHS, _MONTHLY, _PERIOD, next_month, number, parse_json

INCOME, EARNED = 'कुल बिक्री', 'खर्च के बाद कमाई'
ASK_HI = 'आप इस व्यवसाय से हर महीने लगभग कितनी बिक्री और कितने खर्च का अंदाज़ा लगा रही हैं?'
ASK_EN = 'Roughly how much do you expect to sell each month from this business, and how much will it cost to run?'

INSTRUCTIONS = """You turn a didi's question about how much she could earn from a business into figures. Code does the
arithmetic and draws the chart; you only extract what she said. Use the conversation: if she is answering your
earlier question about the business, take the figures from her answer.

Reply with JSON only:
{"follow_up": null or "<one short question in her language>", "business": "<what business, in a few words, or null>",
 "monthly_income": <rupees a month of sales or income, or null>, "monthly_expense": <rupees a month of running costs, or 0>,
 "monthly_profit": <rupees a month left after costs, only if she said that instead, or null>,
 "investment": <one-time money to start the business, or 0>, "months": <number of months, 12 if she gave no period>}

Rules:
- Use only numbers she said. Never invent sales, costs or profit for her.
- "कमाई"/"आमदनी"/"बिक्री" a month is monthly_income; "खर्च"/"लागत" a month is monthly_expense; "बचेगा"/"मुनाफ़ा"/"फ़ायदा"
  a month is monthly_profit. "शुरू करने में ₹10,000 लगेंगे" is investment.
- Convert periods to months: "एक साल" 12, "छह महीने" 6, "दो साल" 24.
- If she gave neither monthly_income nor monthly_profit, set follow_up to one short, kind question asking roughly how
  much she expects to sell each month and what it will cost to run. Ask about sales and costs, never about savings.
- If she gave either one, follow_up is null.
All numbers are plain integers, with no ₹ sign and no commas."""


def clean_params(p):
    p = p if isinstance(p, dict) else {}
    out = {'follow_up': str(p.get('follow_up') or '').strip() or None, 'business': str(p.get('business') or '').strip()[:60] or None}
    for k in ('monthly_income', 'monthly_expense', 'monthly_profit', 'investment', 'months'):
        out[k] = number(p.get(k))
    if out['monthly_income'] is not None or out['monthly_profit'] is not None:
        out['follow_up'] = None  # she gave figures: compute them
    return out


def scenario(p, question=''):
    """The figures code computes from, or None when she has given no monthly figure yet."""
    if p['monthly_income'] is None and p['monthly_profit'] is None:
        return None
    months = int(p['months'] or 0) or None
    defaulted = not months or (months == DEFAULT_MONTHS and not _PERIOD.search(_MONTHLY.sub(' ', question or '')))
    income = max(p['monthly_income'] or 0, 0)
    expense = max(p['monthly_expense'] or 0, 0) if p['monthly_income'] is not None else 0
    profit = income - expense if p['monthly_income'] is not None else p['monthly_profit']
    return {'business': p['business'], 'monthly_income': int(round(income)) if p['monthly_income'] is not None else None,
            'monthly_expense': int(round(expense)), 'monthly_profit': int(round(profit)),
            'investment': int(round(max(p['investment'] or 0, 0))), 'months': min(months or DEFAULT_MONTHS, MAX_MONTHS),
            'months_defaulted': defaulted}


def project(s, start_month):
    months = s['months']
    earned = [s['monthly_profit'] * k - s['investment'] for k in range(1, months + 1)]
    income = [s['monthly_income'] * k for k in range(1, months + 1)] if s['monthly_income'] is not None else None
    payback = next((k for k in range(1, MAX_MONTHS + 1) if s['monthly_profit'] * k >= s['investment']), None) \
        if s['investment'] and s['monthly_profit'] > 0 else None
    return {'months': months, 'x': [next_month(start_month, k) for k in range(1, months + 1)], 'earned': earned,
            'income': income, 'total_earned': earned[-1], 'total_income': income[-1] if income else None, 'payback_months': payback}


def chart(s, r):
    series = ([{'name': INCOME, 'values': r['income']}] if r['income'] and s['monthly_expense'] else []) \
        + [{'name': EARNED, 'values': r['earned']}]
    assumptions = [f'हर महीने ₹{indian(s["monthly_income"])} बिक्री और ₹{indian(s["monthly_expense"])} खर्च' if s['monthly_income'] is not None
                   else f'हर महीने ₹{indian(s["monthly_profit"])} खर्च के बाद बचते हैं',
                   'हर महीने बिक्री और खर्च एक जैसे रहें']
    if s['investment']:
        assumptions.append(f'शुरू करने में ₹{indian(s["investment"])} लगे, जो कमाई से घटाए')
    if s['months_defaulted']:
        assumptions.append('समय नहीं बताया, इसलिए एक साल (12 महीने) का हिसाब')
    return {'kind': 'earnings_projection', 'title': f'{r["months"]} महीने में व्यवसाय से कमाई',
            'spec': {'type': 'line', 'x': r['x'], 'series': series, 'y_label': '₹'},
            'figures': {'months': r['months'], 'projected_new': r['total_earned'], 'monthly_profit': s['monthly_profit'],
                        'investment': s['investment'], 'payback_months': r['payback_months']},
            'assumptions': assumptions}


def code_answer(name, s, r, lang='hi'):
    rs = lambda n: '₹' + indian(abs(n))  # noqa: E731
    first = (name or '').split(' ')[0]
    total, months = r['total_earned'], r['months']
    if lang != 'hi':
        text = f'{first}, ' if first else ''
        if s['months_defaulted']:
            text += 'You did not say for how long, so I worked it out for one year (12 months). '
        text += f'If {rs(s["monthly_profit"])} is left each month after costs, ' + \
            (f'you would earn {rs(total)} in {months} months' if total >= 0 else f'after {months} months you would still be {rs(total)} short')
        text += f', after taking out the {rs(s["investment"])} to start.' if s['investment'] else '.'
        if r['payback_months']:
            text += f' The start-up money would come back in about {r["payback_months"]} months.'
        return text
    text = f'{first} जी, ' if first else ''
    if s['months_defaulted']:
        text += 'आपने समय नहीं बताया, इसलिए एक साल (12 महीने) का हिसाब लगाया है। '
    text += f'अगर खर्च के बाद हर महीने {rs(s["monthly_profit"])} बचते हैं, तो ' + \
        (f'{months} महीने में आपकी कमाई {rs(total)} होगी' if total >= 0 else f'{months} महीने बाद भी {rs(total)} की कमी रहेगी')
    text += f', शुरू करने के {rs(s["investment"])} घटाकर।' if s['investment'] else '।'
    if r['payback_months']:
        text += f' लगाए गए पैसे लगभग {r["payback_months"]} महीने में वापस आ जाएँगे।'
    return text


def answer(ctx, start_month):
    """Extract her figures (one LLM call), compute them (a recorded tool call), emit the chart, word the answer.
    Returns the answer text, or the one follow-up question when she has not said what the business would make."""
    lang = 'hi' if ctx.llm_language == 'hi' else 'en'
    last = [m for q, a in ctx.history[-1:] for m in ({'role': 'user', 'content': q}, {'role': 'assistant', 'content': a})]
    reply = ctx.llm([{'role': 'system', 'content': INSTRUCTIONS}, *last, {'role': 'user', 'content': ctx.question}],
                    max_tokens=300, temperature=0)['content']
    p = clean_params(parse_json(reply))
    said = ' '.join([q for q, _ in ctx.history[-1:]] + [ctx.question])  # the period may be in the question she is answering
    s = scenario(p, said)
    if s is None:  # never a savings question: what will the business sell and cost?
        return clean_answer(ASK_HI if lang == 'hi' else ASK_EN)
    ctx.worded_by_code = True
    ctx.known |= facts.numbers(s)  # her own figures, for the self-check
    args = {'kind': 'earnings_projection', **{k: v for k, v in s.items() if k != 'months_defaulted'}}
    r = ctx.tool('project_earnings', args, lambda: project(s, start_month),
                 summary=lambda r: f'{r["months"]} months at ₹{s["monthly_profit"]}/month: ₹{r["total_earned"]}')
    ctx.emit('chart', **chart(s, r), position=ctx.run['position'])
    name = str(ctx.didi.get('name_local') or ctx.didi.get('name') or '')
    return code_answer(name, s, r, lang)
