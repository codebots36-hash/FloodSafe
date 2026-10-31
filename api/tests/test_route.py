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


def test_replaying_a_date_does_not_move_a_fastest_route():
    body = client.get(f"/route?from={RISHIKESH}&to={UTTARKASHI}&replay=2013-06-17").json()
    assert body["replay"] == "2013-06-17"
    assert any("2013-06-17" in note for note in body["notes"])
    live = client.get(f"/route?from={RISHIKESH}&to={UTTARKASHI}").json()
    assert body["distance_km"] == live["distance_km"]


def test_a_point_at_sea_is_refused():
    response = client.get(f"/route?from={RISHIKESH}&to={BAY_OF_BENGAL}")
    assert response.status_code == 400
    assert response.json()["error"] == "too_far_from_road"



def test_places_match_the_contracts():
    import json
    import os
    contracts = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "contracts")
    for name in ("shelters", "hospitals", "localities"):
        response = client.get(f"/{name}")
        assert response.status_code == 200
        items = response.json()["items"]
        with open(os.path.join(contracts, f"{name}.json"), encoding="utf-8") as fh:
            expected = set(json.load(fh)["items"][0])
        assert items and all(set(item) == expected for item in items), name
    names = {item["name"] for item in client.get("/localities").json()["items"]}
    assert {"Rishikesh", "Dehradun", "Uttarkashi"} <= names


def test_a_deploy_serves_the_stored_gzip_without_compressing_again(monkeypatch):
    import gzip
    import os
    from app import main
    packaged = os.path.join(os.path.dirname(os.path.abspath(main.__file__)), "..", "data", "layers")
    monkeypatch.setattr(main, "LAYER_DIRS", [packaged])

    stored = client.get("/layers/road-risk", headers={"Accept-Encoding": "gzip"})
    assert stored.headers["content-encoding"] == "gzip"
    assert "Accept-Encoding" in stored.headers["vary"]  # a cache must not mix the two forms up
    assert stored.json()["type"] == "FeatureCollection"  # the client undid the one gzip layer

    plain = client.get("/layers/road-risk", headers={"Accept-Encoding": "identity"})
    assert "content-encoding" not in plain.headers
    assert plain.json() == stored.json()

    # the stored file really is the layer compressed once, not twice
    with open(os.path.join(packaged, "road_risk.geojson.gz"), "rb") as fh:
        assert gzip.decompress(fh.read()) == plain.content


def test_the_root_says_where_to_start():
    body = client.get("/").json()
    assert body["health"] == "/health" and body["docs"] == "/docs"


def test_an_unknown_address_or_method_uses_the_error_shape():
    unknown = client.get("/no/such/thing")
    assert (unknown.status_code, unknown.json()["error"]) == (404, "not_found")
    assert unknown.json()["detail"]
    wrong = client.post("/health")
    assert (wrong.status_code, wrong.json()["error"]) == (405, "method_not_allowed")
