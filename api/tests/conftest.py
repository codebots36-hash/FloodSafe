import pytest

from app import reports


@pytest.fixture(autouse=True)
def fresh_reports(tmp_path, monkeypatch):
    """Every test gets an empty reports database, so none touches the real one."""
    monkeypatch.setattr(reports, "DB_PATH", str(tmp_path / "reports.db"))
    reports._hits.clear()
