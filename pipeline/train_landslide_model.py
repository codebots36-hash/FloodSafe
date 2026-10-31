"""Train a landslide susceptibility model on real recorded landslides.

Inputs
    pipeline/raw/landslides/global_landslide_catalog_nasa.zip  NASA Global Landslide Catalog
    pipeline/cache/ffpi/{dem,slope,landcover,soil_hsg,forest_pct,state_mask}.tif  (build_ffpi.py)
    pipeline/out/ffpi.tif                                      (build_ffpi.py)
    pipeline/out/graph/edge_geom_*.npy                         road lines (build_graph.py)
Outputs
    pipeline/cache/landslide/{relief_1km,slope_1km}.tif   1 km context rasters
    pipeline/out/landslide_model.joblib                   the fitted model
    pipeline/out/landslide_model_meta.json                features, cross-validated skill, cutoff

Why a model: the hand-weighted index in build_road_risk.py separates the
catalogue's landslides from random road points only moderately (AUC 0.71) and
from random cells no better than chance (0.49). This learns the weights from
the events instead.

Keeping the number honest
- Labels: catalogue events in Uttarakhand placed to within 10 km, each moved
  to the nearest road point within its stated accuracy. Coarser ones (25 km,
  50 km, unknown) say almost nothing about the terrain at a point.
- Background: random points on roads, at least 5 km from any event. The
  catalogue is collected from news, so slides are recorded where roads and
  people are; with both classes on roads the model cannot just learn "on a
  road", and roads are where it is used.
- Cross-validation is spatially blocked: ~25 km squares are held out whole,
  so neighbouring events never sit on both sides of a split.
- The old index is scored on exactly the same held-out points. The model is
  used for routing only if it beats the index there ("use_model" in the meta);
  otherwise build_road_risk.py keeps the index and says why.

Usage (after build_ffpi.py and build_graph.py, before build_road_risk.py):
    python pipeline/train_landslide_model.py
"""

import datetime
import json
import os
import time

import geopandas as gpd
import joblib
import numpy as np
from osgeo import gdal
from scipy import ndimage
from scipy.spatial import cKDTree
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.model_selection import GroupKFold

from build_ffpi import CACHE as FFPI_CACHE, OUT, RAW, RES, create_like, log, row_blocks

CACHE = os.path.join(os.path.dirname(FFPI_CACHE), "landslide")
LANDSLIDE_ZIP = os.path.join(RAW, "landslides", "global_landslide_catalog_nasa.zip")
MODEL_PATH = os.path.join(OUT, "landslide_model.joblib")
META_PATH = os.path.join(OUT, "landslide_model_meta.json")
INDEX_PATH = os.path.join(os.path.dirname(FFPI_CACHE), "road_risk", "landslide_index.tif")

ACCURATE = {"exact": 1000, "1km": 1000, "5km": 5000, "10km": 10000}  # stated accuracy -> snap radius, m
WINDOW_PX = 33                 # ~1 km square for relief and mean slope
BACKGROUND_PER_EVENT = 10
BACKGROUND_MIN_M = 5000
BLOCK_M = 25_000
FOLDS = 5
SEED = 42

# (name, raster path, is categorical). Neighbour DEM cells give aspect and curvature.
RASTERS = [
    ("elevation_m", os.path.join(FFPI_CACHE, "dem.tif"), False),
    ("slope_deg", os.path.join(FFPI_CACHE, "slope.tif"), False),
    ("relief_1km_m", os.path.join(CACHE, "relief_1km.tif"), False),
    ("slope_1km_deg", os.path.join(CACHE, "slope_1km.tif"), False),
    ("forest_pct", os.path.join(FFPI_CACHE, "forest_pct.tif"), False),
    ("ffpi", os.path.join(OUT, "ffpi.tif"), False),
    ("landcover", os.path.join(FFPI_CACHE, "landcover.tif"), True),
    ("soil_hsg", os.path.join(FFPI_CACHE, "soil_hsg.tif"), True),
]
FEATURES = [name for name, _, _ in RASTERS] + ["aspect_sin", "aspect_cos", "curvature"]
CATEGORICAL = [cat for _, _, cat in RASTERS] + [False, False, False]

