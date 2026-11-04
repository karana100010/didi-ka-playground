"""Plan reviewer: Plan-and-Execute with a second LLM call that reviews the plan before any consent card, and may
return a corrected plan (fewer registers, a decision table, a follow-up). The corrected plan goes through the same
checks (`plan_execute._check`, gate A); an invalid one is ignored. For the flow comparison: LLM plan review
against gate A's code rules.
"""
import json

from . import plan_execute

NAME = 'Plan reviewer'

REVIEW = """
You are reviewing the plan below before the didi is asked for permission. Check that it reads only what her question
needs (no register "just in case", no ids), uses a decision table or business plan when she asks for one, and asks a
follow-up only when her question is too vague. If the plan is right, reply with it unchanged; otherwise reply with the
corrected plan. Reply with the JSON plan only.

Her question: {question}
Proposed plan: {plan}"""


def review(ctx, plan, planner):
    shown = {**plan, 'items': [{'source': i['source_id'], 'table': i['table'], 'fields': i['fields']} for i in plan['items']]}
    text = ctx.llm([{'role': 'system', 'content': planner},
                    {'role': 'user', 'content': REVIEW.format(question=ctx.question,
                                                              plan=json.dumps(shown, ensure_ascii=False))}],
                   max_tokens=600, temperature=0)['content']
    revised, problem = plan_execute._parse(ctx, text)
    if not revised:
        return plan
    before = sorted((i['table'], tuple(i['fields'])) for i in plan['items'])
    after = sorted((i['table'], tuple(i['fields'])) for i in revised['items'])
    if before != after or revised.get('decision') != plan.get('decision') or revised.get('follow_up') != plan.get('follow_up'):
        ctx.emit('plan_review', before=[t for t, _ in before], after=[t for t, _ in after],
                 decision=revised.get('decision'), follow_up=bool(revised.get('follow_up')))
    return revised


def run(ctx):
    return plan_execute.run(ctx, review=review)
