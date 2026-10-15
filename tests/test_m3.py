"""M3: consent requests and answers, the gateway's checks, refusals remembered, policy, department view."""
import json
import re

import pytest

from conftest import WS, login as _login, stream, tool_call

NATIVE = {'agent': 'native_tools', 'critic': False}


def login(client, didi='D-001', samples=True):
    return _login(client, didi, samples, **NATIVE)

LAKHPATI = 'क्या मैं लखपति दीदी बन गई हूँ?'


def source_ids(client):
    sources = {s['name']: s['id'] for s in client.get('/v1/sources', headers=WS).json()['sources']}
    return sources['lokos_mock.xlsx'], sources['vbg_agri.sqlite']


def ask(client, session, text):
    return client.post(f'/v1/sessions/{session["id"]}/turns', headers=WS, json={'text': text}).json()['id']


def consent_event(events):
    return next(e['data'] for e in events if e['type'] == 'consent_request')


def tool_results(call):
    return [json.loads(m['content']) for m in call['messages'] if m['role'] == 'tool']


def test_lakhpati_question_end_to_end(client, fake_llm):
    session = login(client, 'D-001')
    lokos, _ = source_ids(client)
    system = {}

    def consent_call(messages, tools):
        system['text'] = messages[0]['content']
        return tool_call('request_consent', purpose='lakhpati_status_check', items=[
            {'source': lokos, 'table': 'dar', 'fields': ['month', 'income', 'expense', 'cycle_no']},
            {'source': lokos, 'table': 'enterprise', 'fields': ['type', 'monthly_turnover']}])

    def fetch_calls(messages, tools):
        consent = tool_results({'messages': messages})[-1]
        assert consent['decision'] == 'granted' and consent['refused_registers'] == ['loans']
        cid = consent['consent_id']
        return {'content': '', 'tool_calls': [
            {'id': 'f1', 'type': 'function', 'function': {'name': 'fetch_records', 'arguments': json.dumps(
                {'consent_id': cid, 'source': lokos, 'table': 'dar', 'fields': ['month', 'income', 'expense', 'cycle_no']})}},
            {'id': 'f2', 'type': 'function', 'function': {'name': 'fetch_records', 'arguments': json.dumps(
                {'consent_id': cid, 'source': lokos, 'table': 'enterprise', 'fields': ['type', 'monthly_turnover']})}}]}

    def calc_call(messages, tools):
        dar, enterprise = tool_results({'messages': messages})[-2:]
        assert dar['count'] == 28 and set(dar['rows'][0]) == {'month', 'income', 'expense', 'cycle_no'}
        assert enterprise['rows'] == [{'type': 'dairy', 'monthly_turnover': 18240}]
        last12 = [r for r in dar['rows'] if r['month'] >= '2025-10']
        return tool_call('calculate', expression='sum(income) - sum(expense)',
                         variables={'income': [r['income'] for r in last12], 'expense': [r['expense'] for r in last12]})

    def final(messages, tools):
        assert tool_results({'messages': messages})[-1] == {'result': 124800}
        return 'सुनीता जी, आपके डिजिटल आजीविका रजिस्टर के अनुसार पिछले 12 महीनों में आमदनी ₹1,24,800 रही।'

    interpreter = '{"decision": "grant", "granted_items": [1, 2], "refused_registers": ["loans"], "confidence": 0.93}'
    fake_llm.script += [consent_call, interpreter, fetch_calls, calc_call, final]

    turn = ask(client, session, LAKHPATI)
    events, end = stream(client, turn)
    assert end['status'] == 'waiting'
    assert 'Source ' + lokos in system['text'] and 'table dar: Digital Aajeevika Register' in system['text']
    card = consent_event(events)
    assert card['purpose']['hi'] == 'यह जाँचने के लिए कि आप लखपति दीदी हैं या नहीं'
    assert card['statement']['hi']['lines'] == ['डिजिटल आजीविका रजिस्टर से: महीना, आमदनी, खर्च और कारोबार-चक्र।',
                                                'उद्यम रजिस्टर से: धंधे का प्रकार और मासिक कारोबार।']
    assert card['statement']['hi']['speech'].startswith('यह जाँचने के लिए कि आप लखपति दीदी हैं या नहीं, हमें आपकी यह जानकारी देखनी होगी:')

    answered = client.post(f'/v1/consents/{card["consent_id"]}/answer', headers=WS,
                           json={'text': 'हाँ देख लो, पर लोन वाली बात मत देखना', 'mode': 'voice'}).json()
    assert answered['status'] == 'granted' and answered['refused'] == ['loans']
    assert answered['summary']['text'] == 'समझा गया: हाँ · सारी जानकारी · कर्ज़ रजिस्टर नहीं देखा जाएगा'
    assert answered['interpreter']['by'] == 'llm'

    events2, end2 = stream(client, turn, after=events[-1]['id'])
    assert end2['status'] == 'done'
    types = [e['type'] for e in events2]
    assert types == ['consent_result', 'llm', 'tool_call', 'tool_call', 'llm', 'tool_call', 'llm', 'answer', 'discarded', 'metrics']
    assert len(fake_llm.calls) == 5  # the first LLM call was replayed, not repeated
    answer = next(e['data'] for e in events2 if e['type'] == 'answer')
    assert [(e['register']['hi'], e['rows']) for e in answer['evidence']] == [('डिजिटल आजीविका रजिस्टर', 28), ('उद्यम रजिस्टर', 1)]
    metrics = end2['runs'][0]['result']['metrics']
    assert metrics['consent_prompts'] == 1 and len(metrics['fields_read']) == 6 and metrics['llm_calls'] == 4

    consents = client.get(f'/v1/sessions/{session["id"]}/consents', headers=WS).json()['consents']
    assert consents[0]['status'] == 'granted' and consents[0]['used'] and consents[0]['discarded']

    dept = client.get('/v1/department', headers=WS, params={'owner': 'LokOS (DAY-NRLM)'}).json()
    assert dept['owners'] == ['AgriStack', 'LokOS (DAY-NRLM)', 'VB–G RAM G']
    assert dept['stats'] == {'accesses_today': 2, 'didis': 1, 'refused': 0, 'blocked': 0}
    assert {r['status'] for r in dept['log']} == {'Used, then discarded'} and dept['log'][0]['didi'] == 'Sunita D.'
    assert '18240' not in json.dumps(dept)  # fields, never values

    # The refusal is remembered: a loan question in the same login is not asked again.
    fake_llm.script += [
        tool_call('request_consent', purpose='loan_question', items=[{'source': lokos, 'table': 'loans', 'fields': ['outstanding']}]),
        lambda messages, tools: (tool_results({'messages': messages})[-1]['decision'] == 'denied' and 'मैं आपका कर्ज़ नहीं देख सकती।')]
    events3, end3 = stream(client, ask(client, session, 'मेरा कितना कर्ज़ बाकी है?'))
    assert 'consent_skipped' in [e['type'] for e in events3] and 'consent_request' not in [e['type'] for e in events3]
    assert end3['status'] == 'done'


