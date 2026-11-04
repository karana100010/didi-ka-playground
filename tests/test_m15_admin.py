"""M15: admin sign-in. An admin views any didi's chat (her sessions and answers) and draws charts from her registers.
Admin reads never use her consents, so they must be explicit and recorded: a reason to open her view, every read
logged with the admin's name and reason, the department's policy still applies, and only her own rows are read."""
import json

import pytest

from conftest import WS, login
from test_m12_history_api import ask

PASSWORD = 'test-admin-pass'


@pytest.fixture()
def admin_client(client, monkeypatch):
    import settings
    monkeypatch.setattr(settings, 'ADMIN_USER', 'admin')
    monkeypatch.setattr(settings, 'ADMIN_PASSWORD', PASSWORD)
    client.post('/v1/sources/sample', headers=WS)
    return client


def sign_in(client):
    r = client.post('/v1/admin/login', headers=WS, json={'user': 'admin', 'password': PASSWORD})
    assert r.status_code == 200, r.text
    return {**WS, 'X-Admin-Token': r.json()['token']}


def chart(client, headers, kind, didi='D-001', **extra):
    return client.post(f'/v1/admin/didis/{didi}/charts', headers=headers, json={'kind': kind, 'reason': 'monthly review', **extra})


def audit(client):
    return client.get('/v1/audit', headers=WS).json()['events']


def test_wrong_password_is_refused_and_logged(admin_client):
    r = admin_client.post('/v1/admin/login', headers=WS, json={'user': 'admin', 'password': 'nope'})
    assert r.status_code == 401 and r.json()['error']['code'] == 'admin_denied'
    assert any(e['kind'] == 'admin_login_failed' for e in audit(admin_client))


def test_admin_routes_need_a_valid_token(admin_client):
    assert admin_client.get('/v1/admin/me', headers=WS).status_code == 401
    assert admin_client.get('/v1/admin/me', headers={**WS, 'X-Admin-Token': 'forged.token'}).status_code == 401
    headers = sign_in(admin_client)
    assert admin_client.get('/v1/admin/me', headers=headers).json() == {'user': 'admin'}
    assert chart(admin_client, WS, 'savings').status_code == 401


def test_token_ends_when_the_password_changes(admin_client, monkeypatch):
    headers = sign_in(admin_client)
    import settings
    monkeypatch.setattr(settings, 'ADMIN_PASSWORD', 'a-new-password')
    assert admin_client.get('/v1/admin/me', headers=headers).status_code == 401


def test_local_seed_password_is_generated_once(client, monkeypatch, tmp_path):
    import admin
    import settings
    monkeypatch.setattr(settings, 'ADMIN_PASSWORD', '')
    user, first = admin.credential()
    assert user == 'admin' and len(first) >= 12 and (tmp_path / 'admin.json').exists()
    assert admin.credential()[1] == first


def test_hosted_without_a_password_turns_admin_off(client, monkeypatch):
    import settings
    monkeypatch.setattr(settings, 'ADMIN_PASSWORD', '')
    monkeypatch.setattr(settings, 'HOSTED', True)
    r = client.post('/v1/admin/login', headers=WS, json={'user': 'admin', 'password': 'x'})
    assert r.status_code == 503 and r.json()['error']['code'] == 'admin_off'


def test_view_shows_her_sessions_and_answers_with_the_reason_recorded(admin_client, fake_llm):
    session = login(admin_client, 'D-001', samples=False)
    ask(admin_client, fake_llm, session, 'पंचसूत्र क्या है?', 'पंचसूत्र पाँच आदतें हैं।')
    login(admin_client, 'D-002', samples=False)
    headers = sign_in(admin_client)

    assert admin_client.post('/v1/admin/didis/D-001/view', headers=headers, json={'reason': ''}).status_code == 422
    body = admin_client.post('/v1/admin/didis/D-001/view', headers=headers, json={'reason': 'field visit follow-up'}).json()
    assert body['didi']['didi_id'] == 'D-001' and 'keys' not in body['didi']
    assert [s['id'] for s in body['sessions']] == [session['id']]
    assert [(t['question'], t['answer']) for t in body['turns']] == [('पंचसूत्र क्या है?', 'पंचसूत्र पाँच आदतें हैं।')]
    viewed = next(e for e in audit(admin_client) if e['kind'] == 'admin_view')
    assert viewed['didi_id'] == 'D-001' and viewed['detail'] == {'admin': 'admin', 'reason': 'field visit follow-up'}

    other = admin_client.post('/v1/admin/didis/D-002/view', headers=headers, json={'reason': 'field visit'}).json()
    assert other['turns'] == []
    assert admin_client.post('/v1/admin/didis/D-999/view', headers=headers, json={'reason': 'x' * 5}).status_code == 404


