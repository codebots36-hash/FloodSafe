"""Web push: tell a subscribed phone when its zone turns CRITICAL.

Input   POST /push/subscribe (a browser PushSubscription and a zone id), and
        the zone list each time /zones or the rainfall relay recomputes it
Output  one push per subscription when its zone goes from not CRITICAL to
        CRITICAL; nothing on the way back down, and nothing on a replay

Off unless VAPID_PUBLIC_KEY and VAPID_PRIVATE_KEY are set. Make a pair with
`python -m app.push` from api/ and set both on the server (the public one is
not secret; the app fetches it from /push/public-key).
"""

import json
import os
import threading
import time

from pydantic import BaseModel, Field

from app.routing import RoutingError
from app.store import db, schema

SUBJECT = os.environ.get("VAPID_SUBJECT", "mailto:floodsafe@example.invalid")
MAX_SUBSCRIPTIONS = 5000

schema("""CREATE TABLE IF NOT EXISTS push_subs (
    endpoint TEXT PRIMARY KEY,
    subscription TEXT NOT NULL,
    zone_id TEXT NOT NULL,
    last_status TEXT,
    created_ts DOUBLE PRECISION NOT NULL
)""")

_last_seen = {"as_of": None}
_lock = threading.Lock()


class SubscriptionKeys(BaseModel):
    p256dh: str = Field(max_length=200)
    auth: str = Field(max_length=100)


class Subscription(BaseModel):
    endpoint: str = Field(pattern=r"^https://", max_length=1000)
    expirationTime: float | None = None
    keys: SubscriptionKeys


class Subscribe(BaseModel):
    subscription: Subscription
    zone_id: str = Field(max_length=100)


def public_key():
    key = os.environ.get("VAPID_PUBLIC_KEY")
    if not key or not os.environ.get("VAPID_PRIVATE_KEY"):
        raise RoutingError("not_configured", "Push alerts are not switched on here.", 503)
    return key


def subscribe(body, zone_ids):
    public_key()
    if body.zone_id not in zone_ids:
        raise RoutingError("not_found", f"No zone with id {body.zone_id}", 404)
    with db() as con:
        count = con.execute("SELECT COUNT(*) AS n FROM push_subs").fetchone()["n"]
        if count >= MAX_SUBSCRIPTIONS:
            raise RoutingError("full", "No room for more push subscriptions.", 503)
        con.execute("INSERT INTO push_subs VALUES (?, ?, ?, NULL, ?) ON CONFLICT (endpoint) "
                    "DO UPDATE SET subscription = excluded.subscription, zone_id = excluded.zone_id",
                    (body.subscription.endpoint, body.subscription.model_dump_json(),
                     body.zone_id, time.time()))
    return {"ok": True}


def _send(subscription, payload):
    """True if the push service took it; False if the subscription is gone."""
    from pywebpush import WebPushException, webpush
    try:
        webpush(subscription_info=json.loads(subscription), data=json.dumps(payload),
                vapid_private_key=os.environ["VAPID_PRIVATE_KEY"], vapid_claims={"sub": SUBJECT},
                timeout=10)
        return True
    except WebPushException as exc:
        status = getattr(exc.response, "status_code", None)
        print(f"[push] send failed ({status}): {exc}", flush=True)
        return status not in (404, 410)


def notify(zones_response):
    """Push to everyone whose zone has just turned CRITICAL. Runs once per new reading."""
    if zones_response.get("replay") or not os.environ.get("VAPID_PRIVATE_KEY"):
        return
    with _lock:
        if _last_seen["as_of"] == zones_response["as_of"]:
            return
        _last_seen["as_of"] = zones_response["as_of"]
    status = {z["id"]: z for z in zones_response["zones"]}
    with db() as con:
        subs = con.execute("SELECT endpoint, subscription, zone_id, last_status FROM push_subs").fetchall()
    # Sent with no transaction open: each push can wait seconds on the push
    # service, and a held SQLite write lock would stall report saving meanwhile.
    gone, changed = [], []
    for sub in subs:
        zone = status.get(sub["zone_id"])
        if zone is None or zone["status"] == sub["last_status"]:
            continue
        if zone["status"] == "CRITICAL":
            payload = {"title": f"{zone['name']} is CRITICAL for flooding",
                       "body": f"{zone['rain_mm']['h24']} mm of rain in 24 h. Avoid travel "
                               "near rivers and check official warnings.",
                       "zone_id": zone["id"]}
            if not _send(sub["subscription"], payload):
                gone.append((sub["endpoint"],))
                continue
        changed.append((zone["status"], sub["endpoint"]))
    with db() as con:
        for row in gone:
            con.execute("DELETE FROM push_subs WHERE endpoint = ?", row)
        for row in changed:
            con.execute("UPDATE push_subs SET last_status = ? WHERE endpoint = ?", row)


if __name__ == "__main__":
    import base64

    from cryptography.hazmat.primitives import serialization
    from py_vapid import Vapid01

    vapid = Vapid01()
    vapid.generate_keys()
    raw = vapid.public_key.public_bytes(serialization.Encoding.X962,
                                        serialization.PublicFormat.UncompressedPoint)
    private = vapid.private_key.private_numbers().private_value.to_bytes(32, "big")
    print("VAPID_PUBLIC_KEY=" + base64.urlsafe_b64encode(raw).decode().rstrip("="))
    print("VAPID_PRIVATE_KEY=" + base64.urlsafe_b64encode(private).decode().rstrip("="))
