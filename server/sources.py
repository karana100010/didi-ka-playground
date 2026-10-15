"""Connected sources: adding them, their setup (who the users are, how tables link), and logging in.

The first source set up as "primary" holds the users table; its rows are the didis in the login dropdown
(standing in for Aadhaar authentication). Other sources are "linked" to those didis by a key column,
didi_id or household_id, which is the GramStack identity layer in miniature.
"""
from pathlib import Path

from connectors import ConnectorError
from consent import catalog
from connectors import excel, gsheet, sql
from core import files, store

SAMPLES = [
    dict(kind='excel', name='lokos_mock.xlsx', file='lokos_mock.xlsx',
         setup=dict(role='primary', users_table='users', key_column='didi_id', owner_dept='LokOS (DAY-NRLM)')),
    dict(kind='sql', name='vbg_agri.sqlite', file='vbg_agri.sqlite',
         setup=dict(role='linked', join_key='household_id', owner_dept='VB–G RAM G',
                    table_owners={'agristack': 'AgriStack'})),
]
# Shown in the login preview when the users table has them; everything else stays out of the dropdown.
DIDI_FIELDS = ['name', 'name_local', 'language', 'asr_language', 'role', 'shg_id', 'household_id', 'village',
               'block', 'district', 'state']
EXCEL_TYPES = {'.xlsx'}
SQLITE_TYPES = {'.sqlite', '.sqlite3', '.db'}


# --- connecting ----------------------------------------------------------------------------

def connector(source):
    loc = source['location']
    if source['kind'] == 'excel':
        return excel.ExcelConnector(lambda: files.read(loc), f'{source["id"]}')
    if source['kind'] == 'gsheet':
        return gsheet.connector(loc['gsheet_id'])
    if source['kind'] == 'sql':
        if 'sql_url' in loc:
            return sql.SqlConnector(url=loc['sql_url'])
        return sql.SqlConnector(sqlite_path=files.local_path(loc, f'{source["id"]}.sqlite'))
    raise ConnectorError('unknown_kind', f'Unknown source kind {source["kind"]}.', 500)


def add_upload(workspace, filename, data):
    suffix = Path(filename or '').suffix.lower()
    if suffix in EXCEL_TYPES:
        tables = {name: t['columns'] for name, t in excel.parse(data).items()}
        kind, content_type = 'excel', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    elif suffix in SQLITE_TYPES:
        if not data.startswith(b'SQLite format 3\x00'):
            raise ConnectorError('not_sqlite', 'This is not a SQLite database file.')
        kind, content_type, tables = 'sql', 'application/vnd.sqlite3', None
    else:
        raise ConnectorError('bad_type', 'Upload an Excel workbook (.xlsx) or a SQLite file (.sqlite or .db).')
    source_id = store.new_id('src')
    ref = files.put(f'sources/{workspace}/{source_id}/{files.safe_name(filename)}', data, content_type)
    if tables is None:
        try:
            tables = sql.SqlConnector(sqlite_path=files.local_path(ref, f'{source_id}.sqlite')).tables()
        except ConnectorError:
            files.delete(ref)
            raise
    return _add(workspace, kind, files.safe_name(filename), ref, tables, source_id=source_id)


def add_gsheet(workspace, link):
    sid = gsheet.sheet_id(link)
    tables = gsheet.connector(sid).tables()
    return _add(workspace, 'gsheet', f'Google Sheet {sid[:8]}…', {'gsheet_id': sid}, tables)


def add_sql_url(workspace, url):
    sql.check_url(url)
    tables = sql.SqlConnector(url=url).tables()
    return _add(workspace, 'sql', sql.label(url).split('/')[-1] or 'Postgres', {'sql_url': url.strip()}, tables)


def add_samples(workspace):
    """The bundled mock registers, already set up. Replaces earlier samples in this workspace."""
    for source in store.list_sources(workspace):
        if 'bundled' in source['location']:
            store.remove_source(workspace, source['id'])
    for source in store.list_sources(workspace):  # the sample workbook becomes the primary source
        if source['setup'].get('role') == 'primary':
            store.save_setup(workspace, source['id'], {**source['setup'], 'role': 'linked', 'confirmed': False})
    added = []
    for sample in SAMPLES:
        ref = {'bundled': sample['file']}
        tables = connector({'id': f'sample-{sample["file"]}', 'kind': sample['kind'], 'location': ref}).tables()
        added.append(_add(workspace, sample['kind'], sample['name'], ref, tables,
                          setup={**sample['setup'], 'confirmed': True}))
    settings_ = store.get_workspace_settings(workspace)
    if not settings_.get('vocabulary'):  # the custom consent words from the design doc §4.4
        store.save_workspace_settings(workspace, {**settings_, 'vocabulary': catalog.SAMPLE_CUSTOM})
    return added


