"""FloodSafe API.

Serves the endpoints described in contracts/. Implemented so far:
  GET /health             the server is up
  GET /route              a road route between two points (fastest or safest)
  GET /layers/road-risk   roads rated MODERATE or worse (GeoJSON)
  GET /layers/atlas       surveyed hazard atlas polygons (GeoJSON)
  GET /shelters, /hospitals, /localities   places from OpenStreetMap
  GET /weather            live rainfall for a point (Open-Meteo)
  GET /zones              watched places and their rain status
  GET /reports, POST /reports, POST /reports/{id}/confirm   community reports
  GET /warnings           official alerts from NDMA SACHET
  GET /push/public-key, POST /push/subscribe   push alerts when a zone turns CRITICAL
  POST /sos               an SOS text with the location
  GET /responder/summary  reports, zones not SAFE, shelters, SOS pings

Data sources: the road graph built by pipeline/build_graph.py from
OpenStreetMap (Geofabrik India central zone, ODbL), with risk classes from
pipeline/build_road_risk.py; places from pipeline/build_places.py.

Run from api/: uvicorn app.main:app --host 0.0.0.0 --port 8000
"""

import hmac
import json
import os
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import numpy as np
from fastapi import FastAPI, Header, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import forecast, push, reports, sachet, sos
from app.forecast import parse_depart
from app.graph import get_graph
from app.routing import (MAX_DETOUR, RoutingError, km_by_class, route, route_timed,
                         safest_weights, segments)
from app.store import DBError
from app.weather import WeatherUnavailable, parse_day, readings, replayed, store
from app.zones import current as current_zones

IST = timezone(timedelta(hours=5, minutes=30))

API_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Same order as the graph (app/graph.py): a local build first, else the
# copy made by pipeline/package_api_data.py.
LAYER_DIRS = [
    os.path.join(os.path.dirname(API_DIR), "pipeline", "out", "layers"),  # local build
    os.path.join(API_DIR, "data", "layers"),  # what a deploy runs on
]
LAYER_FILES = {"road-risk": "road_risk.geojson", "atlas": "atlas.geojson"}
DATA_DIR = os.path.join(API_DIR, "data")  # pipeline/build_places.py writes here

# Without pipeline/build_road_risk.py's files no edge has a risk class. The
# route still reports its shape honestly and says the scores are missing,
# rather than dressing up an unscored road as a safe one.
RISK_NOT_READY = "Risk scores are not loaded, so no stretch of this route has a risk class."


@asynccontextmanager
async def lifespan(_app):
    get_graph()  # pay the load cost at startup, not on the first request
    yield


app = FastAPI(title="FloodSafe", lifespan=lifespan)

# The React app runs on another laptop and origin, so allow every origin.
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
# The road-risk layer is ~11 MB of GeoJSON and ~1 MB gzipped.
app.add_middleware(GZipMiddleware, minimum_size=1000)


@app.exception_handler(RoutingError)
async def routing_error(_request, exc: RoutingError):
    return JSONResponse(status_code=exc.status, content={"error": exc.code, "detail": exc.detail})


@app.exception_handler(RequestValidationError)
async def bad_request(_request, exc: RequestValidationError):
    """Every error has the {error, detail} shape of contracts/error.json."""
    first = exc.errors()[0]
    field = ".".join(str(p) for p in first["loc"] if p not in ("body", "query", "path"))
    detail = f"{field}: {first['msg']}" if field else first["msg"]
    return JSONResponse(status_code=400, content={"error": "bad_request", "detail": detail})


@app.exception_handler(StarletteHTTPException)
async def http_error(_request, exc: StarletteHTTPException):
    """An unknown address or wrong method, in the same {error, detail} shape."""
    known = {404: ("not_found", "There is no endpoint at that address. Open / for where to start."),
             405: ("method_not_allowed", "That endpoint does not accept this method.")}
    code, detail = known.get(exc.status_code, ("http_error", str(exc.detail)))
    return JSONResponse(status_code=exc.status_code, content={"error": code, "detail": detail},
                        headers=getattr(exc, "headers", None))


