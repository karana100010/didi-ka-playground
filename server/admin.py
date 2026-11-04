"""Admin sign-in and the admin's view of one didi: her sessions, her answers, and charts from her registers.

The admin only views; she still answers her own consent cards. Admin reads do not use her consents, so they are
never silent: opening a didi needs a stated reason, every register read goes through gateway.admin_fetch (the
department's pause and purpose policy, her own rows only) and is logged with the admin's name and that reason,
where the department view shows it.

The credential is PLAYGROUND_ADMIN_USER / PLAYGROUND_ADMIN_PASSWORD. Locally, when no password is set, one is
generated once into data/local/admin.json (git-ignored); read it there. Hosted, admin sign-in is off until set.
"""
import base64
import hashlib
import hmac
import json
import secrets
import time
from collections import defaultdict

import gateway
import memory
import settings
import sources
from agents import earnings, projection
from connectors import ConnectorError
from core import store

TOKEN_TTL = 12 * 3600
KINDS = ('savings', 'loans', 'income', 'wages', 'projection', 'earnings')
MAX_POINTS = 24  # months shown in a history chart


class AdminError(ConnectorError):
    pass


# --- the credential and the token ----------------------------------------------------------

def credential():
    """(user, password), or (user, None) when admin sign-in is off."""
    if settings.ADMIN_PASSWORD:
        return settings.ADMIN_USER, settings.ADMIN_PASSWORD
    if settings.HOSTED:
        return settings.ADMIN_USER, None
    seed = settings.LOCAL / 'admin.json'
    if not seed.exists():
        seed.parent.mkdir(parents=True, exist_ok=True)
        seed.write_text(json.dumps({'user': settings.ADMIN_USER, 'password': secrets.token_urlsafe(12)}), encoding='utf-8')
    data = json.loads(seed.read_text(encoding='utf-8'))
    return data.get('user') or settings.ADMIN_USER, data['password']


def _sign(payload, password):
    key = hashlib.sha256(b'admin-token|' + password.encode()).digest()  # a new password ends every token
    return hmac.new(key, payload.encode(), hashlib.sha256).hexdigest()


def login(workspace, user, password):
    expected_user, expected = credential()
    if not expected:
        raise AdminError('admin_off', 'Admin sign-in is not set up. Set PLAYGROUND_ADMIN_PASSWORD.', 503)
    ok = hmac.compare_digest(user.encode(), expected_user.encode()) & hmac.compare_digest(password.encode(), expected.encode())
    if not ok:
        store.log(workspace, 'admin_login_failed', detail={'user': user[:64]})
        raise AdminError('admin_denied', 'That admin name or password is wrong.', 401)
    expires = int(time.time()) + TOKEN_TTL
    payload = f'{expected_user}|{expires}'
    store.log(workspace, 'admin_login', detail={'user': expected_user})
    token = base64.urlsafe_b64encode(payload.encode()).decode() + '.' + _sign(payload, expected)
    return {'token': token, 'user': expected_user, 'expires': expires}


def verify(token):
    """The admin's name for a valid, unexpired token, else None."""
    _, expected = credential()
    if not expected or '.' not in (token or ''):
        return None
    encoded, signature = token.rsplit('.', 1)
    try:
        payload = base64.urlsafe_b64decode(encoded.encode()).decode()
        user, expires = payload.rsplit('|', 1)
        expires = int(expires)
    except ValueError:
        return None
    if not hmac.compare_digest(signature, _sign(payload, expected)) or expires < time.time():
        return None
    return user


# --- her view ------------------------------------------------------------------------------

def _didi(workspace, didi_id):
    source, everyone = sources.didis(workspace)
    didi = next((d for d in everyone if str(d['didi_id']) == str(didi_id)), None)
    if not didi:
        raise AdminError('unknown_didi', f'There is no didi with id {didi_id} in the users table.', 404)
    # The same keys her login would carry, so the gateway scopes every read to her rows.
    users, key = source['setup']['users_table'], source['setup']['key_column']
    wanted = {key, 'household_id', 'shg_id'} | {s['setup'].get('join_key') for s in store.list_sources(workspace)
                                                if s['setup'].get('role') == 'linked'}
    columns = [c for c in source['tables'][users] if c in wanted]
    row = next(iter(sources.connector(source).rows(users, columns, where={key: didi['didi_id']})), {})
    didi['keys'] = {c: row.get(c) for c in columns if c != key} | {'didi_id': didi['didi_id']}
    return source, didi


