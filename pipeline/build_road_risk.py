"""Score every road edge for flood and landslide risk.

Inputs
    pipeline/out/graph/                 road graph (build_graph.py)
    pipeline/out/ffpi_class.tif         modelled flood class, 30 m (build_ffpi.py)
    pipeline/out/atlas_hazard.geojson   surveyed flood zones (atlas_classify.py)
    pipeline/cache/ffpi/{dem,slope,landcover,state_mask}.tif   (build_ffpi.py)
    pipeline/raw/landslides/global_landslide_catalog_nasa.zip  (NASA GLC)
Outputs
    pipeline/out/graph/edge_risk.npy       uint8 per directed edge: 0-3 = LOW..EXTREME,
                                           255 = not scored (outside the state / no data)
    pipeline/out/graph/edge_surveyed.npy   bool: the edge's class comes from the atlas
    pipeline/out/graph/edge_landslide.npy  bool: crosses a landslide-prone cell
    pipeline/out/layers/road_risk.geojson  roads MODERATE or worse, simplified ~20 m
    pipeline/out/layers/atlas.geojson      atlas polygons, simplified
    pipeline/out/road_risk_meta.json

Landslide-prone comes from the trained model (train_landslide_model.py)
when that model beat this index on held-out ground ("use_model" in
out/landslide_model_meta.json): each road takes its worst point's
probability, and the top 10% of state road length is prone. Otherwise it falls back to the transparent
index below: a weighted sum of slope, local relief and land cover scores
(tables below; a modelling choice), checked, never tuned, against the NASA
Global Landslide Catalog. Index-prone = the top 10% of the index in the state.

Each road is sampled about every 30 m. A sample takes the atlas class where it
falls in an atlas polygon ("surveyed"), else the FFPI class ("modelled"). The
edge takes its worst sample; on a tie the surveyed source wins.

Usage:
    python pipeline/build_road_risk.py
"""

import datetime
import json
import math
import os
import time

import geopandas as gpd
import numpy as np
from osgeo import gdal
from pyproj import Transformer
from rasterio.features import rasterize
from rasterio.transform import Affine
from scipy import ndimage
from scipy.stats import rankdata
from shapely.geometry import LineString, MultiLineString, mapping
from shapely.ops import linemerge

from build_ffpi import CACHE as FFPI_CACHE, CLASS_NAMES, CRS, OUT, RAW, RES, \
    create_like, cutoffs_from_hist, log, row_blocks
import train_landslide_model as slide_model

GRAPH = os.path.join(OUT, "graph")
LAYERS = os.path.join(OUT, "layers")
CACHE = os.path.join(os.path.dirname(FFPI_CACHE), "road_risk")
LANDSLIDE_ZIP = os.path.join(RAW, "landslides", "global_landslide_catalog_nasa.zip")

# ---------------------------------------------------------------------------
# Landslide susceptibility: scores 0-1 per factor, then a weighted sum.
# A MODELLING CHOICE following common practice (slope, relief, cover), not a
# calibrated model. Do not tune these to the validation AUC.
# ---------------------------------------------------------------------------
WEIGHTS = {"slope": 0.5, "relief": 0.3, "landcover": 0.2}
# Slope in degrees: soil slopes fail most around 30-45; cliffs above that are
# mostly bare rock (rockfall, less material to slide).
SLOPE_EDGES_DEG = [10, 20, 30, 45, 60]
SLOPE_SCORES = [0.0, 0.3, 0.7, 1.0, 0.7, 0.4]
RELIEF_WINDOW_PX = 33          # ~1 km square
RELIEF_FULL_M = 1000           # relief of 1 km or more scores 1
LANDCOVER_SCORE = {            # ESA WorldCover class -> score
    10: 0.3,   # tree cover
    20: 0.6,   # shrubland
    30: 0.6,   # grassland
    40: 0.5,   # cropland (terraces)
    50: 0.4,   # built-up (road cuts, but paved)
    60: 1.0,   # bare / sparse vegetation
    70: 0.2,   # snow and ice
    80: 0.0,   # permanent water
    90: 0.0,   # herbaceous wetland
    95: 0.0,   # mangroves
    100: 0.6,  # moss and lichen
}
INDEX_SCALE = 200              # index 0-1 stored as uint8 0-200; 255 = no data
PRONE_PERCENTILE = 90
PRONE_ROAD_SHARE = 0.10          # trained model: share of state road length flagged
N_RANDOM = 10_000
AUC_WARN = 0.6

