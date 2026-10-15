"""M2: sessions with prefs, turns over the event stream, conversation context, record-and-replay, models."""
import pytest

from conftest import WS, login, stream


def test_session_prefs(client, fake_llm):
    session = login(client)
    assert session['prefs']['agent'] == 'plan_execute' and session['prefs']['critic'] is True
    saved = client.put(f'/v1/sessions/{session["id"]}/prefs', headers=WS,
                       json={'model': 'openai/gpt-oss-20b', 'steps_view': 'collapsed', 'agent': 'nonsense', 'evil': 1}).json()
    assert saved['prefs']['model'] == 'openai/gpt-oss-20b' and saved['prefs']['steps_view'] == 'collapsed'
    assert saved['prefs']['agent'] == 'plan_execute' and 'evil' not in saved['prefs']


def test_direct_turn_streams_answer_and_remembers_context(client, fake_llm):
    session = login(client)
    client.put(f'/v1/sessions/{session["id"]}/prefs', headers=WS, json={'agent': 'direct'})
    fake_llm.script += ['सुनीता जी, लखपति दीदी वह है जिसकी सालाना आमदनी ₹1 लाख से ज़्यादा हो।',
                        '**सुनीता जी**, आपके रजिस्टर मैं यहाँ नहीं देख सकती।']
    turn = client.post(f'/v1/sessions/{session["id"]}/turns', headers=WS, json={'text': 'लखपति दीदी क्या है?', 'mode': 'voice'}).json()
    events, end = stream(client, turn['id'])
    types = [e['type'] for e in events]
    assert types == ['run_started', 'llm', 'answer', 'metrics'] and end['status'] == 'done'
    answer = events[2]['data']
    assert answer['text'].startswith('सुनीता जी') and answer['language'] == 'hi'
    system = fake_llm.calls[0]['messages'][0]['content']
    assert 'Sunita Devi (सुनीता देवी), treasurer of Maa Durga Jeevika SHG' in system and 'Reply only in Hindi' in system

    # The second question carries the first question and answer as context (no records), and markdown is stripped.
    turn2 = client.post(f'/v1/sessions/{session["id"]}/turns', headers=WS, json={'text': 'मेरी आमदनी कितनी है?'}).json()
    events2, _ = stream(client, turn2['id'])
    assert [m['role'] for m in fake_llm.calls[1]['messages']] == ['system', 'user', 'assistant', 'user']
    assert fake_llm.calls[1]['messages'][1]['content'] == 'लखपति दीदी क्या है?'
    assert next(e for e in events2 if e['type'] == 'answer')['data']['text'].startswith('सुनीता जी, आपके')

    # Reopening a finished stream replays stored events and drives nothing.
    again, end = stream(client, turn2['id'])
    assert [e['type'] for e in again] == ['run_started', 'llm', 'answer', 'metrics'] and len(fake_llm.calls) == 2
    after, _ = stream(client, turn2['id'], after=again[-1]['id'])
    assert after == []

    detail = client.get(f'/v1/sessions/{session["id"]}', headers=WS).json()
    assert [t['status'] for t in detail['turns']] == ['done', 'done'] and detail['turns'][0]['mode'] == 'voice'


def test_llm_error_is_shown_in_hindi(client, monkeypatch):
    from llm import nvidia

    def broken(*args, **kwargs):
        raise nvidia.LlmError('rate_limited', 'NVIDIA API said 429.', 503)
    monkeypatch.setattr(nvidia, 'chat', broken)
    session = login(client)
    turn = client.post(f'/v1/sessions/{session["id"]}/turns', headers=WS, json={'text': 'नमस्ते'}).json()
    events, end = stream(client, turn['id'])
    err = next(e for e in events if e['type'] == 'error')['data']
    assert err['code'] == 'rate_limited' and 'थोड़ी देर बाद' in err['hi'] and end['status'] == 'error'