def _charts(turn_id):
    """The charts her answer showed (the answering run's only)."""
    return [row['data'] for row in store.turn_events(turn_id) if row['type'] == 'chart' and row['data'].get('position') in (0, None)]


def view(workspace, user, didi_id, reason):
    """Open her view: what she sees in chat (her sessions and answers), with the admin's reason recorded."""
    source, didi = _didi(workspace, didi_id)
    store.log(workspace, 'admin_view', source_id=source['id'], didi_id=str(didi['didi_id']), detail={'admin': user, 'reason': reason})
    sessions = sorted((s for s in store.workspace_sessions(workspace) if s['didi_id'] == str(didi['didi_id'])),
                      key=lambda s: store.aware(s['created_at']))
    turns = memory.recall(workspace, didi['didi_id'], 50)
    for turn in turns:
        turn['charts'] = _charts(turn['turn_id']) if turn.get('turn_id') else []
    return {'admin': user, 'reason': reason, 'didi': {k: v for k, v in didi.items() if k != 'keys'},
            'sessions': [{'id': s['id'], 'created_at': store.aware(s['created_at']).isoformat()} for s in sessions],
            'turns': turns, 'kinds': list(KINDS)}


# --- charts --------------------------------------------------------------------------------

def _read(workspace, user, reason, didi, table, fields):
    """Her rows of `table`, through the gateway's admin path, or [] when no source has that table."""
    for source in store.list_sources(workspace):
        if table in source['tables'] and set(fields) <= set(source['tables'][table]):
            try:
                return gateway.admin_fetch(workspace, source, table, fields, didi, user, reason)
            except gateway.Blocked as blocked:
                raise AdminError(blocked.code, blocked.message, 403) from blocked
    return []


def _month(value):
    return str(value)[:7]


def _hindi(didi):
    return (didi.get('language') or 'hi') not in ('ta', 'en', 'te', 'kn', 'ml', 'bn', 'or', 'gu', 'pa', 'mr')


def _empty(didi, what):
    raise AdminError('no_rows', (f'{what}: इस दीदी के रजिस्टर में कुछ नहीं मिला।' if _hindi(didi) else f'{what}: nothing in her register.'), 404)


