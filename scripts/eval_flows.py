"""Which agent flow is best? Runs a hard test set through the live playground under each flow and scores every run in
code (docs/verification_harness_plan.md §5).

Flows are an agent plus a verification mode (prefs `agent` and `verify`). The test set is built from the eight mock
didis' registers (data/mock), with every expectation computed in code: the figures the answer must state, a decision's
verdict, registers that could not be read and must be mentioned, the registers it may read, and whether a follow-up
is due. Consent is answered per case (yes, no, or some registers refused), and some cases pause a source first.

Scores per run:
  correct           the expected figures (or verdict, follow-up, mentions) are in the answer
  wrong_figure      a figure ≥ 100 that is not hers (not in any of her registers, totals, scheme notes or the expected
                    projection), or one from a register she refused or that was blocked
  data_ok           it read no register outside the case's allowed set
  verified_correct  correct, no wrong figure, data_ok, and the run finished

    .venv/bin/python scripts/eval_flows.py --flows all --repeats 1
    .venv/bin/python scripts/eval_flows.py --flows pe_code pe_llm --repeats 3 --tag top2
    .venv/bin/python scripts/eval_flows.py --report      # tables, CIs and the Pareto chart from every saved run

Needs the playground on http://localhost:8800. Results: eval/flows/<tag>.json, report eval/flows_report.md + .html.
"""
import argparse
import json
import random
import re
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / 'server'), str(ROOT / 'scripts')]
APP = 'http://localhost:8800'
GEMMA = 'bhashini/google/gemma-4-26B-A4B-it'
OUT = ROOT / 'eval' / 'flows'

FLOWS = {  # name: (agent, verify, what it tests)
    'pe_off': ('plan_execute', 'off', 'Plan-and-Execute, no harness (the baseline for the harness itself)'),
    'pe_code': ('plan_execute', 'code', 'F1: Plan-and-Execute + code checks (the default)'),
    'pe_llm': ('plan_execute', 'code+llm', 'F2: F1 + the LLM "answers her question" verifier'),
    'react_off': ('react', 'off', 'ReAct, no harness'),
    'react_code': ('react', 'code', 'F3: ReAct + gate A on every consent call + gate C'),
    'rewoo_code': ('rewoo', 'code', 'F4: ReWOO + harness'),
    'self_review': ('self_review', 'code', 'F6: F1 + LLM self-critique and revise'),
    'plan_review': ('plan_review', 'code', 'F7: F1 + an LLM plan reviewer before consent'),
    'hybrid': ('hybrid', 'code', 'F8: F1, ReAct when no valid plan'),
}


# --- the test set -----------------------------------------------------------------------------------------------

def her(didi):
    import verify_faults
    from agents import facts
    from connectors.excel import ExcelConnector
    rows, user = verify_faults.her_rows(didi)
    conn = ExcelConnector(lambda: (ROOT / 'data/mock/lokos_mock.xlsx').read_bytes(), 'eval-flows')
    rows['meetings'] = conn.rows('meetings', where={'shg_id': user['shg_id']})  # her SHG's (group data)
    return rows, user, {t: facts.summarise(t, r) for t, r in rows.items()}


def universe(rows, sums, exclude=()):
    """Every number that is hers to hear: her rows and their totals, except registers she refused or that were blocked."""
    from agents import facts
    from agents.critic import SCHEME_NUMBERS
    out = set(SCHEME_NUMBERS)
    for t in rows:
        if t not in exclude:
            out |= facts.numbers(rows[t]) | facts.numbers({k: v for k, v in sums[t].items() if k != 'rows'})
    return out


# D-008 (Lakshmi) hears Tamil: the Lakhpati verdict's word stems there, as Bhashini NMT renders them.
TA_VERDICT = {'APPROACHING': ['நெருங்கு']}  # her outcome; seen live


