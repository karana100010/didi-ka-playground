"""DSPy prompt optimisation for the Plan-and-Execute planner (a prototype; the app never imports DSPy).

The planner's job has an exact answer that code can compute: which tables (and which fields) a question needs,
whether a decision table or a business plan answers it, and whether she must first be asked a follow-up. So the
metric is computed in code, with no LLM judge, and GEPA reads its feedback text to rewrite the instructions.

Steps (the first runs in the app's venv because it renders the real planner prompt; the rest in .dspy-venv):

    .venv/bin/python scripts/dspy_planner.py contexts          # eval/dspy/contexts.json: the prompt per didi
    .dspy-venv/bin/python scripts/dspy_planner.py dataset      # eval/dspy/planner_dataset.json (70/30 split)
    .dspy-venv/bin/python scripts/dspy_planner.py eval --model nvidia/nemotron-3.5-lightning-30b-a3b
    .dspy-venv/bin/python scripts/dspy_planner.py optimize --model nvidia/nemotron-3.5-lightning-30b-a3b
    .dspy-venv/bin/python scripts/dspy_planner.py eval --model bhashini/google/gemma-4-26B-A4B-it \
        --program eval/dspy/program-nemotron-3.5-lightning-30b-a3b.json

Any model can be `claude-cli/<model>`, e.g. Opus 5.5 as GEPA's teacher (the reflection LM), through the Claude Code
CLI on this machine (`claude -p`, no tools, no session saved), billed to whatever the CLI is logged in with:

    claude auth login                                          # once, in your own terminal
    .dspy-venv/bin/python scripts/dspy_planner.py optimize --reflection claude-cli/claude-opus-5-5 --name opus-teacher

`eval` scores the held-out set with the production prompt, or with an optimised program, and appends a row to
eval/dspy/results.json. The prompt is rendered exactly as plan_execute.run sends it (system = instructions +
her registers, then the conversation, then her question), so the baseline is the production planner.
"""
import argparse
import json
import random
import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / 'eval' / 'dspy'
CONTEXTS, DATASET, RESULTS = OUT / 'contexts.json', OUT / 'planner_dataset.json', OUT / 'results.json'
NVIDIA_BASE = 'https://integrate.api.nvidia.com/v1'
BHASHINI_URL = 'https://dhruva-api.bhashini.gov.in/services/inference/llm'
REGISTERS_HEADER = 'Her registers (source id, table, fields):\n'
DIDIS = [f'D-00{i}' for i in range(1, 9)]


def env():
    values = {}
    for line in (ROOT / '.env').read_text(encoding='utf-8').splitlines():
        key, _, value = line.partition('=')
        if key and not key.startswith('#'):
            values[key.strip()] = value.strip().strip('"\'')
    return values


# --- 1. contexts: the real planner prompt for each mock didi (app venv) ---------------------------------------

def dump_contexts():
    """Load the sample registers in a throwaway store, log in as each didi and capture the planner's system
    prompt and her sources, so the DSPy side needs nothing from the app."""
    import os
    import tempfile
    sys.path[:0] = [str(ROOT / 'server'), str(ROOT / 'tests')]
    os.environ['PLAYGROUND_ACCESS_CODE'] = ''
    import settings
    settings.LOCAL, settings.ACCESS_CODE, settings.BLOB_TOKEN, settings.DATABASE_URL = Path(tempfile.mkdtemp()), '', '', ''
    import tracing
    tracing.enabled = lambda: False
    import decisions
    import tools
    from agents import plan_execute
    from consent import catalog
    from llm import nvidia
    from plans import templates
    seen = {}
    schema_text = tools.schema_text

    def capture(ctx):
        seen['ctx'], seen['registers'] = ctx, schema_text(ctx)
        return seen['registers']

    def no_records(model, messages, **_):
        return {'message': {'role': 'assistant', 'content': '{"follow_up": "?", "needs_records": false, "items": []}'},
                'usage': {}, 'ms': 1, 'model': model}

    tools.schema_text, nvidia.chat = capture, no_records
    import main
    from conftest import login
    from fastapi.testclient import TestClient
    from test_m4 import run_turn
    from wallet import service as wallet_service
    instructions = plan_execute.PLANNER % (', '.join(tools.DECISIONS), ', '.join(catalog.PURPOSES),
                                          ', '.join(catalog.SCHEME_INFO), ', '.join(wallet_service.KINDS),
                                          ', '.join(wallet_service.VERIFIERS), '')
    assert instructions.endswith(REGISTERS_HEADER)
    out = {'instructions': instructions[:-len(REGISTERS_HEADER)].rstrip('\n'), 'key_columns': list(catalog.KEY_COLUMNS),
           'decisions': {d: decisions.describe(d)['needs'] for d in tools.DECISIONS},
           'plans': {k: templates.needs(k) for k in tools.PLAN_TYPES}, 'didis': {}}
    with TestClient(main.app) as client:
        for didi in DIDIS:
            run_turn(client, login(client, didi, agent='plan_execute'), 'नमस्ते')
            ctx = seen['ctx']
            readable = {}
            for source in ctx.sources.values():
                for table, columns in source['tables'].items():
                    if f'  - table {table}:' in seen['registers'] and not tools.is_profile(source, table):
                        readable.setdefault(source['id'], {})[table] = list(columns)
            profile = {s['id']: [t for t in s['tables'] if tools.is_profile(s, t)] for s in ctx.sources.values()}
            enterprise = sorted({str(r.get('type')) for r in _rows(ctx, 'enterprise')})
            out['didis'][didi] = {'name': ctx.didi.get('name'), 'role': ctx.didi.get('role'), 'language': ctx.didi.get('language'),
                                  'enterprise': enterprise, 'registers': seen['registers'], 'tables': readable, 'profile': profile}
    OUT.mkdir(parents=True, exist_ok=True)
    CONTEXTS.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding='utf-8')
    print(f'wrote {CONTEXTS.relative_to(ROOT)}: {len(out["didis"])} didis')


