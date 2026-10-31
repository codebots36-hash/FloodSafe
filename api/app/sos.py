"""SOS: a text to family with your location, and an opt-in ping for responders.

Input   POST /sos (contracts/sos_request.json)
Output  contracts/sos.json. "sent" when textbee (https://textbee.dev) took the
        message, which needs TEXTBEE_API_KEY and TEXTBEE_DEVICE_ID; otherwise
        "fallback", and the app opens the phone's own SMS app with sms_body.

A ping is kept for the responder dashboard only when the person ticks
share_with_responders, and only its place and time are kept, not the name or
numbers. Pings older than PING_HOURS are not shown.
"""

import os
import re
import time

import requests
from pydantic import BaseModel, Field

from app.routing import RoutingError
from app.store import db, schema

TEXTBEE = "https://api.textbee.dev/api/v1/gateway/devices/{device}/send-sms"
PHONE = r"^\+\d{8,15}$"
PING_HOURS = 24

schema("""CREATE TABLE IF NOT EXISTS sos_pings (
    lat DOUBLE PRECISION NOT NULL,
    lon DOUBLE PRECISION NOT NULL,
    created_ts DOUBLE PRECISION NOT NULL
)""")


class SosRequest(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    numbers: list[str] = Field(min_length=1, max_length=5)
    share_with_responders: bool = False


def link(lat, lon):
    return f"https://www.openstreetmap.org/?mlat={lat:.5f}&mlon={lon:.5f}#map=16/{lat:.5f}/{lon:.5f}"


def send(body):
    bad = [n for n in body.numbers if not re.match(PHONE, n)]
    if bad:
        raise RoutingError("bad_request", "numbers: each needs the country code, like +919876543210.")
    name = re.sub(r"[\x00-\x1f\x7f]", " ", body.name).strip()
    where = link(body.lat, body.lon)
    message = f"SOS from {name} via FloodSafe. I need help. My location: {where}"

    if body.share_with_responders:
        with db() as con:
            con.execute("INSERT INTO sos_pings VALUES (?, ?, ?)",
                        (round(body.lat, 4), round(body.lon, 4), time.time()))

    status = "fallback"
    key, device = os.environ.get("TEXTBEE_API_KEY"), os.environ.get("TEXTBEE_DEVICE_ID")
    if key and device:
        try:
            response = requests.post(TEXTBEE.format(device=device), headers={"x-api-key": key},
                                     json={"recipients": body.numbers, "message": message}, timeout=15)
            if response.ok:
                status = "sent"
            else:
                print(f"[sos] textbee refused: HTTP {response.status_code}", flush=True)
        except requests.RequestException as exc:
            print(f"[sos] textbee unreachable: {exc}", flush=True)
    return {"status": status, "sms_body": message, "link": where}


def pings():
    with db() as con:
        rows = con.execute("SELECT lat, lon, created_ts FROM sos_pings WHERE created_ts >= ? "
                           "ORDER BY created_ts DESC LIMIT 200",
                           (time.time() - PING_HOURS * 3600,)).fetchall()
    return [{"lat": r["lat"], "lon": r["lon"], "created_ts": r["created_ts"]} for r in rows]
