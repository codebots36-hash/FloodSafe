"""Shortest-path routing over the road graph.

Input   a RoadGraph, a start and an end lat/lon
Output  a route: its polyline, its length and time, and the edges behind it

Only "fastest" exists here. Risk-weighted "safest" routing arrives once
pipeline/build_road_risk.py has scored every edge; see S11/S12.
"""

import numpy as np
from scipy.sparse.csgraph import dijkstra

# Further than this from any road and the request is a mistake rather than a
# trip: a tap in the middle of a reservoir, or a GPS fix that has not settled.
MAX_SNAP_M = 2000


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

    coordinates = _stitch(graph, edge_ids)
    distance_m = float(np.asarray(graph.edge_len_m)[edge_ids].sum())
    duration_s = float(graph.travel_s[edge_ids].sum())

    return {
        "nodes": nodes,
        "edge_ids": edge_ids,
        "coordinates": coordinates,
        "distance_m": distance_m,
        "duration_s": duration_s,
        "snapped_m": {"start": source_m, "end": target_m},
    }


def _stitch(graph, edge_ids):
    """Join each edge's polyline into one line, without repeating junctions."""
    coordinates = []
    for edge_id in edge_ids:
        span = graph.edge_coords(edge_id)
        if coordinates:
            span = span[1:]  # its first point is the previous edge's last
        coordinates.extend([round(float(x), 6), round(float(y), 6)] for x, y in span)
    return coordinates
