"""M12: session memory. Her earlier questions survive a new login: in Redis when REDIS_URL is set, otherwise (or when
Redis is down) from the core store. Only conversation text is kept."""
import json

import fakeredis
import pytest
import redis

from conftest import WS, login
from test_m4 import run_turn

WORKSPACE = WS['X-Workspace']
PANCHSUTRA = json.dumps({'needs_records': False, 'items': [], 'scheme_topics': ['panchsutra'], 'steps': []})


@pytest.fixture()
def fake_redis(client):
    import memory
    r = fakeredis.FakeRedis(decode_responses=True)
    memory.use_client(r)
    yield r
    memory.use_client(None)


class DeadRedis:
    def __getattr__(self, name):
        def fail(*args, **kwargs):
            raise redis.ConnectionError('down')
        return fail


def ask(client, fake_llm, session, question, answer):
    fake_llm.script += [PANCHSUTRA, answer]
    events, end = run_turn(client, session, question)
    assert end['status'] == 'done'
    return end


def sent_texts(fake_llm):
    return [m.get('content') or '' for call in fake_llm.calls for m in call['messages']]


def test_remember_recall_forget_round_trip(fake_redis):
    import memory
    session = {'id': 'ses_1', 'didi_id': 'D-001'}
    memory.remember(WORKSPACE, session, 'turn_1', 'पहला सवाल', 'पहला जवाब', 'plan_execute')
    memory.remember(WORKSPACE, session, 'turn_2', 'दूसरा सवाल', 'दूसरा जवाब', 'react')
    assert memory.backend() == 'redis'
    entries = memory.recall(WORKSPACE, 'D-001')
    assert [e['turn_id'] for e in entries] == ['turn_1', 'turn_2']  # oldest first
    assert set(entries[0]) == {'turn_id', 'session_id', 'question', 'answer', 'agent', 'at'}
    assert entries[1]['agent'] == 'react' and entries[0]['session_id'] == 'ses_1'
    assert [e['turn_id'] for e in memory.recall(WORKSPACE, 'D-001', limit=1)] == ['turn_2']

    key = f'didi:memory:{WORKSPACE}:D-001'
    assert 29 * 86400 < fake_redis.ttl(key) <= 30 * 86400
    assert memory.recall(WORKSPACE, 'D-002') == [] and memory.recall('other-workspace', 'D-001') == []

    assert memory.forget(WORKSPACE, 'D-001') == 2
    assert not fake_redis.exists(key) and memory.recall(WORKSPACE, 'D-001') == []


def test_empty_answers_are_not_remembered_and_the_list_is_capped(fake_redis):
    import memory
    session = {'id': 'ses_1', 'didi_id': 'D-001'}
    memory.remember(WORKSPACE, session, 'turn_0', 'सवाल', '', 'react')
    assert memory.recall(WORKSPACE, 'D-001') == []
    for i in range(memory.CAP + 5):
        memory.remember(WORKSPACE, session, f'turn_{i}', 'q', 'a', 'react')
    assert fake_redis.llen(f'didi:memory:{WORKSPACE}:D-001') == memory.CAP


def test_history_survives_a_new_login_with_redis(client, fake_llm, fake_redis):
    first = login(client, 'D-001')
    ask(client, fake_llm, first, 'पंचसूत्र क्या है?', 'पंचसूत्र पाँच आदतें हैं।')
    raw = fake_redis.lrange(f'didi:memory:{WORKSPACE}:D-001', 0, -1)
    assert len(raw) == 1
    entry = json.loads(raw[0])
    assert set(entry) == {'turn_id', 'session_id', 'question', 'answer', 'agent', 'at'}  # text only, no register rows
    assert entry['session_id'] == first['id'] and entry['agent'] == 'plan_execute'

    second = login(client, 'D-001', samples=False)
    assert second['id'] != first['id']
    fake_llm.calls.clear()
    ask(client, fake_llm, second, 'और बताइए', 'ठीक है।')
    texts = sent_texts(fake_llm)
    assert 'पंचसूत्र क्या है?' in texts and 'पंचसूत्र पाँच आदतें हैं।' in texts
    assert len(fake_redis.lrange(f'didi:memory:{WORKSPACE}:D-001', 0, -1)) == 2

    other = login(client, 'D-002', samples=False)
    fake_llm.calls.clear()
    ask(client, fake_llm, other, 'पंचसूत्र?', 'पाँच आदतें।')
    assert 'पंचसूत्र क्या है?' not in sent_texts(fake_llm)  # another didi never sees her memory


def test_store_fallback_without_redis(client, fake_llm):
    import memory
    assert memory.backend() == 'store'
    first = login(client, 'D-001')
    ask(client, fake_llm, first, 'पंचसूत्र क्या है?', 'पंचसूत्र पाँच आदतें हैं।')
    entries = memory.recall(WORKSPACE, 'D-001')
    assert len(entries) == 1 and entries[0]['turn_id'].startswith('turn_')
    assert entries[0]['question'] == 'पंचसूत्र क्या है?' and entries[0]['agent'] == 'plan_execute'
    assert entries[0]['session_id'] == first['id']

    second = login(client, 'D-001', samples=False)
    fake_llm.calls.clear()
    ask(client, fake_llm, second, 'और बताइए', 'ठीक है।')
    assert 'पंचसूत्र क्या है?' in sent_texts(fake_llm)
    assert [e['question'] for e in memory.recall(WORKSPACE, 'D-001')] == ['पंचसूत्र क्या है?', 'और बताइए']

    assert memory.forget(WORKSPACE, 'D-001') == 2
    assert memory.recall(WORKSPACE, 'D-001') == []


def test_store_fallback_when_redis_is_unreachable(client, fake_llm):
    import memory
    memory.use_client(DeadRedis())
    try:
        assert memory.backend() == 'store'
        first = login(client, 'D-001')
        ask(client, fake_llm, first, 'पंचसूत्र क्या है?', 'पंचसूत्र पाँच आदतें हैं।')  # remember fails quietly
        assert [e['answer'] for e in memory.recall(WORKSPACE, 'D-001')] == ['पंचसूत्र पाँच आदतें हैं।']
    finally:
        memory.use_client(None)


def test_unreachable_redis_url_falls_back_and_waits_before_retrying(client, monkeypatch):
    import memory
    import settings
    monkeypatch.setattr(settings, 'REDIS_URL', 'redis://127.0.0.1:1/0')  # nothing listens on port 1
    memory.use_client(None)
    try:
        assert memory.backend() == 'store'
        assert memory._client is None and memory._down_until > 0
        assert memory._redis() is None  # not retried until RETRY seconds pass
        assert memory.recall(WORKSPACE, 'D-001') == []
    finally:
        memory.use_client(None)
