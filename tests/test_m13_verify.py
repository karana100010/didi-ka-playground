"""M13: the verification harness. Gate A drops registers her question does not ask about, id fields and what the
department's policy forbids, before the consent card; gate C checks an answer's figures, registers, periods,
verdict and what could not be read, and repairs what it can in code. The fault-injection suite holds every check
to its catch rate and false-alarm rate."""
import json
import sys
from pathlib import Path

from conftest import login
from test_m4 import lokos_id, run_turn

ROOT = Path(__file__).resolve().parent.parent


class FakeCtx:
    """Just what gate C reads from a RunContext."""

    def __init__(self, ev_rows=None, decision=None, obligations=(), rewrite=None, fallback=None):
        from agents import facts
        from verify import provenance
        self.question, self.llm_language, self.didi = 'सवाल', 'hi', {'name_local': 'सुनीता देवी', 'name': 'Sunita Devi'}
        self.known, self.facts, self.fetched = set(), {}, {}
        for table, rows in (ev_rows or {}).items():
            totals = {k: v for k, v in facts.summarise(table, rows).items() if k != 'rows'}
            self.known |= facts.numbers(rows) | facts.numbers(totals)
            provenance.merge(self.facts, provenance.from_rows(table, rows, totals))
            self.fetched[table] = rows
        self.decision_results = [decision] if decision else []
        self.obligations, self.worded_by_code, self.code_fallback = list(obligations), False, fallback
        self.metrics, self.run, self.events, self.llm_calls = {}, {'position': 0}, [], []
        self._rewrite, self.prefs = rewrite, {}

    def emit(self, type_, **data):
        self.events.append((type_, data))

    def llm(self, messages, **_):
        self.llm_calls.append(messages)
        return {'content': self._rewrite}


def savings_rows():
    from test_m6 import her_rows
    return her_rows('D-001')['savings']


def dar_rows():
    from test_m6 import her_rows
    return her_rows('D-001')['dar']


def test_a_good_answer_passes_quietly():
    from verify import answer
    ctx = FakeCtx({'savings': savings_rows()})
    text = 'सुनीता जी, आपके बचत रजिस्टर के अनुसार आपकी कुल बचत ₹12,000 है।'
    assert answer.deliver(ctx, text) == text
    assert ctx.events == [] and ctx.metrics['verification']['answer'] == 'passed'


def test_a_register_she_refused_is_said_in_code():
    from verify import answer
    ctx = FakeCtx({'savings': savings_rows()}, obligations=[{'table': 'loans', 'reason': 'refused'}])
    out = answer.deliver(ctx, 'सुनीता जी, आपकी कुल बचत ₹12,000 है।')
    assert out == 'सुनीता जी, आपकी कुल बचत ₹12,000 है। आपका कर्ज़ रजिस्टर मैं नहीं देख पाई।'
    (kind, event), = ctx.events
    assert kind == 'verification' and event['action'] == 'repaired' and event['findings'][0]['name'] == 'obligations'
    assert ctx.llm_calls == []   # repaired by code, no LLM call


def test_an_english_sentence_is_dropped():
    from verify import answer
    ctx = FakeCtx({'savings': savings_rows()})
    out = answer.deliver(ctx, 'सुनीता जी, आपकी कुल बचत ₹12,000 है। Your savings are good, keep saving every month.')
    assert out == 'सुनीता जी, आपकी कुल बचत ₹12,000 है।'


def test_a_wrong_verdict_falls_back_to_the_decision_table():
    import decisions
    from verify import answer
    rows = dar_rows()
    result = decisions.evaluate('lakhpati_status', decisions.inputs_from_rows('lakhpati_status', {'dar': rows}))
    assert result['outcome'] == 'ON_TRACK'
    ctx = FakeCtx({'dar': rows}, decision={'decision_id': 'lakhpati_status', **result})
    out = answer.deliver(ctx, 'सुनीता जी, बधाई हो! नियम के अनुसार आप "लखपति दीदी" हैं।')
    assert '"लखपति बनने की राह पर"' in out and 'बधाई' not in out
    (kind, event), = ctx.events
    assert event['action'] == 'fallback' and event['findings'][0]['name'] == 'verdict'


