"""Statewide Flash Flood Potential Index (FFPI) for Uttarakhand.

FFPI after Smith (2003), as used by US NWS forecast offices: each terrain
layer is reindexed to 1-10 and averaged with slope counted twice,

    FFPI = (2 * slope + soil + landcover + forest) / 5

Inputs
    pipeline/raw/dem/*.tif         Copernicus DEM GLO-30
    pipeline/raw/soil/*.tif        SoilGrids v2.0 sand and clay, 0-5 and 5-15 cm,
                                   already cut to the state (g/kg)
    pipeline/raw/landcover/*.tif   ESA WorldCover 10 m 2021 v200
    pipeline/raw/osm/uk.osm.pbf    OpenStreetMap, for the state boundary
Outputs
    pipeline/out/state_boundary.geojson
    pipeline/out/ffpi.tif          float32, -9999 outside the state / no data
    pipeline/out/ffpi_class.tif    uint8 0-3 = LOW..EXTREME, 255 = no data
    pipeline/out/ffpi_meta.json    class cut-offs, inputs, date
    pipeline/out/ffpi_preview.png

The state at 30 m is about 59M cells, so nothing is held whole: GDAL warps
on disk with a memory cap, and every numpy step runs over row blocks.
Intermediates go to pipeline/cache/ffpi/ and a re-run skips any that exist.
Layers are warped over the state's bounding box and the state mask is applied
only in the final combine, so slope and forest density at the border still
see real neighbours.

Usage:
    python pipeline/build_ffpi.py
"""

import datetime
import glob
import json
import os
import time

import numpy as np
from osgeo import gdal
from PIL import Image
from scipy.ndimage import uniform_filter

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(REPO, "pipeline", "raw")
OUT = os.path.join(REPO, "pipeline", "out")
CACHE = os.path.join(REPO, "pipeline", "cache", "ffpi")

CRS = "EPSG:32644"
RES = 30
BLOCK_ROWS = 512
WARP_MEM_MB = 512
GTIFF = ["TILED=YES", "COMPRESS=LZW", "BIGTIFF=IF_SAFER"]

# ---------------------------------------------------------------------------
# Reindex tables. They follow the FFPI method (each factor scored 1-10, higher
# = more flash-flood prone) but the break points are a MODELLING CHOICE for
# Himalayan terrain, not values published by Smith (2003) or any agency.
# ---------------------------------------------------------------------------

# Slope in degrees: below 2 -> 1, 2-5 -> 2, ... 35 and over -> 10.
SLOPE_EDGES_DEG = [2, 5, 8, 12, 16, 20, 25, 30, 35]

# USDA texture class -> hydrologic soil group, after SCS TR-55 (1986),
# Appendix A. Texture only: depth to bedrock or water table is not known.
TEXTURE_HSG = {
    "sand": "A", "loamy sand": "A", "sandy loam": "A",
    "loam": "B", "silt loam": "B", "silt": "B",
    "sandy clay loam": "C",
    "clay loam": "D", "silty clay loam": "D", "sandy clay": "D",
    "silty clay": "D", "clay": "D",
}
HSG_CODE = {"A": 1, "B": 2, "C": 3, "D": 4}
SOIL_SCORE = {1: 1, 2: 4, 3: 7, 4: 10}  # A..D
# SoilGrids has no value over glaciers, rock, water and most towns (about 8%
# of the state). Where WorldCover agrees there is no soil mantle there, score
# it as group D (NRCS treats impervious surfaces and rock as D). This is a
# modelling choice; elsewhere a missing soil value stays no data.
NO_SOIL_LANDCOVER = [50, 60, 70, 80, 100]  # built-up, bare, snow/ice, water, moss/lichen

# ESA WorldCover class -> score.
LANDCOVER_SCORE = {
    10: 1,    # tree cover
    20: 4,    # shrubland
    30: 5,    # grassland
    40: 6,    # cropland
    50: 10,   # built-up
    60: 9,    # bare / sparse vegetation
    70: 8,    # snow and ice
    80: 10,   # permanent water: sheds all rain, and it is where floods run
    90: 7,    # herbaceous wetland
    95: 2,    # mangroves
    100: 7,   # moss and lichen
}

