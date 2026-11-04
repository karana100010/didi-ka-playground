"""M9: business plans stay sane: inputs come from her registers or her own words (never numbers the planner made
up), a loss or a shortfall is said plainly, a new business is not "₹0 profit now", and the critic never garbles a
plan's figures."""
import json

from conftest import login
from test_m4 import run_turn
from test_m6 import her_rows


def build_turn(client, fake_llm, didi, kind, question, changes=None):
    session = login(client, didi, critic=True)
    plan = {'needs_records': True, 'business_plan': kind, 'plan_changes': changes or {}, 'purpose': 'business_plan',
            'items': [], 'scheme_topics': [], 'steps': ['Build the plan']}
    fake_llm.script += [json.dumps(plan)]
    events, end = run_turn(client, session, question)
    file = next(e['data'] for e in events if e['type'] == 'file')
    return events, end['runs'][0]['result'], file


def test_her_numbers_are_heard_in_words_and_digits():
    from plans.service import said_numbers
    assert said_numbers('मुझे दो गाय और लेनी हैं') == {2.0}
    assert said_numbers('५० हज़ार का माल, 20% बिक्री बढ़ेगी') == {50.0, 50000.0, 20.0}
    assert said_numbers('डेढ़ लाख') == {1.5, 150000.0}
    assert said_numbers('मुझे सिलाई का बिज़नेस प्लान बनाना है') == set()


def test_made_up_changes_are_refused():
    from plans import service, templates
    assumptions = templates.derive('kirana', her_rows('D-004'))
    refused = service.apply_changes('kirana', assumptions, {'monthly_sales': 5000, 'interest_rate': 7, 'sales_increase': 0.2,
                                                            'stock_investment': 50000}, 'मुझे 50 हज़ार का नया माल लाना है')
    values = {k: v['value'] for k, v in assumptions.items()}
    assert (values['monthly_sales'], values['interest_rate'], values['sales_increase']) == (30000, 12, 20)  # her register
    assert values['stock_investment'] == 50000 and assumptions['stock_investment']['source'] == 'Her words, through the agent'
    assert len(refused) == 3 and all('she did not say' in r for r in refused)


def test_kirana_shortfall_is_said_plainly_and_not_garbled(client, fake_llm):
    # What nemotron planned live for Phoolmati: numbers she never said, replacing her ₹30,000 sales register.
    made_up = {'monthly_sales': 5000, 'sales_increase': 0.2, 'extra_running_cost': 500, 'stock_investment': 10000,
               'own_contribution': 2000, 'interest_rate': 7, 'tenure_months': 24, 'household_income_now': 15000}
    events, result, file = build_turn(client, fake_llm, 'D-004', 'kirana', 'मुझे किराना दुकान का बिज़नेस प्लान चाहिए', made_up)
    r = file['results']
    assert (r['revenue_now'], r['profit_now'], r['investment']) == (30000, 8100, 40000)
    assert r['extra_after_emi'] < 0
    answer = result['answer']
    assert '₹-' not in answer and 'ज़्यादा बचेंगे' not in answer
    assert f'हर महीने ₹{abs(r["extra_after_emi"])} कम पड़ेंगे' in answer and 'वापस आ जाएगी' not in answer
    critic = next(e['data'] for e in events if e['type'] == 'critic')
    assert critic['missing'] == [] and not critic['rewrote'] and not critic.get('withheld')
    assert len(fake_llm.calls) == 1   # no critic rewrite


def test_a_new_dairy_is_not_called_zero_profit_now(client, fake_llm):
    _, result, file = build_turn(client, fake_llm, 'D-002', 'dairy', 'मुझे दो गाय लेकर डेयरी शुरू करनी है',
                                 {'cows_to_add': 2, 'cows_now': 1})
    answer = result['answer']
    assert answer.startswith('रेखा जी, आपके डेयरी का प्लान तैयार है। अभी आपका यह काम नहीं है; 2 गाय से डेयरी शुरू करने पर')
    assert 'मुनाफ़ा ₹0' not in answer and file['results']['profit_now'] == 0   # cows_now=1 was refused: she has none


def test_new_kirana_shop_has_no_sales_now():
    from plans import builder, templates
    assumptions = templates.derive('kirana', her_rows('D-002'))
    assert assumptions['shop_now']['value'] == 0
    r = builder.compute('kirana', {k: v['value'] for k, v in assumptions.items()})
    assert r['revenue_now'] == 0 and r['profit_now'] == 0 and r['revenue_after'] == 20000


def test_tailoring_material_cost_comes_from_her_register():
    from plans import builder, templates
    assumptions = templates.derive('tailoring', her_rows('D-002'))
    assert assumptions['material_per_garment']['value'] == 64   # (₹5,000 − 3 × ₹250 power) / 66 garments
    r = builder.compute('tailoring', {k: v['value'] for k, v in assumptions.items()})
    assert round(r['cost_now']) == 4974 and round(r['profit_now']) == 11526   # her register: ₹16,500 − ₹5,000


def test_plan_keeps_half_her_savings():
    from plans import templates
    assert templates.derive('dairy', her_rows('D-005'))['own_contribution']['value'] == 2700   # of ₹5,400
    assert templates.derive('dairy', her_rows('D-008'))['own_contribution']['value'] == 10000  # of ₹20,500: the cap


def test_negative_results_are_known_by_size():
    from agents import facts
    assert facts.numbers({'extra_after_emi': -855}) == {855.0}


def test_a_plan_is_built_even_if_the_planner_lists_items_it_may_not_read(client, fake_llm):
    # Live, nemotron set business_plan and also listed key fields and other tables; the plan was dropped.
    session = login(client, 'D-004')
    plan = {'needs_records': True, 'business_plan': 'kirana', 'plan_changes': {}, 'purpose': 'enterprise_question',
            'items': [{'source': 'src_nope', 'table': 'loans', 'fields': ['loan_id', 'rate_pct']}], 'steps': []}
    fake_llm.script += [json.dumps(plan)]
    events, end = run_turn(client, session, 'मुझे किराना दुकान का बिज़नेस प्लान चाहिए')
    assert any(e['type'] == 'file' for e in events) and len(fake_llm.calls) == 1
    card = next(e['data'] for e in events if e['type'] == 'consent_request')
    assert [i['table'] for i in card['items']] == ['dar', 'enterprise', 'savings']
