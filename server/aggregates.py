"""The admin's totals across didis and SHGs: counts, sums and averages from every register, computed by code.

Each table is read whole through gateway.admin_aggregate_fetch (the department's pause and purpose rules, purpose
admin_aggregate, every read logged with the admin's name and reason) and the rows never leave the server. Only
figures over groups of at least MIN_GROUP didis (or households) are returned: a smaller group is hidden, and when the
hidden groups add up to fewer than MIN_GROUP, the next smallest group is hidden too, so no one's figures can be
worked out by subtracting the shown groups from the total. The SHG registry is the groups' own records (meetings,
savings collected, loans given), shown per SHG when the SHG has at least MIN_GROUP members.
"""
from collections import defaultdict

import gateway
from admin import AdminError
from core import store
from decisions import engine

MIN_GROUP = 3
USER_FIELDS = ['didi_id', 'shg_id', 'household_id', 'district', 'state']
SHG_FIELDS = ['shg_id', 'shg_name', 'members', 'grade']
MEETING_FIELDS = ['shg_id', 'held', 'attendance', 'savings_collected', 'loans_given', 'repayments_due', 'repayments_made']
LAKHPATI = 'lakhpati_status'


def _metric(key, label, op, unit='', digits=0):
    return {'key': key, 'label': label, 'op': op, 'unit': unit, 'digits': digits}


# Each register: (table, title, fields, key column, unit counted, metrics, per-key figures from its rows)
def _savings(rows):
    rows = sorted(rows, key=lambda r: str(r['month']))
    return {'saved': rows[-1]['cumulative'] or 0, 'deposit': sum(r['amount'] or 0 for r in rows) / len(rows), 'months': len(rows)}


def _loans(rows):
    return {'loans': len(rows), 'borrowed': sum(r['amount'] or 0 for r in rows), 'outstanding': sum(r['outstanding'] or 0 for r in rows),
            'overdue': any((r['overdue_days'] or 0) > 0 for r in rows)}


def _last12(rows):
    months = sorted({str(r['month']) for r in rows})[-12:]
    return [r for r in rows if str(r['month']) in months]


def _dar(rows):
    recent = _last12(rows)
    income, expense = sum(r['income'] or 0 for r in recent), sum(r['expense'] or 0 for r in recent)
    return {'income': income, 'expense': expense, 'net': income - expense}


def _enterprise(rows):
    return {'enterprises': len(rows), 'turnover': sum(r['monthly_turnover'] or 0 for r in rows),
            'cost': sum(r['monthly_cost'] or 0 for r in rows),
            'profit': sum((r['monthly_turnover'] or 0) - (r['monthly_cost'] or 0) for r in rows),
            'assets': sum(r['assets_value'] or 0 for r in rows)}


def _wages(rows):
    return {'days': sum(r['days_worked'] or 0 for r in rows), 'paid': sum(r['wages_paid'] or 0 for r in rows),
            'pending': sum(r['wages_pending'] or 0 for r in rows)}


def _farms(rows):
    return {'farmers': len(rows), 'acres': sum(r['land_acres'] or 0 for r in rows),
            'irrigated': any(str(r['irrigated']).lower() == 'yes' for r in rows)}


REGISTERS = [
    ('savings', 'Savings register', ['didi_id', 'month', 'amount', 'cumulative'], 'didi_id', 'didis', [
        _metric('saved', 'Total saved', 'sum', '₹'), _metric('saved', 'Saved per didi (average)', 'avg', '₹'),
        _metric('deposit', 'Monthly deposit (average)', 'avg', '₹')], _savings),
    ('loans', 'Loan register', ['didi_id', 'amount', 'outstanding', 'overdue_days'], 'didi_id', 'didis', [
        _metric('loans', 'Loans', 'sum'), _metric('borrowed', 'Amount borrowed', 'sum', '₹'),
        _metric('outstanding', 'Outstanding', 'sum', '₹'), _metric('outstanding', 'Outstanding per didi (average)', 'avg', '₹'),
        _metric('overdue', 'Didis with an overdue loan', 'count')], _loans),
    ('dar', 'Digital Aajeevika Register (last 12 months)', ['didi_id', 'month', 'income', 'expense', 'cycle_no'], 'didi_id', 'didis', [
        _metric('income', 'Income', 'sum', '₹'), _metric('expense', 'Costs', 'sum', '₹'), _metric('net', 'Net income', 'sum', '₹'),
        _metric('net', 'Net income per didi (average)', 'avg', '₹')], _dar),
    ('enterprise', 'Enterprise register', ['didi_id', 'monthly_turnover', 'monthly_cost', 'assets_value'], 'didi_id', 'didis', [
        _metric('enterprises', 'Enterprises', 'sum'), _metric('turnover', 'Monthly turnover', 'sum', '₹'),
        _metric('cost', 'Monthly costs', 'sum', '₹'), _metric('profit', 'Monthly profit per didi (average)', 'avg', '₹'),
        _metric('assets', 'Assets', 'sum', '₹')], _enterprise),
    ('wages_vbg', 'VB–G RAM G wages', ['household_id', 'month', 'days_worked', 'wages_paid', 'wages_pending'], 'household_id', 'households', [
        _metric('days', 'Days worked', 'sum'), _metric('days', 'Days per household (average)', 'avg'),
        _metric('paid', 'Wages paid', 'sum', '₹'), _metric('pending', 'Wages pending', 'sum', '₹')], _wages),
    ('agristack', 'AgriStack farmer records', ['household_id', 'land_acres', 'irrigated'], 'household_id', 'households', [
        _metric('farmers', 'Farmer records', 'sum'), _metric('acres', 'Land (acres)', 'sum', '', 2),
        _metric('acres', 'Land per household (average, acres)', 'avg', '', 2), _metric('irrigated', 'Households with irrigation', 'count')], _farms),
]