def _consent_then(fake_llm, lokos_or_fn, table, fields, purpose='other_question', after=None):
    """Script: ask consent for one table; then (after her answer) fetch it with the granted consent; then answer."""
    def consent(messages, tools):
        source = lokos_or_fn() if callable(lokos_or_fn) else lokos_or_fn
        return tool_call('request_consent', purpose=purpose, items=[{'source': source, 'table': table, 'fields': fields}])

    def fetch(messages, tools):
        result = tool_results({'messages': messages})[-1]
        source = lokos_or_fn() if callable(lokos_or_fn) else lokos_or_fn
        return tool_call('fetch_records', consent_id=result.get('consent_id', 'none'), source=source, table=table, fields=fields)

    def answer(messages, tools):
        after and after(tool_results({'messages': messages})[-1])
        return 'ठीक है।'
    fake_llm.script += [consent, fetch, answer]


def test_group_data_needs_an_office_bearer(client, fake_llm):
    seen = {}
    session = login(client, 'D-005')  # Santosh is a member
    lokos, _ = source_ids(client)
    _consent_then(fake_llm, lokos, 'meetings', ['held', 'repayments_made'], 'bank_linkage_readiness',
                  after=lambda r: seen.update(member=r))
    turn = ask(client, session, 'क्या हमारा समूह बैंक लोन के लिए तैयार है?')
    events, _ = stream(client, turn)
    client.post(f'/v1/consents/{consent_event(events)["consent_id"]}/answer', headers=WS, json={'button': 'yes'})
    stream(client, turn, after=events[-1]['id'])
    assert 'group data' in seen['member']['error']
    dept = client.get('/v1/department', headers=WS, params={'owner': 'LokOS (DAY-NRLM)'}).json()
    assert dept['stats']['blocked'] == 1 and dept['log'][0]['status'].startswith('Blocked: meetings is group data')

    session = login(client, 'D-001', samples=False)  # Sunita is the treasurer
    _consent_then(fake_llm, lokos, 'meetings', ['held', 'repayments_made'], 'bank_linkage_readiness',
                  after=lambda r: seen.update(treasurer=r))
    turn = ask(client, session, 'क्या हमारा समूह बैंक लोन के लिए तैयार है?')
    events, _ = stream(client, turn)
    client.post(f'/v1/consents/{consent_event(events)["consent_id"]}/answer', headers=WS, json={'button': 'yes'})
    stream(client, turn, after=events[-1]['id'])
    assert seen['treasurer']['count'] == 26 and seen['treasurer']['rows'][0] == {'held': 'yes', 'repayments_made': 2400}


