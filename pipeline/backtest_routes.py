"""Backtest: does Safest avoid real landslides, or just take a different road?

Inputs
    pipeline/out/graph/                 road graph and risk classes (build_graph.py,
                                        build_road_risk.py)
    the landslide model's inputs        (train_landslide_model.py)
    api/data/localities.json, api/data/weather_history/   (named replays)
Outputs
    pipeline/out/backtest_routes.json
    pipeline/cache/backtest/road_features.npy   features of every road point, reused

Why this test: a route judged by the risk layer that chose it always looks
safer. Here the judge is independent: NASA Global Landslide Catalog events
the landslide model never saw.

How
- The same 5 spatially blocked folds as train_landslide_model.py. For each
  fold the model is refitted without that fold's landslides, and roads are
  flagged landslide-prone by the deployed rule (build_road_risk.prone_spans).
  Flood classes (atlas, FFPI) never saw the catalogue, so they stay as built.
- Fastest and Safest (strict, mild past MAX_DETOUR, as the API does) between
  every pair of the 83 towns in localities.json. No rain: this tests the risk
  layer, not a day's forecast.
- An encounter is a Fastest route passing within R of a held-out landslide
  (its snapped road point) more than 5 km from either end; nearer than that
  no route could avoid it. Safest "avoids" it if it never comes within R.
- Control: on the same Fastest route, a random road point at least 5 km from
  any catalogued landslide. If Safest only avoided landslides as often as it
  avoids these, it is just taking another road, not finding the danger.
- Many slides sit on the only road up a valley. So each encounter also asks
  whether any way around existed: the same trip with the stretch within R
  removed, within MAX_DETOUR x the Fastest time. Avoidance is then compared
  where a way around existed, for the slide and for its control.
- Rates are averaged per landslide (busy highways would otherwise count one
  slide hundreds of times), with a bootstrap over landslides for the gap.

Usage (after build_road_risk.py):
    python pipeline/backtest_routes.py
"""

import json
import os
import sys
import time

import numpy as np
from numba import njit
from osgeo import gdal
from pyproj import Transformer
from scipy.sparse.csgraph import dijkstra
from scipy.spatial import cKDTree
from sklearn.model_selection import GroupKFold

import train_landslide_model as slide_model
from build_ffpi import CACHE as FFPI_CACHE, OUT, log
from build_road_risk import GRAPH, prone_spans, sample_spans, to_cells

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "api"))
from app.graph import RoadGraph  # noqa: E402
from app.routing import MAX_DETOUR, MAX_SNAP_M, safest_weights  # noqa: E402

CACHE = os.path.join(os.path.dirname(FFPI_CACHE), "backtest")
RESULT_PATH = os.path.join(OUT, "backtest_routes.json")
RADII_M = [500, 1000, 2000]
MAIN_RADIUS_M = 1000
END_CLEAR_M = 5000
CONTROL_MIN_M = 5000
BOOTSTRAP = 2000
# The way-around check runs one search per encounter, so at most this many
# random encounters per landslide get one.
AROUND_PER_SLIDE = 20
SEED = 42
# Named disasters, replayed with the rain that fell. The cut point is the
# place the road was lost, by its OSM locality.
NAMED = [
    {"name": "Dharali, 5 Aug 2025", "replay": "2025-08-05",
     "from": "uttarkashi", "to": "gangotri", "cut": "dharali"},
    {"name": "Kedarnath, 16 Jun 2013", "replay": "2013-06-16",
     "from": "rudraprayag", "to": "sonprayag", "cut": "sonprayag"},
]

to_utm = Transformer.from_crs("EPSG:4326", "EPSG:32644", always_xy=True)