def _rows(ctx, table):
    """Her rows in a table, read straight from the mock workbook (the dataset only needs her enterprise type)."""
    import openpyxl
    sheet = openpyxl.load_workbook(ROOT / 'data' / 'mock' / 'lokos_mock.xlsx', read_only=True)[table]
    rows = list(sheet.iter_rows(values_only=True))
    return [dict(zip(rows[0], r)) for r in rows[1:] if dict(zip(rows[0], r)).get('didi_id') == ctx.didi['didi_id']]


# --- 2. dataset: Hindi questions with the plan computed by code ----------------------------------------------

def need(table, required, allowed=()):
    """A table the answer needs: `required` fields must be asked for; `allowed` are fine too; anything else is
    asking more than the answer needs."""
    return {table: {'need': list(required), 'ok': sorted(set(required) | set(allowed))}}


SAV = ('month', 'amount', 'cumulative')
LOAN = ('loan_id', 'source', 'amount', 'purpose', 'outstanding', 'emi', 'overdue_days')
DAR = ('month', 'cycle_no', 'activity', 'income', 'expense')
WAGE = ('month', 'days_worked', 'wages_paid', 'wages_pending')
ENT = ('type', 'units', 'unit_label', 'monthly_turnover', 'monthly_cost')
LAND = ('land_acres', 'irrigated', 'crop_kharif', 'crop_rabi')

