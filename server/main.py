"""Didi ka Playground: one FastAPI app for the API, bhashini_kit's language routes and the web pages.

Local:   .venv/bin/uvicorn main:app --app-dir server --reload --port 8800
Vercel:  api/index.py imports `app` from here (see vercel.json).
"""
import hmac
import json
import queue
import re
import threading
import time

import settings  # noqa: I001  (first: loads .env before the Bhashini config is read)

from fastapi import FastAPI, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import agents
import decisions
import department
import runner
import sources
from bhashini import routes as bhashini_routes
from connectors import ConnectorError
from consent import catalog, ledger
from core import files, store
from llm import nvidia

app = FastAPI(title='Didi ka Playground', docs_url='/v1/docs', openapi_url='/v1/openapi.json')
WORKSPACE = re.compile(r'^[A-Za-z0-9-]{8,64}$')
PUBLIC = {'/v1/features'}


def error(code, message, status):
    return JSONResponse({'error': {'code': code, 'message': message}}, status_code=status)


@app.middleware('http')
async def gate(request: Request, call_next):
    """Every /api and /v1 call needs the access code (when one is set) and /v1 calls need a workspace id."""
    path = request.url.path
    if path.startswith(('/api/', '/v1/')) and path not in PUBLIC and not path.startswith('/v1/docs') \
            and path != '/v1/openapi.json':
        if settings.ACCESS_CODE:
            sent = request.headers.get('x-access-code', '')
            if not hmac.compare_digest(sent.encode(), settings.ACCESS_CODE.encode()):
                return error('access_denied', 'Enter the access code to use the playground.', 401)
        if path.startswith('/v1/') and path != '/v1/status' and not WORKSPACE.match(request.headers.get('x-workspace', '')):
            return error('no_workspace', 'Missing X-Workspace header. Reload the page.', 400)
    return await call_next(request)


@app.exception_handler(ConnectorError)
async def connector_error(_, exc: ConnectorError):
    return error(exc.code, exc.message, exc.status)


@app.exception_handler(files.FileError)
async def file_error(_, exc: files.FileError):
    return error('file_error', str(exc), exc.status)


@app.exception_handler(store.NotConfigured)
async def not_configured(_, exc: store.NotConfigured):
    return error('not_configured', str(exc), 503)


@app.exception_handler(nvidia.LlmError)
async def llm_error(_, exc: nvidia.LlmError):
    return error(exc.code, exc.message, exc.status)


def ws(request: Request):
    return request.headers['x-workspace']


# --- status --------------------------------------------------------------------------------

@app.get('/v1/features')
async def features():
    """Public: tells the page whether to ask for an access code."""
    return {'access_required': bool(settings.ACCESS_CODE), 'hosted': settings.HOSTED}


