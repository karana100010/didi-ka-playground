"""Savings projections ("if I save ₹x more a month, how much after N months?").

One LLM call only extracts the scenario from her question; code computes every figure, the chart and the answer,
so no figure can be wrong once the scenario is right (eval/dspy/projection_report.md: held-out 1.000 this way,
0.94 when the LLM computes, and compound interest always wrong). The extractor's instructions are the GEPA-optimised
ones from eval/dspy/projection_program-extract-planner-opus.json, copied to projection_instructions.txt.

    balance(0) = her balance now + any one-time deposit she names
    balance(k) = round(balance(k-1) × (1 + rate/1200) + monthly deposit)        (rate 0 unless she names one)
    "now" series: her current deposit, no rate, no one-time deposit; "new" series: everything she asked for
"""
import json
import re
from collections import Counter
from pathlib import Path

from decisions.engine import indian

from .context import clean_answer

INSTRUCTIONS = (Path(__file__).with_name('projection_instructions.txt')).read_text(encoding='utf-8').strip()
NOW, NEW = 'अभी की बचत से', 'बढ़ी हुई बचत से'
MAX_MONTHS = 600
DEFAULT_MONTHS = 12
FIELDS = ['month', 'amount', 'cumulative']  # all the savings register a projection reads
# A period she named ("3 महीने बाद", "एक साल"), once "a month" phrasings ("हर महीने", "महीने के ₹300") are taken out.
_MONTHLY = re.compile(r'(हर|प्रति|per|har|every)\s*(महीने|माह|mahine|mahina|month)|(महीने|माह|mahine)\s*(का|के|की|ka|ke|ki)\s*₹?\s*\d|महीना|mahina|monthly',
                      re.I)
_PERIOD = re.compile(r'महीन|माह|साल|बरस|वर्ष|हफ़्त|हफ्त|mahin|saal|sal\b|month|year|week', re.I)


# --- her savings register ---------------------------------------------------------------------------------

def her_facts(rows, name=''):
    """Balance (last cumulative), monthly deposit (most common non-zero amount) and last month, from her rows."""
    mine = sorted((r for r in rows if r.get('month')), key=lambda r: str(r['month']))
    if not mine:
        return None
    deposits = [r['amount'] for r in mine if r.get('amount')]
    last = mine[-1]
    balance = last.get('cumulative')
    if balance is None:  # she did not allow her total: her deposits are all we know
        balance = sum(r.get('amount') or 0 for r in mine)
    return {'name': name, 'balance': balance, 'monthly_deposit': Counter(deposits).most_common(1)[0][0] if deposits else 0,
            'last_month': str(last['month'])[:7], 'months': len(mine), 'months_with_no_deposit': len(mine) - len(deposits)}


def facts_text(f):
    return (f'नाम: {f["name"]}\nबचत रजिस्टर: {f["last_month"]} तक कुल बचत ₹{indian(f["balance"])}; हर महीने ₹{f["monthly_deposit"]} जमा '
            f'(पिछले {f["months"]} में से {f["months"] - f["months_with_no_deposit"]} महीने)।\nअगला महीना: {next_month(f["last_month"], 1)}')


def next_month(month, k):
    y, m = map(int, month.split('-'))
    m += k
    y, m = y + (m - 1) // 12, (m - 1) % 12 + 1
    return f'{y}-{m:02d}'


# --- the scenario ---------------------------------------------------------------------------------------------

def parse_json(text):
    try:
        return json.loads(text[text.index('{'):text.rindex('}') + 1])
    except (ValueError, AttributeError):
        return None


def number(v):
    try:
        return float(str(v).replace(',', '').replace('₹', ''))
    except (TypeError, ValueError):
        return None


def clean_params(p):
    p = p if isinstance(p, dict) else {}
    out = {'follow_up': str(p.get('follow_up') or '').strip() or None}
    for k in ('new_deposit', 'months', 'target', 'annual_rate', 'lump_sum'):
        out[k] = number(p.get(k))
    return out


