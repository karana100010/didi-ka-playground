"""Client for the local Laya sidecar (scripts/laya_server.py): convaiinnovations/laya, a small multilingual
classifier that answers typed questions (choice, score, yes/no) about a text. It does not write text.

Laya needs PyTorch, so it runs only on a computer with the sidecar started; on Vercel LAYA_URL is empty and
the option shows as unavailable.
"""
import time

import requests

import settings

_health = {'at': 0.0, 'value': None}


class LayaError(Exception):
    pass


def health(max_age=15):
    """{'ok': bool, ...}, cached for a few seconds so the side panel can poll it."""
    if not settings.LAYA_URL:
        return {'ok': False, 'reason': 'Laya runs only locally: start scripts/laya_server.py and set LAYA_URL.'}
    if time.monotonic() - _health['at'] < max_age and _health['value']:
        return _health['value']
    try:
        response = requests.get(f'{settings.LAYA_URL}/health', timeout=1.5)
        value = {**response.json(), 'ok': response.ok, 'url': settings.LAYA_URL}
    except (requests.RequestException, ValueError):
        value = {'ok': False, 'url': settings.LAYA_URL,
                 'reason': f'No Laya sidecar at {settings.LAYA_URL}. Start it with .laya-venv/bin/python scripts/laya_server.py.'}
    _health.update(at=time.monotonic(), value=value)
    return value


def predict(state, questions, timeout=120):
    if not settings.LAYA_URL:
        raise LayaError('Laya is not available here (it runs only locally).')
    try:
        response = requests.post(f'{settings.LAYA_URL}/predict', json={'state': state, 'questions': questions}, timeout=timeout)
    except requests.RequestException as error:
        _health.update(at=0.0, value=None)
        raise LayaError(f'Laya sidecar did not answer: {error}') from error
    if not response.ok:
        raise LayaError(f'Laya sidecar error {response.status_code}: {response.text[:200]}')
    return response.json()
