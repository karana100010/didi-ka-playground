"""M10: savings projections. The planner routes the question, she is asked once for her savings register only, one
LLM call extracts the scenario, and code computes the figures, the chart (sent before the answer) and the answer."""
import json

from conftest import login
from test_m4 import lokos_id, run_turn
from test_m6 import her_rows

YEAR = 'हर महीने 100 और जमा करूँ तो एक साल बाद कितनी बचत होगी?'


def d001():
    from agents import projection
    return projection.her_facts(her_rows('D-001')['savings'], 'सुनीता देवी')


def params(**p):
    return {'follow_up': None, 'new_deposit': None, 'months': None, 'target': None, 'annual_rate': 0, 'lump_sum': 0, **p}


def test_her_facts_come_from_her_savings_register():
    f = d001()
    assert (f['balance'], f['monthly_deposit'], f['last_month'], f['months_with_no_deposit']) == (12000, 200, '2026-09', 0)


def test_hundred_more_for_three_months():
    from agents import projection
    f = d001()
    s = projection.scenario(f, params(new_deposit=300, months=3), 'हर महीने 100 और जमा करूँ तो 3 महीने बाद?')
    r = projection.project(f, s)
    assert r['x'] == ['2026-10', '2026-11', '2026-12']
    assert r['now'] == [12200, 12400, 12600] and r['new'] == [12300, 12600, 12900]
    assert (r['projected_new'], r['projected_now'], r['added']) == (12900, 12600, 300)
    answer = projection.code_answer(f, s, r)
    assert answer == ('सुनीता जी, हर महीने ₹300 जमा करने पर 3 महीने बाद आपकी बचत ₹12,900 होगी। '
                      'अभी की तरह ₹200 जमा करने पर ₹12,600 होती, यानी ₹300 ज़्यादा जुड़ेंगे।')


def test_target_runs_until_she_reaches_it():
    from agents import projection
    f = d001()
    s = projection.scenario(f, params(new_deposit=300, months=None, target=13000), 'हर महीने 100 और डालूँ तो 13000 कब होंगे?')
    r = projection.project(f, s)
    assert (r['months'], r['new'], r['now'][-1], r['reached']) == (4, [12300, 12600, 12900, 13200], 12800, True)
    assert 'आपकी बचत 4 महीने में ₹13,000 तक पहुँचेगी: तब ₹13,200 होगी।' in projection.code_answer(f, s, r)


def test_interest_and_a_lump_sum_compound_in_code():
    from agents import projection
    f = d001()
    r = projection.project(f, projection.scenario(f, params(new_deposit=200, months=2, annual_rate=12, lump_sum=1000), '2 महीने'))
    assert r['new'] == [round(13000 * 1.01 + 200), round(round(13000 * 1.01 + 200) * 1.01 + 200)] == [13330, 13663]


def test_no_period_defaults_to_a_year_and_says_so():
    from agents import projection
    f = d001()
    s = projection.scenario(f, params(new_deposit=300, months=12), 'हर महीने 100 और बचाऊँ तो कितनी हो जाएगी?')
    assert s['months'] == 12 and s['months_defaulted']
    assert projection.code_answer(f, s, projection.project(f, s)).startswith('सुनीता जी, आपने समय नहीं बताया, इसलिए एक साल (12 महीने)')
    assert not projection.scenario(f, params(new_deposit=300, months=12), YEAR)['months_defaulted']


def test_guard_drops_a_needless_follow_up():
    from agents import projection
    f = d001()
    asked = 'आप हर महीने कितना और जमा करेंगी?'
    assert projection.guard(f, params(follow_up=asked, new_deposit=300, months=12))['follow_up'] is None
    assert projection.guard(f, params(follow_up=asked, new_deposit=200, months=12))['follow_up'] == asked  # her deposit: no change
    assert projection.guard(f, params(follow_up=asked, new_deposit=300))['follow_up'] == asked  # no period or target


def projection_turn(client, fake_llm, extract, question=YEAR, extra_items=()):
    session = login(client, 'D-001')
    lokos = lokos_id(client)
    plan = {'needs_records': True, 'projection': True, 'purpose': 'other_question', 'decision': None, 'business_plan': None,
            'items': [{'source': lokos, 'table': 'savings', 'fields': ['month', 'amount']}, *extra_items],
            'scheme_topics': [], 'steps': ['Read her savings', 'Project them']}
    seen = {}

    def extractor(messages, tools):
        seen['messages'] = messages
        return json.dumps(extract, ensure_ascii=False)
    fake_llm.script += [json.dumps(plan), extractor]
    events, end = run_turn(client, session, question)
    return events, end, seen


