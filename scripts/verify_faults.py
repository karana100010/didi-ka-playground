"""Fault injection for the verification harness: does each check catch real mistakes, and leave good answers alone?

Known-good cases are built from the eight mock didis' real registers (data/mock): an answer worded the way the
app words it, with the evidence a run would have (her rows, their exact totals, a decision's outcome, a register
she refused). Each is then broken in one way, and gate C (server/verify/answer.py) must flag it:

  figure_off        a figure changed by 3–20%                     (figures check)
  wrong_register    the figure credited to another register        (attribution)
  wrong_period      "the last 12 months" said as "this month"      (period)
  flip_verdict      a decision outcome she did not get             (verdict)
  drop_obligation   the "could not see your loan register" line    (obligations)
  english           an English sentence added                      (form)
  other_question    another question's answer, its register unread (figures; a read one needs an LLM verifier)

Gate A (server/verify/plan.py) gets the planner dataset's expected plans (eval/dspy/planner_dataset.json) as the
good cases, broken by: extra_table (a register her question does not name), swap_table, id_field.

    .venv/bin/python scripts/verify_faults.py            # prints the table, writes eval/verify_faults_report.json
"""
import json
import random
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'server'))

TARGETS = {'recall': 0.95, 'false_alarm': 0.02}
GATE_C_FAULTS = ('figure_off', 'wrong_register', 'wrong_period', 'flip_verdict', 'drop_obligation', 'english')
GATE_A_FAULTS = ('extra_table', 'id_field')
REPORTED_ONLY = ('other_question', 'swap_table')  # measured, not held to the target (see the docstring)


def her_rows(didi):
    from connectors.excel import ExcelConnector
    conn = ExcelConnector(lambda: (ROOT / 'data/mock/lokos_mock.xlsx').read_bytes(), 'verify-faults')
    rows = {t: conn.rows(t, where={'didi_id': didi}) for t in ('savings', 'loans', 'dar', 'enterprise')}
    household = conn.rows('users', where={'didi_id': didi})[0]['household_id']
    db = sqlite3.connect(ROOT / 'data/mock/vbg_agri.sqlite')
    db.row_factory = sqlite3.Row
    for table in ('wages_vbg', 'agristack'):
        rows[table] = [{k: r[k] for k in r.keys() if k != 'household_id'}
                       for r in db.execute(f'select * from {table} where household_id = ?', (household,))]
    for table in rows:
        for r in rows[table]:
            r.pop('didi_id', None)
    return rows, conn.rows('users', where={'didi_id': didi})[0]


def rupees(n):
    from decisions.engine import indian
    return '₹' + indian(round(n))


def evidence(question, read, decisions=(), obligations=()):
    """Gate C evidence for answers built from `read` = {table: rows}, as RunContext.fetch would record it."""
    from agents import facts
    from agents.critic import SCHEME_NUMBERS
    from verify import provenance
    from verify.answer import Evidence
    known, fx = set(SCHEME_NUMBERS), {}
    for table, rows in read.items():
        totals = {k: v for k, v in facts.summarise(table, rows).items() if k != 'rows'}
        known |= facts.numbers(rows) | facts.numbers(totals)
        provenance.merge(fx, provenance.from_rows(table, rows, totals))
    return Evidence(question=question, language='hi', known=known, facts=fx, read=bool(read), decisions=list(decisions),
                    obligations=list(obligations))