@app.get("/", include_in_schema=False)
def index():
    return {"name": "FloodSafe API", "health": "/health", "docs": "/docs",
            "contracts": "https://github.com/codebots36-hash/FloodSafe/tree/main/contracts"}


def parse_point(text, name):
    try:
        lat_text, lon_text = text.split(",")
        lat, lon = float(lat_text), float(lon_text)
    except (ValueError, AttributeError):
        raise RoutingError("bad_point", f"{name} must look like 'lat,lon'.")
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise RoutingError("bad_point", f"{name} is not a valid latitude and longitude.")
    return lat, lon


@app.get("/health")
def health():
    graph = get_graph()
    return {"status": "ok", "nodes": graph.n_nodes, "edges": int(len(graph.edge_u))}


MAX_DEPART_AHEAD = timedelta(hours=48)  # the forecast reaches about this far


def departure(text, day, peak):
    """When the trip starts. Live: now, or a time up to MAX_DEPART_AHEAD ahead.
    On a replay: a time on that day, by default its worst hour of rain."""
    now = datetime.now(IST)
    try:
        when = parse_depart(text) if text else None
    except ValueError as exc:
        raise RoutingError("bad_depart", str(exc))
    if day:
        if when is None:
            return peak or datetime.combine(day, datetime.min.time(), IST).replace(hour=12)
        if when.astimezone(IST).date() != day:
            raise RoutingError("bad_depart", f"On a replay of {day.isoformat()}, depart must be on that day.")
        return when
    if when is None:
        return now
    if when < now - timedelta(hours=1):
        raise RoutingError("bad_depart", "depart is in the past; use a replay date for a past trip.")
    if when > now + MAX_DEPART_AHEAD:
        raise RoutingError("bad_depart", "The rain forecast only reaches 48 hours ahead.")
    return when


@app.get("/route")
def get_route(
    from_: str = Query(..., alias="from", description="lat,lon"),
    to: str = Query(..., description="lat,lon"),
    mode: str = Query("fastest", pattern="^(fastest|safest)$"),
    depart: str | None = Query(None, description="ISO 8601; defaults to now"),
    replay: str | None = Query(None, description="YYYY-MM-DD; the rain that fell that day"),
):
    day = replay_day(replay)
    graph = get_graph()
    if mode == "safest" and not graph.has_risk:
        raise RoutingError("not_ready", "Safest routing needs the road risk scores.", 501)

    start, end = parse_point(from_, "from"), parse_point(to, "to")
    notes = []
    try:
        sky, peak = forecast.load(day)
    except WeatherUnavailable as exc:
        print(f"[route] no rain for routing: {exc}", flush=True)
        sky, peak = None, None
        notes.append("Rain could not be read, so this route does not take it into account.")
    leave = departure(depart, day, peak)

    result = route(graph, start, end)
    zone = forecast.edge_zones(graph) if sky else None
    if mode == "safest":
        penalty = None
        if not day:  # reports are about now, so a replayed past date has none
            try:
                penalty = reports.penalties(graph, reports.active())
            except DBError as exc:
                print(f"[reports] could not read reports for routing: {exc}", flush=True)

        def safest(level):
            weights = safest_weights(graph, level, penalty)
            if sky:
                # Each road is judged on the rain at the hour this route reaches it.
                return route_timed(graph, start, end, weights, leave.timestamp(), sky, zone)
            return route(graph, start, end, weights)

        fastest_s = result["duration_s"]
        result = safest("strict")
        if result["duration_s"] > MAX_DETOUR * fastest_s:
            result = safest("mild")
            notes.append(f"Avoiding every risky road meant a trip over {MAX_DETOUR} times "
                         "as long, so this one uses milder risk penalties.")

    edge_ids = result["edge_ids"]
    rain_mm = None
    if sky:
        # The hour you reach each stretch along the route actually taken.
        reach = leave.timestamp() + np.concatenate([[0.0], np.cumsum(graph.travel_s[edge_ids])[:-1]])
        rain_mm, level = sky.at(zone[edge_ids], reach)
        wet_km = round(float(np.asarray(graph.edge_len_m)[edge_ids][level > 0].sum()) / 1000, 1)
        if wet_km:
            notes.append(f"By the time you reach them, {wet_km} km of this route are in places "
                         "at WATCH or CRITICAL for rain.")
        if reach[-1] + graph.travel_s[edge_ids[-1]] > sky.end:
            notes.append("The rain record does not reach the end of this trip.")
    km, unscored_km = km_by_class(graph, edge_ids)
    segs = segments(graph, edge_ids, result["bounds"], rain_mm)
    if day:
        notes.append(f"Replaying {day.isoformat()}: the rain is what actually fell that day "
                     "(ERA5), read for the hour you reach each stretch.")
    else:
        try:
            found, verified = reports.attach(result["coordinates"], segs)
        except DBError as exc:
            print(f"[reports] could not read reports for a route: {exc}", flush=True)
            notes.append("Community reports could not be read, so none are shown on this route.")
        else:
            if found:
                notes.append(f"{found} community report{'s' if found != 1 else ''} "
                             f"lie{'s' if found == 1 else ''} on this route ({verified} verified).")
    if not graph.has_risk:
        notes.append(RISK_NOT_READY)
    elif unscored_km:
        notes.append(f"{unscored_km} km of this route is outside Uttarakhand and has no risk class.")
    if km["EXTREME"]:
        notes.append(f"{km['EXTREME']} km of this route is rated EXTREME."
                     + (" Try the safest route." if mode == "fastest" else
                        " No way around it was found."))

    return {
        "mode": mode,
        "depart": leave.astimezone(IST).isoformat(timespec="seconds"),
        "replay": day.isoformat() if day else None,
        "distance_km": round(result["distance_m"] / 1000, 1),
        "duration_min": round(result["duration_s"] / 60),
        "geometry": {"type": "LineString", "coordinates": result["coordinates"]},
        "segments": segs,
        "summary": {
            "km_by_class": km,
            "extreme_segments": sum(s["risk_class"] == "EXTREME" for s in segs),
        },
        "notes": notes,
    }