# Forest density = % of WorldCover tree cover within a ~1 km window.
# Under 10% -> 10, 10-20% -> 9, ... 90% and over -> 1.
FOREST_WINDOW_PX = 33  # 33 x 30 m = 990 m
FOREST_EDGES_PCT = [10, 20, 30, 40, 50, 60, 70, 80, 90]

CLASS_NAMES = ["LOW", "MODERATE", "SIGNIFICANT", "EXTREME"]
CLASS_PERCENTILES = [50, 75, 90]
CLASS_COLOURS = [(76, 175, 80), (255, 193, 7), (255, 112, 67), (183, 28, 28)]

gdal.UseExceptions()


def log(msg, t0=None):
    stamp = f"  [{time.time() - t0:5.1f}s]" if t0 else ""
    print(f"{msg}{stamp}", flush=True)


def cache(name):
    return os.path.join(CACHE, name)


def step(name, path):
    """True when `path` still has to be built; logs a skip otherwise."""
    if os.path.exists(path):
        log(f"{name}: cached, skipping")
        return False
    log(f"{name}: building")
    return True


def finish(tmp, path):
    os.replace(tmp, path)


def lookup(table, size):
    lut = np.zeros(size, dtype=np.uint8)
    for k, v in table.items():
        lut[k] = v
    return lut


def classify_texture(sand, clay):
    """USDA texture triangle (NRCS rules). Percent inputs -> index into
    TEXTURE_HSG (rules are in its key order), -1 where undefined."""
    silt = 100 - sand - clay
    rules = [
        silt + 1.5 * clay < 15,                                               # sand
        (silt + 1.5 * clay >= 15) & (silt + 2 * clay < 30),                   # loamy sand
        ((clay >= 7) & (clay < 20) & (sand > 52) & (silt + 2 * clay >= 30))
        | ((clay < 7) & (silt < 50) & (silt + 2 * clay >= 30)),               # sandy loam
        (clay >= 7) & (clay < 27) & (silt >= 28) & (silt < 50) & (sand <= 52),  # loam
        ((silt >= 50) & (clay >= 12) & (clay < 27))
        | ((silt >= 50) & (silt < 80) & (clay < 12)),                         # silt loam
        (silt >= 80) & (clay < 12),                                           # silt
        (clay >= 20) & (clay < 35) & (silt < 28) & (sand > 45),               # sandy clay loam
        (clay >= 27) & (clay < 40) & (sand > 20) & (sand <= 45),              # clay loam
        (clay >= 27) & (clay < 40) & (sand <= 20),                            # silty clay loam
        (clay >= 35) & (sand > 45),                                           # sandy clay
        (clay >= 40) & (silt >= 40),                                          # silty clay
        (clay >= 40) & (sand <= 45) & (silt < 40),                            # clay
    ]
    return np.select(rules, range(len(rules)), default=-1)


def texture_to_hsg(sand, clay):
    """Percent sand/clay -> HSG code 1-4 (A-D), 0 where undefined."""
    lut = np.array([HSG_CODE[TEXTURE_HSG[n]] for n in TEXTURE_HSG] + [0], dtype=np.uint8)
    return lut[classify_texture(sand, clay)]  # -1 indexes the trailing 0


def check():
    """Fails loudly if the texture or percentile logic breaks."""
    t = lambda s, c: list(TEXTURE_HSG)[int(classify_texture(np.array(s), np.array(c)))]
    assert t(92, 3) == "sand"
    assert t(65, 10) == "sandy loam"
    assert t(40, 20) == "loam"
    assert t(20, 15) == "silt loam"
    assert t(60, 25) == "sandy clay loam"
    assert t(30, 33) == "clay loam"
    assert t(20, 50) == "clay"
    assert list(texture_to_hsg(np.array([92, 40, 60, 20]), np.array([3, 20, 25, 50]))) == [1, 2, 3, 4]
    hist = np.zeros(51, dtype=np.int64)
    hist[[10, 20, 30, 40]] = 25
    assert cutoffs_from_hist(hist, [50, 75, 90]) == [20, 30, 40]