def test_paused_source_and_purpose_policy(client, fake_llm):
    seen = {}
    session = login(client, 'D-004')
    lokos, sql_source = source_ids(client)
    client.put(f'/v1/sources/{sql_source}/policy', headers=WS, json={'paused': True})
    _consent_then(fake_llm, sql_source, 'wages_vbg', ['month', 'wages_paid'], 'wages_question', after=lambda r: seen.update(p=r))
    turn = ask(client, session, 'मेरी मज़दूरी कितनी मिली?')
    events, _ = stream(client, turn)
    assert 'PAUSED' in fake_llm.calls[0]['messages'][0]['content']
    client.post(f'/v1/consents/{consent_event(events)["consent_id"]}/answer', headers=WS, json={'button': 'yes'})
    stream(client, turn, after=events[-1]['id'])
    assert 'paused access' in seen['p']['error']

    client.put(f'/v1/sources/{sql_source}/policy', headers=WS, json={'paused': False, 'purposes': {'wages_vbg': ['farm_question']}})
    _consent_then(fake_llm, sql_source, 'wages_vbg', ['month', 'wages_paid'], 'wages_question', after=lambda r: seen.update(q=r))
    turn = ask(client, session, 'मेरी मज़दूरी कितनी मिली?')
    events, _ = stream(client, turn)
    client.post(f'/v1/consents/{consent_event(events)["consent_id"]}/answer', headers=WS, json={'button': 'yes'})
    stream(client, turn, after=events[-1]['id'])
    assert 'does not allow "wages_question"' in seen['q']['error']

    client.put(f'/v1/sources/{sql_source}/policy', headers=WS, json={'paused': False, 'purposes': None})
    _consent_then(fake_llm, sql_source, 'wages_vbg', ['month', 'wages_paid'], 'wages_question', after=lambda r: seen.update(r=r))
    turn = ask(client, session, 'मेरी मज़दूरी कितनी मिली?')
    events, _ = stream(client, turn)
    client.post(f'/v1/consents/{consent_event(events)["consent_id"]}/answer', headers=WS, json={'button': 'yes'})
    stream(client, turn, after=events[-1]['id'])
    assert seen['r']['count'] == 12  # Phoolmati's household only (linked by household_id)


def _pending_consent(client, fake_llm, session, lokos, table='savings', fields=('month', 'amount')):
    # A fresh login each time: a no in one case would otherwise be remembered for the next.
    session = login(client, session['didi']['didi_id'], samples=False)
    _consent_then(fake_llm, lokos, table, list(fields), 'savings_question')
    turn = ask(client, session, 'मेरी बचत कितनी है?')
    events, _ = stream(client, turn)
    return turn, events, consent_event(events)['consent_id']


