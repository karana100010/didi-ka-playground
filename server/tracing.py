"""Optional tracing to a Langfuse instance on this computer: one trace per run, so the agents can be compared.

Each run becomes one Langfuse trace (session = the didi's login, tags = agent and planner): every fresh LLM call
is a generation with its full request and response, every tool call a span with its full result, consent and
the self-check are events, and the run's metrics are scores (consent prompts, LLM calls, fields read, run time).
Langfuse 4 takes traces only as OpenTelemetry, so spans go to /api/public/otel/v1/traces as OTLP JSON and
scores to /api/public/scores.

Traces carry her fetched records, so this is for mock data on a laptop only: it is off unless the Langfuse keys
are set, and always off on Vercel. Replayed steps are not sent again (the run context only forwards new events).
Sending happens on a background thread and never fails a run.
"""
import hashlib
import json
import os
import threading
import time

import requests

import settings


def _config():
    env = {k: os.environ.get(k, '') for k in ('LANGFUSE_PUBLIC_KEY', 'LANGFUSE_SECRET_KEY', 'LANGFUSE_BASE_URL', 'LANGFUSE_HOST')}
    if not all(env[k] for k in ('LANGFUSE_PUBLIC_KEY', 'LANGFUSE_SECRET_KEY')) and (settings.ROOT / '.env').exists():
        for line in (settings.ROOT / '.env').read_text(encoding='utf-8').splitlines():
            key, _, value = line.partition('=')
            if key.strip() in env and not env[key.strip()]:
                env[key.strip()] = value.strip().strip('"\'')
    url = (env['LANGFUSE_BASE_URL'] or env['LANGFUSE_HOST'] or 'http://localhost:3000').rstrip('/')
    return env['LANGFUSE_PUBLIC_KEY'], env['LANGFUSE_SECRET_KEY'], url


def enabled():
    public, secret, _ = _config()
    return bool(public and secret) and not settings.HOSTED


def trace_id(run_id):
    return hashlib.md5(run_id.encode()).hexdigest()  # OpenTelemetry wants 32 hex characters


def _value(v):
    if isinstance(v, bool):
        return {'boolValue': v}
    if isinstance(v, int):
        return {'intValue': v}
    if isinstance(v, float):
        return {'doubleValue': v}
    if isinstance(v, list) and all(isinstance(x, str) for x in v):
        return {'arrayValue': {'values': [{'stringValue': x} for x in v]}}
    return {'stringValue': v if isinstance(v, str) else json.dumps(v, ensure_ascii=False, default=str)}


