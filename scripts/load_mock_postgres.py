"""Copies the mock wages_vbg and agristack tables into a Postgres database, to demo the SQL connector.

    .venv/bin/python scripts/load_mock_postgres.py postgresql://user:password@host/mock_sources

Use a database of its own (on Neon, add a second database to the branch): the playground refuses to read
from its own core-store database.
"""
import sqlite3
import sys
from pathlib import Path

from sqlalchemy import create_engine, text

MOCK = Path(__file__).resolve().parent.parent / 'data' / 'mock' / 'vbg_agri.sqlite'


def main(url):
    url = url.replace('postgres://', 'postgresql://', 1).replace('postgresql://', 'postgresql+psycopg://', 1)
    source = sqlite3.connect(f'file:{MOCK}?mode=ro', uri=True)
    engine = create_engine(url)
    with engine.begin() as db:
        db.execute(text('DROP TABLE IF EXISTS wages_vbg, agristack'))
        db.execute(text('''CREATE TABLE wages_vbg (household_id text NOT NULL, month text NOT NULL, job_card_no text NOT NULL,
                           days_worked integer NOT NULL, wages_paid integer NOT NULL, wages_pending integer NOT NULL,
                           PRIMARY KEY (household_id, month))'''))
        db.execute(text('''CREATE TABLE agristack (farmer_id text PRIMARY KEY, household_id text NOT NULL,
                           land_acres numeric(5,2) NOT NULL, irrigated text NOT NULL, crop_kharif text NOT NULL,
                           crop_rabi text NOT NULL)'''))
        for table in ('wages_vbg', 'agristack'):
            cursor = source.execute(f'SELECT * FROM {table}')
            columns = [c[0] for c in cursor.description]
            rows = [dict(zip(columns, row)) for row in cursor]
            db.execute(text(f'INSERT INTO {table} ({", ".join(columns)}) VALUES ({", ".join(":" + c for c in columns)})'), rows)
            print(f'{table}: {len(rows)} rows')


if __name__ == '__main__':
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