def test_answers_partial_readback_why_and_unclear(client, fake_llm):
    session = login(client, 'D-007')
    lokos, _ = source_ids(client)
    url = lambda cid: f'/v1/consents/{cid}/answer'  # noqa: E731

    # "Show only some": a partial grant by ticking fields.
    _, _, cid = _pending_consent(client, fake_llm, session, lokos, 'savings', ('month', 'amount', 'cumulative'))
    out = client.post(url(cid), headers=WS, json={'button': 'some', 'granted': [
        {'source_id': lokos, 'table': 'savings', 'fields': ['month', 'cumulative', 'phone_last4']}]}).json()
    assert out['status'] == 'partial' and out['granted'] == [{'source_id': lokos, 'table': 'savings', 'fields': ['month', 'cumulative']}]
    fake_llm.script.clear()

    # A custom word decides without the LLM (हओ is Bundeli for yes, seeded with the samples).
    _, _, cid = _pending_consent(client, fake_llm, session, lokos)
    calls = len(fake_llm.calls)
    assert client.post(url(cid), headers=WS, json={'text': 'हओ'}).json()['status'] == 'granted'
    assert len(fake_llm.calls) == calls
    fake_llm.script.clear()

    # "Why?" gets an explanation and she is asked again; then a plain no.
    _, _, cid = _pending_consent(client, fake_llm, session, lokos)
    out = client.post(url(cid), headers=WS, json={'text': 'पहले बताओ क्यों चाहिए?'}).json()
    assert out['status'] == 'pending' and out['message']['text'].startswith('आपकी बचत के बारे में')
    assert client.post(url(cid), headers=WS, json={'text': 'नहीं'}).json()['status'] == 'denied'
    fake_llm.script.clear()

    # A shaky LLM reading is read back; she confirms.
    _, _, cid = _pending_consent(client, fake_llm, session, lokos)
    fake_llm.script.insert(0, '{"decision": "grant", "granted_items": [1], "refused_registers": [], "confidence": 0.6}')
    out = client.post(url(cid), headers=WS, json={'text': 'चलो देख लेना फिर'}).json()
    assert out['status'] == 'pending' and out.get('readback') and 'क्या यह सही है?' in out['message']['text']
    assert client.post(url(cid), headers=WS, json={'button': 'confirm'}).json()['status'] == 'granted'
    fake_llm.script.clear()

    # Unclear twice is a no.
    _, _, cid = _pending_consent(client, fake_llm, session, lokos)
    fake_llm.script[:0] = ['{"decision": "unclear", "confidence": 0.2}', '{"decision": "unclear", "confidence": 0.1}']
    assert client.post(url(cid), headers=WS, json={'text': 'अरे वो तो'}).json()['status'] == 'pending'
    assert client.post(url(cid), headers=WS, json={'text': 'हम्म'}).json()['status'] == 'denied'


def test_withdrawn_consent_blocks_the_fetch(client, fake_llm):
    seen = {}
    session = login(client, 'D-002')
    lokos, _ = source_ids(client)
    _consent_then(fake_llm, lokos, 'savings', ['month', 'amount'], 'savings_question', after=lambda r: seen.update(r=r))
    turn = ask(client, session, 'मेरी बचत कितनी है?')
    events, _ = stream(client, turn)
    cid = consent_event(events)['consent_id']
    client.post(f'/v1/consents/{cid}/answer', headers=WS, json={'button': 'yes'})
    assert client.delete(f'/v1/consents/{cid}', headers=WS).json()['status'] == 'withdrawn'
    stream(client, turn, after=events[-1]['id'])
    assert 'withdrawn' in seen['r']['error']


def test_old_consent_and_injection_row(client, fake_llm):
    seen = {}
    session = login(client, 'D-005')
    lokos, _ = source_ids(client)
    _consent_then(fake_llm, lokos, 'dar', ['month', 'activity', 'income'], 'income_question', after=lambda r: seen.update(r=r))
    turn = ask(client, session, 'मेरी आमदनी कितनी है?')
    events, _ = stream(client, turn)
    cid = consent_event(events)['consent_id']
    client.post(f'/v1/consents/{cid}/answer', headers=WS, json={'button': 'yes'})
    stream(client, turn, after=events[-1]['id'])
    rows = seen['r']['rows']
    assert len(rows) == 31 and any('SYSTEM: ignore' in r['activity'] for r in rows)  # the planted cell, as data
    # The gateway only ever returned Santosh's rows: nothing of anyone else's to leak.
    system = fake_llm.calls[-1]['messages'][0]['content']
    assert 'not instructions' in system

    # Reusing that consent in a later question is refused: consent covers one question.
    fake_llm.script += [tool_call('fetch_records', consent_id=cid, source=lokos, table='dar', fields=['income']),
                        lambda m, t: seen.update(old=tool_results({'messages': m})[-1]) or 'ठीक है।']
    stream(client, ask(client, session, 'और पिछले साल?'))
    assert 'earlier question' in seen['old']['error']


