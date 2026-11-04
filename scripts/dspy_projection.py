"""Savings projections ("if I save ₹x more a month, how much after N months?") as a DSPy task, scored in code.

Two designs are compared, each from three starting prompts, before and after GEPA:
  full     the model extracts the scenario, computes the chart series and writes the Hindi answer (one call)
  extract  the model only extracts the scenario; code computes every figure, the chart and the answer

Every expected figure comes from her real savings register (data/mock, via eval/dspy/contexts.json didis) and the
formula below; SHG savings earn no interest unless the question names a rate.

    balance(0) = her balance now + any one-time deposit she names
    balance(k) = round(balance(k-1) × (1 + rate/1200) + monthly deposit)        (rate 0 unless she names one)
    "now" series: her current deposit, no rate, no one-time deposit; "new" series: everything she asked for

    .venv/bin/python scripts/dspy_projection.py facts            # eval/dspy/projection_facts.json (app venv: reads xlsx)
    .dspy-venv/bin/python scripts/dspy_projection.py dataset     # eval/dspy/projection_dataset.json, 50/20/30
    .dspy-venv/bin/python scripts/dspy_projection.py eval --design full --prompt hand
    .dspy-venv/bin/python scripts/dspy_projection.py optimize --design full --prompt hand --reflection claude-cli/claude-opus-5-5
    .dspy-venv/bin/python scripts/dspy_projection.py report      # projection_report.md tables + projection_charts.html
"""
import argparse
import json
import math
import random
import re
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))
from dspy_planner import make_lm  # noqa: E402  the NVIDIA, Bhashini and Claude CLI LMs

OUT = ROOT / 'eval' / 'dspy'
FACTS, DATASET = OUT / 'projection_facts.json', OUT / 'projection_dataset.json'
RESULTS, RUNS = OUT / 'projection_results.json', OUT / 'projection_gepa'
GEMMA = 'bhashini/google/gemma-4-26B-A4B-it'
NOW, NEW = 'अभी की बचत से', 'बढ़ी हुई बचत से'
DIDIS = [f'D-00{i}' for i in range(1, 9)]


# --- her savings register ---------------------------------------------------------------------------------

def dump_facts():
    import openpyxl
    rows = list(openpyxl.load_workbook(ROOT / 'data' / 'mock' / 'lokos_mock.xlsx', read_only=True)['savings'].iter_rows(values_only=True))
    users = list(openpyxl.load_workbook(ROOT / 'data' / 'mock' / 'lokos_mock.xlsx', read_only=True)['users'].iter_rows(values_only=True))
    names = {u[0]: u[2] for u in users[1:]}
    out = {}
    for didi in DIDIS:
        mine = sorted((dict(zip(rows[0], r)) for r in rows[1:] if r[0] == didi), key=lambda r: r['month'])
        deposits = [r['amount'] for r in mine if r['amount']]
        out[didi] = {'name': names[didi], 'balance': mine[-1]['cumulative'], 'monthly_deposit': max(set(deposits), key=deposits.count),
                     'last_month': mine[-1]['month'], 'months_with_no_deposit': sum(1 for r in mine if not r['amount'])}
    OUT.mkdir(parents=True, exist_ok=True)
    FACTS.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding='utf-8')
    print(json.dumps(out, ensure_ascii=False, indent=1))


def facts_text(f):
    return (f'नाम: {f["name"]}\nबचत रजिस्टर: {f["last_month"]} तक कुल बचत ₹{rupees(f["balance"])}; हर महीने ₹{f["monthly_deposit"]} जमा '
            f'(पिछले 12 में से {12 - f["months_with_no_deposit"]} महीने)।\nअगला महीना: {next_month(f["last_month"], 1)}')


def rupees(n):
    n = int(round(n))
    sign, s = ('-' if n < 0 else ''), str(abs(n))
    if len(s) <= 3:
        return sign + s
    head, tail = s[:-3], s[-3:]
    groups = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    return sign + ','.join(([head] if head else []) + groups + [tail])


def next_month(month, k):
    y, m = map(int, month.split('-'))
    m += k
    y, m = y + (m - 1) // 12, (m - 1) % 12 + 1
    return f'{y}-{m:02d}'


# --- the formula ------------------------------------------------------------------------------------------------

def project(facts, p):
    """Expected figures for scenario p: {new_deposit, months | target, annual_rate, lump_sum}."""
    rate, lump, new = p.get('annual_rate') or 0, p.get('lump_sum') or 0, p['new_deposit']
    now_dep, base = facts['monthly_deposit'], facts['balance']

    def grow(k_max, stop_at=None):
        series, b = [], base + lump
        for k in range(1, k_max + 1):
            b = round(b * (1 + rate / 1200) + new)
            series.append(b)
            if stop_at is not None and b >= stop_at:
                break
        return series

    if p.get('target'):
        new_series = grow(600, stop_at=p['target'])
        months = len(new_series)
    else:
        months = p['months']
        new_series = grow(months)
    now_series = [base + now_dep * k for k in range(1, months + 1)]
    return {'months': months, 'x': [next_month(facts['last_month'], k) for k in range(1, months + 1)],
            'now': now_series, 'new': new_series, 'projected_now': now_series[-1], 'projected_new': new_series[-1],
            'added': new_series[-1] - now_series[-1]}


