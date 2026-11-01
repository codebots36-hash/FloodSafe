"""Rain forecast along a route, for the hour you reach each stretch.

Input   the 200 watched zones and their hourly rain (app/weather.py, the same
        cached copy /zones uses, so routing spends no extra Open-Meteo quota)
Output  a Forecast that says, for any road edge and arrival time, the rain
        in that hour and whether the nearest zone is WATCH or CRITICAL then

Each zone's series runs from four days back into the forecast, so the status
at an hour is read exactly as /zones reads it now: trailing 1, 3 and 24 h rain
against the zone's own thresholds, part observed and part forecast. An edge
takes the nearest zone within MAX_ZONE_M; one further away has no forecast.
On a replayed date the series is the rain that actually fell (ERA5), so
"forecast" there means what came next, which is what the replay is for.
"""

from datetime import datetime

import numpy as np
from scipy.spatial import cKDTree

from app.graph import M_PER_DEG_LAT, M_PER_DEG_LON
from app.weather import IST, WeatherUnavailable, _hour, history, key, payloads, series
from app.zones import WINDOWS, _load, _worst_hour, thresholds_for

MAX_ZONE_M = 20000      # zones are ~16 km apart on average; further than this, no forecast
LEVELS = {"SAFE": 0, "WATCH": 1, "CRITICAL": 2}

_edge_zone = None       # nearest zone per directed edge, -1 for none
_cache = {}             # (hour, replay) -> Forecast; one entry is kept


class Forecast:
    """Per-zone hourly rain and status on one shared clock."""

    def __init__(self, zones, found):
        thresholds = _load("thresholds")
        rows = []
        for zone in zones:
            times, values = series(found[key(zone["lat"], zone["lon"])][0])
            rain = np.array([np.nan if v is None else v for v in values], dtype=np.float64)
            limits = thresholds_for(zone, thresholds)
            sums = np.concatenate([[0.0], np.cumsum(np.nan_to_num(rain))])
            idx = np.arange(len(rain))
            level = np.zeros(len(rain), dtype=np.int8)
            for name, rank in (("watch", 1), ("critical", 2)):
                hit = np.zeros(len(rain), dtype=bool)
                for w, hours in WINDOWS.items():
                    hit |= sums[idx + 1] - sums[np.maximum(0, idx + 1 - hours)] >= limits[name][w]
                level[hit] = rank
            rows.append((times[0].timestamp(), rain, level))

        width = max(len(r[1]) for r in rows)
        self.t0 = np.array([r[0] for r in rows])
        self.mm = np.full((len(rows), width), np.nan)
        self.level = np.zeros((len(rows), width), dtype=np.int8)
        for i, (_, rain, level) in enumerate(rows):
            self.mm[i, :len(rain)] = rain
            self.level[i, :len(level)] = level
        self.end = max(r[0] + 3600 * (len(r[1]) - 1) for r in rows)

    def at(self, zone_idx, when_ts):
        """(mm, level) per item; mm is NaN and level 0 where nothing is known."""
        zone_idx = np.asarray(zone_idx)
        when_ts = np.asarray(when_ts, dtype=np.float64)
        known = (zone_idx >= 0) & np.isfinite(when_ts)
        z = np.where(known, zone_idx, 0)
        # Open-Meteo's value at hour T is the rain that fell in the hour before T.
        h = np.ceil((np.where(known, when_ts, 0) - self.t0[z]) / 3600).astype(np.int64)
        known &= (h >= 0) & (h < self.mm.shape[1])
        h = np.where(known, h, 0)
        mm = np.where(known, self.mm[z, h], np.nan)
        return mm, np.where(known, self.level[z, h], 0)


def edge_zones(graph):
    """Nearest zone for every directed edge (by its start node), -1 if none is close."""
    global _edge_zone
    if _edge_zone is None:
        zones = _load("zones")["zones"]
        tree = cKDTree([[z["lon"] * M_PER_DEG_LON, z["lat"] * M_PER_DEG_LAT] for z in zones])
        u = np.asarray(graph.edge_u)
        xy = np.column_stack([np.asarray(graph.node_lon)[u] * M_PER_DEG_LON,
                              np.asarray(graph.node_lat)[u] * M_PER_DEG_LAT])
        gap, idx = tree.query(xy, distance_upper_bound=MAX_ZONE_M)
        _edge_zone = np.where(np.isfinite(gap), idx, -1).astype(np.int16)
    return _edge_zone


def load(replay=None):
    """(Forecast, peak) for now, or for a replayed day; peak is that day's
    worst hour (None live). Raises WeatherUnavailable when the rain is missing."""
    cache_key = (_hour(), replay)
    # Read once: another request may clear the cache between a check and a lookup.
    entry = _cache.get(cache_key)
    if entry is None:
        zones = _load("zones")["zones"]
        points = [(z["lat"], z["lon"]) for z in zones]
        if replay:
            found = {c: (p, "replay") for c, p in history(points, replay).items()}
        else:
            found = payloads(points)
        missing = [z for z in zones if key(z["lat"], z["lon"]) not in found]
        if missing:
            raise WeatherUnavailable(f"Rainfall could not be read for {len(missing)} zones.")
        peak = None
        if replay:
            at = _worst_hour(found, replay)
            peak = series(next(iter(found.values()))[0])[0][at]
        entry = (Forecast(zones, found), peak)
        _cache.clear()
        _cache[cache_key] = entry
    return entry


def parse_depart(text):
    """An aware datetime from an ISO string; a time without an offset is read as IST."""
    try:
        when = datetime.fromisoformat(text)
    except (TypeError, ValueError):
        raise ValueError("depart must be an ISO 8601 time, like 2026-10-31T14:00:00+05:30.")
    return when if when.tzinfo else when.replace(tzinfo=IST)