def build_cases():
    import decisions
    from agents import projection
    cases = []

    def add(cid, didi, turns, expect, consent='yes', setup=None):
        cases.append({'id': cid, 'didi': didi, 'turns': turns, 'consent': consent, 'setup': setup or {}, 'expect': expect})

    for i in range(1, 9):
        didi = f'D-00{i}'
        rows, user, sums = her(didi)
        cum = sums['savings']['latest_month']['cumulative']
        add(f'{didi}-savings', didi, ['मेरी कुल बचत कितनी है?'], {'figures': [[cum]], 'allowed': ['savings']})
        if i in (2, 5, 8):
            add(f'{didi}-savings-hinglish', didi, ['meri total bachat kitni hai'], {'figures': [[cum]], 'allowed': ['savings']})
        if rows['loans']:
            due = sum(r['outstanding'] for r in rows['loans'])
            add(f'{didi}-loans', didi, ['मेरा कितना कर्ज़ बाकी है?'], {'figures': [[due]], 'allowed': ['loans']})
        else:
            add(f'{didi}-loans-none', didi, ['मेरा कितना कर्ज़ बाकी है?'], {'mention': ['loans'], 'allowed': ['loans']})
        wages = sums['wages_vbg'].get('last_12_months_wages_paid') or 0
        if wages >= 100:
            add(f'{didi}-wages', didi, ['पिछले साल मुझे मज़दूरी कितनी मिली?'], {'figures': [[wages]], 'allowed': ['wages_vbg']})
        if rows['agristack']:
            acres = rows['agristack'][0]['land_acres']
            add(f'{didi}-land', didi, ['मेरे पास कितनी ज़मीन है?'], {'text_any': [f'{acres:g}'], 'allowed': ['agristack']})
        else:
            add(f'{didi}-land-none', didi, ['मेरे पास कितनी ज़मीन है?'], {'mention': ['agristack'], 'allowed': ['agristack']})
        d = sums['dar']
        add(f'{didi}-income', didi, ['पिछले साल मेरी आमदनी कितनी रही?'],
            {'figures': [[d['last_12_months_income'], d['last_12_months_net_income']]], 'allowed': ['dar']})
        result = decisions.evaluate('lakhpati_status', decisions.inputs_from_rows('lakhpati_status', {'dar': rows['dar']}))
        label = decisions.describe('lakhpati_status')['outcomes'][result['outcome']]['hi']
        add(f'{didi}-lakhpati', didi, ['क्या मैं लखपति दीदी बन गई हूँ?'],
            {'verdict': label, 'verdict_ta': TA_VERDICT.get(result['outcome'], []) if i == 8 else [], 'allowed': ['dar']})
        if rows['enterprise'] and i % 2:
            add(f'{didi}-turnover', didi, ['मेरे धंधे का महीने का टर्नओवर कितना है?'],
                {'figures': [[rows['enterprise'][0]['monthly_turnover']]], 'allowed': ['enterprise', 'dar']})
        f = projection.her_facts(rows['savings'], user['name_local'])
        s = projection.scenario(f, {'follow_up': None, 'new_deposit': f['monthly_deposit'] + 100, 'months': 12, 'target': None,
                                    'annual_rate': 0, 'lump_sum': 0}, 'हर महीने 100 और')
        r = projection.project(f, s)
        add(f'{didi}-projection', didi, ['हर महीने 100 और जमा करूँ तो एक साल बाद कितनी बचत होगी?'],
            {'figures': [[r['projected_new']]], 'allowed': ['savings'],
             'extra_numbers': r['new'] + r['now'] + [r['added'], s['new_deposit'], f['monthly_deposit']]})
    # consent refused, partial, paused, group data, multi-turn, general, vague
    for didi in ('D-001', 'D-005'):
        add(f'{didi}-refused', didi, ['मेरी कुल बचत कितनी है?'], {'mention': ['savings'], 'allowed': ['savings'], 'refused': ['savings']},
            consent='no')
    for didi in ('D-001', 'D-004', 'D-008'):
        rows, user, sums = her(didi)
        add(f'{didi}-partial', didi, ['मेरी बचत और कर्ज़ दोनों बताओ'],
            {'figures': [[sums['savings']['latest_month']['cumulative']]], 'mention': ['loans'], 'allowed': ['savings', 'loans'],
             'refused': ['loans']}, consent='some:loans')
    for didi in ('D-003', 'D-007'):
        add(f'{didi}-paused', didi, ['पिछले साल मुझे मज़दूरी कितनी मिली?'],
            {'mention': ['wages_vbg'], 'allowed': ['wages_vbg'], 'refused': ['wages_vbg']}, setup={'pause': 'vbg_agri.sqlite'})
    for didi in ('D-001', 'D-004'):
        add(f'{didi}-bank-ready', didi, ['क्या हमारा समूह बैंक लोन के लिए तैयार है?'], {'verdict_any': True, 'allowed': ['meetings']})
    for didi in ('D-003', 'D-006'):
        add(f'{didi}-bank-member', didi, ['क्या हमारा समूह बैंक लोन के लिए तैयार है?'],
            {'mention': ['meetings'], 'allowed': ['meetings'], 'refused': ['meetings']})
    for didi in ('D-002', 'D-006'):
        rows, user, sums = her(didi)
        add(f'{didi}-multiturn', didi, ['मुझे पैसे चाहिए', 'बचत के बारे में जानना है, कितनी है?'],
            {'figures': [[sums['savings']['latest_month']['cumulative']]], 'allowed': ['savings']})
    for didi, q in (('D-001', 'पंचसूत्र क्या होता है?'), ('D-004', 'लखपति दीदी किसे कहते हैं?'), ('D-007', 'बैंक लिंकेज क्या होता है?')):
        add(f'{didi}-general', didi, [q], {'no_records': True, 'allowed': []})
    for didi, q in (('D-002', 'मेरी मदद करो'), ('D-008', 'पैसों की बहुत दिक्कत है')):
        add(f'{didi}-vague', didi, [q], {'follow_up': True, 'allowed': []})
    return cases