# --- dataset ---------------------------------------------------------------------------------------------------

WORDS = {50: 'पचास', 100: 'सौ', 150: 'डेढ़ सौ', 200: 'दो सौ', 250: 'ढाई सौ', 300: 'तीन सौ', 400: 'चार सौ', 500: 'पाँच सौ',
         1000: 'एक हज़ार', 2000: 'दो हज़ार', 5000: 'पाँच हज़ार', 10000: 'दस हज़ार', 15000: 'पंद्रह हज़ार',
         20000: 'बीस हज़ार', 25000: 'पच्चीस हज़ार', 30000: 'तीस हज़ार', 40000: 'चालीस हज़ार', 50000: 'पचास हज़ार'}
HORIZON = {3: ['3 महीने', 'तीन महीने'], 6: ['6 महीने', 'छह महीने'], 9: ['9 महीने', 'नौ महीने'], 12: ['12 महीने', 'एक साल'],
           18: ['18 महीने', 'डेढ़ साल'], 24: ['24 महीने', 'दो साल'], 36: ['36 महीने', 'तीन साल']}
HORIZON_EN = {3: '3 mahine', 6: '6 mahine', 9: '9 mahine', 12: 'ek saal', 18: '18 mahine', 24: 'do saal', 36: 'teen saal'}


def say(rng, n, style):
    if style == 'words' and n in WORDS:
        return WORDS[n]
    return f'{n}' if style == 'hinglish' or rng.random() < 0.5 else f'₹{n}'


def when(rng, months, style):
    return HORIZON_EN[months] if style == 'hinglish' else rng.choice(HORIZON[months])