# category -> [(question, expected)]; expected keys: tables, decision, business_plan, plan_changes, follow_up, history
BANK = {
    'savings': [('मेरी कुल बचत कितनी है?', need('savings', ['cumulative'], SAV)),
                ('मैंने अब तक समूह में कितना पैसा जमा किया है?', need('savings', ['cumulative'], SAV)),
                ('पिछले महीने मैंने कितनी बचत जमा की?', need('savings', ['month', 'amount'], SAV)),
                ('meri bachat kitni hai', need('savings', ['cumulative'], SAV)),
                ('हर महीने मैं कितना बचत करती हूँ?', need('savings', ['month', 'amount'], SAV))],
    'loans': [('मेरा कितना कर्ज़ बाकी है?', need('loans', ['outstanding'], LOAN)),
              ('मेरी लोन की किस्त कितनी है?', need('loans', ['emi'], LOAN)),
              ('मेरे लोन पर ब्याज कितना लग रहा है?', need('loans', ['rate_pct'], LOAN + ('rate_pct',))),
              ('mera loan kitna baaki hai', need('loans', ['outstanding'], LOAN)),
              ('क्या मेरी कोई किस्त चूकी हुई है?', need('loans', ['overdue_days'], LOAN))],
    'income': [('पिछले साल मेरी आमदनी कितनी रही?', need('dar', ['income'], DAR)),
               ('मेरे धंधे में महीने का मुनाफ़ा कितना है?', need('dar', ['income', 'expense'], DAR)),
               ('इस महीने मेरी कमाई कितनी हुई?', need('dar', ['month', 'income'], DAR)),
               ('meri kamai kitni hai', need('dar', ['income'], DAR))],
    'land': [('मेरे पास कितनी ज़मीन है?', need('agristack', ['land_acres'], LAND)),
             ('मेरे खेत में सिंचाई है या नहीं?', need('agristack', ['irrigated'], LAND)),
             ('मेरे खेत में कौन सी फसल लगी है?', need('agristack', ['crop_kharif', 'crop_rabi'], LAND)),
             ('mere paas kitni zameen hai', need('agristack', ['land_acres'], LAND))],
    'wages': [('पिछले साल मुझे मज़दूरी कितनी मिली?', need('wages_vbg', ['wages_paid'], WAGE)),
              ('मैंने इस साल कितने दिन काम किया?', need('wages_vbg', ['days_worked'], WAGE)),
              ('मेरी कितनी मज़दूरी अभी बाकी है?', need('wages_vbg', ['wages_pending'], WAGE)),
              ('राम जी योजना में मेरा कितना पैसा आया?', need('wages_vbg', ['wages_paid'], WAGE))],
    'enterprise': [('मेरे धंधे का महीने का टर्नओवर कितना है?', need('enterprise', ['monthly_turnover'], ENT)),
                   ('मेरे धंधे में हर महीने कितना खर्च होता है?', need('enterprise', ['monthly_cost'], ENT)),
                   ('मेरे धंधे में कितनी गाय या मशीनें हैं?', need('enterprise', ['units'], ENT)),
                   ('मेरा कौन सा धंधा रजिस्टर में दर्ज है?', need('enterprise', ['type'], ENT))],
    'lakhpati': [('क्या मैं लखपति दीदी बन गई हूँ?', {'decision': 'lakhpati_status'}),
                 ('क्या मैं लखपति दीदी हूँ?', {'decision': 'lakhpati_status'}),
                 ('kya main lakhpati didi hoon', {'decision': 'lakhpati_status'}),
                 ('लखपति दीदी बनने में मुझे और कितना समय लगेगा?', {'decision': 'lakhpati_status'})],
    'bank_ready': [('क्या हमारा समूह बैंक लोन के लिए तैयार है?', {'decision': 'bank_linkage_readiness'}),
                   ('क्या हमारे समूह को बैंक से कर्ज़ मिल सकता है?', {'decision': 'bank_linkage_readiness'}),
                   ('बैंक लिंकेज के लिए हमारा समूह कितना तैयार है?', {'decision': 'bank_linkage_readiness'})],
    'business_plan': [('मेरी डेयरी बढ़ाने का बिज़नेस प्लान बना दो', {'business_plan': 'dairy'}),
                      ('दो गाय और लेकर डेयरी बढ़ानी है, प्लान बना दो', {'business_plan': 'dairy', 'plan_changes': {'cows_to_add': 2}}),
                      ('सिलाई का धंधा शुरू करने का प्लान बनाओ', {'business_plan': 'tailoring'}),
                      ('एक सिलाई मशीन और खरीदनी है, उसका प्लान बना दो', {'business_plan': 'tailoring', 'plan_changes': {'machines_to_add': 1}}),
                      ('किराना दुकान का बिज़नेस प्लान चाहिए', {'business_plan': 'kirana'}),
                      ('50 हज़ार का नया माल लाना है, किराना का प्लान बना दो', {'business_plan': 'kirana', 'plan_changes': {'stock_investment': 50000}})],
    'general': [('पंचसूत्र क्या होता है?', {}), ('लखपति दीदी किसे कहते हैं?', {}), ('बैंक लिंकेज क्या होता है?', {}),
                ('सीआईएफ क्या है?', {}), ('वीबी-जी राम जी योजना क्या है?', {}), ('डिजिटल आजीविका रजिस्टर क्या होता है?', {}),
                ('समूह की बैठक हर हफ़्ते क्यों ज़रूरी है?', {})],
    'vague': [('मुझे पैसे चाहिए', {'follow_up': True}), ('मुझे लोन चाहिए', {'follow_up': True}),
              ('मेरी मदद करो', {'follow_up': True}), ('पैसों की बहुत दिक्कत है', {'follow_up': True}),
              ('mujhe paise chahiye', {'follow_up': True})],
    'combo': [('मेरी बचत और कर्ज़ दोनों बताओ', {**need('savings', ['cumulative'], SAV), **need('loans', ['outstanding'], LOAN)}),
              ('पिछले साल मेरी आमदनी और मज़दूरी कितनी रही?', {**need('dar', ['income'], DAR), **need('wages_vbg', ['wages_paid'], WAGE)}),
              ('मेरी ज़मीन कितनी है और उसमें कौन सी फसल है?', need('agristack', ['land_acres', 'crop_kharif', 'crop_rabi'], LAND))],
    'follow_up_answer': [
        ('गाय खरीदनी है, डेयरी का प्लान बना दो', {'business_plan': 'dairy', 'history': [['मुझे पैसे चाहिए', 'आपको पैसे किस काम के लिए चाहिए, और कितने?']]}),
        ('बचत के बारे में', {**need('savings', ['cumulative'], SAV), 'history': [['मेरी मदद करो', 'बताइए, आप किस बारे में जानना चाहती हैं: बचत, कर्ज़ या आमदनी?']]}),
        ('कर्ज़ के बारे में, कितना बाकी है', {**need('loans', ['outstanding'], LOAN), 'history': [['मेरी मदद करो', 'बताइए, आप किस बारे में जानना चाहती हैं: बचत, कर्ज़ या आमदनी?']]}),
    ],
}
MY_PLAN = 'मेरे धंधे का बिज़नेस प्लान बना दो'


