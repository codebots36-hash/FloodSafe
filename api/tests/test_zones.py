"""Does /zones report every watched place with its rain and its thresholds?

Run from api/:  python -m pytest -q
Needs api/data/zones.json (pipeline/build_places.py) and thresholds.json
(pipeline/calibrate_thresholds.py). Reaches Open-Meteo, so the live checks
skip when it cannot be reached.
"""

import json
import os

import pytest
from fastapi.testclient import TestClient

from app import zones
from app.main import app

CONTRACT = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "contracts", "zones.json")

client = TestClient(app)


def body():
    response = client.get("/zones")
    if response.status_code == 503:
        pytest.skip(f"Open-Meteo unreachable: {response.json()['detail']}")
    assert response.status_code == 200
    return response.json()


def test_the_response_matches_the_contract():
    live = body()
    with open(CONTRACT, encoding="utf-8") as fh:
        expected = json.load(fh)
    assert set(live) == set(expected)
    assert set(live["zones"][0]) == set(expected["zones"][0])
    for field in ("rain_mm", "watch", "critical"):
        assert set(live["zones"][0][field]) == set(expected["zones"][0][field]), field

    assert live["source"] in ("live", "relay")
    assert live["replay"] is None
    assert live["threshold_basis"].endswith(".")


def test_every_zone_is_reported_once_with_a_real_status():
    live = body()["zones"]
    assert len(live) == len(zones._load("zones")["zones"])
    assert len({z["id"] for z in live}) == len(live)
    for zone in live:
        assert zone["status"] in ("SAFE", "WATCH", "CRITICAL")
        assert zone["hazard_class"] in ("LOW", "MODERATE", "SIGNIFICANT", "EXTREME")
        assert zone["risk_source"] in ("surveyed", "modelled")
        assert zone["rain_mm"]["h24"] >= zone["rain_mm"]["h3"] >= zone["rain_mm"]["h1"] >= 0
        for window in ("h1", "h3", "h24"):
            assert zone["critical"][window] > zone["watch"][window] > 0, zone["id"]


def test_the_status_matches_the_zones_own_thresholds():
    for zone in body()["zones"]:
        over_watch = any(zone["rain_mm"][w] >= zone["watch"][w] for w in ("h1", "h3", "h24"))
        over_critical = any(zone["rain_mm"][w] >= zone["critical"][w]
                            for w in ("h1", "h3", "h24"))
        expected = "CRITICAL" if over_critical else "WATCH" if over_watch else "SAFE"
        assert zone["status"] == expected, zone["id"]


def test_a_flashier_zone_warns_on_less_rain():
    thresholds = zones._load("thresholds")
    mild = {"hazard_class": "LOW", "soil_group": "A"}
    flashy = {"hazard_class": "EXTREME", "soil_group": "D"}
    for window in ("h1", "h3", "h24"):
        assert (zones.thresholds_for(flashy, thresholds)["watch"][window]
                < zones.thresholds_for(mild, thresholds)["watch"][window])


def test_status_is_decided_window_by_window():
    limits = {"watch": {"h1": 5, "h3": 10, "h24": 40},
              "critical": {"h1": 8, "h3": 20, "h24": 77}}
    quiet = {"h1": 0.0, "h3": 0.0, "h24": 0.0, "antecedent_48h": 0.0}
    assert zones.status_of(quiet, limits) == "SAFE"
    assert zones.status_of({**quiet, "h3": 10.0}, limits) == "WATCH"      # one window is enough
    assert zones.status_of({**quiet, "h1": 8.1}, limits) == "CRITICAL"
    assert zones.status_of({**quiet, "h24": 39.9}, limits) == "SAFE"      # just under
    assert zones.status_of({**quiet, "h1": None}, limits) == "SAFE"       # a gap is not a warning


def test_the_thresholds_were_calibrated_not_guessed():
    thresholds = zones._load("thresholds")
    assert thresholds["labels"]["flood_district_days"] > 100
    for level, target in (("watch", 0.15), ("critical", 0.05)):
        measured = thresholds["levels"][level]
        assert abs(measured["POFD"] - target) < 0.01, level   # the false-alarm budget was met
        assert measured["hits"] > 0 and measured["POD"] > 0
    assert all(auc > 0.5 for auc in thresholds["roc_auc"].values())


def test_replaying_the_2013_flood_turns_the_state_critical():
    response = client.get("/zones", params={"replay": "2013-06-17"})
    if response.status_code == 503:
        pytest.skip(f"the rainfall archive could not be read: {response.json()['detail']}")
    live = response.json()

    assert live["source"] == "replay"
    assert live["replay"] == "2013-06-17"
    assert live["as_of"].startswith("2013-06-17")

    warned = [z for z in live["zones"] if z["status"] != "SAFE"]
    assert len(warned) > len(live["zones"]) / 2  # it was a statewide event
    kedarnath = next(z for z in live["zones"] if z["id"] == "kedarnath")
    assert kedarnath["status"] == "CRITICAL"
    assert kedarnath["rain_mm"]["h24"] > kedarnath["critical"]["h24"]


def test_a_replayed_list_is_one_moment_not_each_zones_worst():
    response = client.get("/zones", params={"replay": "2013-06-17"})
    if response.status_code == 503:
        pytest.skip("the rainfall archive could not be read")
    # Every zone is read at the same hour, so the one as_of describes them all.
    assert response.json()["as_of"].endswith("+05:30")
