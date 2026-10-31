"""Live rainfall from Open-Meteo.

Input   lat/lon points
Output  one reading per point, in the shape of contracts/weather.json

Open-Meteo publishes a new hour of data once an hour, so a point is fetched
at most once an hour and every other request is served from that copy, marked
"relay". All the points wanted at once go in a single request, and nearby
points share a reading: the zone list is the same for every visitor, and a
hackathon venue behind one NAT would otherwise spend the free daily quota in
minutes.

A failed refresh serves the last good reading, with the "as_of" time it was
taken, rather than nothing. A gap is never filled with a made-up value.

Source: Open-Meteo forecast API (https://open-meteo.com), CC-BY 4.0.
"""

import json
import os
import threading
from datetime import datetime, timedelta, timezone

import requests

IST = timezone(timedelta(hours=5, minutes=30))
API = "https://api.open-meteo.com/v1/forecast"

GRID = 0.1              # points this close share a reading (~11 km, Open-Meteo's own grid)
PAST_DAYS = 4           # enough to cover the trailing 72 h whatever the hour
FORECAST_DAYS = 2       # enough for the next 24 h
FORECAST_HOURS = 24
TRAILING_HOURS = 72
TIMEOUT_S = 20
MAX_POINTS = 100        # per request, well inside what Open-Meteo accepts

RELAY_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "data", "weather_relay.json")

_lock = threading.Lock()
_relay = None  # {"lat,lon": {"hour": "2026-10-31T14", "payload": {...}}}


class WeatherUnavailable(Exception):
    """No reading could be fetched and none was stored earlier."""


def _hour(now=None):
    return (now or datetime.now(IST)).strftime("%Y-%m-%dT%H")


def key(lat, lon):
    """Points within the same grid cell share one reading."""
    return f"{round(lat / GRID) * GRID:.1f},{round(lon / GRID) * GRID:.1f}"


def _load_relay():
    global _relay
    if _relay is None:
        try:
            with open(RELAY_FILE, encoding="utf-8") as fh:
                _relay = json.load(fh)
        except (OSError, ValueError):
            _relay = {}
    return _relay


def _save_relay():
    tmp = RELAY_FILE + ".tmp"
    os.makedirs(os.path.dirname(RELAY_FILE), exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(_relay, fh, separators=(",", ":"))
    os.replace(tmp, RELAY_FILE)


def _request(keys):
    """One call for every point, in the order given. Raises on any failure."""
    pairs = [k.split(",") for k in keys]
    params = {
        "latitude": ",".join(lat for lat, _ in pairs),
        "longitude": ",".join(lon for _, lon in pairs),
        "current": "precipitation",
        "hourly": "precipitation",
        "past_days": PAST_DAYS,
        "forecast_days": FORECAST_DAYS,
        "timezone": "Asia/Kolkata",
    }
    response = requests.get(API, params=params, timeout=TIMEOUT_S)
    if response.status_code != 200:
        reason = response.text[:200]
        try:
            reason = response.json().get("reason", reason)
        except ValueError:
            pass
        raise RuntimeError(f"HTTP {response.status_code}: {reason}")
    payload = response.json()
    # One point comes back as a bare object, several as a list.
    return payload if isinstance(payload, list) else [payload]


def series(payload):
    """(times, values, now_index) from a payload; times are aware IST datetimes.

    The series runs from PAST_DAYS ago to FORECAST_DAYS ahead, so "now" is
    found in it rather than assumed to be the last entry.
    """
    hourly = payload.get("hourly") or {}
    stamps, values = hourly.get("time") or [], hourly.get("precipitation") or []
    if not stamps:
        raise WeatherUnavailable("Open-Meteo returned no hourly rainfall.")
    times = [datetime.fromisoformat(s).replace(tzinfo=IST) for s in stamps]
    now = datetime.now(IST)
    now_index = max((i for i, t in enumerate(times) if t <= now), default=0)
    return times, values, now_index


def total(values, end, hours):
    """Rain over the `hours` ending at index `end`, skipping gaps."""
    chunk = [v for v in values[max(0, end - hours + 1):end + 1] if v is not None]
    return round(sum(chunk), 1) if chunk else None


def _reading(payload, source):
    times, values, i = series(payload)
    current = payload.get("current") or {}
    as_of = current.get("time")
    return {
        "as_of": (datetime.fromisoformat(as_of).replace(tzinfo=IST) if as_of
                  else times[i]).isoformat(timespec="seconds"),
        "source": source,
        "current_mm_h": current.get("precipitation"),
        "last_72h_mm": total(values, i, TRAILING_HOURS),
        "hourly": [{"time": t.isoformat(timespec="seconds"),
                    "mm": None if v is None else round(v, 1)}
                   for t, v in zip(times[i + 1:i + 1 + FORECAST_HOURS],
                                   values[i + 1:i + 1 + FORECAST_HOURS])],
    }


def payloads(points):
    """Raw Open-Meteo payload and its source per point, refreshing by the hour.

    Returns {key: (payload, "live" | "relay")}; a point with no reading at all
    is left out.
    """
    hour = _hour()
    keys = list(dict.fromkeys(key(lat, lon) for lat, lon in points))
    with _lock:
        relay = _load_relay()
        stale = [k for k in keys if (relay.get(k) or {}).get("hour") != hour]

    fetched = {}
    for chunk in [stale[i:i + MAX_POINTS] for i in range(0, len(stale), MAX_POINTS)]:
        try:
            for k, payload in zip(chunk, _request(chunk)):
                fetched[k] = payload
        except (requests.RequestException, RuntimeError, ValueError) as exc:
            # Stale beats empty: the caller gets the last good reading instead.
            print(f"[weather] refresh failed ({exc}); serving the relay copy", flush=True)
            break

    with _lock:
        relay = _load_relay()
        for k, payload in fetched.items():
            relay[k] = {"hour": hour, "payload": payload}
        if fetched:
            _save_relay()
        return {k: (relay[k]["payload"], "live" if k in fetched else "relay")
                for k in keys if k in relay}


def readings(points):
    """One reading per point, in the shape of contracts/weather.json."""
    found = payloads(points)
    out = []
    for lat, lon in points:
        entry = found.get(key(lat, lon))
        if entry is None:
            raise WeatherUnavailable(
                "Rainfall for that point could not be fetched, and none was stored earlier.")
        out.append(_reading(*entry))
    return out
