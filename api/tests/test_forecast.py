"""Is each stretch of a route judged on the rain at the hour you reach it?

Run from api/:  python -m pytest -q
The replay checks read api/data/weather_history/2013-06-16.json, the ERA5
rain kept for the Kedarnath flood, so they need no network.
"""

import math

import numpy as np
from fastapi.testclient import TestClient

from app.forecast import Forecast
from app.graph import get_graph
from app.main import app
from app.routing import rain_factor

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
    factor = rain_factor(graph, np.full(len(graph.edge_u), 2))
    low = (np.asarray(graph.edge_risk) == 0) & ~np.asarray(graph.edge_landslide)
    assert (factor[low] == 1).all()
    assert (factor[~low & (np.asarray(graph.edge_risk) < 4)] == 4).all()


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
