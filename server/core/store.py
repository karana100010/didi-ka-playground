"""The core store: what the GramStack layer itself keeps. Postgres when DATABASE_URL is set (always on Vercel);
locally, without it, a SQLite file under data/local/. Tables are created on first use.

It never keeps source values for longer than one run: run_steps (the checkpoint that lets a run pause for
consent and resume in another function call) can hold fetched values, and is deleted when the run ends.
"""
import json
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import (JSON, Boolean, Column, DateTime, Index, Integer, MetaData, String, Table, Text,
                        create_engine, delete, func, insert, select, update)
from sqlalchemy.exc import IntegrityError

import settings


class NotConfigured(Exception):
    pass


metadata = MetaData()

# A workspace is one browser's playground: its connected sources and sessions. The id is created in the
# browser and sent as X-Workspace, so people sharing one deployment don't see each other's data.
sources = Table(
    'sources', metadata,
    Column('id', String(24), primary_key=True),
    Column('workspace', String(64), nullable=False),
    Column('kind', String(16), nullable=False),          # excel | gsheet | sql
    Column('name', String(200), nullable=False),
    Column('location', JSON, nullable=False),            # {bundled|blob|local|gsheet_id|sql_url}; never sent to the browser
    Column('tables', JSON, nullable=False),              # {table: [columns]}
    Column('setup', JSON, nullable=False),               # role, keys, owner, field labels, consent wording, policy
    Column('created_at', DateTime(timezone=True), nullable=False),
    Index('ix_sources_workspace', 'workspace'),
)

sessions = Table(
    'sessions', metadata,
    Column('id', String(24), primary_key=True),
    Column('workspace', String(64), nullable=False),
    Column('source_id', String(24), nullable=False),
    Column('didi_id', String(64), nullable=False),
    Column('didi', JSON, nullable=False),                # the users row shown at login (name, language, ...)
    Column('prefs', JSON, nullable=False),               # model, agent, compare_with, language_mode, steps_view, speak
    Column('created_at', DateTime(timezone=True), nullable=False),
)

turns = Table(
    'turns', metadata,
    Column('id', String(24), primary_key=True),
    Column('workspace', String(64), nullable=False),
    Column('session_id', String(24), nullable=False),
    Column('question', Text, nullable=False),
    Column('mode', String(8), nullable=False),           # text | voice
    Column('status', String(16), nullable=False),        # running | waiting | done | error
    Column('answer', Text),                              # the main run's answer, for the next question's context
    Column('created_at', DateTime(timezone=True), nullable=False),
    Index('ix_turns_session', 'session_id', 'created_at'),
)

runs = Table(
    'runs', metadata,
    Column('id', String(24), primary_key=True),
    Column('turn_id', String(24), nullable=False),
    Column('position', Integer, nullable=False),         # 0 = the agent answering her, 1+ = compare runs
    Column('agent', String(32), nullable=False),
    Column('model', String(120), nullable=False),
    Column('status', String(16), nullable=False),        # queued | running | waiting | done | error
    Column('lease_until', DateTime(timezone=True)),      # set while one function call drives the run
    Column('result', JSON),                              # {answer, metrics}
    Column('error', JSON),
    Column('updated_at', DateTime(timezone=True), nullable=False),
    Index('ix_runs_turn', 'turn_id', 'position'),
)

run_steps = Table(  # the checkpoint; deleted when the run ends
    'run_steps', metadata,
    Column('run_id', String(24), primary_key=True),
    Column('seq', Integer, primary_key=True),
    Column('kind', String(16), nullable=False),          # llm | tool | consent
    Column('digest', String(64), nullable=False),
    Column('response', JSON, nullable=False),
)

events = Table(
    'events', metadata,
    Column('id', Integer, primary_key=True, autoincrement=True),
    Column('turn_id', String(24), nullable=False),
    Column('run_id', String(24), nullable=False),
    Column('run_seq', Integer, nullable=False),          # nth event of this run; a replayed run skips ones it already wrote
    Column('ts', DateTime(timezone=True), nullable=False),
    Column('type', String(32), nullable=False),
    Column('data', JSON, nullable=False),
    Index('ux_events_run_seq', 'run_id', 'run_seq', unique=True),
    Index('ix_events_turn', 'turn_id', 'id'),
)