def make_case(rng, facts, kind):
    """One question of a kind, in a random style, with the scenario parameters it asks for."""
    style = rng.choice(['digits', 'digits', 'words', 'hinglish'])
    cur = facts['monthly_deposit']
    months = rng.choice([3, 6, 9, 12, 18, 24, 36])
    h = lambda: when(rng, months, style)  # noqa: E731
    n = lambda v: say(rng, v, style)  # noqa: E731
    p = {'new_deposit': cur, 'months': months, 'target': None, 'annual_rate': 0, 'lump_sum': 0}
    if kind == 'add':
        x = rng.choice([50, 100, 150, 200, 300, 500])
        p['new_deposit'] = cur + x
        q = rng.choice([f'अगर मैं हर महीने {n(x)} और बचत करूँ तो {h()} बाद मेरी कुल बचत कितनी होगी?',
                        f'हर महीने {n(x)} ज़्यादा जमा करूँ तो {h()} में कितनी बचत हो जाएगी और कितना ज़्यादा जुड़ेगा?',
                        f'मैं बचत {n(x)} महीना बढ़ा दूँ तो {h()} बाद कितना पैसा होगा?']) if style != 'hinglish' else \
            rng.choice([f'agar main har mahine {x} rupaye zyada bachaun to {h()} baad kitna hoga?',
                        f'har mahine {x} extra jama karun to {h()} mein meri savings kitni hogi?'])
    elif kind == 'set':
        y = rng.choice([v for v in (300, 400, 500, 1000) if v > cur])
        p['new_deposit'] = y
        q = rng.choice([f'अगर मैं अब से हर महीने {n(y)} जमा करूँ तो {h()} बाद कितनी बचत होगी?',
                        f'अभी की जगह महीने के {n(y)} बचाऊँ तो {h()} में कितना फ़र्क पड़ेगा?']) if style != 'hinglish' else \
            f'agar main ab se har mahine {y} rupaye jama karun to {h()} baad kitni bachat hogi?'
    elif kind == 'reduce':
        x = rng.choice([v for v in (50, 100) if v < cur])
        p['new_deposit'] = cur - x
        q = rng.choice([f'अगर मैं हर महीने {n(x)} कम जमा करूँ तो {h()} बाद कितनी बचत होगी और कितनी कम होगी?',
                        f'घर का खर्च बढ़ गया है, बचत {n(x)} घटा दूँ तो {h()} में कितना कम जुड़ेगा?'])
    elif kind == 'double':
        p['new_deposit'] = cur * 2
        q = rng.choice([f'अगर मैं अपनी महीने की बचत दोगुनी कर दूँ तो {h()} बाद कितना होगा?',
                        f'बचत डबल कर दूँ तो {h()} में कितना ज़्यादा जमा होगा?']) if style != 'hinglish' else \
            f'agar main apni monthly bachat double kar dun to {h()} baad kitna hoga?'
    elif kind == 'percent':
        pct = rng.choice([v for v in (20, 25, 50) if cur * v % 100 == 0])
        p['new_deposit'] = cur + cur * pct // 100
        q = rng.choice([f'अगर मैं अपनी बचत {pct}% बढ़ा दूँ तो {h()} बाद कुल कितनी होगी?',
                        f'महीने की बचत में {pct} प्रतिशत की बढ़ोतरी करूँ तो {h()} में कितना ज़्यादा जुड़ेगा?'])
    elif kind in ('target', 'target_add'):
        x = rng.choice([100, 200, 300]) if kind == 'target_add' else 0
        p['new_deposit'], p['months'] = cur + x, None
        lo, hi = facts['balance'] + p['new_deposit'] * 8, facts['balance'] + p['new_deposit'] * 36
        spoken = [w for w in WORDS if lo <= w <= hi]
        if style == 'words' and spoken:
            target = rng.choice(spoken)
        else:
            target = math.ceil((facts['balance'] + p['new_deposit'] * rng.randint(8, 36)) / 1000) * 1000
            style = 'digits' if style == 'words' else style
        p['target'] = target
        t = n(target)
        q = (rng.choice([f'मेरी बचत {t} तक कब पहुँचेगी?', f'अभी की तरह बचत करती रहूँ तो {t} होने में कितने महीने लगेंगे?'])
             if not x else f'अगर हर महीने {n(x)} और जमा करूँ तो {t} तक पहुँचने में कितने महीने लगेंगे?')
        if style == 'hinglish':
            q = f'meri savings {target} tak kab pahunchegi?' if not x else f'har mahine {x} zyada dalun to {target} hone mein kitne mahine lagenge?'
    elif kind == 'no_horizon':
        x = rng.choice([50, 100, 200])
        sub = rng.choice(['add', 'double', 'set'])
        p['months'] = 12
        if sub == 'add':
            p['new_deposit'] = cur + x
            q = f'अगर मैं हर महीने {n(x)} और बचाऊँ तो मेरी बचत कितनी हो जाएगी?' if style != 'hinglish' else f'agar main {x} rupaye mahina aur bachaun to kitna ho jayega?'
        elif sub == 'double':
            p['new_deposit'] = cur * 2
            q = 'अपनी बचत दोगुनी कर दूँ तो कितना फ़ायदा होगा?'
        else:
            y = rng.choice([v for v in (400, 500, 1000) if v > cur])
            p['new_deposit'] = y
            q = f'महीने के {n(y)} जमा करने लगूँ तो कितनी बचत होगी?'
    elif kind == 'rate':
        rate = rng.choice([6, 7, 8, 9])
        if rng.random() < 0.5:
            q = f'अगर मेरी बचत पर {rate}% सालाना ब्याज मिले तो {h()} बाद कितनी होगी?'
        else:
            x = rng.choice([100, 200])
            p['new_deposit'] = cur + x
            q = f'अगर मैं हर महीने {n(x)} और जमा करूँ और बैंक {rate}% सालाना ब्याज दे तो {h()} बाद कितना होगा?'
        p['annual_rate'] = rate
    elif kind == 'lump':
        lump = rng.choice([1000, 2000, 5000])
        p['lump_sum'] = lump
        if rng.random() < 0.5:
            q = f'अगर मैं अभी एक बार में {n(lump)} जमा कर दूँ तो {h()} बाद कुल बचत कितनी होगी?'
        else:
            x = rng.choice([50, 100])
            p['new_deposit'] = cur + x
            q = f'बकरी बेचकर {n(lump)} मिले हैं, वो जमा कर दूँ और हर महीने {n(x)} ज़्यादा डालूँ तो {h()} में कितना होगा?'
    elif kind == 'follow_up':
        q = rng.choice(['अगर मैं ज़्यादा बचत करूँ तो कितना जमा हो जाएगा?', 'बचत बढ़ाऊँ तो क्या फ़ायदा होगा?',
                        'कुछ और पैसे हर महीने डालूँ तो कितना होगा?', 'agar main zyada bachat karun to kitna hoga?',
                        'अगले साल तक बचत बढ़ानी है, कितना डालूँ?'])
        return q, {'follow_up': True}, style
    return q, {'follow_up': False, **p}, style


KINDS = {'add': 5, 'set': 3, 'reduce': 1, 'double': 2, 'percent': 2, 'target': 2, 'target_add': 2, 'no_horizon': 3,
         'rate': 2, 'lump': 2, 'follow_up': 2}