@njit(cache=True)
def path_edges(pred, indptr, indices, csr_eid, source, target):
    """Directed edge ids from source to target, read back off a predecessor array."""
    nodes = [target]
    node = target
    while node != source:
        node = pred[node]
        nodes.append(node)
    out = np.empty(len(nodes) - 1, dtype=np.int64)
    for k in range(len(nodes) - 1, 0, -1):
        a, b = nodes[k], nodes[k - 1]
        lo, hi = indptr[a], indptr[a + 1]
        while lo < hi:  # each CSR row is sorted by column
            mid = (lo + hi) // 2
            if indices[mid] < b:
                lo = mid + 1
            else:
                hi = mid
        out[len(nodes) - 1 - k] = csr_eid[lo]
    return out


def road_points():
    """Every road sample point: span id, UTM x/y, grid cell, and whether it is scored."""
    off = np.load(os.path.join(GRAPH, "edge_geom_offsets.npy"))
    ll = np.load(os.path.join(GRAPH, "edge_geom_lonlat.npy"))
    span, lon, lat = sample_spans(off, ll)
    ref = gdal.Open(os.path.join(FFPI_CACHE, "state_mask.tif"))
    gt, shape = ref.GetGeoTransform(), (ref.RasterYSize, ref.RasterXSize)
    row, col, ok = to_cells(lon, lat, gt, shape)
    index = slide_model.sample(slide_model.INDEX_PATH, row, col)
    in_state = ok & np.isfinite(index) & (index != 255)  # as build_road_risk.score_edges
    x, y = to_utm.transform(lon, lat)
    return span, np.column_stack([x, y]), row, col, in_state, len(off) - 1