consents = Table(
    'consents', metadata,
    Column('id', String(24), primary_key=True),
    Column('workspace', String(64), nullable=False),
    Column('session_id', String(24), nullable=False),
    Column('turn_id', String(24), nullable=False),
    Column('run_id', String(24), nullable=False),
    Column('didi_id', String(64), nullable=False),
    Column('purpose', JSON, nullable=False),             # {id, en, hi}
    Column('items', JSON, nullable=False),               # [{source_id, table, fields, labels, source_name}]
    Column('statement', JSON, nullable=False),           # {hi, en}: what she was told
    Column('status', String(16), nullable=False),        # pending | granted | partial | denied | withdrawn
    Column('granted', JSON, nullable=False),             # [{source_id, table, fields}]
    Column('refused', JSON, nullable=False),             # topics or fields she said no to
    Column('answer', JSON),                              # {mode, button?, text?}
    Column('interpreter', JSON),                         # {by: button|vocabulary|llm, model?, confidence?}
    Column('created_at', DateTime(timezone=True), nullable=False),
    Column('decided_at', DateTime(timezone=True)),
    Column('used_at', DateTime(timezone=True)),
    Column('discarded_at', DateTime(timezone=True)),
    Index('ix_consents_session', 'session_id', 'created_at'),
)

audit = Table(
    'audit_events', metadata,
    Column('id', String(24), primary_key=True),
    Column('ts', DateTime(timezone=True), nullable=False),
    Column('workspace', String(64), nullable=False),
    Column('kind', String(32), nullable=False),          # source_added | login | fetch | fetch_blocked | consent_* | ...
    Column('source_id', String(24)),
    Column('didi_id', String(64)),
    Column('detail', JSON, nullable=False),
    Index('ix_audit_workspace_ts', 'workspace', 'ts'),
)

model_probes = Table(
    'model_probes', metadata,
    Column('model', String(120), primary_key=True),
    Column('result', String(8), nullable=False),         # tools | text | error
    Column('detail', String(300), nullable=False),
    Column('checked_at', DateTime(timezone=True), nullable=False),
)

llm_cache = Table(  # the demo cache: replays a rehearsed question if the API is throttled
    'llm_cache', metadata,
    Column('key', String(64), primary_key=True),
    Column('model', String(120), nullable=False),
    Column('response', JSON, nullable=False),
    Column('created_at', DateTime(timezone=True), nullable=False),
)

files_table = Table(  # generated deliverables, e.g. business-plan workbooks (the file itself is in Blob or local disk)
    'files', metadata,
    Column('id', String(24), primary_key=True),
    Column('workspace', String(64), nullable=False),
    Column('session_id', String(24), nullable=False),
    Column('turn_id', String(24), nullable=False),
    Column('kind', String(24), nullable=False),
    Column('name', String(200), nullable=False),
    Column('ref', JSON, nullable=False),
    Column('summary', JSON, nullable=False),
    Column('created_at', DateTime(timezone=True), nullable=False),
)

workspace_settings = Table(
    'workspace_settings', metadata,
    Column('workspace', String(64), primary_key=True),
    Column('data', JSON, nullable=False),                # consent vocabulary, demo mode
)

_engine = None


def engine():
    global _engine
    if _engine is None:
        url = settings.DATABASE_URL
        if url:
            url = url.replace('postgres://', 'postgresql://', 1).replace('postgresql://', 'postgresql+psycopg://', 1)
            _engine = create_engine(url, pool_pre_ping=True, pool_size=3, max_overflow=4)
        elif settings.HOSTED:
            raise NotConfigured('DATABASE_URL is not set. Add a Postgres database (Neon) to the Vercel project.')
        else:
            settings.LOCAL.mkdir(parents=True, exist_ok=True)
            _engine = create_engine(f'sqlite:///{settings.LOCAL / "core.sqlite"}',
                                    connect_args={'check_same_thread': False, 'timeout': 15})
        metadata.create_all(_engine)
    return _engine


def kind():
    return 'postgres' if settings.DATABASE_URL else ('missing' if settings.HOSTED else 'sqlite')


def new_id(prefix):
    return f'{prefix}_{secrets.token_hex(6)}'


def now():
    return datetime.now(timezone.utc)


def aware(value):
    """SQLite hands back naive datetimes; treat them as UTC."""
    return value.replace(tzinfo=timezone.utc) if value is not None and value.tzinfo is None else value


def _row(row):
    return dict(row._mapping) if row is not None else None


def _one(query):
    with engine().connect() as db:
        return _row(db.execute(query).first())


def _all(query):
    with engine().connect() as db:
        return [_row(r) for r in db.execute(query).all()]


def _write(statement):
    with engine().begin() as db:
        return db.execute(statement).rowcount


# --- sources -------------------------------------------------------------------------------