def guard(facts, p, question=''):
    """Correct the extractor from what she said: an amount she named is never asked again, "₹N ज़्यादा/और" is her
    deposit + N (not a target), and a target needs a "तक / कब / पहुँच" question."""
    amounts = said_amounts(question)
    if p['target'] and question and not _TARGET.search(question):
        p['target'] = None
        p['months'] = p['months'] or DEFAULT_MONTHS
    more = _MORE.search(question or '')
    if more and not _LUMP.search(question) and _amount(more.group(1)) != p['target']:
        p['new_deposit'] = facts['monthly_deposit'] + _amount(more.group(1))
    if amounts:
        p['follow_up'] = None  # she gave the figure; compute it
    if p['follow_up'] and p['new_deposit'] is not None and (p['months'] or p['target']) \
            and p['new_deposit'] != facts['monthly_deposit']:
        p['follow_up'] = None
    return p


_UNIT = r'(?:\s*(?:रुपये|रुपए|रुपया|rupaye|rupees|rs\.?))?'
_AMOUNT = re.compile(r'₹?\s*(\d[\d,]*)' + _UNIT + r'(?!\s*(?:\d|%|प्रतिशत|महीन|माह|साल|बरस|वर्ष|हफ़्त|हफ्त|दिन|mahin|month|saal|year|week|din))', re.I)
_MORE = re.compile(r'₹?\s*(\d[\d,]*)' + _UNIT + r'\s*(?:पे|per|प्रति)?\s*(?:हर\s*महीने|हर\s*माह|प्रति\s*माह|महीने|har\s*mahine)?\s*'
                   r'(?:ज़्यादा|ज्यादा|अधिक|और|extra|more|zyada|jyada|aur)', re.I)
_LUMP = re.compile(r'एक\s*(?:बार|साथ)|lump|ek\s*baar', re.I)
_TARGET = re.compile(r'तक|कब|पहुँच|पहुंच|reach|tak\b|kab\b', re.I)


def _amount(text):
    return int(text.replace(',', ''))


def said_amounts(question):
    """Rupee amounts in her words: numbers that are not a period ("3 महीने", "2 साल") or a rate."""
    return [_amount(m.group(1)) for m in _AMOUNT.finditer(question or '')]


def scenario(facts, p, question=''):
    """The parameters code computes from, or None when there is nothing to compute. Defaults: her deposit, 12 months."""
    target = int(p['target']) if p['target'] and p['target'] > 0 else None
    months = None if target else int(p['months'] or 0) or None
    defaulted = not target and (not months or (months == DEFAULT_MONTHS and not _PERIOD.search(_MONTHLY.sub(' ', question or ''))))
    new = p['new_deposit'] if p['new_deposit'] is not None and p['new_deposit'] >= 0 else facts['monthly_deposit']
    rate = max(p['annual_rate'] or 0, 0)
    return {'new_deposit': int(round(new)), 'months': min(months or DEFAULT_MONTHS, MAX_MONTHS) if not target else None,
            'target': target, 'annual_rate': int(rate) if float(rate).is_integer() else rate, 'lump_sum': int(round(max(p['lump_sum'] or 0, 0))),
            'months_defaulted': defaulted}


# --- the formula ------------------------------------------------------------------------------------------------

def project(facts, p):
    """Both series and the figures for scenario p: {new_deposit, months | target, annual_rate, lump_sum}."""
    rate, lump, new = p.get('annual_rate') or 0, p.get('lump_sum') or 0, p['new_deposit']
    now_dep, base = facts['monthly_deposit'], facts['balance']

    def grow(k_max, stop_at=None):
        series, b = [], base + lump
        for _ in range(k_max):
            b = round(b * (1 + rate / 1200) + new)
            series.append(b)
            if stop_at is not None and b >= stop_at:
                break
        return series

    if p.get('target'):
        new_series = grow(MAX_MONTHS, stop_at=p['target'])
        months = len(new_series)
    else:
        months = p['months']
        new_series = grow(months)
    now_series = [base + now_dep * k for k in range(1, months + 1)]
    return {'months': months, 'x': [next_month(facts['last_month'], k) for k in range(1, months + 1)],
            'now': now_series, 'new': new_series, 'projected_now': now_series[-1], 'projected_new': new_series[-1],
            'added': new_series[-1] - now_series[-1], 'reached': not p.get('target') or new_series[-1] >= p['target']}


