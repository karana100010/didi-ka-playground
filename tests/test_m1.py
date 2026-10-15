"""M1 end-to-end tests: sources, setup, login, read-only SQL, access code, Bhashini route mounting.

    .venv/bin/python -m pytest tests -q
    DATABASE_URL=postgresql://localhost/didi_playground_test .venv/bin/python -m pytest tests -q   # core store on Postgres

Uses the bundled mock files and a throwaway local store. No network calls except the optional Bhashini test.
"""
import io
import os
import sqlite3
from pathlib import Path

import pytest
from openpyxl import Workbook

from conftest import ROOT, WS


def xlsx(sheets):
    wb = Workbook()
    wb.remove(wb.active)
    for title, rows in sheets.items():
        ws = wb.create_sheet(title)
        for row in rows:
            ws.append(row)
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def test_workspace_required(client):
    assert client.get('/v1/sources').status_code == 400
    assert client.get('/v1/features').status_code == 200


def test_access_code(client, monkeypatch):
    import settings
    monkeypatch.setattr(settings, 'ACCESS_CODE', 's3cret')
    assert client.get('/v1/features').json()['access_required'] is True
    assert client.get('/v1/sources', headers=WS).status_code == 401
    assert client.post('/api/bhashini/translate', json={'text': 'hi'}).status_code == 401
    assert client.get('/v1/sources', headers={**WS, 'X-Access-Code': 's3cret'}).status_code == 200


def test_samples_login_flow(client):
    added = client.post('/v1/sources/sample', headers=WS).json()['sources']
    assert [s['setup']['role'] for s in added] == ['primary', 'linked']
    assert set(added[0]['tables']) == {'users', 'shg', 'meetings', 'savings', 'loans', 'dar', 'enterprise'}
    assert set(added[1]['tables']) == {'wages_vbg', 'agristack'}
    assert 'location' not in added[0]

    didis = client.get('/v1/didis', headers=WS).json()['didis']
    assert len(didis) == 8
    sunita = didis[0]
    assert sunita['didi_id'] == 'D-001' and sunita['name_local'] == 'सुनीता देवी'
    assert sunita['shg_name'] == 'Maa Durga Jeevika SHG' and sunita['role'] == 'treasurer'
    assert 'phone_last4' not in sunita  # only the login preview fields leave the server

    session = client.post('/v1/sessions', headers=WS, json={'didi_id': 'D-003'}).json()
    assert session['didi']['asr_language'] == 'bho'
    assert client.get(f'/v1/sessions/{session["id"]}', headers=WS).json()['didi']['name'] == 'Meena Yadav'
    other = {'X-Workspace': 'someone-else-0002'}
    assert client.get(f'/v1/sessions/{session["id"]}', headers=other).status_code == 404
    assert [s['name'] for s in client.get('/v1/sources', headers=other).json()['sources']] == ['lokos_mock.xlsx', 'vbg_agri.sqlite']

    kinds = [e['kind'] for e in client.get('/v1/audit', headers=WS).json()['events']]
    assert 'login' in kinds and 'users_listed' in kinds

    # Loading samples again replaces them instead of duplicating.
    client.post('/v1/sources/sample', headers=WS)
    assert len(client.get('/v1/sources', headers=WS).json()['sources']) == 2


def test_source_preview_has_samples(client):
    source = client.post('/v1/sources/sample', headers=WS).json()['sources'][0]
    full = client.get(f'/v1/sources/{source["id"]}', headers=WS).json()
    dar = full['tables']['dar']
    assert dar['columns'][:6] == ['didi_id', 'month', 'cycle_no', 'activity', 'income', 'expense']
    assert len(dar['sample']) == 5 and dar['sample'][0]['month'] == '2024-10'
    assert full['tables']['shg']['sample'][0]['formed_on'] == '2016-03-12'  # Excel dates come back as dates