def test_pause_and_replay(client, fake_llm, monkeypatch):
    """A run that stops after its first LLM call continues in a new stream without repeating that call."""
    import agents
    from agents.context import Pause

    class TwoStep:
        NAME = 'Two-step test agent'
        paused = False

        @classmethod
        def run(cls, ctx):
            first = ctx.llm([{'role': 'user', 'content': 'one'}])['content']
            ctx.emit('thought', text=f'got {first}')
            if not cls.paused:
                cls.paused = True
                raise Pause('time')
            second = ctx.llm([{'role': 'user', 'content': 'two'}])['content']
            return f'{first} {second}'

    monkeypatch.setitem(agents.AGENTS, 'two_step', TwoStep)
    session = login(client)
    client.put(f'/v1/sessions/{session["id"]}/prefs', headers=WS, json={'agent': 'two_step'})
    fake_llm.script += ['A', 'B']
    turn = client.post(f'/v1/sessions/{session["id"]}/turns', headers=WS, json={'text': 'go'}).json()
    events, end = stream(client, turn['id'])
    assert end['status'] == 'running' and [e['type'] for e in events] == ['run_started', 'llm', 'thought']
    events2, end2 = stream(client, turn['id'], after=events[-1]['id'])
    assert end2['status'] == 'done'
    assert [e['type'] for e in events2] == ['llm', 'answer', 'metrics']   # replayed events are not written twice
    assert next(e for e in events2 if e['type'] == 'answer')['data']['text'] == 'A B'
    assert len(fake_llm.calls) == 2                                        # the first call was replayed, not repeated
    metrics = end2['runs'][0]['result']['metrics']
    assert metrics['llm_calls'] == 2 and metrics['prompt_tokens'] == 200
    from core import store
    assert store.recorded_steps(end2['runs'][0]['id']) == {}               # checkpoint purged when the run ended


def test_compare_runs_one_after_another(client, fake_llm):
    session = login(client)
    client.put(f'/v1/sessions/{session["id"]}/prefs', headers=WS,
               json={'agent': 'direct', 'compare_with': 'direct', 'compare_model': 'openai/gpt-oss-20b'})
    fake_llm.script += ['main answer', 'compare answer']
    turn = client.post(f'/v1/sessions/{session["id"]}/turns', headers=WS, json={'text': 'नमस्ते'}).json()
    events, end = stream(client, turn['id'])
    answers = [e['data'] for e in events if e['type'] == 'answer']
    assert [(a['text'], a['position']) for a in answers] == [('main answer', 0), ('compare answer', 1)]
    assert [c['model'] for c in fake_llm.calls] == ['nvidia/nemotron-3.5-lightning-30b-a3b', 'openai/gpt-oss-20b']
    detail = client.get(f'/v1/sessions/{session["id"]}', headers=WS).json()
    assert detail['turns'][0]['answer'] == 'main answer'


def test_models_and_probe(client, fake_llm, monkeypatch):
    from llm import nvidia
    data = client.get('/v1/llm/models', headers=WS).json()
    assert data['default'] == 'nvidia/nemotron-3.5-lightning-30b-a3b'
    assert [m['id'] for m in data['others']] == ['meta/llama-3.2-90b-vision-instruct']
    assert {'id': 'direct', 'name': 'Direct (no tools)'} in data['agents'] and data['agents'][0]['id'] == 'plan_execute'
    monkeypatch.setattr(nvidia, 'probe', lambda model: ('tools', '812 ms'))
    assert client.post('/v1/llm/probe', headers=WS, json={'model': 'openai/gpt-oss-20b'}).json()['probe'] == 'tools'
    data = client.get('/v1/llm/models', headers=WS).json()
    assert next(m for m in data['recommended'] if m['id'] == 'openai/gpt-oss-20b')['probe'] == 'tools'


def test_probe_reads_tool_calls(monkeypatch):
    from conftest import FakeLLM, tool_call
    from llm import nvidia
    monkeypatch.setattr(nvidia, 'chat', FakeLLM(tool_call('get_savings', member_id='D-001')))
    assert nvidia.probe('x')[0] == 'tools'
    monkeypatch.setattr(nvidia, 'chat', FakeLLM('Her savings are 500.'))
    assert nvidia.probe('x')[0] == 'text'


@pytest.mark.skipif(True, reason='set NVIDIA_LIVE=1 and remove this skip to call NVIDIA')
def test_live_placeholder():
    pass
