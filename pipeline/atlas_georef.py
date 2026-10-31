"""Georeference the atlas flash-flood hazard map from its printed graticule.

Input   pipeline/out/atlas/page75.png  (from atlas_render.py)
        pipeline/atlas_gcps.csv        ground control points: page pixel -> lon/lat
Output  pipeline/out/atlas/atlas_georef.tif  (RGBA, EPSG:4326)

The GCPs are the 12 crossings of the printed 78-81 E and 29-31 N lines,
measured on the 300 dpi render and confirmed by eye. "visible" says whether
the crossing itself shows; the rest lie under the opaque state fill and are
crossings of the same lines measured where they show. The map was embedded at
about 100 dpi, so one source pixel (3 px here) is about 400 m on the ground.

Same as gdal_translate -gcp ... then gdalwarp -tps -t_srs EPSG:4326, through
the GDAL Python bindings. A thin-plate spline passes exactly through every
GCP, so its own residuals are zero; the residuals printed are against a
first-order (affine) fit, which shows how consistent the points are.

Usage:
    python pipeline/atlas_georef.py
"""

import csv
import math
import os
import time

from osgeo import gdal

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ATLAS = os.path.join(REPO, "pipeline", "out", "atlas")
PAGE_PNG = os.path.join(ATLAS, "page75.png")
GCPS = os.path.join(REPO, "pipeline", "atlas_gcps.csv")
OUT = os.path.join(ATLAS, "atlas_georef.tif")

# Inside of the map frame on the 300 dpi page (x0, y0, width, height), so the
# page margins, title and frame line are not warped.
FRAME = (672, 703, 3607, 2383)

gdal.UseExceptions()


def main():
    t0 = time.time()
    with open(GCPS, newline="") as f:
        rows = list(csv.DictReader(f))
    x0, y0 = FRAME[0], FRAME[1]
    gcps = [gdal.GCP(float(r["lon"]), float(r["lat"]), 0,
                     float(r["pixel_x"]) - x0, float(r["pixel_y"]) - y0, r["id"])
            for r in rows]

    framed = gdal.Translate("/vsimem/atlas_gcps.tif", PAGE_PNG, srcWin=FRAME,
                            GCPs=gcps, outputSRS="EPSG:4326")
    tmp = OUT + ".tmp.tif"
    gdal.Warp(tmp, framed, dstSRS="EPSG:4326", tps=True, resampleAlg="near",
              dstAlpha=True, creationOptions=["TILED=YES", "COMPRESS=DEFLATE"])
    framed = None
    gdal.Unlink("/vsimem/atlas_gcps.tif")
    os.replace(tmp, OUT)

    # Consistency check: residual of each GCP against an affine fit.
    gt = gdal.GCPsToGeoTransform(gcps)
    inv = gdal.InvGeoTransform(gt)
    print(f"{'id':>3} {'lon':>4} {'lat':>4} {'visible':>8}  residual px")
    worst = 0.0
    for g, r in zip(gcps, rows):
        px, py = gdal.ApplyGeoTransform(inv, g.GCPX, g.GCPY)
        res = math.hypot(px - g.GCPPixel, py - g.GCPLine)
        worst = max(worst, res)
        print(f"{r['id']:>3} {r['lon']:>4} {r['lat']:>4} {r['visible']:>8}  {res:6.2f}")
    deg_per_px = math.hypot(gt[1], gt[2])
    print(f"affine fit: {1 / deg_per_px:.0f} px per degree, worst residual {worst:.2f} px "
          f"(~{worst * deg_per_px * 111:.2f} km)")

    ds = gdal.Open(OUT)
    g = ds.GetGeoTransform()
    w, s = g[0] + ds.RasterXSize * g[1], g[3] + ds.RasterYSize * g[5]
    print(f"{OUT}: {ds.RasterXSize} x {ds.RasterYSize} px, "
          f"lon {g[0]:.3f}..{w:.3f}, lat {s:.3f}..{g[3]:.3f}  [{time.time() - t0:.1f}s]")


if __name__ == "__main__":
    main()
