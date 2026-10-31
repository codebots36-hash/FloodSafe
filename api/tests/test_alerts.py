"""Official warnings, push alerts, SOS and the responder summary.

Run from api/:  python -m pytest -q
Nothing here reaches the network: the feed, the push service and the zone
list are stood in for.
"""

import json
import os

import requests
from fastapi.testclient import TestClient

from app import main, push, sachet, sos
from app.main import app

CONTRACTS = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "contracts")
client = TestClient(app)

CAP_DOC = """<cap:alert xmlns:cap="urn:oasis:names:tc:emergency:cap:1.2">
<cap:identifier>IN-1</cap:identifier><cap:sender>Uttarakhand-SDMA</cap:sender>
<cap:info><cap:language>en-IN</cap:language><cap:event>Heavy Rain</cap:event>
<cap:severity>Severe</cap:severity><cap:effective>2026-10-31T08:30:00+05:30</cap:effective>
<cap:expires>2099-01-01T08:30:00+05:30</cap:expires>
<cap:headline>Heavy rain likely over Dehradun</cap:headline>
<cap:area><cap:areaDesc>Dehradun</cap:areaDesc></cap:area></cap:info>
<cap:info><cap:headline>देहरादून में भारी वर्षा की संभावना</cap:headline></cap:info>
</cap:alert>""".encode()
FEED = """<rss><channel>
<item><title>Heavy rain</title><link>https://sachet.ndma.gov.in/x?identifier=1</link>
<author>controlroom@ndma.gov.in (IMD Dehradun)</author><guid>g1</guid></item>
<item><title>Cyclone over Odisha</title><link>https://sachet.ndma.gov.in/x?identifier=2</link>
<author>controlroom@ndma.gov.in (IMD Bhubaneswar)</author><guid>g2</guid></item>
</channel></rss>""".encode()


def contract(name):
    with open(os.path.join(CONTRACTS, name), encoding="utf-8") as fh:
        return json.load(fh)


def fresh_sachet(monkeypatch, get):
    monkeypatch.setattr(sachet, "_docs", {})
    monkeypatch.setattr(sachet, "_state", {"alerts": [], "as_of": None, "ok": False, "tried": 0.0})
    monkeypatch.setattr(sachet, "_get", get)


def test_warnings_keep_uttarakhand_alerts_in_english_and_hindi(monkeypatch):
    opened = []

    def get(url):
        opened.append(url)
        return FEED if url == sachet.FEED else CAP_DOC
    fresh_sachet(monkeypatch, get)

    body = client.get("/warnings").json()
    assert set(body) == set(contract("warnings.json"))
    assert body["status"] == "ok"
    [alert] = body["alerts"]
    assert set(alert) == set(contract("warnings.json")["alerts"][0])
    assert alert["headline_hi"].startswith("देहरादून")
    assert alert["sender"] == "Uttarakhand-SDMA"
    assert len(opened) == 2  # the Odisha item was never opened


def test_an_unreadable_feed_is_unavailable_not_empty(monkeypatch):
    def get(url):
        raise requests.ConnectionError("down")
    fresh_sachet(monkeypatch, get)
    body = client.get("/warnings").json()
    assert (body["status"], body["alerts"]) == ("unavailable", [])


def test_push_is_off_without_keys(monkeypatch):
    monkeypatch.delenv("VAPID_PRIVATE_KEY", raising=False)
    assert client.get("/push/public-key").status_code == 503


def test_a_subscriber_is_pushed_once_when_their_zone_turns_critical(monkeypatch):
    monkeypatch.setenv("VAPID_PUBLIC_KEY", "pub")
    monkeypatch.setenv("VAPID_PRIVATE_KEY", "priv")
    monkeypatch.setattr(main, "notify_later", lambda zones: None)
    sent = []
    monkeypatch.setattr(push, "_send", lambda sub, payload: sent.append(payload) or True)

    request = contract("push_subscribe_request.json")
    request["zone_id"] = "karnaprayag"
    assert client.post("/push/subscribe", json=request).json() == {"ok": True}
    request["zone_id"] = "nowhere"
    assert client.post("/push/subscribe", json=request).status_code == 404

    def zones(as_of, status):
        return {"as_of": as_of, "replay": None, "zones": [
            {"id": "karnaprayag", "name": "Karnaprayag", "status": status, "rain_mm": {"h24": 80.0}}]}
    push.notify(zones("t1", "WATCH"))
    push.notify(zones("t2", "CRITICAL"))
    push.notify(zones("t2", "CRITICAL"))  # same reading again
    push.notify(zones("t3", "CRITICAL"))  # still critical: no second alert
    assert [p["zone_id"] for p in sent] == ["karnaprayag"]


def test_sos_falls_back_to_the_phone_and_shares_only_when_asked(monkeypatch):
    monkeypatch.delenv("TEXTBEE_API_KEY", raising=False)
    monkeypatch.setattr(main, "current_zones", lambda: {"zones": [
        {"id": "karnaprayag", "name": "Karnaprayag", "status": "CRITICAL"}]})
    request = contract("sos_request.json")
    request["numbers"] = ["+919876543210"]
    body = client.post("/sos", json=request).json()
    assert set(body) == set(contract("sos.json"))
    assert body["status"] == "fallback"
    assert "Asha" in body["sms_body"] and body["link"] in body["sms_body"]
    assert sos.pings() == []

    client.post("/sos", json={**request, "share_with_responders": True})
    summary = client.get("/responder/summary").json()
    assert set(summary) == set(contract("responder_summary.json"))
    assert len(summary["sos_pings"]) == 1
    assert summary["critical_zones"] == [{"id": "karnaprayag", "name": "Karnaprayag", "status": "CRITICAL"}]

    bad = client.post("/sos", json={**request, "numbers": ["98765"]})
    assert (bad.status_code, bad.json()["error"]) == (400, "bad_request")
