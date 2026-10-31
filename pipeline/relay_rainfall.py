"""Fetch rainfall for every watched zone and hand it to a deployed API.

Why. Open-Meteo's free quota is counted per address, and a shared host such as
Render is usually over it already: its own fetch got "HTTP 429: Daily API
request limit exceeded", so /weather and /zones had no rain. This runs from
another address (this laptop, or GitHub Actions) and sends the readings to the
API, which then needs no Open-Meteo access of its own. The same batched call
the API would have made is used, so the two give identical readings.

Input   api/data/zones.json     the watched zones (build_places.py)
        Open-Meteo forecast API  https://open-meteo.com, CC BY 4.0
Output  POST <api>/relay/weather
        RAINFALL_RELAY_SECRET in the environment must match the one set on the
        API, otherwise the API refuses the readings.

One hour of 114 cells is 114 calls against a free 10,000 a day, so hourly use
is about 2,700 a day.

Usage:
    python pipeline/relay_rainfall.py https://floodsafe-api-xxxx.onrender.com
    python pipeline/relay_rainfall.py https://floodsafe-api-xxxx.onrender.com --every 3600
"""

import argparse
import json
import os
import sys
import time

import requests

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "api"))
from app import weather  # noqa: E402  (needs the path above)

ZONES = os.path.join(REPO, "api", "data", "zones.json")
TRIES = 3
RETRY_AFTER_S = 300  # in --every mode, after a failed round


def cells():
    with open(ZONES, encoding="utf-8") as fh:
        zones = json.load(fh)["zones"]
    return sorted({weather.key(z["lat"], z["lon"]) for z in zones})


def fetch(keys):
    out = {}
    for i in range(0, len(keys), weather.MAX_POINTS):
        chunk = keys[i:i + weather.MAX_POINTS]
        out.update(zip(chunk, weather._request(chunk)))
    return out


def send(api, secret, payloads):
    """POST the readings. A sleeping host takes a minute to wake, so be patient."""
    last = None
    for attempt in range(1, TRIES + 1):
        try:
            response = requests.post(f"{api.rstrip('/')}/relay/weather", json={"cells": payloads},
                                     headers={"X-Relay-Secret": secret}, timeout=180)
            if response.status_code == 200:
                return response.json()["kept"]
            last = f"HTTP {response.status_code}: {response.text[:200]}"
            if response.status_code in (403, 503) and "not_configured" not in response.text:
                break  # wrong secret will not fix itself
        except requests.RequestException as exc:
            last = str(exc)
        time.sleep(10 * attempt)
    raise RuntimeError(f"the API did not take the readings ({last})")


def run(api, secret):
    keys = cells()
    payloads = fetch(keys)
    kept = send(api, secret, payloads)
    print(f"{time.strftime('%H:%M:%S')}  fetched {len(payloads)} cells, the API kept {kept}", flush=True)
    if kept != len(keys):
        raise RuntimeError(f"expected {len(keys)} cells kept, the API kept {kept}")


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("api", help="address of the deployed API")
    parser.add_argument("--every", type=int, metavar="SECONDS", help="keep running, once per this many seconds")
    args = parser.parse_args()
    secret = os.environ.get("RAINFALL_RELAY_SECRET")
    if not secret:
        sys.exit("Set RAINFALL_RELAY_SECRET to the same value as on the API.")

    while True:
        try:
            run(args.api, secret)
            wait = args.every
        except (RuntimeError, requests.RequestException) as exc:
            print(f"{time.strftime('%H:%M:%S')}  failed: {exc}", flush=True)
            if not args.every:
                sys.exit(1)
            wait = min(args.every, RETRY_AFTER_S)
        if not args.every:
            return
        time.sleep(wait)


if __name__ == "__main__":
    main()