def cutoffs_from_hist(hist, percentiles):
    """Inverted-CDF percentiles of an integer-valued histogram."""
    cum = np.cumsum(hist)
    return [int(np.searchsorted(cum, p / 100 * cum[-1])) for p in percentiles]


def row_blocks(height):
    for r0 in range(0, height, BLOCK_ROWS):
        yield r0, min(BLOCK_ROWS, height - r0)


def create_like(path, ref, dtype, nodata):
    drv = gdal.GetDriverByName("GTiff")
    ds = drv.Create(path, ref.RasterXSize, ref.RasterYSize, 1, dtype, GTIFF)
    ds.SetGeoTransform(ref.GetGeoTransform())
    ds.SetProjection(ref.GetProjection())
    ds.GetRasterBand(1).SetNoDataValue(nodata)
    return ds


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------

def build_boundary(path):
    from pyrosm import OSM

    t0 = time.time()
    if not step("boundary", path):
        return
    b = OSM(os.path.join(RAW, "osm", "uk.osm.pbf")).get_boundaries(boundary_type="administrative")
    b = b[(b["admin_level"] == "4") & b["name"].str.contains("Uttarakhand", na=False)]
    if len(b) != 1:
        raise SystemExit(f"expected one Uttarakhand admin_level=4 boundary, found {len(b)}")
    b = b[["id", "name", "geometry"]]
    tmp = path + ".tmp"
    b.to_file(tmp, driver="GeoJSON")
    finish(tmp, path)
    area = b.to_crs(CRS).area.iloc[0] / 1e6
    log(f"boundary: {b['name'].iloc[0]} (OSM relation {b['id'].iloc[0]}), {area:,.0f} km2", t0)


def boundary_utm(boundary, path):
    """The boundary in UTM 44N (gdal.Rasterize does not reproject) and its
    bounding box snapped outward to the 30 m grid."""
    if not os.path.exists(path):
        gdal.VectorTranslate(path + ".tmp.gpkg", boundary, dstSRS=CRS, format="GPKG")
        finish(path + ".tmp.gpkg", path)
    ds = gdal.OpenEx(path)
    xmin, xmax, ymin, ymax = ds.GetLayer(0).GetExtent()
    snap = lambda v, f: float(f(v / RES) * RES)
    return snap(xmin, np.floor), snap(ymin, np.floor), snap(xmax, np.ceil), snap(ymax, np.ceil)


def warp(src, path, bounds, resample, dtype, nodata, src_nodata=None, options=GTIFF):
    tmp = path + ".tmp.tif"
    gdal.Warp(tmp, src, dstSRS=CRS, outputBounds=bounds, xRes=RES, yRes=RES,
              resampleAlg=resample, outputType=dtype, srcNodata=src_nodata,
              dstNodata=nodata, creationOptions=options, warpMemoryLimit=WARP_MEM_MB,
              overviewLevel="NONE",  # the inputs are COGs: read full resolution, not overviews
              multithread=True, warpOptions=["NUM_THREADS=ALL_CPUS"])
    finish(tmp, path)


def build_mask(boundary, path, bounds):
    t0 = time.time()
    if not step("state mask", path):
        return
    tmp = path + ".tmp.tif"
    gdal.Rasterize(tmp, boundary, outputSRS=CRS, outputBounds=bounds, xRes=RES, yRes=RES,
                   outputType=gdal.GDT_Byte, burnValues=[1], initValues=[0],
                   creationOptions=GTIFF)
    finish(tmp, path)
    ds = gdal.Open(path)
    log(f"state mask: {ds.RasterXSize} x {ds.RasterYSize} cells", t0)


