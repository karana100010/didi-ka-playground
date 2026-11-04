"""M13: business earnings projections. "How much could I earn from a new business?" is not a savings question: no
register is read and no consent asked, she is asked once for the expected sales and costs (never about savings), and
code computes the figures, the chart and the answer from what she says."""
import json

from conftest import login
from test_m4 import run_turn

ASK = 'क्या आप मुझे ग्राफ के जरिए समझा सकते हैं कि अगर मैं एक नया व्यवसाय शुरू करूँ तो मेरे 1 साल में कितने पैसे कमा सकती हूँ'
REPLY = 'हर महीने 8000 की बिक्री होगी और 3000 खर्च'
PLAN = {'projection': 'earnings', 'needs_records': False, 'items': [], 'purpose': 'other_question', 'steps': ['Earnings']}


def params(**p):
    return {'follow_up': None, 'business': None, 'monthly_income': None, 'monthly_expense': 0, 'monthly_profit': None,
            'investment': 0, 'months': 12, **p}


def test_sales_and_costs_for_a_year():
    from agents import earnings
    s = earnings.scenario(earnings.clean_params(params(monthly_income=8000, monthly_expense=3000)), 'एक साल में')
    r = earnings.project(s, '2026-10')
    assert r['x'][0] == '2026-11' and len(r['x']) == 12
    assert r['earned'][:3] == [5000, 10000, 15000] and r['total_earned'] == 60000 and r['total_income'] == 96000
    assert earnings.code_answer('सुनीता देवी', s, r) == ('सुनीता जी, अगर खर्च के बाद हर महीने ₹5,000 बचते हैं, '
                                                          'तो 12 महीने में आपकी कमाई ₹60,000 होगी।')


def test_start_up_money_is_taken_out_and_paid_back():
    from agents import earnings
    s = earnings.scenario(earnings.clean_params(params(monthly_profit=4000, investment=10000, months=6)), '6 महीने में')
    r = earnings.project(s, '2026-10')
    assert r['earned'] == [-6000, -2000, 2000, 6000, 10000, 14000] and r['payback_months'] == 3
    c = earnings.chart(s, r)
    assert [x['name'] for x in c['spec']['series']] == ['खर्च के बाद कमाई']  # no sales line when she gave only profit
    assert 'शुरू करने में ₹10,000 लगे, जो कमाई से घटाए' in c['assumptions']
    assert 'लगभग 3 महीने में वापस' in earnings.code_answer('', s, r)


def test_no_figures_means_one_question_about_sales_not_savings():
    from agents import earnings
    assert earnings.scenario(earnings.clean_params(params(follow_up='आप हर महीने कितना बचाएँगी?')), ASK) is None
    assert 'बच' not in earnings.ASK_HI and 'बिक्री' in earnings.ASK_HI and 'खर्च' in earnings.ASK_HI


def test_new_business_question_asks_for_sales_then_charts_earnings(client, fake_llm):
    session = login(client, 'D-001')
    savings_follow_up = {'follow_up': 'आप हर महीने व्यवसाय से कितने रुपये बचाने की सोच रही हैं?', 'monthly_income': None}
    fake_llm.script += [json.dumps(PLAN), json.dumps(savings_follow_up)]
    events, end = run_turn(client, session, ASK)
    types = [e['type'] for e in events]
    assert 'consent_request' not in types and 'chart' not in types
    assert end['runs'][0]['result']['answer'] == 'आप इस व्यवसाय से हर महीने लगभग कितनी बिक्री और कितने खर्च का अंदाज़ा लगा रही हैं?'

    seen = {}

    def extract(messages, tools):
        seen['messages'] = messages
        return json.dumps({'follow_up': None, 'monthly_income': 8000, 'monthly_expense': 3000, 'investment': 0, 'months': 12})
    fake_llm.script += [json.dumps(PLAN), extract]
    events, end = run_turn(client, session, REPLY)
    types = [e['type'] for e in events]
    assert 'consent_request' not in types and types.index('chart') < types.index('answer')
    asked, earlier, said = seen['messages'][1:]  # her question and our follow-up come with her answer
    assert (asked['content'], said['content']) == (ASK, REPLY) and 'बिक्री' in earlier['content']
    call = next(e['data'] for e in events if e['type'] == 'tool_call' and e['data']['tool'] == 'project_earnings')
    assert call['args']['monthly_profit'] == 5000
    chart = next(e['data'] for e in events if e['type'] == 'chart')
    assert chart['kind'] == 'earnings_projection' and chart['title'] == '12 महीने में व्यवसाय से कमाई'
    names = [s['name'] for s in chart['spec']['series']]
    assert names == ['कुल बिक्री', 'खर्च के बाद कमाई'] and not any('बचत' in n for n in names)
    assert chart['figures']['projected_new'] == 60000
    assert end['runs'][0]['result']['answer'] == ('सुनीता जी, अगर खर्च के बाद हर महीने ₹5,000 बचते हैं, '
                                                  'तो 12 महीने में आपकी कमाई ₹60,000 होगी।')  # "1 साल" was in her question


def test_savings_projection_still_needs_a_true_flag():
    from agents import plan_execute
    assert 'earnings' in plan_execute.PLANNER and 'not a savings question' in plan_execute.PLANNER
