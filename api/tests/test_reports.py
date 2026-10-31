"""Do community reports match contracts/ and follow the trust rules?

Run from api/:  python -m pytest -q
Each test uses its own empty database (conftest.py).
"""

import json
import os
import time

import pytest
from fastapi.testclient import TestClient

from app import reports
from app.main import app

CONTRACTS = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "contracts")
RISHIKESH = "30.0869,78.2676"
DEHRADUN = "30.3165,78.0322"

client = TestClient(app)


def contract(name):
    with open(os.path.join(CONTRACTS, name), encoding="utf-8") as fh:
        return json.load(fh)


def road_points(n=6):
    """n points that lie on a real road, spread along Rishikesh -> Dehradun."""
    line = client.get(f"/route?from={RISHIKESH}&to={DEHRADUN}").json()["geometry"]["coordinates"]
    step = len(line) // (n + 1)
    return [(lat, lon) for lon, lat in line[step::step][:n]]


def post(reporter="anon-aaaaaaaa", kind="flooded", point=None, note="Water over the road"):
    lat, lon = point or road_points(1)[0]
    return client.post("/reports", json={"lat": lat, "lon": lon, "kind": kind,
                                         "note": note, "reporter_id": reporter})


def confirm(report_id, reporter):
    return client.post(f"/reports/{report_id}/confirm", json={"reporter_id": reporter})


def test_a_new_report_matches_the_contract():
    response = post()
    assert response.status_code == 200
    body = response.json()
    assert set(body) == set(contract("report.json"))
    assert body["id"].startswith("r_")
    assert body["trust"] == "unverified"
    assert body["trust_reason"] == "Not confirmed yet"
    assert body["confirmations"] == 0
    assert body["created_at"].endswith("+05:30")
    assert body["near_locality"]


def test_the_list_matches_the_contract_and_is_newest_first():
    assert client.get("/reports").json() == {"items": []}
    first, second = road_points(2)
    post(point=first, kind="flooded")
    post(point=second, kind="blocked")
    items = client.get("/reports").json()["items"]
    assert [r["kind"] for r in items] == ["blocked", "flooded"]
    assert all(set(r) == set(contract("report.json")) for r in items)


def test_two_other_devices_verify_a_report():
    report = post().json()
    one = confirm(report["id"], "anon-bbbbbbbb").json()
    assert (one["trust"], one["confirmations"]) == ("unverified", 1)
    assert "needed" in one["trust_reason"]
    two = confirm(report["id"], "anon-cccccccc").json()
    assert (two["trust"], two["confirmations"]) == ("verified", 2)
    assert two["trust_reason"] == "Confirmed from 2 different devices"
    assert client.get("/reports").json()["items"][0]["trust"] == "verified"


def test_one_device_cannot_confirm_twice_or_confirm_its_own_report():
    report = post().json()
    confirm(report["id"], "anon-bbbbbbbb")
    again = confirm(report["id"], "anon-bbbbbbbb").json()
    assert again["confirmations"] == 1  # counted once

    own = confirm(report["id"], "anon-aaaaaaaa")
    assert own.status_code == 409
    assert own.json()["error"] == "own_report"


def test_confirming_an_unknown_report_is_404_in_the_error_shape():
    response = confirm("r_000000", "anon-bbbbbbbb")
    assert response.status_code == 404
    assert response.json() == {"error": "not_found", "detail": "No report with id r_000000"}


def test_a_double_tap_returns_the_first_report():
    point = road_points(1)[0]
    first, second = post(point=point).json(), post(point=point).json()
    assert first["id"] == second["id"]
    assert len(client.get("/reports").json()["items"]) == 1


def test_one_device_is_limited_to_five_reports_an_hour():
    points = road_points(6)
    for point in points[:5]:
        assert post(point=point).status_code == 200
    sixth = post(point=points[5])
    assert sixth.status_code == 429
    assert sixth.json()["error"] == "rate_limited"
    assert post(reporter="anon-dddddddd", point=points[5]).status_code == 200  # another device is fine


