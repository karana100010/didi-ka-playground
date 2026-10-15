"""NVIDIA API client: chat completions (with tools), the model list, and a one-call tool-support probe.

The key stays on the server (NVIDIA_API_KEY). Free-tier keys are rate-limited, so chat() retries 429 and
5xx with back-off but never past the caller's deadline, and the probe runs only when asked.
"""
import time

import requests

import settings

BASE = 'https://integrate.api.nvidia.com/v1'
RETRY_STATUS = {429, 500, 502, 503, 504}

# Shortlist from docs/design.html §10, chosen by name from the live catalogue on 15 Oct 2026.
# Tool support and Indic quality are measured by probe() and the eval set, not assumed.
# Measured live on 15 Oct 2026 with reasoning off: lightning answers in about a second, super in about 20 s.
RECOMMENDED = [
    ('nvidia/nemotron-3.5-lightning-30b-a3b', 'Default: fast, native tools'),
    ('nvidia/nemotron-3-super-120b-a12b', 'Larger, slower (about 20 s a call)'),
    ('nvidia/nemotron-3-ultra-550b-a55b', 'Large model for comparison'),
    ('moonshotai/kimi-k2.6', 'Third-party comparison'),
    ('z-ai/glm-5.3', 'Third-party comparison'),
    ('deepseek-ai/deepseek-v4.1-flash', 'Third-party comparison'),
    ('google/gemma-4-31b-it', 'Smaller open model'),
    ('openai/gpt-oss-20b', 'Smaller open model'),
]
DEFAULT_MODEL = RECOMMENDED[0][0]
FAST_MODEL = RECOMMENDED[0][0]
# Ids with these words are embedding, safety, reward, parsing, vision-only or code models: not for the chat.
NOT_CHAT = ('embed', 'reward', 'safety', 'guard', 'parse', 'clip', 'vila', 'kosmos', 'deplot', 'fuyu', 'neva',
            'detector', 'calibration', 'translate', 'code', 'starcoder', 'coder', 'cosmos', 'diffusion',
            'retriever', 'topic-control', 'chatqa', 'llama2', 'recurrentgemma', 'gemma-2b', 'muse')


class LlmError(Exception):
    def __init__(self, code, message, status=502):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def _key():
    key = settings.nvidia_key()
    if not key:
        where = 'as a Vercel environment variable' if settings.HOSTED else 'to .env (NVIDIA_API_KEY=nvapi-…)'
        raise LlmError('not_configured', f'NVIDIA_API_KEY is not set. Add it {where}.', 503)
    return key


def chat(model, messages, tools=None, temperature=0.2, max_tokens=900, deadline=None, on_wait=None, stop=None):
    """One chat completion. Returns {'message': {...}, 'usage': {...}, 'ms': int, 'model': str}.

    deadline: time.monotonic() value after which no retry starts. on_wait(seconds) is called before each
    back-off so the caller can tell the user ("थोड़ा रुकिए").
    """
    if settings.FAKE_LLM:
        return _fake(model, messages, tools)
    body = {'model': model, 'messages': messages, 'temperature': temperature, 'max_tokens': max_tokens}
    if tools:
        body['tools'] = tools
        body['tool_choice'] = 'auto'
    if stop:
        body['stop'] = stop
    if 'nemotron' in model:
        # Nemotron reasons by default and can spend the whole token budget on it, leaving no answer.
        body['chat_template_kwargs'] = {'enable_thinking': False}
    headers = {'Authorization': f'Bearer {_key()}', 'Accept': 'application/json'}
    wait, started = 2.0, time.monotonic()
    while True:
        remaining = (deadline - time.monotonic()) if deadline else 60
        if remaining <= 3:
            raise LlmError('timeout', 'Out of time for this step.', 504)
        try:
            response = requests.post(f'{BASE}/chat/completions', json=body, headers=headers,
                                     timeout=min(55, max(5, remaining)))
        except requests.Timeout as error:
            raise LlmError('timeout', f'{model} did not answer in time.', 504) from error
        except requests.RequestException as error:
            raise LlmError('unreachable', f'Could not reach the NVIDIA API: {error}.', 502) from error
        if response.status_code in RETRY_STATUS:
            retry_after = response.headers.get('Retry-After', '')
            pause = float(retry_after) if retry_after.replace('.', '', 1).isdigit() else wait
            if deadline is None or time.monotonic() + pause + 5 < deadline:
                if on_wait:
                    on_wait(pause)
                time.sleep(pause)
                wait = min(wait * 2, 16)
                continue
            code = 'rate_limited' if response.status_code == 429 else 'unavailable'
            raise LlmError(code, f'NVIDIA API said {response.status_code} ({_detail(response)}).', 503)
        if response.status_code == 401 or response.status_code == 403:
            raise LlmError('bad_key', 'The NVIDIA API rejected the key. Check NVIDIA_API_KEY.', 503)
        if not response.ok:
            raise LlmError('rejected', f'{model}: {_detail(response)}', 502 if response.status_code >= 500 else 422)
        data = response.json()
        try:
            choice = data['choices'][0]
            message = choice['message']
        except (KeyError, IndexError, TypeError) as error:
            raise LlmError('bad_response', f'{model} returned no message ({str(data)[:200]}).', 502) from error
        if not (message.get('content') or message.get('tool_calls')) and choice.get('finish_reason') == 'length':
            raise LlmError('reasoning_overflow', f'{model} used all {max_tokens} tokens before answering.', 502)
        return {'message': _clean(message), 'usage': data.get('usage') or {}, 'model': model,
                'ms': int((time.monotonic() - started) * 1000)}


