"""M17: admin totals across didis and SHGs. Every register is read whole through the gateway's aggregate path, computed
by code (no LLM), and only figures over groups of at least 3 didis come back. The department's pause and purpose
policy applies (purpose admin_aggregate) and every read is logged with the admin's name and reason."""
import sqlite3
from collections import defaultdict

import openpyxl
import pytest

from conftest import ROOT, WS
from test_m15_admin import admin_client, audit, sign_in  # noqa: F401  (fixture)

REASON = 'quarterly CLF review'


def _sheet(name):
    wb = openpyxl.load_workbook(ROOT / 'data/mock/lokos_mock.xlsx', read_only=True)
    rows = list(wb[name].iter_rows(values_only=True))
    return [dict(zip(rows[0], r)) for r in rows[1:]]


def _sql(query):
    with sqlite3.connect(ROOT / 'data/mock/vbg_agri.sqlite') as db:
        return db.execute(query).fetchall()


def totals(client, headers, reason=REASON):
    return client.post('/v1/admin/aggregates', headers=headers, json={'reason': reason})


def register(body, table):
    return next(r for r in body['registers'] if r['table'] == table)


@pytest.fixture()
def body(admin_client):  # noqa: F811
    r = totals(admin_client, sign_in(admin_client))
    assert r.status_code == 200, r.text
    return r.json()


def test_needs_an_admin_and_a_reason(admin_client):  # noqa: F811
    assert totals(admin_client, WS).status_code == 401
    assert totals(admin_client, sign_in(admin_client), reason='').status_code == 422


def test_overview_and_shg_registry_match_the_mock_registers(body):
    users, shgs, meetings = _sheet('users'), _sheet('shg'), _sheet('meetings')
    assert body['overview'] == {'didis': len(users), 'shgs': len({u['shg_id'] for u in users}),
                                'districts': len({u['district'] for u in users}), 'households': len(users)}
    held = [m for m in meetings if m['held'] == 'yes']
    o = body['shg']['overall']
    assert o['shgs'] == len(shgs) and o['members'] == sum(s['members'] for s in shgs) == 94
    assert o['grades'] == {'A': 4, 'B': 3, 'C': 1}
    assert o['meetings_recorded'] == len(meetings) and o['meetings_held'] == len(held)
    assert o['savings_collected'] == sum(m['savings_collected'] for m in held)
    assert o['loans_given'] == sum(m['loans_given'] for m in held)
    assert o['repayment_pct'] == round(100 * sum(m['repayments_made'] for m in held) / sum(m['repayments_due'] for m in held))
    first = body['shg']['per_shg'][0]
    mine = [m for m in held if m['shg_id'] == 'SHG-101']
    assert first['shg_id'] == 'SHG-101' and first['members'] == 12 and first['grade'] == 'A' and first['place'] == 'Gaya, Bihar'
    assert first['savings_collected'] == sum(m['savings_collected'] for m in mine)
    assert first['attendance_pct'] == round(100 * sum(m['attendance'] / 12 for m in mine) / len(mine))
    assert len(body['shg']['per_shg']) == 8 and body['shg']['hidden_shgs'] == 0


