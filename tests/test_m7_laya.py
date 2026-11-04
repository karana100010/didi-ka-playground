"""The Laya planner: Laya's labels become the plan; consent, fetching and the writer are unchanged. Laya is mocked."""
import pytest

from conftest import WS, login
from test_m4 import run_turn


def laya_answers(intent, p_intent, register='none', enterprise='dairy'):
    choice = lambda c, p=0.9: {'type': 'choice', 'choice': c, 'probabilities': {c: p}}  # noqa: E731
    return {'answers': {'intent': choice(intent, p_intent), 'register': choice(register), 'enterprise': choice(enterprise)},
            'routing': {'model': 'multilingual'}, 'ms': 30}


@pytest.fixture()
def laya(monkeypatch):
    from llm import laya as client
    calls_obj = type('Calls', (list,), {})()
    calls_obj.next = []
    monkeypatch.setattr(client, 'predict', lambda state, questions, timeout=120: (calls_obj.append(state), calls_obj.next.pop(0))[1])
    return calls_obj


def test_laya_plans_a_savings_question(client, fake_llm, laya):
    session = login(client, 'D-001', planner='laya')
    laya.next.append(laya_answers('my_numbers', 0.97, 'savings'))
    fake_llm.script += ['सुनीता जी, आपकी बचत रजिस्टर के अनुसार आपकी कुल बचत अच्छी है।']
    events, end = run_turn(client, session, 'मेरी कुल बचत कितनी है?')
    request = next(e for e in events if e['type'] == 'consent_request')['data']
    assert [i['table'] for i in request['items']] == ['savings']
    assert len(fake_llm.calls) == 1, 'Laya planned; only the writer used the LLM'
    assert any(e['type'] == 'tool_call' and e['data']['tool'] == 'laya_plan' for e in events)
    assert end['status'] == 'done'


def test_unsure_lakhpati_label_falls_back_and_vague_asks(client, fake_llm, laya):
    session = login(client, 'D-001', planner='laya')
    laya.next.append(laya_answers('lakhpati', 0.6))  # Panchsutra, mislabelled: no records, no decision
    fake_llm.script += ['पंचसूत्र समूह की पाँच आदतें हैं।']
    events, end = run_turn(client, session, 'पंचसूत्र क्या होता है?')
    assert 'consent_request' not in [e['type'] for e in events] and end['status'] == 'done'

    laya.next.append(laya_answers('needs_money', 0.94))
    events, end = run_turn(client, session, 'मुझे पैसे चाहिए')
    answer = next(e for e in events if e['type'] == 'answer')['data']['text']
    assert 'किस काम के लिए' in answer and len(fake_llm.calls) == 1


def test_laya_plan_extracts_cows_and_falls_back_when_down(client, fake_llm, laya, monkeypatch):
    from agents import laya_planner
    assert laya_planner._changes('मुझे दो गाय और खरीदनी है', 'dairy') == {'cows_to_add': 2}
    assert laya_planner._changes('add 3 machines', 'tailoring') == {'machines_to_add': 3}

    from llm import laya as client_module

    def down(*a, **k):
        raise client_module.LayaError('no sidecar')
    monkeypatch.setattr(client_module, 'predict', down)
    session = login(client, 'D-002', planner='laya')
    fake_llm.script += ['{"needs_records": false, "items": [], "scheme_topics": ["panchsutra"], "steps": ["Explain"]}',
                        'पंचसूत्र समूह की पाँच आदतें हैं।']
    events, end = run_turn(client, session, 'पंचसूत्र क्या है?')
    assert end['status'] == 'done' and len(fake_llm.calls) == 2, 'the LLM planned instead'


def test_laya_status_endpoint(client):
    body = client.get('/v1/laya', headers=WS).json()
    assert 'ok' in body


