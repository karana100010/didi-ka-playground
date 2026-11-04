"""Laya sidecar: a small local HTTP service around convaiinnovations/laya, for the planning step.

Laya needs PyTorch, so it runs in its own virtual environment and never on Vercel:

    python3.11 -m venv .laya-venv
    .laya-venv/bin/pip install 'laya[serve]'
    .laya-venv/bin/python scripts/laya_server.py          # http://localhost:8790

The playground reaches it at LAYA_URL (default http://localhost:8790). The first request downloads the
checkpoints from Hugging Face (about 1.3 GB) and loads them; later ones take tens of milliseconds.
"""
import os
import threading
import time

import uvicorn
from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title='Laya sidecar')
_router = None
_lock = threading.Lock()
_info = {'loaded': False, 'load_seconds': None}


def router():
    global _router
    with _lock:
        if _router is None:
            from laya import Router
            started = time.monotonic()
            _router = Router()
            _info.update(loaded=True, load_seconds=round(time.monotonic() - started, 1))
    return _router


class Predict(BaseModel):
    state: str
    questions: dict


@app.get('/health')
def health():
    import laya
    return {'ok': True, 'model': 'convaiinnovations/laya', 'version': getattr(laya, '__version__', None), **_info}


@app.post('/predict')
def predict(body: Predict):
    started = time.monotonic()
    result = router().predict(body.state, body.questions)
    return {'answers': result.get('answers', {}), 'routing': result.get('routing', {}),
            'ms': round((time.monotonic() - started) * 1000)}


if __name__ == '__main__':
    threading.Thread(target=router, daemon=True).start()  # load while the port opens
    uvicorn.run(app, host='127.0.0.1', port=int(os.environ.get('LAYA_PORT', 8790)))
