"""Community reports of flooded, blocked or landslide-hit roads.

Input   POST /reports and POST /reports/{id}/confirm
Output  reports in the shape of contracts/report.json, kept in the database
        (app/store.py: Postgres on a deploy, SQLite on a laptop)

A report is "verified" once two other devices have confirmed it, or, for a
flooded road or a landslide, when at least RAIN_TRUST_MM of rain fell around
it in the 72 hours before it was posted (Open-Meteo, the ~11 km cell it lies
in). Otherwise it is "unverified", and trust_reason says which rule applied
and no more. Verified does not mean proven: nobody here has seen the road.

Safest routing steers around reports (see penalties): roads within
ROUTE_BUFFER_M of an unverified report cost UNVERIFIED_PENALTY times more,
of a verified one VERIFIED_PENALTY times, which all but blocks them. The
reporter id is a random string the app invents, so anyone can invent three
of them; the rate limits keep out accidents and casual noise, not a
determined liar, which is why an unverified report only slows a road.

Reports older than ACTIVE_HOURS drop out of the list: a flooded road is a
statement about now.
"""

import json
import os
import re
import secrets
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from typing import Literal

import numpy as np
from pydantic import BaseModel, Field
from scipy.spatial import cKDTree

from app.graph import M_PER_DEG_LAT, M_PER_DEG_LON
from app.routing import MAX_SNAP_M, RoutingError
from app.store import DATA, IntegrityError, db, schema
from app.weather import WeatherUnavailable, readings

IST = timezone(timedelta(hours=5, minutes=30))

KINDS = ("flooded", "blocked", "landslide")
NOTE_MAX = 280
ACTIVE_HOURS = 24
LIST_MAX = 500
VERIFIED_AT = 2                 # confirmations from other devices
DUPLICATE_M = 100               # same device, same kind, this close and this soon = a double tap
DUPLICATE_MIN = 10
NEAR_LOCALITY_M = 5000
ROUTE_BUFFER_M = 50             # a report this close to a route lies on it
RAIN_TRUST_MM = 2.5             # 72 h rain around a flooded road / landslide that verifies it
RAIN_KINDS = ("flooded", "landslide")
UNVERIFIED_PENALTY = 20         # Safest cost multiplier on roads near a report
VERIFIED_PENALTY = 1000
CANDIDATE_M = 3000              # road nodes this close are checked against a report

# Per hour. Reporter limits are counted in the database and survive a restart;
# the per-IP one is memory only and is just there to blunt a flood.
MAX_REPORTS_PER_REPORTER = 5
MAX_CONFIRMS_PER_REPORTER = 30
MAX_CALLS_PER_IP = 60

REPORTER_ID = re.compile(r"^[A-Za-z0-9_-]{8,64}$")

schema("""CREATE TABLE IF NOT EXISTS reports (
    id TEXT PRIMARY KEY,
    lat DOUBLE PRECISION NOT NULL,
    lon DOUBLE PRECISION NOT NULL,
    kind TEXT NOT NULL,
    note TEXT NOT NULL,
    created_ts DOUBLE PRECISION NOT NULL,
    reporter_id TEXT NOT NULL,
    near_locality TEXT
)""",
       "ALTER TABLE reports ADD COLUMN rain_72h_mm DOUBLE PRECISION",
       "CREATE INDEX IF NOT EXISTS reports_ts ON reports (created_ts)",
       """CREATE TABLE IF NOT EXISTS confirmations (
    report_id TEXT NOT NULL REFERENCES reports (id),
    reporter_id TEXT NOT NULL,
    created_ts DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (report_id, reporter_id)
)""")