gdal.UseExceptions()


def build_context(path, kind):
    """1 km relief (max - min elevation) or mean slope, block by block."""
    if os.path.exists(path):
        return
    t0 = time.time()
    src = gdal.Open(os.path.join(FFPI_CACHE, "dem.tif" if kind == "relief" else "slope.tif"))
    band = src.GetRasterBand(1)
    nodata = band.GetNoDataValue()
    w, h = src.RasterXSize, src.RasterYSize
    halo = WINDOW_PX // 2
    tmp = path + ".tmp.tif"
    dst = create_like(tmp, src, gdal.GDT_Float32, -9999)
    out = dst.GetRasterBand(1)
    for r0, n in row_blocks(h):
        a0, a1 = max(0, r0 - halo), min(h, r0 + n + halo)
        grid = band.ReadAsArray(0, a0, w, a1 - a0).astype(np.float32)
        valid = grid != nodata
        if kind == "relief":
            hi = ndimage.maximum_filter(np.where(valid, grid, -np.inf), WINDOW_PX)
            lo = ndimage.minimum_filter(np.where(valid, grid, np.inf), WINDOW_PX)
            value = hi - lo
        else:  # mean of the valid cells in the window
            total = ndimage.uniform_filter(np.where(valid, grid, 0), WINDOW_PX)
            count = ndimage.uniform_filter(valid.astype(np.float32), WINDOW_PX)
            value = np.where(count > 0, total / np.maximum(count, 1e-6), np.nan)
        value = value[r0 - a0:r0 - a0 + n]
        out.WriteArray(np.where(valid[r0 - a0:r0 - a0 + n] & np.isfinite(value), value, -9999)
                       .astype(np.float32), 0, r0)
    dst = None
    os.replace(tmp, path)
    log(f"{os.path.basename(path)}: built", t0)


def sample(path, row, col):
    """Raster values at (row, col), reading one block of rows at a time; NaN for no data."""
    ds = gdal.Open(path)
    band = ds.GetRasterBand(1)
    nodata = band.GetNoDataValue()
    h, w = ds.RasterYSize, ds.RasterXSize
    out = np.full(len(row), np.nan, dtype=np.float32)
    inside = (row >= 0) & (row < h) & (col >= 0) & (col < w)
    order = np.argsort(np.where(inside, row, -1), kind="stable")
    sorted_rows = np.where(inside, row, -1)[order]
    for r0, n in row_blocks(h):
        lo, hi = np.searchsorted(sorted_rows, [r0, r0 + n])
        if lo == hi:
            continue
        idx = order[lo:hi]
        block = band.ReadAsArray(0, r0, w, n)
        vals = block[row[idx] - r0, col[idx]].astype(np.float32)
        if nodata is not None:
            vals[vals == nodata] = np.nan
        out[idx] = vals
    return out


def features(row, col):
    """Feature matrix (len(row) x len(FEATURES)) for cells on the 30 m grid."""
    cols = [sample(path, row, col) for _, path, _ in RASTERS]
    dem = os.path.join(FFPI_CACHE, "dem.tif")
    north, south = sample(dem, row - 1, col), sample(dem, row + 1, col)
    west, east = sample(dem, row, col - 1), sample(dem, row, col + 1)
    centre = cols[0]
    dzdx, dzdy = (east - west) / (2 * RES), (north - south) / (2 * RES)
    aspect = np.arctan2(dzdx, dzdy)                       # 0 = facing north
    curvature = (north + south + east + west - 4 * centre) / RES ** 2
    return np.column_stack(cols + [np.sin(aspect), np.cos(aspect), curvature])


def model():
    return HistGradientBoostingClassifier(
        max_depth=3, learning_rate=0.05, max_iter=200, l2_regularization=1.0,
        min_samples_leaf=20, categorical_features=CATEGORICAL, random_state=SEED)


