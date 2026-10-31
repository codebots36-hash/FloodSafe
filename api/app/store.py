"""The one database: community reports, push subscriptions and SOS pings.

Postgres when DATABASE_URL is set (Neon on a deploy, where the disk is wiped
on every restart), else a SQLite file for a laptop and the tests. The SQL is
written once, in the dialect both accept: "?" placeholders (turned into "%s"
for Postgres), ON CONFLICT for upserts, and every count selected under a name.
An "ALTER TABLE ... ADD COLUMN" in a schema adds a column to a database made
before it existed, and is skipped where the column is already there.
"""

import os
import sqlite3
from contextlib import contextmanager

try:
    import psycopg
    from psycopg.rows import dict_row
except ImportError:  # only needed when DATABASE_URL is set
    psycopg = None

DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
DB_PATH = os.environ.get("REPORTS_DB") or os.path.join(DATA, "reports.db")

DBError = (sqlite3.Error,) + ((psycopg.Error,) if psycopg else ())
IntegrityError = (sqlite3.IntegrityError,) + ((psycopg.IntegrityError,) if psycopg else ())

_schemas = []      # CREATE statements registered by each module
_ready = set()     # databases whose tables exist


def schema(*statements):
    """Register CREATE TABLE / CREATE INDEX statements, run once per database."""
    _schemas.extend(statements)


class _Postgres:
    def __init__(self, con):
        self.con = con

    def execute(self, sql, params=()):
        return self.con.execute(sql.replace("?", "%s"), params)


@contextmanager
def db():
    """A connection that commits when the block ends and rolls back if it fails."""
    url = os.environ.get("DATABASE_URL")
    if url:
        if psycopg is None:
            raise RuntimeError("DATABASE_URL is set but psycopg is not installed.")
        with psycopg.connect(url, row_factory=dict_row, connect_timeout=10) as con:
            if url not in _ready:
                for statement in _schemas:
                    con.execute(statement.replace("ADD COLUMN", "ADD COLUMN IF NOT EXISTS"))
                con.commit()
                _ready.add(url)
            yield _Postgres(con)  # psycopg commits on a clean exit, rolls back on error
        return

    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=10)
    con.row_factory = sqlite3.Row
    try:
        if DB_PATH not in _ready:
            con.execute("PRAGMA journal_mode = WAL")
            for statement in _schemas:
                try:
                    con.execute(statement)
                except sqlite3.OperationalError as exc:
                    if "duplicate column" not in str(exc):
                        raise
            _ready.add(DB_PATH)
        with con:
            yield con
    finally:
        con.close()