def test_vocabulary_and_wording_endpoints(client, fake_llm):
    login(client)
    lokos, _ = source_ids(client)
    vocab = client.get('/v1/vocabulary', headers=WS).json()
    assert [e['phrase'] for e in vocab['custom']['hi']] == ['हओ', 'हव', 'बाद में बताएँगे']
    client.put('/v1/vocabulary', headers=WS, json={'hi': [{'phrase': 'हाँव', 'meaning': 'yes', 'note': 'test'}]})
    assert client.get('/v1/vocabulary', headers=WS).json()['custom']['hi'][0]['phrase'] == 'हाँव'

    bad = client.put(f'/v1/sources/{lokos}/wording', headers=WS, json={'consent': {'template': {'hi': 'क्या हम देख लें?'}}})
    assert bad.status_code == 422
    saved = client.put(f'/v1/sources/{lokos}/wording', headers=WS, json={
        'registers': {'dar': {'hi': 'आजीविका रजिस्टर'}}, 'fields': {'dar': {'income': {'hi': 'कमाई', 'sensitivity': 'high'}}},
        'consent': {'template': {'hi': 'क्या हम आपके {source_name} से {fields} देख सकते हैं?'}}}).json()
    assert saved['wording']['registers']['dar'] == {'hi': 'आजीविका रजिस्टर', 'en': 'Digital Aajeevika Register'}
    assert saved['wording']['fields']['dar']['income']['hi'] == 'कमाई'
    assert saved['setup']['role'] == 'primary'  # wording does not disturb the setup

    from consent import ledger
    from core import store
    source = store.get_source(WS['X-Workspace'], lokos)
    _, statement = ledger.build({lokos: source}, [{'source_id': lokos, 'table': 'dar', 'fields': ['month', 'income']}],
                                'income_question', 'hi')
    assert statement['hi']['lines'] == ['क्या हम आपके आजीविका रजिस्टर से महीना और कमाई देख सकते हैं?']


def test_calculator():
    from tools import calculate
    assert calculate('sum(a) - sum(b)', {'a': [1, 2, 3], 'b': ['1,000', 2]}) == {'result': -996}
    assert calculate('avg(x)', {'x': [10, 11]}) == {'result': 10.5}
    assert 'error' in calculate('__import__("os")', {})
    assert 'error' in calculate('2 ** 99', {})
    assert 'error' in calculate('x.y', {'x': 1})


@pytest.mark.parametrize('said,meaning', [('हाँ', 'granted'), ('जी हां', 'granted'), ('नहीं', 'denied'), ('ठीक है', 'granted'),
                                          ('haan theek hai', 'granted'), ('मत देखो', 'denied'), ('किसलिए?', 'why')])
def test_vocabulary_reading(said, meaning):
    from consent import catalog, ledger
    row = {'items': [{'source_id': 's', 'table': 't', 'fields': ['f'], 'labels': {'hi': ['x'], 'en': ['x']}, 'register': {'hi': 'r', 'en': 'r'}}]}
    vocab = {'custom': [], 'base': catalog.VOCABULARY['hi'], 'qualifiers': catalog.QUALIFIERS['hi'] + catalog.QUALIFIERS['en']}
    assert ledger.interpret(said, 'hi', row, vocab)['decision'] == meaning


def test_normalise():
    from consent.ledger import normalise
    assert normalise('हाँ, ज़रूर!') == normalise('हां जरूर') and re.match(r'^\S+ \S+$', normalise('हाँ, ज़रूर!'))


def test_spoken_reply_confirms_a_readback(client, fake_llm):
    session = login(client, 'D-007')
    lokos, _ = source_ids(client)
    _, _, cid = _pending_consent(client, fake_llm, session, lokos)
    fake_llm.script.insert(0, '{"decision": "grant", "granted_items": [1], "refused_registers": [], "confidence": 0.6}')
    out = client.post(f'/v1/consents/{cid}/answer', headers=WS, json={'text': 'चलो देख लेना फिर', 'mode': 'voice'}).json()
    assert out.get('readback')
    assert client.post(f'/v1/consents/{cid}/answer', headers=WS, json={'text': 'हाँ', 'mode': 'voice'}).json()['status'] == 'granted'