class ReportCreate(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    kind: Literal["flooded", "blocked", "landslide"]
    note: str = Field(default="", max_length=NOTE_MAX)
    reporter_id: str = Field(pattern=REPORTER_ID.pattern)


class ReportConfirm(BaseModel):
    reporter_id: str = Field(pattern=REPORTER_ID.pattern)


_hits = defaultdict(deque)


def check_ip(ip, now):
    queue = _hits[ip]
    while queue and queue[0] < now - 3600:
        queue.popleft()
    if len(queue) >= MAX_CALLS_PER_IP:
        raise RoutingError("rate_limited", "Too many requests from this address. Try again later.", 429)
    queue.append(now)


def metres(lat1, lon1, lat2, lon2):
    return float(np.hypot((lat1 - lat2) * M_PER_DEG_LAT, (lon1 - lon2) * M_PER_DEG_LON))


def check_place(graph, lat, lon):
    """The point must be on or near a road inside Uttarakhand."""
    node, gap = graph.snap(lat, lon)
    if gap > MAX_SNAP_M:
        raise RoutingError("too_far_from_road", f"That point is {gap / 1000:.1f} km from the nearest road.")
    lo, hi = graph.csr_indptr[node], graph.csr_indptr[node + 1]
    codes = np.asarray(graph.edge_risk)[graph.csr_edge_id[lo:hi]]
    # 255 = a road with no risk score, which is how the pipeline marks outside the state
    if graph.has_risk and len(codes) and (codes == 255).all():
        raise RoutingError("outside_uttarakhand", "Reports can only be made inside Uttarakhand.")


_places = None


def nearest_locality(lat, lon):
    """Name of the nearest locality within NEAR_LOCALITY_M, or None."""
    global _places
    if _places is None:
        try:
            with open(os.path.join(DATA, "localities.json"), encoding="utf-8") as fh:
                items = json.load(fh)["items"]
        except OSError:
            items = []
        xy = np.array([[i["lon"] * M_PER_DEG_LON, i["lat"] * M_PER_DEG_LAT] for i in items]).reshape(-1, 2)
        _places = (cKDTree(xy) if len(xy) else None, [i["name"] for i in items])
    tree, names = _places
    if tree is None:
        return None
    gap, index = tree.query([lon * M_PER_DEG_LON, lat * M_PER_DEG_LAT], distance_upper_bound=NEAR_LOCALITY_M)
    return names[index] if np.isfinite(gap) else None


def rain_near(lat, lon):
    """Rain in the 72 h to now around a point, or None if it cannot be read."""
    try:
        return readings([(lat, lon)])[0]["last_72h_mm"]
    except WeatherUnavailable:
        return None


def shape(row, confirmations):
    """A database row as the contract's report."""
    rain = row["rain_72h_mm"]
    if confirmations >= VERIFIED_AT:
        trust, reason = "verified", f"Confirmed from {confirmations} different devices"
    elif row["kind"] in RAIN_KINDS and rain is not None and rain >= RAIN_TRUST_MM:
        trust, reason = "verified", (f"{rain:g} mm of rain fell around it in the 72 hours "
                                     "before it was posted")
    elif confirmations:
        trust, reason = "unverified", (f"Confirmed from {confirmations} other device; "
                                       f"{VERIFIED_AT} are needed to verify it")
    else:
        trust, reason = "unverified", "Not confirmed yet"
    return {
        "id": row["id"],
        "lat": row["lat"],
        "lon": row["lon"],
        "kind": row["kind"],
        "note": row["note"],
        "created_at": datetime.fromtimestamp(row["created_ts"], IST).isoformat(timespec="seconds"),
        "trust": trust,
        "trust_reason": reason,
        "confirmations": confirmations,
        "near_locality": row["near_locality"],
    }


LIST_SQL = """
    SELECT r.*, (SELECT COUNT(*) FROM confirmations c WHERE c.report_id = r.id) AS n
    FROM reports r WHERE r.created_ts >= ? ORDER BY r.created_ts DESC LIMIT ?
"""


def active():
    """Reports from the last ACTIVE_HOURS, newest first."""
    with db() as con:
        rows = con.execute(LIST_SQL, (time.time() - ACTIVE_HOURS * 3600, LIST_MAX)).fetchall()
    return [shape(row, row["n"]) for row in rows]


def create(graph, body, ip):
    now = time.time()
    check_ip(ip, now)
    check_place(graph, body.lat, body.lon)
    note = re.sub(r"[\x00-\x1f\x7f]", " ", body.note).strip()

    with db() as con:
        recent = con.execute(
            "SELECT * FROM reports WHERE reporter_id = ? AND created_ts > ?",
            (body.reporter_id, now - 3600)).fetchall()
        for row in recent:  # a double tap returns the first report instead of adding a second
            if (row["kind"] == body.kind and row["created_ts"] > now - DUPLICATE_MIN * 60
                    and metres(row["lat"], row["lon"], body.lat, body.lon) <= DUPLICATE_M):
                n = con.execute("SELECT COUNT(*) AS n FROM confirmations WHERE report_id = ?",
                                (row["id"],)).fetchone()["n"]
                return shape(row, n)
        if len(recent) >= MAX_REPORTS_PER_REPORTER:
            raise RoutingError("rate_limited", f"At most {MAX_REPORTS_PER_REPORTER} reports an hour "
                               "from one device.", 429)

        near = nearest_locality(body.lat, body.lon)
        rain = rain_near(body.lat, body.lon) if body.kind in RAIN_KINDS else None
        for _ in range(5):
            report_id = "r_" + secrets.token_hex(3)
            try:
                con.execute("INSERT INTO reports (id, lat, lon, kind, note, created_ts, reporter_id, "
                            "near_locality, rain_72h_mm) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            (report_id, body.lat, body.lon, body.kind, note, now, body.reporter_id,
                             near, rain))
                break
            except IntegrityError:
                continue
        else:
            raise RoutingError("busy", "Could not save the report. Try again.", 503)
        return shape(con.execute("SELECT * FROM reports WHERE id = ?", (report_id,)).fetchone(), 0)


