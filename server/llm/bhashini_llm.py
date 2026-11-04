"""Bhashini's hosted LLMs (Dhruva inference API), e.g. Google Gemma 4 26B served by Bhashini.

Same call shape as nvidia.chat, so the agents need no change: model ids start with "bhashini/" and nvidia.chat
hands them here. The API returns text only: no native tool calls (the Native tool calling agent cannot use these
models; the others plan in JSON or text) and no token counts. A stop sequence is applied to the text afterwards.
"""
import os
import time

import requests

import settings

URL = 'https://dhruva-api.bhashini.gov.in/services/inference/llm'
MODELS = [('bhashini/google/gemma-4-26B-A4B-it', 'Default: Gemma 4 26B on Bhashini (no native tools)')]


def key():
    value = os.environ.get('BHASHINI_LLM_KEY', '')
    if not value and not settings.HOSTED and (settings.ROOT / '.env').exists():
        for line in (settings.ROOT / '.env').read_text(encoding='utf-8').splitlines():
            if line.strip().startswith('BHASHINI_LLM_KEY='):
                value = line.split('=', 1)[1].strip().strip('"\'')
    return value


def chat(model, messages, tools=None, temperature=0.2, max_tokens=900, deadline=None, on_wait=None, stop=None):
    from .nvidia import LlmError
    if tools:
        raise LlmError('rejected', f'{model} does not support native tool calls; use an agent that plans in text.', 422)
    if not key():
        where = 'as a Vercel environment variable' if settings.HOSTED else 'to .env'
        raise LlmError('not_configured', f'BHASHINI_LLM_KEY is not set. Add it {where}.', 503)
    turns = [{'role': m['role'] if m['role'] in ('system', 'user', 'assistant') else 'user', 'content': m.get('content') or ''}
             for m in messages]
    body = {'pipelineTasks': [{'taskType': 'llm', 'config': {'serviceId': model, 'temperature': temperature,
                                                             'topP': 0.9, 'maxTokens': max_tokens}}],
            'inputData': {'input': [turns]}}
    wait, started = 2.0, time.monotonic()
    while True:
        remaining = (deadline - time.monotonic()) if deadline else 60
        if remaining <= 3:
            raise LlmError('timeout', 'Out of time for this step.', 504)
        try:
            response = requests.post(URL, json=body, headers={'Authorization': key()}, timeout=min(55, max(5, remaining)))
        except requests.Timeout as error:
            raise LlmError('timeout', f'{model} did not answer in time.', 504) from error
        except requests.RequestException as error:
            raise LlmError('unreachable', f'Could not reach the Bhashini LLM API: {error}.', 502) from error
        if response.status_code in (429, 500, 502, 503, 504) and (deadline is None or time.monotonic() + wait + 5 < deadline):
            if on_wait:
                on_wait(wait)
            time.sleep(wait)
            wait = min(wait * 2, 16)
            continue
        if response.status_code in (401, 403):
            raise LlmError('bad_key', 'The Bhashini LLM API rejected the key. Check BHASHINI_LLM_KEY.', 503)
        if not response.ok:
            raise LlmError('rejected', f'{model}: Bhashini said {response.status_code} ({response.text[:200]}).',
                           502 if response.status_code >= 500 else 422)
        try:
            text = response.json()['pipelineResponse'][0]['output'][0]['target'] or ''
        except (KeyError, IndexError, TypeError, ValueError) as error:
            raise LlmError('bad_response', f'{model} returned no text ({response.text[:200]}).', 502) from error
        for word in stop or []:
            if word in text:
                text = text[:text.index(word)]
        return {'message': {'role': 'assistant', 'content': text}, 'usage': {}, 'model': model,
                'ms': int((time.monotonic() - started) * 1000)}
