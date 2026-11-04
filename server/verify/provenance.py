"""Gate B: where every number came from. Each figure maps to the (register, field, period) it was read or computed
from, so an answer can be checked for naming the right register and the right period, not just a known number.

Periods: 'month' (one month's row), 'item' (one loan, one enterprise), 'latest' (the latest month), 'current'
(a balance now), 'year' (last 12 months), 'monthly_average', 'all' (every row read), 'cycle' (a business cycle),
'computed' (calculate, a decision, a plan or projection).
"""

PERIOD_KEYS = (('last_12_months_monthly_average_', 'monthly_average'), ('last_12_months_', 'year'), ('total_', 'all'))


def _add(facts, value, origin):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return
    facts.setdefault(round(abs(float(value)), 2), set()).add(origin)


def from_rows(table, rows, totals):
    """Provenance of her fetched rows and of their exact totals (from facts.summarise)."""
    facts = {}
    for row in rows:
        period = 'month' if row.get('month') else 'item'
        for field, value in row.items():
            if field not in ('month', 'cycle_no'):
                _add(facts, value, (table, field, period))
    for key, value in totals.items():
        if key == 'latest_month' and isinstance(value, dict):
            for field, v in value.items():
                _add(facts, v, (table, field, 'latest'))
        elif key == 'current' and isinstance(value, dict):
            for field, v in value.items():
                _add(facts, v, (table, field, 'current'))
        elif key == 'business_cycles' and isinstance(value, dict):
            for entry in value.values():
                for field, v in entry.items():
                    _add(facts, v, (table, field, 'cycle'))
        elif key == 'per_item' and isinstance(value, list):
            for entry in value:
                for field, v in entry.items():
                    _add(facts, v, (table, field, 'item'))
        else:
            for prefix, period in PERIOD_KEYS:
                if key.startswith(prefix):
                    _add(facts, value, (table, key[len(prefix):], period))
                    break
    return facts


def computed(tool, result, table=''):
    """Every number in a tool's result (calculate, decide, a plan, a projection) as computed by that tool."""
    facts = {}

    def walk(value):
        if isinstance(value, dict):
            for v in value.values():
                walk(v)
        elif isinstance(value, (list, tuple)):
            for v in value:
                walk(v)
        else:
            _add(facts, value, (table or tool, tool, 'computed'))

    walk(result)
    return facts


def merge(into, more):
    for value, origins in more.items():
        into.setdefault(value, set()).update(origins)
    return into


def lookup(facts, value, tolerance=0.005):
    """The origins of a figure in an answer (same tolerance as facts.grounded)."""
    hits = set()
    for known, origins in facts.items():
        if abs(known - value) <= max(1.0, tolerance * abs(known)):
            hits |= origins
    return hits