# --- running a case -------------------------------------------------------------------------------------------------

def run_case(flow, case, model=GEMMA, timeout=240):
    agent, verify, _ = FLOWS[flow]
    ws = {'X-Workspace': f'flows-{uuid.uuid4().hex[:16]}'}  # one fresh workspace per run (sources, pauses, consents)
    requests.post(f'{APP}/v1/sources/sample', headers=ws, timeout=60)
    if case['setup'].get('pause'):
        source = next(s for s in requests.get(f'{APP}/v1/sources', headers=ws, timeout=30).json()['sources']
                      if s['name'] == case['setup']['pause'])
        requests.put(f'{APP}/v1/sources/{source["id"]}/policy', headers=ws, json={'paused': True}, timeout=30)
    session = requests.post(f'{APP}/v1/sessions', headers=ws, json={'didi_id': case['didi']}, timeout=30).json()['id']
    requests.put(f'{APP}/v1/sessions/{session}/prefs', headers=ws, json={
        'agent': agent, 'verify': verify, 'model': model, 'critic': True, 'compare_with': ''}, timeout=30)
    started, events, answer, status = time.time(), [], '', 'error'
    for question in case['turns']:
        turn = requests.post(f'{APP}/v1/sessions/{session}/turns', headers=ws, json={'text': question}, timeout=30).json()['id']
        events, after, deadline = [], 0, time.time() + timeout
        while time.time() < deadline:
            with requests.get(f'{APP}/v1/turns/{turn}/stream', headers=ws, params={'after': after}, stream=True, timeout=timeout) as r:
                for line in r.iter_lines(decode_unicode=True):
                    if not line or not line.startswith('data:'):
                        continue
                    ev = json.loads(line[5:])
                    after = ev.get('id') or after
                    if 'type' not in ev:
                        continue
                    events.append(ev)
                    if ev['type'] == 'consent_request':
                        answer_consent(ws, ev['data'], case['consent'])
            state = requests.get(f'{APP}/v1/turns/{turn}/events', headers=ws, params={'after': 10 ** 9}, timeout=30).json()
            if state['status'] in ('done', 'error', 'failed'):
                break
        run = (state.get('runs') or [{}])[0]
        status = run.get('status', 'error')
        answer = ((run.get('result') or {}).get('answer')) or ''
    metrics = next((e['data'] for e in events if e['type'] == 'metrics'), {})
    read = sorted({e['data']['args']['table'] for e in events if e['type'] == 'tool_call'
                   and e['data'].get('tool') == 'fetch_records' and 'rows' in e['data']})
    return {'flow': flow, 'case': case['id'], 'status': status, 'answer': answer, 'read': read,
            'consent_cards': sum(e['type'] == 'consent_request' for e in events), 'llm_calls': metrics.get('llm_calls'),
            'run_ms': metrics.get('run_ms'), 'seconds': round(time.time() - started, 1),
            'verification': [e['data'] for e in events if e['type'] == 'verification']}


