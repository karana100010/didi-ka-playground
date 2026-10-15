"""M5: decision tables, derivations from the mock registers, and the decide tool inside the agents."""
import json

import pytest

from conftest import ROOT, WS, login, stream, tool_call
from test_m4 import lokos_id, run_turn


def rows(table, didi=None, shg=None):
    import sys
    sys.path.insert(0, str(ROOT / 'server'))
    from connectors.excel import ExcelConnector
    conn = ExcelConnector(lambda: (ROOT / 'data/mock/lokos_mock.xlsx').read_bytes(), 'test-mock')
    where = {'didi_id': didi} if didi else {'shg_id': shg}
    return conn.rows(table, where=where)


@pytest.mark.parametrize('didi,outcome,rule', [('D-001', 'ON_TRACK', 2), ('D-002', 'LAKHPATI', 1), ('D-003', 'NOT_YET', 4),
                                               ('D-004', 'APPROACHING', 3), ('D-005', 'APPROACHING', 3), ('D-006', 'NOT_YET', 4),
                                               ('D-007', 'APPROACHING', 3), ('D-008', 'APPROACHING', 3)])
def test_lakhpati_status_for_every_didi(didi, outcome, rule):
    import decisions
    inputs = decisions.inputs_from_rows('lakhpati_status', {'dar': rows('dar', didi=didi)})
    result = decisions.evaluate('lakhpati_status', inputs)
    assert (result['outcome'], result['matched_rules']) == (outcome, [rule])
    if didi == 'D-001':
        assert inputs == {'annual_income': 124800, 'avg_monthly_income': 10400, 'cycles_sustained': 2}
        assert result['reasons'][0]['hi'] == 'आमदनी साल में ₹1 लाख से ऊपर है, पर ज़रूरी 4 में से अभी 2 चक्र ही पूरे हुए हैं।'
    if didi == 'D-008':  # ₹1 lakh a year but not above ₹10,000 a month: the official rule does not apply
        assert inputs['annual_income'] >= 100000 and inputs['avg_monthly_income'] <= 10000


@pytest.mark.parametrize('shg,outcome,findings', [
    ('SHG-101', 'READY', []), ('SHG-103', 'NEEDS_WORK', ['REGULAR_MEETINGS', 'REGULAR_SAVINGS', 'TIMELY_REPAYMENT']),
    ('SHG-105', 'NEEDS_WORK', ['REGULAR_SAVINGS']), ('SHG-106', 'NEEDS_WORK', ['INTERNAL_LENDING']),
    ('SHG-107', 'NEEDS_WORK', ['BOOKS_OF_ACCOUNT'])])
def test_panchsutra_for_each_group(shg, outcome, findings):
    import decisions
    result = decisions.evaluate('bank_linkage_readiness',
                                decisions.inputs_from_rows('bank_linkage_readiness', {'meetings': rows('meetings', shg=shg)}))
    assert result['outcome'] == outcome and result['findings'] == findings


def test_conditions_and_formatting():
    from decisions.engine import indian, matches
    assert matches('-', None) and matches('>= 100000', 100000) and not matches('> 10000', 10000)
    assert matches('[60000..100000)', 60000) and not matches('[60000..100000)', 100000) and matches('(1..2]', 2)
    assert matches('yes', 'Yes') and matches('a, b', 'b') and not matches('no', 'yes')
    assert indian(124800) == '1,24,800' and indian(1000000) == '10,00,000' and indian(999) == '999'


def test_decision_endpoints(client):
    tables = client.get('/v1/decisions', headers=WS).json()['decisions']
    assert [t['id'] for t in tables] == ['bank_linkage_readiness', 'lakhpati_status']
    out = client.post('/v1/decisions/lakhpati_status/evaluate', headers=WS,
                      json={'inputs': {'annual_income': 150000, 'avg_monthly_income': 12500, 'cycles_sustained': 4}}).json()
    assert out['outcome'] == 'LAKHPATI' and out['basis'] == {'rule_1': 'official'}
    assert client.get('/v1/decisions/nope', headers=WS).status_code == 404