def chart(facts, s, r):
    """The chart event's fields (title, spec, figures, assumptions) for scenario s and its projection r."""
    assumptions = ['अगर हर महीने जमा करती रहें', f'शुरुआत: {facts["last_month"]} तक की बचत ₹{indian(facts["balance"])}']
    if s['lump_sum']:
        assumptions.append(f'अभी एक बार में ₹{indian(s["lump_sum"])} जमा')
    assumptions.append(f'{indian_rate(s["annual_rate"])}% सालाना ब्याज जोड़ा' if s['annual_rate'] else 'कोई ब्याज नहीं जोड़ा')
    if s['months_defaulted']:
        assumptions.append('समय नहीं बताया, इसलिए एक साल (12 महीने) का हिसाब')
    title = f'₹{indian(s["target"])} तक की बचत' if s['target'] else f'{r["months"]} महीने में बचत'
    return {'kind': 'savings_projection', 'title': title,
            'spec': {'type': 'line', 'x': r['x'], 'series': [{'name': NOW, 'values': r['now']}, {'name': NEW, 'values': r['new']}],
                     'y_label': '₹'},
            'figures': {'projected_now': r['projected_now'], 'projected_new': r['projected_new'], 'added': r['added'],
                        'months': r['months'], 'target': s['target'], 'current_deposit': facts['monthly_deposit'],
                        'new_deposit': s['new_deposit'], 'annual_rate': s['annual_rate'], 'lump_sum': s['lump_sum']},
            'assumptions': assumptions}


def indian_rate(rate):
    return indian(rate) if float(rate).is_integer() else f'{rate:g}'


# --- the answer, worded by code ----------------------------------------------------------------------------------

def code_answer(facts, s, r, lang='hi'):
    """Two or three spoken sentences with the projected balance, the difference and the months."""
    name = (facts.get('name') or '').split(' ')[0]
    rs = lambda n: '₹' + indian(abs(n))  # noqa: E731
    diff, period = r['added'], r['months']
    if lang != 'hi':
        text = f'{name}, ' if name else ''
        if s['months_defaulted']:
            text += 'You did not say for how long, so I worked it out for one year (12 months). '
        if s['target']:
            text += f'Saving {rs(s["new_deposit"])} a month, your savings will reach {rs(s["target"])} in {period} months' + \
                (f': {rs(r["projected_new"])} by then.' if r['projected_new'] != s['target'] else '.')
        else:
            text += f'Saving {rs(s["new_deposit"])} a month, your savings will be {rs(r["projected_new"])} after {period} months.'
        if s['lump_sum']:
            text += f' This includes the one-time deposit of {rs(s["lump_sum"])}.'
        if s['annual_rate']:
            text += f' It adds {indian_rate(s["annual_rate"])}% interest a year.'
        if r['new'] == r['now']:  # she changes nothing: there is no difference to tell
            return text
        return text + (f' Saving {rs(facts["monthly_deposit"])} a month as now, it would be {rs(r["projected_now"])}, so '
                       f'{rs(diff)} {"more" if diff >= 0 else "less"}.')
    text = f'{name} जी, ' if name else ''
    if s['months_defaulted']:
        text += 'आपने समय नहीं बताया, इसलिए एक साल (12 महीने) का हिसाब लगाया है। '
    if s['target']:
        text += f'हर महीने {rs(s["new_deposit"])} जमा करने पर आपकी बचत {period} महीने में {rs(s["target"])} तक पहुँचेगी' + \
            (f': तब {rs(r["projected_new"])} होगी।' if r['projected_new'] != s['target'] else '।')
    else:
        text += f'हर महीने {rs(s["new_deposit"])} जमा करने पर {period} महीने बाद आपकी बचत {rs(r["projected_new"])} होगी।'
    if s['lump_sum']:
        text += f' इसमें एक बार में जमा किए {rs(s["lump_sum"])} भी जुड़े हैं।'
    if s['annual_rate']:
        text += f' इसमें {indian_rate(s["annual_rate"])}% सालाना ब्याज जोड़ा है।'
    if r['new'] == r['now']:
        return text
    return text + (f' अभी की तरह {rs(facts["monthly_deposit"])} जमा करने पर {rs(r["projected_now"])} होती, यानी {rs(diff)} '
                   + ('ज़्यादा जुड़ेंगे।' if diff >= 0 else 'कम जुड़ेंगे।'))


