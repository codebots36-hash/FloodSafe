"""Download historical hourly rainfall for calibrating the flood thresholds.

The WATCH/CRITICAL thresholds that turn live rainfall into a zone status have
to be calibrated against the rain that actually fell before real floods, and
on the monsoon days that passed without one. This fetches that record.

  Points   one per Uttarakhand district (13), taken from the OpenStreetMap
           district boundaries in pipeline/raw/osm/uk.osm.pbf and snapped to
           the nearest ERA5 grid centre (0.25 degrees).
  Period   27 May to 30 September, 2023 back to 2000. June starts with its
           antecedent rainfall already in hand.
  Source   Open-Meteo archive API, ERA5 model, hourly precipitation, in IST
           so days line up with the flood inventory's dates.
           https://archive-api.open-meteo.com/v1/archive

That is 13 x 24 = 312 requests against a free limit of 10,000 a day, so a
single run finishes comfortably. The run is still resumable: every
point-year is cached as its own file and skipped next time. Requests are
paced under the per-minute limit and the run stops cleanly if the daily one
is reached. It never works around the limits, and never fills a gap with a
made-up value.

Usage:
    python pipeline/fetch_era5.py            # fetch whatever is missing
    python pipeline/fetch_era5.py --points   # only rebuild the points CSV
    python pipeline/fetch_era5.py --status   # how much is cached
"""

import argparse
import csv
import json
import os
import sys
import time

import requests
from pyrosm import OSM
from shapely.ops import unary_union

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PBF = os.path.join(REPO, "pipeline", "raw", "osm", "uk.osm.pbf")
POINTS_CSV = os.path.join(REPO, "pipeline", "out", "era5_points.csv")
CACHE_DIR = os.path.join(REPO, "pipeline", "cache", "era5")

API = "https://archive-api.open-meteo.com/v1/archive"
YEARS = list(range(2023, 1999, -1))  # newest first: a partial run keeps the recent years
SEASON_START = "05-27"  # a few days before June, so June has antecedent rain
SEASON_END = "09-30"

EXPECTED_DISTRICTS = 13
GRID = 0.25  # ERA5 resolution, degrees

REQUEST_GAP = 1.2  # seconds between requests, under Open-Meteo's per-minute limit
MAX_RETRIES = 4


def log(msg):
    print(msg, flush=True)


# ---------------------------------------------------------------
# District points
# ---------------------------------------------------------------
def snap_to_grid(value):
    """Nearest ERA5 grid centre."""
    return round(value / GRID) * GRID


def build_points():
    """One ERA5 grid point per Uttarakhand district, from OSM boundaries.

    admin_level 4 is the state, admin_level 5 is a district. The .osm.pbf is
    a bounding-box extract, so it also carries districts of neighbouring
    states; those are dropped by keeping only districts whose own
    representative point falls inside the Uttarakhand polygon.
    """
    if not os.path.exists(PBF):
        sys.exit(f"missing {PBF} -- run the osmium extract in S6 first")

    log("reading district boundaries from uk.osm.pbf ...")
    boundaries = OSM(PBF).get_boundaries()
    boundaries = boundaries[boundaries["name"].notna()]

    state = boundaries[
        (boundaries["admin_level"] == "4") & (boundaries["name"] == "Uttarakhand")
    ]
    if state.empty:
        sys.exit("no admin_level=4 boundary named Uttarakhand found")
    state_geom = unary_union(state.geometry.values)

    districts = boundaries[boundaries["admin_level"] == "5"]
    rows = []
    for name, geom in zip(districts["name"], districts.geometry):
        if geom is None or geom.is_empty:
            continue
        point = geom.representative_point()
        if not state_geom.contains(point):
            continue  # a neighbouring state's district caught by the bbox
        clean = name.replace(" district", "").replace(" District", "").strip()
        rows.append(
            {
                "district": clean,
                "lat": round(snap_to_grid(point.y), 2),
                "lon": round(snap_to_grid(point.x), 2),
                "centroid_lat": round(point.y, 5),
                "centroid_lon": round(point.x, 5),
            }
        )

    # One district can appear as several relations; keep the first of each name.
    seen, unique = set(), []
    for row in rows:
        if row["district"] not in seen:
            seen.add(row["district"])
            unique.append(row)
    unique.sort(key=lambda r: r["district"])

    if len(unique) != EXPECTED_DISTRICTS:
        log(f"\nExpected {EXPECTED_DISTRICTS} districts, found {len(unique)}:")
        for row in unique:
            log(f"  {row['district']}  ({row['lat']}, {row['lon']})")
        sys.exit("\nStopping. Check the boundaries before spending API calls.")

    os.makedirs(os.path.dirname(POINTS_CSV), exist_ok=True)
    with open(POINTS_CSV, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(unique[0].keys()))
        writer.writeheader()
        writer.writerows(unique)

    log(f"{len(unique)} districts -> {POINTS_CSV}")
    for row in unique:
        log(f"  {row['district']:<20} {row['lat']:6.2f} N  {row['lon']:6.2f} E")
    return unique