def build_dataset(seed=11):
    facts = json.loads(FACTS.read_text(encoding='utf-8'))
    rng = random.Random(seed)
    cases, seen = [], set()
    for didi in DIDIS:
        for kind, count in KINDS.items():
            made = 0
            for _ in range(50):
                if made == count:
                    break
                q, p, style = make_case(rng, facts[didi], kind)
                if (didi, q) in seen:
                    continue
                seen.add((didi, q))
                expected = {'params': p}
                if not p['follow_up']:
                    expected.update(project(facts[didi], p))
                style = 'hinglish' if re.search(r'[A-Za-z]{3,}', q) else 'words' if style == 'hinglish' else style
                cases.append({'id': f'{didi}-{kind}-{len(cases)}', 'didi': didi, 'kind': kind, 'style': style,
                              'question': q, 'expected': expected})
                made += 1
    by_kind = defaultdict(list)
    for c in cases:
        by_kind[c['kind']].append(c)
    for group in by_kind.values():  # 50/20/30, stratified by kind
        rng.shuffle(group)
        n = len(group)
        for i, c in enumerate(group):
            c['split'] = 'train' if i < round(n * 0.5) else 'val' if i < round(n * 0.7) else 'test'
    DATASET.write_text(json.dumps({'description': 'Savings projection questions; every expected figure computed by '
                                   'the formula in scripts/dspy_projection.py from her savings register.',
                                   'seed': seed, 'cases': cases}, ensure_ascii=False, indent=1), encoding='utf-8')
    counts = defaultdict(int)
    for c in cases:
        counts[c['split']] += 1
    print(f'wrote {DATASET.relative_to(ROOT)}: {len(cases)} cases {dict(counts)}; styles '
          f'{dict((s, sum(c["style"] == s for c in cases)) for s in ("digits", "words", "hinglish"))}')


# --- prompts ---------------------------------------------------------------------------------------------------

PARAMS_SCHEMA = ('{"follow_up": null or "<one short question in Hindi>", "new_deposit": <rupees a month>, '
                 '"months": <number or null>, "target": <rupees or null>, "annual_rate": <percent a year, 0 if none>, '
                 '"lump_sum": <one-time deposit, 0 if none>}')
CHART_SCHEMA = ('{"type": "line", "x": ["YYYY-MM", ...], "series": [{"name": "अभी की बचत से", "values": [...]}, '
                '{"name": "बढ़ी हुई बचत से", "values": [...]}], "y_label": "₹"}')