def test_register_totals_match_the_mock_registers(body):
    savings = defaultdict(list)
    for r in _sheet('savings'):
        savings[r['didi_id']].append(r)
    latest = [sorted(rs, key=lambda r: r['month'])[-1]['cumulative'] for rs in savings.values()]
    s = register(body, 'savings')['overall']
    assert s['n'] == 8 and s['values']['Total saved'] == sum(latest) and s['values']['Saved per didi (average)'] == round(sum(latest) / 8)

    loans = _sheet('loans')
    borrowers = {r['didi_id'] for r in loans}
    l = register(body, 'loans')['overall']
    assert l['n'] == len(borrowers)
    assert l['values']['Loans'] == len(loans) and l['values']['Amount borrowed'] == sum(r['amount'] for r in loans)
    assert l['values']['Outstanding'] == sum(r['outstanding'] for r in loans)
    assert l['values']['Didis with an overdue loan'] == len({r['didi_id'] for r in loans if r['overdue_days'] > 0})

    dar = _sheet('dar')
    income = expense = 0
    for didi in {r['didi_id'] for r in dar}:
        mine = [r for r in dar if r['didi_id'] == didi]
        months = sorted({r['month'] for r in mine})[-12:]
        income += sum(r['income'] for r in mine if r['month'] in months)
        expense += sum(r['expense'] for r in mine if r['month'] in months)
    d = register(body, 'dar')['overall']['values']
    assert d['Income'] == income and d['Costs'] == expense and d['Net income'] == income - expense

    e = register(body, 'enterprise')['overall']['values']
    assert e['Monthly turnover'] == sum(r['monthly_turnover'] for r in _sheet('enterprise'))

    (days, paid, pending), = _sql('select sum(days_worked), sum(wages_paid), sum(wages_pending) from wages_vbg')
    w = register(body, 'wages_vbg')
    assert w['unit'] == 'households' and w['overall']['n'] == _sql('select count(distinct household_id) from wages_vbg')[0][0]
    assert (w['overall']['values']['Days worked'], w['overall']['values']['Wages paid'], w['overall']['values']['Wages pending']) == (days, paid, pending)

    (farmers, acres, irrigated), = _sql("select count(*), sum(land_acres), sum(irrigated = 'yes') from agristack")
    a = register(body, 'agristack')['overall']['values']
    assert a['Farmer records'] == farmers and a['Land (acres)'] == round(acres, 2) and a['Households with irrigation'] == irrigated


def test_lakhpati_counts_come_from_the_decision_table(body):
    from decisions import engine
    dar = _sheet('dar')
    expected = defaultdict(int)
    for didi in {r['didi_id'] for r in dar}:
        mine = [r for r in dar if r['didi_id'] == didi]
        expected[engine.evaluate('lakhpati_status', engine.inputs_from_rows('lakhpati_status', {'dar': mine}))['outcome']] += 1
    lakhpati = body['lakhpati']
    assert lakhpati['decision'] == 'lakhpati_status' and lakhpati['overall']['n'] == 8
    labels = engine.table('lakhpati_status')['outcomes']
    assert {o: lakhpati['overall']['values'][labels[o]['en'].capitalize()] for o in labels} == {o: expected[o] for o in labels}
    assert sum(lakhpati['overall']['values'].values()) == 8


def test_groups_under_three_didis_are_hidden_and_cannot_be_subtracted(body):
    savings = register(body, 'savings')['breakdowns']
    # One didi per SHG and per district in the mock data: every such group is hidden, none is named.
    for name in ('shg', 'district'):
        assert savings[name]['rows'] == [] and savings[name]['hidden_groups'] == 8 and savings[name]['others'] is None
    # Grade A has 4 didis, B 3, C 1. C is hidden; B is hidden too, or C would be total minus A minus B.
    grade = savings['grade']
    assert [(r['group'], r['n']) for r in grade['rows']] == [('Grade A', 4)]
    assert grade['hidden_groups'] == 2 and grade['others']['n'] == 4
    # Loans: the grade C didi has none, so A (4) and B (3) both show and nothing is hidden.
    loans = register(body, 'loans')['breakdowns']['grade']
    assert [(r['group'], r['n']) for r in loans['rows']] == [('Grade A', 4), ('Grade B', 3)] and loans['hidden_groups'] == 0
    # No shown group is ever below 3, anywhere.
    sections = [*body['registers'], body['lakhpati']]
    assert all(r['n'] >= 3 for s in sections for b in s['breakdowns'].values() for r in b['rows'])
    assert 'D-00' not in str(body) and 'H-0' not in str(body)  # no didi or household id leaves the server


def test_breakdown_suppression_rules():
    import aggregates
    metrics = [aggregates._metric('x', 'X', 'sum')]
    per = {f'd{i}': {'x': i} for i in range(7)}
    groups = {'d0': 'a', 'd1': 'a', 'd2': 'a', 'd3': 'b', 'd4': 'b', 'd5': 'b', 'd6': 'c'}
    out = aggregates.breakdown(per, groups.get, metrics)
    assert [r['group'] for r in out['rows']] == ['b'] and out['others'] == {'n': 4, 'values': {'X': 0 + 1 + 2 + 6}}
    assert aggregates.overall({'d0': {'x': 1}, 'd1': {'x': 2}}, metrics) == {'n': 2, 'hidden': True}


