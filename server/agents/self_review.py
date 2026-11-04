"""Self-review (Self-Refine / Reflexion style): Plan-and-Execute answers, then the model critiques its own answer
against what was read and, if it finds a problem, rewrites it. Two more LLM calls when it finds one, one when it
does not. Code-worded answers (a decision, a plan, a projection) are left alone. For the flow comparison
(docs/verification_harness_plan.md §5): LLM review against code review.
"""
import json

from . import plan_execute, prompts
from .context import clean_answer
from .writer import usable

NAME = 'Self-review'

CRITIQUE = """Check your answer to her question against what was read from her records. Reply with OK if it gives
what she asked, every figure matches the records and is said with the right register and period, and anything that
could not be read is mentioned. Otherwise list the problems in one or two short lines (in English).

Her question: {question}
Records read (exact totals): {records}
Decision outcomes: {decisions}
Could not read: {could_not}
Your answer: {answer}"""

REVISE = """Rewrite your answer to her question fixing these problems: {problems}
Use only figures from the records (exact totals): {records}
Keep it to 2 to 5 short spoken sentences in her language. Reply with the answer only."""


def run(ctx):
    answer = plan_execute.run(ctx)
    if ctx.worded_by_code or not answer or not ctx.fetched:
        return answer
    records = json.dumps(ctx.summaries, ensure_ascii=False, default=str)[:4000]
    decisions = [f'{d["decision_id"]}: {d["outcome"]}' for d in ctx.decision_results]
    could_not = [f'{o["table"]} ({o["reason"]})' for o in ctx.obligations]
    review = ctx.llm([{'role': 'user', 'content': CRITIQUE.format(question=ctx.question, records=records, decisions=decisions,
                                                                  could_not=could_not, answer=answer)}],
                     max_tokens=150, temperature=0)['content'] or ''
    if review.strip().upper().startswith('OK'):
        return answer
    ctx.emit('self_review', problems=review.strip()[:300])
    system = prompts.system(ctx.didi, ctx.llm_language)
    revised = clean_answer(ctx.llm([{'role': 'system', 'content': system},
                                    {'role': 'user', 'content': ctx.question},
                                    {'role': 'assistant', 'content': answer},
                                    {'role': 'user', 'content': REVISE.format(problems=review.strip(), records=records)}],
                                   max_tokens=450, temperature=0.2)['content'])
    return revised if usable(ctx, revised) else answer