def build_dem(path, bounds):
    t0 = time.time()
    if not step("dem", path):
        return
    tiles = sorted(glob.glob(os.path.join(RAW, "dem", "*.tif")))
    vrt = gdal.BuildVRT("/vsimem/dem.vrt", tiles)
    # Striped, not tiled: the safest GeoTIFF layout for WhiteboxTools to read.
    warp(vrt, path, bounds, "bilinear", gdal.GDT_Float32, -9999, options=["COMPRESS=LZW"])
    vrt = None
    log(f"dem: {len(tiles)} tiles mosaicked and warped to {CRS} at {RES} m", t0)


def build_slope(dem, path):
    import whitebox

    t0 = time.time()
    if not step("slope", path):
        return
    wbt = whitebox.WhiteboxTools()
    wbt.set_verbose_mode(False)
    tmp = path + ".tmp.tif"
    if wbt.slope(dem, tmp, units="degrees") != 0 or not os.path.exists(tmp):
        raise SystemExit("WhiteboxTools slope failed")
    finish(tmp, path)
    log("slope: degrees via WhiteboxTools", t0)


def build_soil(path, bounds):
    t0 = time.time()
    if not step("soil", path):
        return
    soil = os.path.join(RAW, "soil")
    read = lambda n: gdal.Open(os.path.join(soil, n)).ReadAsArray().astype(np.float32)
    layers = {n: read(f"{n}.tif") for n in
              ("sand_0-5cm", "sand_5-15cm", "clay_0-5cm", "clay_5-15cm")}
    valid = np.all([a != -32768 for a in layers.values()], axis=0)
    sand = (layers["sand_0-5cm"] + layers["sand_5-15cm"]) / 20  # g/kg -> %
    clay = (layers["clay_0-5cm"] + layers["clay_5-15cm"]) / 20
    hsg = np.where(valid, texture_to_hsg(sand, clay), 0).astype(np.uint8)

    ref = gdal.Open(os.path.join(soil, "sand_0-5cm.tif"))
    small = cache("soil_hsg_4326.tif")
    ds = create_like(small, ref, gdal.GDT_Byte, 0)
    ds.GetRasterBand(1).WriteArray(hsg)
    ds = None
    warp(small, path, bounds, "near", gdal.GDT_Byte, 0)
    counts = {g: int((hsg == c).sum()) for g, c in HSG_CODE.items()}
    log(f"soil: hydrologic groups on the 0.01 deg grid {counts}, no data {int((hsg == 0).sum())}", t0)


def build_landcover(path, bounds):
    t0 = time.time()
    if not step("landcover", path):
        return
    tiles = sorted(glob.glob(os.path.join(RAW, "landcover", "*.tif")))
    vrt = gdal.BuildVRT("/vsimem/lc.vrt", tiles)
    warp(vrt, path, bounds, "mode", gdal.GDT_Byte, 0, src_nodata=0)
    vrt = None
    log(f"landcover: {len(tiles)} WorldCover tiles, majority to {RES} m", t0)


def build_forest(landcover, path):
    """% tree cover in a FOREST_WINDOW_PX square, counting only cells with data."""
    t0 = time.time()
    if not step("forest density", path):
        return
    src = gdal.Open(landcover)
    band = src.GetRasterBand(1)
    w, h = src.RasterXSize, src.RasterYSize
    halo = FOREST_WINDOW_PX // 2
    tmp = path + ".tmp.tif"
    dst = create_like(tmp, src, gdal.GDT_Byte, 255)
    out = dst.GetRasterBand(1)
    for r0, n in row_blocks(h):
        a0, a1 = max(0, r0 - halo), min(h, r0 + n + halo)
        lc = band.ReadAsArray(0, a0, w, a1 - a0)
        trees = uniform_filter((lc == 10).astype(np.float32), FOREST_WINDOW_PX, mode="constant")
        valid = uniform_filter((lc != 0).astype(np.float32), FOREST_WINDOW_PX, mode="constant")
        with np.errstate(invalid="ignore", divide="ignore"):
            pct = np.where(valid > 0, np.round(100 * trees / valid), 255).astype(np.uint8)
        out.WriteArray(pct[r0 - a0:r0 - a0 + n], 0, r0)
    dst = None
    finish(tmp, path)
    log("forest density: tree share within ~1 km", t0)