def add_source(workspace, kind_, name, location, tables, setup, source_id=None):
    row = dict(id=source_id or new_id('src'), workspace=workspace, kind=kind_, name=name, location=location,
               tables=tables, setup=setup, created_at=now())
    _write(insert(sources).values(**row))
    log(workspace, 'source_added', source_id=row['id'], detail={'kind': kind_, 'name': name, 'tables': list(tables)})
    return row


def get_source(workspace, source_id):
    return _one(select(sources).where(sources.c.id == source_id, sources.c.workspace == workspace))


def list_sources(workspace):
    return _all(select(sources).where(sources.c.workspace == workspace).order_by(sources.c.created_at))


def save_setup(workspace, source_id, setup, log_kind='source_setup'):
    _write(update(sources).where(sources.c.id == source_id, sources.c.workspace == workspace).values(setup=setup))
    if log_kind:
        log(workspace, log_kind, source_id=source_id, detail={k: v for k, v in setup.items() if k not in ('fields',)})


def remove_source(workspace, source_id):
    gone = _write(delete(sources).where(sources.c.id == source_id, sources.c.workspace == workspace))
    if gone:
        log(workspace, 'source_removed', source_id=source_id)
    return bool(gone)


# --- sessions ------------------------------------------------------------------------------

def add_session(workspace, source_id, didi, prefs):
    row = dict(id=new_id('ses'), workspace=workspace, source_id=source_id, didi_id=str(didi['didi_id']), didi=didi,
               prefs=prefs, created_at=now())
    _write(insert(sessions).values(**row))
    log(workspace, 'login', source_id=source_id, didi_id=row['didi_id'], detail={'session_id': row['id']})
    return row


def get_session(workspace, session_id):
    return _one(select(sessions).where(sessions.c.id == session_id, sessions.c.workspace == workspace))


def workspace_sessions(workspace):
    return _all(select(sessions).where(sessions.c.workspace == workspace))


def save_prefs(workspace, session_id, prefs):
    _write(update(sessions).where(sessions.c.id == session_id, sessions.c.workspace == workspace).values(prefs=prefs))


# --- turns, runs, checkpoints, events ------------------------------------------------------

def add_turn(workspace, session_id, question, mode, run_specs):
    turn = dict(id=new_id('turn'), workspace=workspace, session_id=session_id, question=question, mode=mode,
                status='running', answer=None, created_at=now())
    run_rows = [dict(id=new_id('run'), turn_id=turn['id'], position=i, agent=agent, model=model, status='queued',
                     lease_until=None, result=None, error=None, updated_at=now())
                for i, (agent, model) in enumerate(run_specs)]
    with engine().begin() as db:
        db.execute(insert(turns).values(**turn))
        db.execute(insert(runs), run_rows)
    return turn, run_rows


def get_turn(workspace, turn_id):
    return _one(select(turns).where(turns.c.id == turn_id, turns.c.workspace == workspace))


def session_turns(session_id, limit=20):
    rows = _all(select(turns).where(turns.c.session_id == session_id).order_by(turns.c.created_at.desc()).limit(limit))
    return list(reversed(rows))


def set_turn(turn_id, **values):
    _write(update(turns).where(turns.c.id == turn_id).values(**values))


def turn_runs(turn_id):
    return _all(select(runs).where(runs.c.turn_id == turn_id).order_by(runs.c.position))


def get_run(run_id):
    return _one(select(runs).where(runs.c.id == run_id))


def set_run(run_id, **values):
    _write(update(runs).where(runs.c.id == run_id).values(updated_at=now(), **values))


def lease_run(run_id, seconds):
    """Claim the run for this function call. False if another call is driving it."""
    t = now()
    with engine().begin() as db:
        claimed = db.execute(update(runs).where(
            runs.c.id == run_id, runs.c.status.in_(('queued', 'running')),
            (runs.c.lease_until.is_(None)) | (runs.c.lease_until < t),
        ).values(lease_until=t + timedelta(seconds=seconds), status='running', updated_at=t)).rowcount
    return claimed == 1


def release_run(run_id):
    _write(update(runs).where(runs.c.id == run_id).values(lease_until=None))


def recorded_steps(run_id):
    return {r['seq']: r for r in _all(select(run_steps).where(run_steps.c.run_id == run_id))}


def record_step(run_id, seq, kind_, digest, response):
    try:
        _write(insert(run_steps).values(run_id=run_id, seq=seq, kind=kind_, digest=digest,
                                        response=json.loads(json.dumps(response, default=str))))
    except IntegrityError:
        pass  # already recorded by an earlier call of this run