def test_plan_and_execute_runs_the_lakhpati_table(client, fake_llm):
    session = login(client, 'D-001')
    plan = {'needs_records': True, 'decision': 'lakhpati_status', 'purpose': 'income_question', 'items': [],
            'scheme_topics': ['lakhpati_didi'], 'steps': ['Run the Lakhpati table']}

    fake_llm.script += [json.dumps(plan)]   # the answer is worded by code from the table's result
    events, end = run_turn(client, session, 'क्या मैं लखपति दीदी बन गई हूँ?')
    card = next(e['data'] for e in events if e['type'] == 'consent_request')
    assert card['purpose']['id'] == 'lakhpati_status_check'                               # the table's purpose
    assert card['items'][0]['fields'] == ['month', 'income', 'expense', 'cycle_no']     # exactly the table's fields
    answer = end['runs'][0]['result']['answer']
    assert answer == ('सुनीता जी, आपके डिजिटल आजीविका रजिस्टर के अनुसार पिछले 12 महीनों में आपके परिवार की आमदनी ₹1,24,800 रही, '
                      'यानी हर महीने औसतन ₹10,400। आमदनी साल में ₹1 लाख से ऊपर है, पर ज़रूरी 4 में से अभी 2 चक्र ही पूरे हुए हैं। '
                      'नियम के अनुसार आप "लखपति बनने की राह पर" हैं।')
    step = next(e['data'] for e in events if e['type'] == 'decision')
    assert step['step']['hi'] == 'लखपति दीदी की स्थिति का नियम लगाया (नियम 2)'
    metrics = end['runs'][0]['result']['metrics']
    assert metrics['decision'] == 'lakhpati_status: ON_TRACK' and metrics['grounded_numbers'] == '2/2'
    assert metrics['llm_calls'] == 1 and metrics['fields_read'] == ['dar.cycle_no', 'dar.expense', 'dar.income', 'dar.month']


def test_native_agent_uses_describe_and_decide(client, fake_llm):
    session = login(client, 'D-002', agent='native_tools', critic=False)
    seen = {}
    fake_llm.script += [
        tool_call('describe_decision', decision_id='lakhpati_status'),
        lambda m, t: tool_call('request_consent', purpose=json.loads(m[-1]['content'])['purpose'],
                               items=json.loads(m[-1]['content'])['consent_items']),
        lambda m, t: tool_call('decide', decision_id='lakhpati_status', consent_id=json.loads(m[-1]['content'])['consent_id']),
        lambda m, t: seen.update(result=json.loads(m[-1]['content'])) or 'रेखा जी, आप लखपति दीदी हैं।',
    ]
    events, end = run_turn(client, session, 'क्या मैं लखपति दीदी हूँ?')
    assert seen['result']['outcome'] == 'LAKHPATI' and seen['result']['basis'] == {'rule_1': 'official'}
    assert end['runs'][0]['result']['metrics']['decision'] == 'lakhpati_status: LAKHPATI'


def test_bank_readiness_needs_an_office_bearer(client, fake_llm):
    session = login(client, 'D-005', agent='native_tools', critic=False)   # Santosh is a member
    seen = {}
    fake_llm.script += [
        tool_call('describe_decision', decision_id='bank_linkage_readiness'),
        lambda m, t: tool_call('request_consent', purpose='bank_linkage_readiness', items=json.loads(m[-1]['content'])['consent_items']),
        lambda m, t: tool_call('decide', decision_id='bank_linkage_readiness', consent_id=json.loads(m[-1]['content'])['consent_id']),
        lambda m, t: seen.update(result=json.loads(m[-1]['content'])) or 'ठीक है।',
    ]
    run_turn(client, session, 'क्या हमारा समूह बैंक लोन के लिए तैयार है?')
    assert 'group data' in seen['result']['error']
