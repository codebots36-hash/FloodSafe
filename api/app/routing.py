"""Shortest-path routing over the road graph.

Input   a RoadGraph, a start and an end lat/lon
Output  a route: its polyline, its length and time, and the edges behind it

"fastest" minimises travel time. "safest" minimises travel time x a risk
penalty per edge, using the classes pipeline/build_road_risk.py gave every
edge. With a rain forecast, Safest uses route_timed, which judges each road on
the rain at the hour the search actually reaches it.
"""

import math
from heapq import heappop, heappush

import numpy as np
from numba import njit
from scipy.sparse.csgraph import dijkstra

# Further than this from any road and the request is a mistake rather than a
# trip: a tap in the middle of a reservoir, or a GPS fix that has not settled.
MAX_SNAP_M = 2000

RISK_CLASSES = ["LOW", "MODERATE", "SIGNIFICANT", "EXTREME"]

# Safest cost per edge = travel time x penalty. "strict" squares the risk
# scores 1/2/4/8 and all but blocks EXTREME; "mild" is the fallback when strict
# sends the route on a huge detour. Roads outside the state have no score and
# are costed like MODERATE, so Safest is not lured out of the state onto roads
# we know nothing about. A modelling choice, not an official rule.
PENALTIES = {
    "strict": {"LOW": 1, "MODERATE": 4, "SIGNIFICANT": 16, "EXTREME": 1000,
               "unscored": 4, "landslide": 2},
    "mild": {"LOW": 1, "MODERATE": 2, "SIGNIFICANT": 4, "EXTREME": 8,
             "unscored": 2, "landslide": 2},
}
MAX_DETOUR = 3  # strict Safest longer than this x Fastest -> use "mild"
RAIN_FACTOR = np.array([1.0, 2.0, 4.0])  # SAFE / WATCH / CRITICAL, see _rain_prone
# No drive in the state takes a day, so route_timed's search guide only looks
# this far ahead. A longer trip would still route, maybe not on the cheapest path.
TRIP_HORIZON_S = 24 * 3600


class RoutingError(Exception):
    """Something the caller can fix, reported as a 4xx with this code."""

    def __init__(self, code, detail, status=400):
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.status = status


def _node_path(predecessors, source, target):
    """Walk the predecessor array back from target to source."""
    path = [target]
    node = target
    while node != source:
        node = int(predecessors[node])
        if node < 0:
            raise RoutingError("no_route", "No road connects those two points.", 404)
        path.append(node)
    path.reverse()
    return path


def _snap_ends(graph, start, end):
    source, source_m = graph.snap(*start)
    target, target_m = graph.snap(*end)
    for label, metres in (("start", source_m), ("destination", target_m)):
        if metres > MAX_SNAP_M:
            raise RoutingError(
                "too_far_from_road",
                f"The {label} is {metres / 1000:.1f} km from the nearest road.",
            )
    if source == target:
        raise RoutingError("same_place", "The start and destination are the same place on the road map.")
    return source, target, {"start": source_m, "end": target_m}


def _result(graph, nodes, edge_ids, snapped):
    coordinates, bounds = _stitch(graph, edge_ids)
    return {
        "nodes": nodes,
        "edge_ids": edge_ids,
        "coordinates": coordinates,
        "bounds": bounds,
        "distance_m": float(np.asarray(graph.edge_len_m)[edge_ids].sum()),
        "duration_s": float(graph.travel_s[edge_ids].sum()),
        "snapped_m": snapped,
    }


def route(graph, start, end, weights=None):
    """Cheapest path from start to end, each a (lat, lon) pair.

    weights is an optional per-directed-edge cost array; without it the cost
    is travel time, which is what "fastest" means.
    """
    source, target, snapped = _snap_ends(graph, start, end)

    matrix = graph.csr
    if weights is not None:
        matrix = matrix.copy()
        matrix.data = np.maximum(weights[graph.csr_edge_id], 1e-3)

    costs, predecessors = dijkstra(matrix, indices=source, return_predecessors=True)
    if not np.isfinite(costs[target]):
        raise RoutingError("no_route", "No road connects those two points.", 404)

    nodes = _node_path(predecessors, source, target)
    edge_ids = []
    for a, b in zip(nodes[:-1], nodes[1:]):
        edge_id = graph.edge_between(a, b)
        if edge_id is None:  # cannot happen for a path Dijkstra returned
            raise RoutingError("no_route", "The route could not be rebuilt.", 500)
        edge_ids.append(edge_id)

    return _result(graph, nodes, edge_ids, snapped)


