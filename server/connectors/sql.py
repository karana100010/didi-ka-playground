"""SQL databases: Postgres by URL, or a SQLite file that was uploaded or bundled. Read-only either way.

SQLite opens with mode=ro. Postgres runs every read inside SET TRANSACTION READ ONLY with a statement
timeout, which also works through Neon's pooled (PgBouncer) connections. Only postgres:// URLs are
accepted: a sqlite:/// URL would let anyone read files on the server.
"""
import sqlite3

from sqlalchemy import column, create_engine, inspect, select, table, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError, SQLAlchemyError
from sqlalchemy.pool import NullPool

import settings

from .base import MAX_ROWS, Connector, ConnectorError, clean

MAX_TABLES = 50


def check_url(url):
    """A Postgres URL the playground may connect to, or ConnectorError."""
    try:
        parsed = make_url((url or '').strip())
    except ArgumentError as error:
        raise ConnectorError('bad_url', 'That is not a database URL. Use postgresql://user:password@host/database.') from error
    if parsed.get_backend_name() not in ('postgres', 'postgresql') or not parsed.host or not parsed.database:
        raise ConnectorError('bad_url', 'Only Postgres URLs are accepted, like postgresql://user:password@host/database. '
                                        'For SQLite, upload the .sqlite file instead.')
    if settings.DATABASE_URL:
        core = make_url(settings.DATABASE_URL.replace('postgres://', 'postgresql://', 1))
        if (core.host, core.database) == (parsed.host, parsed.database):
            raise ConnectorError('core_database', 'That is the playground\'s own database. Put source tables in a '
                                                  'separate database.')
    return parsed


def label(url):
    """The URL without its password, for showing in the UI."""
    try:
        return make_url(url).render_as_string(hide_password=True)
    except ArgumentError:
        return 'database'


class SqlConnector(Connector):
    def __init__(self, url=None, sqlite_path=None):
        if sqlite_path:
            path = str(sqlite_path)
            self._engine = create_engine('sqlite://', poolclass=NullPool,
                                         creator=lambda: sqlite3.connect(f'file:{path}?mode=ro', uri=True))
            self._postgres = False
        else:
            parsed = check_url(url).set(drivername='postgresql+psycopg')
            self._engine = create_engine(parsed, poolclass=NullPool, connect_args={'connect_timeout': 8})
            self._postgres = True
        self._tables = None

    def tables(self):
        if self._tables is None:
            try:
                found = inspect(self._engine)
                names = sorted(found.get_table_names())[:MAX_TABLES]
                self._tables = {name: [c['name'] for c in found.get_columns(name)] for name in names}
            except SQLAlchemyError as error:
                raise ConnectorError('db_error', f'Could not read the database: {_short(error)}', 502) from error
            if not self._tables:
                raise ConnectorError('empty', 'This database has no tables.')
        return self._tables

    def rows(self, table_name, columns=None, where=None, limit=None):
        columns = self.check(table_name, columns, where)
        t = table(table_name, *[column(c) for c in self.tables()[table_name]])
        query = select(*[t.c[c] for c in columns])
        for key, value in (where or {}).items():
            query = query.where(t.c[key] == value)
        query = query.limit(min(limit or MAX_ROWS, MAX_ROWS))
        try:
            with self._engine.connect() as db, db.begin():
                if self._postgres:
                    db.execute(text('SET TRANSACTION READ ONLY'))
                    db.execute(text('SET LOCAL statement_timeout = 5000'))
                result = db.execute(query)
                return [{c: clean(v) for c, v in zip(columns, row)} for row in result]
        except SQLAlchemyError as error:
            raise ConnectorError('db_error', f'The database query failed: {_short(error)}', 502) from error


def _short(error):
    return str(getattr(error, 'orig', error)).strip().splitlines()[0][:200]
