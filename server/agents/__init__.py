"""Agent types. Each module has NAME and run(ctx) -> answer text. See docs/design.html §5.

The default is Plan-and-Execute: one consent card per question and exact figures from code. ReAct is the
fallback for models without native tool calling. Direct is the no-records baseline for the compare view.
"""
from . import direct, native_tools, plan_execute, react, rewoo

AGENTS = {'plan_execute': plan_execute, 'native_tools': native_tools, 'react': react, 'rewoo': rewoo, 'direct': direct}