def build_dataset(seed=7):
    contexts = json.loads(CONTEXTS.read_text(encoding='utf-8'))
    rng = random.Random(seed)
    cases = []
    for didi in DIDIS:
        info = contexts['didis'][didi]
        bank = {k: list(v) for k, v in BANK.items()}
        if any(t in ('dairy', 'kirana', 'tailoring') for t in info['enterprise']):  # her own enterprise has a template
            bank['business_plan'].append((MY_PLAN, {'business_plan': next(t for t in info['enterprise'] if t in ('dairy', 'kirana', 'tailoring'))}))
        extra = set(rng.sample(sorted(bank), 6))
        for category in sorted(bank):
            for question, expected in rng.sample(bank[category], 2 if category in extra else 1):
                cases.append({'id': f'{didi}-{category}-{len(cases)}', 'didi': didi, 'category': category, 'question': question,
                              'history': expected.get('history', []), 'expected': expected_plan(expected)})
    by_category = defaultdict(list)
    for case in cases:
        by_category[case['category']].append(case)
    for group in by_category.values():  # 30% held out, stratified by question type
        rng.shuffle(group)
        for i, case in enumerate(group):
            case['split'] = 'test' if i < round(len(group) * 0.3) else 'train'
    data = {'description': 'Planner questions for the eight mock didis; the expected plan is computed by code from the '
                           'question type and her registers (her enterprise type for "my business plan").',
            'seed': seed, 'cases': cases}
    DATASET.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding='utf-8')
    print(f'wrote {DATASET.relative_to(ROOT)}: {len(cases)} cases, {Counter(c["split"] for c in cases)}, '
          f'{dict(Counter(c["category"] for c in cases))}')


def expected_plan(spec):
    tables = {k: v for k, v in spec.items() if isinstance(v, dict) and 'need' in v}
    return {'tables': tables, 'decision': spec.get('decision'), 'business_plan': spec.get('business_plan'),
            'plan_changes': spec.get('plan_changes', {}), 'follow_up': bool(spec.get('follow_up'))}


# --- 3. the metric: the plan as plan_execute._check would leave it, scored in code -----------------------------

def check(contexts, didi, text):
    """plan_execute._parse/_check and tools._items on plain JSON. Returns (plan, problem)."""
    try:
        plan = json.loads(text[text.index('{'):text.rindex('}') + 1])
    except ValueError:
        return None, 'Your last reply was not valid JSON. Reply with the JSON plan only.'
    if not isinstance(plan, dict):
        return None, 'Your last reply was not a JSON object. Reply with the JSON plan only.'
    info = contexts['didis'][didi]
    items = []
    if plan.get('needs_records') and plan.get('items') and plan.get('business_plan') not in contexts['plans']:
        items, problem = check_items(contexts, info, plan['items'])
        if problem:
            return None, f'Your last plan had a problem: {problem} Fix it and reply with the JSON plan only.'
    plan['items'] = items or []
    plan['follow_up'] = str(plan.get('follow_up') or '').strip()[:300] or None
    plan['decision'] = plan.get('decision') if plan.get('decision') in contexts['decisions'] else None
    plan['business_plan'] = plan.get('business_plan') if plan.get('business_plan') in contexts['plans'] else None
    plan['plan_changes'] = plan.get('plan_changes') if isinstance(plan.get('plan_changes'), dict) else {}
    if plan['business_plan']:
        plan['items'] = [{'table': n['table'], 'fields': n['fields']} for n in contexts['plans'][plan['business_plan']]]
    if plan['decision'] and not plan['business_plan']:
        plan['items'] = [{'table': n['table'], 'fields': n['fields']} for n in contexts['decisions'][plan['decision']]]
    return plan, ''


def check_items(contexts, info, raw):
    if not isinstance(raw, list) or not raw:
        return None, 'items must list at least one {source, table, fields}.'
    items = []
    for item in raw:
        item = item if isinstance(item, dict) else {}
        source = str(item.get('source', ''))
        if source not in info['tables'] and source not in info['profile']:
            return None, f'Unknown source {item.get("source")!r}. Use a source id from the list in your instructions.'
        table = item.get('table')
        if table in info['profile'].get(source, []):
            continue
        columns = info['tables'].get(source, {}).get(table)
        if columns is None:
            return None, f'{source} has no table {table!r}.'
        unknown = [f for f in item.get('fields') or [] if f not in columns]
        if unknown:
            return None, f'Table {table} has no field {", ".join(map(repr, unknown))}.'
        fields = [f for f in item.get('fields') or [] if f not in contexts['key_columns']]
        if fields:
            items.append({'table': table, 'fields': list(dict.fromkeys(fields))})
    if not items:
        return None, 'No readable fields were listed (key columns like didi_id are applied automatically).'
    return items, None


