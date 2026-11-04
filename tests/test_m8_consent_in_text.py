"""M8: ReWOO and ReAct ask for consent with the tool, never in words, so the didi sees a consent card on the
first turn instead of having to say "जी देखिए" in a second one."""
import json

from conftest import login
from test_m4 import lokos_id, run_turn

QUESTION = 'नमस्ते मेरे पास कितने पैसे हैं आप बता सकते हैं क्या'
ASKS = 'नमस्ते संतोष जी, मुझे आपकी अनुमति चाहिए। मैं आपके बचत रजिस्टर को देख सकती हूँ। क्या मैं देखूँ?'
FIELDS = ['month', 'amount', 'cumulative']


def rewoo_plan(lokos):
    return json.dumps({'steps': [
        {'id': 'E1', 'tool': 'request_consent', 'why': 'permission', 'args': {
            'purpose': 'savings_question', 'items': [{'source': lokos, 'table': 'savings', 'fields': FIELDS}]}},
        {'id': 'E2', 'tool': 'fetch_records', 'why': 'read', 'args': {
            'consent_id': '#E1', 'source': lokos, 'table': 'savings', 'fields': FIELDS}}]}, ensure_ascii=False)


def act(tool, **args):
    return f'Thought: I need {tool}.\nAction: {tool}\nAction Input: {json.dumps(args, ensure_ascii=False)}'


def last_observation(messages):
    return json.loads(messages[-1]['content'].removeprefix('Observation: '))


def test_rewoo_replans_when_it_asks_in_words(client, fake_llm):
    session = login(client, 'D-005', agent='rewoo')
    lokos = lokos_id(client)
    fake_llm.script += ['{"steps": []}', ASKS,                                # plans nothing, then asks in words
                        rewoo_plan(lokos), 'संतोष जी, आपके बचत रजिस्टर के अनुसार आपकी कुल बचत ₹5,400 है।']
    events, end = run_turn(client, session, QUESTION)
    assert [e['type'] for e in events].count('consent_request') == 1
    assert 'request_consent step' in fake_llm.calls[2]['messages'][-1]['content']   # the re-plan was told why
    rejected = [e['data']['reason'] for e in events if e['type'] == 'writer_rejected']
    assert rejected == ['asked for permission instead of requesting consent']
    result = end['runs'][0]['result']
    assert result['answer'].endswith('₹5,400 है।') and result['metrics']['consent_prompts'] == 1
    assert result['metrics']['grounded_numbers'] == '1/1'


def test_rewoo_retries_a_plan_that_is_not_valid_json(client, fake_llm):
    session = login(client, 'D-005', agent='rewoo')
    lokos = lokos_id(client)
    # What nemotron wrote: E1's object is never closed, so the whole plan used to be read as "no steps".
    broken = rewoo_plan(lokos).replace('"fields": ["month", "amount", "cumulative"]}]}}, {"id": "E2"',
                                       '"fields": ["month", "amount", "cumulative"]}]}, {"id": "E2"', 1)
    assert broken != rewoo_plan(lokos)
    fake_llm.script += [broken, rewoo_plan(lokos), 'संतोष जी, आपकी कुल बचत ₹5,400 है।']
    events, end = run_turn(client, session, QUESTION)
    assert 'not valid JSON' in fake_llm.calls[1]['messages'][-1]['content']
    assert [e['type'] for e in events].count('consent_request') == 1 and end['runs'][0]['status'] == 'done'


def test_rewoo_solver_without_records_is_told_not_to_state_figures(client, fake_llm):
    session = login(client, 'D-005', agent='rewoo')
    fake_llm.script += ['{"steps": []}', 'पंचसूत्र समूह की पाँच आदतें हैं।']
    _, end = run_turn(client, session, 'पंचसूत्र क्या है?')
    solver = fake_llm.calls[1]['messages'][0]['content']
    # The ₹1,24,800 that once reached Santosh was the money-format example in the prompt, copied as her savings.
    assert 'None of her records were read' in solver and '1,24,800' not in solver
    assert len(fake_llm.calls) == 2 and end['runs'][0]['result']['answer'] == 'पंचसूत्र समूह की पाँच आदतें हैं।'


def test_react_is_nudged_to_request_consent(client, fake_llm):
    session = login(client, 'D-005', agent='react', critic=False)
    lokos = lokos_id(client)
    fake_llm.script += [
        ASKS,                                                                  # no Action: asks her in words
        act('request_consent', purpose='savings_question', items=[{'source': lokos, 'table': 'savings', 'fields': FIELDS}]),
        lambda m, t: act('fetch_records', consent_id=last_observation(m)['consent_id'], source=lokos, table='savings', fields=FIELDS),
        'Thought: I can answer now\nFinal Answer: संतोष जी, आपकी कुल बचत ₹5,400 है।',
    ]
    events, end = run_turn(client, session, QUESTION)
    assert [e['type'] for e in events].count('consent_request') == 1
    assert 'request_consent' in fake_llm.calls[1]['messages'][-1]['content']
    assert end['runs'][0]['result']['answer'] == 'संतोष जी, आपकी कुल बचत ₹5,400 है।'


def test_react_final_answer_asking_permission_is_nudged_once(client, fake_llm):
    session = login(client, 'D-005', agent='react', critic=False)
    fake_llm.script += [f'Thought: I should ask.\nFinal Answer: {ASKS}', f'Final Answer: {ASKS}']
    _, end = run_turn(client, session, QUESTION)
    assert len(fake_llm.calls) == 2                                            # one nudge, then it is not looped
    assert end['runs'][0]['result']['answer'] == ASKS


def test_compare_rewoo_with_react_shows_one_card_on_the_first_turn(client, fake_llm):
    session = login(client, 'D-005', agent='rewoo', compare_with='react', critic=False)
    lokos = lokos_id(client)
    fake_llm.script += [
        '{"steps": []}', ASKS, rewoo_plan(lokos), 'संतोष जी, आपकी कुल बचत ₹5,400 है।',          # ReWOO, re-planned
        ASKS,                                                                                  # ReAct, nudged
        act('request_consent', purpose='savings_question', items=[{'source': lokos, 'table': 'savings', 'fields': FIELDS}]),
        lambda m, t: act('fetch_records', consent_id=last_observation(m)['consent_id'], source=lokos, table='savings', fields=FIELDS),
        'Final Answer: संतोष जी, आपकी कुल बचत ₹5,400 है।',
    ]
    events, end = run_turn(client, session, QUESTION)
    assert [e['type'] for e in events].count('consent_request') == 1                   # she was asked once, on this turn
    assert [r['status'] for r in end['runs']] == ['done', 'done']
    assert [r['result']['metrics']['consent_prompts'] for r in end['runs']] == [1, 1]
    assert end['runs'][1]['result']['metrics']['consent_reused'] == 1
    assert all(r['result']['answer'].endswith('₹5,400 है।') for r in end['runs'])


def test_asks_permission():
    from agents.writer import asks_instead_of_consent, asks_permission

    class Ctx:
        consent_asked = False
    assert asks_permission(ASKS) and asks_permission('क्या मुझे यह जानकारी लेने की अनुमति देंगी?')
    assert asks_permission('Shall I check your register?')
    assert not asks_permission('संतोष जी, आपकी कुल बचत ₹5,400 है।')
    assert not asks_permission('आपने अनुमति नहीं दी, इसलिए मैं ज़मीन नहीं देख पाई।')
    assert asks_instead_of_consent(Ctx(), ASKS)
    Ctx.consent_asked = True                         # she was asked with a card (or refused it earlier): not nudged
    assert not asks_instead_of_consent(Ctx(), ASKS)