def good_answers(didi):
    """[(kind, question, answer, evidence, register name used, the one-month/year figure)] for one didi."""
    import decisions
    from agents import facts
    rows, user = her_rows(didi)
    name = user['name_local'].split()[0]
    out = []
    s = facts.summarise('savings', rows['savings'])
    if rows['savings']:
        cum = s['latest_month']['cumulative']
        out.append(('savings', 'मेरी कुल बचत कितनी है?', f'{name} जी, आपके बचत रजिस्टर के अनुसार आपकी कुल बचत {rupees(cum)} है।',
                    evidence('मेरी कुल बचत कितनी है?', {'savings': rows['savings']}), 'बचत रजिस्टर'))
    if rows['loans']:
        due = sum(r['outstanding'] for r in rows['loans'])
        out.append(('loans', 'मेरा कितना कर्ज़ बाकी है?', f'{name} जी, आपके कर्ज़ रजिस्टर के अनुसार आपका कुल {rupees(due)} कर्ज़ बाकी है।',
                    evidence('मेरा कितना कर्ज़ बाकी है?', {'loans': rows['loans']}), 'कर्ज़ रजिस्टर'))
    else:
        out.append(('loans_none', 'मेरा कितना कर्ज़ बाकी है?', f'{name} जी, आपके कर्ज़ रजिस्टर में आपके नाम से कुछ दर्ज नहीं है।',
                    evidence('मेरा कितना कर्ज़ बाकी है?', {'loans': []}, obligations=[{'table': 'loans', 'reason': 'no_rows'}]),
                    'कर्ज़ रजिस्टर'))
    d = facts.summarise('dar', rows['dar'])
    if rows['dar']:
        year = d['last_12_months_income']
        out.append(('income_year', 'पिछले साल मेरी आमदनी कितनी रही?',
                    f'{name} जी, आपके डिजिटल आजीविका रजिस्टर के अनुसार पिछले 12 महीनों में आपकी कुल आमदनी {rupees(year)} रही।',
                    evidence('पिछले साल मेरी आमदनी कितनी रही?', {'dar': rows['dar']}), 'डिजिटल आजीविका रजिस्टर'))
        month = d['latest_month']['income']
        if month >= 100 and month != year:
            out.append(('income_month', 'इस महीने मेरी कमाई कितनी हुई?',
                        f'{name} जी, आपके डिजिटल आजीविका रजिस्टर के अनुसार इस महीने आपकी आमदनी {rupees(month)} रही।',
                        evidence('इस महीने मेरी कमाई कितनी हुई?', {'dar': rows['dar']}), 'डिजिटल आजीविका रजिस्टर'))
        result = decisions.evaluate('lakhpati_status', decisions.inputs_from_rows('lakhpati_status', {'dar': rows['dar']}))
        outcomes = decisions.describe('lakhpati_status')['outcomes']
        label = outcomes[result['outcome']]['hi']
        made = {'decision_id': 'lakhpati_status', 'outcome': result['outcome'],
                'labels': {k: [v['hi'], v['en']] for k, v in outcomes.items()}}
        out.append(('lakhpati', 'क्या मैं लखपति दीदी हूँ?',
                    f'{name} जी, आपके डिजिटल आजीविका रजिस्टर के अनुसार पिछले 12 महीनों में आपके परिवार की आमदनी '
                    f'{rupees(result["inputs"]["annual_income"])} रही। नियम के अनुसार आप "{label}" हैं।',
                    evidence('क्या मैं लखपति दीदी हूँ?', {'dar': rows['dar']}, decisions=[made]), 'डिजिटल आजीविका रजिस्टर'))
    if rows['wages_vbg']:
        w = facts.summarise('wages_vbg', rows['wages_vbg'])
        out.append(('wages', 'पिछले साल मुझे मज़दूरी कितनी मिली?',
                    f'{name} जी, आपके मज़दूरी रिकॉर्ड के अनुसार पिछले 12 महीनों में आपको {rupees(w["last_12_months_wages_paid"])} मज़दूरी मिली।',
                    evidence('पिछले साल मुझे मज़दूरी कितनी मिली?', {'wages_vbg': rows['wages_vbg']}), 'मज़दूरी रिकॉर्ड'))
    if rows['enterprise']:
        turnover = rows['enterprise'][0]['monthly_turnover']
        out.append(('enterprise', 'मेरे धंधे का टर्नओवर कितना है?',
                    f'{name} जी, आपके उद्यम रजिस्टर के अनुसार आपके धंधे का महीने का टर्नओवर {rupees(turnover)} है।',
                    evidence('मेरे धंधे का टर्नओवर कितना है?', {'enterprise': rows['enterprise']}), 'उद्यम रजिस्टर'))
    if rows['savings']:
        out.append(('refused_loans', 'मेरी बचत और कर्ज़ बताओ',
                    f'{name} जी, आपके बचत रजिस्टर के अनुसार आपकी कुल बचत {rupees(s["latest_month"]["cumulative"])} है। '
                    'आपका कर्ज़ रजिस्टर मैं नहीं देख पाई।',
                    evidence('मेरी बचत और कर्ज़ बताओ', {'savings': rows['savings']},
                             obligations=[{'table': 'loans', 'reason': 'refused'}]), 'बचत रजिस्टर'))
    return out