def calls(contexts, plan):
    """What plan_execute.execute would do with the plan: decide, build a plan, fetch the other tables."""
    out = set()
    decided = set()
    if plan.get('business_plan'):
        out.add(('build_business_plan', plan['business_plan']))
        decided |= {n['table'] for n in contexts['plans'][plan['business_plan']]}
    if plan.get('decision'):
        out.add(('decide', plan['decision']))
        decided = {n['table'] for n in contexts['decisions'][plan['decision']]}
    out |= {('fetch', i['table']) for i in plan['items'] if i['table'] not in decided}
    return out


def score(contexts, case, text, problem=''):
    """(score in [0, 1], feedback). 0 for a plan the app would reject, or a follow-up where none was due (or none
    where one was). Otherwise tool-call F1 on fetch/decide/build, times a field factor: required fields asked for,
    and no fields beyond what the answer needs (data minimisation)."""
    exp = case['expected']
    plan, problem = check(contexts, case['didi'], text) if text is not None else (None, problem or 'no reply')
    if not plan:
        return 0.0, f'The app rejected this plan: {problem}'
    asks = bool(plan['follow_up'] and not plan['items'] and not plan['decision'] and not plan['business_plan'])
    if exp['follow_up'] != asks:
        return 0.0, ('The request is too vague to plan: set "follow_up" to one short, kind question in her language, '
                     'needs_records false and items [].' if exp['follow_up'] else
                     'Wrongly asked a follow-up question: the request was clear enough to plan (or she was answering an '
                     'earlier follow-up). Plan from it instead.')
    if asks:
        return 1.0, 'Correct: the request is vague, so a short follow-up question is asked first.'
    want = {('fetch', t) for t in exp['tables']} | ({('decide', exp['decision'])} if exp['decision'] else set()) | \
           ({('build_business_plan', exp['business_plan'])} if exp['business_plan'] else set())
    got = calls(contexts, plan)
    notes = []
    if not want:
        if got:
            return 0.0, (f'A general question needs none of her registers, but the plan reads {_names(got)}. Set needs_records '
                         'false and items [] and answer from scheme_topics: reading her records here is a privacy failure.')
        return 1.0, 'Correct: a general question, answered without reading her registers.'
    hit = want & got
    f1 = 2 * len(hit) / (len(want) + len(got)) if got else 0.0
    if want - got:
        notes.append(f'Missing: {_names(want - got)}.')
    if got - want:
        notes.append(f'Not needed (privacy failure): {_names(got - want)}.')
    factors = []
    for item in plan['items']:
        spec = exp['tables'].get(item['table'])
        if not spec or ('fetch', item['table']) not in hit:
            continue
        extra = [f for f in item['fields'] if f not in spec['ok']]
        missing = [f for f in spec['need'] if f not in item['fields']]
        recall = 1 - len(missing) / len(spec['need'])
        factors.append(recall * (1 - len(extra) / len(item['fields'])))
        if extra:
            notes.append(f'{item["table"]}: fields {", ".join(extra)} are more than this question needs; ask only for '
                         f'{", ".join(spec["need"])} (and at most {", ".join(f for f in spec["ok"] if f not in spec["need"])}).')
        if missing:
            notes.append(f'{item["table"]}: the answer needs {", ".join(missing)}, which the plan did not ask for.')
    field = sum(factors) / len(factors) if factors else 1.0
    value = f1 * (0.7 + 0.3 * field)
    if exp['business_plan'] and plan['business_plan'] == exp['business_plan']:
        said, put = _numbers(exp['plan_changes']), _numbers(plan['plan_changes'])
        if said != put:
            value *= 0.8
            notes.append(f'plan_changes should be {json.dumps(exp["plan_changes"])}: only numbers she said herself, '
                         f'nothing else (got {json.dumps(plan["plan_changes"], ensure_ascii=False)}).')
    return round(value, 4), ' '.join(notes) or 'Correct plan: the right tables and only the fields the answer needs.'


def _names(found):
    return ', '.join(f'{kind} {what}' for kind, what in sorted(found))


def _numbers(changes):
    out = {}
    for k, v in (changes or {}).items():
        try:
            out[k] = float(v)
        except (TypeError, ValueError):
            out[k] = v
    return out


# --- 4. DSPy: the planner module, the exact prompt format, the LMs -------------------------------------------

