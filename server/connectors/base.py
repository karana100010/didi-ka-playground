from datetime import date, datetime
from decimal import Decimal

MAX_ROWS = 5000  # per read; the playground's registers are far smaller


class ConnectorError(Exception):
    def __init__(self, code, message, status=422):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


class Connector:
    """A read-only view of one source system."""

    def tables(self):
        """{table: [column, ...]} in source order."""
        raise NotImplementedError

    def rows(self, table, columns=None, where=None, limit=None):
        """Rows of `table` as dicts. `columns` limits the fields returned; `where` is {column: value} equality."""
        raise NotImplementedError

    def check(self, table, columns=None, where=None):
        schema = self.tables()
        if table not in schema:
            raise ConnectorError('unknown_table', f'There is no table called "{table}" in this source.')
        unknown = [c for c in [*(columns or []), *(where or {})] if c not in schema[table]]
        if unknown:
            raise ConnectorError('unknown_column', f'Table "{table}" has no column {", ".join(map(repr, unknown))}.')
        return list(columns) if columns else schema[table]


def clean(value):
    """A JSON-friendly cell value. Excel dates come back as datetimes at midnight; show them as dates."""
    if isinstance(value, datetime):
        return value.date().isoformat() if value.time() == datetime.min.time() else value.isoformat(timespec='seconds')
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, bytes):
        return f'<{len(value)} bytes>'
    return value
