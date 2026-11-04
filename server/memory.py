"""Session memory: a didi's earlier questions and answers, kept across logins.

Redis when REDIS_URL is set and reachable (one list per workspace + didi, TTL refreshed on write); otherwise,
or on any Redis error, the core store's turns. Only conversation text is kept: never register values or consents.
"""
import json
import time

import settings
from core import store

CAP = 200  # entries kept per didi
RETRY = 30  # seconds before trying a dead Redis again

_client = None
_down_until = 0.0


def use_client(client):
    """Tests inject a client (e.g. fakeredis); None resets to lazy creation."""
    global _client, _down_until
    _client, _down_until = client, 0.0


def key(workspace, didi_id):
    return f'didi:memory:{workspace}:{didi_id}'


def _redis():
    global _client
    if _client is not None:
        return _client
    if not settings.REDIS_URL or time.monotonic() < _down_until:
        return None
    import redis
    _client = redis.Redis.from_url(settings.REDIS_URL, socket_timeout=0.5, socket_connect_timeout=0.5,
                                   decode_responses=True)
    return _client


def _failed():
    """Fall back to the store for a while; a later call reconnects."""
    global _client, _down_until
    if not settings.REDIS_URL:  # an injected test client: keep it
        _down_until = 0.0
        return
    _client, _down_until = None, time.monotonic() + RETRY


def _call(fn):
    """fn(redis) or None when Redis is off or fails."""
    client = _redis()
    if client is None:
        return None
    try:
        return fn(client)
    except Exception:  # noqa: BLE001 - any Redis trouble means the store answers this call
        _failed()
        return None


def backend():
    return 'redis' if _call(lambda r: r.ping()) else 'store'


def remember(workspace, session, turn_id, question, answer, agent):
    if not answer:
        return None
    entry = dict(turn_id=turn_id, session_id=session['id'], question=question, answer=answer, agent=agent,
                 at=store.now().isoformat())

    def push(r):
        k = key(workspace, session['didi_id'])
        pipe = r.pipeline()
        pipe.rpush(k, json.dumps(entry, ensure_ascii=False))
        pipe.ltrim(k, -CAP, -1)
        pipe.expire(k, int(settings.MEMORY_TTL_DAYS * 86400))
        pipe.execute()
        return True

    _call(push)  # in the store fallback the turn is already saved
    return None


def recall(workspace, didi_id, limit=50):
    """Her entries, oldest first, across all her sessions."""
    rows = _call(lambda r: r.lrange(key(workspace, didi_id), -limit, -1) if limit else [])
    if rows is not None:
        return [json.loads(row) for row in rows]
    return store.didi_turns(workspace, didi_id, limit)


def forget(workspace, didi_id):
    """Remove her memory everywhere it is kept. Returns the entries removed."""
    k = key(workspace, didi_id)
    removed = _call(lambda r: r.pipeline().llen(k).delete(k).execute()[0]) or 0
    return max(removed, store.forget_didi_turns(workspace, didi_id))  # the same turns may be in both