def dspy_parts(contexts):
    import dspy

    class Plan(dspy.Signature):
        question: str = dspy.InputField()
        registers: str = dspy.InputField()
        history: list = dspy.InputField()
        plan_json: str = dspy.OutputField()

    Plan = Plan.with_instructions(contexts['instructions'])

    class PlannerAdapter(dspy.ChatAdapter):
        """Renders the planner call as plan_execute.run does; any other signature (GEPA's reflection) is left to
        the ordinary ChatAdapter."""

        def __init__(self):
            super().__init__(use_json_adapter_fallback=False)

        def format(self, signature, demos, inputs):
            if 'plan_json' not in signature.output_fields:
                return super().format(signature, demos, inputs)
            messages = [{'role': 'system', 'content': f'{signature.instructions}\n{REGISTERS_HEADER}{inputs["registers"]}'}]
            for demo in demos:
                if demo.get('question') and demo.get('plan_json'):
                    messages += [{'role': 'user', 'content': demo['question']}, {'role': 'assistant', 'content': demo['plan_json']}]
            for q, a in inputs.get('history') or []:
                messages += [{'role': 'user', 'content': q}, {'role': 'assistant', 'content': a}]
            return messages + [{'role': 'user', 'content': inputs['question']}]

        def parse(self, signature, completion):
            if 'plan_json' not in signature.output_fields:
                return super().parse(signature, completion)
            return {'plan_json': completion or ''}

    class Planner(dspy.Module):
        """One planner call, and one retry carrying the problem, as plan_execute.run does."""

        def __init__(self):
            super().__init__()
            self.plan = dspy.Predict(Plan)

        def forward(self, question, registers, history, didi):
            problem = ''
            for _ in range(2):
                text = self.plan(question=question + problem, registers=registers, history=history).plan_json
                plan, why = check(contexts, didi, text)
                if plan:
                    break
                problem = f'\n\n({why})'
            return dspy.Prediction(plan_json=text)

    return dspy, Planner, PlannerAdapter


def make_lm(model, max_tokens=600):
    import dspy
    if model.startswith('claude-cli/'):
        return ClaudeCliLM(model, max_tokens=max_tokens)
    keys = env()
    if model.startswith('bhashini/'):
        return BhashiniLM(model, keys['BHASHINI_LLM_KEY'], max_tokens=max_tokens)
    extra = {'chat_template_kwargs': {'enable_thinking': False}} if 'nemotron' in model else {}
    return patient_lm()(f'openai/{model}', api_base=NVIDIA_BASE, api_key=keys['NVIDIA_API_KEY'], temperature=0,
                        max_tokens=max_tokens, num_retries=8, extra_body=extra, cache=True)


def patient_lm():
    """dspy.LM that waits and retries when the NVIDIA free tier answers 429 (DSPy 3.4 does not retry itself)."""
    import dspy

    class PatientLM(dspy.LM):
        def __call__(self, *args, **kwargs):
            for wait in (5, 15, 30, 60, 90, 120, 120, 0):
                try:
                    return super().__call__(*args, **kwargs)
                except Exception as error:  # noqa: BLE001
                    if not wait or not any(s in f'{type(error).__name__} {error}' for s in ('RateLimit', '429', 'Timeout', '503', '502')):
                        raise
                    time.sleep(wait)

    return PatientLM