OTHER_REGISTERS = ['बचत रजिस्टर', 'कर्ज़ रजिस्टर', 'डिजिटल आजीविका रजिस्टर', 'उद्यम रजिस्टर', 'मज़दूरी रिकॉर्ड', 'किसान रिकॉर्ड']


def mutations(case, others, rng):
    """[(fault, broken answer)] that apply to this good case."""
    from agents import facts
    kind, question, answer, ev, register = case
    out = []
    figures = facts.numbers_in(answer)
    if figures:
        x = figures[0]
        for _ in range(20):
            y = round(x * (1 + rng.choice([-1, 1]) * rng.uniform(0.03, 0.20)) / 10) * 10
            if not any(abs(y - k) <= max(1, 0.005 * k) for k in ev.known):
                break
        out.append(('figure_off', answer.replace(rupees(x), rupees(y), 1)))
        wrong = [r for r in OTHER_REGISTERS if r != register]
        out.append(('wrong_register', answer.replace(register, rng.choice(wrong), 1)))
    if 'पिछले 12 महीनों में' in answer and kind != 'lakhpati' and figures:  # a ₹0 total has no figure to check
        out.append(('wrong_period', answer.replace('पिछले 12 महीनों में', 'इस महीने', 1)))
    if 'इस महीने' in answer and figures:
        out.append(('wrong_period', answer.replace('इस महीने', 'पिछले 12 महीनों में', 1)))
    for d in ev.decisions:
        mine = d['labels'][d['outcome']][0]
        other = rng.choice([v[0] for k, v in d['labels'].items() if k != d['outcome']])
        out.append(('flip_verdict', answer.replace(f'"{mine}"', f'"{other}"')))
    if ev.obligations and kind == 'refused_loans':
        out.append(('drop_obligation', answer.replace(' आपका कर्ज़ रजिस्टर मैं नहीं देख पाई।', '')))
    out.append(('english', answer + ' Your savings are good and you should keep saving every month for the future.'))
    other = [o for o in others if o[0] != kind and o[4] != register and facts.numbers_in(o[2])]
    if other:
        out.append(('other_question', rng.choice(other)[2]))
    return out


def gate_c_suite(seed=3):
    from verify import answer as gate_c
    rng = random.Random(seed)
    good, broken = [], []
    for didi in [f'D-00{i}' for i in range(1, 9)]:
        cases = good_answers(didi)
        for case in cases:
            found = [f for f in gate_c.check(case[2], case[3]) if not f.ok]
            good.append({'didi': didi, 'kind': case[0], 'flagged': [f'{f.name}: {f.detail}' for f in found]})
            for fault, text in mutations(case, cases, rng):
                caught = [f.name for f in gate_c.check(text, case[3]) if not f.ok]
                broken.append({'didi': didi, 'kind': case[0], 'fault': fault, 'caught': caught, 'answer': text})
    return good, broken


ID_OF = {'agristack': 'farmer_id', 'wages_vbg': 'job_card_no', 'loans': 'loan_id', 'enterprise': 'enterprise_id'}
PURPOSE_OF = {'savings': 'savings_question', 'loans': 'loan_question', 'dar': 'income_question', 'wages_vbg': 'wages_question',
              'agristack': 'farm_question', 'enterprise': 'enterprise_question', 'meetings': 'group_question'}
FIELDS = {'savings': ['month', 'cumulative'], 'loans': ['outstanding'], 'dar': ['month', 'income'], 'wages_vbg': ['wages_paid'],
          'agristack': ['land_acres'], 'enterprise': ['monthly_turnover'], 'meetings': ['held']}