def test_old_reports_drop_out_and_cannot_be_confirmed():
    report = post().json()
    with reports.db() as con:
        con.execute("UPDATE reports SET created_ts = ?",
                    (time.time() - (reports.ACTIVE_HOURS + 1) * 3600,))
    assert client.get("/reports").json()["items"] == []
    late = confirm(report["id"], "anon-bbbbbbbb")
    assert late.status_code == 410
    assert late.json()["error"] == "expired"


@pytest.mark.parametrize("change, field", [
    ({"kind": "earthquake"}, "kind"),
    ({"reporter_id": "short"}, "reporter_id"),
    ({"reporter_id": "has spaces in it"}, "reporter_id"),
    ({"note": "x" * (reports.NOTE_MAX + 1)}, "note"),
    ({"lat": 123.0}, "lat"),
])
def test_bad_input_is_a_400_in_the_error_shape(change, field):
    lat, lon = road_points(1)[0]
    body = {"lat": lat, "lon": lon, "kind": "flooded", "note": "", "reporter_id": "anon-aaaaaaaa", **change}
    response = client.post("/reports", json=body)
    assert response.status_code == 400
    assert response.json()["error"] == "bad_request"
    assert response.json()["detail"].startswith(field)


def test_a_point_outside_the_state_or_far_from_any_road_is_refused():
    outside = post(point=(28.8386, 78.7733))  # Moradabad, Uttar Pradesh
    assert (outside.status_code, outside.json()["error"]) == (400, "outside_uttarakhand")
    far = post(point=(15.0, 88.0))
    assert (far.status_code, far.json()["error"]) == (400, "too_far_from_road")
    assert client.get("/reports").json()["items"] == []  # nothing was saved


def test_control_characters_are_stripped_from_a_note():
    body = post(note="  water\x00 over\nthe road  ").json()
    assert body["note"] == "water  over the road"


def test_a_report_marks_the_fastest_route_without_changing_it():
    route = client.get(f"/route?from={RISHIKESH}&to={DEHRADUN}").json()
    assert all(s["report_id"] is None for s in route["segments"])

    lon, lat = route["geometry"]["coordinates"][len(route["geometry"]["coordinates"]) // 2]
    report = post(point=(lat, lon), kind="blocked").json()

    again = client.get(f"/route?from={RISHIKESH}&to={DEHRADUN}").json()
    assert [s["report_id"] for s in again["segments"]].count(report["id"]) == 1
    assert any("1 community report lies on this route (0 verified)" in n for n in again["notes"])
    assert again["distance_km"] == route["distance_km"]  # Fastest is never moved by a report
    assert again["geometry"] == route["geometry"]


def test_a_report_far_from_the_route_is_not_marked():
    far = client.get("/route?from=30.729,78.443&to=30.5565,79.5644").json()["geometry"]["coordinates"]
    lon, lat = far[len(far) // 2]
    post(point=(lat, lon))
    route = client.get(f"/route?from={RISHIKESH}&to={DEHRADUN}").json()
    assert all(s["report_id"] is None for s in route["segments"])
    assert not any("community report" in n for n in route["notes"])


def test_rain_around_a_flooded_road_verifies_it(monkeypatch):
    monkeypatch.setattr(reports, "rain_near", lambda lat, lon: 6.4)
    flooded = post().json()
    assert flooded["trust"] == "verified"
    assert flooded["trust_reason"] == "6.4 mm of rain fell around it in the 72 hours before it was posted"
    blocked = post(reporter="anon-bbbbbbbb", kind="blocked").json()
    assert blocked["trust"] == "unverified"  # rain says nothing about a fallen tree


def test_safest_steers_around_a_verified_report():
    before = client.get(f"/route?from={RISHIKESH}&to={DEHRADUN}&mode=safest").json()
    line = before["geometry"]["coordinates"]
    lon, lat = line[len(line) // 2]
    report = post(point=(lat, lon), kind="blocked").json()
    # Unverified, the road only costs 20x more, which a short stretch with no
    # nearby way round can still be worth; verified all but blocks it.
    confirm(report["id"], "anon-cccccccc")
    confirm(report["id"], "anon-dddddddd")

    after = client.get(f"/route?from={RISHIKESH}&to={DEHRADUN}&mode=safest").json()
    assert after["geometry"] != before["geometry"]
    assert report["id"] not in [s["report_id"] for s in after["segments"]]