PROMPTS = {
    'full': {
        'naive': f"""Answer the didi's question about her future savings. Reply with JSON:
{{"params": {PARAMS_SCHEMA}, "chart": {CHART_SCHEMA}, "answer": "<answer in Hindi>"}}""",
        'planner': f"""You answer a didi's question about how her SHG savings would grow. Her savings register is below.
Reply with JSON only:
{{"params": {PARAMS_SCHEMA},
 "chart": null or {CHART_SCHEMA},
 "answer": "<2 to 3 short spoken sentences in Hindi>"}}

Use the figures from her register exactly as they are. Put in "params" only what she said; take everything else from
her register. If she did not say how much more she would save, set "follow_up" to one short, kind question in Hindi,
set "chart" to null and make "answer" that question. The chart has one point per month, starting next month, for
her current savings and for the change she asked about. The answer states the projected balance and how much more
she would have saved. Write in Hindi only; never mention JSON or field names.""",
        'hand': f"""You work out how a didi's SHG savings would grow if she changed what she saves, and say it simply in Hindi.
Her savings register is given below: her balance now and what she deposits each month.

Reply with one JSON object only:
{{"params": {PARAMS_SCHEMA},
 "chart": null or {CHART_SCHEMA},
 "answer": "<2 or 3 short spoken Hindi sentences>"}}

1. params, from her words (numbers can be in words: पचास 50, सौ 100, डेढ़ सौ 150, दो सौ 200, ढाई सौ 250, पाँच सौ 500,
   हज़ार 1000; एक साल 12 months, डेढ़ साल 18, दो साल 24, तीन साल 36; she may type Hindi in English letters):
   - "₹x और/ज़्यादा/extra" -> new_deposit = her deposit + x. "x कम/घटा" -> her deposit − x. "अब से ₹y" -> y.
     "दोगुनी/double" -> 2 × her deposit. "p% बढ़ा" -> her deposit × (1 + p/100). Otherwise new_deposit = her deposit.
   - months: the period she said. If she gave none, use 12 and say in the answer that you took 12 months (एक साल).
   - target: when she asks how long until a sum, put the sum here and set months to null.
   - annual_rate: only if she names an interest rate; SHG savings earn no interest otherwise (0).
   - lump_sum: a one-time amount she will deposit now, else 0.
   - If she wants to save more but gives no amount, set follow_up to one short kind Hindi question asking how much
     more a month, chart null, and answer = that question. Never guess an amount.
2. chart: month by month, starting the month after her register's last month.
   balance(0) = her balance now + lump_sum (lump_sum only in the new series).
   new series: balance(k) = round(balance(k−1) × (1 + annual_rate/1200) + new_deposit).
   current series: her balance now + k × her current deposit (no rate, no lump sum).
   For a target, run the new series until it first reaches the target; that many months is the answer, and both
   series have that length. Both series must have exactly as many values as x has months.
3. answer: the last value of the new series (projected balance), how much more than the current series (the
   difference, or how much less if she saves less), and the months. Write money like ₹16,200. Hindi only: no English
   words, no field names.

Example: balance ₹12,000, deposit ₹200, last month 2026-09; "हर महीने 100 और जमा करूँ तो 3 महीने बाद?" ->
{{"params": {{"follow_up": null, "new_deposit": 300, "months": 3, "target": null, "annual_rate": 0, "lump_sum": 0}},
 "chart": {{"type": "line", "x": ["2026-10", "2026-11", "2026-12"], "series": [{{"name": "अभी की बचत से", "values": [12200, 12400, 12600]}},
 {{"name": "बढ़ी हुई बचत से", "values": [12300, 12600, 12900]}}], "y_label": "₹"}},
 "answer": "हर महीने ₹300 जमा करने पर 3 महीने बाद आपकी बचत ₹12,900 होगी। अभी की तरह ₹200 जमा करने पर ₹12,600 होती, यानी ₹300 ज़्यादा जुड़ेंगे।"}}""",
    },
    'extract': {
        'naive': f"""Read the didi's question about her future savings. Reply with JSON: {PARAMS_SCHEMA}""",
        'planner': f"""You read a didi's question about how her SHG savings would grow and write down the scenario; code
does the arithmetic and draws the chart. Her savings register is below. Reply with JSON only:
{PARAMS_SCHEMA}

Put in it only what she said; take her current deposit from her register when she does not change it. If she did not
say how much more she would save, set "follow_up" to one short, kind question in Hindi and leave the rest as they are.
If she gave no period, set months to 12.""",
        'hand': f"""You read a didi's question about how her SHG savings would grow and write down the scenario as JSON.
Code does all the arithmetic and draws the chart, so never compute balances. Her savings register is below.

Reply with one JSON object only: {PARAMS_SCHEMA}

Numbers may be in words: पचास 50, सौ 100, डेढ़ सौ 150, दो सौ 200, ढाई सौ 250, पाँच सौ 500, हज़ार 1000;
एक साल 12 months, डेढ़ साल 18, दो साल 24, तीन साल 36. She may type Hindi in English letters.
- "₹x और/ज़्यादा/extra" -> new_deposit = her current deposit + x. "x कम/घटा" -> her deposit − x. "अब से ₹y" -> y.
  "दोगुनी/double" -> 2 × her deposit. "p% बढ़ा" -> her deposit × (1 + p/100). Otherwise new_deposit = her deposit.
- months: the period she said. None given -> 12.
- target: when she asks how long until a sum ("कब तक ₹20,000", "कितने महीने लगेंगे"), the sum here, months null.
- annual_rate: only a rate she names (SHG savings earn none otherwise): 0.
- lump_sum: a one-time amount she will deposit now ("एक बार में", "बेचकर मिले हैं, जमा कर दूँ"), else 0.
- If she wants to save more but gives no amount, set follow_up to one short kind Hindi question asking how much more
  a month. Never guess an amount.
Example: deposit ₹200; "हर महीने डेढ़ सौ और डालूँ तो दो साल बाद?" ->
{{"follow_up": null, "new_deposit": 350, "months": 24, "target": null, "annual_rate": 0, "lump_sum": 0}}""",
    },
}


# --- parsing and the code path -----------------------------------------------------------------------------------

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


def code_answer(facts, p):
    """What the extract design shows: the figures, chart and answer from code (the production idea)."""
    if p['follow_up']:
        return None, p['follow_up']
    if not p['new_deposit'] or (not p['months'] and not p['target']):
        return None, 'माफ़ कीजिए, मैं यह हिसाब नहीं लगा पाई। आप हर महीने कितना और कितने महीने जमा करना चाहती हैं?'
    scenario = {'new_deposit': p['new_deposit'], 'months': int(p['months'] or 0) or None, 'target': p['target'] or None,
                'annual_rate': p['annual_rate'] or 0, 'lump_sum': p['lump_sum'] or 0}
    if not scenario['target'] and scenario['months'] > 600:
        scenario['months'] = 600
    r = project(facts, scenario)
    chart = {'type': 'line', 'x': r['x'], 'series': [{'name': NOW, 'values': r['now']}, {'name': NEW, 'values': r['new']}], 'y_label': '₹'}
    period = f'{r["months"]} महीने'
    if scenario['target']:
        text = f'हर महीने ₹{rupees(scenario["new_deposit"])} जमा करने पर आपकी बचत {period} में ₹{rupees(scenario["target"])} तक पहुँचेगी: तब ₹{rupees(r["projected_new"])} होगी।'
    else:
        text = f'हर महीने ₹{rupees(scenario["new_deposit"])} जमा करने पर {period} बाद आपकी बचत ₹{rupees(r["projected_new"])} होगी।'
    if scenario['annual_rate']:
        text += f' इसमें {rupees(scenario["annual_rate"])}% सालाना ब्याज जोड़ा है।'
    diff = r['added']
    text += f' अभी की तरह ₹{rupees(facts["monthly_deposit"])} जमा करने पर ₹{rupees(r["projected_now"])} होती, यानी ₹{rupees(abs(diff))} ' + \
            ('ज़्यादा जुड़ेंगे।' if diff >= 0 else 'कम जुड़ेंगे।')
    return chart, text


