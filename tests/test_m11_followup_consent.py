"""M11: a follow-up does not ask for her consent again. Her answer to the agent's question, or her next question about the
same records, reuses a consent she gave earlier in this login for the same purpose; a new register or field, another
purpose, or a new login is asked."""
import json

from conftest import login
from test_m4 import lokos_id, run_turn

SAVINGS = ['month', 'amount', 'cumulative']


def projection_plan(lokos):
    return {'needs_records': True, 'projection': True, 'purpose': 'savings_question', 'decision': None, 'business_plan': None,
            'items': [{'source': lokos, 'table': 'savings', 'fields': SAVINGS}], 'scheme_topics': [], 'steps': ['Project']}


def extract(**p):
    return json.dumps({'follow_up': None, 'new_deposit': None, 'months': None, 'target': None, 'annual_rate': 0,
                       'lump_sum': 0, **p}, ensure_ascii=False)


def records_plan(lokos, table, fields, purpose):
    return {'needs_records': True, 'purpose': purpose, 'items': [{'source': lokos, 'table': table, 'fields': fields}],
            'scheme_topics': [], 'steps': ['Read']}


def consents(events):
    return [e for e in events if e['type'] == 'consent_request']


def test_her_answer_to_the_follow_up_is_not_asked_again(client, fake_llm):
    session = login(client, 'D-001')
    lokos = lokos_id(client)
    asked = 'आप हर महीने कितने रुपये और जमा करना चाहेंगी?'
    fake_llm.script += [json.dumps(projection_plan(lokos)), extract(follow_up=asked, new_deposit=200, months=12)]
    first, end = run_turn(client, session, 'बचत बढ़ाऊँ तो क्या फ़ायदा होगा?')
    assert len(consents(first)) == 1 and end['runs'][0]['result']['answer'] == asked

    fake_llm.script += [json.dumps(projection_plan(lokos)), extract(new_deposit=300, months=12)]
    second, end = run_turn(client, session, '100 रुपये और, एक साल के लिए')
    assert consents(second) == []
    result = next(e['data'] for e in second if e['type'] == 'consent_result')
    assert result['reused'] and result['decision'] == 'granted'
    chart = next(e['data'] for e in second if e['type'] == 'chart')
    assert chart['figures']['projected_new'] == 15600 and chart['figures']['added'] == 1200


def test_her_next_question_on_the_same_register_reuses_it_but_a_new_register_is_asked(client, fake_llm):
    session = login(client, 'D-001')
    lokos = lokos_id(client)
    fake_llm.script += [json.dumps(records_plan(lokos, 'savings', ['month', 'cumulative'], 'savings_question')), 'बचत ₹12,000 है।']
    first, _ = run_turn(client, session, 'मेरी कुल बचत कितनी है?')
    assert len(consents(first)) == 1

    fake_llm.script += [json.dumps(projection_plan(lokos)), extract(new_deposit=300, months=12)]
    second, _ = run_turn(client, session, 'और हर महीने 100 और डालूँ तो एक साल में?')
    # her first consent covered month and cumulative only; the projection also reads amount, so she is asked
    assert len(consents(second)) == 1

    fake_llm.script += [json.dumps(projection_plan(lokos)), extract(new_deposit=400, months=12)]
    third, _ = run_turn(client, session, 'और 200 और डालूँ तो?')
    assert consents(third) == []   # the same fields as her previous turn

    fake_llm.script += [json.dumps(records_plan(lokos, 'loans', ['outstanding'], 'loan_question')), 'कर्ज़ ₹28,993 बाकी है।']
    fourth, _ = run_turn(client, session, 'मेरा कर्ज़ कितना बाकी है?')
    assert len(consents(fourth)) == 1   # a new register is always asked


def test_a_consent_earlier_in_the_login_is_reused_for_the_same_purpose(client, fake_llm):
    session = login(client, 'D-001')
    lokos = lokos_id(client)
    savings = json.dumps(records_plan(lokos, 'savings', ['month', 'cumulative'], 'savings_question'))
    fake_llm.script += [savings, 'बचत ₹12,000 है।']
    run_turn(client, session, 'मेरी कुल बचत कितनी है?')
    fake_llm.script += [json.dumps({'needs_records': False, 'items': [], 'scheme_topics': ['panchsutra'], 'steps': []}),
                        'पंचसूत्र पाँच आदतें हैं।']
    run_turn(client, session, 'पंचसूत्र क्या है?')
    fake_llm.script += [savings, 'बचत ₹12,000 है।']
    third, _ = run_turn(client, session, 'मेरी बचत फिर से बताइए')
    assert consents(third) == []   # a different question in between does not make her say yes again
    result = next(e['data'] for e in third if e['type'] == 'consent_result')
    assert result['reused'] is True and result['decision'] == 'granted'


def test_another_purpose_or_a_new_login_is_asked_again(client, fake_llm):
    session = login(client, 'D-001')
    lokos = lokos_id(client)
    fake_llm.script += [json.dumps(records_plan(lokos, 'savings', ['cumulative'], 'savings_question')), 'बचत ₹12,000 है।']
    run_turn(client, session, 'मेरी कुल बचत कितनी है?')
    fake_llm.script += [json.dumps(records_plan(lokos, 'savings', ['cumulative'], 'lakhpati_status_check')), 'ठीक है।']
    other, _ = run_turn(client, session, 'क्या मैं लखपति दीदी बन सकती हूँ?')
    assert len(consents(other)) == 1   # the same field for another purpose is asked

    again = login(client, 'D-001', samples=False)
    fake_llm.script += [json.dumps(records_plan(lokos, 'savings', ['cumulative'], 'savings_question')), 'बचत ₹12,000 है।']
    later, _ = run_turn(client, again, 'मेरी बचत फिर से बताइए')
    assert len(consents(later)) == 1   # her yes lasts this login only


def test_the_card_says_it_covers_her_next_question_too(client, fake_llm):
    session = login(client, 'D-001')
    lokos = lokos_id(client)
    fake_llm.script += [json.dumps(records_plan(lokos, 'savings', ['cumulative'], 'savings_question')), 'बचत ₹12,000 है।']
    events, _ = run_turn(client, session, 'मेरी कुल बचत कितनी है?')
    card = consents(events)[0]['data']
    assert 'इस सवाल और इस बातचीत में इसी बारे में आपके आगे के सवालों के लिए' in json.dumps(card, ensure_ascii=False)
