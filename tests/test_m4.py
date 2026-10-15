"""M4: Plan-and-Execute, ReAct, ReWOO, the self-check, pivot language mode, and compare runs sharing consent."""
import json

from conftest import WS, login, stream, tool_call

LAKHPATI = 'क्या मैं लखपति दीदी बन गई हूँ?'


def lokos_id(client):
    return next(s['id'] for s in client.get('/v1/sources', headers=WS).json()['sources'] if s['name'] == 'lokos_mock.xlsx')


def answer_consents(client, events, button='yes'):
    for e in events:
        if e['type'] == 'consent_request':
            client.post(f'/v1/consents/{e["data"]["consent_id"]}/answer', headers=WS, json={'button': button})


def run_turn(client, session, text, button='yes'):
    """Ask, answer every consent card with `button`, and follow the stream to the end."""
    turn = client.post(f'/v1/sessions/{session["id"]}/turns', headers=WS, json={'text': text}).json()['id']
    events, end = stream(client, turn)
    while end['status'] == 'waiting':
        answer_consents(client, events, button)
        more, end = stream(client, turn, after=events[-1]['id'])
        events += more
    return events, end


def test_plan_and_execute_asks_once_and_gives_exact_figures(client, fake_llm):
    session = login(client, 'D-001')
    lokos = lokos_id(client)
    seen = {}
    plan = {'needs_records': True, 'purpose': 'lakhpati_status_check', 'scheme_topics': ['lakhpati_didi'],
            'items': [{'source': lokos, 'table': 'dar', 'fields': ['month', 'income', 'expense', 'cycle_no']},
                      {'source': lokos, 'table': 'enterprise', 'fields': ['type', 'monthly_turnover']}],
            'steps': ['Ask permission once', 'Read DAR and enterprise', 'Compare with the Lakhpati rule']}

    def writer(messages, tools):
        seen['facts'] = json.loads(messages[0]['content'].split('Facts:\n', 1)[1])
        return 'सुनीता जी, डिजिटल आजीविका रजिस्टर के अनुसार पिछले 12 महीनों में आपकी आमदनी ₹1,24,800 रही, यानी हर महीने ₹10,400।'
    fake_llm.script += [f'```json\n{json.dumps(plan)}\n```', writer]
    events, end = run_turn(client, session, LAKHPATI)

    types = [e['type'] for e in events]
    assert types.count('consent_request') == 1 and 'plan' in types and end['status'] == 'done'
    dar = next(r for r in seen['facts']['registers'] if r['table'] == 'dar')
    assert dar['last_12_months_net_income'] == 124800 and dar['last_12_months_monthly_average_net_income'] == 10400
    assert dar['business_cycles']['3']['net_income'] == 64680 and dar['business_cycles']['4']['net_income'] == 60120
    assert 'Lakhpati Didi (DAY-NRLM)' in seen['facts']['schemes']['lakhpati_didi']
    metrics = end['runs'][0]['result']['metrics']
    assert metrics['llm_calls'] == 2 and metrics['consent_prompts'] == 1 and metrics['grounded_numbers'] == '2/2'
    assert not tool_calls_named(fake_llm, 'calculate')


def tool_calls_named(fake, name):
    return [c for c in fake.calls if any((m.get('tool_calls') or [{}])[0].get('function', {}).get('name') == name
                                         for m in c['messages'] if m['role'] == 'assistant')]


def test_plan_and_execute_without_records_and_bad_json(client, fake_llm):
    session = login(client, 'D-002')
    fake_llm.script += ['I think we should look at the records.',          # not JSON: the planner gets one more try
                        '{"needs_records": false, "items": [], "scheme_topics": ["panchsutra"], "steps": ["Explain"]}',
                        'पंचसूत्र समूह की पाँच आदतें हैं।']
    events, end = run_turn(client, session, 'पंचसूत्र क्या है?')
    assert 'consent_request' not in [e['type'] for e in events] and end['status'] == 'done'
    assert 'not valid JSON' in fake_llm.calls[1]['messages'][-1]['content']
    assert end['runs'][0]['result']['metrics']['grounded_numbers'] is None