def bhashini_class():
    import dspy
    import requests

    class _BhashiniLM(dspy.BaseLM):
        """A dspy.LM for Bhashini's hosted LLMs (Dhruva inference API), as server/llm/bhashini_llm.py calls them:
        text in, text out, backing off on 429/5xx. Replies are cached in memory by request."""

        def __init__(self, model, key, max_tokens=600):
            super().__init__(model=model, temperature=0, max_tokens=max_tokens, cache=False)
            self.key, self.memo = key, {}

        def forward(self, prompt=None, messages=None, **kwargs):
            turns = [{'role': m['role'] if m['role'] in ('system', 'user', 'assistant') else 'user',
                      'content': m.get('content') if isinstance(m.get('content'), str) else json.dumps(m.get('content'))}
                     for m in messages or [{'role': 'user', 'content': prompt}]]
            temperature = kwargs.get('temperature', self.kwargs.get('temperature', 0))
            body = {'pipelineTasks': [{'taskType': 'llm', 'config': {
                        'serviceId': self.model, 'temperature': temperature, 'topP': 0.9,
                        'maxTokens': kwargs.get('max_tokens', self.kwargs.get('max_tokens', 600))}}],
                    'inputData': {'input': [turns]}}
            memo = json.dumps(body, ensure_ascii=False, sort_keys=True)
            if memo not in self.memo:
                self.memo[memo] = self._post(body)
            text = self.memo[memo]
            return {'choices': [{'message': {'role': 'assistant', 'content': text}, 'finish_reason': 'stop'}],
                    'usage': {'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0}, 'model': self.model}

        def _post(self, body):
            wait = 4
            for _ in range(8):
                try:
                    reply = requests.post(BHASHINI_URL, json=body, headers={'Authorization': self.key}, timeout=90)
                except requests.RequestException:
                    reply = None
                if reply is not None and reply.ok:
                    return reply.json()['pipelineResponse'][0]['output'][0]['target'] or ''
                if reply is not None and reply.status_code not in (429, 500, 502, 503, 504):
                    raise RuntimeError(f'Bhashini said {reply.status_code}: {reply.text[:200]}')
                time.sleep(wait)
                wait = min(wait * 2, 60)
            raise RuntimeError('Bhashini: no answer after 8 tries')

    return _BhashiniLM


def BhashiniLM(model, key, max_tokens=600):  # noqa: N802 - a class built lazily so the module imports without DSPy
    return bhashini_class()(model, key, max_tokens)


def claude_cli_class():
    import os
    import subprocess
    import tempfile

    import dspy

    class _ClaudeCliLM(dspy.BaseLM):
        """A dspy.LM that asks a Claude model through the Claude Code CLI: `claude -p --model <model>` with tools off,
        no session saved, run in an empty folder so no CLAUDE.md, memory or project settings reach the prompt.
        System messages become --system-prompt; the other turns are sent as one transcript on stdin (GEPA's
        reflection call is a single user message). Waits and retries on rate limits and overload."""

        def __init__(self, model, max_tokens=6000, executable='claude'):
            super().__init__(model=model, temperature=1.0, max_tokens=max_tokens, cache=False)
            self.executable, self.memo, self.cost = executable, {}, 0.0
            self.folder = tempfile.mkdtemp(prefix='dspy-claude-cli-')
            # The CLI must use its own login, not the settings of a Claude Code session this script may run inside.
            self.env = {k: v for k, v in os.environ.items()
                        if not k.startswith(('CLAUDE_CODE_', 'CLAUDECODE', 'ANTHROPIC_BASE_URL'))}

        def forward(self, prompt=None, messages=None, **kwargs):
            messages = messages or [{'role': 'user', 'content': prompt}]
            text = lambda m: m['content'] if isinstance(m.get('content'), str) else json.dumps(m.get('content'), ensure_ascii=False)  # noqa: E731
            system = '\n\n'.join(text(m) for m in messages if m['role'] == 'system')
            turns = [m for m in messages if m['role'] != 'system']
            stdin = text(turns[0]) if len(turns) == 1 else '\n\n'.join(f'{m["role"].upper()}:\n{text(m)}' for m in turns)
            key = json.dumps([system, stdin], ensure_ascii=False)
            if key not in self.memo:
                self.memo[key] = self._ask(system, stdin)
            return {'choices': [{'message': {'role': 'assistant', 'content': self.memo[key]}, 'finish_reason': 'stop'}],
                    'usage': {'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0}, 'model': self.model}

        def _ask(self, system, stdin):
            command = [self.executable, '-p', '--model', self.model.removeprefix('claude-cli/'), '--tools', '',
                       '--output-format', 'json', '--no-session-persistence', '--strict-mcp-config']
            if system:
                command += ['--system-prompt', system]
            wait = 10
            for _ in range(6):
                try:
                    done = subprocess.run(command, input=stdin, capture_output=True, text=True, timeout=600,
                                          cwd=self.folder, env=self.env)
                except subprocess.TimeoutExpired:
                    reply = {'is_error': True, 'result': 'timed out after 600 s'}
                else:
                    try:
                        reply = json.loads(done.stdout)
                    except ValueError:
                        reply = {'is_error': True, 'result': (done.stderr or done.stdout)[:300]}
                if not reply.get('is_error'):
                    self.cost += reply.get('total_cost_usd') or 0
                    return reply.get('result') or ''
                problem = str(reply.get('result'))
                if 'not logged in' in problem.lower() or '/login' in problem:
                    raise RuntimeError(f'Claude CLI: {problem}. Run `claude auth login` in your terminal, then retry.')
                if not any(s in problem.lower() for s in ('rate limit', 'overloaded', '429', '529', 'timed out')):
                    raise RuntimeError(f'Claude CLI: {problem}')
                time.sleep(wait)
                wait = min(wait * 2, 120)
            raise RuntimeError(f'Claude CLI: no answer after 6 tries ({problem})')

    return _ClaudeCliLM


def ClaudeCliLM(model, max_tokens=6000, executable=None):  # noqa: N802 - built lazily, like BhashiniLM
    import os
    return claude_cli_class()(model, max_tokens, executable or os.environ.get('CLAUDE_CLI', 'claude'))


def examples(dspy, contexts, split):
    data = json.loads(DATASET.read_text(encoding='utf-8'))['cases']
    return [dspy.Example(question=c['question'], registers=contexts['didis'][c['didi']]['registers'], history=c['history'],
                         didi=c['didi'], case=c).with_inputs('question', 'registers', 'history', 'didi')
            for c in data if c['split'] in split]


def gepa_metric(contexts):
    import dspy

    def metric(gold, pred, trace=None, pred_name=None, pred_trace=None):
        value, feedback = score(contexts, gold.case, getattr(pred, 'plan_json', None))
        if pred_name is None:
            return value
        return dspy.Prediction(score=value, feedback=f'Question type: {gold.case["category"]}. Score {value}. {feedback}')

    return metric


def short(model):
    return model.split('/')[-1]


def run_eval(args):
    contexts = json.loads(CONTEXTS.read_text(encoding='utf-8'))
    dspy, Planner, PlannerAdapter = dspy_parts(contexts)
    dspy.configure(lm=make_lm(args.model), adapter=PlannerAdapter())
    program = Planner()
    if args.program:
        program.load(str(ROOT / args.program))
    rows = examples(dspy, contexts, args.split.split(','))
    if args.limit:
        rows = rows[:args.limit]
    scored = []

    def one(example):
        try:
            text = program(**example.inputs()).plan_json
        except Exception as error:  # noqa: BLE001 - an unreachable model scores 0 for that case, and says why
            return example.case, 0.0, f'error: {error}'[:300], ''
        value, feedback = score(contexts, example.case, text)
        return example.case, value, feedback, text

    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(args.threads) as pool:
        for case, value, feedback, text in pool.map(one, rows):
            scored.append({'id': case['id'], 'category': case['category'], 'score': value, 'feedback': feedback, 'reply': text})
            print(f'{value:.2f}  {case["id"]:32} {feedback[:110]}', flush=True)
    by_cat = defaultdict(list)
    for s in scored:
        by_cat[s['category']].append(s['score'])
    total = sum(s['score'] for s in scored) / len(scored)
    invalid = sum(s['feedback'].startswith(('The app rejected', 'error')) for s in scored)
    row = {'when': time.strftime('%Y-%m-%d %H:%M'), 'model': args.model, 'program': args.program or 'production PLANNER',
           'split': args.split, 'n': len(scored), 'score': round(total, 4), 'invalid': invalid,
           'by_category': {k: round(sum(v) / len(v), 3) for k, v in sorted(by_cat.items())}, 'cases': scored}
    results = json.loads(RESULTS.read_text(encoding='utf-8')) if RESULTS.exists() else []
    RESULTS.write_text(json.dumps(results + [row], ensure_ascii=False, indent=1), encoding='utf-8')
    print(f'\n{args.model} [{row["program"]}] {args.split}: {total:.3f} over {len(scored)} (rejected/errors {invalid})')
    print(json.dumps(row['by_category'], ensure_ascii=False))


def run_optimize(args):
    contexts = json.loads(CONTEXTS.read_text(encoding='utf-8'))
    dspy, Planner, PlannerAdapter = dspy_parts(contexts)
    dspy.configure(lm=make_lm(args.model), adapter=PlannerAdapter())
    train = examples(dspy, contexts, ['train'])
    random.Random(1).shuffle(train)
    cut = len(train) * 2 // 3  # GEPA learns from the first part and picks candidates on the rest
    reflection = make_lm(args.reflection, max_tokens=6000)
    reflection.kwargs['temperature'] = 1.0
    optimizer = dspy.GEPA(metric=gepa_metric(contexts), max_metric_calls=args.budget, reflection_lm=reflection,
                          num_threads=args.threads, reflection_minibatch_size=args.minibatch, track_stats=True,
                          seed=args.seed, log_dir=str(OUT / 'gepa' / (args.name or short(args.model))))
    optimized = optimizer.compile(Planner(), trainset=train[:cut], valset=train[cut:])
    path = OUT / f'program-{args.name or short(args.model)}.json'
    optimized.save(str(path))
    details = optimized.detailed_results
    print(f'saved {path.relative_to(ROOT)}; best val score {max(details.val_aggregate_scores):.3f} '
          f'(baseline {details.val_aggregate_scores[0]:.3f}) over {len(details.candidates)} candidates')
    print(optimized.plan.signature.instructions)
    if getattr(reflection, 'cost', None):
        print(f'teacher ({args.reflection}) cost reported by the CLI: ${reflection.cost:.2f}')


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('contexts')
    sub.add_parser('dataset')
    ev = sub.add_parser('eval')
    ev.add_argument('--model', default='nvidia/nemotron-3.5-lightning-30b-a3b')
    ev.add_argument('--program', help='a saved optimised program (eval/dspy/program-*.json); default: production prompt')
    ev.add_argument('--split', default='test')
    ev.add_argument('--threads', type=int, default=2)
    ev.add_argument('--limit', type=int, default=0)
    op = sub.add_parser('optimize')
    op.add_argument('--model', default='nvidia/nemotron-3.5-lightning-30b-a3b')
    op.add_argument('--reflection', default='nvidia/nemotron-3-ultra-550b-a55b',
                    help='the teacher that rewrites the prompt, e.g. claude-cli/claude-opus-5-5')
    op.add_argument('--budget', type=int, default=400, help='metric calls (each is one planner run)')
    op.add_argument('--minibatch', type=int, default=4)
    op.add_argument('--threads', type=int, default=2)
    op.add_argument('--seed', type=int, default=0)
    op.add_argument('--name', help='saves eval/dspy/program-<name>.json (default: the task model)')
    args = parser.parse_args()
    {'contexts': lambda: dump_contexts(), 'dataset': lambda: build_dataset(),
     'eval': lambda: run_eval(args), 'optimize': lambda: run_optimize(args)}[args.command]()


if __name__ == '__main__':
    main()
