"""Copy the built road graph and map layers into api/data/, which is what a
deployed API reads.

Input   pipeline/out/graph/    road graph with risk (build_graph.py, build_road_risk.py)
        pipeline/out/layers/   map layers (build_road_risk.py)
Output  api/data/graph/        the same files, committed so a fresh clone can run
        api/data/layers/

pipeline/out/ is not committed, and the graph is 54 MB, so without this copy
the API cannot start anywhere but the machine that built it. Run it after
build_road_risk.py and commit the result. api/tests/test_packaged_data.py
fails if the copy has fallen behind the build.

Usage:
    python pipeline/package_api_data.py
"""

import os
import shutil
import sys

import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(REPO, "pipeline", "out")
DEST = os.path.join(REPO, "api", "data")
FOLDERS = ("graph", "layers")
GRAPH_FILES = [
    "node_lon.npy", "node_lat.npy", "edge_u.npy", "edge_v.npy", "edge_len_m.npy",
    "edge_class.npy", "edge_geom_id.npy", "edge_reversed.npy", "edge_geom_offsets.npy",
    "edge_geom_lonlat.npy", "edge_risk.npy", "edge_surveyed.npy", "edge_landslide.npy",
    "class_codes.json", "graph_meta.json",
]
LAYER_FILES = ["road_risk.geojson", "atlas.geojson"]


def check(graph):
    """Refuse to package a graph whose risk files do not belong to it."""
    missing = [f for f in GRAPH_FILES if not os.path.exists(os.path.join(graph, f))]
    if missing:
        sys.exit(f"missing from {graph}: {missing}\nRun build_graph.py and build_road_risk.py first.")
    edges = len(np.load(os.path.join(graph, "edge_u.npy"), mmap_mode="r"))
    for name in ("edge_v", "edge_len_m", "edge_class", "edge_geom_id", "edge_reversed",
                 "edge_risk", "edge_surveyed", "edge_landslide"):
        n = len(np.load(os.path.join(graph, f"{name}.npy"), mmap_mode="r"))
        if n != edges:
            sys.exit(f"{name}.npy has {n:,} entries but the graph has {edges:,} edges. "
                     "Re-run build_road_risk.py.")
    return edges


def main():
    graph, layers = (os.path.join(OUT, f) for f in FOLDERS)
    edges = check(graph)
    missing = [f for f in LAYER_FILES if not os.path.exists(os.path.join(layers, f))]
    if missing:
        sys.exit(f"missing from {layers}: {missing}\nRun build_road_risk.py first.")

    total = 0
    for folder, names in (("graph", GRAPH_FILES), ("layers", LAYER_FILES)):
        os.makedirs(os.path.join(DEST, folder), exist_ok=True)
        for name in names:
            src, dst = os.path.join(OUT, folder, name), os.path.join(DEST, folder, name)
            shutil.copy2(src, dst)
            total += os.path.getsize(dst)
    print(f"copied {len(GRAPH_FILES) + len(LAYER_FILES)} files ({total / 1e6:.1f} MB), "
          f"{edges:,} directed edges, into {os.path.relpath(DEST, REPO)}")


if __name__ == "__main__":
    main()
