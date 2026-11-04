"""Gate C: before the didi hears an answer, check it against what the run actually read and decided.

Checks, cheapest first (all code):
  form           not degenerate, no setup text, no permission-asking after her records were read, not mostly English
  figures        every figure is a number the run read or computed (the critic's rule; the critic still does the
                 rewrite, this records it)
  attribution    a figure in a sentence that names one register must come from that register
  period         a figure said for "this month" must be a month's figure; one said for "the last 12 months" must
                 be a year's figure (sentences that name both are skipped)
  units          a ₹ figure must be money, an acre figure must be land
  verdict        a decision table's outcome is the one said: its label is there, and no other outcome is said as hers
  obligations    a register that could not be read (refused, blocked, no rows) is mentioned

Actions: a wrong verdict falls back to the decision's own code-worded answer; an unmentioned obligation gets one
code-written sentence; attribution, period and unit slips get one LLM rewrite with the findings as feedback, then
the run's code-worded answer if it has one; anything else is delivered with the findings recorded (warn).
"""
import re
from dataclasses import dataclass, field

from . import Finding, emit, failed, provenance

REGISTER_NAMES = {
    'savings': ('बचत रजिस्टर', 'savings register'),
    'loans': ('कर्ज़ रजिस्टर', 'कर्ज रजिस्टर', 'ऋण रजिस्टर', 'लोन रजिस्टर', 'loan register'),
    'dar': ('डिजिटल आजीविका रजिस्टर', 'आजीविका रजिस्टर', 'digital aajeevika register'),
    'enterprise': ('उद्यम रजिस्टर', 'enterprise register'),
    'wages_vbg': ('मज़दूरी रिकॉर्ड', 'मजदूरी रिकॉर्ड', 'मज़दूरी रजिस्टर', 'मजदूरी रजिस्टर', 'wage record'),
    'agristack': ('किसान रिकॉर्ड', 'एग्रीस्टैक', 'कृषि रिकॉर्ड', 'कृषि रजिस्टर', 'farmer record', 'agristack'),
    'meetings': ('बैठक रजिस्टर', 'meeting register'),
}
REGISTER_HI = {'savings': 'बचत रजिस्टर', 'loans': 'कर्ज़ रजिस्टर', 'dar': 'डिजिटल आजीविका रजिस्टर', 'enterprise': 'उद्यम रजिस्टर',
               'wages_vbg': 'मज़दूरी रिकॉर्ड', 'agristack': 'किसान रिकॉर्ड', 'meetings': 'बैठक रजिस्टर'}
THIS_MONTH = ('इस महीने', 'पिछले महीने', 'this month', 'last month')
LAST_YEAR = ('पिछले 12 महीन', 'पिछले बारह महीन', 'पिछले साल', 'साल भर', 'पूरे साल', 'last 12 months', 'last year')
PER_MONTH = ('हर महीने', 'महीने का', 'महीने के', 'प्रति माह', 'औसत', 'a month', 'per month', 'average')
MONTH_PERIODS = {'month', 'latest', 'item', 'current', 'computed'}
YEAR_PERIODS = {'year', 'all', 'cycle', 'current', 'item', 'computed'}
NOT_MONEY = ('land_acres', 'days_worked', 'units', 'units_sold', 'overdue_days', 'tenure_months', 'rate_pct', 'attendance',
             'members', 'cycle_no')
NEGATION = ('नहीं', ' न ', 'not ', "n't", 'अभी तक')
COULD_NOT = ('नहीं देख', 'नहीं पढ़', 'अनुमति नहीं', 'अनुमति न', 'दर्ज नहीं', 'जानकारी नहीं', 'रिकॉर्ड नहीं', 'नहीं मिल',
             'नहीं कर पा', 'जांच नहीं', 'जाँच नहीं',
             'could not', 'no record', 'not allowed', 'permission')
_SENTENCE = re.compile(r'(?<=[।.!?])\s+')
_FIGURE = re.compile(r'(₹\s*)?(?<![\w.])(\d{1,3}(?:,\d{2,3})+|\d+)(?:\.(\d+))?(\s*(?:एकड़|acres?))?')


@dataclass
class Evidence:
    """What a run read and decided, as gate C needs it (built from a RunContext, or by hand in tests)."""
    question: str = ''
    language: str = 'hi'
    known: set = field(default_factory=set)       # every number read or computed (the critic's set)
    facts: dict = field(default_factory=dict)     # number -> {(table, field, period)} (provenance)
    read: bool = False                            # any register was read
    decisions: list = field(default_factory=list)  # [{'decision_id', 'outcome', 'labels': {outcome: [labels]}}]
    obligations: list = field(default_factory=list)  # [{'table', 'reason'}]
    worded_by_code: bool = False

    @classmethod
    def from_ctx(cls, ctx):
        import decisions as tables
        made = []
        for d in ctx.decision_results:
            outcomes = tables.describe(d['decision_id'])['outcomes']
            made.append({'decision_id': d['decision_id'], 'outcome': d['outcome'],
                         'labels': {k: [v['hi'], v['en']] for k, v in outcomes.items()}})
        from agents.critic import SCHEME_NUMBERS
        return cls(question=ctx.question, language=ctx.llm_language, known=set(ctx.known) | SCHEME_NUMBERS,
                   facts=ctx.facts, read=bool(ctx.fetched), decisions=made,
                   obligations=list({o['table']: o for o in reversed(ctx.obligations)}.values()),
                   worded_by_code=ctx.worded_by_code)