def test_react_asks_as_it_goes(client, fake_llm):
    session = login(client, 'D-001', agent='react', critic=False)
    lokos = lokos_id(client)

    def act(tool, **args):
        return f'Thought: I need to use {tool}.\nAction: {tool}\nAction Input: {json.dumps(args, ensure_ascii=False)}\nObservation: (made up)'

    def last_observation(messages):
        return json.loads(messages[-1]['content'].removeprefix('Observation: '))

    fake_llm.script += [
        act('request_consent', purpose='income_question', items=[{'source': lokos, 'table': 'dar', 'fields': ['month', 'income']}]),
        lambda m, t: act('fetch_records', consent_id=last_observation(m)['consent_id'], source=lokos, table='dar', fields=['month', 'income']),
        act('request_consent', purpose='enterprise_question', items=[{'source': lokos, 'table': 'enterprise', 'fields': ['type']}]),
        lambda m, t: act('fetch_records', consent_id=last_observation(m)['consent_id'], source=lokos, table='enterprise', fields=['type']),
        'Thought: I can answer now\nFinal Answer: आपका धंधा डेयरी है।',
    ]
    events, end = run_turn(client, session, 'मेरा धंधा कैसा चल रहा है?')
    assert [e['type'] for e in events].count('consent_request') == 2
    assert any(e['type'] == 'thought' for e in events)
    assert fake_llm.stops[0] == ['Observation:']
    # The made-up "Observation" the model wrote is cut off; only the real one goes back to it.
    assert '(made up)' not in json.dumps(fake_llm.calls[1]['messages'], ensure_ascii=False)
    result = end['runs'][0]['result']
    assert result['answer'] == 'आपका धंधा डेयरी है।' and result['metrics']['consent_prompts'] == 2


def test_rewoo_runs_the_plan_without_llm_in_between(client, fake_llm):
    session = login(client, 'D-001', agent='rewoo')
    lokos = lokos_id(client)
    seen = {}
    plan = {'steps': [
        {'id': 'E1', 'tool': 'request_consent', 'why': 'permission', 'args': {
            'purpose': 'income_question', 'items': [{'source': lokos, 'table': 'dar', 'fields': ['month', 'income', 'expense']}]}},
        {'id': 'E2', 'tool': 'fetch_records', 'why': 'read', 'args': {
            'consent_id': '#E1', 'source': lokos, 'table': 'dar', 'fields': ['month', 'income', 'expense']}},
        {'id': 'E3', 'tool': 'calculate', 'why': 'income minus expenses', 'args': {
            'expression': 'sum(i) - sum(e)', 'variables': {'i': '#E2.income', 'e': '#E2.expense'}}}]}

    def solver(messages, tools):
        seen['evidence'] = json.loads(messages[0]['content'].split('Plan and evidence:\n', 1)[1])
        return 'दो साल में आपकी कुल आमदनी, खर्च घटाकर, ₹1,82,400 रही।'
    fake_llm.script += [json.dumps(plan), solver]
    events, end = run_turn(client, session, 'दो साल में मेरी आमदनी कितनी रही?')
    assert len(fake_llm.calls) == 2
    calc = next(s for s in seen['evidence'] if s['step'] == 'E3')['result']
    assert calc == {'result': 182400}
    assert 'rows' not in next(s for s in seen['evidence'] if s['step'] == 'E2')['result']  # summaries, not every row
    assert end['runs'][0]['result']['metrics']['grounded_numbers'] == '1/1'


def test_critic_rewrites_an_invented_figure(client, fake_llm):
    session = login(client, 'D-007', agent='native_tools')
    lokos = lokos_id(client)
    fake_llm.script += [
        tool_call('request_consent', purpose='savings_question', items=[{'source': lokos, 'table': 'savings', 'fields': ['month', 'amount', 'cumulative']}]),
        lambda m, t: tool_call('fetch_records', consent_id=json.loads(m[-1]['content'])['consent_id'], source=lokos,
                               table='savings', fields=['month', 'amount', 'cumulative']),
        'कमला जी, आपकी कुल बचत ₹25,000 है।',               # invented
        'कमला जी, आपकी कुल बचत ₹15,450 है।',               # the critic's rewrite
    ]
    events, end = run_turn(client, session, 'मेरी कुल बचत कितनी है?')
    critic = next(e['data'] for e in events if e['type'] == 'critic')
    assert critic['rewrote'] and critic['missing'] == ['25,000'] and critic['after'] == '1/1'
    assert '25,000' in fake_llm.calls[-1]['messages'][-1]['content']
    assert end['runs'][0]['result']['answer'] == 'कमला जी, आपकी कुल बचत ₹15,450 है।'


def test_pivot_mode_translates_and_protects_amounts(client, fake_llm, monkeypatch):
    from bhashini import client as bhashini
    calls = []

    def fake_translate(text, source, target, service_id=None):
        calls.append((source, target, text))
        if target == 'en':
            return {'text': text.replace('मेरी आमदनी', 'my income').replace('कितनी है?', 'how much?')}
        return {'text': text.replace('Your income was', 'आपकी आमदनी').replace('.', '।')}
    monkeypatch.setattr(bhashini, 'translate', fake_translate)
    session = login(client, 'D-001', agent='direct', language_mode='pivot')
    fake_llm.script += ['Your income was ₹1,24,800.']
    events, end = run_turn(client, session, 'सुनीता: मेरी आमदनी कितनी है?')
    assert fake_llm.calls[0]['messages'][-1]['content'] == 'सुनीता: my income how much?'   # her name kept out of NMT
    assert 'Reply only in English' in fake_llm.calls[0]['messages'][0]['content']
    assert len(calls) == 2 and 'सुनीता' not in calls[0][2]
    assert calls[1][2].count('ZQ') == 1 and '1,24,800' not in calls[1][2]            # the amount never went to NMT
    assert end['runs'][0]['result']['answer'] == 'आपकी आमदनी ₹1,24,800।'
    assert [e['data']['direction'] for e in events if e['type'] == 'translated'] == ['in', 'out']


