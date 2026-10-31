"""Does /weather return live rainfall in the shape contracts/weather.json promises?

Run from api/:  python -m pytest -q
Reaches Open-Meteo, so the live checks skip when it cannot be reached.
"""

import datetime
import json
import os

import pytest
from fastapi.testclient import TestClient

from app import weather
from app.main import app

RISHIKESH = {"lat": 30.0869, "lon": 78.2676}
CONTRACT = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "contracts", "weather.json")

client = TestClient(app)


def fetch(**params):
    response = client.get("/weather", params={**RISHIKESH, **params})
    if response.status_code == 503:
        pytest.skip(f"Open-Meteo unreachable: {response.json()['detail']}")
    assert response.status_code == 200
    return response.json()


def test_the_reading_matches_the_contract():
    body = fetch()
    with open(CONTRACT, encoding="utf-8") as fh:
        expected = json.load(fh)
    assert set(body) == set(expected)
    assert set(body["hourly"][0]) == set(expected["hourly"][0])

    assert body["source"] in ("live", "relay")
    # an aware IST time, close to now
    as_of = datetime.datetime.fromisoformat(body["as_of"])
    assert as_of.utcoffset() == datetime.timedelta(hours=5, minutes=30)
    assert abs((datetime.datetime.now(weather.IST) - as_of).total_seconds()) < 6 * 3600

    assert body["current_mm_h"] >= 0
    assert body["last_72h_mm"] >= 0
    assert len(body["hourly"]) == weather.FORECAST_HOURS
    hours = [datetime.datetime.fromisoformat(h["time"]) for h in body["hourly"]]
    assert hours[0] > as_of                                  # the forecast starts ahead of now
    assert all(b - a == datetime.timedelta(hours=1) for a, b in zip(hours, hours[1:]))


def test_a_failed_refresh_serves_the_stored_reading(monkeypatch):
    fetch()  # make sure this point has been fetched at least once

    def no_network(_keys):
        raise RuntimeError("pretend Open-Meteo is down")

    monkeypatch.setattr(weather, "_request", no_network)
    monkeypatch.setattr(weather, "_hour", lambda now=None: "1999-01-01T00")  # force a refresh
    body = fetch()
    assert body["source"] == "relay"
    assert body["last_72h_mm"] is not None


def test_nearby_points_share_one_reading():
    assert weather.key(30.0869, 78.2676) == weather.key(30.09, 78.30)
    assert weather.key(30.0869, 78.2676) != weather.key(30.3165, 78.0322)


def test_replay_is_not_ready_yet():
    response = client.get("/weather", params={**RISHIKESH, "replay": "2013-06-16"})
    assert response.status_code == 501
    assert response.json()["error"] == "not_ready"


def test_a_point_outside_the_world_is_refused():
    assert client.get("/weather", params={"lat": 120, "lon": 78}).status_code == 422