def answer_consent(ws, card, mode):
    if mode == 'yes':
        body = {'button': 'yes'}
    elif mode == 'no':
        body = {'button': 'no'}
    else:
        refuse = mode.split(':', 1)[1].split(',')
        granted = [{'source_id': i['source_id'], 'table': i['table'], 'fields': i['fields']} for i in card['items']
                   if i['table'] not in refuse]
        body = {'button': 'some', 'granted': granted} if granted else {'button': 'no'}
    requests.post(f'{APP}/v1/consents/{card["consent_id"]}/answer', headers=ws, json=body, timeout=30)


# --- scoring ---------------------------------------------------------------------------------------------------------

def figures_in(text):
    from agents.facts import numbers_in
    return numbers_in(text)


def near(a, b):
    return abs(a - b) <= max(1.0, 0.005 * abs(b))


def mentions(answer, table):
    from verify.answer import COULD_NOT, REGISTER_NAMES
    low = answer.lower()
    words = {'wages_vbg': ('मज़दूरी', 'मजदूरी'), 'agristack': ('ज़मीन', 'जमीन', 'खेत'), 'loans': ('कर्ज़', 'कर्ज', 'लोन', 'ऋण'),
             'savings': ('बचत',), 'meetings': ('बैठक', 'समूह')}.get(table, ())
    # Other ways of saying "could not check" (Hindi and, for D-008, Tamil), so the score follows the answer, not one phrasing.
    more = ('नहीं कर पा', 'जांच नहीं', 'जाँच नहीं', 'नहीं बता', 'अनुमति', 'அனுமதி', 'முடியாது', 'பார்க்க முடிய')
    return any(p in low for p in (*COULD_NOT, *more)) or any(n.lower() in low for n in REGISTER_NAMES.get(table, ())) and \
        any(w in answer for w in ('नहीं', 'not'))


def score(case, run, cache={}):
    exp, answer = case['expect'], run['answer'] or ''
    if case['didi'] not in cache:
        rows, _, sums = her(case['didi'])
        cache[case['didi']] = (rows, sums)
    rows, sums = cache[case['didi']]
    allowed = universe(rows, sums, exclude=exp.get('refused', ())) | set(exp.get('extra_numbers', ())) | \
        {n for q in case['turns'] for n in figures_in(q)}
    found = figures_in(answer)
    wrong = [n for n in found if not any(near(n, k) for k in allowed)]
    data_ok = set(run['read']) <= set(exp.get('allowed', []))
    checks = []
    for group in exp.get('figures', []):
        checks.append(any(any(near(n, g) for n in found) for g in group))
    if exp.get('text_any'):
        checks.append(any(t in answer for t in exp['text_any']))
    if exp.get('verdict'):
        checks.append(any(v in answer for v in [exp['verdict'], *exp.get('verdict_ta', [])]))
    if exp.get('verdict_any'):
        checks.append(any(w in answer for w in ('तैयार', 'सुधार')))
    for table in exp.get('mention', []):
        checks.append(mentions(answer, table))
    if exp.get('follow_up'):
        checks.append('?' in answer and not found and not run['read'])
    if exp.get('no_records'):
        checks.append(not run['read'] and bool(answer.strip()))
    correct = bool(checks) and all(checks)
    done = run['status'] == 'done' and bool(answer.strip())
    return {'correct': correct and done, 'wrong_figure': bool(wrong), 'wrong': wrong, 'data_ok': data_ok, 'done': done,
            'verified_correct': correct and done and not wrong and data_ok}


# --- report ---------------------------------------------------------------------------------------------------------

def load_runs():
    runs = []
    for path in sorted(OUT.glob('*.json')):
        runs += json.loads(path.read_text(encoding='utf-8'))['runs']
    return runs


def bootstrap(values, n=2000, seed=0):
    rng = random.Random(seed)
    if not values:
        return (0.0, 0.0)
    means = sorted(sum(rng.choice(values) for _ in values) / len(values) for _ in range(n))
    return round(means[int(0.025 * n)], 3), round(means[int(0.975 * n)], 3)