SAMPLE_M = 30
SIMPLIFY_DEG = 0.0002          # ~20 m
ATLAS_SIMPLIFY_DEG = 0.0005    # ~50 m; the atlas is only good to a few km
COORD_DECIMALS = 4             # ~10 m, finer than the 20 m simplification

gdal.UseExceptions()


def step(name, path):
    if os.path.exists(path):
        log(f"{name}: cached, skipping")
        return False
    log(f"{name}: building")
    return True


def build_landslide_index(path):
    t0 = time.time()
    if not step("landslide index", path):
        return
    open_ = lambda n: gdal.Open(os.path.join(FFPI_CACHE, n))
    ds = {k: open_(f"{k}.tif") for k in ("dem", "slope", "landcover", "state_mask")}
    b = {k: d.GetRasterBand(1) for k, d in ds.items()}
    ref = ds["state_mask"]
    w, h = ref.RasterXSize, ref.RasterYSize
    dem_nd, slope_nd = b["dem"].GetNoDataValue(), b["slope"].GetNoDataValue()
    lc_lut = np.full(256, np.nan, dtype=np.float32)
    for k, v in LANDCOVER_SCORE.items():
        lc_lut[k] = v
    slope_lut = np.array(SLOPE_SCORES, dtype=np.float32)
    halo = RELIEF_WINDOW_PX // 2

    tmp = path + ".tmp.tif"
    dst = create_like(tmp, ref, gdal.GDT_Byte, 255)
    out = dst.GetRasterBand(1)
    for r0, n in row_blocks(h):
        a0, a1 = max(0, r0 - halo), min(h, r0 + n + halo)
        dem = b["dem"].ReadAsArray(0, a0, w, a1 - a0)
        valid = dem != dem_nd
        hi = ndimage.maximum_filter(np.where(valid, dem, -np.inf), RELIEF_WINDOW_PX)
        lo = ndimage.minimum_filter(np.where(valid, dem, np.inf), RELIEF_WINDOW_PX)
        relief = (hi - lo)[r0 - a0:r0 - a0 + n]

        slope = b["slope"].ReadAsArray(0, r0, w, n)
        lc = b["landcover"].ReadAsArray(0, r0, w, n)
        mask = b["state_mask"].ReadAsArray(0, r0, w, n) == 1
        s_lc = lc_lut[lc]
        idx = (WEIGHTS["slope"] * slope_lut[np.digitize(slope, SLOPE_EDGES_DEG)]
               + WEIGHTS["relief"] * np.clip(relief / RELIEF_FULL_M, 0, 1)
               + WEIGHTS["landcover"] * s_lc)
        ok = mask & (slope != slope_nd) & np.isfinite(relief) & ~np.isnan(s_lc)
        out.WriteArray(np.where(ok, np.round(idx * INDEX_SCALE), 255).astype(np.uint8), 0, r0)
    dst = None
    os.replace(tmp, path)
    log("landslide index: slope, 1 km relief and land cover", t0)


def read_grid(path):
    ds = gdal.Open(path)
    return ds.GetRasterBand(1).ReadAsArray(), ds.GetGeoTransform()