@app.get("/layers/{name}")
def get_layer(name: str, request: Request):
    if name not in LAYER_FILES:
        raise RoutingError("not_found", f"No layer called '{name}'.", 404)
    headers = {"Cache-Control": "public, max-age=3600", "Vary": "Accept-Encoding"}
    for folder in LAYER_DIRS:
        path = os.path.join(folder, LAYER_FILES[name])
        if os.path.exists(path):
            # A deploy has the layer already gzipped (pipeline/package_api_data.py):
            # compressing 11 MB on every request cost ~7 s on a small host. The
            # middleware leaves a response that is already encoded alone.
            if os.path.exists(path + ".gz") and "gzip" in request.headers.get("accept-encoding", ""):
                return FileResponse(path + ".gz", media_type="application/geo+json",
                                    headers={**headers, "Content-Encoding": "gzip"})
            return FileResponse(path, media_type="application/geo+json", headers=headers)
    raise RoutingError("not_found", f"The {name} layer has not been built yet.", 404)


def places_file(name):
    path = os.path.join(DATA_DIR, f"{name}.json")
    if not os.path.exists(path):
        raise RoutingError("not_found", f"The {name} list has not been built yet.", 404)
    return FileResponse(path, media_type="application/json",
                        headers={"Cache-Control": "public, max-age=3600"})


def replay_day(replay):
    """A replay date, or None for live data."""
    if not replay:
        return None
    try:
        return parse_day(replay)
    except RuntimeError as exc:
        raise RoutingError("bad_replay", str(exc))


@app.get("/weather")
def get_weather(
    lat: float = Query(..., ge=-90, le=90),
    lon: float = Query(..., ge=-180, le=180),
    replay: str | None = Query(None, description="YYYY-MM-DD; the rain that fell that day"),
):
    day = replay_day(replay)
    try:
        return (replayed([(lat, lon)], day) if day else readings([(lat, lon)]))[0]
    except WeatherUnavailable as exc:
        raise RoutingError("weather_unavailable", str(exc), 503)


class WeatherRelay(BaseModel):
    cells: dict[str, dict]


