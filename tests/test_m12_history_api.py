"""M12: her conversation history API. GET lists her finished questions newest first, across all her logins (Redis
when it is there, else the core store); DELETE clears them and is audited. Other didis never see them."""
import json

import fakeredis
import pytest

from conftest import WS, login
from test_m4 import run_turn


@pytest.fixture(params=['redis', 'store'])
def backend(request, client):
    import memory
    if request.param == 'redis':
        memory.use_client(fakeredis.FakeRedis(decode_responses=True))
    yield request.param
    memory.use_client(None)


def ask(client, fake_llm, session, question, answer):
    fake_llm.script += [json.dumps({'needs_records': False, 'items': [], 'scheme_topics': ['panchsutra'], 'steps': []}),
                        answer]
    _, end = run_turn(client, session, question)
    assert end['status'] == 'done'
    return end


def history(client, session, **params):
    return client.get(f'/v1/sessions/{session["id"]}/history', headers=WS, params=params)


def test_history_lists_her_questions_newest_first_across_logins(client, fake_llm, backend):
    first = login(client, 'D-001')
    ask(client, fake_llm, first, 'पंचसूत्र क्या है?', 'पंचसूत्र पाँच आदतें हैं।')
    ask(client, fake_llm, first, 'नियमित बैठक क्यों?', 'बैठक से समूह मज़बूत रहता है।')

    body = history(client, first).json()
    assert body['backend'] == backend
    turns = body['turns']
    assert [t['question'] for t in turns] == ['नियमित बैठक क्यों?', 'पंचसूत्र क्या है?']
    assert turns[0]['answer'] == 'बैठक से समूह मज़बूत रहता है।'
    for t in turns:
        assert t['turn_id'] and t['at'] and isinstance(t['agent'], str) and t['agent']
    assert [t['question'] for t in history(client, first, limit=1).json()['turns']] == ['नियमित बैठक क्यों?']

    second = login(client, 'D-001', samples=False)
    assert second['id'] != first['id']
    assert [t['question'] for t in history(client, second).json()['turns']] == ['नियमित बैठक क्यों?', 'पंचसूत्र क्या है?']

    other = login(client, 'D-002', samples=False)
    assert history(client, other).json()['turns'] == []


def test_delete_clears_her_history_and_is_audited(client, fake_llm, backend):
    session = login(client, 'D-001')
    ask(client, fake_llm, session, 'पंचसूत्र क्या है?', 'पंचसूत्र पाँच आदतें हैं।')
    ask(client, fake_llm, session, 'नियमित बैठक क्यों?', 'बैठक से समूह मज़बूत रहता है।')
    other = login(client, 'D-002', samples=False)
    ask(client, fake_llm, other, 'बचत क्यों?', 'बचत से सुरक्षा मिलती है।')

    response = client.delete(f'/v1/sessions/{session["id"]}/history', headers=WS)
    assert response.status_code == 200 and response.json() == {'cleared': 2}
    assert history(client, session).json()['turns'] == []
    assert [t['question'] for t in history(client, other).json()['turns']] == ['बचत क्यों?']

    from core import store
    events = [e for e in store.recent_audit(WS['X-Workspace'], kinds=['history_cleared'])]
    assert len(events) == 1 and events[0]['didi_id'] == 'D-001' and events[0]['detail'] == {'cleared': 2}
    assert events[0]['source_id'] == session['source_id']


def test_unknown_session_is_404(client, backend):
    assert client.get('/v1/sessions/nope/history', headers=WS).status_code == 404
    assert client.delete('/v1/sessions/nope/history', headers=WS).status_code == 404
