"""Hybrid: Plan-and-Execute, and ReAct only when planning fails (no valid plan after the retry), instead of answering
without her records. ReAct can then adapt step by step where one plan could not be written. For the flow comparison:
does adapting only when needed beat either alone?
"""
from . import plan_execute, react

NAME = 'Hybrid (Plan-and-Execute, then ReAct)'


def run(ctx):
    def adapt(ctx):
        ctx.emit('status', transient=True, hi='', en='No valid plan: ReAct takes over')
        return react.run(ctx)
    return plan_execute.run(ctx, on_no_plan=adapt)