def _lowest_factor(sky, leave_ts):
    """Per zone, the smallest rain factor at any hour from leave_ts to
    TRIP_HORIZON_S later; an hour outside the record counts as dry (1)."""
    width = sky.level.shape[1]
    first = np.ceil((leave_ts - sky.t0) / 3600).astype(np.int64)
    last = np.ceil((leave_ts + TRIP_HORIZON_S - sky.t0) / 3600).astype(np.int64)
    hours = np.arange(width)
    inside = (hours >= first[:, None]) & (hours <= last[:, None])
    lowest = np.where(inside, sky.level, 127).min(axis=1)
    lowest[(first < 0) | (last >= width) | ~inside.any(axis=1)] = 0
    return RAIN_FACTOR[lowest]


# Compiled when this module is imported (and cached on disk), so no request pays for it.
@njit("int64[::1](int64[::1], int64[::1], float64[::1], float64[::1], int32[::1], "
      "float64[:, ::1], float64[::1], float64[::1], int64, int64, float64)", cache=True)
def _timed_search(indptr, heads, base, travel, rain_zone, factor, t0, ahead,
                  source, target, leave_ts):
    """A* over CSR edges where edge k entered at time t costs
    base[k] x factor[zone, hour of t]. Returns, per node, the CSR position of
    the edge used to reach it (-1 if never reached)."""
    n = len(indptr) - 1
    width = factor.shape[1]
    cost = np.full(n, np.inf)
    clock = np.zeros(n)
    via = np.full(n, -1, dtype=np.int64)
    done = np.zeros(n, dtype=np.bool_)
    cost[source] = 0.0
    clock[source] = leave_ts
    heap = [(ahead[source], 0.0, source)]
    while heap:
        _, d, u = heappop(heap)
        if done[u]:
            continue
        if u == target:
            break
        done[u] = True
        now = clock[u]
        for k in range(indptr[u], indptr[u + 1]):
            v = heads[k]
            if done[v] or not math.isfinite(ahead[v]):
                continue
            c = base[k]
            z = rain_zone[k]
            if z >= 0:
                # Open-Meteo's value at hour T is the rain in the hour before T.
                h = math.ceil((now - t0[z]) / 3600)
                if 0 <= h < width:
                    c *= factor[z, h]
            nd = d + c
            if nd < cost[v]:
                cost[v] = nd
                clock[v] = now + travel[k]
                via[v] = k
                heappush(heap, (nd + ahead[v], nd, v))
    return via


def route_timed(graph, start, end, weights, leave_ts, sky, zone):
    """Time-dependent A*: the cheapest path when a road's cost depends on when
    you reach it.

    weights   per-directed-edge Safest cost before rain (risk x reports)
    leave_ts  departure, unix seconds
    sky       an app.forecast.Forecast; zone the nearest zone per edge

    The search carries the clock along each partial path, so every road is
    multiplied by the rain factor for the hour this path reaches it, not the
    hour the Fastest route would. Travel time itself does not depend on the
    hour; only the rain penalty does.
    ponytail: label-setting on cost alone, so a dearer-but-earlier partial path
    is dropped; a full time/cost Pareto search would keep it.
    """
    source, target, snapped = _snap_ends(graph, start, end)

    eid = graph.csr_edge_id
    base_arr = np.maximum(np.asarray(weights)[eid], 1e-3)
    rain_zone_arr = np.where(_rain_prone(graph)[eid], zone[eid], -1).astype(np.int32)
    # A* guide: the exact cost to target if every road had the lowest rain
    # factor its zone reaches in the next TRIP_HORIZON_S, from one backwards
    # Dijkstra. That never overestimates, so the search stays exact, and in
    # rain that persists it is close enough to skip most of the state.
    floor = _lowest_factor(sky, leave_ts)
    known = rain_zone_arr >= 0
    to_target = graph.csr.copy()
    to_target.data = base_arr * np.where(known, floor[np.where(known, rain_zone_arr, 0)], 1.0)
    ahead_arr = dijkstra(to_target.T.tocsr(), indices=target)
    if not np.isfinite(ahead_arr[source]):
        raise RoutingError("no_route", "No road connects those two points.", 404)

    via = _timed_search(
        graph.csr_indptr, graph.csr_indices, base_arr,
        np.ascontiguousarray(graph.travel_s[eid]), rain_zone_arr,
        np.ascontiguousarray(RAIN_FACTOR[sky.level]), np.ascontiguousarray(sky.t0, dtype=np.float64),
        ahead_arr, source, target, float(leave_ts))
    if target != source and via[target] < 0:
        raise RoutingError("no_route", "No road connects those two points.", 404)

    positions = []
    node = target
    while node != source:
        k = int(via[node])
        positions.append(k)
        node = int(np.searchsorted(graph.csr_indptr, k, side="right") - 1)
    positions.reverse()
    edge_ids = [int(eid[k]) for k in positions]
    nodes = [source] + [int(graph.csr_indices[k]) for k in positions]
    return _result(graph, nodes, edge_ids, snapped)


