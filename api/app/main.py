"""FloodSafe v2 API.

Serves the endpoints described in contracts/. Implemented so far:
  GET /health             the server is up
  GET /route              a road route between two points (fastest or safest)
  GET /layers/road-risk   roads rated MODERATE or worse (GeoJSON)
  GET /layers/atlas       surveyed hazard atlas polygons (GeoJSON)
  GET /shelters, /hospitals, /localities   places from OpenStreetMap

Data sources: the road graph built by pipeline/build_graph.py from
OpenStreetMap (Geofabrik India central zone, ODbL), with risk classes from
pipeline/build_road_risk.py; places from pipeline/build_places.py.

Run from api/: uvicorn app.main:app --host 0.0.0.0 --port 8000
"""

import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse

from app.graph import get_graph
from app.routing import MAX_DETOUR, RoutingError, km_by_class, route, safest_weights, segments

IST = timezone(timedelta(hours=5, minutes=30))

API_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LAYER_DIRS = [
    os.path.join(API_DIR, "data", "layers"),  # what a deploy unpacks
    os.path.join(os.path.dirname(API_DIR), "pipeline", "out", "layers"),  # local build
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


app = FastAPI(title="FloodSafe v2", lifespan=lifespan)

# The React app runs on another laptop and origin, so allow every origin.
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
# The road-risk layer is ~11 MB of GeoJSON and ~1 MB gzipped.
app.add_middleware(GZipMiddleware, minimum_size=1000)


@app.exception_handler(RoutingError)
async def routing_error(_request, exc: RoutingError):
    return JSONResponse(status_code=exc.status, content={"error": exc.code, "detail": exc.detail})


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


@app.get("/route")
def get_route(
    from_: str = Query(..., alias="from", description="lat,lon"),
    to: str = Query(..., description="lat,lon"),
    mode: str = Query("fastest", pattern="^(fastest|safest)$"),
    depart: str | None = Query(None, description="ISO 8601; defaults to now"),
    replay: str | None = Query(None, description="YYYY-MM-DD; not implemented yet"),
):
    if replay:
        raise RoutingError("not_ready", "Replay mode is not implemented yet.", 501)

    graph = get_graph()
    if mode == "safest" and not graph.has_risk:
        raise RoutingError("not_ready", "Safest routing needs the road risk scores.", 501)

    start, end = parse_point(from_, "from"), parse_point(to, "to")
    notes = []
    result = route(graph, start, end)
    if mode == "safest":
        fastest_m = result["distance_m"]
        result = route(graph, start, end, safest_weights(graph, "strict"))
        if result["distance_m"] > MAX_DETOUR * fastest_m:
            result = route(graph, start, end, safest_weights(graph, "mild"))
            notes.append(f"Avoiding every risky road meant a route over {MAX_DETOUR} times "
                         "longer, so this one uses milder risk penalties.")

    km, unscored_km = km_by_class(graph, result["edge_ids"])
    segs = segments(graph, result["edge_ids"], result["bounds"])
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
        "depart": depart or datetime.now(IST).isoformat(timespec="seconds"),
        "replay": None,
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
def get_layer(name: str):
    if name not in LAYER_FILES:
        raise RoutingError("not_found", f"No layer called '{name}'.", 404)
    for folder in LAYER_DIRS:
        path = os.path.join(folder, LAYER_FILES[name])
        if os.path.exists(path):
            return FileResponse(path, media_type="application/geo+json",
                                headers={"Cache-Control": "public, max-age=3600"})
    raise RoutingError("not_found", f"The {name} layer has not been built yet.", 404)


def places_file(name):
    path = os.path.join(DATA_DIR, f"{name}.json")
    if not os.path.exists(path):
        raise RoutingError("not_found", f"The {name} list has not been built yet.", 404)
    return FileResponse(path, media_type="application/json",
                        headers={"Cache-Control": "public, max-age=3600"})


@app.get("/shelters")
def get_shelters():
    return places_file("shelters")


@app.get("/hospitals")
def get_hospitals():
    return places_file("hospitals")


@app.get("/localities")
def get_localities():
    return places_file("localities")