def gate_a_suite(seed=5):
    from verify import plan as gate_a
    rng = random.Random(seed)
    cases = json.loads((ROOT / 'eval/dspy/planner_dataset.json').read_text(encoding='utf-8'))['cases']
    good, broken = [], []
    for case in cases:
        tables = case['expected']['tables']
        if not tables or case['expected']['decision'] or case['expected']['business_plan']:
            continue
        text = gate_a.intent_text(case['question'], [tuple(h) for h in case['history']])
        # A running balance is read with its month (gate A's needs_month rule): the dataset's labels list a savings
        # total as just `cumulative`, which is how the live run summed twelve balances into ₹1,30,800.
        items = [{'source_id': 'src', 'table': t, 'fields': (['month'] if t == 'savings' and 'cumulative' in spec['need']
                                                              and 'month' not in spec['need'] else []) + spec['need']}
                 for t, spec in tables.items()]
        purpose = PURPOSE_OF[next(iter(tables))]
        kept, findings = gate_a.check(text, items, purpose)
        good.append({'id': case['id'], 'flagged': [f'{f.name}: {f.detail}' for f in findings if not f.ok],
                     'dropped': len(kept) < len(items)})
        asked = gate_a.asked_about(text) | gate_a.PURPOSE_TABLES.get(purpose, set())
        unrelated = [t for t in FIELDS if t not in asked and t not in tables]
        extra = rng.choice(unrelated)
        bad_items = items + [{'source_id': 'src', 'table': extra, 'fields': FIELDS[extra]}]
        kept, findings = gate_a.check(text, bad_items, purpose)
        broken.append({'id': case['id'], 'fault': 'extra_table', 'caught': [f.name for f in findings if not f.ok],
                       'repaired': all(i['table'] != extra for i in kept)})
        swapped = [{'source_id': 'src', 'table': extra, 'fields': FIELDS[extra]}]
        _, findings = gate_a.check(text, swapped, purpose)
        broken.append({'id': case['id'], 'fault': 'swap_table', 'caught': [f.name for f in findings if not f.ok]})
        with_id = [t for t in tables if t in ID_OF]
        if with_id:
            t = with_id[0]
            bad_items = [{**i, 'fields': i['fields'] + [ID_OF[t]]} if i['table'] == t else i for i in items]
            kept, findings = gate_a.check(text, bad_items, purpose)
            broken.append({'id': case['id'], 'fault': 'id_field', 'caught': [f.name for f in findings if not f.ok],
                           'repaired': all(ID_OF[t] not in i['fields'] for i in kept)})
    return good, broken


def summarise(good, broken):
    rows = defaultdict(lambda: [0, 0])
    for b in broken:
        rows[b['fault']][0] += bool(b['caught'])
        rows[b['fault']][1] += 1
    false_alarms = sum(bool(g['flagged']) for g in good)
    return {'good': len(good), 'false_alarms': false_alarms, 'false_alarm_rate': round(false_alarms / max(1, len(good)), 3),
            'recall': {k: {'caught': c, 'of': n, 'rate': round(c / n, 3)} for k, (c, n) in sorted(rows.items())}}


LLM_FAULTS = ('other_question_read', 'dodge', 'half_answer')


def records(ev_rows):
    """What a run hands the step-5 verifier: each register's exact totals, as RunContext.summaries holds them."""
    from agents import facts
    from consent import catalog
    return [{'register': catalog.REGISTERS[t][1], **{k: v for k, v in facts.summarise(t, rows).items() if k != 'rows'}}
            for t, rows in ev_rows.items()]


def llm_cases(didi):
    """(good, faults) for the step-5 verifier: faults code cannot see because every figure is real and read."""
    rows, user = her_rows(didi)
    name = user['name_local'].split()[0]
    good, faults = [], []
    by_kind = {c[0]: c for c in good_answers(didi)}
    read_both = {t: rows[t] for t in ('savings', 'loans', 'dar') if rows[t]}
    for kind, case in by_kind.items():
        if kind in ('refused_loans', 'loans_none', 'lakhpati'):
            continue
        table = {'savings': 'savings', 'loans': 'loans', 'income_year': 'dar', 'income_month': 'dar', 'wages': 'wages_vbg',
                 'enterprise': 'enterprise'}[kind]
        read = {**read_both, table: rows[table]}
        good.append({'kind': kind, 'question': case[1], 'answer': case[2], 'records': records(read)})
        other = next((c for k, c in by_kind.items() if k in ('savings', 'loans', 'income_year') and k != kind
                      and k not in ('income_month',) and c[4] != case[4]), None)
        if other:
            faults.append({'fault': 'other_question_read', 'kind': kind, 'question': case[1], 'answer': other[2],
                           'records': records(read)})
        faults.append({'fault': 'dodge', 'kind': kind, 'question': case[1], 'records': records(read),
                       'answer': f'{name} जी, यह जानकारी आपके {case[4]} में दर्ज है। आप ऐसे ही मेहनत करती रहिए, आपका भविष्य अच्छा होगा।'})
    if 'savings' in by_kind and 'loans' in by_kind:
        both = f'{by_kind["savings"][2]} {by_kind["loans"][2].split(" जी, ", 1)[1]}'
        good.append({'kind': 'savings_and_loans', 'question': 'मेरी बचत और कर्ज़ दोनों बताओ', 'answer': both,
                     'records': records({'savings': rows['savings'], 'loans': rows['loans']})})
        faults.append({'fault': 'half_answer', 'kind': 'savings_and_loans', 'question': 'मेरी बचत और कर्ज़ दोनों बताओ',
                       'answer': by_kind['savings'][2], 'records': records({'savings': rows['savings'], 'loans': rows['loans']})})
    return good, faults