def test_saying_she_is_not_yet_a_lakhpati_is_not_a_wrong_verdict():
    import decisions
    from verify import answer
    rows = dar_rows()
    result = decisions.evaluate('lakhpati_status', decisions.inputs_from_rows('lakhpati_status', {'dar': rows}))
    ctx = FakeCtx({'dar': rows}, decision={'decision_id': 'lakhpati_status', **result})
    text = ('सुनीता जी, आप अभी लखपति दीदी नहीं बनी हैं, पर लखपति बनने की राह पर हैं। पिछले 12 महीनों में आपकी आमदनी '
            '₹1,24,800 रही।')
    assert answer.deliver(ctx, text) == text and ctx.events == []


def test_a_misattributed_figure_is_rewritten_once_then_falls_back_to_code():
    from verify import answer
    rows = {'savings': savings_rows(), 'dar': dar_rows()}
    wrong = 'सुनीता जी, आपके डिजिटल आजीविका रजिस्टर के अनुसार आपकी कुल बचत ₹12,000 है।'
    right = 'सुनीता जी, आपके बचत रजिस्टर के अनुसार आपकी कुल बचत ₹12,000 है।'
    ctx = FakeCtx(rows, rewrite=right)
    assert answer.deliver(ctx, wrong) == right and len(ctx.llm_calls) == 1
    assert 'savings' in ctx.llm_calls[0][-1]['content'] or 'register' in ctx.llm_calls[0][-1]['content']
    ctx = FakeCtx(rows, rewrite=wrong, fallback=lambda: 'बचत रजिस्टर: कुल बचत ₹12,000।')
    assert answer.deliver(ctx, wrong) == 'बचत रजिस्टर: कुल बचत ₹12,000।'
    assert ctx.events[-1][1]['action'] == 'fallback'


def test_a_year_figure_said_for_this_month_is_flagged():
    from verify import answer
    ctx = FakeCtx({'dar': dar_rows()})
    year = answer.check('सुनीता जी, पिछले 12 महीनों में आपकी आमदनी ₹1,24,800 रही, यानी हर महीने ₹10,400।', answer.Evidence(
        known=ctx.known, facts=ctx.facts, read=True))
    assert all(f.ok for f in year if f.name == 'period')
    month = answer.check('सुनीता जी, इस महीने आपकी आमदनी ₹1,24,800 रही।', answer.Evidence(known=ctx.known, facts=ctx.facts, read=True))
    assert [f.name for f in month if not f.ok] == ['period']


def test_gate_a_drops_a_register_her_question_does_not_name(client, fake_llm):
    session = login(client, 'D-001')
    lokos = lokos_id(client)
    plan = {'needs_records': True, 'purpose': 'income_question', 'scheme_topics': [], 'steps': ['Read'],
            'items': [{'source': lokos, 'table': 'dar', 'fields': ['month', 'income']},
                      {'source': lokos, 'table': 'loans', 'fields': ['outstanding', 'loan_id']}]}
    fake_llm.script += [json.dumps(plan), 'सुनीता जी, पिछले 12 महीनों में आपकी आमदनी ₹1,63,200 रही।']
    events, _ = run_turn(client, session, 'पिछले साल मेरी आमदनी कितनी रही?')
    card = next(e['data'] for e in events if e['type'] == 'consent_request')
    assert [i['table'] for i in card['items']] == ['dar']
    gate = next(e['data'] for e in events if e['type'] == 'verification' and e['data']['stage'] == 'plan')
    assert {f['name'] for f in gate['findings']} == {'id_field', 'unjustified_table'}


def test_the_verify_pref_turns_the_harness_off(client, fake_llm):
    session = login(client, 'D-001', verify='off')
    lokos = lokos_id(client)
    plan = {'needs_records': True, 'purpose': 'income_question', 'scheme_topics': [], 'steps': ['Read'],
            'items': [{'source': lokos, 'table': 'dar', 'fields': ['month', 'income']},
                      {'source': lokos, 'table': 'loans', 'fields': ['outstanding']}]}
    fake_llm.script += [json.dumps(plan), 'ठीक है।']
    events, _ = run_turn(client, session, 'पिछले साल मेरी आमदनी कितनी रही?')
    card = next(e['data'] for e in events if e['type'] == 'consent_request')
    assert [i['table'] for i in card['items']] == ['dar', 'loans'] and not any(e['type'] == 'verification' for e in events)