def auc(pos, neg):
    from build_road_risk import auc as rank_auc
    return round(rank_auc(np.asarray(pos, float), np.asarray(neg, float)), 3)


def training_points(gt, shape):
    """(row, col, label, x, y, accuracy) for events and road background points."""
    from build_road_risk import GRAPH, sample_spans, to_cells
    from pyproj import Transformer

    glc = gpd.read_file(f"zip://{LANDSLIDE_ZIP}!global_landslide_catalog_NASA.shp")
    state = gpd.read_file(os.path.join(OUT, "state_boundary.geojson")).to_crs(glc.crs)
    ev = glc[glc.within(state.geometry.iloc[0]) & glc["location_a"].isin(list(ACCURATE))]
    er, ec, eok = to_cells(ev.geometry.x.values, ev.geometry.y.values, gt, shape)
    ev, er, ec = ev[eok], er[eok], ec[eok]

    off = np.load(os.path.join(GRAPH, "edge_geom_offsets.npy"))
    ll = np.load(os.path.join(GRAPH, "edge_geom_lonlat.npy"))
    _, lon, lat = sample_spans(off, ll)
    rr, rc, rok = to_cells(lon, lat, gt, shape)
    mask = sample(os.path.join(FFPI_CACHE, "state_mask.tif"), rr, rc)
    in_state = np.flatnonzero(rok & (mask == 1))

    to_utm = Transformer.from_crs("EPSG:4326", "EPSG:32644", always_xy=True)
    ex, ey = to_utm.transform(ev.geometry.x.values, ev.geometry.y.values)
    # Both classes on roads: each event moves to the nearest road point within
    # its stated accuracy, so the model cannot learn "on a road or not".
    sx, sy = to_utm.transform(lon[in_state], lat[in_state])
    gap, near = cKDTree(np.column_stack([sx, sy])).query(np.column_stack([ex, ey]))
    hit = gap <= ev["location_a"].map(ACCURATE).values
    ev, er, ec = ev[hit], rr[in_state][near[hit]], rc[in_state][near[hit]]
    ex, ey = sx[near[hit]], sy[near[hit]]

    rng = np.random.default_rng(SEED)
    picks = rng.choice(in_state, min(len(in_state), 40 * BACKGROUND_PER_EVENT * len(ev)), replace=False)
    bx, by = to_utm.transform(lon[picks], lat[picks])
    gap, _ = cKDTree(np.column_stack([ex, ey])).query(np.column_stack([bx, by]))
    far = picks[gap >= BACKGROUND_MIN_M][:BACKGROUND_PER_EVENT * len(ev)]
    fx, fy = to_utm.transform(lon[far], lat[far])

    row = np.concatenate([er, rr[far]])
    col = np.concatenate([ec, rc[far]])
    label = np.concatenate([np.ones(len(er), int), np.zeros(len(far), int)])
    x, y = np.concatenate([ex, fx]), np.concatenate([ey, fy])
    accuracy = ev["location_a"].value_counts().to_dict()
    return row, col, label, x, y, {k: int(v) for k, v in accuracy.items()}