def llm_suite(model, threads=3):
    """Recall and false alarms of the step-5 verifier with `model` (any model nvidia.chat serves, Bhashini included),
    and how many of its faults the code checks alone catch."""
    from concurrent.futures import ThreadPoolExecutor

    from llm import nvidia
    from verify import llm_check

    def call(messages):
        return nvidia.chat(model, messages, temperature=0, max_tokens=200)['message']['content'] or ''

    good, faults = [], []
    for didi in [f'D-00{i}' for i in range(1, 9)]:
        g, f = llm_cases(didi)
        good += g
        faults += f

    def one(case):
        finding, verdict = llm_check.judge(call, case['question'], None, case['records'], [], [], case['answer'])
        return {**{k: v for k, v in case.items() if k != 'records'}, 'flagged': not finding.ok, 'detail': finding.detail,
                'verdict': verdict}

    with ThreadPoolExecutor(threads) as pool:
        judged_good = list(pool.map(one, good))
        judged_faults = list(pool.map(one, faults))
    recall = {}
    for fault in LLM_FAULTS:
        hits = [c for c in judged_faults if c['fault'] == fault]
        recall[fault] = {'caught': sum(c['flagged'] for c in hits), 'of': len(hits),
                         'rate': round(sum(c['flagged'] for c in hits) / max(1, len(hits)), 3)}
    false = [c for c in judged_good if c['flagged']]
    return {'model': model, 'good': len(judged_good), 'false_alarms': len(false),
            'false_alarm_rate': round(len(false) / max(1, len(judged_good)), 3), 'recall': recall,
            'false_alarm_cases': false, 'missed': [c for c in judged_faults if not c['flagged']]}


def run():
    c_good, c_broken = gate_c_suite()
    a_good, a_broken = gate_a_suite()
    report = {'gate_c': summarise(c_good, c_broken), 'gate_a': summarise(a_good, a_broken), 'targets': TARGETS,
              'gate_c_false_alarms': [g for g in c_good if g['flagged']], 'gate_a_false_alarms': [g for g in a_good if g['flagged']],
              'gate_c_missed': [b for b in c_broken if not b['caught']], 'gate_a_missed': [b for b in a_broken if not b['caught']]}
    return report


def main():
    if '--llm' in sys.argv:
        model = sys.argv[sys.argv.index('--llm') + 1]
        report = llm_suite(model)
        out = ROOT / f'eval/verify_llm_{model.split("/")[-1]}.json'
        out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding='utf-8')
        print(f'{model}: {report["good"]} good answers, false alarms {report["false_alarms"]} ({report["false_alarm_rate"]:.1%})')
        for fault, x in report['recall'].items():
            print(f'  {fault:20} caught {x["caught"]:3}/{x["of"]:<3} {x["rate"]:.0%}')
        print(f'wrote {out.relative_to(ROOT)}')
        return
    report = run()
    (ROOT / 'eval/verify_faults_report.json').write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding='utf-8')
    for gate in ('gate_a', 'gate_c'):
        r = report[gate]
        print(f'\n{gate}: {r["good"]} good cases, false alarms {r["false_alarms"]} ({r["false_alarm_rate"]:.1%})')
        for fault, x in r['recall'].items():
            target = '' if fault in REPORTED_ONLY else (' ok' if x['rate'] >= TARGETS['recall'] else ' BELOW TARGET')
            print(f'  {fault:16} caught {x["caught"]:3}/{x["of"]:<3} {x["rate"]:.0%}{target}')
    print('\nwrote eval/verify_faults_report.json')


if __name__ == '__main__':
    main()