def build_ffpi(paths, ffpi_path, class_path, meta_path):
    t0 = time.time()
    if os.path.exists(class_path) and os.path.exists(meta_path):
        log("ffpi: cached, skipping")
        return
    log("ffpi: combining")
    ds = {k: gdal.Open(p) for k, p in paths.items()}
    bands = {k: d.GetRasterBand(1) for k, d in ds.items()}
    ref = ds["mask"]
    w, h = ref.RasterXSize, ref.RasterYSize
    slope_nd = bands["slope"].GetNoDataValue()
    soil_lut = lookup(SOIL_SCORE, 256)
    lc_lut = lookup(LANDCOVER_SCORE, 256)

    # Scores are integers, so 5 * FFPI is an integer 5..50: keep that (`total`)
    # for exact percentiles from a histogram instead of sorting 59M floats.
    tmp_total = cache("ffpi_total.tmp.tif")
    tot_ds = create_like(tmp_total, ref, gdal.GDT_Byte, 0)
    tot_band = tot_ds.GetRasterBand(1)
    tmp_ffpi = ffpi_path + ".tmp.tif"
    f_ds = create_like(tmp_ffpi, ref, gdal.GDT_Float32, -9999)
    f_band = f_ds.GetRasterBand(1)

    hist = np.zeros(51, dtype=np.int64)
    in_state = missing = soil_filled = 0
    for r0, n in row_blocks(h):
        rd = lambda k: bands[k].ReadAsArray(0, r0, w, n)
        mask, slope, soil, lc, forest = (rd(k) for k in ("mask", "slope", "soil", "landcover", "forest"))
        s_slope = (np.digitize(slope, SLOPE_EDGES_DEG) + 1).astype(np.uint8)
        s_soil = soil_lut[soil]
        no_soil = (soil == 0) & np.isin(lc, NO_SOIL_LANDCOVER)
        s_soil[no_soil] = SOIL_SCORE[HSG_CODE["D"]]
        s_lc = lc_lut[lc]
        s_forest = (10 - np.digitize(forest, FOREST_EDGES_PCT)).astype(np.uint8)
        state = mask == 1
        ok = state & (slope != slope_nd) & (s_soil > 0) & (s_lc > 0) & (forest != 255)
        total = np.where(ok, 2 * s_slope.astype(np.uint16) + s_soil + s_lc + s_forest, 0).astype(np.uint8)
        hist += np.bincount(total[ok], minlength=51)
        in_state += int(state.sum())
        missing += int((state & ~ok).sum())
        soil_filled += int((state & no_soil).sum())
        tot_band.WriteArray(total, 0, r0)
        f_band.WriteArray(np.where(ok, total / 5, -9999).astype(np.float32), 0, r0)
    f_ds = None
    finish(tmp_ffpi, ffpi_path)
    log(f"ffpi: {in_state:,} state cells; {soil_filled:,} ({100 * soil_filled / in_state:.2f}%) scored "
        f"soil D for no soil mantle; {missing:,} ({100 * missing / in_state:.2f}%) left as no data", t0)

    cuts = cutoffs_from_hist(hist, CLASS_PERCENTILES)
    tmp_class = class_path + ".tmp.tif"
    c_ds = create_like(tmp_class, ref, gdal.GDT_Byte, 255)
    c_band = c_ds.GetRasterBand(1)
    for r0, n in row_blocks(h):
        total = tot_band.ReadAsArray(0, r0, w, n)
        cls = np.where(total > 0, np.digitize(total, np.array(cuts) + 1), 255).astype(np.uint8)
        c_band.WriteArray(cls, 0, r0)
    c_ds = tot_ds = None
    os.remove(tmp_total)
    finish(tmp_class, class_path)

    valid = int(hist.sum())
    bins = np.digitize(np.arange(51), np.array(cuts) + 1)
    shares = {CLASS_NAMES[i]: round(100 * int(hist[bins == i].sum()) / valid, 2) for i in range(4)}
    meta = {
        "method": "Flash Flood Potential Index after Smith (2003): (2*slope + soil + landcover + forest) / 5",
        "built": datetime.date.today().isoformat(),
        "crs": CRS,
        "resolution_m": RES,
        "classes": CLASS_NAMES,
        "class_rule": "LOW <= p50 < MODERATE <= p75 < SIGNIFICANT <= p90 < EXTREME, percentiles of FFPI inside the state",
        "cutoffs": {f"p{p}": c / 5 for p, c in zip(CLASS_PERCENTILES, cuts)},
        "class_share_pct": shares,
        "state_cells": in_state,
        "no_data_cells": missing,
        "soil_scored_D_no_soil_mantle_cells": soil_filled,
        "tables": {
            "slope_edges_deg": SLOPE_EDGES_DEG,
            "texture_to_hsg": TEXTURE_HSG,
            "soil_score_by_hsg": {g: SOIL_SCORE[c] for g, c in HSG_CODE.items()},
            "no_soil_landcover_scored_D": NO_SOIL_LANDCOVER,
            "landcover_score": LANDCOVER_SCORE,
            "forest_window_m": FOREST_WINDOW_PX * RES,
            "forest_edges_pct": FOREST_EDGES_PCT,
            "note": "Reindex break points are a modelling choice following the FFPI method, not official values.",
        },
        "inputs": {
            "dem": "Copernicus DEM GLO-30 (" + ", ".join(sorted(os.listdir(os.path.join(RAW, "dem")))) + ")",
            "soil": "SoilGrids v2.0 (ISRIC) sand and clay, 0-5 and 5-15 cm, 0.01 deg",
            "landcover": "ESA WorldCover 10 m 2021 v200 (" + ", ".join(sorted(os.listdir(os.path.join(RAW, "landcover")))) + ")",
            "boundary": "OpenStreetMap, Uttarakhand boundary=administrative admin_level=4 (uk.osm.pbf)",
        },
    }
    tmp = meta_path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(meta, f, indent=2)
    finish(tmp, meta_path)
    log(f"ffpi: cut-offs {meta['cutoffs']}, class shares {shares}", t0)


