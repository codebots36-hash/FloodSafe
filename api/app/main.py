"""FloodSafe v2 API.

Serves the endpoints described in contracts/. Implemented so far:
  GET /health   the server is up
  GET /route    a road route between two points (mode=fastest)

Data source: the road graph built by pipeline/build_graph.py from
OpenStreetMap (Geofabrik India central zone, ODbL).

Run from api/: uvicorn app.main:app --host 0.0.0.0 --port 8000
"""

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.graph import get_graph
from app.routing import RoutingError, route

IST = timezone(timedelta(hours=5, minutes=30))

# Until pipeline/build_road_risk.py has run, no edge has a risk class. The
# route still reports its shape honestly and says the scores are missing,
# rather than dressing up an unscored road as a safe one.
RISK_NOT_READY = "Risk scores are not loaded yet, so every segment reads as LOW."


@asynccontextmanager
async def lifespan(_app):
    get_graph()  # pay the load cost at startup, not on the first request
    yield


app = FastAPI(title="FloodSafe v2", lifespan=lifespan)

# The React app runs on another laptop and origin, so allow every origin.
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


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
    if mode == "safest":
        raise RoutingError(
            "not_ready",
            "Safest routing needs the road risk scores, which are built in a later step.",
            501,
        )
    if replay:
        raise RoutingError("not_ready", "Replay mode is not implemented yet.", 501)

    graph = get_graph()
    result = route(graph, parse_point(from_, "from"), parse_point(to, "to"))

    distance_km = round(result["distance_m"] / 1000, 1)
    last = len(result["coordinates"]) - 1

    return {
        "mode": mode,
        "depart": depart or datetime.now(IST).isoformat(timespec="seconds"),
        "replay": None,
        "distance_km": distance_km,
        "duration_min": round(result["duration_s"] / 60),
        "geometry": {"type": "LineString", "coordinates": result["coordinates"]},
        "segments": [
            {
                "start": 0,
                "end": last,
                "risk_class": "LOW",
                "risk_source": "modelled",
                "landslide_prone": False,
                "forecast_rain_mm": None,
                "report_id": None,
            }
        ],
        "summary": {
            "km_by_class": {
                "LOW": distance_km,
                "MODERATE": 0.0,
                "SIGNIFICANT": 0.0,
                "EXTREME": 0.0,
            },
            "extreme_segments": 0,
        },
        "notes": [RISK_NOT_READY],
    }
