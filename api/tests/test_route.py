"""Does /route return a real road route, and do the map layers load?

Run from api/:  python -m pytest -q
Needs the graph built by pipeline/build_graph.py and the risk files and
layers built by pipeline/build_road_risk.py.
"""

from fastapi.testclient import TestClient

from app.main import app

RISHIKESH = "30.0869,78.2676"
DEHRADUN = "30.3165,78.0322"
UTTARKASHI = "30.729,78.443"
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


def test_segments_cover_the_whole_line_without_gaps():
    body = client.get(f"/route?from={RISHIKESH}&to={UTTARKASHI}").json()
    segs = body["segments"]
    assert segs[0]["start"] == 0
    assert segs[-1]["end"] == len(body["geometry"]["coordinates"]) - 1
    for prev, nxt in zip(segs, segs[1:]):
        assert prev["end"] == nxt["start"]
        # neighbours always differ, or they would have been merged
        assert (prev["risk_class"], prev["risk_source"], prev["landslide_prone"]) != \
               (nxt["risk_class"], nxt["risk_source"], nxt["landslide_prone"])
    km = body["summary"]["km_by_class"]
    assert abs(sum(km.values()) - body["distance_km"]) < 0.5  # all inside the state


def test_safest_has_no_more_extreme_km_than_fastest():
    fast = client.get(f"/route?from={RISHIKESH}&to={UTTARKASHI}&mode=fastest").json()
    safe = client.get(f"/route?from={RISHIKESH}&to={UTTARKASHI}&mode=safest").json()
    assert safe["mode"] == "safest"
    assert safe["summary"]["km_by_class"]["EXTREME"] <= fast["summary"]["km_by_class"]["EXTREME"]


def test_layers_are_gzipped_geojson():
    for name in ("road-risk", "atlas"):
        response = client.get(f"/layers/{name}", headers={"Accept-Encoding": "gzip"})
        assert response.status_code == 200
        assert response.headers["content-encoding"] == "gzip"
        assert "max-age=3600" in response.headers["cache-control"]
        body = response.json()
        assert body["type"] == "FeatureCollection" and body["features"]


def test_an_unknown_layer_is_404():
    response = client.get("/layers/nope")
    assert response.status_code == 404
    assert response.json()["error"] == "not_found"


def test_a_point_at_sea_is_refused():
    response = client.get(f"/route?from={RISHIKESH}&to={BAY_OF_BENGAL}")
    assert response.status_code == 400
    assert response.json()["error"] == "too_far_from_road"