def _add(workspace, kind, name, location, tables, setup=None, source_id=None):
    return store.add_source(workspace, kind, name, location, tables, setup or _guess_setup(workspace, tables),
                            source_id=source_id)


def _guess_setup(workspace, tables):
    """A pre-filled setup the operator confirms on the connect screen."""
    has_primary = any(s['setup'].get('role') == 'primary' for s in store.list_sources(workspace))
    users = next((t for t in tables if t.lower() in ('users', 'members', 'didis')), None)
    if not has_primary and users:
        key = next((c for c in tables[users] if c.lower() in ('didi_id', 'member_id', 'id')), tables[users][0])
        return dict(role='primary', users_table=users, key_column=key, owner_dept='', confirmed=False)
    columns = {c for cols in tables.values() for c in cols}
    join = next((k for k in ('didi_id', 'household_id') if k in columns), '')
    return dict(role='linked', join_key=join, owner_dept='', confirmed=False)


# --- setup ---------------------------------------------------------------------------------

def save_setup(workspace, source_id, body):
    source = _get(workspace, source_id)
    tables = source['tables']
    role = body.get('role')
    owner = str(body.get('owner_dept') or '').strip()[:120]
    table_owners = {t: str(d).strip()[:120] for t, d in (body.get('table_owners') or {}).items() if t in tables and d}
    if role == 'primary':
        users_table, key = body.get('users_table'), body.get('key_column')
        if users_table not in tables:
            raise ConnectorError('bad_setup', 'Pick the table that lists the didis.')
        if key not in tables[users_table]:
            raise ConnectorError('bad_setup', f'Pick the column in "{users_table}" that identifies each didi.')
        for other in store.list_sources(workspace):
            if other['id'] != source_id and other['setup'].get('role') == 'primary':
                store.save_setup(workspace, other['id'], {**other['setup'], 'role': 'linked', 'confirmed': False})
        setup = dict(role='primary', users_table=users_table, key_column=key)
    elif role == 'linked':
        join = body.get('join_key')
        main = primary(workspace)
        if not main:
            raise ConnectorError('no_primary', 'Set up the source that holds the users first.')
        if join not in main['tables'][main['setup']['users_table']]:
            raise ConnectorError('bad_setup', 'Link by a column that is in the users table, such as didi_id or household_id.')
        if not any(join in cols for cols in tables.values()):
            raise ConnectorError('bad_setup', f'No table in this source has a "{join}" column.')
        setup = dict(role='linked', join_key=join)
    else:
        raise ConnectorError('bad_setup', 'Choose whether this source holds the users or links to them.')
    kept = {k: v for k, v in source['setup'].items() if k in ('fields', 'registers', 'consent', 'policy')}
    setup = {**kept, **setup, 'owner_dept': owner, 'table_owners': table_owners, 'confirmed': True}
    store.save_setup(workspace, source_id, setup)
    return {**source, 'setup': setup}


SENSITIVITY = ('low', 'medium', 'high')


def save_wording(workspace, source_id, body):
    """Register names, field labels and sensitivity, and the consent template, as the didi will hear them."""
    source = _get(workspace, source_id)
    tables = source['tables']
    registers = {t: {lang: str(v)[:80].strip() for lang, v in (names or {}).items() if lang in ('hi', 'en', 'ta') and str(v).strip()}
                 for t, names in (body.get('registers') or {}).items() if t in tables}
    fields = {}
    for table, columns in (body.get('fields') or {}).items():
        if table not in tables:
            continue
        for column, label in (columns or {}).items():
            if column not in tables[table]:
                continue
            entry = {lang: str(label.get(lang, ''))[:80].strip() for lang in ('hi', 'en', 'ta') if str(label.get(lang, '')).strip()}
            if label.get('sensitivity') in SENSITIVITY:
                entry['sensitivity'] = label['sensitivity']
            fields.setdefault(table, {})[column] = entry
    template = {lang: str(text)[:400].strip() for lang, text in ((body.get('consent') or {}).get('template') or {}).items()
                if lang in ('hi', 'en', 'ta') and str(text).strip()}
    for lang, text in template.items():
        if '{fields}' not in text:
            raise ConnectorError('bad_template', f'The {lang} consent template must include {{fields}}, so she hears what will be read.')
    setup = {**source['setup'], 'registers': registers, 'fields': fields, 'consent': {'template': template}}
    store.save_setup(workspace, source_id, setup, log_kind='source_wording')
    return {**source, 'setup': setup}


def save_policy(workspace, source_id, body):
    """The owning department's policy: whether the source is exposed, and which purposes may read which tables."""
    source = _get(workspace, source_id)
    purposes = body.get('purposes')
    if purposes is not None:
        purposes = {t: [p for p in (allowed or []) if p in catalog.PURPOSES] for t, allowed in purposes.items()
                    if t in source['tables']}
    policy = {'paused': bool(body.get('paused')), 'purposes': purposes}
    setup = {**source['setup'], 'policy': policy}
    store.save_setup(workspace, source_id, setup, log_kind='policy_changed')
    return {**source, 'setup': setup}


