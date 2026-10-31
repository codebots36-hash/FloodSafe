"""Watched places and whether the rain on them has reached a warning level.

Inputs  api/data/zones.json       the 200 zones (pipeline/build_places.py)
        api/data/thresholds.json  calibrated rain depths
                                  (pipeline/calibrate_thresholds.py)
        live rainfall from app/weather.py
Output  the /zones response described in contracts/

A zone turns WATCH or CRITICAL when the rain on it reaches a depth that only
15% or 5% of quiet monsoon days reached over 24 monsoons, shifted by what the
zone's own hazard class and soil group say about how little rain it takes.

The thresholds catch about a third of the floods the inventory records at
WATCH; rainfall alone cannot do much better, because a district rain series
cannot see a cloudburst over one valley. The measured skill is in
api/data/thresholds.json and is never hidden behind the status.
"""

import json
import os

from app.weather import WeatherUnavailable, history, index_now, key, payloads, series, total

DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
WINDOWS = {"h1": 1, "h3": 3, "h24": 24}
ANTECEDENT_H = 48

BASIS = ("WATCH and CRITICAL are the rain depths that only 15% and 5% of quiet monsoon days "
         "reached over 24 monsoons of ERA5 rainfall, measured against the floods the India "
         "Flood Inventory records for each district, then shifted for each zone's hazard "
         "class and soil group.")

_cache = {}


def _load(name):
    if name not in _cache:
        with open(os.path.join(DATA, f"{name}.json"), encoding="utf-8") as fh:
            _cache[name] = json.load(fh)
    return _cache[name]


def thresholds_for(zone, thresholds):
    """The zone's own depths: the calibrated ones, shifted by its terrain."""
    factor = (thresholds["hazard_factor"].get(zone["hazard_class"], 1.0)
              * thresholds["soil_factor"].get(zone["soil_group"], 1.0))
    return {level: {w: round(mm * factor, 1) for w, mm in body["mm"].items()}
            for level, body in thresholds["levels"].items()}


def status_of(rain, limits):
    """CRITICAL once any window reaches its critical depth, then WATCH."""
    for level in ("critical", "watch"):
        if any(rain[w] is not None and rain[w] >= limits[level][w] for w in WINDOWS):
            return level.upper()
    return "SAFE"


def _rain(values, at):
    """Trailing rain at one hour, in the shape the contract's rain_mm wants."""
    return {**{w: total(values, at, hours) for w, hours in WINDOWS.items()},
            "antecedent_48h": (total(values, at - WINDOWS["h24"], ANTECEDENT_H)
                               if at >= WINDOWS["h24"] else None)}


def _worst_hour(found, day):
    """The hour of `day` when rain was heaviest anywhere in the state.

    Every zone is then read at that same moment, so one replayed list is one
    snapshot rather than each zone's own worst hour stitched together.
    """
    best, at = -1.0, None
    for payload, _ in found.values():
        times, values = series(payload)
        for i, time in enumerate(times):
            if time.date() != day:
                continue
            depth = total(values, i, WINDOWS["h24"]) or 0
            if depth > best:
                best, at = depth, i
    if at is None:
        raise WeatherUnavailable(f"No rainfall was recorded for {day.isoformat()}.")
    return at


def current(replay=None):
    zones = _load("zones")["zones"]
    thresholds = _load("thresholds")
    points = [(z["lat"], z["lon"]) for z in zones]

    if replay:
        found = {cell: (payload, "replay")
                 for cell, payload in history(points, replay).items()}
    else:
        found = payloads(points)
    missing = {z["id"] for z in zones if key(z["lat"], z["lon"]) not in found}
    if missing:
        raise WeatherUnavailable(
            f"Rainfall could not be read for {len(missing)} of {len(zones)} zones.")

    at = _worst_hour(found, replay) if replay else None
    # Zones in the same grid cell share a reading, so each cell is read once.
    rain_by_cell, time_by_cell = {}, {}
    for cell, (payload, _) in found.items():
        times, values = series(payload)
        hour = index_now(times) if at is None else min(at, len(times) - 1)
        rain_by_cell[cell] = _rain(values, hour)
        time_by_cell[cell] = times[hour]

    sources, out = set(), []
    for zone in zones:
        cell = key(zone["lat"], zone["lon"])
        sources.add(found[cell][1])
        limits = thresholds_for(zone, thresholds)
        rain = rain_by_cell[cell]
        out.append({**{k: zone[k] for k in ("id", "name", "parent", "district", "lat", "lon",
                                            "hazard_class", "risk_source")},
                    "status": status_of(rain, limits),
                    "rain_mm": rain,
                    "watch": limits["watch"],
                    "critical": limits["critical"],
                    "soil_group": zone["soil_group"],
                    "catchment_km2": zone["catchment_km2"]})

    return {
        "as_of": min(time_by_cell.values()).isoformat(timespec="seconds"),
        # One stale cell makes the whole list stale, and says so.
        "source": "replay" if replay else ("live" if sources == {"live"} else "relay"),
        "replay": replay.isoformat() if replay else None,
        "threshold_basis": BASIS,
        "zones": out,
    }