def test_savings_chart_is_her_register_and_the_read_is_logged(admin_client):
    headers = sign_in(admin_client)
    d = chart(admin_client, headers, 'savings').json()
    assert d['spec']['x'][0] == '2025-10' and d['spec']['series'][0]['values'][:2] == [9800, 10000]
    assert len(d['spec']['x']) == 12
    read = next(e for e in audit(admin_client) if e['kind'] == 'fetch')
    assert read['didi_id'] == 'D-001'
    assert read['detail']['admin'] == 'admin' and read['detail']['reason'] == 'monthly review'
    assert read['detail']['purpose'] == 'admin_review' and read['detail']['consent_id'] is None
    assert read['detail']['scope'] == 'didi_id'


def test_every_basic_chart_has_the_chart_card_shape(admin_client):
    headers = sign_in(admin_client)
    for kind in ('savings', 'loans', 'income', 'wages', 'projection', 'earnings'):
        r = chart(admin_client, headers, kind)
        assert r.status_code == 200, (kind, r.text)
        d = r.json()
        assert d['title'] and d['spec']['series'], kind
        assert all(len(s['values']) == len(d['spec']['x']) for s in d['spec']['series']), kind


def test_loans_chart_pays_down_to_zero(admin_client):
    headers = sign_in(admin_client)
    d = chart(admin_client, headers, 'loans').json()
    names = [s['name'] for s in d['spec']['series']]
    assert names == ['L-1001 · SHG internal', 'L-1002 · Bank linkage']
    assert [s['values'][0] for s in d['spec']['series']] == [2626, 26367]
    assert all(s['values'][-1] == 0 for s in d['spec']['series'])


def test_projection_adds_the_extra_deposit(admin_client):
    headers = sign_in(admin_client)
    d = chart(admin_client, headers, 'projection', months=6, extra=100).json()
    f = d['figures']
    assert f['months'] == 6 and f['new_deposit'] == f['current_deposit'] + 100 and f['added'] == 600


def test_department_pause_blocks_admin_reads_and_logs_it(admin_client):
    sources = admin_client.get('/v1/sources', headers=WS).json()['sources']
    primary = next(s for s in sources if s['setup'].get('role') == 'primary')
    admin_client.put(f'/v1/sources/{primary["id"]}/policy', headers=WS, json={'paused': True})
    headers = sign_in(admin_client)
    r = chart(admin_client, headers, 'savings')
    assert r.status_code == 403 and r.json()['error']['code'] == 'paused'
    blocked = next(e for e in audit(admin_client) if e['kind'] == 'fetch_blocked')
    assert blocked['detail']['admin'] == 'admin' and blocked['detail']['reason_code'] == 'paused'


def test_department_view_names_the_admin_and_reason(admin_client):
    headers = sign_in(admin_client)
    chart(admin_client, headers, 'savings')
    view = admin_client.get('/v1/department', headers=WS).json()
    row = next(r for r in view['log'] if r['kind'] == 'admin')
    assert row['purpose'] == 'Admin review' and row['status'] == 'Admin admin: monthly review' and row['didi_id'] == 'D-001'


def test_past_answer_charts_come_back_in_the_view(admin_client, fake_llm, monkeypatch):
    session = login(admin_client, 'D-001', samples=False)
    end = ask(admin_client, fake_llm, session, 'पंचसूत्र क्या है?', 'पंचसूत्र पाँच आदतें हैं।')
    from core import store
    turn_id = store.session_turns(session['id'])[0]['id']
    run_id = store.turn_runs(turn_id)[0]['id']
    store.add_event(turn_id, run_id, 999, 'chart', {'title': 't', 'spec': {'x': ['2026-01'], 'series': []}, 'position': 0})
    headers = sign_in(admin_client)
    body = admin_client.post('/v1/admin/didis/D-001/view', headers=headers, json={'reason': 'review'}).json()
    assert [c['title'] for c in body['turns'][0]['charts']] == ['t']
    assert end['status'] == 'done' and json.dumps(body, ensure_ascii=False)