class Tracer:
    """Collects one pass of a run and sends it when the pass ends (done, paused for consent, or failed)."""

    def __init__(self, ctx):
        self.on = enabled()
        self.ctx = ctx
        self.trace_id = trace_id(ctx.run['id'])
        self.trace_attrs = self._trace_attrs() if self.on else {}  # on every pass: a resumed run never re-sends run_started
        self.spans, self.scores = [], []
        self.count = 0

    def _trace_attrs(self):
        import agents
        ctx, run = self.ctx, self.ctx.run
        module = agents.AGENTS.get(run['agent'])
        return {'langfuse.trace.name': getattr(module, 'NAME', run['agent']),
                'langfuse.session.id': ctx.session['id'], 'langfuse.user.id': ctx.didi.get('didi_id') or ctx.didi.get('name'),
                'langfuse.trace.input': ctx.turn['question'],
                'langfuse.trace.tags': [run['agent'], f'planner:{ctx.prefs.get("planner", "llm")}',
                                        'pivot' if ctx.llm_language != ctx.language else 'direct', 'compare' if run.get('position') else 'main'],
                'langfuse.trace.metadata.model': run['model'], 'langfuse.trace.metadata.run_id': run['id'],
                'langfuse.trace.metadata.turn_id': ctx.turn['id'], 'langfuse.trace.metadata.didi': ctx.didi.get('name')}

    def _span(self, name, kind, start_ns, end_ns, **attrs):
        if not self.on:
            return
        self.count += 1
        span_id = hashlib.md5(f'{self.ctx.run["id"]}:{self.ctx.event_seq}:{self.count}:{name}'.encode()).hexdigest()[:16]
        attrs = {'langfuse.observation.type': kind, **self.trace_attrs, **attrs}
        self.spans.append({'traceId': self.trace_id, 'spanId': span_id, 'name': name, 'kind': 1,
                           'startTimeUnixNano': str(start_ns), 'endTimeUnixNano': str(end_ns),
                           'attributes': [{'key': k, 'value': _value(v)} for k, v in attrs.items() if v is not None]})

    # --- hooks called by RunContext ----------------------------------------------------------------

    def event(self, type_, data):
        if not self.on:
            return
        ctx, now = self.ctx, time.time_ns()
        if type_ == 'run_started':
            self._span('run started', 'event', now, now, **{'langfuse.observation.input': data})
        elif type_ == 'llm_detail':
            request, usage = data.get('request') or {}, data.get('usage') or {}
            self._span(f'llm call {data.get("call")}', 'generation', now - int((data.get('ms') or 0) * 1e6), now, **{
                'langfuse.observation.input': request.get('messages'), 'langfuse.observation.output': data.get('response'),
                'gen_ai.request.model': request.get('model'), 'langfuse.observation.model.name': request.get('model'),
                'langfuse.observation.model_parameters': {k: request.get(k) for k in ('temperature', 'max_tokens') if request.get(k) is not None},
                'langfuse.observation.metadata.tools': request.get('tools') or None,
                'gen_ai.usage.input_tokens': usage.get('prompt_tokens'), 'gen_ai.usage.output_tokens': usage.get('completion_tokens')})
        elif type_ == 'answer':
            self._span('answer', 'event', now, now, **{'langfuse.observation.output': data.get('text'), 'langfuse.trace.output': data.get('text')})
        elif type_ == 'metrics':
            self._span('metrics', 'event', now, now, **{'langfuse.observation.output': data})
            for name in ('llm_calls', 'tool_calls', 'consent_prompts', 'fields_read', 'run_ms', 'prompt_tokens', 'completion_tokens'):
                if isinstance(data.get(name), (int, float)):
                    self.scores.append({'traceId': self.trace_id, 'name': name, 'value': data[name], 'dataType': 'NUMERIC'})
        elif type_ == 'error':
            self._span('error', 'event', now, now, **{'langfuse.observation.level': 'ERROR', 'langfuse.observation.status_message': data.get('message'),
                                                      'langfuse.observation.input': data, 'langfuse.trace.output': {'error': data.get('code')}})
        elif type_ in ('plan', 'consent_request', 'consent_result', 'critic', 'writer_rejected', 'translated', 'discarded', 'status'):
            warn = type_ == 'writer_rejected' or (type_ == 'critic' and data.get('rewrote'))
            self._span(type_, 'event', now, now, **{'langfuse.observation.input': data,
                                                    'langfuse.observation.level': 'WARNING' if warn else 'DEFAULT'})

    def tool(self, name, args, result, summary):
        now = time.time_ns()
        self._span(f'tool: {name}', 'tool', now, now, **{'langfuse.observation.input': args, 'langfuse.observation.output': result,
                                                          'langfuse.observation.metadata.summary': summary})

    def flush(self):
        if not (self.spans or self.scores):
            return
        spans, scores, self.spans, self.scores = self.spans, self.scores, [], []
        threading.Thread(target=_send, args=(spans, scores), daemon=True).start()


def _send(spans, scores):
    public, secret, url = _config()
    body = {'resourceSpans': [{'resource': {'attributes': [{'key': 'service.name', 'value': {'stringValue': 'didi-playground'}}]},
                               'scopeSpans': [{'scope': {'name': 'didi-playground'}, 'spans': spans}]}]}
    try:
        if spans:
            requests.post(f'{url}/api/public/otel/v1/traces', auth=(public, secret), timeout=10,
                          data=json.dumps(body, ensure_ascii=False, default=str).encode(), headers={'Content-Type': 'application/json'})
        for score in scores:
            requests.post(f'{url}/api/public/scores', auth=(public, secret), json=score, timeout=10)
    except requests.RequestException:
        pass  # tracing is best effort