def road_features(row, col, in_state):
    path = os.path.join(CACHE, "road_features.npy")
    if os.path.exists(path):
        return np.load(path)
    todo = np.flatnonzero(in_state)
    X = np.concatenate([slide_model.features(row[c], col[c])
                        for c in np.array_split(todo, max(1, len(todo) // 1_000_000))])
    np.save(path, X)
    return X


def fold_events():
    """Training points and folds exactly as train_landslide_model.main() makes them."""
    ref = gdal.Open(os.path.join(FFPI_CACHE, "state_mask.tif"))
    gt, shape = ref.GetGeoTransform(), (ref.RasterYSize, ref.RasterXSize)
    row, col, y, px, py, _ = slide_model.training_points(gt, shape)
    X = slide_model.features(row, col)
    index = slide_model.sample(slide_model.INDEX_PATH, row, col)
    keep = np.isfinite(X[:, :6]).all(axis=1) & np.isfinite(index) & (index != 255)
    X, y, px, py = X[keep], y[keep], px[keep], py[keep]
    groups = (np.floor(px / slide_model.BLOCK_M).astype(np.int64) * 100_000
              + np.floor(py / slide_model.BLOCK_M).astype(np.int64))
    folds = list(GroupKFold(n_splits=slide_model.FOLDS).split(X, y, groups))
    return X, y, np.column_stack([px, py]), folds


def towns(graph):
    with open(os.path.join(os.path.dirname(HERE), "api", "data", "localities.json"), encoding="utf-8") as fh:
        items = json.load(fh)["items"]
    out = []
    for t in items:
        if t["parent"] is None:
            node, gap = graph.snap(t["lat"], t["lon"])
            if gap <= MAX_SNAP_M:
                out.append((t["name"], node))
    return out


def near_spans(tree, span, xy, radius):
    return np.unique(span[tree.query_ball_point(xy, radius)])


def route_backtest(graph, X, y, events_xy, folds, span_info):
    span, pts_xy, row, col, in_state, n_span = span_info
    t0 = time.time()
    F = road_features(row, col, in_state)
    log(f"road features: {len(F):,} points", t0)
    geom_id = np.asarray(graph.edge_geom_id)
    tree = cKDTree(pts_xy)

    # Each landslide belongs to the one fold that holds it out.
    event_fold = np.full(len(y), -1)
    for k, (_, test) in enumerate(folds):
        event_fold[test] = k
    slides = np.flatnonzero(y == 1)
    slide_xy, slide_fold = events_xy[slides], event_fold[slides]
    near = {r: [set(near_spans(tree, span, p, r)) for p in slide_xy] for r in RADII_M}

    # Control points: scored road points at least CONTROL_MIN_M from any catalogued slide.
    gap, _ = cKDTree(slide_xy).query(pts_xy)
    eligible = in_state & (gap >= CONTROL_MIN_M)
    order = np.argsort(span, kind="stable")
    starts = np.searchsorted(span[order], np.arange(n_span + 1))

    flags = []
    for k, (train, _) in enumerate(folds):
        fitted = slide_model.model().fit(X[train], y[train])
        p = np.zeros(len(span), dtype=np.float32)
        p[in_state] = fitted.predict_proba(F)[:, 1]
        flags.append(prone_spans(span, p, in_state, n_span)[0][geom_id])
        log(f"fold {k + 1}: refitted without its {int(y[folds[k][1]].sum())} landslides, roads flagged", t0)

    stops = towns(graph)
    node_xy = np.column_stack(to_utm.transform(np.asarray(graph.node_lon)[[n for _, n in stops]],
                                               np.asarray(graph.node_lat)[[n for _, n in stops]]))
    csr = (graph.csr_indptr, graph.csr_indices, graph.csr_edge_id)
    length = np.asarray(graph.edge_len_m)
    rng = np.random.default_rng(SEED)
    weighted = {}

    def matrix(k, level):
        if (k, level) not in weighted:
            graph.edge_landslide = flags[k]
            m = graph.csr.copy()
            m.data = np.maximum(safest_weights(graph, level)[graph.csr_edge_id], 1e-3)
            weighted[(k, level)] = m
        return weighted[(k, level)]

    encounters = []
    for i, (_, s) in enumerate(stops):
        cost, pred = dijkstra(graph.csr, indices=s, return_predecessors=True)
        trees = {}
        for j, (_, t) in enumerate(stops):
            if j == i or not np.isfinite(cost[t]):
                continue
            fast = path_edges(pred, *csr, s, t)
            fast_spans = set(geom_id[fast].tolist())
            ends = node_xy[[i, j]]
            for e in range(len(slides)):
                if not near[MAIN_RADIUS_M][e] & fast_spans:
                    continue
                if np.hypot(*(ends - slide_xy[e]).T).min() < END_CLEAR_M:
                    continue
                k = slide_fold[e]
                if (k, "strict") not in trees:
                    trees[(k, "strict")] = dijkstra(matrix(k, "strict"), indices=s, return_predecessors=True)[1]
                safe = path_edges(trees[(k, "strict")], *csr, s, t)
                if length[safe].sum() > MAX_DETOUR * length[fast].sum():
                    if (k, "mild") not in trees:
                        trees[(k, "mild")] = dijkstra(matrix(k, "mild"), indices=s, return_predecessors=True)[1]
                    safe = path_edges(trees[(k, "mild")], *csr, s, t)
                safe_spans = set(geom_id[safe].tolist())

                pool = np.concatenate([order[starts[g]:starts[g + 1]] for g in fast_spans])
                pool = pool[eligible[pool]]
                pool = pool[np.hypot(*(pts_xy[pool][:, None, :] - ends[None]).transpose(2, 0, 1)).min(1)
                            >= END_CLEAR_M]
                control = pts_xy[rng.choice(pool)] if len(pool) else None
                encounters.append({
                    "slide": e, "trip": (s, t, float(graph.travel_s[fast].sum())),
                    "control_spans": None if control is None else near_spans(tree, span, control, MAIN_RADIUS_M),
                    "time_ratio": float(graph.travel_s[safe].sum() / graph.travel_s[fast].sum()),
                    "avoided": {r: not (near[r][e] & safe_spans) for r in RADII_M},
                    "control_avoided": None if control is None else
                        {r: not (set(near_spans(tree, span, control, r)) & safe_spans) for r in RADII_M},
                })
        if (i + 1) % 10 == 0:
            log(f"routes from {i + 1}/{len(stops)} towns, {len(encounters)} encounters", t0)

    # Was there any way around? Only for a random sample per slide: one search each.
    csr_span = geom_id[graph.csr_edge_id]
    by_slide = {}
    for e in encounters:
        by_slide.setdefault(e["slide"], []).append(e)
    sampled = [e for group in by_slide.values()
               for e in rng.permutation(np.array(group, dtype=object))[:AROUND_PER_SLIDE]]
    for n, e in enumerate(sampled):
        s, t, fast_s = e["trip"]
        e["around"] = way_around(graph, csr_span, s, t, list(near[MAIN_RADIUS_M][e["slide"]]), fast_s)
        if e["control_spans"] is not None:
            e["control_around"] = way_around(graph, csr_span, s, t, e["control_spans"], fast_s)
        if (n + 1) % 500 == 0:
            log(f"way around checked for {n + 1}/{len(sampled)} sampled encounters", t0)
    return encounters, len(stops), int(len(slides))


def way_around(graph, csr_span, s, t, blocked, fast_s):
    """Whether s still reaches t within MAX_DETOUR x fast_s with the blocked spans removed."""
    m = graph.csr.copy()
    m.data[np.isin(csr_span, blocked)] = np.inf
    cost = dijkstra(m, indices=s, limit=MAX_DETOUR * fast_s)
    return bool(np.isfinite(cost[t]))


def summarise(encounters):
    paired = [e for e in encounters if e["control_avoided"] is not None]
    slides = sorted({e["slide"] for e in paired})
    out = {"encounters": len(paired), "landslides": len(slides),
           "median_time_ratio": round(float(np.median([e["time_ratio"] for e in paired])), 3)}
    rng = np.random.default_rng(SEED)
    for r in RADII_M:
        per = np.array([[np.mean([e["avoided"][r] for e in paired if e["slide"] == s]),
                         np.mean([e["control_avoided"][r] for e in paired if e["slide"] == s])]
                        for s in slides])
        boot = per[rng.integers(0, len(per), (BOOTSTRAP, len(per)))].mean(1)
        gap = boot[:, 0] - boot[:, 1]
        out[f"{r}m"] = {"landslides_avoided": round(float(per[:, 0].mean()), 3),
                        "controls_avoided": round(float(per[:, 1].mean()), 3),
                        "gap_95ci": [round(float(np.percentile(gap, 2.5)), 3),
                                     round(float(np.percentile(gap, 97.5)), 3)]}

    # Where a way around existed (sampled encounters only), at MAIN_RADIUS_M.
    r = MAIN_RADIUS_M
    checked = [e for e in paired if "around" in e and "control_around" in e]
    per = []
    for s in sorted({e["slide"] for e in checked}):
        mine = [e for e in checked if e["slide"] == s]
        slide_ok = [e["avoided"][r] for e in mine if e["around"]]
        control_ok = [e["control_avoided"][r] for e in mine if e["control_around"]]
        per.append([np.mean([e["around"] for e in mine]), np.mean([e["control_around"] for e in mine]),
                    np.mean(slide_ok) if slide_ok else np.nan, np.mean(control_ok) if control_ok else np.nan])
    per = np.array(per)
    both = per[np.isfinite(per[:, 2]) & np.isfinite(per[:, 3])]
    boot = both[rng.integers(0, len(both), (BOOTSTRAP, len(both)))]
    gap = boot[:, :, 2].mean(1) - boot[:, :, 3].mean(1)
    out["where_a_way_around_existed"] = {
        "radius_m": r,
        "encounters_checked": len(checked),
        "share_with_a_way_around": {"landslides": round(float(per[:, 0].mean()), 3),
                                    "controls": round(float(per[:, 1].mean()), 3)},
        "landslides_with_a_way_around": int(np.isfinite(per[:, 2]).sum()),
        "landslides_avoided": round(float(np.nanmean(per[:, 2])), 3),
        "controls_avoided": round(float(np.nanmean(per[:, 3])), 3),
        "gap_95ci_paired": [round(float(np.percentile(gap, 2.5)), 3),
                            round(float(np.percentile(gap, 97.5)), 3)],
        "paired_landslides": int(len(both)),
    }
    return out


def named_replays():
    """The two named disasters, through the API's own /route on their replayed rain."""
    from fastapi.testclient import TestClient
    from app.main import app

    with open(os.path.join(os.path.dirname(HERE), "api", "data", "localities.json"), encoding="utf-8") as fh:
        places = {p["id"]: p for p in json.load(fh)["items"]}
    out = []
    with TestClient(app) as client:
        for case in NAMED:
            a, b, cut = places[case["from"]], places[case["to"]], places[case["cut"]]
            cut_xy = np.array(to_utm.transform(cut["lon"], cut["lat"]))
            row = {"case": case["name"], "from": a["name"], "to": b["name"], "cut_point": cut["name"]}
            for mode in ("fastest", "safest"):
                body = client.get("/route", params={
                    "from": f"{a['lat']},{a['lon']}", "to": f"{b['lat']},{b['lon']}",
                    "mode": mode, "replay": case["replay"]}).json()
                if "geometry" not in body:
                    row[mode] = {"error": body}
                    continue
                xy = np.column_stack(to_utm.transform(*np.array(body["geometry"]["coordinates"]).T))
                row[mode] = {"km": body["distance_km"], "minutes": body["duration_min"],
                             "passes_cut_point_within_1km": bool(np.hypot(*(xy - cut_xy).T).min() <= 1000),
                             "km_by_class": body["summary"]["km_by_class"], "notes": body["notes"]}
            out.append(row)
    return out


def main():
    t0 = time.time()
    os.makedirs(CACHE, exist_ok=True)
    X, y, events_xy, folds = fold_events()
    log(f"{int(y.sum())} landslides in {len(folds)} spatial folds", t0)
    graph = RoadGraph(GRAPH)
    encounters, n_towns, n_slides = route_backtest(graph, X, y, events_xy, folds, road_points())
    result = {
        "question": "Does Safest avoid real landslides it was never trained on, more often "
                    "than it avoids any other stretch of the Fastest route?",
        "events": f"{n_slides} NASA Global Landslide Catalog landslides in Uttarakhand placed "
                  "within 10 km, each at its nearest road point",
        "routes": f"Fastest and Safest (no rain) between every pair of {n_towns} towns",
        "held_out": "landslide flags from a model refitted without the fold holding each landslide",
        "control": f"a random road point on the same Fastest route >= {CONTROL_MIN_M // 1000} km "
                   "from any catalogued landslide",
        "summary": summarise(encounters),
        "named_replays": named_replays(),
        "limits": [
            "Catalogue points are placed only to 1-10 km and moved to the nearest road, "
            "so the matched road may not be the one that failed.",
            "No rain: the test is of the risk layer, not of forecast-timed routing.",
            "Flood classes (atlas, FFPI) are used as built; they never saw the catalogue.",
        ],
    }
    with open(RESULT_PATH, "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=2, ensure_ascii=False)
    log(json.dumps(result["summary"], indent=2), t0)
    log(json.dumps(result["named_replays"], indent=2, ensure_ascii=False), t0)
    log(f"saved {os.path.relpath(RESULT_PATH)}", t0)


def check():
    """path_edges reads the right edges back off a tiny graph."""
    from scipy.sparse import csr_matrix
    # 0 -> 1 -> 2 and a dearer 0 -> 2; edge ids 7, 8, 9
    indptr = np.array([0, 2, 3, 3])
    indices = np.array([1, 2, 2])
    m = csr_matrix((np.array([1.0, 5.0, 1.0]), indices, indptr), shape=(3, 3))
    _, pred = dijkstra(m, indices=0, return_predecessors=True)
    assert path_edges(pred, indptr, indices, np.array([7, 9, 8]), 0, 2).tolist() == [7, 8]


if __name__ == "__main__":
    check()
    main()