# --- small-group suppression ---------------------------------------------------------------

def _figures(items, metrics):
    """{'n': how many, 'values': {label: figure}} over per-didi (or per-household) figures."""
    out = {}
    for m in metrics:
        values = [i[m['key']] for i in items]
        if m['op'] == 'sum':
            figure = sum(values)
        elif m['op'] == 'avg':
            figure = sum(values) / len(values) if values else 0
        else:  # count: how many have it
            figure = sum(1 for v in values if v)
        out[m['label']] = round(figure, m['digits']) if m['digits'] else round(figure)
    return {'n': len(items), 'values': out}


def overall(per_key, metrics):
    items = list(per_key.values())
    return _figures(items, metrics) if len(items) >= MIN_GROUP else {'n': len(items), 'hidden': True}


def breakdown(per_key, group_of, metrics, label_of=str):
    """Figures per group, with groups under MIN_GROUP hidden, and more hidden when the hidden ones could be worked out."""
    groups = defaultdict(list)
    for key, figures in per_key.items():
        groups[group_of(key) or 'Unknown'].append(figures)
    hidden = {g for g, items in groups.items() if len(items) < MIN_GROUP}
    while 0 < sum(len(groups[g]) for g in hidden) < MIN_GROUP and len(hidden) < len(groups):
        hidden.add(min((g for g in groups if g not in hidden), key=lambda g: (len(groups[g]), str(g))))
    rows = [{'group': label_of(g), **_figures(groups[g], metrics)} for g in sorted(groups, key=str) if g not in hidden]
    pooled = [f for g in hidden for f in groups[g]]
    return {'rows': rows, 'hidden_groups': len(hidden), 'groups': len(groups),
            'others': _figures(pooled, metrics) if rows and len(pooled) >= MIN_GROUP else None}


# --- reading -------------------------------------------------------------------------------

def _source(workspace, table, fields):
    return next((s for s in store.list_sources(workspace) if table in s['tables'] and set(fields) <= set(s['tables'][table])), None)


def _read(workspace, user, reason, table, fields):
    """(rows, None), (None, {'code', 'message'}) when the gateway refused, or (None, None) when no source has it."""
    source = _source(workspace, table, fields)
    if not source:
        return None, None
    try:
        return gateway.admin_aggregate_fetch(workspace, source, table, fields, user, reason), None
    except gateway.Blocked as blocked:
        return None, {'code': blocked.code, 'message': blocked.message}


def _by(rows, key):
    out = defaultdict(list)
    for r in rows:
        if r.get(key) not in (None, ''):
            out[str(r[key])].append(r)
    return out


# --- the view ------------------------------------------------------------------------------

def view(workspace, user, reason):
    store.log(workspace, 'admin_aggregate', detail={'admin': user, 'reason': reason})
    people, refused = _read(workspace, user, reason, 'users', USER_FIELDS)
    if refused:
        raise AdminError(refused['code'], refused['message'], 403)
    if people is None:
        raise AdminError('no_primary', 'No connected source has a users table with didi_id, shg_id, household_id, district and state.', 404)
    didis = {str(p['didi_id']): p for p in people}
    by_household = {str(p['household_id']): p for p in people if p.get('household_id')}
    shg_rows, shg_refused = _read(workspace, user, reason, 'shg', SHG_FIELDS)
    shgs = {str(s['shg_id']): s for s in shg_rows or []}

    def person(key, unit):
        return (didis if unit == 'didis' else by_household).get(key) or {}

    def groupings(unit):
        out = {'shg': (lambda k: person(k, unit).get('shg_id'),
                       lambda g: f'{shgs[g]["shg_name"]} ({g})' if g in shgs else str(g)),
               'district': (lambda k: ', '.join(filter(None, [person(k, unit).get('district'), person(k, unit).get('state')])), str)}
        if shgs:
            out['grade'] = (lambda k: (shgs.get(str(person(k, unit).get('shg_id'))) or {}).get('grade'), lambda g: f'Grade {g}')
        return out

    def summary(per_key, metrics, unit):
        return {'unit': unit, 'metrics': metrics, 'overall': overall(per_key, metrics),
                'breakdowns': {name: breakdown(per_key, group_of, metrics, label_of)
                               for name, (group_of, label_of) in groupings(unit).items()}}

    registers, dar = [], (None, None)
    for table, title, fields, key, unit, metrics, per in REGISTERS:
        source = _source(workspace, table, fields)
        entry = {'table': table, 'title': title, 'owner': gateway.owner(source, table) if source else None}
        rows, blocked = _read(workspace, user, reason, table, fields)
        if table == 'dar':
            dar = rows, blocked
        if blocked:
            registers.append({**entry, 'blocked': blocked})
            continue
        if rows is None:
            registers.append({**entry, 'missing': True})
            continue
        per_key = {k: per(rs) for k, rs in _by(rows, key).items()}
        registers.append({**entry, **summary(per_key, metrics, unit)})
    return {'admin': user, 'reason': reason, 'min_group': MIN_GROUP,
            'overview': {'didis': len(didis), 'shgs': len({p['shg_id'] for p in people if p.get('shg_id')}),
                         'districts': len({(p.get('district'), p.get('state')) for p in people if p.get('district')}),
                         'households': len(by_household)},
            'shg': _shg_registry(workspace, user, reason, people, shg_rows, shg_refused),
            'lakhpati': _lakhpati(*dar, summary),
            'registers': registers}


