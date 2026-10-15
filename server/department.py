"""The department view: every access to one owner's tables, and that owner's policy.

Built from the audit log. It lists which fields were read, never their values; the department already holds
those in its own system.
"""
from datetime import datetime, timezone

import gateway
from consent import catalog, ledger
from core import store


def view(workspace, owner=''):
    sources = store.list_sources(workspace)
    owners = sorted({gateway.owner(s, t) for s in sources for t in s['tables']})
    if not owners:
        return {'owners': [], 'owner': '', 'stats': {}, 'log': [], 'sources': [], 'purposes': _purposes()}
    if owner not in owners:  # open on the department whose tables were read most recently
        latest = next((e for e in store.recent_audit(workspace, limit=50, kinds=('fetch', 'fetch_blocked'))
                       if e['source_id'] in {s['id'] for s in sources}), None)
        source = next((s for s in sources if latest and s['id'] == latest['source_id']), None)
        owner = gateway.owner(source, latest['detail'].get('table')) if source else owners[0]
    owned = {(s['id'], t) for s in sources for t in s['tables'] if gateway.owner(s, t) == owner}
    names = _didi_names(workspace)
    consents = {}
    log = []
    today = datetime.now(timezone.utc).date()
    stats = {'accesses_today': 0, 'didis': set(), 'refused': 0, 'blocked': 0}
    for event in store.recent_audit(workspace, limit=800, kinds=('fetch', 'fetch_blocked', 'consent_decided')):
        d = event['detail']
        if event['kind'] == 'consent_decided':
            row = consents.setdefault(d['consent_id'], store.get_consent(d['consent_id']))
            tables = [(i['source_id'], i['table']) for i in (row['items'] if row else [])]
            mine = [t for s, t in tables if (s, t) in owned]
            if d.get('decision') != 'denied' or not mine:
                continue
            stats['refused'] += 1
            log.append(_row(event, names, d.get('purpose'), mine, 'None', d['consent_id'], 'refused', 'Refused by the didi'))
            continue
        if (event['source_id'], d.get('table')) not in owned:
            continue
        if event['kind'] == 'fetch_blocked':
            stats['blocked'] += 1
            log.append(_row(event, names, d.get('purpose'), [d.get('table')], 'None', d.get('consent_id'), 'blocked',
                            f'Blocked: {d.get("message", d.get("reason"))}'))
            continue
        row = consents.setdefault(d['consent_id'], store.get_consent(d['consent_id']))
        discarded = bool(row and row['discarded_at'])
        if store.aware(event['ts']).date() == today:
            stats['accesses_today'] += 1
        stats['didis'].add(event['didi_id'])
        log.append(_row(event, names, d.get('purpose'), [d.get('table')], f'{d.get("table")}: {", ".join(d.get("fields", []))}',
                        d.get('consent_id'), 'ok', 'Used, then discarded' if discarded else 'Used'))
    stats['didis'] = len(stats['didis'])
    owned_sources = []
    for source in sources:
        tables = [t for t in source['tables'] if (source['id'], t) in owned]
        if tables:
            policy = {'paused': False, 'purposes': None, **((source['setup'] or {}).get('policy') or {})}
            owned_sources.append({'id': source['id'], 'name': source['name'], 'tables': [
                {'table': t, 'register': ledger.register(source, t, 'en')} for t in tables], 'policy': policy})
    return {'owners': owners, 'owner': owner, 'stats': stats, 'log': log[:200], 'sources': owned_sources,
            'purposes': _purposes()}


def _row(event, names, purpose, tables, fields, consent_id, kind, status):
    didi = str(event['didi_id'] or '')
    return {'ts': store.aware(event['ts']).isoformat(), 'didi_id': didi, 'didi': names.get(didi, ''),
            'purpose': catalog.PURPOSES.get(purpose, ('', purpose or ''))[1], 'tables': tables, 'fields': fields,
            'consent_id': consent_id, 'kind': kind, 'status': status}


def _purposes():
    return [{'id': k, 'en': en} for k, (_, en) in catalog.PURPOSES.items()]


def _didi_names(workspace):
    """Short names (Sunita D.) from logins, so the view needs no extra read of the users table."""
    names = {}
    for row in store.workspace_sessions(workspace):
        name = str(row['didi'].get('name') or '')
        parts = name.split()
        names[row['didi_id']] = f'{parts[0]} {parts[-1][0]}.' if len(parts) > 1 else name
    return names
