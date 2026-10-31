"""Render the flash-flood hazard map page of the state hazard atlas to PNG.

Input   pipeline/raw/atlas.pdf  (page 75: section 3.4.3 "Flash flood hazard
        zones", Figure 3.72, Map Ref 050000000_H_FLFLZ0100Y)
Output  pipeline/out/atlas/page75.png  (300 dpi)

The PDF has no text layer, so everything downstream is read from this image.

Usage:
    python pipeline/atlas_render.py
"""

import os
import time

import pymupdf

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PDF = os.path.join(REPO, "pipeline", "raw", "atlas.pdf")
OUT = os.path.join(REPO, "pipeline", "out", "atlas")
PAGE = 75  # 1-based
DPI = 300


def main():
    t0 = time.time()
    os.makedirs(OUT, exist_ok=True)
    doc = pymupdf.open(PDF)
    pix = doc[PAGE - 1].get_pixmap(dpi=DPI)
    path = os.path.join(OUT, f"page{PAGE}.png")
    pix.save(path)
    print(f"page {PAGE} of {doc.page_count} -> {path} ({pix.width} x {pix.height} px)  "
          f"[{time.time() - t0:.1f}s]")


if __name__ == "__main__":
    main()