def not_reached(facts, s, lang='hi'):
    if lang != 'hi':
        return (f'Saving ₹{indian(s["new_deposit"])} a month, your savings of ₹{indian(facts["balance"])} would not reach '
                f'₹{indian(s["target"])} in {MAX_MONTHS // 12} years. Would you like to work it out with a larger monthly saving?')
    return (f'हर महीने ₹{indian(s["new_deposit"])} जमा करने पर आपकी ₹{indian(facts["balance"])} की बचत {MAX_MONTHS // 12} साल में भी '
            f'₹{indian(s["target"])} तक नहीं पहुँचेगी। क्या ज़्यादा बचत करके हिसाब लगाऊँ?')


def already_there(facts, s, lang='hi'):
    if lang != 'hi':
        return f'Your savings register already shows ₹{indian(facts["balance"])}, which is more than ₹{indian(s["target"])}.'
    return f'आपके बचत रजिस्टर में अभी ₹{indian(facts["balance"])} हैं, यानी आप ₹{indian(s["target"])} से आगे पहुँच चुकी हैं।'


# --- the agent step ----------------------------------------------------------------------------------------------

def answer(ctx, rows):
    """Extract the scenario (one LLM call), compute it (a recorded tool call), emit the chart, and word the answer.
    Returns the answer text, or None when her rows cannot be used (the writer answers instead)."""
    lang = 'hi' if ctx.llm_language == 'hi' else 'en'
    name = str(ctx.didi.get('name_local') or ctx.didi.get('name') or '')
    facts = her_facts(rows, name)
    if not facts:
        return None
    last = [m for q, a in ctx.history[-1:] for m in ({'role': 'user', 'content': q}, {'role': 'assistant', 'content': a})]
    reply = ctx.llm([{'role': 'system', 'content': f'{INSTRUCTIONS}\n\nHer savings register:\n{facts_text(facts)}'},
                     *last, {'role': 'user', 'content': ctx.question}], max_tokens=300, temperature=0)['content']
    p = guard(facts, clean_params(parse_json(reply)), ctx.question)
    if p['follow_up']:
        return clean_answer(p['follow_up'])  # she is asked first; no chart, her next turn continues from it
    s = scenario(facts, p, ctx.question)
    args = {'kind': 'savings_projection', **{k: v for k, v in s.items() if k != 'months_defaulted'}}
    ctx.worded_by_code = True
    if s['target'] and s['target'] <= facts['balance'] + s['lump_sum']:
        return already_there(facts, s, lang)
    r = ctx.tool('project_savings', args, lambda: project(facts, s),
                 summary=lambda r: f'{r["months"]} months: ₹{r["projected_now"]} now → ₹{r["projected_new"]} with the change '
                                   f'({"+" if r["added"] >= 0 else ""}{r["added"]})')
    if not r['reached']:
        return not_reached(facts, s, lang)
    ctx.emit('chart', **chart(facts, s, r), position=ctx.run['position'])
    return code_answer(facts, s, r, lang)
