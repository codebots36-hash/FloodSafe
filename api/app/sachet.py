"""Official warnings for Uttarakhand from NDMA's SACHET feed.

Input   the all-India SACHET RSS feed, and the CAP 1.2 document each item links
        to (https://sachet.ndma.gov.in/, published by NDMA as public domain)
Output  the /warnings response described in contracts/warnings.json

IMD, CWC and the state disaster authority publish their alerts through
SACHET. Only items that could be about Uttarakhand (issued from Dehradun or by
the state, or naming the state or a district) are opened; CWC's river alerts
come from one national office, so all of theirs are opened. An alert is kept
when its sender, area or headline names Uttarakhand or one of its districts,
and it has not expired.

Alerts are shown as issued, apart from FloodSafe's own zone status. A feed
that cannot be read gives "unavailable", never an empty "ok" list, so the app
never says "no warnings" when it simply could not look.
"""

import re
import threading
import time
import xml.etree.ElementTree as ET
from datetime import datetime

import requests

from app.weather import IST

FEED = "https://sachet.ndma.gov.in/cap_public_website/rss/rss_india.xml"
HEADERS = {"User-Agent": "FloodSafe (+https://github.com/codebots36-hash/FloodSafe)"}
TIMEOUT_S = 15
REFRESH_S = 10 * 60
MAX_DOCS = 30            # CAP documents opened per refresh; each is read once and kept
MAX_BYTES = 1_000_000
CAP = {"cap": "urn:oasis:names:tc:emergency:cap:1.2"}

STATE = ("uttarakhand", "uttaranchal", "उत्तराखंड", "उत्तराखण्ड")
DISTRICTS = ("almora", "bageshwar", "chamoli", "champawat", "dehradun", "dehra dun", "haridwar",
             "hardwar", "nainital", "pauri", "pithoragarh", "rudraprayag", "tehri",
             "udham singh nagar", "uttarkashi", "अल्मोड़ा", "बागेश्वर", "चमोली", "चंपावत",
             "देहरादून", "हरिद्वार", "नैनीताल", "पौड़ी", "पिथौरागढ़", "रुद्रप्रयाग", "टिहरी",
             "उधम सिंह नगर", "उत्तरकाशी")
DEVANAGARI = re.compile("[ऀ-ॿ]")

_lock = threading.Lock()
_docs = {}               # guid -> parsed alert (or None if not about Uttarakhand)
_state = {"alerts": [], "as_of": None, "ok": False, "tried": 0.0}


def names_uttarakhand(text):
    text = (text or "").lower()
    return any(word in text for word in STATE + DISTRICTS)


def _get(url):
    response = requests.get(url, headers=HEADERS, timeout=TIMEOUT_S)
    response.raise_for_status()
    if len(response.content) > MAX_BYTES:
        raise ValueError("document too large")
    return response.content


def parse_cap(xml, guid, link):
    """The contract's alert from one CAP document, or None if it is not about Uttarakhand."""
    root = ET.fromstring(xml)

    def text(node, tag):
        found = node.find(f"cap:{tag}", CAP) if node is not None else None
        return (found.text or "").strip() if found is not None and found.text else ""

    infos = root.findall("cap:info", CAP)
    if not infos:
        return None
    english = next((i for i in infos if text(i, "language").lower().startswith("en")), infos[0])
    hindi = next((i for i in infos if text(i, "language").lower().startswith("hi")
                  or DEVANAGARI.search(text(i, "headline"))), None)
    area = "; ".join(text(a, "areaDesc") for a in english.findall("cap:area", CAP) if text(a, "areaDesc"))
    sender = text(root, "sender")
    headline = text(english, "headline")
    if not any(names_uttarakhand(t) for t in (sender, area, headline)):
        return None
    return {
        "id": guid,
        "headline": headline,
        "headline_hi": text(hindi, "headline") if hindi is not None else "",
        "severity": text(english, "severity"),
        "event": text(english, "event"),
        "area": area,
        "sender": sender,
        "effective": text(english, "effective"),
        "expires": text(english, "expires"),
        "link": link,
    }


def _current(alert, now):
    try:
        return not alert["expires"] or datetime.fromisoformat(alert["expires"]) > now
    except (ValueError, TypeError):  # TypeError: an expiry with no time zone
        return True


def refresh():
    """Read the feed and rebuild the list; keeps the last good list on failure."""
    try:
        feed = ET.fromstring(_get(FEED))
    except (requests.RequestException, ValueError, ET.ParseError) as exc:
        print(f"[sachet] feed could not be read: {exc}", flush=True)
        _state["ok"] = False
        return
    opened = 0
    for item in feed.findall("./channel/item"):
        guid = (item.findtext("guid") or "").strip()
        link = (item.findtext("link") or "").strip()
        author = (item.findtext("author") or "").lower()
        if not guid or guid in _docs or not link.startswith("https://sachet.ndma.gov.in/"):
            continue
        if not ("dehradun" in author or "uttarakhand" in author or "(cwc)" in author
                or names_uttarakhand(item.findtext("title"))):
            continue
        if opened >= MAX_DOCS:
            break  # the rest are opened on the next refresh
        opened += 1
        try:
            _docs[guid] = parse_cap(_get(link), guid, link)
        except (requests.RequestException, ValueError, ET.ParseError) as exc:
            print(f"[sachet] alert {guid} could not be read: {exc}", flush=True)
    now = datetime.now(IST)
    _state["alerts"] = sorted((a for a in _docs.values() if a and _current(a, now)),
                              key=lambda a: a["effective"], reverse=True)
    _state["as_of"] = now.isoformat(timespec="seconds")
    _state["ok"] = True


def warnings():
    """The /warnings response, re-reading the feed at most every REFRESH_S."""
    with _lock:
        if time.time() - _state["tried"] > REFRESH_S:
            _state["tried"] = time.time()
            refresh()
        return {"status": "ok" if _state["ok"] else "unavailable",
                "as_of": _state["as_of"],
                "alerts": list(_state["alerts"])}
