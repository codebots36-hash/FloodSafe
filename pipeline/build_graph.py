"""Turn the OpenStreetMap road extract into compact arrays for routing.

Input   pipeline/raw/osm/uk_roads.osm.pbf  (OpenStreetMap, ODbL)
Output  pipeline/out/graph/*.npy + class_codes.json + graph_meta.json

pyrosm hands back one edge per pair of consecutive nodes, so the raw network
is about 3.1M nodes and 3.2M edges -- mostly shape points along a road
rather than real junctions. Two passes shrink that:

  1. Chains of degree-2 nodes are merged into a single edge that keeps the
     full polyline, so the graph holds junctions and the geometry is still
     there to draw.
  2. Only the largest connected component is kept, so routing can never be
     asked for a road island it cannot leave.

Everything is stored as plain .npy arrays that the API memory-maps at
startup, which keeps it inside a small free-tier instance.

Usage:
    python pipeline/build_graph.py
"""

import json
import os
import time

import numpy as np
import pandas as pd
from pyrosm import OSM
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PBF = os.path.join(REPO, "pipeline", "raw", "osm", "uk_roads.osm.pbf")
OUT = os.path.join(REPO, "pipeline", "out", "graph")

# Free-flow speeds in km/h. These are PLANNING ASSUMPTIONS for hill roads,
# not measured speeds: OSM maxspeed is missing on almost every road here.
# They set travel time, which is what "Fastest" minimises.
SPEED_KMH = {
    "motorway": 80,
    "motorway_link": 50,
    "trunk": 60,
    "trunk_link": 40,
    "primary": 50,
    "primary_link": 35,
    "secondary": 40,
    "secondary_link": 30,
    "tertiary": 30,
    "tertiary_link": 25,
    "unclassified": 25,
    "residential": 25,
    "living_street": 15,
    "service": 15,
    "track": 15,
    "road": 25,
}
DEFAULT_SPEED_KMH = 25

EARTH_R = 6371008.8


def log(msg, t0=None):
    stamp = f"  [{time.time() - t0:5.1f}s]" if t0 else ""
    print(f"{msg}{stamp}", flush=True)


def segment_lengths_m(lon, lat, u, v):
    """Great-circle length of each straight segment, in metres."""
    lon1, lat1 = np.radians(lon[u]), np.radians(lat[u])
    lon2, lat2 = np.radians(lon[v]), np.radians(lat[v])
    dlon, dlat = lon2 - lon1, lat2 - lat1
    a = np.sin(dlat / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2) ** 2
    return (2 * EARTH_R * np.arcsin(np.sqrt(np.clip(a, 0, 1)))).astype(np.float32)


def build_adjacency(u, v, n_nodes):
    """CSR-style adjacency over the undirected graph: for each node, the
    incident edge ids and the node at the other end."""
    ends = np.concatenate([u, v])
    others = np.concatenate([v, u])
    edge_ids = np.concatenate([np.arange(len(u)), np.arange(len(u))])

    order = np.argsort(ends, kind="stable")
    ends, others, edge_ids = ends[order], others[order], edge_ids[order]

    indptr = np.zeros(n_nodes + 1, dtype=np.int64)
    np.cumsum(np.bincount(ends, minlength=n_nodes), out=indptr[1:])
    return indptr, others, edge_ids


