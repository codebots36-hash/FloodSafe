"""Places from OpenStreetMap: shelters, hospitals, localities and flood zones.

Inputs
    pipeline/raw/osm/uk.osm.pbf               OpenStreetMap (Geofabrik, ODbL)
    pipeline/out/state_boundary.geojson       (build_ffpi.py)
    pipeline/out/ffpi.tif, ffpi_class.tif     modelled flood index and class (build_ffpi.py)
    pipeline/out/atlas_hazard.geojson         surveyed flood zones (atlas_classify.py)
    pipeline/cache/ffpi/soil_hsg.tif          hydrologic soil group 1-4 = A-D (build_ffpi.py)
    pipeline/raw/basins/hybas_as_lev08_v1c.shp   HydroBASINS level 8, Asia
Outputs
    pipeline/out/districts.geojson
    api/data/shelters.json, hospitals.json, localities.json   (as in contracts/)
    api/data/zones.json   the static part of each zone in contracts/zones.json;
                          /zones adds status, rain and thresholds at request time

A zone is a locality within 1 km of a MODERATE-or-worse cell, taking the atlas
class where the atlas covers the cell ("surveyed") and the FFPI class
elsewhere ("modelled"). Its hazard_class is the worst class within that 1 km,
surveyed on a tie, the same rule build_road_risk.py uses for roads.

Usage:
    python pipeline/build_places.py
"""

import datetime
import json
import os
import re
import time

import geopandas as gpd
import numpy as np
from osgeo import gdal
from pyrosm import OSM
from scipy.spatial import cKDTree

from build_ffpi import CACHE as FFPI_CACHE, CLASS_NAMES, CRS, OUT, RAW, REPO, RES, log
from build_road_risk import atlas_grid, read_grid, to_cells, write_json

gdal.UseExceptions()

API_DATA = os.path.join(REPO, "api", "data")
PBF = os.path.join(RAW, "osm", "uk.osm.pbf")
BASINS = os.path.join(RAW, "basins", "hybas_as_lev08_v1c.shp")

SHELTER_AMENITIES = ["shelter", "community_centre", "social_facility"]
PLACE_TYPES = ["city", "town", "village", "hamlet", "suburb", "neighbourhood"]
TOWNS = ["city", "town"]
PARENT_KM = 15
NEAR_LOCALITY_KM = 5
DUPLICATE_M = 2000  # same name and district this close = one place mapped twice
ZONE_RADIUS_M = 1000
MAX_ZONES = 200
MIN_PER_DISTRICT = 8  # every district gets at least this many, if it has them
HSG = {1: "A", 2: "B", 3: "C", 4: "D"}
EXPECTED_DISTRICTS = 13


def name_of(tags):
    """English name if tagged, else the main name, else None."""
    tags = json.loads(tags) if isinstance(tags, str) else tags
    if not isinstance(tags, dict):
        return None
    return tags.get("name:en") or tags.get("name")


