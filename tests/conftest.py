"""Shared fixtures: a fresh core store per test (local SQLite, or Postgres when DATABASE_URL is set) and a
scripted fake LLM, so no test calls NVIDIA unless asked to."""
import importlib
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'server'))
WS = {'X-Workspace': 'test-workspace-0001'}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('PLAYGROUND_ACCESS_CODE', '')
    monkeypatch.delenv('VERCEL', raising=False)
    monkeypatch.delenv('BLOB_READ_WRITE_TOKEN', raising=False)
    import settings
    importlib.reload(settings)
    monkeypatch.setattr(settings, 'LOCAL', tmp_path)
    monkeypatch.setattr(settings, 'ACCESS_CODE', '')
    monkeypatch.setattr(settings, 'BLOB_TOKEN', '')
    from core import store
    if not os.environ.get('DATABASE_URL'):
        monkeypatch.setattr(settings, 'DATABASE_URL', '')
    monkeypatch.setattr(store, '_engine', None)
    if os.environ.get('DATABASE_URL'):
        engine = store.engine()
        store.metadata.drop_all(engine)
        store.metadata.create_all(engine)
    import main
    from fastapi.testclient import TestClient
    with TestClient(main.app) as c:
        yield c


class FakeLLM:
    """Returns scripted messages in order. A script item is a string (content) or a dict (a full message)."""

    def __init__(self, *script):
        self.script = list(script)
        self.calls = []

    def __call__(self, model, messages, tools=None, temperature=0.2, max_tokens=900, deadline=None, on_wait=None, stop=None):
        self.stops = getattr(self, 'stops', []) + [stop]
        self.calls.append({'model': model, 'messages': messages, 'tools': tools})
        if not self.script:
            raise AssertionError('FakeLLM ran out of scripted replies')
        item = self.script.pop(0)
        if callable(item):
            item = item(messages, tools)
        message = {'role': 'assistant', 'content': item} if isinstance(item, str) else {'role': 'assistant', **item}
        return {'message': message, 'usage': {'prompt_tokens': 100, 'completion_tokens': 20, 'total_tokens': 120},
                'ms': 7, 'model': model}


@pytest.fixture()
def fake_llm(monkeypatch):
    from llm import nvidia
    fake = FakeLLM()
    monkeypatch.setattr(nvidia, 'chat', fake)
    monkeypatch.setattr(nvidia, 'list_models', lambda: [m for m, _ in nvidia.RECOMMENDED] + ['meta/llama-3.2-90b-vision-instruct'])
    return fake


def tool_call(name, **args):
    return {'content': '', 'tool_calls': [{'id': f'call_{name}', 'type': 'function',
                                           'function': {'name': name, 'arguments': json.dumps(args, ensure_ascii=False)}}]}


def stream(client, turn_id, after=0, headers=WS):
    """Read one event stream to its end. Returns (events, end)."""
    events, end = [], None
    with client.stream('GET', f'/v1/turns/{turn_id}/stream?after={after}', headers=headers) as response:
        assert response.status_code == 200
        kind = None
        for line in response.iter_lines():
            if line.startswith('event: '):
                kind = line[7:]
            elif line.startswith('data: '):
                data = json.loads(line[6:])
                if kind == 'end':
                    end = data
                else:
                    events.append(data)
    return events, end


def login(client, didi='D-001', samples=True, **prefs):
    """Log in as a didi (loading the sample registers first unless samples=False), optionally setting prefs."""
    if samples:
        client.post('/v1/sources/sample', headers=WS)
    session = client.post('/v1/sessions', headers=WS, json={'didi_id': didi}).json()
    if prefs:
        session['prefs'] = client.put(f'/v1/sessions/{session["id"]}/prefs', headers=WS, json=prefs).json()['prefs']
    return session