def _lakhpati(rows, blocked, summary):
    """How many didis each Lakhpati status covers, from the decision table run by code on each didi's DAR rows."""
    table = engine.table(LAKHPATI)
    if blocked:
        return {'blocked': blocked}
    if rows is None:
        return {'missing': True}
    per_didi = {}
    for didi_id, mine in _by(rows, 'didi_id').items():
        outcome = engine.evaluate(LAKHPATI, engine.inputs_from_rows(LAKHPATI, {'dar': mine}))['outcome']
        per_didi[didi_id] = {o: outcome == o for o in table['outcomes']}
    metrics = [_metric(o, label['en'].capitalize(), 'count') for o, label in table['outcomes'].items()]
    return {'decision': LAKHPATI, 'version': table['version'], **summary(per_didi, metrics, 'didis')}


def _shg_registry(workspace, user, reason, people, shg_rows, shg_refused):
    """Each SHG's own records: members, grade, meetings held, attendance, savings collected, loans given and repaid."""
    if shg_refused:
        return {'blocked': shg_refused}
    if shg_rows is None:
        return {'missing': True}
    meetings, meetings_refused = _read(workspace, user, reason, 'meetings', MEETING_FIELDS)
    places = defaultdict(set)
    for p in people:
        if p.get('shg_id') and p.get('district'):
            places[str(p['shg_id'])].add(', '.join(filter(None, [p['district'], p.get('state')])))
    held = _by([m for m in meetings or [] if str(m.get('held')).lower() == 'yes'], 'shg_id')
    recorded = _by(meetings or [], 'shg_id')

    def figures(ids):
        members = sum(int(shgs[i]['members'] or 0) for i in ids)
        rows = [m for i in ids for m in held.get(i, [])]
        due, made = sum(m['repayments_due'] or 0 for m in rows), sum(m['repayments_made'] or 0 for m in rows)
        attendance = [(m['attendance'] or 0) / int(shgs[m['shg_id']]['members']) for m in rows if shgs[m['shg_id']]['members']]
        out = {'shgs': len(ids), 'members': members, 'grades': dict(sorted(_count(shgs[i]['grade'] for i in ids).items()))}
        if meetings is not None:
            out.update({'meetings_recorded': sum(len(recorded.get(i, [])) for i in ids), 'meetings_held': len(rows),
                        'attendance_pct': round(100 * sum(attendance) / len(attendance)) if attendance else None,
                        'savings_collected': sum(m['savings_collected'] or 0 for m in rows),
                        'loans_given': sum(m['loans_given'] or 0 for m in rows), 'repayments_due': due, 'repayments_made': made,
                        'repayment_pct': round(100 * made / due) if due else None})
        return out

    shgs = {str(s['shg_id']): s for s in shg_rows}
    shown = [i for i in sorted(shgs) if int(shgs[i]['members'] or 0) >= MIN_GROUP]
    per_shg = [{'shg_id': i, 'shg_name': shgs[i]['shg_name'], 'grade': shgs[i]['grade'],
                'place': ' / '.join(sorted(places.get(i, []))), **figures([i])} for i in shown]
    districts = defaultdict(list)
    for i in shown:
        districts[' / '.join(sorted(places.get(i, []))) or 'Unknown'].append(i)
    return {'overall': figures(list(shgs)), 'per_shg': per_shg, 'hidden_shgs': len(shgs) - len(shown),
            'per_district': [{'group': d, **figures(ids)} for d, ids in sorted(districts.items())],
            'meetings_blocked': meetings_refused, 'meetings_missing': meetings is None and not meetings_refused}


def _count(values):
    out = defaultdict(int)
    for v in values:
        out[str(v)] += 1
    return out