def slug(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def points(gdf):
    """Point per feature (centroids of polygons), in UTM and lon/lat."""
    utm = gdf.to_crs(CRS)
    utm = utm.set_geometry(utm.geometry.centroid)
    ll = utm.to_crs("EPSG:4326")
    return utm, ll.geometry.x.to_numpy(), ll.geometry.y.to_numpy()


def nearest(tree_xy, query_xy, max_m):
    """Index of the nearest tree point within max_m, or -1."""
    dist, idx = cKDTree(tree_xy).query(query_xy, distance_upper_bound=max_m)
    return np.where(np.isfinite(dist), idx, -1)


def xy(utm):
    return np.column_stack([utm.geometry.x, utm.geometry.y])


def in_state(gdf, state):
    return gdf[gdf.geometry.within(state)]


def build_districts(osm, state):
    t0 = time.time()
    b = osm.get_boundaries(boundary_type="administrative")
    b = b[(b["admin_level"] == "5") & b["name"].notna()]
    # admin_level 5 relations that cross the border (Bijnor, UP) are dropped
    b = b[b.geometry.representative_point().within(state)]
    b = b.assign(name=b["name"].str.replace(r"\s+district$", "", regex=True, case=False))
    if len(b) != EXPECTED_DISTRICTS:
        raise SystemExit(f"expected {EXPECTED_DISTRICTS} districts, found {sorted(b['name'])}")
    b = b[["id", "name", "geometry"]]
    path = os.path.join(OUT, "districts.geojson")
    b.to_file(path + ".tmp", driver="GeoJSON")
    os.replace(path + ".tmp", path)
    log(f"districts: {len(b)}", t0)
    return b.to_crs(CRS)


def district_of(utm, districts):
    """District name per point; points in a boundary gap take the nearest."""
    joined = gpd.sjoin_nearest(utm[["geometry"]], districts[["name", "geometry"]], how="left")
    return joined[~joined.index.duplicated()]["name"].to_numpy()


def build_localities(osm, state, districts):
    t0 = time.time()
    p = osm.get_data_by_custom_criteria(
        custom_filter={"place": PLACE_TYPES}, filter_type="keep",
        keep_nodes=True, keep_ways=True, keep_relations=False)
    p = p.assign(name=p["tags"].map(name_of))
    p = in_state(p[p["name"].notna()], state)
    # towns first, so on a duplicate name the town keeps the plain id
    p = p.assign(rank=p["place"].map({t: i for i, t in enumerate(PLACE_TYPES)}))
    p = p.sort_values(["rank", "id"]).reset_index(drop=True)
    utm, lon, lat = points(p)
    district = district_of(utm, districts)

    # One place mapped twice (a node and an area) -> keep the first
    keep = np.ones(len(p), dtype=bool)
    pts = xy(utm)
    for idx in p.groupby([p["name"], district]).indices.values():
        for k, j in enumerate(idx[1:], 1):
            prev = idx[:k][keep[idx[:k]]]
            if (np.hypot(*(pts[prev] - pts[j]).T) < DUPLICATE_M).any():
                keep[j] = False
    p, utm = p[keep].reset_index(drop=True), utm[keep].reset_index(drop=True)
    lon, lat, district = lon[keep], lat[keep], district[keep]

    town = p["place"].isin(TOWNS).to_numpy()
    town_idx = np.flatnonzero(town)
    near = nearest(xy(utm)[town_idx], xy(utm), PARENT_KM * 1000)

    items, seen = [], set()
    for i, row in p.iterrows():
        name = row["name"]
        if town[i]:
            parent = None
        elif near[i] >= 0:
            parent = p.at[town_idx[near[i]], "name"]
        else:
            parent = f"{district[i]} district"
        # A name with no Latin letters slugs to "", which would clash with "none chosen".
        base = slug(name) or f"{row['osm_type']}-{row['id']}"
        lid = next(c for c in (base, f"{base}-{slug(district[i])}",
                               f"{base}-{row['osm_type']}-{row['id']}") if c not in seen)
        seen.add(lid)
        items.append({"id": lid, "name": name, "parent": parent, "district": district[i],
                      "lat": round(float(lat[i]), 5), "lon": round(float(lon[i]), 5)})
    counts = p["place"].value_counts().to_dict()
    log(f"localities: {len(items):,} {counts}", t0)
    return items, utm, p["place"].to_numpy()


def build_amenities(osm, state, loc_utm, localities):
    t0 = time.time()
    a = osm.get_pois(custom_filter={"amenity": SHELTER_AMENITIES + ["hospital"]})
    a = in_state(a[a["name"].notna()], state).reset_index(drop=True)
    utm, lon, lat = points(a)
    near = nearest(xy(loc_utm), xy(utm), NEAR_LOCALITY_KM * 1000)
    out = {"shelters": [], "hospitals": []}
    for i, row in a.iterrows():
        kind = row["amenity"]
        out["hospitals" if kind == "hospital" else "shelters"].append({
            "id": f"osm:{row['osm_type']}/{row['id']}",
            "name": row["name"],
            "kind": kind,
            "lat": round(float(lat[i]), 5),
            "lon": round(float(lon[i]), 5),
            "near_locality": localities[near[i]]["name"] if near[i] >= 0 else None,
            "source": "OpenStreetMap",
        })
    log(f"shelters: {len(out['shelters'])}, hospitals: {len(out['hospitals'])}", t0)
    return out


def build_zones(localities, loc_utm, place):
    t0 = time.time()
    ffpi_class, gt = read_grid(os.path.join(OUT, "ffpi_class.tif"))
    soil, _ = read_grid(os.path.join(FFPI_CACHE, "soil_hsg.tif"))
    atlas = atlas_grid(ffpi_class.shape, gt)
    ffpi_ds = gdal.Open(os.path.join(OUT, "ffpi.tif"))  # keep: the band dies with it
    ffpi_band = ffpi_ds.GetRasterBand(1)

    r = int(ZONE_RADIUS_M // RES)
    yy, xx = np.mgrid[-r:r + 1, -r:r + 1]
    disk = yy ** 2 + xx ** 2 <= (ZONE_RADIUS_M / RES) ** 2
    lon = np.array([l["lon"] for l in localities])
    lat = np.array([l["lat"] for l in localities])
    rows, cols, ok = to_cells(lon, lat, gt, ffpi_class.shape)

    basins = gpd.read_file(BASINS, bbox=tuple(gpd.GeoSeries(loc_utm.geometry, crs=CRS)
                                              .to_crs("EPSG:4326").total_bounds))
    up_area = gpd.sjoin(loc_utm[["geometry"]].to_crs(basins.crs), basins[["UP_AREA", "geometry"]],
                        how="left", predicate="within")
    up_area = up_area[~up_area.index.duplicated()]["UP_AREA"].to_numpy()

    candidates = []
    for i in np.flatnonzero(ok):
        if not re.search(r"[A-Za-z]", localities[i]["name"]):  # OSM has it only in Devanagari
            continue
        r0, c0 = rows[i] - r, cols[i] - r
        if r0 < 0 or c0 < 0 or r0 + 2 * r + 1 > ffpi_class.shape[0] or c0 + 2 * r + 1 > ffpi_class.shape[1]:
            continue
        win = np.s_[r0:r0 + 2 * r + 1, c0:c0 + 2 * r + 1]
        a, f = atlas[win][disk], ffpi_class[win][disk]
        # 2 * class + surveyed, so max() is the worst class, surveyed on a tie
        score = np.where(a > 0, 2 * (a.astype(np.int16) - 1) + 1,
                         np.where(f < 4, 2 * f.astype(np.int16), -1))
        worst = int(score.max())
        if worst < 2:  # nothing MODERATE or worse within 1 km
            continue
        s = int(soil[rows[i], cols[i]])
        if s not in HSG:  # built-up or bare at the point: the commonest group around it
            near = np.bincount(soil[win][disk], minlength=5)[1:]
            s = int(near.argmax()) + 1 if near.any() else 0
        ffpi = float(ffpi_band.ReadAsArray(int(cols[i]), int(rows[i]), 1, 1)[0, 0])
        candidates.append((i, worst, s, ffpi))

    def keep_first(c):
        i, worst, _, ffpi = c
        return (not (place[i] in TOWNS or worst % 2 == 1), -ffpi)

    candidates.sort(key=keep_first)
    # Each district's first MIN_PER_DISTRICT go ahead of everything else, so
    # districts with few towns are not crowded out by the busy ones.
    taken, first, rest = {}, [], []
    for c in candidates:
        d = localities[c[0]]["district"]
        taken[d] = taken.get(d, 0) + 1
        (first if taken[d] <= MIN_PER_DISTRICT else rest).append(c)
    candidates = first + rest
    kept, dropped = candidates[:MAX_ZONES], candidates[MAX_ZONES:]
    if dropped:
        types = {}
        for i, *_ in dropped:
            types[place[i]] = types.get(place[i], 0) + 1
        log(f"zones: {len(candidates):,} localities qualify; kept {len(kept)} ({MIN_PER_DISTRICT} "
            f"per district first, then towns and surveyed, then highest FFPI), "
            f"dropped {len(dropped):,} {types}")

    zones = []
    for i, worst, s, _ in kept:
        loc = localities[i]
        area = up_area[i]
        zones.append({**{k: loc[k] for k in ("id", "name", "parent", "district", "lat", "lon")},
                      "hazard_class": CLASS_NAMES[worst // 2],
                      "risk_source": "surveyed" if worst % 2 else "modelled",
                      "soil_group": HSG.get(s),
                      "catchment_km2": round(float(area), 1) if area == area else None})
    by_class = {c: sum(z["hazard_class"] == c for z in zones) for c in CLASS_NAMES}
    surveyed = sum(z["risk_source"] == "surveyed" for z in zones)
    log(f"zones: {len(zones)} {by_class}, {surveyed} surveyed", t0)
    return zones


def main():
    t0 = time.time()
    os.makedirs(API_DATA, exist_ok=True)
    state = gpd.read_file(os.path.join(OUT, "state_boundary.geojson")).geometry.iloc[0]
    osm = OSM(PBF)

    districts = build_districts(osm, state)
    localities, loc_utm, place = build_localities(osm, state, districts)
    amenities = build_amenities(osm, state, loc_utm, localities)
    zones = build_zones(localities, loc_utm, place)

    for name, items in (("shelters", amenities["shelters"]), ("hospitals", amenities["hospitals"]),
                        ("localities", localities)):
        mb = write_json(os.path.join(API_DATA, f"{name}.json"), {"items": items})
        log(f"wrote api/data/{name}.json ({len(items):,} items, {mb:.2f} MB)")
    mb = write_json(os.path.join(API_DATA, "zones.json"), {
        "built": datetime.date.today().isoformat(),
        "sources": "OpenStreetMap (ODbL); FFPI and atlas classes; SoilGrids; HydroBASINS lev08",
        "zones": zones})
    log(f"wrote api/data/zones.json ({len(zones)} zones, {mb:.2f} MB)", t0)


if __name__ == "__main__":
    main()