def figures(sentence):
    """[(value, rupees, acres)] for the figures in a sentence that the checks look at (100 and above, or acres)."""
    out = []
    from agents.facts import _DEVANAGARI
    for m in _FIGURE.finditer(sentence.translate(_DEVANAGARI)):
        value = float(m.group(2).replace(',', '') + ('.' + m.group(3) if m.group(3) else ''))
        if value >= 100 or m.group(4):
            out.append((value, bool(m.group(1)), bool(m.group(4))))
    return out


def registers_named(sentence):
    low = sentence.lower()
    return {table for table, names in REGISTER_NAMES.items() if any(n.lower() in low for n in names)}


def check(answer, ev):
    """All gate C findings for an answer (passing checks included, so the trace lists what was checked)."""
    findings = [_form(answer, ev), _figures(answer, ev)]
    findings += _attribution_period_units(answer, ev)
    findings += _verdicts(answer, ev)
    findings += _obligations(answer, ev)
    return findings


def _form(answer, ev):
    from agents import writer
    if not answer:
        return Finding('form', False, 'warn', 'empty answer')
    problems = [name for name, bad in (('degenerate', writer.degenerate(answer)), ('setup text', writer.leaks_setup(answer)),
                                       ('asks permission after reading', ev.read and writer.asks_permission(answer)),
                                       ('English sentence', ev.language != 'en' and writer.strip_english(answer) != answer.strip()))
                if bad]
    return Finding('form', not problems, 'warn', ', '.join(problems))


def _figures(answer, ev):
    from agents import facts
    if not ev.read and not ev.known:
        return Finding('figures', True)
    got, total, missing = facts.grounded(answer, ev.known)
    return Finding('figures', not missing, 'repair', f'{got}/{total} figures traced; not read: '
                   + ', '.join(f'{n:,.0f}' for n in missing) if missing else f'{got}/{total} figures traced',
                   'These figures were not in her records: ' + ', '.join(f'{n:,.0f}' for n in missing) if missing else '')


def _attribution_period_units(answer, ev):
    wrong_register, wrong_period, wrong_unit = [], [], []
    for sentence in _SENTENCE.split(answer or ''):
        named = registers_named(sentence)
        low = sentence.lower()
        this_month = any(p in low for p in THIS_MONTH)
        last_year = any(p in low for p in LAST_YEAR)
        per_month = any(p in low for p in PER_MONTH)
        for value, rupees, acres in figures(sentence):
            origins = provenance.lookup(ev.facts, value)
            read_from = {o for o in origins if o[2] != 'computed'}
            if not origins:
                continue  # not a number from her registers (the figures check covers it)
            if len(named) == 1 and read_from and not any(o[0] in named for o in origins):
                wrong_register.append(f'{value:,.0f} is from {"/".join(sorted({o[0] for o in read_from}))}, '
                                      f'the sentence names {next(iter(named))}')
            periods = {o[2] for o in origins}
            if this_month and not last_year and not periods & MONTH_PERIODS:
                wrong_period.append(f'{value:,.0f} said for one month but it is a {"/".join(sorted(periods))} figure')
            elif last_year and not this_month and not per_month and not periods & YEAR_PERIODS:
                wrong_period.append(f'{value:,.0f} said for the last 12 months but it is a {"/".join(sorted(periods))} figure')
            fields = {o[1] for o in origins}
            if rupees and fields and fields <= set(NOT_MONEY):
                wrong_unit.append(f'₹{value:,.0f} but {"/".join(sorted(fields))} is not money')
            if acres and fields and 'land_acres' not in fields:
                wrong_unit.append(f'{value:g} acres but it is {"/".join(sorted(fields))}')
    return [
        Finding('attribution', not wrong_register, 'repair', '; '.join(wrong_register),
                'Each figure must be said with the register it came from: ' + '; '.join(wrong_register)),
        Finding('period', not wrong_period, 'repair', '; '.join(wrong_period),
                'Say each figure for the period it covers: ' + '; '.join(wrong_period)),
        Finding('units', not wrong_unit, 'repair', '; '.join(wrong_unit), 'Use the right unit: ' + '; '.join(wrong_unit)),
    ]