def test_the_fault_suite_meets_its_targets():
    sys.path.insert(0, str(ROOT / 'scripts'))
    import verify_faults
    report = verify_faults.run()
    for gate, faults in (('gate_c', verify_faults.GATE_C_FAULTS), ('gate_a', verify_faults.GATE_A_FAULTS)):
        r = report[gate]
        assert r['false_alarm_rate'] <= verify_faults.TARGETS['false_alarm'], r
        for fault in faults:
            assert r['recall'][fault]['rate'] >= verify_faults.TARGETS['recall'], (gate, fault, r['recall'][fault])


def test_running_balances_are_never_added_up_and_gate_a_reads_their_month():
    # Live, Gemma planned savings: [cumulative] for "मेरी कुल बचत कितनी है?"; without the month, summarise added
    # twelve monthly balances into ₹1,30,800, the writer said it, and the critic passed it as a known number.
    from agents import facts
    from verify import plan
    rows = [{'cumulative': r['cumulative']} for r in savings_rows()]
    assert 130800 not in facts.numbers(facts.summarise('savings', rows))
    kept, findings = plan.check('मेरी कुल बचत कितनी है?', [{'source_id': 's', 'table': 'savings', 'fields': ['cumulative']}],
                                'savings_question')
    assert kept[0]['fields'] == ['month', 'cumulative'] and [f.name for f in findings if not f.ok] == ['needs_month']
    with_month = facts.summarise('savings', savings_rows())
    assert with_month['latest_month']['cumulative'] == 12000


def llm_ctx(replies, fallback=None, rows=None):
    """A FakeCtx whose LLM answers from `replies` in order, with verify=code+llm."""
    ctx = FakeCtx(rows if rows is not None else {'savings': savings_rows()}, fallback=fallback)
    ctx.prefs = {'verify': 'code+llm'}
    ctx.history, ctx.summaries = [], [{'register': 'Savings register', 'latest_month': {'cumulative': 12000}}]
    script = list(replies)

    def llm(messages, **_):
        ctx.llm_calls.append(messages)
        return {'content': script.pop(0)}
    ctx.llm = llm
    return ctx


def test_the_llm_verifier_sends_a_dodge_to_the_code_answer():
    from verify import answer
    ctx = llm_ctx(['{"answers_question": false, "wrong_claim": null, "missing": "her total savings"}'],
                  fallback=lambda: 'सुनीता जी, बचत रजिस्टर के अनुसार आपकी कुल बचत ₹12,000 है।')
    out = answer.deliver(ctx, 'सुनीता जी, आप ऐसे ही बचत करती रहिए, आपका भविष्य अच्छा होगा।')
    assert out == 'सुनीता जी, बचत रजिस्टर के अनुसार आपकी कुल बचत ₹12,000 है।' and len(ctx.llm_calls) == 1
    (kind, event), = ctx.events
    assert event['action'] == 'fallback' and event['findings'][0]['name'] == 'answers_question'
    sent = json.loads(ctx.llm_calls[0][1]['content'])
    assert sent['her_question'] == 'सवाल' and sent['records_read'][0]['register'] == 'Savings register'


def test_a_good_answer_passes_the_llm_verifier_and_a_bad_reply_fails_open():
    from verify import answer
    good = 'सुनीता जी, आपके बचत रजिस्टर के अनुसार आपकी कुल बचत ₹12,000 है।'
    ctx = llm_ctx(['{"answers_question": true, "wrong_claim": null, "missing": null}'])
    assert answer.deliver(ctx, good) == good and ctx.events == []
    ctx = llm_ctx(['I think it is fine.'])
    assert answer.deliver(ctx, good) == good and ctx.events == []  # not JSON: not judged, never blocks her answer