def to_cells(lon, lat, gt, shape):
    """lon/lat -> (row, col, inside-grid) on the UTM 30 m grid."""
    x, y = Transformer.from_crs("EPSG:4326", CRS, always_xy=True).transform(lon, lat)
    col = np.floor((np.asarray(x) - gt[0]) / gt[1]).astype(np.int64)
    row = np.floor((np.asarray(y) - gt[3]) / gt[5]).astype(np.int64)
    ok = (row >= 0) & (row < shape[0]) & (col >= 0) & (col < shape[1])
    return np.where(ok, row, 0), np.where(ok, col, 0), ok


def auc(pos, neg):
    """ROC AUC by the rank-sum (Mann-Whitney) formula, ties averaged."""
    ranks = rankdata(np.concatenate([pos, neg]))
    n1, n0 = len(pos), len(neg)
    return float((ranks[:n1].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))


def validate(index, gt, state_mask, road_vals):
    """AUC vs random state cells (the spec), and vs random road samples:
    the catalogue records slides where people and roads are, and the index is
    only ever used on roads, so the second is the fairer comparison."""
    t0 = time.time()
    glc = gpd.read_file(f"zip://{LANDSLIDE_ZIP}!global_landslide_catalog_NASA.shp")
    state = gpd.read_file(os.path.join(OUT, "state_boundary.geojson")).to_crs(glc.crs)
    ev = glc[glc.within(state.geometry.iloc[0])]
    in_state = len(ev)
    row, col, ok = to_cells(ev.geometry.x.values, ev.geometry.y.values, gt, index.shape)
    vals = index[row, col]
    ok &= vals != 255
    ev, vals = ev[ok], vals[ok].astype(float)

    rng = np.random.default_rng(42)
    rand = []
    while len(rand) < N_RANDOM:
        r = rng.integers(0, index.shape[0], N_RANDOM)
        c = rng.integers(0, index.shape[1], N_RANDOM)
        keep = (state_mask[r, c] == 1) & (index[r, c] != 255)
        rand.extend(index[r[keep], c[keep]].tolist())
    rand = np.array(rand[:N_RANDOM], dtype=float)

    road_rand = rng.choice(road_vals, N_RANDOM, replace=False).astype(float)
    acc_ok = ev["location_a"].isin(["exact", "1km", "5km"]).values
    result = {
        "events_in_state": in_state,
        "events_used": int(len(vals)),
        "random_points": N_RANDOM,
        "auc_all": round(auc(vals, rand), 3),
        "auc_vs_random_road_points": round(auc(vals, road_rand), 3),
        "events_located_within_5km": int(acc_ok.sum()),
        "auc_located_within_5km": round(auc(vals[acc_ok], rand), 3) if acc_ok.sum() else None,
        "location_accuracy_counts": {k: int(v) for k, v in ev["location_a"].value_counts().items()},
    }
    log(f"landslide check vs NASA GLC: AUC {result['auc_all']} on {result['events_used']} events "
        f"(AUC {result['auc_located_within_5km']} on the {result['events_located_within_5km']} "
        f"placed within 5 km), {N_RANDOM:,} random points; "
        f"AUC {result['auc_vs_random_road_points']} vs {N_RANDOM:,} random road points", t0)
    if result["auc_all"] < AUC_WARN:
        log(f"WARNING: landslide AUC vs random state cells {result['auc_all']} is below {AUC_WARN}")
    return result