def test_compare_run_reuses_the_consent(client, fake_llm):
    session = login(client, 'D-001', agent='plan_execute', compare_with='native_tools', critic=False)
    lokos = lokos_id(client)
    plan = {'needs_records': True, 'purpose': 'savings_question', 'items': [{'source': lokos, 'table': 'savings', 'fields': ['month', 'amount']}],
            'scheme_topics': [], 'steps': ['read savings']}
    fake_llm.script += [
        json.dumps(plan), 'आपने ₹2,400 बचाए।',                                            # main run
        tool_call('request_consent', purpose='savings_question',                          # compare run asks for less
                  items=[{'source': lokos, 'table': 'savings', 'fields': ['amount']}]),
        lambda m, t: tool_call('fetch_records', consent_id=json.loads(m[-1]['content'])['consent_id'], source=lokos,
                               table='savings', fields=['amount']),
        'आपने ₹2,400 बचाए।',
    ]
    events, end = run_turn(client, session, 'मेरी बचत कितनी है?')
    assert [e['type'] for e in events].count('consent_request') == 1                   # she was asked once
    reused = [e['data'] for e in events if e['type'] == 'consent_result' and e['data']['reused']]
    assert len(reused) == 1 and reused[0]['position'] == 1
    runs = end['runs']
    assert [r['status'] for r in runs] == ['done', 'done']
    assert runs[0]['result']['metrics']['consent_prompts'] == 1 and runs[1]['result']['metrics']['consent_prompts'] == 1
    assert runs[1]['result']['metrics']['consent_reused'] == 1 and runs[0]['result']['metrics']['consent_reused'] == 0


def test_facts_and_grounding():
    from agents import facts
    rows = [{'month': f'2026-{m:02d}', 'income': 1000, 'expense': 400, 'cycle_no': 4} for m in range(1, 13)]
    out = facts.summarise('dar', rows)
    assert out['last_12_months_net_income'] == 7200 and out['business_cycles']['4']['net_income'] == 7200
    assert facts.numbers_in('₹1,24,800 और १०४०० और 12 महीने, 3.5') == [124800.0, 10400.0]
    assert facts.grounded('₹1,24,800 और ₹10,401', {124800.0, 10400.0}) == (2, 2, [])
    assert facts.grounded('₹99,999', {124800.0})[2] == [99999.0]


def test_critic_withholds_an_answer_it_cannot_fix(client, fake_llm):
    session = login(client, 'D-007', agent='native_tools')
    lokos = lokos_id(client)
    fake_llm.script += [
        tool_call('request_consent', purpose='savings_question', items=[{'source': lokos, 'table': 'savings', 'fields': ['month', 'cumulative']}]),
        lambda m, t: tool_call('fetch_records', consent_id=json.loads(m[-1]['content'])['consent_id'], source=lokos,
                               table='savings', fields=['month', 'cumulative']),
        'आपकी कुल बचत ₹1,54,500 है।', 'आपकी कुल बचत ₹1,54,000 है।']
    events, end = run_turn(client, session, 'मेरी कुल बचत कितनी है?')
    result = end['runs'][0]['result']
    assert result['answer'].startswith('माफ़ कीजिए') and result['metrics']['grounded_numbers'] == '0/1 (withheld)'
    assert next(e['data'] for e in events if e['type'] == 'critic')['rejected'] == 'आपकी कुल बचत ₹1,54,000 है।'.replace('1,54,000', '1,54,500')


def test_vague_question_gets_a_follow_up_then_continues(client, fake_llm):
    session = login(client, 'D-001')
    fake_llm.script += ['{"follow_up": "सुनीता जी, पैसे किस काम के लिए चाहिए: डेयरी, खेती या कुछ और?", "needs_records": false, "items": []}']
    events, end = run_turn(client, session, 'mujhe paise chahiye')
    assert end['runs'][0]['result']['answer'].startswith('सुनीता जी, पैसे किस काम के लिए') and len(fake_llm.calls) == 1
    assert 'consent_request' not in [e['type'] for e in events]
    # Her reply continues the conversation: the planner sees the earlier question and answer.
    fake_llm.script += ['{"needs_records": false, "items": [], "steps": ["Explain dairy loan options"]}', 'डेयरी के लिए CIF से कर्ज़ मिल सकता है।']
    run_turn(client, session, 'डेयरी के लिए')
    planner = fake_llm.calls[1]['messages']
    assert [m['content'] for m in planner[1:3]] == ['mujhe paise chahiye', 'सुनीता जी, पैसे किस काम के लिए चाहिए: डेयरी, खेती या कुछ और?']