def test_gate_c_spends_at_most_one_llm_call():
    from verify import answer
    rows = {'savings': savings_rows(), 'dar': dar_rows()}
    wrong = 'सुनीता जी, आपके डिजिटल आजीविका रजिस्टर के अनुसार आपकी कुल बचत ₹12,000 है।'
    right = 'सुनीता जी, आपके बचत रजिस्टर के अनुसार आपकी कुल बचत ₹12,000 है।'
    ctx = llm_ctx([right], rows=rows)
    assert answer.deliver(ctx, wrong) == right and len(ctx.llm_calls) == 1  # the rewrite; no verifier after it


def test_code_worded_answers_are_not_sent_to_the_llm_verifier():
    from verify import answer
    ctx = llm_ctx([])
    ctx.worded_by_code = True
    text = 'सुनीता जी, आपके बचत रजिस्टर के अनुसार आपकी कुल बचत ₹12,000 है।'
    assert answer.deliver(ctx, text) == text and ctx.llm_calls == []


def test_a_tamil_answer_never_gets_a_hindi_sentence_appended():
    # Live (D-008, Lakshmi): her Tamil answer already said loans were not allowed; gate C could not read it and
    # appended "आपका कर्ज़ रजिस्टर मैं नहीं देख पाई।" to it.
    from verify import answer
    ctx = FakeCtx({'savings': savings_rows()}, obligations=[{'table': 'loans', 'reason': 'refused'}])
    ctx.llm_language, ctx.prefs = 'ta', {}
    tamil = 'லட்சுமி ஜி, உங்கள் மொத்த சேமிப்பு ₹12,000. உங்கள் கடன்கள் குறித்த தகவல்களைப் பார்க்க எனக்கு அனுமதி இல்லை.'
    assert answer.deliver(ctx, tamil) == tamil


def test_a_code_worded_answer_reaches_a_tamil_didi_in_tamil(monkeypatch):
    # Live (D-008, Lakshmi, direct mode): the Lakhpati verdict and the savings projection are worded by code in Hindi
    # or English only. The verdict reached her in English, and strip_english emptied the projection to "Sorry".
    import runner
    from bhashini import client as bhashini
    calls = []

    def fake_translate(text, source, target, service_id=None):
        calls.append((source, target, text))
        return {'text': text.replace('By the rule, you are', 'விதிப்படி நீங்கள்')}
    monkeypatch.setattr(bhashini, 'translate', fake_translate)
    ctx = type('Ctx', (), {'llm_language': 'ta'})()
    names = ['Lakshmi', 'லட்சுமி']
    out = runner.in_her_language(ctx, 'லட்சுமி, By the rule, you are "approaching".', names)
    assert out.startswith('லட்சுமி, விதிப்படி நீங்கள்') and calls[0][:2] == ('en', 'ta') and 'லட்சுமி' not in calls[0][2]
    tamil = 'லட்சுமி जी, உங்கள் மொத்த சேமிப்பு ₹12,000.'  # a Tamil answer: not translated, but the Hindi "जी" goes
    assert runner.in_her_language(ctx, tamil, names) == 'லட்சுமி, உங்கள் மொத்த சேமிப்பு ₹12,000.' and len(calls) == 1
    ctx.llm_language = 'hi'
    hindi = 'सुनीता जी, आपकी कुल बचत ₹12,000 है।'
    assert runner.in_her_language(ctx, hindi, ['Sunita']) == hindi and len(calls) == 1


def test_a_hindi_follow_up_reaches_a_tamil_didi_in_tamil(monkeypatch):
    import runner
    from bhashini import client as bhashini
    calls = []
    monkeypatch.setattr(bhashini, 'translate', lambda text, s, t, service_id=None: calls.append((s, t)) or {'text': 'உங்களுக்கு என்ன உதவி வேண்டும்?'})
    ctx = type('Ctx', (), {'llm_language': 'ta'})()
    assert runner.in_her_language(ctx, 'आपको किस तरह की मदद चाहिए?', ['Lakshmi']) == 'உங்களுக்கு என்ன உதவி வேண்டும்?'
    ctx.llm_language = 'mr'  # Marathi is written in Devanagari: left as it is
    assert runner.in_her_language(ctx, 'आपको किस तरह की मदद चाहिए?', ['Lakshmi']) == 'आपको किस तरह की मदद चाहिए?'
    assert calls == [('hi', 'ta')]
