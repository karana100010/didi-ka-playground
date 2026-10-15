"""Native tool calling: the model's own `tools` API in a loop until it answers without calling a tool."""
import json

import tools

from . import prompts
from .context import clean_answer

NAME = 'Native tool calling'
MAX_STEPS = 8


def run(ctx):
    messages = [{'role': 'system', 'content': prompts.system(ctx.didi, ctx.llm_language, prompts.tool_rules(tools.schema_text(ctx)))}]
    for question, answer in ctx.history:
        messages += [{'role': 'user', 'content': question}, {'role': 'assistant', 'content': answer}]
    messages.append({'role': 'user', 'content': ctx.question})
    nudged = False
    for _ in range(MAX_STEPS):
        message = ctx.llm(messages, tools=tools.SPECS)
        calls = message.get('tool_calls') or []
        if not calls and not nudged and _asks_permission_in_text(message['content'], messages):
            nudged = True  # small models sometimes ask in words; the app needs the tool call to show her the card
            messages += [{'role': 'assistant', 'content': message['content'] or ''},
                         {'role': 'user', 'content': 'Do not ask for permission in text. Call request_consent now; the app shows her the card.'}]
            continue
        if not calls:
            return clean_answer(message['content'])
        messages.append({'role': 'assistant', 'content': message.get('content') or '', 'tool_calls': calls})
        for call in calls:
            try:
                args = json.loads(call['function']['arguments'] or '{}')
            except ValueError:
                result = {'error': 'The arguments were not valid JSON.'}
            else:
                result = tools.call(ctx, call['function']['name'], args)
            messages.append(tools.tool_message(call['id'], result))
    messages.append({'role': 'user', 'content': 'Answer her now, without calling more tools.'})
    return clean_answer(ctx.llm(messages)['content'])


def _asks_permission_in_text(text, messages):
    asked_before = any(m.get('tool_calls') for m in messages if m['role'] == 'assistant')
    return not asked_before and any(w in (text or '').lower() for w in ('अनुमति', 'सहमति', 'permission', 'consent'))
