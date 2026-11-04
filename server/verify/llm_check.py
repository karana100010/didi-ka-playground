"""Gate C, step 5: does the answer answer her question? One LLM call, only under verify=code+llm, only for an answer
an LLM wrote (code-worded answers are checked by code), and only when gate C has not already spent its one LLM
call on a rewrite.

Code can check that every figure is real and credited to the right register; it cannot tell that a correct
loan figure was given to a savings question, that a two-part question got half an answer, or that a warm
sentence with no figure dodged what she asked. This check is for those, in the style of Chain-of-Verification
and CRITIC: the verifier sees her question, what was read (the exact totals), and the answer, and returns a
small JSON verdict that code acts on.
"""
import json

from . import Finding

PROMPT = """You check an assistant's answer before a rural woman (an SHG member) hears it. You get her question, the
earlier turn if any, what was read from her records (exact totals, labelled by register), any decision table's
outcome, and the answer. Judge only against these; do not use outside knowledge about her.

- answers_question: true if the answer gives what she asked for (the figure, the yes or no, the explanation), or
  asks one short follow-up when her question is too vague to answer. False if it answers a different question,
  only talks around it, or gives a figure for something she did not ask about instead.
- wrong_claim: a statement about her that the records contradict (a wrong figure, a figure credited to the wrong
  register or period, a wrong outcome), quoted briefly; null if none.
- missing: a part of her question the records could answer but the answer leaves out; null if none.

Reply with JSON only: {"answers_question": true or false, "wrong_claim": null or "<short quote>", "missing": null or "<what>"}"""


def case_text(question, earlier, records, decisions, could_not_read, answer):
    return json.dumps({'her_question': question, 'earlier_turn': earlier, 'records_read': records,
                       'decision_outcomes': decisions, 'could_not_read': could_not_read, 'answer': answer},
                      ensure_ascii=False, default=str)[:7000]


def judge(call, question, earlier, records, decisions, could_not_read, answer):
    """(Finding, raw verdict). `call(messages) -> text` is the LLM; a reply that is not JSON passes (fail open)."""
    text = call([{'role': 'system', 'content': PROMPT},
                 {'role': 'user', 'content': case_text(question, earlier, records, decisions, could_not_read, answer)}])
    try:
        verdict = json.loads(text[text.index('{'):text.rindex('}') + 1])
    except (ValueError, AttributeError):
        return Finding('answers_question', True, 'warn', 'verifier reply was not JSON; not judged'), None
    problems = []
    if verdict.get('answers_question') is False:
        problems.append('does not answer her question')
    if verdict.get('wrong_claim'):
        problems.append(f'wrong: {str(verdict["wrong_claim"])[:160]}')
    if verdict.get('missing'):
        problems.append(f'leaves out: {str(verdict["missing"])[:160]}')
    return Finding('answers_question', not problems, 'repair', '; '.join(problems),
                   ' '.join(problems)), verdict


def for_run(ctx, answer):
    """The step-5 finding for a run (the verifier is the run's own model unless VERIFY_MODEL is set)."""
    import os
    earlier = {'didi': ctx.history[-1][0], 'assistant': ctx.history[-1][1]} if ctx.history else None
    decisions = [{'decision': d['decision_id'], 'outcome': d['outcome'], 'outcome_label': d.get('outcome_label')}
                 for d in ctx.decision_results]
    could_not = [f'{o["table"]}: {o["reason"]}' for o in ctx.obligations]
    model = os.environ.get('VERIFY_MODEL') or None
    finding, _ = judge(lambda messages: ctx.llm(messages, max_tokens=200, temperature=0, model=model)['content'],
                       ctx.question, earlier, ctx.summaries, decisions, could_not, answer)
    return finding