def chart(workspace, user, didi_id, kind, reason, months=12, extra=0):
    """A chart event's fields (title, spec, figures, assumptions), the same shape her answers emit."""
    if kind not in KINDS:
        raise AdminError('bad_kind', f'Pick one of: {", ".join(KINDS)}.', 422)
    _, didi = _didi(workspace, didi_id)
    hi = _hindi(didi)
    months = max(1, min(int(months or 12), 120))
    if kind in ('savings', 'projection'):
        rows = _read(workspace, user, reason, didi, 'savings', projection.FIELDS)
        if not rows:
            _empty(didi, 'Savings')
        if kind == 'savings':
            mine = sorted((r for r in rows if r.get('month')), key=lambda r: _month(r['month']))[-MAX_POINTS:]
            return {'kind': 'admin_savings', 'title': 'बचत रजिस्टर: कुल बचत' if hi else 'Savings register: total saved',
                    'spec': {'type': 'line', 'x': [_month(r['month']) for r in mine],
                             'series': [{'name': 'कुल बचत' if hi else 'Total saved', 'values': [r['cumulative'] or 0 for r in mine]}], 'y_label': '₹'},
                    'figures': {}, 'assumptions': [f'{len(mine)} महीने का रिकॉर्ड' if hi else f'{len(mine)} months of records']}
        facts = projection.her_facts(rows, str(didi.get('name') or ''))
        s = {'new_deposit': facts['monthly_deposit'] + max(int(extra or 0), 0), 'months': months, 'target': None,
             'annual_rate': 0, 'lump_sum': 0, 'months_defaulted': False}
        return projection.chart(facts, s, projection.project(facts, s))
    if kind == 'loans':
        rows = _read(workspace, user, reason, didi, 'loans', ['loan_id', 'source', 'rate_pct', 'emi', 'outstanding'])
        rows = [r for r in rows if (r.get('outstanding') or 0) > 0][:3]
        if not rows:
            _empty(didi, 'Loans')
        start = date_month()
        series, longest = [], 1
        for r in rows:  # what is left after each EMI, with the loan's interest
            left, values = r['outstanding'], [r['outstanding']]
            while left > 0 and len(values) <= 120:
                left = max(round(left * (1 + (r.get('rate_pct') or 0) / 1200) - (r.get('emi') or left)), 0)
                values.append(left)
            series.append({'name': f'{r["loan_id"]} · {r.get("source") or ""}'.strip(' ·'), 'values': values})
            longest = max(longest, len(values) - 1)
        for s in series:
            s['values'] += [0] * (longest + 1 - len(s['values']))
        return {'kind': 'admin_loans', 'title': 'कर्ज़ कब तक चुकेगा' if hi else 'Loans: what is left after each EMI',
                'spec': {'type': 'line', 'x': [projection.next_month(start, k) for k in range(longest + 1)], 'series': series, 'y_label': '₹'},
                'figures': {}, 'assumptions': [  # no figures.months: the card would read it as a projected balance
                    'हर महीने पूरी किस्त (EMI) भरें, ब्याज जोड़कर' if hi else 'Every EMI paid in full each month, with interest',
                    f'लगभग {longest} महीने में सब कर्ज़ चुक जाएगा' if hi else f'All paid off in about {longest} months']}
    if kind == 'income':
        rows = _read(workspace, user, reason, didi, 'dar', ['month', 'income', 'expense'])
        if not rows:
            _empty(didi, 'Income')
        by = defaultdict(lambda: [0, 0])
        for r in rows:
            by[_month(r['month'])][0] += r.get('income') or 0
            by[_month(r['month'])][1] += r.get('expense') or 0
        x = sorted(by)[-MAX_POINTS:]
        names = ('आमदनी', 'खर्च', 'बचत (आमदनी − खर्च)') if hi else ('Income', 'Costs', 'Left over')
        return {'kind': 'admin_income', 'title': 'आजीविका रजिस्टर: हर महीने' if hi else 'Livelihood register: each month',
                'spec': {'type': 'line', 'x': x, 'y_label': '₹', 'series': [
                    {'name': names[0], 'values': [by[m][0] for m in x]}, {'name': names[1], 'values': [by[m][1] for m in x]},
                    {'name': names[2], 'values': [by[m][0] - by[m][1] for m in x]}]},
                'figures': {}, 'assumptions': [f'{len(x)} महीने, सब काम जोड़कर' if hi else f'{len(x)} months, all activities added']}
    if kind == 'wages':
        rows = _read(workspace, user, reason, didi, 'wages_vbg', ['month', 'wages_paid', 'wages_pending'])
        if not rows:
            _empty(didi, 'Wages')
        rows = sorted(rows, key=lambda r: _month(r['month']))[-MAX_POINTS:]
        return {'kind': 'admin_wages', 'title': 'मज़दूरी: हर महीने' if hi else 'Wages each month',
                'spec': {'type': 'line', 'x': [_month(r['month']) for r in rows], 'y_label': '₹', 'series': [
                    {'name': 'मिली मज़दूरी' if hi else 'Paid', 'values': [r['wages_paid'] or 0 for r in rows]},
                    {'name': 'बाकी मज़दूरी' if hi else 'Pending', 'values': [r['wages_pending'] or 0 for r in rows]}]},
                'figures': {}, 'assumptions': []}
    # earnings: her enterprise's turnover and costs from the register, projected like her own business questions
    rows = _read(workspace, user, reason, didi, 'enterprise', ['type', 'monthly_turnover', 'monthly_cost'])
    if not rows:
        _empty(didi, 'Enterprise')
    e = rows[0]
    s = earnings.scenario({'follow_up': None, 'business': e.get('type'), 'monthly_income': e.get('monthly_turnover'),
                           'monthly_expense': e.get('monthly_cost'), 'monthly_profit': None, 'investment': 0, 'months': months})
    return earnings.chart(s, earnings.project(s, date_month()))


def date_month():
    return time.strftime('%Y-%m')