def test_upload_excel_and_setup(client, tmp_path):
    data = xlsx({'Members': [['member_id', 'name', 'household_id'], ['M1', 'Asha', 'H1'], ['M2', 'Babita', 'H2']],
                 'Savings': [['member_id', 'month', 'amount'], ['M1', '2026-09', 100]]})
    source = client.post('/v1/sources/upload', headers=WS, files={'file': ('my register.xlsx', data)}).json()
    assert source['name'] == 'my_register.xlsx' and source['setup'] == {
        'role': 'primary', 'users_table': 'Members', 'key_column': 'member_id', 'owner_dept': '', 'confirmed': False}
    assert client.get('/v1/didis', headers=WS).status_code == 409  # not confirmed yet (no samples: a source exists)

    bad = client.put(f'/v1/sources/{source["id"]}/setup', headers=WS,
                     json={'role': 'primary', 'users_table': 'Members', 'key_column': 'nope'})
    assert bad.status_code == 422
    ok = client.put(f'/v1/sources/{source["id"]}/setup', headers=WS,
                    json={'role': 'primary', 'users_table': 'Members', 'key_column': 'member_id', 'owner_dept': 'SRLM'})
    assert ok.json()['setup']['confirmed'] is True
    didis = client.get('/v1/didis', headers=WS).json()['didis']
    assert [d['didi_id'] for d in didis] == ['M1', 'M2'] and didis[0]['name'] == 'Asha'

    # A SQLite upload links to those members by household_id.
    db_path = tmp_path / 'linked.sqlite'
    db = sqlite3.connect(db_path)
    db.execute('CREATE TABLE wages (household_id TEXT, month TEXT, days INTEGER)')
    db.execute("INSERT INTO wages VALUES ('H1', '2026-09', 6)")
    db.commit()
    db.close()
    linked = client.post('/v1/sources/upload', headers=WS, files={'file': ('wages.db', db_path.read_bytes())}).json()
    assert linked['setup']['role'] == 'linked' and linked['setup']['join_key'] == 'household_id'
    assert linked['tables']['wages']['sample'] == [{'household_id': 'H1', 'month': '2026-09', 'days': 6}]
    no = client.put(f'/v1/sources/{linked["id"]}/setup', headers=WS, json={'role': 'linked', 'join_key': 'member_id'})
    assert no.status_code == 422 and 'No table' in no.json()['error']['message']
    yes = client.put(f'/v1/sources/{linked["id"]}/setup', headers=WS,
                     json={'role': 'linked', 'join_key': 'household_id', 'owner_dept': 'VB–G RAM G'})
    assert yes.json()['setup']['owner_dept'] == 'VB–G RAM G'

    assert client.delete(f'/v1/sources/{linked["id"]}', headers=WS).json() == {'deleted': True}
    assert len(client.get('/v1/sources', headers=WS).json()['sources']) == 1


def test_upload_rejects_wrong_files(client):
    r = client.post('/v1/sources/upload', headers=WS, files={'file': ('notes.txt', b'hello')})
    assert r.status_code == 422 and r.json()['error']['code'] == 'bad_type'
    r = client.post('/v1/sources/upload', headers=WS, files={'file': ('fake.xlsx', b'not a zip')})
    assert r.json()['error']['code'] == 'not_excel'
    r = client.post('/v1/sources/upload', headers=WS, files={'file': ('fake.db', b'not sqlite')})
    assert r.json()['error']['code'] == 'not_sqlite'
    import settings
    r = client.post('/v1/sources/upload', headers=WS, files={'file': ('big.xlsx', b'PK' + b'0' * settings.MAX_UPLOAD)})
    assert r.status_code == 413


def test_sql_url_rules(client):
    for url in ('sqlite:////etc/passwd', 'mysql://u:p@h/db', 'not a url', 'postgresql://host-only'):
        r = client.post('/v1/sources/link', headers=WS, json={'sql_url': url})
        assert r.status_code == 422, url
        assert r.json()['error']['code'] == 'bad_url'


def test_gsheet_ids():
    from connectors import ConnectorError, gsheet
    sid = '1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789'
    assert gsheet.sheet_id(f'https://docs.google.com/spreadsheets/d/{sid}/edit#gid=0') == sid
    assert gsheet.sheet_id(sid) == sid
    with pytest.raises(ConnectorError):
        gsheet.sheet_id('https://example.com/spreadsheets/x')


def test_sqlite_connector_is_read_only_and_parameterised():
    from connectors import ConnectorError
    from connectors.sql import SqlConnector
    conn = SqlConnector(sqlite_path=ROOT / 'data' / 'mock' / 'vbg_agri.sqlite')
    rows = conn.rows('wages_vbg', ['month', 'days_worked'], where={'household_id': 'H-0421'})
    assert len(rows) == 12 and set(rows[0]) == {'month', 'days_worked'}
    assert conn.rows('wages_vbg', where={'household_id': "H-0421' OR '1'='1"}) == []
    with pytest.raises(ConnectorError):
        conn.rows('wages_vbg; DROP TABLE agristack')
    with pytest.raises(ConnectorError):
        conn.rows('wages_vbg', ['days_worked; --'])
    with pytest.raises(sqlite3.OperationalError):
        sqlite3.connect(f'file:{ROOT / "data/mock/vbg_agri.sqlite"}?mode=ro', uri=True).execute('DELETE FROM agristack')


def test_pages_and_bhashini_routes_mounted(client):
    for path in ('/', '/connect', '/login', '/chat', '/api.js', '/bhashini.js', '/app.css'):
        assert client.get(path).status_code == 200, path
    paths = set(client.get('/v1/openapi.json').json()['paths'])
    assert {'/api/bhashini/asr', '/api/bhashini/tts', '/api/bhashini/translate', '/api/models'} <= paths
    assert client.get('/api/models').json()['models']['asr']


@pytest.mark.skipif(not os.environ.get('BHASHINI_LIVE'), reason='set BHASHINI_LIVE=1 to call Bhashini')
def test_bhashini_translate_live(client):
    r = client.post('/api/bhashini/translate', json={'text': 'Hello', 'source': 'en', 'target': 'hi'})
    assert r.status_code == 200 and r.json()['text']


def test_new_playground_starts_with_samples(client):
    fresh = {'X-Workspace': 'brand-new-0003'}
    didis = client.get('/v1/didis', headers=fresh).json()['didis']
    assert len(didis) == 8 and didis[0]['name'] == 'Sunita Devi'
