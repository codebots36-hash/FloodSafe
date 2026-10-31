import os

import pytest

from app import reports, store


@pytest.fixture(autouse=True)
def fresh_reports(tmp_path, monkeypatch):
    """Every test gets an empty database, so none touches the real one, and a
    new report reads no rain unless a test says otherwise.

    Set TEST_DATABASE_URL to run against an empty Postgres instead of SQLite;
    its tables are emptied before each test.
    """
    url = os.environ.get("TEST_DATABASE_URL")
    if url:
        monkeypatch.setenv("DATABASE_URL", url)
        with store.db() as con:
            for table in ("confirmations", "reports", "push_subs", "sos_pings"):
                con.execute(f"DELETE FROM {table}")
    else:
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.setattr(store, "DB_PATH", str(tmp_path / "reports.db"))
    monkeypatch.setattr(reports, "rain_near", lambda lat, lon: None)
    reports._hits.clear()
