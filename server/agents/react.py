"""ReAct: Thought → Action → Observation as plain text, parsed by us, until a Final Answer.

It works with any model, including ones without native tool calling. It asks consent as it goes, so a
question that needs two registers usually means two consent cards.
"""
import json
import re

import tools

from . import prompts
from .context import clean_answer

NAME = 'ReAct'
MAX_STEPS = 8
FORMAT = """
Work in steps. Write exactly one step, then stop and wait for the Observation:
Thought: what you will do next, and why
Action: one of request_consent, fetch_records, calculate, scheme_info, describe_decision, decide
Action Input: the arguments as JSON on one line

Arguments:
- request_consent: {"purpose": "<one of: %s>", "items": [{"source": "<source id>", "table": "<table>", "fields": ["<field>", ...]}]}
- fetch_records: {"consent_id": "<id>", "source": "<source id>", "table": "<table>", "fields": ["<field>", ...]}
- calculate: {"expression": "sum(income) - sum(expense)", "variables": {"income": [..], "expense": [..]}}
- scheme_info: {"topic": "<one of: %s>"}
- describe_decision: {"decision_id": "<lakhpati_status or bank_linkage_readiness>"}
- decide: {"decision_id": "<id>", "consent_id": "<id>"}

Before reading a table, ask her permission for the fields you need from it.
When you know the answer, write:
Thought: I can answer now
Final Answer: your answer to her"""


def run(ctx):
    from consent import catalog
    rules = prompts.tool_rules(tools.schema_text(ctx)) + FORMAT % (', '.join(catalog.PURPOSES), ', '.join(catalog.SCHEME_INFO))
    messages = [{'role': 'system', 'content': prompts.system(ctx.didi, ctx.llm_language, rules)}]
    for question, answer in ctx.history:
        messages += [{'role': 'user', 'content': question}, {'role': 'assistant', 'content': f'Final Answer: {answer}'}]
    messages.append({'role': 'user', 'content': ctx.question})
    retries = 0
    for _ in range(MAX_STEPS):
        text = ctx.llm(messages, stop=['Observation:'])['content'] or ''
        thought = _field(text, 'Thought')
        if thought:
            ctx.emit('thought', text=thought[:400])
        final = re.search(r'Final Answer\s*:\s*(.*)', text, re.S)
        if final:
            return clean_answer(final.group(1))
        action = re.search(r'Action\s*:\s*([A-Za-z_]+)', text)
        start = text.find('{', text.find('Action Input')) if 'Action Input' in text else -1
        raw = _first_json(text, start) if start >= 0 else None
        if not action or raw is None:
            retries += 1
            if retries > 2:
                break
            messages += [{'role': 'assistant', 'content': text},
                         {'role': 'user', 'content': 'Observation: I could not read that. Reply with Thought, Action and Action Input, or with Final Answer.'}]
            continue
        try:
            result = tools.call(ctx, action.group(1), json.loads(raw))
        except ValueError:
            result = {'error': 'Action Input was not valid JSON.'}
        step_text = text[:text.find(raw) + len(raw)]
        messages += [{'role': 'assistant', 'content': step_text},
                     {'role': 'user', 'content': 'Observation: ' + json.dumps(result, ensure_ascii=False, default=str)}]
    messages.append({'role': 'user', 'content': 'Write the Final Answer now.'})
    text = ctx.llm(messages)['content'] or ''
    final = re.search(r'Final Answer\s*:\s*(.*)', text, re.S)
    return clean_answer(final.group(1) if final else text)


def _field(text, name):
    match = re.search(rf'{name}\s*:\s*(.*?)(?:\n\s*(?:Action|Final Answer)\s*:|$)', text, re.S)
    return match.group(1).strip() if match else ''


def _first_json(text, start):
    """The balanced {...} starting at `start`, or None."""
    depth, quoted, escaped = 0, False, False
    for i in range(start, len(text)):
        ch = text[i]
        if quoted:
            if escaped:
                escaped = False
            elif ch == '\\':
                escaped = True
            elif ch == '"':
                quoted = False
            continue
        if ch == '"':
            quoted = True
        elif ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None