def primary(workspace):
    return next((s for s in store.list_sources(workspace)
                 if s['setup'].get('role') == 'primary' and s['setup'].get('confirmed')), None)


def _get(workspace, source_id):
    source = store.get_source(workspace, source_id)
    if not source:
        raise ConnectorError('not_found', 'No such source in this playground.', 404)
    return source


def remove(workspace, source_id):
    source = _get(workspace, source_id)
    if 'bundled' not in source['location'] and ('blob' in source['location'] or 'local' in source['location']):
        files.delete(source['location'])
    excel.forget(source_id)
    store.remove_source(workspace, source_id)


# --- describing ----------------------------------------------------------------------------

def describe(source, samples=False):
    """What the browser may see about a source. The location (file URL, database password) stays here."""
    loc = source['location']
    where = ('bundled sample' if 'bundled' in loc else 'Vercel Blob' if 'blob' in loc else 'local file' if 'local' in loc
             else f'Google Sheet {loc["gsheet_id"]}' if 'gsheet_id' in loc else sql.label(loc.get('sql_url', '')))
    setup = source['setup']
    out = dict(id=source['id'], kind=source['kind'], name=source['name'], where=where,
               setup={k: v for k, v in setup.items() if k not in ('fields', 'registers', 'consent', 'policy')},
               tables={t: {'columns': cols} for t, cols in source['tables'].items()},
               created_at=source['created_at'].isoformat())
    # What the didi will hear: saved wording over the defaults from consent/catalog.py.
    defaults = catalog.default_fields(source['tables'])
    out['wording'] = {
        'registers': {t: {**catalog.default_registers({t: []})[t], **(setup.get('registers') or {}).get(t, {})} for t in source['tables']},
        'fields': {t: {c: {**d, **((setup.get('fields') or {}).get(t, {}).get(c, {}))} for c, d in cols.items()}
                   for t, cols in defaults.items()},
        'template': {**catalog.DEFAULT_TEMPLATE, **((setup.get('consent') or {}).get('template') or {})},
    }
    out['policy'] = {'paused': False, 'purposes': None, **(setup.get('policy') or {})}
    if samples:
        conn = connector(source)
        for t in out['tables']:
            out['tables'][t]['sample'] = conn.rows(t, limit=5)
    return out


# --- didis and login -----------------------------------------------------------------------

def ensure_samples(workspace):
    """A playground with no sources starts with the sample registers connected, so log-in works straight away.
    Operators can remove them and connect their own on the connect page."""
    if not store.list_sources(workspace):
        add_samples(workspace)


def didis(workspace, source_id=None):
    if not source_id:
        ensure_samples(workspace)
    source = _get(workspace, source_id) if source_id else primary(workspace)
    if not source or source['setup'].get('role') != 'primary':
        raise ConnectorError('no_primary', 'Connect a source and pick its users table first.', 409)
    users, key = source['setup']['users_table'], source['setup']['key_column']
    conn = connector(source)
    columns = [key] + [c for c in DIDI_FIELDS if c in source['tables'][users] and c != key]
    shg_names = {}
    if 'shg_id' in columns and 'shg' in source['tables'] and {'shg_id', 'shg_name'} <= set(source['tables']['shg']):
        shg_names = {r['shg_id']: r['shg_name'] for r in conn.rows('shg', ['shg_id', 'shg_name'])}
    out = []
    for row in conn.rows(users, columns):
        didi = {'didi_id': row[key], **{c: row[c] for c in columns if c != key}}
        if didi.get('shg_id') in shg_names:
            didi['shg_name'] = shg_names[didi['shg_id']]
        didi.setdefault('name', str(row[key]))
        out.append(didi)
    store.log(workspace, 'users_listed', source_id=source['id'], detail={'count': len(out), 'fields': columns})
    return source, out


def login(workspace, didi_id, prefs):
    source, everyone = didis(workspace)
    didi = next((d for d in everyone if str(d['didi_id']) == str(didi_id)), None)
    if not didi:
        raise ConnectorError('unknown_didi', f'There is no didi with id {didi_id} in the users table.', 404)
    # The values that tie other tables to her (the gateway's filters). Taken from her row now, never from a model.
    users, key = source['setup']['users_table'], source['setup']['key_column']
    wanted = {key, 'household_id', 'shg_id'} | {s['setup'].get('join_key') for s in store.list_sources(workspace)
                                                if s['setup'].get('role') == 'linked'}
    columns = [c for c in source['tables'][users] if c in wanted]
    row = next(iter(connector(source).rows(users, columns, where={key: didi['didi_id']})), {})
    didi['keys'] = {c: row.get(c) for c in columns if c != key} | {'didi_id': didi['didi_id']}
    return store.add_session(workspace, source['id'], didi, prefs)