# --- the metric ------------------------------------------------------------------------------------------------

WEIGHTS = {'params': 0.3, 'figures': 0.3, 'chart': 0.25, 'hindi': 0.15}
DEVANAGARI_DIGITS = str.maketrans('०१२३४५६७८९', '0123456789')


def numbers_in(text):
    found = set()
    for m in re.findall(r'\d[\d,]*(?:\.\d+)?', (text or '').translate(DEVANAGARI_DIGITS)):
        v = number(m.rstrip(','))
        if v is not None:
            found.add(round(v))
    return found


def score(facts, case, out):
    """out = {'params', 'chart', 'answer'} as shown to her. Returns (combined, parts, feedback)."""
    exp, notes = case['expected'], []
    p, chart, answer = out.get('params') or {}, out.get('chart'), out.get('answer') or ''
    latin = re.findall(r'[A-Za-z]{2,}', answer)
    parts = {'hindi': 1.0 if answer and not latin else 0.0}
    if latin:
        notes.append(f'The answer must be Hindi only; it has English words ({", ".join(latin[:4])}).')
    if not answer:
        notes.append('There is no answer text.')
    if exp['params']['follow_up']:
        asked = bool(p.get('follow_up'))
        parts['params'] = 1.0 if asked else 0.0
        parts['figures'] = 1.0 if asked and '?' in answer and not (numbers_in(answer) - {0}) else 0.0
        parts['chart'] = 1.0 if not chart else 0.0
        if not asked:
            notes.append('She did not say how much more she would save: set follow_up to one short Hindi question asking '
                         'how much more a month, chart null, and make the answer that question. Never guess an amount.')
        elif parts['figures'] < 1:
            notes.append('For a follow-up, the answer must be just the question (with ?), with no projected figures.')
        if chart:
            notes.append('No chart when a follow-up is asked.')
        combined = sum(WEIGHTS[k] * parts[k] for k in WEIGHTS)
        return round(combined, 4), parts, ' '.join(notes) or 'Correct: the amount was missing, so she is asked first.'
    if p.get('follow_up'):
        notes.append('Wrongly asked a follow-up: the question gives the amount; work it out.')
        parts.update(params=0.0, figures=0.0, chart=0.0)
        return round(WEIGHTS['hindi'] * parts['hindi'], 4), parts, ' '.join(notes)
    want = exp['params']
    checks = [('new_deposit', want['new_deposit']), ('annual_rate', want['annual_rate']), ('lump_sum', want['lump_sum'])]
    checks.append(('target', want['target']) if want['target'] else ('months', want['months']))
    right = 0
    for key, value in checks:
        got = p.get(key) or 0
        if abs(got - (value or 0)) < 0.01:
            right += 1
        else:
            notes.append(f'{key} should be {value} ({explain(case, key)}), got {p.get(key)}.')
    parts['params'] = right / len(checks)
    have = numbers_in(answer)
    figures = [('projected balance', exp['projected_new']), ('added savings', abs(exp['added']))]
    if want['target']:
        figures = [('months to reach the target', exp['months']), ('balance then', exp['projected_new'])]
    hit = 0
    for label, value in figures:
        if any(abs(v - value) <= 1 for v in have):
            hit += 1
        else:
            notes.append(f'The answer should state the {label}: ₹{rupees(value)}.' if 'months' not in label else
                         f'The answer should state the {label}: {value}.')
    total = len(figures)
    if case['kind'] == 'no_horizon':
        total += 1
        if 12 in have or 'एक साल' in answer or 'साल भर' in answer:
            hit += 1
        else:
            notes.append('She gave no period: say in the answer that you took 12 months (एक साल).')
    parts['figures'] = hit / total
    parts['chart'] = chart_score(exp, chart, notes)
    combined = sum(WEIGHTS[k] * parts[k] for k in WEIGHTS)
    return round(combined, 4), parts, ' '.join(notes) or 'Correct: right scenario, exact figures, valid chart, Hindi only.'


def explain(case, key):
    want = case['expected']['params']
    return {'new_deposit': 'what she would deposit a month after the change she describes',
            'annual_rate': 'only a rate she names; SHG savings earn no interest otherwise',
            'lump_sum': 'a one-time deposit she names, else 0',
            'target': 'the sum she wants to reach',
            'months': 'the period she said, or 12 if she gave none' + (' (she gave none)' if case['kind'] == 'no_horizon' else '')}[key] \
        if want else key