def _fake(model, messages, tools=None):
    """A scripted stand-in for local UI checks without a key (PLAYGROUND_FAKE_LLM=1). Never active on Vercel.

    For a savings or income question it asks consent, fetches, and answers from the rows; otherwise it echoes."""
    import json
    import re
    time.sleep(0.5)
    system = messages[0]['content'] if messages and messages[0]['role'] == 'system' else ''
    question = next((m['content'] for m in reversed(messages) if m['role'] == 'user'
                     and not m['content'].startswith(('Observation:', 'Write the Final Answer', 'Answer her now'))), '')

    def reply(content='', calls=None):
        message = {'role': 'assistant', 'content': content}
        if calls:
            message['tool_calls'] = [{'id': f'fake_{len(messages)}', 'type': 'function',
                                      'function': {'name': calls[0], 'arguments': json.dumps(calls[1], ensure_ascii=False)}}]
        return {'message': message, 'usage': {'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0}, 'model': model, 'ms': 500}

    if system.startswith("You read a rural woman's"):
        said = question
        if any(w in said for w in ('नहीं', 'मत')) and not any(w in said for w in ('पर', 'लेकिन')):
            return reply('{"decision": "deny", "granted_items": [], "refused_registers": [], "confidence": 0.9}')
        if any(w in said for w in ('हाँ', 'हां', 'ठीक', 'देख')):
            refused = ['loans'] if any(w in said for w in ('लोन', 'कर्ज')) else []
            return reply(json.dumps({'decision': 'grant', 'granted_items': [1], 'refused_registers': refused, 'confidence': 0.9}))
        return reply('{"decision": "unclear", "confidence": 0.2}')
    names = [t['function']['name'] for t in tools or []]
    source = re.search(r'Source (src_[0-9a-f]+) \(lokos', system)
    table = 'savings' if 'बचत' in question else 'dar' if any(w in question for w in ('आमदनी', 'लखपति', 'कमाई')) else None
    fields = ['month', 'amount'] if table == 'savings' else ['month', 'income', 'expense', 'cycle_no']
    purpose = 'savings_question' if table == 'savings' else 'lakhpati_status_check' if 'लखपति' in question else 'income_question'
    if system.startswith('You plan how to answer') and any(w in question for w in ('प्लान', 'योजना', 'plan')):
        kind = 'kirana' if 'किराना' in question else 'tailoring' if 'सिलाई' in question else 'dairy'
        return reply(json.dumps({'needs_records': True, 'business_plan': kind, 'plan_changes': {}, 'purpose': 'business_plan',
                                 'items': [], 'scheme_topics': [], 'steps': ['Ask permission once', 'Build the plan', 'Explain it']}))
    if system.startswith('You plan how to answer'):  # Plan-and-Execute planner
        items = [{'source': source.group(1), 'table': table, 'fields': fields}] if source and table else []
        return reply(json.dumps({'needs_records': bool(items), 'purpose': purpose, 'items': items,
                                 'decision': 'lakhpati_status' if 'लखपति' in question else None,
                                 'scheme_topics': ['lakhpati_didi'] if 'लखपति' in question else [],
                                 'steps': ['Ask her permission once', f'Read {table}', 'Answer from exact totals'] if items else ['Answer']}))
    if 'Facts:\n' in system:  # Plan-and-Execute writer
        found = json.loads(system.split('Facts:\n', 1)[1])
        if found.get('business_plan'):
            r = found['business_plan']['results']
            return reply(f'(परीक्षण मॉडल) आपका {found["business_plan"]["enterprise"]["hi"]} का प्लान तैयार है। अभी हर महीने मुनाफ़ा '
                         f'₹{r["profit_now"]:,} है, प्लान के बाद ₹{r["profit_after"]:,} होगा। किस्त ₹{r["emi"]:,} होगी और '
                         f'उसके बाद हर महीने ₹{r["extra_after_emi"]:,} ज़्यादा बचेंगे। लागत {r["payback_months"]} महीनों में वापस आएगी। '
                         'इस प्लान की लागतें नकली हैं। Excel फ़ाइल डाउनलोड कर सकती हैं।')
        if found.get('decision'):
            d = found['decision']
            return reply(f'(परीक्षण मॉडल) आपके रजिस्टर के अनुसार पिछले 12 महीनों में आपकी आमदनी ₹{d["inputs"]["annual_income"]:,} रही। '
                         f'{d["reasons"][0]["hi"]} इसलिए आप "{d["outcome_label"]["hi"]}" हैं।')
        for register in found.get('registers', []):
            if register['table'] == 'savings':
                return reply(f'(परीक्षण मॉडल) आपके बचत रजिस्टर के अनुसार पिछले 12 महीनों में आपने ₹{register["last_12_months_amount"]:,} जमा किए।')
            if register['table'] == 'dar':
                return reply(f'(परीक्षण मॉडल) आपके डिजिटल आजीविका रजिस्टर के अनुसार पिछले 12 महीनों में आपकी आमदनी, खर्च घटाकर, '
                             f'₹{register["last_12_months_net_income"]:,} रही, यानी हर महीने ₹{register["last_12_months_monthly_average_net_income"]:,}।')
        return reply('(परीक्षण मॉडल) मैं आपकी जानकारी के बिना सिर्फ़ सामान्य बात बता सकती हूँ।')
    if 'Rewrite the answer' in question:  # self-check rewrite
        return reply(question.split('Answer:\n', 1)[-1])
    if 'Action Input' in system and source and table:  # ReAct
        observations = [json.loads(m['content'][len('Observation: '):]) for m in messages
                        if m['role'] == 'user' and m['content'].startswith('Observation: {')]
        if not observations:
            return reply('Thought: I need her permission first.\nAction: request_consent\nAction Input: ' + json.dumps(
                {'purpose': purpose, 'items': [{'source': source.group(1), 'table': table, 'fields': fields}]}))
        last = observations[-1]
        if 'consent_id' in last and last.get('decision') in ('granted', 'partial'):
            return reply('Thought: Now I read the register.\nAction: fetch_records\nAction Input: ' + json.dumps(
                {'consent_id': last['consent_id'], 'source': source.group(1), 'table': table, 'fields': fields}))
        if 'rows' in last:
            rows = [r for r in last['rows'] if r.get('month', '') >= '2025-10']
            value = sum(r.get('amount', 0) for r in rows) if table == 'savings' else sum((r.get('income') or 0) - (r.get('expense') or 0) for r in rows)
            return reply(f'Thought: I can answer now\nFinal Answer: (परीक्षण मॉडल) पिछले 12 महीनों का हिसाब ₹{value:,} है।')
        return reply('Thought: She said no.\nFinal Answer: (परीक्षण मॉडल) ठीक है, मैं आपकी जानकारी नहीं देखूँगी।')
    if 'request_consent' in names and source and table:
        fields = ['month', 'amount'] if table == 'savings' else ['month', 'income', 'expense']
        results = [json.loads(m['content']) for m in messages if m['role'] == 'tool']
        if not results:
            purpose = 'savings_question' if table == 'savings' else 'income_question'
            return reply(calls=('request_consent', {'purpose': purpose, 'items': [{'source': source.group(1), 'table': table, 'fields': fields}]}))
        last = results[-1]
        if last.get('decision') in ('granted', 'partial') and len(results) == 1:
            granted = next((g['fields'] for g in last['granted'] if g['table'] == table), fields)
            return reply(calls=('fetch_records', {'consent_id': last['consent_id'], 'source': source.group(1), 'table': table, 'fields': granted}))
        if 'rows' in last:
            rows = last['rows'][-12:]
            if table == 'savings':
                total = rows[-1].get('amount', 0) * len(rows) if rows else 0
                return reply(f'(परीक्षण मॉडल) आपके बचत रजिस्टर के अनुसार पिछले {len(rows)} महीनों में आपने ₹{total:,} जमा किए।')
            net = sum((r.get('income') or 0) - (r.get('expense') or 0) for r in rows)
            return reply(f'(परीक्षण मॉडल) आपके डिजिटल आजीविका रजिस्टर के अनुसार पिछले 12 महीनों की कुल आमदनी, खर्च घटाकर, ₹{net:,} रही।')
        return reply('(परीक्षण मॉडल) ठीक है, मैं आपकी जानकारी नहीं देखूँगी। आपकी अनुमति के बिना मैं सिर्फ़ सामान्य जानकारी दे सकती हूँ।')
    return reply(f'(परीक्षण मॉडल) आपने पूछा: "{question}"। असली जवाब के लिए NVIDIA_API_KEY जोड़िए।')


def _clean(message):
    out = {'role': 'assistant', 'content': message.get('content') or ''}
    if message.get('reasoning_content'):
        out['reasoning'] = message['reasoning_content']
    calls = message.get('tool_calls') or []
    if calls:
        out['tool_calls'] = [{'id': c.get('id') or f'call_{i}', 'type': 'function',
                              'function': {'name': c['function']['name'], 'arguments': c['function'].get('arguments') or '{}'}}
                             for i, c in enumerate(calls) if c.get('function', {}).get('name')]
    return out


def _detail(response):
    try:
        data = response.json()
        detail = data.get('detail') or data.get('error') or data.get('message') or data
        if isinstance(detail, dict):
            detail = detail.get('message') or str(detail)
        return str(detail)[:240]
    except ValueError:
        return response.text[:240] or response.reason


_models = {'at': 0.0, 'ids': []}


def list_models():
    """Chat model ids from the live catalogue, cached for 10 minutes."""
    if time.time() - _models['at'] > 600:
        headers = {'Authorization': f'Bearer {settings.nvidia_key()}'} if settings.nvidia_key() else {}
        try:
            response = requests.get(f'{BASE}/models', headers=headers, timeout=15)
            response.raise_for_status()
            ids = sorted(m['id'] for m in response.json().get('data', []))
            _models.update(at=time.time(), ids=ids)
        except (requests.RequestException, ValueError, KeyError):
            if not _models['ids']:
                _models['ids'] = [m for m, _ in RECOMMENDED]
    return [m for m in _models['ids'] if not any(word in m.lower() for word in NOT_CHAT)]


PROBE_TOOL = [{'type': 'function', 'function': {
    'name': 'get_savings', 'description': 'Returns the savings balance of an SHG member.',
    'parameters': {'type': 'object', 'properties': {'member_id': {'type': 'string'}}, 'required': ['member_id']}}}]


def probe(model):
    """'tools' if the model returns a native tool call, 'text' if it answers in text instead or rejects tools."""
    messages = [{'role': 'system', 'content': 'Use the tools to answer.'},
                {'role': 'user', 'content': 'What is the savings balance of member D-001? Use the get_savings tool.'}]
    try:
        result = chat(model, messages, tools=PROBE_TOOL, max_tokens=200, deadline=time.monotonic() + 45)
    except LlmError as error:
        if 'end of life' in error.message.lower() or 'not found' in error.message.lower():
            return 'error', error.message
        if error.code in ('rejected', 'bad_response'):  # e.g. "tools are not supported for this model"
            return 'text', error.message
        raise
    calls = result['message'].get('tool_calls') or []
    if calls and calls[0]['function']['name'] == 'get_savings':
        return 'tools', f'{result["ms"]} ms'
    return 'text', 'Answered without calling the tool.'