def sample_spans(off, ll):
    """Points about every SAMPLE_M along every geometry span -> (span id, lon, lat)."""
    n_pts = len(ll)
    starts = np.arange(n_pts - 1)
    seg = starts[np.isin(starts, off[1:] - 1, invert=True)]  # not a span's last vertex
    lon0, lat0 = ll[seg, 0].astype(np.float64), ll[seg, 1].astype(np.float64)
    lon1, lat1 = ll[seg + 1, 0].astype(np.float64), ll[seg + 1, 1].astype(np.float64)
    m_lat = 110574.0
    m_lon = 111320.0 * np.cos(np.radians((lat0 + lat1) / 2))
    length = np.hypot((lon1 - lon0) * m_lon, (lat1 - lat0) * m_lat)
    k = np.maximum(1, np.ceil(length / SAMPLE_M)).astype(np.int64)
    rep = np.repeat(np.arange(len(seg)), k)
    t = (np.arange(k.sum()) - np.repeat(np.cumsum(k) - k, k)) / np.repeat(k, k)
    lon = lon0[rep] + t * (lon1 - lon0)[rep]
    lat = lat0[rep] + t * (lat1 - lat0)[rep]
    span = np.searchsorted(off, seg, side="right")[rep] - 1
    last = off[1:] - 1
    return (np.concatenate([span, np.arange(len(last))]),
            np.concatenate([lon, ll[last, 0]]), np.concatenate([lat, ll[last, 1]]))


def atlas_grid(shape, gt):
    """Atlas class + 1 per cell on the 30 m grid (1-4 = LOW..EXTREME), 0 = not in the atlas."""
    atlas_gdf = gpd.read_file(os.path.join(OUT, "atlas_hazard.geojson")).to_crs(CRS)
    code = {n: i + 1 for i, n in enumerate(CLASS_NAMES)}
    return rasterize(((g, code[c]) for g, c in zip(atlas_gdf.geometry, atlas_gdf["hazard_class"])),
                     out_shape=shape, transform=Affine.from_gdal(*gt), dtype="uint8")