def load_points():
    if not os.path.exists(POINTS_CSV):
        return build_points()
    with open(POINTS_CSV, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


# ---------------------------------------------------------------
# Rainfall
# ---------------------------------------------------------------
def cache_path(district, year):
    safe = "".join(c if c.isalnum() else "_" for c in district)
    return os.path.join(CACHE_DIR, f"{safe}_{year}.json")


class DailyLimitReached(Exception):
    """Open-Meteo's free daily quota is used up; stop cleanly and resume tomorrow."""


def fetch_point_year(lat, lon, year):
    """One point, one monsoon. Returns the API's hourly block."""
    params = {
        "latitude": lat,
        "longitude": lon,
        "start_date": f"{year}-{SEASON_START}",
        "end_date": f"{year}-{SEASON_END}",
        "hourly": "precipitation",
        "timezone": "Asia/Kolkata",
        "models": "era5",
    }

    for attempt in range(MAX_RETRIES):
        response = requests.get(API, params=params, timeout=60)

        if response.status_code == 200:
            payload = response.json()
            hourly = payload.get("hourly") or {}
            if not hourly.get("time"):
                raise RuntimeError(f"no hourly data returned for {year}")
            return payload

        reason = ""
        try:
            reason = response.json().get("reason", "")
        except ValueError:
            reason = response.text[:200]

        if "daily" in reason.lower():
            raise DailyLimitReached(reason)

        if response.status_code in (429, 500, 502, 503, 504):
            wait = REQUEST_GAP * (4 ** attempt)
            log(f"    {response.status_code} ({reason[:60]}), waiting {wait:.0f}s")
            time.sleep(wait)
            continue

        raise RuntimeError(f"HTTP {response.status_code}: {reason}")

    raise RuntimeError("gave up after repeated rate limiting")


def fetch_all(points):
    os.makedirs(CACHE_DIR, exist_ok=True)
    todo = [
        (p, y) for y in YEARS for p in points if not os.path.exists(cache_path(p["district"], y))
    ]
    total = len(points) * len(YEARS)

    if not todo:
        log(f"all {total} point-years already cached, nothing to do")
        return

    log(f"{total - len(todo)}/{total} cached, fetching {len(todo)} more")
    done = 0
    for point, year in todo:
        label = f"{point['district']} {year}"
        try:
            payload = fetch_point_year(float(point["lat"]), float(point["lon"]), year)
        except DailyLimitReached as exc:
            log(f"\nOpen-Meteo daily limit reached ({exc}).")
            log(f"{len(todo) - done} point-years left. Run this again tomorrow.")
            return
        except RuntimeError as exc:
            log(f"  FAILED {label}: {exc}")
            continue

        with open(cache_path(point["district"], year), "w", encoding="utf-8") as fh:
            json.dump(payload, fh)

        done += 1
        if done % 10 == 0 or done == len(todo):
            log(f"  {done}/{len(todo)}  (latest: {label})")
        time.sleep(REQUEST_GAP)

    log(f"\ndone: {done} point-years fetched into {CACHE_DIR}")


def show_status(points):
    log(f"{len(points)} districts x {len(YEARS)} years = {len(points) * len(YEARS)} point-years\n")
    grand = 0
    for year in YEARS:
        have = sum(1 for p in points if os.path.exists(cache_path(p["district"], year)))
        grand += have
        mark = "complete" if have == len(points) else ""
        log(f"  {year}  {have:>2}/{len(points)}  {mark}")
    log(f"\ntotal cached: {grand}/{len(points) * len(YEARS)}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--points", action="store_true", help="only rebuild the points CSV")
    parser.add_argument("--status", action="store_true", help="show how much is cached")
    args = parser.parse_args()

    if args.points:
        build_points()
        return

    points = load_points()
    if args.status:
        show_status(points)
    else:
        fetch_all(points)


if __name__ == "__main__":
    main()
