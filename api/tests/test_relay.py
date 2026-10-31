"""Can rain reach the API when its own host is refused by Open-Meteo?

On Render, Open-Meteo answered "HTTP 429: Daily API request limit exceeded", so
/weather and /zones had no rain. pipeline/relay_rainfall.py fetches elsewhere
and posts the readings to /relay/weather. These tests use made-up payloads in
Open-Meteo's shape: they check the plumbing, not any weather.

Run from api/:  python -m pytest -q
"""

import json
import os
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app import weather
from app.main import app

SECRET = "a-secret-for-tests"
RISHIKESH = {"lat": 30.0869, "lon": 78.2676}
CELL = weather.key(RISHIKESH["lat"], RISHIKESH["lon"])
ZONES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "zones.json")

client = TestClient(app)


def refused(_keys):
    raise RuntimeError("HTTP 429: Daily API request limit exceeded. Please try again tomorrow.")


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """An empty relay store, the secret set, and Open-Meteo refusing this host."""
    monkeypatch.setattr(weather, "RELAY_FILE", str(tmp_path / "relay.json"))
    monkeypatch.setattr(weather, "_relay", {})
    monkeypatch.setenv("RAINFALL_RELAY_SECRET", SECRET)
    monkeypatch.setattr(weather, "_request", refused)


def fake(taken):
    """An Open-Meteo-shaped payload taken at `taken`.

    The 72 hours up to it have 1 mm each (72 mm in all); the hours after it,
    which are forecast, have 5 mm each, so reading the wrong "now" shows.
    """
    start = taken.replace(minute=0, second=0, microsecond=0) - timedelta(hours=96)
    times = [start + timedelta(hours=h) for h in range(96 + 48 + 1)]
    values = [0.0 if h <= 24 else 1.0 if h <= 96 else 5.0 for h in range(len(times))]
    stamp = lambda t: t.strftime("%Y-%m-%dT%H:%M")
    return {"current": {"time": stamp(taken), "precipitation": 0.4},
            "hourly": {"time": [stamp(t) for t in times], "precipitation": values}}


def send(cells, secret=SECRET):
    headers = {} if secret is None else {"X-Relay-Secret": secret}
    return client.post("/relay/weather", json={"cells": cells}, headers=headers)


def test_without_a_secret_on_the_server_the_relay_is_off(monkeypatch):
    monkeypatch.delenv("RAINFALL_RELAY_SECRET")
    response = send({CELL: fake(datetime.now(weather.IST))})
    assert (response.status_code, response.json()["error"]) == (503, "not_configured")


@pytest.mark.parametrize("secret", [None, "", "wrong", SECRET + "x"])
def test_a_wrong_or_missing_secret_is_refused(secret):
    response = send({CELL: fake(datetime.now(weather.IST))}, secret)
    assert (response.status_code, response.json()["error"]) == (403, "forbidden")
    assert weather._relay == {}  # nothing was kept


def test_relayed_rain_is_served_without_the_server_fetching_anything():
    now = datetime.now(weather.IST)
    assert send({CELL: fake(now)}).json() == {"ok": True, "kept": 1}

    response = client.get("/weather", params=RISHIKESH)  # _request would raise 429 if it were called
    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "relay"
    assert body["last_72h_mm"] == 72.0
    assert body["current_mm_h"] == 0.4
    assert body["hourly"][0]["mm"] == 5.0  # the first forecast hour


def test_a_relayed_reading_survives_a_restart():
    send({CELL: fake(datetime.now(weather.IST))})
    weather._relay = None  # a new process reads the file again
    assert client.get("/weather", params=RISHIKESH).status_code == 200


def test_cells_that_are_not_ours_or_not_current_are_ignored():
    now = datetime.now(weather.IST)
    old = fake(now - timedelta(days=10))  # its series ends well before now
    junk = {"current": {}, "hourly": {"time": [], "precipitation": []}}
    kept = send({"not a cell": fake(now), "30.1,78.3,5": fake(now), CELL: old, "30.2,78.3": junk}).json()["kept"]
    assert kept == 0
    assert client.get("/weather", params=RISHIKESH).status_code == 503


def test_a_body_that_is_not_made_of_readings_is_a_400():
    response = send({CELL: "text"})
    assert (response.status_code, response.json()["error"]) == (400, "bad_request")


def test_an_old_relay_copy_is_read_as_of_when_it_was_taken(monkeypatch):
    taken = (datetime.now(weather.IST) - timedelta(hours=3)).replace(second=0, microsecond=0)
    send({CELL: fake(taken)})
    monkeypatch.setattr(weather, "_hour", lambda now=None: "1999-01-01T00")  # the copy is now stale

    body = client.get("/weather", params=RISHIKESH).json()
    assert body["source"] == "relay"
    assert body["as_of"] == taken.isoformat(timespec="seconds")
    # The 5 mm hours after `taken` are forecast. They must not be counted as rain that fell.
    assert body["last_72h_mm"] == 72.0


def test_zones_work_from_relayed_rain_alone():
    with open(ZONES, encoding="utf-8") as fh:
        zones = json.load(fh)["zones"]
    taken = (datetime.now(weather.IST) - timedelta(hours=1)).replace(second=0, microsecond=0)
    cells = {weather.key(z["lat"], z["lon"]): fake(taken) for z in zones}
    assert send(cells).json()["kept"] == len(cells)

    response = client.get("/zones")
    assert response.status_code == 200
    body = response.json()
    assert (body["source"], len(body["zones"])) == ("relay", len(zones))
    assert body["as_of"] == taken.isoformat(timespec="seconds")
    # 72 mm in 72 h is 3 mm an hour for 24 h, i.e. 24 mm: under every WATCH depth, so all SAFE.
    assert {z["rain_mm"]["h24"] for z in body["zones"]} == {24.0}


def test_without_any_relay_the_error_still_says_so():
    response = client.get("/weather", params=RISHIKESH)
    assert response.status_code == 503
    assert response.json()["error"] == "weather_unavailable"