def purge_steps(run_id):
    _write(delete(run_steps).where(run_steps.c.run_id == run_id))


def event_count(run_id):
    with engine().connect() as db:
        return db.execute(select(func.count()).select_from(events).where(events.c.run_id == run_id)).scalar() or 0


def add_event(turn_id, run_id, run_seq, type_, data):
    row = dict(turn_id=turn_id, run_id=run_id, run_seq=run_seq, ts=now(), type=type_,
               data=json.loads(json.dumps(data, default=str)))
    try:
        with engine().begin() as db:
            result = db.execute(insert(events).values(**row))
            row['id'] = result.inserted_primary_key[0]
        return row
    except IntegrityError:
        return None  # replayed run: already written


def turn_events(turn_id, after=0):
    return _all(select(events).where(events.c.turn_id == turn_id, events.c.id > after).order_by(events.c.id))


# --- consents ------------------------------------------------------------------------------

def add_consent(row):
    _write(insert(consents).values(**row))
    return row


def get_consent(consent_id, workspace=None):
    query = select(consents).where(consents.c.id == consent_id)
    if workspace:
        query = query.where(consents.c.workspace == workspace)
    return _one(query)


def run_consents(run_id):
    return _all(select(consents).where(consents.c.run_id == run_id).order_by(consents.c.created_at))


def turn_consents(turn_id):
    return _all(select(consents).where(consents.c.turn_id == turn_id).order_by(consents.c.created_at))


def session_consents(session_id):
    return _all(select(consents).where(consents.c.session_id == session_id).order_by(consents.c.created_at.desc()))


def set_consent(consent_id, **values):
    _write(update(consents).where(consents.c.id == consent_id).values(**values))


# --- generated files -----------------------------------------------------------------------

def add_file(row):
    _write(insert(files_table).values(**row))
    return row


def get_file(workspace, file_id):
    return _one(select(files_table).where(files_table.c.id == file_id, files_table.c.workspace == workspace))


# --- workspace settings ----------------------------------------------------------------------

def get_workspace_settings(workspace):
    row = _one(select(workspace_settings).where(workspace_settings.c.workspace == workspace))
    return row['data'] if row else {}


def save_workspace_settings(workspace, data):
    with engine().begin() as db:
        if db.execute(update(workspace_settings).where(workspace_settings.c.workspace == workspace)
                      .values(data=data)).rowcount == 0:
            db.execute(insert(workspace_settings).values(workspace=workspace, data=data))


# --- models --------------------------------------------------------------------------------

def get_probes():
    return {r['model']: r for r in _all(select(model_probes))}


def save_probe(model, result, detail):
    with engine().begin() as db:
        db.execute(delete(model_probes).where(model_probes.c.model == model))
        db.execute(insert(model_probes).values(model=model, result=result, detail=str(detail)[:300], checked_at=now()))


def cache_get(key):
    row = _one(select(llm_cache).where(llm_cache.c.key == key))
    return row['response'] if row else None


def cache_put(key, model, response):
    try:
        _write(insert(llm_cache).values(key=key, model=model, response=response, created_at=now()))
    except IntegrityError:
        pass


# --- audit ---------------------------------------------------------------------------------

def log(workspace, kind_, source_id=None, didi_id=None, detail=None):
    _write(insert(audit).values(id=new_id('ev'), ts=now(), workspace=workspace, kind=kind_, source_id=source_id,
                                didi_id=didi_id, detail=json.loads(json.dumps(detail or {}, default=str))))


def recent_audit(workspace, limit=50, source_id=None, kinds=None):
    query = select(audit).where(audit.c.workspace == workspace)
    if source_id:
        query = query.where(audit.c.source_id == source_id)
    if kinds:
        query = query.where(audit.c.kind.in_(kinds))
    return _all(query.order_by(audit.c.ts.desc()).limit(limit))


def cleanup(hours=2):
    """Drop checkpoints of runs abandoned mid-way (she never answered the consent prompt)."""
    cutoff = now() - timedelta(hours=hours)
    with engine().begin() as db:
        stale = [r[0] for r in db.execute(select(runs.c.id).where(runs.c.status.in_(('waiting', 'running', 'queued')),
                                                                   runs.c.updated_at < cutoff)).all()]
        if stale:
            db.execute(delete(run_steps).where(run_steps.c.run_id.in_(stale)))
            db.execute(update(runs).where(runs.c.id.in_(stale)).values(status='error', error={'code': 'expired'}))
    return len(stale)