def chart_score(exp, chart, notes):
    if not isinstance(chart, dict) or not isinstance(chart.get('series'), list):
        notes.append('The chart is missing or not valid JSON with two series.')
        return 0.0
    series = {str(s.get('name')): s.get('values') for s in chart['series'] if isinstance(s, dict)}
    value = 0.25 if len(chart['series']) == 2 and chart.get('type') == 'line' else 0.0
    if not value:
        notes.append(f'The chart must be type "line" with exactly two series named "{NOW}" and "{NEW}".')
    if isinstance(chart.get('x'), list) and len(chart['x']) == exp['months']:
        value += 0.25
    else:
        notes.append(f'The chart needs one point per month: {exp["months"]} months, got {len(chart.get("x") or [])}.')
    for name, key in ((NOW, 'now'), (NEW, 'new')):
        got = series.get(name) or []
        good = isinstance(got, list) and len(got) == len(exp[key]) and all(
            number(a) is not None and abs(number(a) - b) <= 1 for a, b in zip(got, exp[key]))
        if good:
            value += 0.25
        else:
            first = next((i for i, (a, b) in enumerate(zip(got, exp[key])) if number(a) is None or abs(number(a) - b) > 1), min(len(got), len(exp[key])))
            notes.append(f'Series "{name}" is wrong from month {first + 1}: expected {exp[key][first] if first < len(exp[key]) else "no more values"}'
                         f' (formula: {"balance + k × current deposit" if key == "now" else "balance(k) = round(balance(k−1) × (1 + rate/1200) + new deposit), starting from balance + lump sum"}).')
    return value


# --- DSPy program ---------------------------------------------------------------------------------------------

def dspy_parts(design, facts):
    import dspy

    class Projection(dspy.Signature):
        question: str = dspy.InputField()
        savings: str = dspy.InputField()
        reply: str = dspy.OutputField()

    class RawAdapter(dspy.ChatAdapter):
        """System = instructions + her savings register; user = her question; the reply is kept raw."""

        def __init__(self):
            super().__init__(use_json_adapter_fallback=False)

        def format(self, signature, demos, inputs):
            if 'reply' not in signature.output_fields:
                return super().format(signature, demos, inputs)
            return [{'role': 'system', 'content': f'{signature.instructions}\n\nHer savings register:\n{inputs["savings"]}'},
                    {'role': 'user', 'content': inputs['question']}]

        def parse(self, signature, completion):
            if 'reply' not in signature.output_fields:
                return super().parse(signature, completion)
            return {'reply': completion or ''}

    class Program(dspy.Module):
        def __init__(self, instructions):
            super().__init__()
            self.answer = dspy.Predict(Projection.with_instructions(instructions))

        def forward(self, question, savings, didi):
            raw = self.answer(question=question, savings=savings).reply
            data = parse_json(raw) or {}
            if design == 'full':
                params = clean_params(data.get('params'))
                chart, answer = data.get('chart'), str(data.get('answer') or '')
            else:
                params = clean_params(data)
                chart, answer = code_answer(facts[didi], params)
            return dspy.Prediction(reply=raw, params=params, chart=chart, answer=answer)

    return dspy, Program, RawAdapter


def examples(dspy, facts, splits):
    cases = json.loads(DATASET.read_text(encoding='utf-8'))['cases']
    return [dspy.Example(question=c['question'], savings=facts_text(facts[c['didi']]), didi=c['didi'], case=c)
            .with_inputs('question', 'savings', 'didi') for c in cases if c['split'] in splits]


def gepa_metric(facts):
    import dspy

    def metric(gold, pred, trace=None, pred_name=None, pred_trace=None):
        combined, parts, feedback = score(facts[gold.didi], gold.case, {'params': pred.params, 'chart': pred.chart, 'answer': pred.answer})
        if pred_name is None:
            return combined
        return dspy.Prediction(score=combined, feedback=f'Question kind: {gold.case["kind"]}. Score {combined} '
                                                       f'({", ".join(f"{k} {v:.2f}" for k, v in parts.items())}). {feedback}')

    return metric


def evaluate(program, rows, facts, threads):
    from concurrent.futures import ThreadPoolExecutor

    def one(ex):
        try:
            pred = program(**ex.inputs())
            out = {'params': pred.params, 'chart': pred.chart, 'answer': pred.answer}
        except Exception as error:  # noqa: BLE001 - an unreachable model scores 0 for that case
            out = {'params': {}, 'chart': None, 'answer': '', 'error': str(error)[:200]}
        combined, parts, feedback = score(facts[ex.didi], ex.case, out)
        return {'id': ex.case['id'], 'kind': ex.case['kind'], 'style': ex.case['style'], 'score': combined, 'parts': parts,
                'feedback': feedback, 'out': out}

    with ThreadPoolExecutor(threads) as pool:
        return list(pool.map(one, rows))