def safest_weights(graph, level, extra=None):
    """Per-directed-edge Safest cost for a PENALTIES level, times an optional
    per-edge multiplier (forecast rain, community reports)."""
    p = PENALTIES[level]
    lut = np.full(256, p["unscored"], dtype=np.float64)
    for code, name in enumerate(RISK_CLASSES):
        lut[code] = p[name]
    slide = np.where(np.asarray(graph.edge_landslide), p["landslide"], 1.0)
    weights = graph.travel_s * lut[np.asarray(graph.edge_risk)] * slide
    return weights if extra is None else weights * extra


def _rain_prone(graph):
    """Edges rain makes dearer: a road already rated MODERATE or worse, or
    landslide-prone, costs RAIN_FACTOR[level] times more when its zone is
    WATCH (1) or CRITICAL (2) at the hour you reach it. LOW roads are left
    alone: rain on a road nothing marks as risky is not a reason to leave it.
    A modelling choice, not an official rule."""
    risk = np.asarray(graph.edge_risk)
    return ((risk >= 1) & (risk < len(RISK_CLASSES))) | np.asarray(graph.edge_landslide)


def edge_risk(graph, edge_id):
    """(risk_class, risk_source, landslide_prone); class and source are None
    for an edge with no score."""
    code = int(graph.edge_risk[edge_id])
    if code >= len(RISK_CLASSES):
        return None, None, False
    source = "surveyed" if graph.edge_surveyed[edge_id] else "modelled"
    return RISK_CLASSES[code], source, bool(graph.edge_landslide[edge_id])


def segments(graph, edge_ids, bounds, rain_mm=None):
    """Consecutive edges with the same class, source and landslide flag
    merge into one segment; start/end index the route's coordinates.

    rain_mm is the forecast rain per edge for the hour you reach it (NaN where
    unknown); a segment keeps the wettest of its edges."""
    out = []
    for k, edge_id in enumerate(edge_ids):
        risk_class, source, slide = edge_risk(graph, edge_id)
        mm = None if rain_mm is None or np.isnan(rain_mm[k]) else round(float(rain_mm[k]), 1)
        if out and (out[-1]["risk_class"], out[-1]["risk_source"],
                    out[-1]["landslide_prone"]) == (risk_class, source, slide):
            out[-1]["end"] = bounds[k + 1]
            if mm is not None and (out[-1]["forecast_rain_mm"] or 0) <= mm:
                out[-1]["forecast_rain_mm"] = mm
            continue
        out.append({
            "start": bounds[k],
            "end": bounds[k + 1],
            "risk_class": risk_class,
            "risk_source": source,
            "landslide_prone": slide,
            "forecast_rain_mm": mm,
            "report_id": None,
        })
    return out


def km_by_class(graph, edge_ids):
    """km of route per risk class, plus km with no score."""
    lengths = np.asarray(graph.edge_len_m)[edge_ids] / 1000
    codes = np.asarray(graph.edge_risk)[edge_ids]
    by_class = {name: round(float(lengths[codes == i].sum()), 1)
                for i, name in enumerate(RISK_CLASSES)}
    return by_class, round(float(lengths[codes >= len(RISK_CLASSES)].sum()), 1)


def _stitch(graph, edge_ids):
    """Join each edge's polyline into one line, without repeating junctions.

    Also returns bounds: edge k runs from coordinates[bounds[k]] to
    coordinates[bounds[k + 1]].
    """
    coordinates = []
    bounds = [0]
    for edge_id in edge_ids:
        span = graph.edge_coords(edge_id)
        if coordinates:
            span = span[1:]  # its first point is the previous edge's last
        coordinates.extend([round(float(x), 6), round(float(y), 6)] for x, y in span)
        bounds.append(len(coordinates) - 1)
    return coordinates, bounds
