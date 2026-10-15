"""Direct: one LLM call with only her question and the conversation so far. The baseline that shows what a
model says without her consented data."""
from . import prompts
from .context import clean_answer

NAME = 'Direct (no tools)'


def run(ctx):
    messages = [{'role': 'system', 'content': prompts.system(ctx.didi, ctx.llm_language, prompts.DIRECT_RULES)}]
    for question, answer in ctx.history:
        messages += [{'role': 'user', 'content': question}, {'role': 'assistant', 'content': answer}]
    messages.append({'role': 'user', 'content': ctx.question})
    return clean_answer(ctx.llm(messages)['content'])