def summary(scored):
    parts = {k: round(sum(s['parts'][k] for s in scored) / len(scored), 3) for k in WEIGHTS}
    kinds = defaultdict(list)
    for s in scored:
        kinds[s['kind']].append(s['score'])
    return {'score': round(sum(s['score'] for s in scored) / len(scored), 4), 'parts': parts,
            'by_kind': {k: round(sum(v) / len(v), 3) for k, v in sorted(kinds.items())}}


def save_result(row):
    results = json.loads(RESULTS.read_text(encoding='utf-8')) if RESULTS.exists() else []
    RESULTS.write_text(json.dumps(results + [row], ensure_ascii=False, indent=1), encoding='utf-8')


def run_eval(args):
    facts = json.loads(FACTS.read_text(encoding='utf-8'))
    dspy, Program, RawAdapter = dspy_parts(args.design, facts)
    dspy.configure(lm=make_lm(args.model, max_tokens=2500), adapter=RawAdapter())
    instructions = PROMPTS[args.design][args.prompt]
    label = f'{args.design}/{args.prompt}'
    if args.program:
        instructions = json.loads((ROOT / args.program).read_text(encoding='utf-8'))['instructions']
        label = args.program
    rows = examples(dspy, facts, args.split.split(','))[:args.limit or None]
    scored = evaluate(Program(instructions), rows, facts, args.threads)
    row = {'when': time.strftime('%Y-%m-%d %H:%M'), 'model': args.model, 'design': args.design, 'prompt': label,
           'split': args.split, 'n': len(scored), **summary(scored), 'cases': scored}
    save_result(row)
    print(f'{label} on {args.model} [{args.split}]: {row["score"]} {row["parts"]}\n{row["by_kind"]}')
    return row


def run_optimize(args):
    facts = json.loads(FACTS.read_text(encoding='utf-8'))
    dspy, Program, RawAdapter = dspy_parts(args.design, facts)
    dspy.configure(lm=make_lm(args.model, max_tokens=2500), adapter=RawAdapter())
    train, val = examples(dspy, facts, ['train']), examples(dspy, facts, ['val'])
    reflection = make_lm(args.reflection, max_tokens=8000)
    reflection.kwargs['temperature'] = 1.0
    name = args.name or f'{args.design}-{args.prompt}-{args.reflection.split("/")[-1]}'
    optimizer = dspy.GEPA(metric=gepa_metric(facts), max_metric_calls=args.budget, reflection_lm=reflection,
                          num_threads=args.threads, reflection_minibatch_size=args.minibatch, track_stats=True,
                          seed=args.seed, log_dir=str(RUNS / name))
    optimized = optimizer.compile(Program(PROMPTS[args.design][args.prompt]), trainset=train, valset=val)
    d = optimized.detailed_results
    candidates = [{'instructions': c.answer.signature.instructions, 'val': s, 'found_at_calls': n, 'parents': par}
                  for c, s, n, par in zip(d.candidates, d.val_aggregate_scores, d.discovery_eval_counts, d.parents)]
    out = {'name': name, 'design': args.design, 'prompt': args.prompt, 'model': args.model, 'teacher': args.reflection,
           'budget': args.budget, 'total_metric_calls': d.total_metric_calls, 'best': d.best_idx,
           'teacher_cost_usd': round(getattr(reflection, 'cost', 0) or 0, 2), 'candidates': candidates,
           'instructions': d.candidates[d.best_idx].answer.signature.instructions}
    (OUT / f'projection_program-{name}.json').write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding='utf-8')
    print(f'saved projection_program-{name}.json: val {d.val_aggregate_scores[0]:.3f} -> {max(d.val_aggregate_scores):.3f} '
          f'over {len(candidates)} candidates; teacher cost ${out["teacher_cost_usd"]}')


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('facts')
    sub.add_parser('dataset')
    for name in ('eval', 'optimize'):
        p = sub.add_parser(name)
        p.add_argument('--design', choices=['full', 'extract'], default='full')
        p.add_argument('--prompt', choices=['naive', 'planner', 'hand'], default='hand')
        p.add_argument('--model', default=GEMMA)
        p.add_argument('--threads', type=int, default=2)
        if name == 'eval':
            p.add_argument('--program', help='a saved eval/dspy/projection_program-*.json')
            p.add_argument('--split', default='test')
            p.add_argument('--limit', type=int, default=0)
        else:
            p.add_argument('--reflection', default='claude-cli/claude-opus-5-5')
            p.add_argument('--budget', type=int, default=300)
            p.add_argument('--minibatch', type=int, default=5)
            p.add_argument('--seed', type=int, default=0)
            p.add_argument('--name')
    sub.add_parser('report')
    args = parser.parse_args()
    if args.command == 'report':
        import projection_report
        return projection_report.main()
    {'facts': dump_facts, 'dataset': build_dataset, 'eval': lambda: run_eval(args), 'optimize': lambda: run_optimize(args)}[args.command]()


if __name__ == '__main__':
    main()