def test_every_read_is_logged_with_the_admin_and_reason(admin_client):  # noqa: F811
    totals(admin_client, sign_in(admin_client))
    events = audit(admin_client)
    asked = next(e for e in events if e['kind'] == 'admin_aggregate')
    assert asked['detail'] == {'admin': 'admin', 'reason': REASON}
    reads = [e for e in events if e['kind'] == 'fetch']
    assert {e['detail']['table'] for e in reads} == {'users', 'shg', 'meetings', 'savings', 'loans', 'dar', 'enterprise', 'wages_vbg', 'agristack'}
    for e in reads:
        d = e['detail']
        assert d['admin'] == 'admin' and d['reason'] == REASON and d['purpose'] == 'admin_aggregate' and d['aggregate']
        assert e['didi_id'] is None and d['consent_id'] is None and d['scope'] == 'all'
    view = admin_client.get('/v1/department?owner=LokOS (DAY-NRLM)', headers=WS).json()
    row = next(r for r in view['log'] if r['kind'] == 'admin')
    assert row['purpose'] == 'Admin totals (aggregate)' and row['status'] == f'Admin admin, totals only: {REASON}'
    assert row['didi'] == 'All didis (aggregate)' and view['stats']['didis'] == 0
    assert 'admin_aggregate' in [p['id'] for p in view['purposes']]


def test_a_paused_registry_blocks_the_totals_and_is_logged(admin_client):  # noqa: F811
    sources = admin_client.get('/v1/sources', headers=WS).json()['sources']
    primary = next(s for s in sources if s['setup'].get('role') == 'primary')
    admin_client.put(f'/v1/sources/{primary["id"]}/policy', headers=WS, json={'paused': True})
    r = totals(admin_client, sign_in(admin_client))
    assert r.status_code == 403 and r.json()['error']['code'] == 'paused'
    blocked = next(e for e in audit(admin_client) if e['kind'] == 'fetch_blocked')
    assert blocked['detail']['reason_code'] == 'paused' and blocked['detail']['purpose'] == 'admin_aggregate'
    assert blocked['detail']['admin'] == 'admin' and blocked['detail']['reason'] == REASON


def test_purpose_policy_keeps_a_register_out_and_a_tick_lets_it_in(admin_client):  # noqa: F811
    sources = admin_client.get('/v1/sources', headers=WS).json()['sources']
    linked = next(s for s in sources if s['setup'].get('role') == 'linked')
    admin_client.put(f'/v1/sources/{linked["id"]}/policy', headers=WS, json={'purposes': {'wages_vbg': ['wages_question']}})
    headers = sign_in(admin_client)
    body = totals(admin_client, headers).json()
    assert register(body, 'wages_vbg')['blocked']['code'] == 'purpose_not_allowed' and 'overall' not in register(body, 'wages_vbg')
    assert register(body, 'agristack')['overall']['n'] == 7  # other tables of the same source still read
    assert any(e['kind'] == 'fetch_blocked' and e['detail']['table'] == 'wages_vbg' for e in audit(admin_client))

    admin_client.put(f'/v1/sources/{linked["id"]}/policy', headers=WS,
                     json={'purposes': {'wages_vbg': ['wages_question', 'admin_aggregate']}})
    assert register(totals(admin_client, headers).json(), 'wages_vbg')['overall']['n'] == 8


def test_a_paused_linked_source_blocks_only_its_registers(admin_client):  # noqa: F811
    sources = admin_client.get('/v1/sources', headers=WS).json()['sources']
    linked = next(s for s in sources if s['setup'].get('role') == 'linked')
    admin_client.put(f'/v1/sources/{linked["id"]}/policy', headers=WS, json={'paused': True})
    body = totals(admin_client, sign_in(admin_client)).json()
    assert register(body, 'wages_vbg')['blocked']['code'] == 'paused' and register(body, 'agristack')['blocked']['code'] == 'paused'
    assert register(body, 'savings')['overall']['n'] == 8 and body['shg']['overall']['shgs'] == 8


def test_no_llm_is_called(admin_client, fake_llm):  # noqa: F811
    assert totals(admin_client, sign_in(admin_client)).status_code == 200
    assert fake_llm.calls == []
