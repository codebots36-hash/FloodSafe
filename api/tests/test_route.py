"""Does /route return a real road route?

Run from api/:  python -m pytest -q
Needs the graph built by pipeline/build_graph.py.
"""

from fastapi.testclient import TestClient

from app.main import app

RISHIKESH = "30.0869,78.2676"
DEHRADUN = "30.3165,78.0322"
BAY_OF_BENGAL = "15.0,88.0"

client = TestClient(app)


def test_rishikesh_to_dehradun_is_a_plausible_road_route():
    response = client.get(f"/route?from={RISHIKESH}&to={DEHRADUN}&mode=fastest")
    assert response.status_code == 200
    body = response.json()

    assert body["geometry"]["type"] == "LineString"
    assert len(body["geometry"]["coordinates"]) > 50

    # The road runs about 43 km. Anything far outside this means the graph
    # is broken, usually one-way edges pointing the wrong way.
    assert 20 < body["distance_km"] < 80
    assert body["duration_min"] > 0

    lons = [lon for lon, _ in body["geometry"]["coordinates"]]
    lats = [lat for _, lat in body["geometry"]["coordinates"]]
    assert 77.5 < min(lons) and max(lons) < 81.1
    assert 28.7 < min(lats) and max(lats) < 31.5


def test_segments_cover_the_whole_line():
    body = client.get(f"/route?from={RISHIKESH}&to={DEHRADUN}").json()
    assert body["segments"][0]["start"] == 0
    assert body["segments"][-1]["end"] == len(body["geometry"]["coordinates"]) - 1


def test_a_point_at_sea_is_refused():
    response = client.get(f"/route?from={RISHIKESH}&to={BAY_OF_BENGAL}")
    assert response.status_code == 400
    assert response.json()["error"] == "too_far_from_road"


def test_safest_says_it_is_not_ready_yet():
    response = client.get(f"/route?from={RISHIKESH}&to={DEHRADUN}&mode=safest")
    assert response.status_code == 501
    assert response.json()["error"] == "not_ready"
