"""Is each stretch of a route judged on the rain at the hour you reach it?

Run from api/:  python -m pytest -q
The replay checks read api/data/weather_history/2013-06-16.json, the ERA5
rain kept for the Kedarnath flood, so they need no network.
"""

import datetime
import math

import numpy as np
from fastapi.testclient import TestClient

from app import forecast
from app.forecast import Forecast
from app.graph import get_graph
from app.main import app
from app.routing import _rain_prone, _timed_search, route_timed, safest_weights
from app.routing import route as dijkstra_route

RISHIKESH = "30.0869,78.2676"
UTTARKASHI = "30.729,78.443"
client = TestClient(app)


def route(**params):
    query = "&".join(f"{k}={v}" for k, v in params.items())
    return client.get(f"/route?from={RISHIKESH}&to={UTTARKASHI}&{query}")


def test_the_hour_lookup_reads_each_zones_own_clock():
    sky = Forecast.__new__(Forecast)
    sky.t0 = np.array([0.0, 3600.0])
    sky.mm = np.array([[0.0, 1.0, 9.0], [5.0, 6.0, np.nan]])
    sky.level = np.array([[0, 1, 2], [0, 0, 0]], dtype=np.int8)
    mm, level = sky.at([0, 0, 1, -1, 0], [1, 3601, 3600, 0, 99999])
    assert (mm[0], level[0]) == (1.0, 1)      # the value at T is the rain in the hour before T
    assert (mm[1], level[1]) == (9.0, 2)
    assert mm[2] == 5.0                       # zone 1 starts an hour later
    assert math.isnan(mm[3]) and level[3] == 0  # no zone near
    assert math.isnan(mm[4]) and level[4] == 0  # past the end of the record


def test_rain_leaves_low_roads_alone():
    graph = get_graph()
    prone = _rain_prone(graph)
    low = (np.asarray(graph.edge_risk) == 0) & ~np.asarray(graph.edge_landslide)
    assert not prone[low].any()
    assert prone[~low & (np.asarray(graph.edge_risk) < 4)].all()


def diamond(leave_ts):
    """0 -> 1 -> 3 is quicker, but its 1 -> 3 road is in a zone that turns
    CRITICAL (x4) in its third hour; 0 -> 2 -> 3 stays dry. Returns the middle node."""
    indptr = np.array([0, 2, 3, 4, 4], dtype=np.int64)
    heads = np.array([1, 2, 3, 3], dtype=np.int64)
    base = np.array([10.0, 15.0, 10.0, 15.0])
    rain_zone = np.array([-1, -1, 0, -1], dtype=np.int32)
    factor = np.array([[1.0, 1.0, 4.0, 4.0]])
    via = _timed_search(indptr, heads, base, base.copy(), rain_zone, factor,
                        np.zeros(1), np.zeros(4), 0, 3, float(leave_ts))
    return int(np.searchsorted(indptr, via[3], side="right") - 1)


def test_the_search_judges_each_road_at_the_hour_it_is_reached():
    assert diamond(0) == 1         # reaches 1 -> 3 in hour 1: dry, the quick way is cheapest
    assert diamond(3600) == 2      # reaches it in hour 2: CRITICAL, 10 + 40 > 30, so go round


def test_with_no_rain_the_timed_search_matches_dijkstra():
    graph = get_graph()
    sky, _ = forecast.load(datetime.date(2013, 6, 16))
    dry = Forecast.__new__(Forecast)
    dry.t0, dry.level = sky.t0, np.zeros_like(sky.level)
    start, end = (30.0869, 78.2676), (30.729, 78.443)
    weights = safest_weights(graph, "strict")
    expected = dijkstra_route(graph, start, end, weights)
    got = route_timed(graph, start, end, weights, 0, dry, forecast.edge_zones(graph))
    assert math.isclose(weights[got["edge_ids"]].sum(), weights[expected["edge_ids"]].sum())
    assert got["coordinates"][0] == expected["coordinates"][0]
    assert got["coordinates"][-1] == expected["coordinates"][-1]


def test_a_replayed_safest_route_moves_with_the_departure_hour():
    by_hour = {h: route(mode="safest", replay="2013-06-16", depart=f"2013-06-16T{h:02d}:00").json()
               for h in (0, 3, 9)}
    assert len({b["distance_km"] for b in by_hour.values()}) > 1
    for hour, body in by_hour.items():
        assert body["depart"] == f"2013-06-16T{hour:02d}:00:00+05:30"
        assert any(s["forecast_rain_mm"] is not None for s in body["segments"])
        assert any("WATCH or CRITICAL" in n for n in body["notes"])


def test_rain_never_moves_the_fastest_route():
    dry = route(mode="fastest", replay="2013-06-16", depart="2013-06-16T00:00").json()
    wet = route(mode="fastest", replay="2013-06-16", depart="2013-06-16T12:00").json()
    assert dry["geometry"] == wet["geometry"]


def test_bad_departures_are_refused():
    for params in ({"depart": "soon"},
                   {"depart": "2001-01-01T10:00"},  # live and in the past
                   {"replay": "2013-06-16", "depart": "2013-06-20T10:00"}):  # not the replayed day
        response = route(**params)
        assert (response.status_code, response.json()["error"]) == (400, "bad_depart"), params