def main():
    t0 = time.time()
    os.makedirs(CACHE, exist_ok=True)
    build_context(os.path.join(CACHE, "relief_1km.tif"), "relief")
    build_context(os.path.join(CACHE, "slope_1km.tif"), "slope")
    from build_road_risk import build_landslide_index
    os.makedirs(os.path.dirname(INDEX_PATH), exist_ok=True)
    build_landslide_index(INDEX_PATH)  # the hand-weighted index, scored alongside

    ref = gdal.Open(os.path.join(FFPI_CACHE, "state_mask.tif"))
    gt, shape = ref.GetGeoTransform(), (ref.RasterYSize, ref.RasterXSize)
    row, col, y, px, py, accuracy = training_points(gt, shape)
    X = features(row, col)
    index = sample(INDEX_PATH, row, col)
    keep = np.isfinite(X[:, :6]).all(axis=1) & np.isfinite(index) & (index != 255)
    X, y, index, px, py = X[keep], y[keep], index[keep], px[keep], py[keep]
    log(f"training set: {y.sum()} landslides placed within 10 km, {(y == 0).sum()} road "
        f"points at least {BACKGROUND_MIN_M // 1000} km from any", t0)

    groups = (np.floor(px / BLOCK_M).astype(np.int64) * 100_000 + np.floor(py / BLOCK_M).astype(np.int64))
    oof = np.full(len(y), np.nan)
    folds = []
    for train, test in GroupKFold(n_splits=FOLDS).split(X, y, groups):
        fitted = model().fit(X[train], y[train])
        oof[test] = fitted.predict_proba(X[test])[:, 1]
        if 0 < y[test].sum() < len(test):
            folds.append({"landslides": int(y[test].sum()),
                          "model_auc": auc(oof[test][y[test] == 1], oof[test][y[test] == 0]),
                          "index_auc": auc(index[test][y[test] == 1], index[test][y[test] == 0])})
    model_auc = auc(oof[y == 1], oof[y == 0])
    index_auc = auc(index[y == 1], index[y == 0])
    use_model = model_auc > index_auc
    log(f"spatially blocked {FOLDS}-fold CV, held-out points: model AUC {model_auc}, "
        f"hand-weighted index AUC {index_auc} -> {'use the model' if use_model else 'keep the index'}", t0)

    joblib.dump(model().fit(X, y), MODEL_PATH)

    meta = {
        "built": datetime.date.today().isoformat(),
        "target": "P(a catalogued rainfall-season landslide here) vs other roads",
        "labels": "NASA Global Landslide Catalog events in Uttarakhand placed within 10 km, "
                  "moved to the nearest road point within their stated accuracy",
        "label_accuracy_counts": accuracy,
        "background": f"random road points >= {BACKGROUND_MIN_M} m from any event, "
                      f"{BACKGROUND_PER_EVENT} per event",
        "n_landslides": int(y.sum()),
        "n_background": int((y == 0).sum()),
        "features": FEATURES,
        "model": "sklearn HistGradientBoostingClassifier, depth 3, 200 iterations, lr 0.05",
        "validation": {
            "scheme": f"spatially blocked {FOLDS}-fold CV, {BLOCK_M // 1000} km blocks held out whole",
            "model_auc": model_auc,
            "index_auc_same_points": index_auc,
            "folds": folds,
        },
        "use_model": bool(use_model),
        "prone_rule": "set in build_road_risk.py: the top 10% of state road length by worst-point probability",
        "note": "Skill is measured on held-out 25 km blocks. The catalogue is news-sourced "
                "and many events are placed only to 5-10 km, so treat the flag as a hint.",
    }
    with open(META_PATH, "w", encoding="utf-8") as fh:
        json.dump(meta, fh, indent=2)
    log(f"saved {os.path.relpath(MODEL_PATH)} and {os.path.relpath(META_PATH)}", t0)


def check():
    """Fails loudly if the point sampler or the feature maths break."""
    import tempfile
    path = os.path.join(tempfile.mkdtemp(), "t.tif")
    ds = gdal.GetDriverByName("GTiff").Create(path, 3, 1200, 1, gdal.GDT_Float32)
    grid = np.arange(3600, dtype=np.float32).reshape(1200, 3)
    ds.GetRasterBand(1).WriteArray(grid)
    ds.GetRasterBand(1).SetNoDataValue(5)
    ds = None
    vals = sample(path, np.array([1100, 0, 1, -1, 600]), np.array([2, 1, 2, 0, 0]))
    assert vals[0] == grid[1100, 2] and np.isnan(vals[2]) and np.isnan(vals[3])  # 5 = no data, -1 = off grid
    assert vals[1] == 1 and vals[4] == grid[600, 0]                              # across row blocks


if __name__ == "__main__":
    check()
    main()