def main():
    t0 = time.time()
    os.makedirs(OUT, exist_ok=True)
    if not os.path.exists(PBF):
        raise SystemExit(f"missing {PBF} -- run the osmium commands in S6 first")

    # ---------------------------------------------------------------
    # Load
    # ---------------------------------------------------------------
    log("reading the road network with pyrosm ...")
    nodes, edges = OSM(PBF).get_network(network_type="driving", nodes=True)
    log(f"raw: {len(nodes):,} nodes, {len(edges):,} edges", t0)

    lon = nodes["lon"].to_numpy(dtype=np.float32)
    lat = nodes["lat"].to_numpy(dtype=np.float32)
    index_of = pd.Series(np.arange(len(nodes), dtype=np.int64), index=nodes["id"].to_numpy())

    u = index_of.reindex(edges["u"].to_numpy()).to_numpy()
    v = index_of.reindex(edges["v"].to_numpy()).to_numpy()
    keep = ~(np.isnan(u) | np.isnan(v))
    if not keep.all():
        log(f"dropping {(~keep).sum():,} edges whose endpoints are outside the extract")
    u, v = u[keep].astype(np.int64), v[keep].astype(np.int64)

    highway = edges["highway"].to_numpy()[keep]
    oneway_raw = edges["oneway"].to_numpy()[keep]
    seg_len = segment_lengths_m(lon, lat, u, v)

    # A handful of ways carry a route number in place of a class; treat them
    # as unclassified rather than inventing a speed for them.
    classes = sorted({h if h in SPEED_KMH else "unclassified" for h in pd.unique(highway)})
    class_code = {name: i for i, name in enumerate(classes)}
    seg_class = np.array(
        [class_code[h if h in SPEED_KMH else "unclassified"] for h in highway], dtype=np.uint8
    )

    forward_only = oneway_raw == "yes"
    backward_only = oneway_raw == "-1"
    two_way = ~(forward_only | backward_only)
    log(f"one-way segments: {(~two_way).sum():,}", t0)

    # ---------------------------------------------------------------
    # Merge chains of degree-2 nodes
    # ---------------------------------------------------------------
    log("merging chains of degree-2 nodes ...")
    n_nodes = len(nodes)
    degree = np.bincount(np.concatenate([u, v]), minlength=n_nodes)

    # A node can be swallowed into a chain only if it is a plain pass-through
    # point: exactly two neighbours, and both sides the same class and
    # direction. One-way segments are left unmerged -- they are 2% of the
    # network and chaining them needs direction bookkeeping that earns little.
    indptr, others, edge_ids = build_adjacency(u, v, n_nodes)
    mergeable = degree == 2
    for node in np.flatnonzero(mergeable):
        a, b = edge_ids[indptr[node] : indptr[node + 1]]
        if not (two_way[a] and two_way[b]) or seg_class[a] != seg_class[b]:
            mergeable[node] = False

    junction = ~mergeable
    chains = []  # (start_node, end_node, [segment ids in walk order])
    used = np.zeros(len(u), dtype=bool)

    for start in np.flatnonzero(junction & (degree > 0)):
        for slot in range(indptr[start], indptr[start + 1]):
            seg = edge_ids[slot]
            if used[seg]:
                continue
            used[seg] = True
            # Does the chain run with this segment's stored u->v direction or
            # against it? A one-way flag means nothing without that answer.
            first_forward = bool(u[seg] == start)
            walk, node, prev_seg = [seg], others[slot], seg
            while mergeable[node]:
                pair = edge_ids[indptr[node] : indptr[node + 1]]
                nxt = pair[0] if pair[1] == prev_seg else pair[1]
                if used[nxt]:
                    break
                used[nxt] = True
                walk.append(nxt)
                node = u[nxt] if v[nxt] == node else v[nxt]
                prev_seg = nxt
            chains.append((start, node, walk, first_forward))

    # Rings of degree-2 nodes touch no junction, so nothing above reached
    # them. They cannot be routed through, and are dropped with the
    # largest-component step below.
    log(f"{len(chains):,} merged edges from {len(u):,} segments", t0)

    # ---------------------------------------------------------------
    # Build merged edge arrays (one entry per undirected edge)
    # ---------------------------------------------------------------
    m_u = np.empty(len(chains), dtype=np.int64)
    m_v = np.empty(len(chains), dtype=np.int64)
    m_len = np.empty(len(chains), dtype=np.float32)
    m_class = np.empty(len(chains), dtype=np.uint8)
    m_fwd = np.ones(len(chains), dtype=bool)
    m_bwd = np.ones(len(chains), dtype=bool)
    geom_nodes, geom_offsets = [], np.zeros(len(chains) + 1, dtype=np.int64)

    for i, (start, end, walk, first_forward) in enumerate(chains):
        m_u[i], m_v[i] = start, end
        m_len[i] = seg_len[walk].sum()
        m_class[i] = seg_class[walk[0]]
        if len(walk) == 1:
            # Longer chains hold only two-way segments, so they need no
            # check. A single segment may be one-way, and the chain may run
            # against it, which swaps which direction is blocked.
            first = walk[0]
            if first_forward:
                m_fwd[i], m_bwd[i] = not backward_only[first], not forward_only[first]
            else:
                m_fwd[i], m_bwd[i] = not forward_only[first], not backward_only[first]

        path, node = [start], start
        for seg in walk:
            node = u[seg] if v[seg] == node else v[seg]
            path.append(node)
        geom_nodes.append(np.asarray(path, dtype=np.int64))
        geom_offsets[i + 1] = geom_offsets[i] + len(path)

    geom_nodes = np.concatenate(geom_nodes)

    # ---------------------------------------------------------------
    # Largest connected component
    # ---------------------------------------------------------------
    # The STRONGLY connected component, not just the connected one: with
    # one-way roads in the graph, "joined by some road" does not mean "can
    # actually be driven to". Keeping the strong component means any node in
    # the graph can reach any other, so a route request can never come back
    # empty because of a one-way dead end.
    log("finding the largest strongly connected component ...")
    fwd_i, bwd_i = np.flatnonzero(m_fwd), np.flatnonzero(m_bwd)
    src = np.concatenate([m_u[fwd_i], m_v[bwd_i]])
    dst = np.concatenate([m_v[fwd_i], m_u[bwd_i]])
    adj = coo_matrix(
        (np.ones(len(src), dtype=np.int8), (src, dst)), shape=(n_nodes, n_nodes)
    ).tocsr()
    n_comp, labels = connected_components(adj, directed=True, connection="strong")
    biggest = np.bincount(labels).argmax()
    node_keep = labels == biggest
    edge_keep = node_keep[m_u] & node_keep[m_v]
    log(f"keeping {node_keep.sum():,} nodes in the largest strong component", t0)

    # Renumber the surviving nodes so the arrays stay dense.
    new_id = np.full(n_nodes, -1, dtype=np.int64)
    new_id[node_keep] = np.arange(node_keep.sum())

    m_u, m_v = new_id[m_u[edge_keep]], new_id[m_v[edge_keep]]
    m_len, m_class = m_len[edge_keep], m_class[edge_keep]
    m_fwd, m_bwd = m_fwd[edge_keep], m_bwd[edge_keep]

    kept_spans = [
        geom_nodes[geom_offsets[i] : geom_offsets[i + 1]] for i in np.flatnonzero(edge_keep)
    ]
    new_offsets = np.zeros(len(kept_spans) + 1, dtype=np.int64)
    np.cumsum([len(s) for s in kept_spans], out=new_offsets[1:])
    flat = np.concatenate(kept_spans)
    geom_lonlat = np.empty((len(flat), 2), dtype=np.float32)
    geom_lonlat[:, 0], geom_lonlat[:, 1] = lon[flat], lat[flat]

    # ---------------------------------------------------------------
    # Directed edges
    # ---------------------------------------------------------------
    f, b = np.flatnonzero(m_fwd), np.flatnonzero(m_bwd)
    edge_u = np.concatenate([m_u[f], m_v[b]]).astype(np.int32)
    edge_v = np.concatenate([m_v[f], m_u[b]]).astype(np.int32)
    edge_len = np.concatenate([m_len[f], m_len[b]])
    edge_class = np.concatenate([m_class[f], m_class[b]])
    edge_geom_id = np.concatenate([f, b]).astype(np.int32)
    edge_reversed = np.concatenate([np.zeros(len(f), bool), np.ones(len(b), bool)])

    # ---------------------------------------------------------------
    # Save
    # ---------------------------------------------------------------
    arrays = {
        "node_lon": lon[node_keep],
        "node_lat": lat[node_keep],
        "edge_u": edge_u,
        "edge_v": edge_v,
        "edge_len_m": edge_len,
        "edge_class": edge_class,
        "edge_geom_id": edge_geom_id,
        "edge_reversed": edge_reversed,
        "edge_geom_offsets": new_offsets,
        "edge_geom_lonlat": geom_lonlat,
    }
    for name, arr in arrays.items():
        np.save(os.path.join(OUT, f"{name}.npy"), arr)

    with open(os.path.join(OUT, "class_codes.json"), "w", encoding="utf-8") as fh:
        json.dump(
            {
                "codes": {str(i): name for name, i in class_code.items()},
                "speed_kmh": SPEED_KMH,
                "default_speed_kmh": DEFAULT_SPEED_KMH,
                "note": "Speeds are planning assumptions for hill roads, not measured.",
            },
            fh,
            indent=2,
        )

    meta = {
        "source_file": os.path.basename(PBF),
        "source": "OpenStreetMap via Geofabrik India central zone (ODbL)",
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "build_seconds": round(time.time() - t0, 1),
        "raw_nodes": int(len(nodes)),
        "raw_edges": int(len(edges)),
        "nodes": int(node_keep.sum()),
        "undirected_edges": int(edge_keep.sum()),
        "directed_edges": int(len(edge_u)),
        "geometry_points": int(len(geom_lonlat)),
        # Counted over every raw node slot. Most raw nodes were shape points
        # swallowed into a merged chain, so they survive here as isolated
        # one-node components; that is why this number is close to raw_nodes.
        "components_including_isolated_nodes": int(n_comp),
        "geometry_layout": (
            "edge_geom_offsets/edge_geom_lonlat are indexed by UNDIRECTED edge. "
            "A directed edge points at its span via edge_geom_id, and "
            "edge_reversed says whether to read that span backwards."
        ),
    }
    with open(os.path.join(OUT, "graph_meta.json"), "w", encoding="utf-8") as fh:
        json.dump(meta, fh, indent=2)

    total_mb = sum(a.nbytes for a in arrays.values()) / 1e6
    log("")
    log(f"nodes            {meta['nodes']:>12,}  (from {meta['raw_nodes']:,})")
    log(f"undirected edges {meta['undirected_edges']:>12,}  (from {meta['raw_edges']:,})")
    log(f"directed edges   {meta['directed_edges']:>12,}")
    log(f"geometry points  {meta['geometry_points']:>12,}")
    log(f"arrays on disk   {total_mb:>12.1f} MB")
    log(f"written to {OUT}", t0)


if __name__ == "__main__":
    main()
