"""Turn the georeferenced atlas flash-flood map into hazard polygons.

Input   pipeline/out/atlas/atlas_georef.tif   (from atlas_georef.py)
        pipeline/out/atlas/page75.png         (legend swatches are read from here)
        pipeline/out/state_boundary.geojson   (from build_ffpi.py)
Output  pipeline/out/atlas_hazard.geojson     (one polygon per feature, hazard_class)
        pipeline/out/atlas/atlas_hazard_preview.png

Colour rule. The zones are printed semi-transparent over hillshade and the
map was JPEG-compressed, so on the map a "Low" pixel is a dull olive-green,
far in plain colour distance from the bright legend swatch. Its HUE survives
the blending, so each pixel goes to the legend swatch whose CIELAB hue angle
is nearest, provided it has some colour at all (chroma >= MIN_CHROMA) and is
within MAX_HUE_DIFF of that swatch. Grey hillshade, text, lines and lakes are
no data.

Map symbols share the hazard colours: the international border is orange and
town markers are red dots. Orange/red blobs smaller than SYMBOL_MAX_PX, and
any touching the band just inside the state edge, are removed with a small
halo before the speck sieve.

Usage:
    python pipeline/atlas_classify.py
"""

import json
import os
import time

import geopandas as gpd
import numpy as np
import rasterio
from PIL import Image, ImageDraw
from rasterio.features import rasterize, shapes, sieve
from scipy import ndimage
from shapely.geometry import shape

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(REPO, "pipeline", "out")
ATLAS = os.path.join(OUT, "atlas")
GEOREF = os.path.join(ATLAS, "atlas_georef.tif")
PAGE = os.path.join(ATLAS, "page75.png")
BOUNDARY = os.path.join(OUT, "state_boundary.geojson")
OUT_GEOJSON = os.path.join(OUT, "atlas_hazard.geojson")
OUT_PREVIEW = os.path.join(ATLAS, "atlas_hazard_preview.png")

CLASSES = ["LOW", "MODERATE", "SIGNIFICANT", "EXTREME"]  # raster codes 1..4
# Inside each swatch of the "Hazard Zones" legend, page pixels (x0, y0, x1, y1).
LEGEND_BOXES = {
    "LOW": (2650, 3032, 2705, 3055),
    "MODERATE": (2845, 3032, 2900, 3055),
    "SIGNIFICANT": (3155, 3032, 3210, 3055),
    "EXTREME": (3480, 3032, 3535, 3055),
}
MIN_CHROMA = 20         # the grey hillshade fill sits at about 8
MAX_HUE_DIFF = 25       # degrees from the nearest swatch hue
SYMBOL_MAX_PX = 1500    # a town marker with its halo is a few hundred px
SYMBOL_HALO_PX = 10
BORDER_BAND_PX = 8      # about 1 km at this map's ~130 m pixels
# The PDF's map was a JPEG with colour stored per 16 x 16 source pixels, i.e.
# blocks of ~48 px (~2,300 px, ~6 km across) here. Colour patches much smaller
# than a block are compression noise, so specks under ~600 px (~10 km2) go.
SIEVE_PX = 600
UTM = "EPSG:32644"