def confirm(report_id, reporter_id, ip):
    now = time.time()
    check_ip(ip, now)
    with db() as con:
        row = con.execute("SELECT * FROM reports WHERE id = ?", (report_id,)).fetchone()
        if row is None:
            raise RoutingError("not_found", f"No report with id {report_id}", 404)
        if row["created_ts"] < now - ACTIVE_HOURS * 3600:
            raise RoutingError("expired", f"That report is more than {ACTIVE_HOURS} hours old "
                               "and can no longer be confirmed.", 410)
        if row["reporter_id"] == reporter_id:
            raise RoutingError("own_report", "You cannot confirm your own report.", 409)
        made = con.execute("SELECT COUNT(*) AS n FROM confirmations WHERE reporter_id = ? "
                           "AND created_ts > ?", (reporter_id, now - 3600)).fetchone()["n"]
        if made >= MAX_CONFIRMS_PER_REPORTER:
            raise RoutingError("rate_limited", "Too many confirmations from one device this hour.", 429)
        # a second confirmation from the same device is ignored, not counted twice
        con.execute("INSERT INTO confirmations VALUES (?, ?, ?) ON CONFLICT DO NOTHING",
                    (report_id, reporter_id, now))
        n = con.execute("SELECT COUNT(*) AS n FROM confirmations WHERE report_id = ?",
                        (report_id,)).fetchone()["n"]
        return shape(row, n)


def attach(coordinates, segs):
    """Mark the route's segments that active reports lie on; returns (reports, verified).

    Sets each segment's report_id to the most trusted, then newest, report
    within ROUTE_BUFFER_M of it. Steering round reports is penalties' job.
    """
    items = active()
    if not items or len(coordinates) < 2:
        return 0, 0
    xy = np.array(coordinates, dtype=np.float64) * [M_PER_DEG_LON, M_PER_DEG_LAT]
    a, b = xy[:-1], xy[1:]
    ab = b - a
    length2 = np.maximum((ab ** 2).sum(axis=1), 1e-9)
    starts = [s["start"] for s in segs]
    low, high = xy.min(axis=0) - ROUTE_BUFFER_M, xy.max(axis=0) + ROUTE_BUFFER_M

    best, found, verified = {}, 0, 0
    for item in items:
        p = np.array([item["lon"] * M_PER_DEG_LON, item["lat"] * M_PER_DEG_LAT])
        if (p < low).any() or (p > high).any():
            continue
        t = np.clip(((p - a) * ab).sum(axis=1) / length2, 0, 1)
        gap = np.hypot(*(a + t[:, None] * ab - p).T)
        k = int(gap.argmin())
        if gap[k] > ROUTE_BUFFER_M:
            continue
        found += 1
        verified += item["trust"] == "verified"
        seg = max(int(np.searchsorted(starts, k, side="right")) - 1, 0)
        rank = (item["trust"] == "verified", item["created_at"])
        if seg not in best or rank > best[seg][0]:
            best[seg] = (rank, item["id"])
    for seg, (_, report_id) in best.items():
        segs[seg]["report_id"] = report_id
    return found, verified


def _gap_m(p, line):
    """Shortest distance in metres from point p (x, y metres) to a polyline of [lon, lat]."""
    xy = np.asarray(line, dtype=np.float64) * [M_PER_DEG_LON, M_PER_DEG_LAT]
    if len(xy) == 1:
        return float(np.hypot(*(xy[0] - p)))
    a, ab = xy[:-1], xy[1:] - xy[:-1]
    t = np.clip(((p - a) * ab).sum(axis=1) / np.maximum((ab ** 2).sum(axis=1), 1e-9), 0, 1)
    return float(np.hypot(*(a + t[:, None] * ab - p).T).min())


def penalties(graph, items):
    """Safest cost multiplier per directed edge for these reports, or None if there are none.

    An edge is near a report when its line passes within ROUTE_BUFFER_M. Only
    edges leaving a road node within CANDIDATE_M are checked; both directions
    of a two-way road leave such a node, so both are caught.
    """
    # ponytail: an edge longer than 2 x CANDIDATE_M whose ends are both far from
    # the report is missed; index edge bounding boxes if that ever matters.
    if not items:
        return None
    factor = np.ones(len(graph.edge_u), dtype=np.float64)
    for item in items:
        p = np.array([item["lon"] * M_PER_DEG_LON, item["lat"] * M_PER_DEG_LAT])
        weight = VERIFIED_PENALTY if item["trust"] == "verified" else UNVERIFIED_PENALTY
        for node in graph.tree.query_ball_point(p, CANDIDATE_M):
            for edge_id in graph.csr_edge_id[graph.csr_indptr[node]:graph.csr_indptr[node + 1]]:
                if factor[edge_id] < weight and _gap_m(p, graph.edge_coords(edge_id)) <= ROUTE_BUFFER_M:
                    factor[edge_id] = weight
    return factor
