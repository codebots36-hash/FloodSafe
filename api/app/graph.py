"""Load the road graph built by pipeline/build_graph.py and keep it in memory.

Input   pipeline/out/graph/*.npy  (or api/data/graph on a deploy; set GRAPH_DIR)
        built from OpenStreetMap, Geofabrik India central zone (ODbL)
Output  one RoadGraph object the API reuses for every request

The arrays are opened memory-mapped, so pages are read from disk on demand
rather than all at once. What is built eagerly is small: a CSR matrix of
travel time for Dijkstra, and a KD-tree for snapping a lat/lon to the
nearest road node.
"""

import json
import os
import time

import numpy as np
from scipy.sparse import csr_matrix
from scipy.spatial import cKDTree

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DIRS = [
    os.path.join(HERE, "data", "graph"),  # what a deploy unpacks
    os.path.join(os.path.dirname(HERE), "pipeline", "out", "graph"),  # local build
]

# Uttarakhand sits near 30 N. Scaling degrees to metres about that latitude
# makes the KD-tree's plain euclidean distances accurate enough to snap a
# point to the nearest road; it is never used for route lengths.
REF_LAT = 30.0
M_PER_DEG_LAT = 110574.0
M_PER_DEG_LON = 111320.0 * np.cos(np.radians(REF_LAT))

# Dijkstra treats an explicit zero as "no edge", and a handful of OSM
# segments have coincident endpoints, so every cost gets a floor.
MIN_TRAVEL_S = 0.001


def graph_dir():
    chosen = os.environ.get("GRAPH_DIR")
    if chosen:
        return chosen
    for path in DEFAULT_DIRS:
        if os.path.exists(os.path.join(path, "edge_u.npy")):
            return path
    raise FileNotFoundError(
        "no road graph found. Run `python pipeline/build_graph.py`, or set GRAPH_DIR."
    )


class RoadGraph:
    def __init__(self, directory=None):
        t0 = time.time()
        self.dir = directory or graph_dir()

        def load(name):
            return np.load(os.path.join(self.dir, f"{name}.npy"), mmap_mode="r")

        self.node_lon = load("node_lon")
        self.node_lat = load("node_lat")
        self.edge_u = load("edge_u")
        self.edge_v = load("edge_v")
        self.edge_len_m = load("edge_len_m")
        self.edge_class = load("edge_class")
        self.edge_geom_id = load("edge_geom_id")
        self.edge_reversed = load("edge_reversed")
        self.geom_offsets = load("edge_geom_offsets")
        self.geom_lonlat = load("edge_geom_lonlat")

        # Risk per directed edge from pipeline/build_road_risk.py: 0-3 =
        # LOW..EXTREME, 255 = not scored (outside the state). Without those
        # files every edge reads as not scored and Safest routing is off.
        self.has_risk = os.path.exists(os.path.join(self.dir, "edge_risk.npy"))
        if self.has_risk:
            self.edge_risk = load("edge_risk")
            self.edge_surveyed = load("edge_surveyed")
            self.edge_landslide = load("edge_landslide")
        else:
            n = len(self.edge_u)
            self.edge_risk = np.full(n, 255, dtype=np.uint8)
            self.edge_surveyed = np.zeros(n, dtype=bool)
            self.edge_landslide = np.zeros(n, dtype=bool)

        with open(os.path.join(self.dir, "class_codes.json"), encoding="utf-8") as fh:
            codes = json.load(fh)
        with open(os.path.join(self.dir, "graph_meta.json"), encoding="utf-8") as fh:
            self.meta = json.load(fh)

        self.class_name = {int(k): v for k, v in codes["codes"].items()}
        speeds = codes["speed_kmh"]
        default = codes["default_speed_kmh"]
        mps = np.array(
            [speeds.get(self.class_name[i], default) / 3.6 for i in sorted(self.class_name)]
        )
        self.travel_s = np.maximum(
            np.asarray(self.edge_len_m, dtype=np.float64) / mps[self.edge_class], MIN_TRAVEL_S
        )

        self.n_nodes = len(self.node_lon)
        self._build_csr()

        xy = np.column_stack(
            [
                np.asarray(self.node_lon, dtype=np.float64) * M_PER_DEG_LON,
                np.asarray(self.node_lat, dtype=np.float64) * M_PER_DEG_LAT,
            ]
        )
        self.tree = cKDTree(xy)

        self.load_seconds = round(time.time() - t0, 2)
        print(
            f"road graph ready: {self.n_nodes:,} nodes, {len(self.edge_u):,} directed edges "
            f"in {self.load_seconds}s",
            flush=True,
        )

    def _build_csr(self):
        """CSR of travel time, plus the edge id behind every stored entry.

        Two junctions can be joined by more than one road. Only the quickest
        is kept: a slower parallel edge would never be chosen anyway, and
        duplicates would otherwise be summed into one nonsense cost.
        """
        u = np.asarray(self.edge_u, dtype=np.int64)
        v = np.asarray(self.edge_v, dtype=np.int64)

        order = np.lexsort((self.travel_s, v, u))
        su, sv = u[order], v[order]
        first = np.ones(len(order), dtype=bool)
        first[1:] = (su[1:] != su[:-1]) | (sv[1:] != sv[:-1])
        order = order[first]

        rows, cols = u[order], v[order]
        indptr = np.zeros(self.n_nodes + 1, dtype=np.int64)
        np.cumsum(np.bincount(rows, minlength=self.n_nodes), out=indptr[1:])

        self.csr = csr_matrix(
            (self.travel_s[order], cols, indptr), shape=(self.n_nodes, self.n_nodes)
        )
        self.csr_indptr = indptr
        self.csr_indices = cols
        self.csr_edge_id = order  # index back into the full directed edge arrays

    def edge_between(self, a, b):
        """The directed edge id used to get from node a to node b, or None."""
        lo, hi = self.csr_indptr[a], self.csr_indptr[a + 1]
        window = self.csr_indices[lo:hi]
        pos = np.searchsorted(window, b)
        if pos < len(window) and window[pos] == b:
            return int(self.csr_edge_id[lo + pos])
        return None

    def snap(self, lat, lon):
        """Nearest road node to a lat/lon, with the straight-line gap in metres."""
        metres, index = self.tree.query([lon * M_PER_DEG_LON, lat * M_PER_DEG_LAT])
        return int(index), float(metres)

    def edge_coords(self, edge_id):
        """The polyline for one directed edge, as [[lon, lat], ...]."""
        gid = int(self.edge_geom_id[edge_id])
        span = np.asarray(self.geom_lonlat[self.geom_offsets[gid] : self.geom_offsets[gid + 1]])
        return span[::-1] if self.edge_reversed[edge_id] else span


_graph = None


def get_graph():
    """The one shared RoadGraph, loaded on first use."""
    global _graph
    if _graph is None:
        _graph = RoadGraph()
    return _graph
