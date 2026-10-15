"""Turning fetched rows into exact figures, and checking that an answer's figures come from them.

Plan-and-Execute and ReWOO hand the writer these figures instead of raw rows, so it never has to add up
numbers itself. The self-check uses numbers_in() and grounded() on every answer.
"""
import re

SMALL = 100  # numbers below this (months, counts, cycles) are not checked
# Running balances and stock counts: summing them over months is meaningless, so they are reported as latest values.
STOCK = ('cumulative', 'outstanding', 'balance', 'units', 'assets_value', 'members')


def summarise(table, rows):
    """Exact totals and averages for a table's numeric columns, per year and per business cycle where possible."""
    out = {'table': table, 'rows': len(rows)}
    if not rows:
        return out
    columns = list(rows[0])
    numeric = [c for c in columns if c not in ('cycle_no', 'month', 'tenure_months', 'rate_pct', 'overdue_days', *STOCK)
               and all(isinstance(r.get(c), (int, float)) or r.get(c) is None for r in rows)
               and any(isinstance(r.get(c), (int, float)) for r in rows)]
    months = sorted({r['month'] for r in rows if r.get('month')})
    last12 = set(months[-12:])
    if months:
        out['months'] = f'{months[0]} to {months[-1]}'
        latest = [r for r in rows if r.get('month') == months[-1]]
        out['latest_month'] = {'month': months[-1], **{c: sum(r.get(c) or 0 for r in latest) if c in numeric else latest[-1].get(c)
                                                       for c in columns if c != 'month'}}
    stocks = [c for c in columns if c in STOCK and any(isinstance(r.get(c), (int, float)) for r in rows)]
    if stocks and not months:
        out['current'] = {c: rows[-1].get(c) for c in stocks}
    has_net = 'income' in numeric and 'expense' in numeric

    def total(column, keep=lambda r: True):
        return sum(r.get(column) or 0 for r in rows if keep(r))

    for column in numeric:
        out[f'total_{column}'] = total(column)
        if months:
            out[f'last_12_months_{column}'] = total(column, lambda r: r.get('month') in last12)
            out[f'last_12_months_monthly_average_{column}'] = round(out[f'last_12_months_{column}'] / max(1, len(last12)))
    if has_net:
        out['total_net_income'] = out['total_income'] - out['total_expense']
        if months:
            out['last_12_months_net_income'] = out['last_12_months_income'] - out['last_12_months_expense']
            out['last_12_months_monthly_average_net_income'] = round(out['last_12_months_net_income'] / max(1, len(last12)))
    if 'cycle_no' in columns:
        cycles = sorted({r['cycle_no'] for r in rows if r.get('cycle_no') is not None})
        out['business_cycles'] = {}
        for cycle in cycles:
            in_cycle = [r for r in rows if r.get('cycle_no') == cycle]
            entry = {c: sum(r.get(c) or 0 for r in in_cycle) for c in numeric}
            if has_net:
                entry['net_income'] = entry['income'] - entry['expense']
            span = sorted({r['month'] for r in in_cycle if r.get('month')})
            if span:
                entry['months'] = f'{span[0]} to {span[-1]}'
            out['business_cycles'][str(cycle)] = entry
    if len(rows) <= 12:
        out['rows'] = rows
    return out


def numbers(value):
    """Every number inside a JSON-like value (rows, summaries, calculation results)."""
    found = set()
    if isinstance(value, bool):
        return found
    if isinstance(value, (int, float)):
        found.add(float(value))
    elif isinstance(value, dict):
        for v in value.values():
            found |= numbers(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            found |= numbers(v)
    return found


_DEVANAGARI = str.maketrans('०१२३४५६७८९', '0123456789')
_NUMBER = re.compile(r'(?<![\w.])(\d{1,3}(?:,\d{2,3})+|\d+)(?:\.(\d+))?')


def numbers_in(text):
    """Figures in an answer (Indian or Western digit grouping, Devanagari digits), ignoring small counts."""
    out = []
    for match in _NUMBER.finditer((text or '').translate(_DEVANAGARI)):
        value = float(match.group(1).replace(',', '') + ('.' + match.group(2) if match.group(2) else ''))
        if value >= SMALL:
            out.append(value)
    return out


def grounded(text, known):
    """(grounded, total, ungrounded) for the answer's figures against the known numbers."""
    found = numbers_in(text)
    missing = [n for n in found if not any(abs(n - k) <= max(1.0, 0.005 * abs(k)) for k in known)]
    return len(found) - len(missing), len(found), missing