def score_edges(index, prone_cut, gt):
    t0 = time.time()
    ffpi, _ = read_grid(os.path.join(OUT, "ffpi_class.tif"))
    atlas = atlas_grid(ffpi.shape, gt)

    off = np.load(os.path.join(GRAPH, "edge_geom_offsets.npy"))
    ll = np.load(os.path.join(GRAPH, "edge_geom_lonlat.npy"))
    span, lon, lat = sample_spans(off, ll)
    row, col, ok = to_cells(lon, lat, gt, ffpi.shape)
    log(f"edges: {len(off) - 1:,} road spans sampled at {len(span):,} points", t0)

    a = atlas[row, col]
    f = ffpi[row, col]
    # score = 2 * class + surveyed, so max() gives the worst class, surveyed on a tie
    score = np.where(a > 0, 2 * (a.astype(np.int16) - 1) + 1,
                     np.where(f < 4, 2 * f.astype(np.int16), -1))
    score[~ok] = -1
    li = index[row, col]
    in_state = ok & (li != 255)
    n_span = len(off) - 1
    span_slide = np.zeros(n_span, dtype=bool)
    model, meta = trained_model()
    if model is not None:
        p = np.zeros(len(row), dtype=np.float32)
        todo = np.flatnonzero(in_state)
        for chunk in np.array_split(todo, max(1, len(todo) // 1_000_000)):
            p[chunk] = model.predict_proba(slide_model.features(row[chunk], col[chunk]))[:, 1]
        # A road takes its worst point. The cut is set on roads, weighted by their
        # sample count (~length), so PRONE_ROAD_SHARE of road length is flagged:
        # a cut on points would flag almost every long road somewhere along it.
        span_p = np.full(n_span, -1.0, dtype=np.float32)
        np.maximum.at(span_p, span[in_state], p[in_state])
        weight = np.bincount(span[in_state], minlength=n_span)
        order = np.argsort(-span_p)
        flagged = order[np.cumsum(weight[order]) <= PRONE_ROAD_SHARE * weight.sum()]
        span_slide[flagged] = True
        meta["prone_cutoff"] = round(float(span_p[flagged].min()), 4) if len(flagged) else None
        log(f"landslide-prone from the trained model (CV AUC {meta['validation']['model_auc']}): "
            f"worst-point probability >= {meta['prone_cutoff']}", t0)
    else:
        span_slide[span[in_state & (li > prone_cut)]] = True

    span_score = np.full(n_span, -1, dtype=np.int16)
    np.maximum.at(span_score, span, score)
    span_risk = np.where(span_score >= 0, span_score // 2, 255).astype(np.uint8)
    span_surveyed = (span_score >= 0) & (span_score % 2 == 1)
    log("edges: scored", t0)
    return span_risk, span_surveyed, span_slide, off, ll, li[in_state], meta


def trained_model():
    """(model, meta) when train_landslide_model.py's model beat the index, else (None, meta or None)."""
    if not os.path.exists(slide_model.META_PATH):
        return None, None
    with open(slide_model.META_PATH, encoding="utf-8") as fh:
        meta = json.load(fh)
    if not meta["use_model"]:
        log("landslide model did not beat the index on held-out ground; using the index")
        return None, meta
    import joblib
    return joblib.load(slide_model.MODEL_PATH), meta


def feature_collection(features):
    return {"type": "FeatureCollection", "features": features}


def write_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, separators=(",", ":"))
    os.replace(tmp, path)
    return os.path.getsize(path) / 1e6


def rounded(coords):
    return [[round(x, COORD_DECIMALS), round(y, COORD_DECIMALS)] for x, y in coords]


def export_layers(span_risk, span_surveyed, off, ll):
    t0 = time.time()
    os.makedirs(LAYERS, exist_ok=True)
    # Connected roads of the same class and source are merged into one line:
    # per-feature overhead, not coordinates, is most of the file otherwise.
    groups = {}
    for i in np.flatnonzero((span_risk >= 1) & (span_risk != 255)):
        key = (CLASS_NAMES[span_risk[i]], "surveyed" if span_surveyed[i] else "modelled")
        groups.setdefault(key, []).append(LineString(ll[off[i]:off[i + 1]]))
    feats = []
    for (risk_class, source), lines in groups.items():
        merged = linemerge(MultiLineString(lines))
        for line in getattr(merged, "geoms", [merged]):
            line = line.simplify(SIMPLIFY_DEG)
            feats.append({"type": "Feature",
                          "geometry": {"type": "LineString", "coordinates": rounded(line.coords)},
                          "properties": {"risk_class": risk_class, "risk_source": source}})
    mb_roads = write_json(os.path.join(LAYERS, "road_risk.geojson"), feature_collection(feats))

    atlas = gpd.read_file(os.path.join(OUT, "atlas_hazard.geojson"))
    afeats = []
    for c, g in zip(atlas["hazard_class"], atlas.geometry.simplify(ATLAS_SIMPLIFY_DEG)):
        geo = mapping(g)
        geo["coordinates"] = [rounded(ring) for ring in geo["coordinates"]]
        afeats.append({"type": "Feature", "geometry": geo, "properties": {"hazard_class": c}})
    mb_atlas = write_json(os.path.join(LAYERS, "atlas.geojson"), feature_collection(afeats))
    log(f"layers: road_risk.geojson {len(feats):,} roads, {mb_roads:.1f} MB; "
        f"atlas.geojson {len(afeats)} polygons, {mb_atlas:.2f} MB", t0)
    return {"road_risk_features": len(feats), "road_risk_mb": round(mb_roads, 2),
            "atlas_features": len(afeats), "atlas_mb": round(mb_atlas, 2)}


def check():
    """Fails loudly if sampling or AUC logic breaks."""
    off = np.array([0, 2, 5])
    ll = np.array([[78.0, 30.0], [78.001, 30.0], [79.0, 30.0], [79.0, 30.0005], [79.0, 30.001]],
                  dtype=np.float32)
    span, lon, lat = sample_spans(off, ll)
    assert set(span.tolist()) == {0, 1} and (span == 0).sum() == 5  # ~96 m: 4 + end
    assert abs(auc(np.array([2.0, 3.0]), np.array([0.0, 1.0])) - 1.0) < 1e-9
    assert abs(auc(np.array([1.0]), np.array([1.0])) - 0.5) < 1e-9


def main():
    check()
    t0 = time.time()
    os.makedirs(CACHE, exist_ok=True)
    idx_path = os.path.join(CACHE, "landslide_index.tif")
    build_landslide_index(idx_path)
    index, gt = read_grid(idx_path)
    state_mask, _ = read_grid(os.path.join(FFPI_CACHE, "state_mask.tif"))

    hist = np.bincount(index[(state_mask == 1) & (index != 255)], minlength=256)[:INDEX_SCALE + 1]
    prone_cut = cutoffs_from_hist(hist, [PRONE_PERCENTILE])[0]
    prone_share = 100 * hist[prone_cut + 1:].sum() / hist.sum()
    log(f"landslide-prone: index > {prone_cut / INDEX_SCALE:.3f} "
        f"({prone_share:.1f}% of state cells)")
    span_risk, span_surveyed, span_slide, off, ll, road_vals, slide_meta = score_edges(index, prone_cut, gt)
    validation = validate(index, gt, state_mask, road_vals)
    del index, state_mask, road_vals

    geom_id = np.load(os.path.join(GRAPH, "edge_geom_id.npy"))
    for name, arr in (("edge_risk", span_risk), ("edge_surveyed", span_surveyed),
                      ("edge_landslide", span_slide)):
        np.save(os.path.join(GRAPH, f"{name}.npy"), arr[geom_id])

    span_km = np.zeros(len(span_risk))
    span_km[geom_id] = np.load(os.path.join(GRAPH, "edge_len_m.npy")) / 1000
    scored = span_risk != 255
    km = {CLASS_NAMES[i]: round(float(span_km[span_risk == i].sum()), 1) for i in range(4)}
    total = float(span_km[scored].sum())
    surveyed_km = float(span_km[span_surveyed].sum())
    log("\nroad km by class (scored roads):")
    for n, v in km.items():
        log(f"  {n:<12}{v:>10,.0f} km  {100 * v / total:5.1f}%")
    log(f"  surveyed {surveyed_km:,.0f} km ({100 * surveyed_km / total:.1f}%), "
        f"modelled {total - surveyed_km:,.0f} km ({100 * (total - surveyed_km) / total:.1f}%)")
    log(f"  landslide-prone {span_km[span_slide].sum():,.0f} km; "
        f"not scored (outside the state) {span_km[~scored].sum():,.0f} km")

    layers = export_layers(span_risk, span_surveyed, off, ll)
    meta = {
        "built": datetime.date.today().isoformat(),
        "edge_risk_codes": {"0-3": CLASS_NAMES, "255": "not scored: outside the state or no data"},
        "rule": "worst of samples every ~30 m; atlas class where a sample is in an atlas polygon "
                "(surveyed), else FFPI class (modelled); surveyed wins a tie",
        "road_km_by_class": km,
        "road_km_scored": round(total, 1),
        "road_km_surveyed": round(surveyed_km, 1),
        "road_km_not_scored": round(float(span_km[~scored].sum()), 1),
        "road_km_landslide_prone": round(float(span_km[span_slide].sum()), 1),
        "landslide": {
            "weights": WEIGHTS, "slope_edges_deg": SLOPE_EDGES_DEG, "slope_scores": SLOPE_SCORES,
            "relief_window_m": RELIEF_WINDOW_PX * RES, "relief_full_m": RELIEF_FULL_M,
            "landcover_score": LANDCOVER_SCORE,
            "prone_rule": f"top {100 - PRONE_PERCENTILE}% of the index inside the state",
            "prone_cutoff": prone_cut / INDEX_SCALE,
            "validation_nasa_glc": validation,
            "note": "Transparent index; weights are a modelling choice and were not tuned to "
                    "the validation AUC. Used for the prone flag only when the trained model "
                    "did not beat it.",
            "trained_model": slide_meta,
        },
        "layers": layers,
    }
    write_json(os.path.join(OUT, "road_risk_meta.json"), meta)
    log("done", t0)


if __name__ == "__main__":
    main()
