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

A shared host is often over Open-Meteo's free daily quota (Render was: HTTP 429
"Daily API request limit exceeded"). store() takes readings fetched from another
address, by pipeline/relay_rainfall.py, so the host need not fetch at all.

Source: Open-Meteo forecast API (https://open-meteo.com), CC-BY 4.0.
"""

import json
import os
import re
import threading
from datetime import date, datetime, timedelta, timezone

import requests

IST = timezone(timedelta(hours=5, minutes=30))
API = "https://api.open-meteo.com/v1/forecast"

GRID = 0.1              # points this close share a reading (~11 km, Open-Meteo's own grid)
PAST_DAYS = 4           # enough to cover the trailing 72 h whatever the hour
# Open-Meteo counts "today" in UTC. Between midnight and 05:30 IST a 2-day series
# ends at 23:00 tonight, i.e. 23 hours ahead at 00:00 and 18 at 05:00 IST, short
# of the 24 the contract promises. Three days always reaches past tomorrow.
FORECAST_DAYS = 3
FORECAST_HOURS = 24
TRAILING_HOURS = 72
TIMEOUT_S = 20
MAX_POINTS = 100        # per request, well inside what Open-Meteo accepts

DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
RELAY_FILE = os.path.join(DATA, "weather_relay.json")

# Replay reads the ERA5 archive, which is the same record the thresholds were
# calibrated on, so a replayed day is judged by the rain that actually fell.
ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"
HISTORY_DIR = os.path.join(DATA, "weather_history")
ARCHIVE_FROM = date(1940, 1, 1)
LEAD_DAYS = 3  # fetched before the date, to fill the 24 h window and the 48 h before it

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
    """(times, values) from a payload; times are aware IST datetimes."""
    hourly = payload.get("hourly") or {}
    stamps, values = hourly.get("time") or [], hourly.get("precipitation") or []
    if not stamps:
        raise WeatherUnavailable("Open-Meteo returned no hourly rainfall.")
    return [datetime.fromisoformat(s).replace(tzinfo=IST) for s in stamps], values


def taken_at(payload, times, i):
    """When a payload's reading was taken: its own clock, else the hour at index i."""
    stamp = (payload.get("current") or {}).get("time")
    return datetime.fromisoformat(stamp).replace(tzinfo=IST) if stamp else times[i]


def index_now(times, payload=None):
    """Where "now" sits in the series, which runs from the past into the forecast.

    A stored reading (the relay copy, or the last good one after a failed
    refresh) is read as of when it was taken, not as of the real now: the hours
    in it after that are forecast, and calling them rain that fell would be
    wrong. Without a payload it is the real now.
    """
    limit = taken_at(payload, times, 0) if payload else datetime.now(IST)
    return max((i for i, t in enumerate(times) if t <= limit), default=0)


def index_peak(times, values, day):
    """The hour of `day` when trailing 24 h rain was heaviest.

    Replaying a date has to pick a moment in it, and the peak is the one worth
    seeing: it is when the day's warning would have been loudest.
    """
    hours = [i for i, t in enumerate(times) if t.date() == day]
    if not hours:
        raise WeatherUnavailable(f"No rainfall was returned for {day.isoformat()}.")
    return max(hours, key=lambda i: total(values, i, TRAILING_HOURS // 3) or 0)


def total(values, end, hours):
    """Rain over the `hours` ending at index `end`, skipping gaps."""
    chunk = [v for v in values[max(0, end - hours + 1):end + 1] if v is not None]
    return round(sum(chunk), 1) if chunk else None


def reading(payload, source, index=None):
    """One reading in the shape of contracts/weather.json.

    `index` is which hour of the series to read as "now"; the default is the
    real one. On a replay the 24 hours that follow are what actually fell,
    not a forecast, which is what "source": "replay" is there to say.
    """
    times, values = series(payload)
    i = index_now(times, payload) if index is None else index
    current = payload.get("current") or {}
    live_now = index is None and current.get("time")
    return {
        "as_of": (taken_at(payload, times, i) if index is None else times[i]).isoformat(timespec="seconds"),
        "source": source,
        "current_mm_h": current.get("precipitation") if live_now else total(values, i, 1),
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


CELL = re.compile(r"^-?\d+\.\d,-?\d+\.\d$")


def store(cells):
    """Keep readings fetched somewhere else as this hour's copy; returns how many.

    The server's own address can be over Open-Meteo's free daily quota (a shared
    host usually is), so pipeline/relay_rainfall.py fetches from another address
    and sends the payloads here. A cell is kept only if it is shaped like one of
    ours and its series reaches the present; the hour is stamped here, not taken
    on trust from the sender.
    """
    hour, now, kept = _hour(), datetime.now(IST), 0
    with _lock:
        relay = _load_relay()
        for cell, payload in cells.items():
            if not CELL.match(cell) or not isinstance(payload, dict):
                continue
            try:
                times, _ = series(payload)
            except (WeatherUnavailable, ValueError):
                continue
            if not times[0] <= now <= times[-1]:
                continue
            relay[cell] = {"hour": hour, "payload": payload}
            kept += 1
        if kept:
            _save_relay()
    return kept


def readings(points):
    """One reading per point, in the shape of contracts/weather.json."""
    return [reading(*found) for found in _each(payloads(points), points)]


def _each(found, points):
    for lat, lon in points:
        entry = found.get(key(lat, lon))
        if entry is None:
            raise WeatherUnavailable(
                "Rainfall for that point could not be fetched, and none was stored earlier.")
        yield entry


# ---------------------------------------------------------------------------
# Replay: the rain that actually fell on a past date
# ---------------------------------------------------------------------------

def parse_day(text):
    """A replay date, or a RuntimeError saying why it cannot be replayed."""
    try:
        day = datetime.strptime(text, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        raise RuntimeError("A replay date must look like YYYY-MM-DD.")
    today = datetime.now(IST).date()
    if day >= today:
        raise RuntimeError(f"{text} is not in the past, so there is no record of it yet.")
    if day < ARCHIVE_FROM:
        raise RuntimeError(f"The rainfall record starts in {ARCHIVE_FROM.year}.")
    return day


def _history_file(day):
    return os.path.join(HISTORY_DIR, f"{day.isoformat()}.json")


def history(points, day):
    """{key: payload} of observed rain for `day`, with the days before it.

    A past date never changes, so each date is fetched once and kept. Keeping
    them also means a demo works without a network.
    """
    keys = list(dict.fromkeys(key(lat, lon) for lat, lon in points))
    try:
        with open(_history_file(day), encoding="utf-8") as fh:
            stored = json.load(fh)
    except (OSError, ValueError):
        stored = {}

    missing = [k for k in keys if k not in stored]
    for chunk in [missing[i:i + MAX_POINTS] for i in range(0, len(missing), MAX_POINTS)]:
        pairs = [k.split(",") for k in chunk]
        params = {
            "latitude": ",".join(lat for lat, _ in pairs),
            "longitude": ",".join(lon for _, lon in pairs),
            "hourly": "precipitation",
            "start_date": (day - timedelta(days=LEAD_DAYS)).isoformat(),
            # The day after as well, so "the next 24 hours" exist for a date
            # whose worst hour falls late in the evening.
            "end_date": min(day + timedelta(days=1),
                            datetime.now(IST).date() - timedelta(days=1)).isoformat(),
            "timezone": "Asia/Kolkata",
            "models": "era5",
        }
        response = requests.get(ARCHIVE, params=params, timeout=TIMEOUT_S)
        if response.status_code != 200:
            reason = response.text[:200]
            try:
                reason = response.json().get("reason", reason)
            except ValueError:
                pass
            raise WeatherUnavailable(f"The rainfall record for {day} could not be read: {reason}")
        payload = response.json()
        for k, one in zip(chunk, payload if isinstance(payload, list) else [payload]):
            stored[k] = one

    if missing:
        os.makedirs(HISTORY_DIR, exist_ok=True)
        tmp = _history_file(day) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(stored, fh, separators=(",", ":"))
        os.replace(tmp, _history_file(day))
    return {k: stored[k] for k in keys}


def replayed(points, day):
    """One reading per point, as it stood at the worst hour of `day`."""
    found = {k: (payload, "replay") for k, payload in history(points, day).items()}
    out = []
    for payload, source in _each(found, points):
        times, values = series(payload)
        out.append(reading(payload, source, index_peak(times, values, day)))
    return out