def test_projection_turn_asks_once_and_charts_before_answering(client, fake_llm):
    extract = {'follow_up': None, 'new_deposit': 300, 'months': 12, 'target': None, 'annual_rate': 0, 'lump_sum': 0}
    events, end, seen = projection_turn(client, fake_llm, extract,
                                        extra_items=[{'source': 'lokos', 'table': 'dar', 'fields': ['income']}])
    types = [e['type'] for e in events]
    consents = [e['data'] for e in events if e['type'] == 'consent_request']
    assert len(consents) == 1
    plan = next(e['data'] for e in events if e['type'] == 'plan')
    assert plan['items'] == [{'table': 'savings', 'fields': ['month', 'amount', 'cumulative']}]
    assert plan['purpose'] == 'savings_question'
    system, user = seen['messages'][0]['content'], seen['messages'][-1]
    assert system.endswith('Her savings register:\nनाम: सुनीता देवी\nबचत रजिस्टर: 2026-09 तक कुल बचत ₹12,000; हर महीने ₹200 जमा '
                           '(पिछले 12 में से 12 महीने)।\nअगला महीना: 2026-10')
    assert system.startswith("You turn a didi's question about how her SHG") and user == {'role': 'user', 'content': YEAR}
    call = next(e['data'] for e in events if e['type'] == 'tool_call' and e['data']['tool'] == 'project_savings')
    assert call['args'] == {'kind': 'savings_projection', 'new_deposit': 300, 'months': 12, 'target': None, 'annual_rate': 0,
                            'lump_sum': 0}
    assert types.index('chart') < types.index('answer')
    chart = next(e['data'] for e in events if e['type'] == 'chart')
    months = [f'2026-{m}' for m in ('10', '11', '12')] + [f'2027-{m:02d}' for m in range(1, 10)]
    assert chart['kind'] == 'savings_projection' and chart['title'] == '12 महीने में बचत' and chart['position'] == 0
    assert chart['spec'] == {'type': 'line', 'x': months, 'y_label': '₹',
                             'series': [{'name': 'अभी की बचत से', 'values': [12000 + 200 * k for k in range(1, 13)]},
                                        {'name': 'बढ़ी हुई बचत से', 'values': [12000 + 300 * k for k in range(1, 13)]}]}
    assert chart['figures'] == {'projected_now': 14400, 'projected_new': 15600, 'added': 1200, 'months': 12, 'target': None,
                                'current_deposit': 200, 'new_deposit': 300, 'annual_rate': 0, 'lump_sum': 0}
    assert 'अगर हर महीने जमा करती रहें' in chart['assumptions'] and 'कोई ब्याज नहीं जोड़ा' in chart['assumptions']
    answer = end['runs'][0]['result']['answer']
    assert answer == ('सुनीता जी, हर महीने ₹300 जमा करने पर 12 महीने बाद आपकी बचत ₹15,600 होगी। '
                      'अभी की तरह ₹200 जमा करने पर ₹14,400 होती, यानी ₹1,200 ज़्यादा जुड़ेंगे।')
    assert len(fake_llm.calls) == 2   # planner and extractor; no writer


def test_a_follow_up_is_asked_with_no_chart(client, fake_llm):
    asked = 'आप हर महीने कितने रुपये और जमा करना चाहेंगी?'
    extract = {'follow_up': asked, 'new_deposit': 200, 'months': 12, 'target': None, 'annual_rate': 0, 'lump_sum': 0}
    events, end, _ = projection_turn(client, fake_llm, extract, question='बचत बढ़ाऊँ तो क्या फ़ायदा होगा?')
    types = [e['type'] for e in events]
    assert 'chart' not in types
    assert not any(e['type'] == 'tool_call' and e['data']['tool'] == 'project_savings' for e in events)
    assert end['runs'][0]['result']['answer'] == asked


def test_keeping_her_deposit_tells_no_difference():
    from agents import projection
    f = d001()
    s = projection.scenario(f, params(new_deposit=200, target=15000), 'मेरी बचत 15000 तक कब पहुँचेगी?')
    assert projection.code_answer(f, s, projection.project(f, s)) == \
        'सुनीता जी, हर महीने ₹200 जमा करने पर आपकी बचत 15 महीने में ₹15,000 तक पहुँचेगी।'


def test_scripts_and_styles_are_revalidated_so_the_chart_card_gets_its_css(client):
    for path in ('/app.css', '/chat.js'):
        first = client.get(path)
        assert first.status_code == 200 and first.headers['cache-control'] == 'no-cache'
        again = client.get(path, headers={'If-None-Match': first.headers['etag']})
        assert again.status_code == 304


def test_an_amount_she_named_is_never_asked_again_and_more_is_not_a_target():
    from agents import projection
    q = 'मैं चाहता हूँ कि आप मुझे बताएं कि अगर मैं 10000 पे हर महीने ज़्यादा बचाऊँ तो मेरे पास कितने पैसे होंगे'
    p = projection.guard(d001(), params(follow_up='आप हर महीने कितने रुपये ज़्यादा जमा करना चाहती हैं?', new_deposit=300,
                                        months=12, target=10000), q)
    assert (p['follow_up'], p['new_deposit'], p['target'], p['months']) == (None, 10200, None, 12)


def test_the_guard_keeps_targets_lump_sums_and_a_real_follow_up():
    from agents import projection
    f = d001()
    assert projection.guard(f, params(new_deposit=200, target=18000), 'मेरी बचत 18000 तक कब पहुँचेगी?')['target'] == 18000
    p = projection.guard(f, params(new_deposit=300, target=13000), 'हर महीने 100 और डालूँ तो 13000 कब होंगे?')
    assert (p['new_deposit'], p['target']) == (300, 13000)
    p = projection.guard(f, params(new_deposit=200, months=3, lump_sum=5000), 'अगर मैं एक बार में 5000 जमा कर दूँ और 3 महीने बाद?')
    assert (p['new_deposit'], p['lump_sum']) == (200, 5000)
    assert projection.guard(f, params(follow_up='कितना?', new_deposit=200, months=12), 'थोड़ा ज़्यादा बचाऊँ तो?')['follow_up']
    assert projection.said_amounts('3 महीने बाद 12% ब्याज पर 500 रुपये') == [500]