def lab(r, g, b):
    """sRGB (0-255) -> CIELAB (D65). Written out channel by channel."""
    def lin(c):
        c = np.asarray(c, dtype=np.float64) / 255.0
        return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    r, g, b = lin(r), lin(g), lin(b)
    x = (0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047
    y = 0.2126 * r + 0.7152 * g + 0.0722 * b
    z = (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883
    f = lambda t: np.where(t > 216 / 24389, np.cbrt(t), (24389 / 27 * t + 16) / 116)
    fx, fy, fz = f(x), f(y), f(z)
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


def hue_chroma(r, g, b):
    _, a, bb = lab(r, g, b)
    return np.degrees(np.arctan2(bb, a)) % 360, np.hypot(a, bb)


def hue_diff(h, ref):
    d = np.abs(h - ref) % 360
    return np.minimum(d, 360 - d)


def classify(r, g, b, swatch_hues):
    """Pixel RGB -> class code 1..4, 0 = no data."""
    h, c = hue_chroma(r, g, b)
    diffs = np.stack([hue_diff(h, sh) for sh in swatch_hues])
    best = diffs.argmin(0)
    ok = (c >= MIN_CHROMA) & (diffs.min(0) <= MAX_HUE_DIFF)
    return np.where(ok, best + 1, 0).astype(np.uint8)


def check():
    """Fails loudly if the colour rule breaks."""
    hues = [133.0, 102.0, 64.0, 39.0]  # the legend's hues, as measured
    got = classify(np.array([110, 200, 230, 200, 130, 120]),
                   np.array([150, 195, 140, 60, 130, 150]),
                   np.array([90, 70, 60, 60, 120, 200]), hues)
    # dull green, dull yellow, orange, red, grey, blue
    assert got.tolist() == [1, 2, 3, 4, 0, 0], got


def read_swatches():
    page = np.asarray(Image.open(PAGE).convert("RGB")).astype(int)
    rgb = {}
    for name, (x0, y0, x1, y1) in LEGEND_BOXES.items():
        rgb[name] = [int(v) for v in np.median(page[y0:y1, x0:x1].reshape(-1, 3), axis=0)]
    return rgb


def main():
    check()
    t0 = time.time()
    swatches = read_swatches()
    hues = []
    for name in CLASSES:
        h, c = hue_chroma(*swatches[name])
        hues.append(float(h))
        print(f"legend {name:<11} RGB {swatches[name]}  hue {float(h):5.1f}  chroma {float(c):5.1f}")

    with rasterio.open(GEOREF) as src:
        r, g, b, alpha = src.read()
        transform, crs = src.transform, src.crs
    state = gpd.read_file(BOUNDARY).to_crs(crs)
    inside = rasterize(state.geometry, out_shape=alpha.shape, transform=transform) == 1
    inside &= alpha > 0

    cls = classify(r, g, b, hues)
    cls[~inside] = 0
    raw = {n: int((cls == i + 1).sum()) for i, n in enumerate(CLASSES)}
    print(f"classified pixels in the state: {raw}")

    # Map symbols in the orange/red classes: town dots and the border line.
    band = inside & ~ndimage.binary_erosion(inside, iterations=BORDER_BAND_PX)
    hot = (cls == 3) | (cls == 4)
    lab_ids, n = ndimage.label(hot, structure=np.ones((3, 3)))
    sizes = ndimage.sum(hot, lab_ids, range(1, n + 1))
    on_border = ndimage.maximum(band, lab_ids, range(1, n + 1))
    symbol_ids = np.flatnonzero((sizes < SYMBOL_MAX_PX) | on_border) + 1
    symbols = np.isin(lab_ids, symbol_ids)
    kept = n - len(symbol_ids)
    symbols = ndimage.binary_dilation(symbols, iterations=SYMBOL_HALO_PX)
    cls[symbols] = 0
    print(f"orange/red blobs: {n}, removed as symbols {len(symbol_ids)} "
          f"({int(on_border.sum())} on the border line), kept {kept}")

    cls = sieve(cls, SIEVE_PX, connectivity=8)
    cls[~inside] = 0

    polys = [(shape(geom), CLASSES[int(v) - 1])
             for geom, v in shapes(cls, mask=cls > 0, transform=transform, connectivity=8)]
    gdf = gpd.GeoDataFrame({"hazard_class": [c for _, c in polys]},
                           geometry=[p for p, _ in polys], crs=crs)
    gdf = gdf.dissolve("hazard_class").explode(index_parts=False).reset_index()
    gdf = gpd.clip(gdf, state)
    gdf = gdf[~gdf.is_empty & gdf.geom_type.isin(["Polygon", "MultiPolygon"])]
    gdf = gdf.explode(index_parts=False).reset_index(drop=True)
    gdf = gdf[["hazard_class", "geometry"]]
    tmp = OUT_GEOJSON + ".tmp"
    gdf.to_file(tmp, driver="GeoJSON")
    os.replace(tmp, OUT_GEOJSON)

    utm_area = gdf.to_crs(UTM).area / 1e6
    state_km2 = float(state.to_crs(UTM).area.sum() / 1e6)
    print(f"\n{'class':<12}{'polygons':>9}{'km2':>10}")
    for name in CLASSES:
        sel = gdf["hazard_class"] == name
        print(f"{name:<12}{int(sel.sum()):>9}{utm_area[sel].sum():>10,.0f}")
    total = float(utm_area.sum())
    print(f"{'total':<12}{len(gdf):>9}{total:>10,.0f}  = {100 * total / state_km2:.1f}% "
          f"of the state's {state_km2:,.0f} km2")
    print(f"{OUT_GEOJSON} ({os.path.getsize(OUT_GEOJSON) / 1e6:.1f} MB)")

    preview(gdf, state, swatches)
    print(f"{OUT_PREVIEW}  [{time.time() - t0:.1f}s]")


def preview(gdf, state, swatches, width=1400):
    x0, y0, x1, y1 = state.total_bounds
    k = np.cos(np.radians((y0 + y1) / 2))
    height = int(width * (y1 - y0) / ((x1 - x0) * k))
    px = lambda x, y: ((x - x0) / (x1 - x0) * (width - 20) + 10, (y1 - y) / (y1 - y0) * (height - 20) + 10)
    im = Image.new("RGB", (width, height), (245, 243, 238))
    d = ImageDraw.Draw(im)
    for cls_name, geom in zip(gdf["hazard_class"], gdf.geometry):
        d.polygon([px(x, y) for x, y in geom.exterior.coords], fill=tuple(swatches[cls_name]))
    for geom in state.geometry.explode(index_parts=False):
        d.line([px(x, y) for x, y in geom.exterior.coords], fill=(18, 35, 46), width=2)
    im.save(OUT_PREVIEW)


if __name__ == "__main__":
    main()