@app.get('/v1/status')
async def status():
    return {'bhashini_key': bhashini_routes.key_set(), 'nvidia_key': bool(settings.nvidia_key()),
            'database': store.kind(), 'files': files.kind(),
            'service_account': settings.service_account_email(), 'hosted': settings.HOSTED,
            'max_upload_mb': settings.MAX_UPLOAD // (1024 * 1024)}


# --- sources -------------------------------------------------------------------------------

class LinkRequest(BaseModel):
    gsheet_url: str | None = Field(default=None, max_length=500)
    sql_url: str | None = Field(default=None, max_length=1000)


class SetupRequest(BaseModel):
    role: str = Field(pattern='^(primary|linked)$')
    users_table: str | None = Field(default=None, max_length=120)
    key_column: str | None = Field(default=None, max_length=120)
    join_key: str | None = Field(default=None, max_length=120)
    owner_dept: str = Field(default='', max_length=120)
    table_owners: dict[str, str] = Field(default_factory=dict)


@app.get('/v1/sources')
async def list_sources(request: Request):
    await run_in_threadpool(sources.ensure_samples, ws(request))
    rows = await run_in_threadpool(store.list_sources, ws(request))
    return {'sources': [sources.describe(s) for s in rows]}


@app.get('/v1/sources/{source_id}')
async def get_source(request: Request, source_id: str):
    source = await run_in_threadpool(store.get_source, ws(request), source_id)
    if not source:
        return error('not_found', 'No such source in this playground.', 404)
    return await run_in_threadpool(sources.describe, source, True)


@app.post('/v1/sources/upload')
async def upload_source(request: Request, file: UploadFile):
    data = await file.read(settings.MAX_UPLOAD + 1)
    if len(data) > settings.MAX_UPLOAD:
        return error('too_large', f'Files over {settings.MAX_UPLOAD // (1024 * 1024)} MB cannot be uploaded yet.', 413)
    source = await run_in_threadpool(sources.add_upload, ws(request), file.filename, data)
    return await run_in_threadpool(sources.describe, source, True)


@app.post('/v1/sources/link')
async def link_source(request: Request, body: LinkRequest):
    if bool(body.gsheet_url) == bool(body.sql_url):
        return error('bad_request', 'Send either a Google Sheet link or a database URL.', 422)
    add = sources.add_gsheet if body.gsheet_url else sources.add_sql_url
    source = await run_in_threadpool(add, ws(request), body.gsheet_url or body.sql_url)
    return await run_in_threadpool(sources.describe, source, True)


@app.post('/v1/sources/sample')
async def sample_sources(request: Request):
    added = await run_in_threadpool(sources.add_samples, ws(request))
    return {'sources': [sources.describe(s) for s in added]}


@app.put('/v1/sources/{source_id}/setup')
async def setup_source(request: Request, source_id: str, body: SetupRequest):
    source = await run_in_threadpool(sources.save_setup, ws(request), source_id, body.model_dump())
    return sources.describe(source)


@app.delete('/v1/sources/{source_id}')
async def delete_source(request: Request, source_id: str):
    await run_in_threadpool(sources.remove, ws(request), source_id)
    return {'deleted': True}


# --- didis and sessions --------------------------------------------------------------------

class SessionRequest(BaseModel):
    didi_id: str = Field(min_length=1, max_length=64)


@app.get('/v1/didis')
async def list_didis(request: Request):
    source, didis = await run_in_threadpool(sources.didis, ws(request))
    return {'source': {'id': source['id'], 'name': source['name']}, 'didis': didis}


@app.get('/v1/sources/{source_id}/users')
async def source_users(request: Request, source_id: str):
    source, didis = await run_in_threadpool(sources.didis, ws(request), source_id)
    return {'source': {'id': source['id'], 'name': source['name']}, 'didis': didis}


def session_out(session):
    return {'id': session['id'], 'didi': session['didi'], 'source_id': session['source_id'], 'prefs': session['prefs']}


@app.post('/v1/sessions')
async def create_session(request: Request, body: SessionRequest):
    session = await run_in_threadpool(sources.login, ws(request), body.didi_id, runner.clean_prefs({}))
    return session_out(session)


def _session(request, session_id):
    session = store.get_session(ws(request), session_id)
    if not session:
        raise ConnectorError('not_found', 'This login has expired. Log in again.', 404)
    return session


@app.get('/v1/sessions/{session_id}')
async def get_session(request: Request, session_id: str):
    session = await run_in_threadpool(_session, request, session_id)
    turns = await run_in_threadpool(store.session_turns, session_id, 30)
    return {**session_out(session), 'turns': [
        {'id': t['id'], 'question': t['question'], 'mode': t['mode'], 'status': t['status'], 'answer': t['answer']}
        for t in turns]}


@app.put('/v1/sessions/{session_id}/prefs')
async def save_prefs(request: Request, session_id: str, body: dict):
    session = await run_in_threadpool(_session, request, session_id)
    prefs = runner.clean_prefs({**session['prefs'], **body})
    await run_in_threadpool(store.save_prefs, ws(request), session_id, prefs)
    return {'prefs': prefs}


# --- turns ---------------------------------------------------------------------------------

class TurnRequest(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    mode: str = Field(default='text', pattern='^(text|voice)$')


@app.post('/v1/sessions/{session_id}/turns')
async def create_turn(request: Request, session_id: str, body: TurnRequest):
    session = await run_in_threadpool(_session, request, session_id)
    turn = await run_in_threadpool(runner.start_turn, ws(request), session, body.text, body.mode)
    return {'id': turn['id'], 'question': turn['question']}


def _turn(request, turn_id):
    turn = store.get_turn(ws(request), turn_id)
    if not turn:
        raise ConnectorError('not_found', 'No such question in this playground.', 404)
    return turn


def _event_out(row):
    return {'id': row['id'], 'run_id': row['run_id'], 'type': row['type'], 'data': row['data'],
            'ts': row['ts'].isoformat() if hasattr(row.get('ts'), 'isoformat') else None}


def _runs_out(turn_id):
    return [{'id': r['id'], 'position': r['position'], 'agent': r['agent'], 'model': r['model'], 'status': r['status'],
             'result': r['result'], 'error': r['error']} for r in store.turn_runs(turn_id)]


@app.get('/v1/turns/{turn_id}/events')
async def turn_events(request: Request, turn_id: str, after: int = 0):
    """Stored events after `after`, for reconnecting or when streaming is not available."""
    turn = await run_in_threadpool(_turn, request, turn_id)
    rows = await run_in_threadpool(store.turn_events, turn_id, after)
    return {'status': turn['status'], 'events': [_event_out(r) for r in rows], 'runs': await run_in_threadpool(_runs_out, turn_id)}


@app.get('/v1/turns/{turn_id}/stream')
def turn_stream(request: Request, turn_id: str, after: int = 0):
    """Server-sent events. Opening the stream also drives the turn's runs until one waits for consent, the time
    budget runs out, or all are done. The browser reopens it after a consent answer or a time-budget stop."""
    workspace = ws(request)
    _turn(request, turn_id)
    updates = queue.Queue()

    def work():
        try:
            outcome = runner.drive(workspace, turn_id, sink=updates.put)
        except Exception as exc:  # noqa: BLE001
            updates.put({'id': None, 'type': 'error', 'data': {'code': 'internal', 'message': str(exc), 'hi': runner.ERROR_HI}})
            outcome = 'error'
        updates.put(('outcome', outcome))

    def frame(item):
        return f'event: {item["type"]}\ndata: {json.dumps(_event_out(item) if item.get("ts") else item, ensure_ascii=False, default=str)}\n\n'

    def generate():
        last = after
        for row in store.turn_events(turn_id, after):
            last = row['id']
            yield frame(row)
        threading.Thread(target=work, daemon=True).start()
        outcome = None
        while outcome is None:
            try:
                item = updates.get(timeout=10)
            except queue.Empty:
                yield ': still working\n\n'
                continue
            if isinstance(item, tuple):
                outcome = item[1]
            elif item.get('id') is None or item['id'] > last:
                last = item['id'] or last
                yield frame(item)
        if outcome == 'busy':  # another call is driving this turn: follow its events for a while
            until = time.monotonic() + settings.RUN_BUDGET
            while time.monotonic() < until:
                time.sleep(0.8)
                for row in store.turn_events(turn_id, last):
                    last = row['id']
                    yield frame(row)
                if store.get_turn(workspace, turn_id)['status'] != 'running':
                    break
        for row in store.turn_events(turn_id, last):
            last = row['id']
            yield frame(row)
        status = store.get_turn(workspace, turn_id)['status']
        yield f'event: end\ndata: {json.dumps({"status": status, "last": last, "runs": _runs_out(turn_id)}, default=str)}\n\n'

    return StreamingResponse(generate(), media_type='text/event-stream',
                             headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


# --- consent -------------------------------------------------------------------------------

class ConsentAnswer(BaseModel):
    button: str | None = Field(default=None, pattern='^(yes|no|some|confirm|reject)$')
    text: str | None = Field(default=None, max_length=500)
    mode: str = Field(default='button', pattern='^(button|voice|text)$')
    granted: list[dict] | None = None


def _consent_language(request, consent_id):
    row = store.get_consent(consent_id, ws(request))
    if not row:
        raise ConnectorError('not_found', 'No such consent request.', 404)
    session = store.get_session(ws(request), row['session_id'])
    return (session['didi'].get('language') if session else None) or 'hi'


@app.post('/v1/consents/{consent_id}/answer')
async def consent_answer(request: Request, consent_id: str, body: ConsentAnswer):
    language = await run_in_threadpool(_consent_language, request, consent_id)
    return await run_in_threadpool(ledger.answer, ws(request), consent_id, body.model_dump(), language)


@app.delete('/v1/consents/{consent_id}')
async def consent_withdraw(request: Request, consent_id: str):
    return await run_in_threadpool(ledger.withdraw, ws(request), consent_id)


@app.get('/v1/sessions/{session_id}/consents')
async def session_consents(request: Request, session_id: str):
    await run_in_threadpool(_session, request, session_id)
    rows = await run_in_threadpool(store.session_consents, session_id)
    return {'consents': [{'id': r['id'], 'status': r['status'], 'purpose': r['purpose'], 'turn_id': r['turn_id'],
                          'registers': [i['register'] for i in r['items']], 'fields': sum(len(i['fields']) for i in r['items']),
                          'summary': (r['interpreter'] or {}).get('summary'), 'used': bool(r['used_at']),
                          'discarded': bool(r['discarded_at']), 'created_at': r['created_at'].isoformat()} for r in rows]}


class VocabularyEntry(BaseModel):
    phrase: str = Field(min_length=1, max_length=60)
    meaning: str = Field(pattern='^(yes|no|why)$')
    note: str = Field(default='', max_length=120)


@app.get('/v1/vocabulary')
async def get_vocabulary(request: Request):
    data = await run_in_threadpool(store.get_workspace_settings, ws(request))
    return {'custom': data.get('vocabulary') or {}, 'defaults': catalog.VOCABULARY}


@app.put('/v1/vocabulary')
async def put_vocabulary(request: Request, body: dict[str, list[VocabularyEntry]]):
    data = await run_in_threadpool(store.get_workspace_settings, ws(request))
    vocabulary = {lang: [e.model_dump() for e in entries] for lang, entries in body.items() if re.fullmatch(r'[a-z]{2,3}', lang)}
    await run_in_threadpool(store.save_workspace_settings, ws(request), {**data, 'vocabulary': vocabulary})
    return {'custom': vocabulary}


@app.put('/v1/sources/{source_id}/wording')
async def source_wording(request: Request, source_id: str, body: dict):
    source = await run_in_threadpool(sources.save_wording, ws(request), source_id, body)
    return sources.describe(source)


@app.put('/v1/sources/{source_id}/policy')
async def source_policy(request: Request, source_id: str, body: dict):
    source = await run_in_threadpool(sources.save_policy, ws(request), source_id, body)
    return sources.describe(source)


@app.get('/v1/department')
async def department_view(request: Request, owner: str = ''):
    return await run_in_threadpool(department.view, ws(request), owner)


# --- generated files -----------------------------------------------------------------------

@app.get('/v1/files/{file_id}')
async def download_file(request: Request, file_id: str):
    row = await run_in_threadpool(store.get_file, ws(request), file_id)
    if not row:
        return error('not_found', 'No such file in this playground.', 404)
    data = await run_in_threadpool(files.read, row['ref'])
    from fastapi.responses import Response
    return Response(data, media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                    headers={'Content-Disposition': f'attachment; filename="{files.safe_name(row["name"])}"'})


# --- decision tables -----------------------------------------------------------------------

@app.get('/v1/decisions')
async def list_decisions():
    return {'decisions': list(decisions.all_tables().values())}


@app.get('/v1/decisions/{decision_id}')
async def get_decision(decision_id: str):
    try:
        return decisions.table(decision_id)
    except decisions.DecisionError as exc:
        return error('not_found', str(exc), 404)


@app.post('/v1/decisions/{decision_id}/evaluate')
async def evaluate_decision(decision_id: str, body: dict):
    """Try a table with inputs you type, without touching anyone's records."""
    try:
        return decisions.evaluate(decision_id, body.get('inputs') or {})
    except (decisions.DecisionError, ValueError) as exc:
        return error('bad_inputs', str(exc), 422)


# --- models and agents ---------------------------------------------------------------------

@app.get('/v1/llm/models')
async def llm_models():
    ids = await run_in_threadpool(nvidia.list_models)
    probes = await run_in_threadpool(store.get_probes)

    def info(model, note=''):
        p = probes.get(model)
        return {'id': model, 'note': note, 'probe': p['result'] if p else None, 'probe_detail': p['detail'] if p else ''}

    recommended = {m for m, _ in nvidia.RECOMMENDED}
    return {'key_set': bool(settings.nvidia_key()), 'default': nvidia.DEFAULT_MODEL,
            'recommended': [info(m, note) for m, note in nvidia.RECOMMENDED if m in ids or not ids],
            'others': [info(m) for m in ids if m not in recommended],
            'agents': [{'id': key, 'name': module.NAME} for key, module in agents.AGENTS.items()]}


class ProbeRequest(BaseModel):
    model: str = Field(min_length=3, max_length=120)


@app.post('/v1/llm/probe')
async def llm_probe(body: ProbeRequest):
    result, detail = await run_in_threadpool(nvidia.probe, body.model)
    await run_in_threadpool(store.save_probe, body.model, result, detail)
    return {'model': body.model, 'probe': result, 'probe_detail': detail}


@app.get('/v1/audit')
async def audit(request: Request):
    events = await run_in_threadpool(store.recent_audit, ws(request))
    return {'events': [{**e, 'ts': e['ts'].isoformat()} for e in events]}


# --- Bhashini (bhashini_kit's API, unchanged) ------------------------------------------------

app.include_router(bhashini_routes.router)


@app.get('/healthz')
async def health():
    return {'ok': True}


# --- pages ---------------------------------------------------------------------------------

PAGES = {'/': 'index.html', '/connect': 'connect.html', '/login': 'login.html', '/chat': 'chat.html', '/decisions': 'decisions.html',
         '/department': 'department.html'}


def _page(name):
    async def serve():
        return FileResponse(settings.WEB / name, headers={'Cache-Control': 'no-cache'})
    return serve


for route, page in PAGES.items():
    app.add_api_route(route, _page(page), methods=['GET'], include_in_schema=False)

app.mount('/', StaticFiles(directory=settings.WEB), name='web')