@app.post("/relay/weather")
def relay_weather(body: WeatherRelay, x_relay_secret: str | None = Header(None)):
    """Rainfall fetched from another address; see pipeline/relay_rainfall.py.

    Off unless RAINFALL_RELAY_SECRET is set on the server, and it must match.
    """
    secret = os.environ.get("RAINFALL_RELAY_SECRET", "")
    if not secret:
        raise RoutingError("not_configured", "The rainfall relay is not switched on here.", 503)
    if not hmac.compare_digest((x_relay_secret or "").encode(), secret.encode()):
        raise RoutingError("forbidden", "Wrong relay secret.", 403)
    kept = store(body.cells)
    try:
        notify_later(current_zones())  # fresh rain may have turned a zone CRITICAL
    except WeatherUnavailable:
        pass
    return {"ok": True, "kept": kept}


def notify_later(zones):
    """Send push alerts off the request, so a slow push service never delays a page."""
    def run():
        try:
            push.notify(zones)
        except Exception as exc:  # a background thread has nobody else to tell
            print(f"[push] notify failed: {exc}", flush=True)
    threading.Thread(target=run, daemon=True).start()


@app.get("/zones")
def get_zones(replay: str | None = Query(None, description="YYYY-MM-DD; the rain that fell that day")):
    try:
        result = current_zones(replay_day(replay))
    except WeatherUnavailable as exc:
        raise RoutingError("weather_unavailable", str(exc), 503)
    notify_later(result)
    return result


@app.get("/warnings")
def get_warnings():
    return sachet.warnings()


@app.get("/push/public-key")
def get_push_key():
    return {"public_key": push.public_key()}


@app.post("/push/subscribe")
def post_push_subscribe(body: push.Subscribe):
    with open(os.path.join(DATA_DIR, "zones.json"), encoding="utf-8") as fh:
        zone_ids = {z["id"] for z in json.load(fh)["zones"]}
    return push.subscribe(body, zone_ids)


@app.post("/sos")
def post_sos(body: sos.SosRequest, request: Request):
    reports.check_ip(client_ip(request), time.time())
    return sos.send(body)


@app.get("/responder/summary")
def get_responder_summary():
    """One screen for a district control room. Each part is read on its own,
    so one missing source never blanks the rest."""
    now = datetime.now(IST)
    try:
        critical = [{"id": z["id"], "name": z["name"], "status": z["status"]}
                    for z in current_zones()["zones"] if z["status"] != "SAFE"]
    except WeatherUnavailable:
        critical = None
    try:
        with open(os.path.join(DATA_DIR, "shelters.json"), encoding="utf-8") as fh:
            shelters = len(json.load(fh)["items"])
    except OSError:
        shelters = None
    try:
        report_count = len(reports.active())
        pings = sos.pings()
    except DBError as exc:
        print(f"[responder] database could not be read: {exc}", flush=True)
        report_count, pings = None, []
    return {
        "as_of": now.isoformat(timespec="seconds"),
        "reports_last_24h": report_count,
        "critical_zones": critical,
        "shelters_count": shelters,
        "sos_pings": [{"lat": p["lat"], "lon": p["lon"],
                       "time": datetime.fromtimestamp(p["created_ts"], IST).isoformat(timespec="seconds")}
                      for p in pings],
    }


def client_ip(request: Request):
    """The visitor's address. On Render a proxy makes every request, so the visitor
    is the first X-Forwarded-For entry; a client can fake that header, which only
    lets it dodge a per-address limit that is there to blunt floods, not liars."""
    forwarded = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    return forwarded or (request.client.host if request.client else "unknown")


@app.get("/reports")
def get_reports():
    return {"items": reports.active()}


@app.post("/reports")
def post_report(body: reports.ReportCreate, request: Request):
    return reports.create(get_graph(), body, client_ip(request))


@app.post("/reports/{report_id}/confirm")
def post_confirm(report_id: str, body: reports.ReportConfirm, request: Request):
    return reports.confirm(report_id, body.reporter_id, client_ip(request))


@app.get("/shelters")
def get_shelters():
    return places_file("shelters")


@app.get("/hospitals")
def get_hospitals():
    return places_file("hospitals")


@app.get("/localities")
def get_localities():
    return places_file("localities")
