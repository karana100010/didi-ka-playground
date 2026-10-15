"""Derivations: decision inputs computed by code from consented rows. Each takes {table: rows}.

A business cycle is six months (cycle 1 = Oct 2024–Mar 2025). It counts as sustained when its average
monthly net income is above ₹10,000, the monthly line in the Lakhpati Didi definition.
"""
SUSTAINED_MONTHLY = 10000


def _dar(rows_by_table):
    rows = rows_by_table.get('dar') or []
    if not rows:
        raise ValueError('dar rows are needed')
    return rows


def _net_by_month(rows):
    months = {}
    for r in rows:
        months[r['month']] = months.get(r['month'], 0) + (r.get('income') or 0) - (r.get('expense') or 0)
    return months


def dar_net_last_12_months(rows_by_table):
    months = _net_by_month(_dar(rows_by_table))
    return sum(months[m] for m in sorted(months)[-12:])


def dar_net_monthly_average(rows_by_table):
    months = _net_by_month(_dar(rows_by_table))
    recent = sorted(months)[-12:]
    return round(sum(months[m] for m in recent) / max(1, len(recent)))


def dar_cycles_sustained(rows_by_table):
    """Consecutive most recent business cycles whose average monthly net income is above ₹10,000."""
    rows = _dar(rows_by_table)
    cycles = {}
    for r in rows:
        entry = cycles.setdefault(r['cycle_no'], {'net': 0, 'months': set()})
        entry['net'] += (r.get('income') or 0) - (r.get('expense') or 0)
        entry['months'].add(r['month'])
    count = 0
    for cycle in sorted(cycles, reverse=True):
        entry = cycles[cycle]
        if entry['net'] / max(1, len(entry['months'])) > SUSTAINED_MONTHLY:
            count += 1
        else:
            break
    return count


def _meetings(rows_by_table):
    rows = rows_by_table.get('meetings') or []
    if not rows:
        raise ValueError('meetings rows are needed')
    return rows


def meetings_held_pct(rows_by_table):
    rows = _meetings(rows_by_table)
    return round(100 * sum(1 for r in rows if r.get('held') == 'yes') / len(rows))


def savings_regular_pct(rows_by_table):
    rows = _meetings(rows_by_table)
    return round(100 * sum(1 for r in rows if (r.get('savings_collected') or 0) > 0) / len(rows))


def internal_lending(rows_by_table):
    return 'yes' if any((r.get('loans_given') or 0) > 0 for r in _meetings(rows_by_table)) else 'no'


def repayment_on_time_pct(rows_by_table):
    rows = [r for r in _meetings(rows_by_table) if r.get('held') == 'yes']
    due = sum(r.get('repayments_due') or 0 for r in rows)
    return round(100 * sum(r.get('repayments_made') or 0 for r in rows) / due) if due else 100


def books_updated(rows_by_table):
    held = [r for r in _meetings(rows_by_table) if r.get('held') == 'yes']
    latest = max(held, key=lambda r: r.get('date') or '') if held else None
    return latest.get('books_updated') if latest else 'no'