def test_follow_up_reply_naming_a_business_gets_a_plan(client, fake_llm, laya):
    session = login(client, 'D-001', planner='laya')
    laya.next.append(laya_answers('needs_money', 0.6, 'loans'))  # vague, whatever register Laya guesses
    events, _ = run_turn(client, session, 'मुझे पैसे चाहिए')
    assert 'consent_request' not in [e['type'] for e in events]
    reply = laya_answers('lakhpati', 0.98, 'none', 'dairy')
    reply['answers']['enterprise']['probabilities'] = {'dairy': 0.98}
    laya.next.append(reply)  # Laya's intent label is ignored for a reply; the enterprise decides
    events, end = run_turn(client, session, 'दो गाय और खरीदनी है')
    plan = next(e for e in events if e['type'] == 'plan')['data']
    assert 'Build a dairy business plan' in plan['steps'] and end['status'] == 'done'


def test_writer_speaking_as_her_is_rejected():
    from agents.writer import speaks_as_her
    assert speaks_as_her('मेरा नाम सुनीता है। मेरी कुल बचत ₹12,000 है।')
    assert not speaks_as_her('सुनीता जी, आपकी कुल बचत ₹12,000 है। मेरे हिसाब से यह अच्छी है।')


def test_setup_text_never_reaches_history_or_her(client, fake_llm):
    from agents.writer import leaks_setup
    assert leaks_setup('(परीक्षण मॉडल) आपने पूछा: "x"। असली जवाब के लिए NVIDIA_API_KEY जोड़िए।')
    assert leaks_setup('नमस्ते संतोष जी, मुझे NVIDIA_API_KEY नहीं मिली')
    assert not leaks_setup('संतोष जी, आपकी कुल बचत ₹12,000 है।')
    assert leaks_setup('<|tool_call>call:fetch_records{table: "savings"}')

    session = login(client, 'D-002', agent='direct')
    fake_llm.script += ['(परीक्षण मॉडल) आपने पूछा: "नमस्ते"। असली जवाब के लिए NVIDIA_API_KEY जोड़िए।']
    events, end = run_turn(client, session, 'नमस्ते')
    answer = next(e for e in events if e['type'] == 'answer')['data']['text']
    assert 'NVIDIA' not in answer and any(e['type'] == 'writer_rejected' for e in events)

    seen = {}
    fake_llm.script += [lambda messages, tools: seen.setdefault('m', messages) and 'संतोष जी, नमस्ते।']
    run_turn(client, session, 'आप कैसी हैं?')
    assert not any('NVIDIA' in str(m.get('content')) for m in seen['m'][1:]), 'the leaked answer was not sent back'


def test_english_translations_are_removed_from_a_hindi_answer(client, fake_llm):
    session = login(client, 'D-002', agent='direct', critic=False)
    fake_llm.script += ['आपकी बचत ₹12,000 है। (Your savings are ₹12,000.) SHG से नया कर्ज़ ले सकती हैं। '
                        'You can also take a bank loan. क्या आप आगे बात करना चाहेंगी?']
    events, _ = run_turn(client, session, 'मेरी बचत कितनी है?')
    answer = next(e for e in events if e['type'] == 'answer')['data']['text']
    assert answer == 'आपकी बचत ₹12,000 है। SHG से नया कर्ज़ ले सकती हैं। क्या आप आगे बात करना चाहेंगी?'


def test_a_run_becomes_one_langfuse_trace(client, fake_llm, monkeypatch):
    import tracing
    sent = {'spans': [], 'scores': []}
    monkeypatch.setattr(tracing, 'enabled', lambda: True)
    monkeypatch.setattr(tracing.Tracer, 'flush', lambda self: (sent['spans'].extend(self.spans), sent['scores'].extend(self.scores)))
    session = login(client, 'D-001', agent='direct', critic=False)
    fake_llm.script += ['पंचसूत्र समूह की पाँच आदतें हैं।']
    _, end = run_turn(client, session, 'पंचसूत्र क्या है?')
    run_id = end['runs'][0]['id']
    attrs = lambda span: {a['key']: a['value'] for a in span['attributes']}  # noqa: E731
    kinds = [attrs(s)['langfuse.observation.type']['stringValue'] for s in sent['spans']]
    assert 'generation' in kinds and {s['traceId'] for s in sent['spans']} == {tracing.trace_id(run_id)}
    assert attrs(sent['spans'][0])['langfuse.trace.input']['stringValue'] == 'पंचसूत्र क्या है?'
    assert {s['name'] for s in sent['scores']} >= {'llm_calls', 'consent_prompts', 'run_ms'}