def _verdicts(answer, ev):
    out = []
    sentences = _SENTENCE.split(answer or '')
    for d in ev.decisions:
        mine = d['labels'][d['outcome']]
        said = any(label.lower() in (answer or '').lower() for label in mine)
        wrong = []
        for outcome, labels in d['labels'].items():
            if outcome == d['outcome']:
                continue
            for sentence in sentences:
                low = sentence.lower()
                if any(label.lower() in low for label in labels) and not any(m.lower() in low for m in mine) \
                        and not any(n in f' {low} ' for n in NEGATION):
                    wrong.append(outcome)
        if wrong:
            out.append(Finding('verdict', False, 'block', f'{d["decision_id"]}: said {", ".join(sorted(set(wrong)))}, '
                               f'the table decided {d["outcome"]}'))
        elif not said:
            out.append(Finding('verdict', False, 'repair', f'{d["decision_id"]}: the outcome {d["outcome"]} is not said'))
        else:
            out.append(Finding('verdict', True))
    return out


def _obligations(answer, ev):
    if ev.language not in ('hi', 'en'):
        # Her answer is in a language these phrases cannot read (Tamil, say): appending a Hindi sentence to it would
        # be worse than not checking. Reported, not repaired.
        return [Finding('obligations', True, 'warn', f'not checked in {ev.language}')]
    low = (answer or '').lower()
    missing = [o for o in ev.obligations
               if not any(n.lower() in low for n in REGISTER_NAMES.get(o['table'], ())) and not any(p in low for p in COULD_NOT)]
    return [Finding('obligations', not missing, 'repair', ', '.join(f'{o["table"]} ({o["reason"]})' for o in missing))]


def obligation_sentences(obligations, language):
    """One plain sentence per register that could not be read, for an answer that left it out."""
    out = []
    for o in obligations:
        name = REGISTER_HI.get(o['table'], o['table'])
        if language == 'en':
            out.append(f'I could not check your {o["table"]} records.' if o['reason'] != 'no_rows' else
                       f'Your {o["table"]} records show nothing for you.')
        elif o['reason'] == 'no_rows':
            out.append(f'आपके {name} में आपके नाम से कुछ दर्ज नहीं है।')
        else:
            out.append(f'आपका {name} मैं नहीं देख पाई।')
    return ' '.join(out)


REWRITE = """This answer to her question has mistakes found by checking it against what was read:
{problems}
Rewrite it in the same language, keeping everything that is right and every figure as it is, so that each figure
is said with the register and the period it came from. Reply with the rewritten answer only.

Answer:
{answer}"""


def deliver(ctx, answer):
    """Gate C for a run: check, repair if it can, emit the verdict, and return the answer she will hear."""
    ev = Evidence.from_ctx(ctx)
    findings = check(answer, ev)
    bad = {f.name: f for f in failed(findings)}
    action = 'deliver' if not bad else 'warn'
    if 'verdict' in bad and bad['verdict'].severity == 'block' and ctx.decision_results:
        from agents.plan_execute import decision_answer
        answer, action = decision_answer(ctx, ctx.decision_results[-1]), 'fallback'
        ctx.worded_by_code = True
        bad = {f.name: f for f in failed(check(answer, ev))}
    slips = [bad[n] for n in ('attribution', 'period', 'units') if n in bad]
    spent = False  # gate C makes at most one LLM call: a rewrite, or the step-5 verifier
    if slips and not ev.worded_by_code and not ctx.worded_by_code:
        spent = True
        from agents import prompts
        from agents.context import clean_answer
        problems = '\n'.join(f'- {f.feedback}' for f in slips)
        rewritten = clean_answer(ctx.llm([{'role': 'system', 'content': prompts.system(ctx.didi, ctx.llm_language)},
                                          {'role': 'user', 'content': REWRITE.format(problems=problems, answer=answer)}],
                                         temperature=0)['content'])
        again = {f.name: f for f in failed(check(rewritten, ev))}
        if rewritten and not any(n in again for n in ('attribution', 'period', 'units', 'figures')):
            answer, action, bad = rewritten, 'repaired', again
        elif ctx.code_fallback:
            answer, action = ctx.code_fallback(), 'fallback'
            bad = {f.name: f for f in failed(check(answer, ev))}
    if ctx.prefs.get('verify') == 'code+llm' and not spent and not ctx.worded_by_code and answer:
        from . import llm_check
        finding = llm_check.for_run(ctx, answer)
        findings.append(finding)
        if not finding.ok:
            bad['answers_question'] = finding
            if ctx.code_fallback:
                answer, action = ctx.code_fallback(), 'fallback'
                bad.pop('answers_question')
    if 'form' in bad and 'English' in bad['form'].detail:
        from agents.writer import strip_english
        answer, action = strip_english(answer), 'repaired'  # she hears only her language
    if 'obligations' in bad:
        missing = [o for o in ev.obligations if o['table'] in bad['obligations'].detail]
        answer = f'{answer} {obligation_sentences(missing, ctx.llm_language)}'.strip()
        action = 'repaired' if action == 'deliver' or action == 'warn' else action
        bad.pop('obligations')
    if bad and action == 'deliver':
        action = 'warn'
    emit(ctx, 'answer', findings, action)
    return answer
