"""M14: the flows built for the comparison (scripts/eval_flows.py): a plan reviewer before consent, self-review after
the answer, and a hybrid that hands over to ReAct when no valid plan comes back."""
import json

from conftest import login
from test_m4 import lokos_id, run_turn

SAVINGS_Q = 'मेरी कुल बचत कितनी है?'


def plan_for(lokos, *tables):
    fields = {'savings': ['month', 'cumulative'], 'loans': ['outstanding'], 'dar': ['month', 'income']}
    return json.dumps({'needs_records': True, 'purpose': 'savings_question', 'scheme_topics': [], 'steps': ['Read'],
                       'items': [{'source': lokos, 'table': t, 'fields': fields[t]} for t in tables]})


def test_the_plan_reviewer_trims_the_plan_before_she_is_asked(client, fake_llm):
    session = login(client, 'D-001', agent='plan_review', verify='off')  # gate A off: only the reviewer trims
    lokos = lokos_id(client)
    fake_llm.script += [plan_for(lokos, 'savings', 'loans'), plan_for(lokos, 'savings'),
                        'सुनीता जी, आपके बचत रजिस्टर के अनुसार आपकी कुल बचत ₹12,000 है।']
    events, end = run_turn(client, session, SAVINGS_Q)
    card = next(e['data'] for e in events if e['type'] == 'consent_request')
    assert [i['table'] for i in card['items']] == ['savings']
    review = next(e['data'] for e in events if e['type'] == 'plan_review')
    assert review['before'] == ['loans', 'savings'] and review['after'] == ['savings']
    assert 'Proposed plan' in fake_llm.calls[1]['messages'][-1]['content']


def test_self_review_revises_only_when_the_critique_finds_a_problem(client, fake_llm):
    session = login(client, 'D-001', agent='self_review')
    lokos = lokos_id(client)
    good = 'सुनीता जी, आपके बचत रजिस्टर के अनुसार आपकी कुल बचत ₹12,000 है।'
    fake_llm.script += [plan_for(lokos, 'savings'), good, 'OK']
    _, end = run_turn(client, session, SAVINGS_Q)
    assert end['runs'][0]['result']['answer'] == good and len(fake_llm.calls) == 3

    session = login(client, 'D-001', samples=False, agent='self_review')
    fake_llm.script += [plan_for(lokos, 'savings'), 'सुनीता जी, आप बचत करती रहिए।',
                        'It does not give her total savings (12,000).', good]
    events, end = run_turn(client, session, SAVINGS_Q)
    assert end['runs'][0]['result']['answer'] == good
    assert next(e['data'] for e in events if e['type'] == 'self_review')['problems'].startswith('It does not give')


def test_the_hybrid_hands_over_to_react_when_no_plan_comes_back(client, fake_llm):
    session = login(client, 'D-001', agent='hybrid')
    fake_llm.script += ['not a plan', 'still not a plan', 'Thought: I can answer now\nFinal Answer: पंचसूत्र पाँच आदतें हैं।']
    _, end = run_turn(client, session, 'पंचसूत्र क्या होता है?')
    assert end['runs'][0]['result']['answer'] == 'पंचसूत्र पाँच आदतें हैं।'
    assert 'Thought' in fake_llm.calls[2]['messages'][0]['content']  # the third call is ReAct's