def build_preview(class_path, path, width=900):
    t0 = time.time()
    ds = gdal.Open(class_path)
    h = round(ds.RasterYSize * width / ds.RasterXSize)
    cls = ds.GetRasterBand(1).ReadAsArray(buf_xsize=width, buf_ysize=h,
                                          resample_alg=gdal.GRIORA_Mode)
    rgba = np.zeros((h, width, 4), dtype=np.uint8)
    for i, c in enumerate(CLASS_COLOURS):
        rgba[cls == i] = (*c, 255)
    Image.fromarray(rgba).save(path)
    log(f"preview: {path}", t0)


def main():
    check()
    t0 = time.time()
    os.makedirs(CACHE, exist_ok=True)
    os.makedirs(OUT, exist_ok=True)

    boundary = os.path.join(OUT, "state_boundary.geojson")
    build_boundary(boundary)
    boundary = cache("state_boundary_utm.gpkg")
    bounds = boundary_utm(os.path.join(OUT, "state_boundary.geojson"), boundary)

    p = {
        "mask": cache("state_mask.tif"),
        "dem": cache("dem.tif"),
        "slope": cache("slope.tif"),
        "soil": cache("soil_hsg.tif"),
        "landcover": cache("landcover.tif"),
        "forest": cache("forest_pct.tif"),
    }
    build_mask(boundary, p["mask"], bounds)
    build_dem(p["dem"], bounds)
    build_slope(p["dem"], p["slope"])
    build_soil(p["soil"], bounds)
    build_landcover(p["landcover"], bounds)
    build_forest(p["landcover"], p["forest"])

    ffpi = os.path.join(OUT, "ffpi.tif")
    ffpi_class = os.path.join(OUT, "ffpi_class.tif")
    build_ffpi({k: p[k] for k in ("mask", "slope", "soil", "landcover", "forest")},
               ffpi, ffpi_class, os.path.join(OUT, "ffpi_meta.json"))
    build_preview(ffpi_class, os.path.join(OUT, "ffpi_preview.png"))
    log("done", t0)


if __name__ == "__main__":
    main()