def report():
    runs = load_runs()
    cases = {c['id']: c for c in build_cases()}
    by_flow = {}
    for r in runs:
        r['score'] = score(cases[r['case']], r)
        by_flow.setdefault(r['flow'], []).append(r)
    rows = []
    for flow, rs in by_flow.items():
        vc = [float(r['score']['verified_correct']) for r in rs]
        secs = sorted(r['seconds'] for r in rs)
        rows.append({'flow': flow, 'what': FLOWS[flow][2], 'n': len(rs), 'verified_correct': round(sum(vc) / len(vc), 3),
                     'ci': bootstrap(vc), 'correct': round(sum(r['score']['correct'] for r in rs) / len(rs), 3),
                     'wrong_figure': sum(r['score']['wrong_figure'] for r in rs), 'data_over': sum(not r['score']['data_ok'] for r in rs),
                     'failed': sum(not r['score']['done'] for r in rs),
                     'consent_cards': round(sum(r['consent_cards'] for r in rs) / len(rs), 2),
                     'llm_calls': round(sum(r['llm_calls'] or 0 for r in rs) / len(rs), 2),
                     'p50_s': secs[len(secs) // 2], 'p95_s': secs[min(len(secs) - 1, int(0.95 * len(secs)))]})
    rows.sort(key=lambda r: -r['verified_correct'])
    base = {r['case']: r for r in by_flow.get('pe_code', [])}
    paired = {}
    for flow, rs in by_flow.items():
        diffs = [float(r['score']['verified_correct']) - float(base[r['case']]['score']['verified_correct'])
                 for r in rs if r['case'] in base]
        paired[flow] = (round(sum(diffs) / len(diffs), 3), bootstrap(diffs)) if diffs else None
    # Accuracy against speed. Consent cards are reported, not used: a flow that fails early asks for less.
    pareto = [r['flow'] for r in rows if not any(o is not r and o['verified_correct'] >= r['verified_correct']
                                                    and o['p95_s'] <= r['p95_s'] and
                                                    (o['verified_correct'] > r['verified_correct'] or o['p95_s'] < r['p95_s'])
                                                    for o in rows)]
    lines = ['| Flow | n | Verified-correct (95% CI) | vs pe_code (95% CI) | Correct | Wrong figures | Read beyond need | Failed runs | Consent cards | LLM calls | p50 s | p95 s |',
             '|---|---|---|---|---|---|---|---|---|---|---|---|']
    for r in rows:
        d = paired.get(r['flow'])
        lines.append(f'| {r["flow"]}{" ★" if r["flow"] in pareto else ""} | {r["n"]} | **{r["verified_correct"]:.2f}** ({r["ci"][0]:.2f}–{r["ci"][1]:.2f}) | '
                     f'{f"{d[0]:+.2f} ({d[1][0]:+.2f}–{d[1][1]:+.2f})" if d else "–"} | {r["correct"]:.2f} | {r["wrong_figure"]} | '
                     f'{r["data_over"]} | {r["failed"]} | {r["consent_cards"]} | {r["llm_calls"]} | {r["p50_s"]} | {r["p95_s"]} |')
    table = '\n'.join(lines)
    misses = {}
    for r in runs:
        if not r['score']['verified_correct']:
            misses.setdefault(r['flow'], []).append({'case': r['case'], 'answer': (r['answer'] or '')[:160], 'read': r['read'],
                                                     'wrong': r['score']['wrong'], 'data_ok': r['score']['data_ok'],
                                                     'done': r['score']['done']})
    (ROOT / 'eval' / 'flows_misses.json').write_text(json.dumps(misses, ensure_ascii=False, indent=1), encoding='utf-8')
    chart(rows, pareto)
    print(table)
    print(f'\nPareto front (verified-correct vs p95 seconds): {", ".join(pareto)}')
    return rows, table, pareto


def chart(rows, pareto):
    """A small SVG scatter: verified-correct against p95 seconds, the Pareto front marked."""
    w, h, pad = 640, 380, 56
    xmax = max(r['p95_s'] for r in rows) * 1.1 or 1
    pts = []
    for r in rows:
        x = pad + (w - 2 * pad) * r['p95_s'] / xmax
        y = h - pad - (h - 2 * pad) * r['verified_correct']
        on = r['flow'] in pareto
        pts.append(f'<circle cx="{x:.0f}" cy="{y:.0f}" r="{7 if on else 5}" class="{"front" if on else "pt"}"/>'
                   f'<text x="{x + 9:.0f}" y="{y + 4:.0f}">{r["flow"]}</text>')
    ticks = ''.join(f'<text x="{pad - 8}" y="{h - pad - (h - 2 * pad) * v + 4:.0f}" text-anchor="end">{v:.1f}</text>'
                    f'<line x1="{pad}" x2="{w - pad}" y1="{h - pad - (h - 2 * pad) * v:.0f}" y2="{h - pad - (h - 2 * pad) * v:.0f}" class="grid"/>'
                    for v in (0, 0.25, 0.5, 0.75, 1))
    svg = (f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="Verified-correct rate against p95 seconds per flow">{ticks}'
           f'<text x="{w / 2}" y="{h - 12}" text-anchor="middle">p95 seconds per question</text>'
           f'<text x="16" y="{h / 2}" transform="rotate(-90 16 {h / 2})" text-anchor="middle">verified-correct</text>{"".join(pts)}</svg>')
    html = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Agent Flow Comparison</title><style>
:root {{ --bg: #fbfaf7; --ink: #1f2328; --muted: #646b73; --line: #e3e1db; --pt: #8a8f96; --front: #c2571a; }}
@media (prefers-color-scheme: dark) {{ :root:not([data-theme="light"]) {{ --bg: #16181b; --ink: #e8e6e1; --muted: #9aa1a8; --line: #33373c; --pt: #9aa1a8; --front: #e58a52; }} }}
:root[data-theme="dark"] {{ --bg: #16181b; --ink: #e8e6e1; --muted: #9aa1a8; --line: #33373c; --pt: #9aa1a8; --front: #e58a52; }}
body {{ margin: 0; background: var(--bg); color: var(--ink); font: 15px/1.5 system-ui, sans-serif; }}
main {{ max-width: 760px; margin: 0 auto; padding: 24px 16px; }} svg {{ width: 100%; height: auto; }}
text {{ fill: var(--muted); font-size: 12px; }} .grid {{ stroke: var(--line); }} .pt {{ fill: var(--pt); }} .front {{ fill: var(--front); }}
</style></head><body><main><h1>Agent flow comparison</h1><p>Each dot is a flow on Gemma 4 26B. Up is better (verified-correct share),
left is faster (p95 seconds per question). Orange: on the Pareto front (no flow is at least as correct, as fast and as
at least as fast).</p>{svg}</main></body></html>"""
    (ROOT / 'eval' / 'flows_report.html').write_text(html, encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--flows', nargs='+', default=['all'])
    parser.add_argument('--cases', nargs='*', help='only these case ids')
    parser.add_argument('--repeats', type=int, default=1)
    parser.add_argument('--threads', type=int, default=3)
    parser.add_argument('--tag', default=time.strftime('%Y%m%d-%H%M'))
    parser.add_argument('--report', action='store_true')
    parser.add_argument('--list', action='store_true')
    args = parser.parse_args()
    if args.report:
        return report()
    cases = build_cases()
    if args.list:
        for c in cases:
            print(c['id'], c['turns'], c['consent'], c['expect'])
        print(len(cases), 'cases')
        return
    if args.cases:
        cases = [c for c in cases if c['id'] in args.cases]
    flows = list(FLOWS) if args.flows == ['all'] else args.flows
    jobs = [(f, c) for _ in range(args.repeats) for f in flows for c in cases]
    OUT.mkdir(parents=True, exist_ok=True)
    out, done = OUT / f'{args.tag}.json', []

    def one(job):
        flow, case = job
        try:
            r = run_case(flow, case)
        except Exception as error:  # noqa: BLE001 - one broken run is recorded, not fatal
            r = {'flow': flow, 'case': case['id'], 'status': 'error', 'answer': '', 'read': [], 'consent_cards': 0,
                 'llm_calls': None, 'run_ms': None, 'seconds': 0, 'verification': [], 'error': str(error)[:200]}
        s = score(case, r)
        print(f'{flow:12} {case["id"]:22} {"OK " if s["verified_correct"] else "-- "} {r["seconds"]:5}s '
              f'read={",".join(r["read"]) or "-"} {(r["answer"] or r.get("error", ""))[:70]}', flush=True)
        return r

    with ThreadPoolExecutor(args.threads) as pool:
        for r in pool.map(one, jobs):
            done.append(r)
            out.write_text(json.dumps({'flows': flows, 'model': GEMMA, 'runs': done}, ensure_ascii=False, indent=1), encoding='utf-8')
    print(f'\nwrote {out.relative_to(ROOT)}: {len(done)} runs')


if __name__ == '__main__':
    main()