def test_bhashini_models_route_to_dhruva_and_refuse_tools(monkeypatch):
    from llm import bhashini_llm, nvidia
    seen = {}

    class Reply:
        ok, status_code, text = True, 200, ''

        def json(self):
            return {'pipelineResponse': [{'output': [{'target': 'नमस्ते जी। Observation: x'}]}]}
    monkeypatch.setattr(bhashini_llm, 'key', lambda: 'k')
    monkeypatch.setattr(bhashini_llm.requests, 'post', lambda url, json, headers, timeout: seen.update(body=json) or Reply())
    result = nvidia.chat('bhashini/google/gemma-4-26B-A4B-it', [{'role': 'user', 'content': 'नमस्ते'}], stop=['Observation:'])
    assert result['message']['content'] == 'नमस्ते जी। '
    assert seen['body']['pipelineTasks'][0]['config']['serviceId'] == 'bhashini/google/gemma-4-26B-A4B-it'
    try:
        nvidia.chat('bhashini/google/gemma-4-26B-A4B-it', [], tools=[{'type': 'function'}])
        raise AssertionError('tools should be refused')
    except nvidia.LlmError as error:
        assert error.code == 'rejected'


def test_judge_scores_reach_the_trace(client, fake_llm, monkeypatch):
    import judge
    import tracing
    posted = []
    monkeypatch.setattr(tracing, 'enabled', lambda: True)
    monkeypatch.setattr(tracing.Tracer, 'flush', lambda self: None)
    monkeypatch.setattr(tracing, '_config', lambda: ('pk', 'sk', 'http://langfuse'))
    monkeypatch.setattr(judge, '_spawn', lambda target, *args: target(*args))
    monkeypatch.setattr(judge.requests, 'post', lambda url, auth, timeout, json: posted.append(json))
    session = login(client, 'D-001', agent='direct', critic=False)
    fake_llm.script += ['पंचसूत्र समूह की पाँच आदतें हैं।',
                        '{"judge_grounded": 1, "judge_answers_question": 1, "judge_hindi_only": 1, "judge_minimal_data": 1, "reason": "Fine."}']
    _, end = run_turn(client, session, 'पंचसूत्र क्या है?')
    assert fake_llm.calls[-1]['model'] == judge.JUDGE_MODEL
    assert {p['name'] for p in posted} == set(judge.CRITERIA) and posted[0]['traceId'] == tracing.trace_id(end['runs'][0]['id'])


def test_outstanding_adds_up_across_loans():
    from agents import facts
    rows = [{'source': 'SHG internal', 'amount': 15000, 'outstanding': 2626}, {'source': 'Bank linkage', 'amount': 50000, 'outstanding': 26367}]
    out = facts.summarise('loans', rows)
    assert out['current']['outstanding'] == 28993 and len(out['per_item']) == 2
    assert out['per_item'][1] == {'source': 'Bank linkage', 'outstanding': 26367}


def test_wallet_off_on_hosted(monkeypatch):
    """With the wallet off (the Vercel default), its routes and page are 404 and the planner never routes to it."""
    import settings
    from fastapi.testclient import TestClient
    import main
    monkeypatch.setattr(settings, 'WALLET_ENABLED', False)
    c = TestClient(main.app)
    headers = {'X-Workspace': 'test-workspace-1', 'X-Access-Code': settings.ACCESS_CODE}
    for path in ('/verify', '/v1/verifiers', '/.well-known/did.json', '/v1/sessions/abc/wallet'):
        assert c.get(path, headers=headers).status_code == 404, path
    assert c.get('/healthz').json()['wallet'] == 'off'
