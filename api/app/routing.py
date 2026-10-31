"""Shortest-path routing over the road graph.

Input   a RoadGraph, a start and an end lat/lon
Output  a route: its polyline, its length and time, and the edges behind it

"fastest" minimises travel time. "safest" minimises travel time x a risk
penalty per edge, using the classes pipeline/build_road_risk.py gave every
edge.
"""

import numpy as np
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


def route(graph, start, end, weights=None):
    """Cheapest path from start to end, each a (lat, lon) pair.

    weights is an optional per-directed-edge cost array; without it the cost
    is travel time, which is what "fastest" means.
    """
    source, source_m = graph.snap(*start)
    target, target_m = graph.snap(*end)

    for label, metres in (("start", source_m), ("destination", target_m)):
        if metres > MAX_SNAP_M:
            raise RoutingError(
                "too_far_from_road",
                f"The {label} is {metres / 1000:.1f} km from the nearest road.",
            )

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

    coordinates, bounds = _stitch(graph, edge_ids)
    distance_m = float(np.asarray(graph.edge_len_m)[edge_ids].sum())
    duration_s = float(graph.travel_s[edge_ids].sum())

    return {
        "nodes": nodes,
        "edge_ids": edge_ids,
        "coordinates": coordinates,
        "bounds": bounds,
        "distance_m": distance_m,
        "duration_s": duration_s,
        "snapped_m": {"start": source_m, "end": target_m},
    }


def safest_weights(graph, level):
    """Per-directed-edge Safest cost for a PENALTIES level."""
    p = PENALTIES[level]
    lut = np.full(256, p["unscored"], dtype=np.float64)
    for code, name in enumerate(RISK_CLASSES):
        lut[code] = p[name]
    slide = np.where(np.asarray(graph.edge_landslide), p["landslide"], 1.0)
    return graph.travel_s * lut[np.asarray(graph.edge_risk)] * slide


def edge_risk(graph, edge_id):
    """(risk_class, risk_source, landslide_prone); class and source are None
    for an edge with no score."""
    code = int(graph.edge_risk[edge_id])
    if code >= len(RISK_CLASSES):
        return None, None, False
    source = "surveyed" if graph.edge_surveyed[edge_id] else "modelled"
    return RISK_CLASSES[code], source, bool(graph.edge_landslide[edge_id])


def segments(graph, edge_ids, bounds):
    """Consecutive edges with the same class, source and landslide flag
    merge into one segment; start/end index the route's coordinates."""
    out = []
    for k, edge_id in enumerate(edge_ids):
        risk_class, source, slide = edge_risk(graph, edge_id)
        if out and (out[-1]["risk_class"], out[-1]["risk_source"],
                    out[-1]["landslide_prone"]) == (risk_class, source, slide):
            out[-1]["end"] = bounds[k + 1]
            continue
        out.append({
            "start": bounds[k],
            "end": bounds[k + 1],
            "risk_class": risk_class,
            "risk_source": source,
            "landslide_prone": slide,
            "forecast_rain_mm": None,
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
