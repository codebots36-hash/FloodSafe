# FloodSafe

FloodSafe is flood-aware navigation for Uttarakhand, India. It routes people around roads at risk of flash floods and landslides, using real, published hazard, terrain and rainfall data, and gives live rain, official warnings, community reports, shelters and an SOS message for when things go wrong.

- Web app: https://floodsafe-web.onrender.com
- API: https://floodsafe-api-4nzc.onrender.com (free tier; the first request after a quiet spell takes a minute to wake it)

## From prototype to project

FloodSafe began as a small prototype that showed flood-aware routing could work: a map, a risk score per road and a route that steered around the worst of it. This repository grows that idea into a full project:

- risk for every road in the state, built from published hazard, terrain, soil and land-cover data rather than hand-set values
- a landslide model trained and tested on recorded landslides
- routing that judges each road on the rain forecast for the hour you would reach it
- live rain, rain-triggered zone alerts and official NDMA warnings
- community reports, SOS messages and a responder dashboard
- an installable app that keeps working without signal
- replay of past disasters (Kedarnath 2013, Dharali 2025) on the rain that actually fell
- tests, published API contracts and a reproducible data pipeline

## Project layout

| Folder | What is in it |
|---|---|
| `api/` | FastAPI backend: routing, rain, zones, reports, warnings, push alerts, SOS |
| `web/` | React + MapLibre app, installable as a PWA ([web/README.md](web/README.md)) |
| `pipeline/` | scripts that download the raw data and build everything the API serves ([pipeline/README.md](pipeline/README.md)) |
| `contracts/` | one example response per endpoint, with every field explained ([contracts/README.md](contracts/README.md)) |

## Team

- Shivang: data pipeline and backend API
- Waheed: React app
- Shourya: React app

## Branches

`main` is what is deployed. Work happens on the branches below and reaches `main` through pull requests.

| Branch | Owner | What it is for |
|---|---|---|
| `main` | everyone | the working app; the web app and API deploy from it |
| `shivang` | Shivang | data pipeline and backend API: road graph, flood and landslide risk, Safest routing, rain, zones, reports, SACHET warnings, push alerts, SOS and the responder API |
| `waheed` | Waheed | the map page: route search and compare, risk layer, precise location, shelters, hospitals, report pins and SOS |
| `shourya` | Shourya | the rest of the web app and its deploy: rain card, zones list, API settings and the static site |
| `web` | Waheed, Shourya | the first shared web app branch (risk map, real routes, compare, live layers, zones panel), merged in PR #3; kept for reference |
| `shiv/time-dependent-routing` | Shivang | one feature: time-dependent A* so Safest judges each road on the rain when the route reaches it, merged in PR #12 |

## How it works

1. **Risk map.** A Flash Flood Potential Index (Smith 2003) is computed for the whole state at 30 m from slope, soil, land cover and forest density. Where the state hazard atlas covers a place, its surveyed flood zones are used instead and marked `surveyed`; everywhere else the class is `modelled`.
2. **Road risk.** Every road segment in OpenStreetMap is sampled about every 30 m and takes the worst class along it, plus a landslide-prone flag from a machine-learning model trained on recorded landslides (the top 10% of road length).
3. **Routing.** Fastest minimises travel time. Safest minimises travel time multiplied by a risk penalty per segment (see `api/app/routing.py`): a landslide-prone road costs 32x more, a value chosen on held-out landslides (`pipeline/tune_penalties.py`); the flood-class penalties are a modelling choice, not an official rule. If that makes the trip take more than 3x the Fastest time, milder penalties are used and the app says so.
4. **Forecast-timed routing.** Safest judges each road on the rain at the hour you would reach it (`depart`): a risky road costs 2x more where its nearest zone is WATCH then, 4x at CRITICAL. The search is a time-dependent A* (`route_timed` in `api/app/routing.py`, compiled with Numba): it carries the clock along each route it tries, so a detour is judged on the hours it actually reaches each valley. Each stretch of the answer carries that hour's rain.
5. **Zones.** 200 watched places turn WATCH or CRITICAL when live rain reaches depths calibrated on 24 monsoons of rainfall against recorded floods. A phone can subscribe to a push alert when its zone turns CRITICAL.
6. **Reports.** Community reports are verified by two other devices or by 2.5 mm of rain around them in the 72 hours before. Safest makes roads near an unverified report cost 20x more and all but blocks them near a verified one.
7. **Replay.** `?replay=YYYY-MM-DD` re-runs rain, zones and routing on what actually fell on a past day. The rain for 16 and 17 June 2013 (Kedarnath) and 5 August 2025 (Dharali) is kept in `api/data/weather_history/`, so those replay without a network.
8. **Official warnings** come from NDMA SACHET as issued, apart from FloodSafe's own status. **SOS** texts family a map link; sharing it with the responder dashboard is opt-in. The app installs as a PWA and keeps the last route, shelters and viewed map tiles for use without signal.

## Run it

API (Python 3.11+):

```
cd api
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Tests: `pip install -r requirements-dev.txt`, then `python -m pytest -q` from `api/`.

The API needs the road graph in `pipeline/out/graph/` (or `api/data/graph/`, or the folder named by `GRAPH_DIR`), built by the pipeline below. Endpoints and their exact shapes are in [contracts/](contracts/README.md).

Rain on a shared host: Open-Meteo's free quota is per address and a shared host is usually over it (Render got HTTP 429), so live rain can be fetched elsewhere and sent to the API. Set `RAINFALL_RELAY_SECRET` on the API, then run `RAINFALL_RELAY_SECRET=... python pipeline/relay_rainfall.py <api address> --every 3600` from a laptop, or let `.github/workflows/rainfall-relay.yml` do it hourly. The API marks those readings `relay` and the time they were taken. The relay is off unless the secret is set.

Optional settings on the API (see `api/.env.example`):

- `DATABASE_URL`: Postgres (e.g. Neon) for reports, push subscriptions and SOS pings. Without it they go in a SQLite file, which a free host's disk loses on every restart. Tests run on SQLite, or on an empty Postgres named by `TEST_DATABASE_URL`.
- `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`, `VAPID_SUBJECT`: push alerts. `python -m app.push` from `api/` prints a new pair.
- `TEXTBEE_API_KEY`, `TEXTBEE_DEVICE_ID`: SOS texts sent by the server. Without them the app opens the phone's SMS app instead.

Web app: in `web/`, create `.env.local` containing `VITE_API_URL=` followed by the API's address (no trailing slash), then `npm install` and `npm run dev`. Restart the dev server after changing it.

## Rebuild the data

Run from the repository root, in this order. Raw files go to `pipeline/raw/`, which is not committed.

| Step | Script | Produces |
|---|---|---|
| 1 | `pipeline/download.py osm dem landcover soil basins landslides floods` | raw inputs |
| 2 | `pipeline/build_graph.py` | road graph |
| 3 | `pipeline/build_ffpi.py` | statewide flood index and classes |
| 4 | `pipeline/atlas_render.py`, `atlas_georef.py`, `atlas_classify.py` | surveyed flood zones from the atlas |
| 5 | `pipeline/train_landslide_model.py` | landslide model and its held-out skill (needs scikit-learn) |
| 6 | `pipeline/build_road_risk.py` | per-road risk, landslide flag, map layers |
| 7 | `pipeline/build_places.py` | localities, shelters, hospitals, 200 zones |
| 8 | `pipeline/fetch_era5.py`, then `calibrate_thresholds.py` | rain thresholds |
| 9 | `pipeline/backtest_routes.py` | does Safest avoid real landslides? (`out/backtest_routes.json`) |
| 10 | `pipeline/tune_penalties.py` | the landslide penalty, chosen on some landslides and tested on others (`out/tune_penalties.json`) |

## Data sources and licences

Everything is real, published data. Nothing is simulated.

| Data | Source | Licence |
|---|---|---|
| Roads, places, shelters, hospitals, district boundaries | OpenStreetMap, Geofabrik extract | ODbL, © OpenStreetMap contributors |
| Terrain | Copernicus DEM GLO-30 | Copernicus data licence, free with attribution |
| Land cover | ESA WorldCover 2021 v200 | CC BY 4.0 |
| Soil sand and clay | SoilGrids v2.0, ISRIC | CC BY 4.0 |
| Catchment area | HydroBASINS Asia level 8, HydroSHEDS | free use with attribution |
| Surveyed flood zones | State hazard atlas, section 3.4.3 "Flash flood hazard zones", Figure 3.72 (Map Ref 050000000_H_FLFLZ0100Y) | **publisher and licence to be confirmed**: the PDF carries neither |
| Landslide model training and validation | NASA Global Landslide Catalog | open NASA data |
| Recorded floods | India Flood Inventory v3, Saharia et al. 2021, Zenodo 10.5281/zenodo.4742142 | CC BY-NC 4.0, non-commercial use |
| Historic rain (calibration, replay) | ERA5 reanalysis via the Open-Meteo archive API | Copernicus licence, CC BY 4.0 |
| Live rain | Open-Meteo forecast API | CC BY 4.0, free for non-commercial use. "Weather data by Open-Meteo.com" |
| Official warnings | NDMA SACHET CAP feed (IMD, CWC, Uttarakhand SDMA) | public domain, shown as issued |

## What the numbers do and do not say

- **Rain thresholds.** WATCH fires on 15% of quiet monsoon days and CRITICAL on 5%. At those levels WATCH catches about 35% of the 206 recorded flood district-days and CRITICAL about 21%. Rainfall alone cannot see a cloudburst over one valley, and catching more floods means warning on far more quiet days. The full operating curve is in `api/data/thresholds.json`.
- **Per-zone adjustment.** Shifting each zone's depths by hazard class and soil group is a modelling choice, not a fitted result.
- **Atlas accuracy.** The atlas map is embedded at about 100 dpi, so one source pixel is about 400 m on the ground. Its zones are good to a few kilometres.
- **Landslide model.** Gradient-boosted trees on 11 terrain, soil and land-cover features, trained on the 109 NASA catalogue landslides in Uttarakhand placed within 10 km (each moved to the nearest road within that accuracy) against 1,089 road points at least 5 km from any. On held-out 25 km blocks it ranks landslides above other roads with ROC AUC 0.84, against 0.74 for the old hand-weighted index on the same points (better in 4 of 5 folds). The catalogue is collected from news and most events are placed only to 5 or 10 km, so treat the flag as a strong hint, not a prediction. Details: `pipeline/out/landslide_model_meta.json`. If a retrained model ever stops beating the index, the build falls back to the index on its own.
- **Does Safest avoid real landslides? Now somewhat, where a way around exists; not yet proven.** `pipeline/backtest_routes.py` routes Fastest and Safest (no rain) between every pair of 82 towns: the 83 in `localities.json`, less one more than 2 km from any road. For each landslide it uses flags from a model refitted without that landslide's 25 km fold. It counts 38,588 times a Fastest route passes within 1 km of one of 81 held-out landslides (more than 5 km from either end), and compares each with a random stretch of the same route at least 5 km from any catalogued slide.
  - The risk layer does find the danger: 67% of the 109 held-out landslides lie within 1 km of a road flagged landslide-prone, against 23% of random road points; 42% are near an EXTREME flood-class road, against 7%.
  - With the first penalties (landslide x2, mild fallback past 3x the distance) the route choice did not turn that into avoidance: where a way around existed, Safest avoided 64% of landslides and 74% of controls (paired gap -19 to -1 points), at a median 1.74x the Fastest time. The x2 was drowned by flood-class penalties of up to x1000.
  - `pipeline/tune_penalties.py` tried landslide penalties of x2 to x32 on the landslides of folds 1-3 and picked x32 by a rule fixed beforehand (largest avoidance gap). Tested once on folds 4-5 (28 landslides it never used), the gap where a way around existed moved from -15 to +9 points (95% interval -7 to +24), with the median trip from 1.74x to 2.23x the Fastest time. The interval still includes zero.
  - x32 sent some trips on very long detours (Kedarnath 288 min against Fastest's 70) without tripping the fallback, which then measured distance. The fallback now measures time, and the mild penalties keep x2 for landslides so the fallback is really milder. That change came after the hold-out was spent, so it has no held-out test of its own.
  - As deployed, on all 81 landslides: where a way around existed (69% of landslides, 92% of controls), Safest avoids 75% of landslides and 70% of controls (paired gap -3 to +13 points, 95% bootstrap over landslides). Over all trips it still avoids fewer landslides than controls (51% against 66%), because many slides sit on the only road up a valley. Safest takes a median 1.89x the Fastest time.
  - Dharali (5 Aug 2025, Uttarkashi to Gangotri) has no way around: both routes pass Dharali, and Safest says "33.1 km of this route is rated EXTREME. No way around it was found." Kedarnath (16 Jun 2013, Rudraprayag to Sonprayag): strict penalties would take over 3x the Fastest time, so Safest uses the milder ones and says so; it drops all 7.6 km of EXTREME road for 14 km and 30 minutes more, but must still reach Sonprayag. Gaurikund is 3.3 km from the nearest road in the graph and cannot be routed to.
  - Limits: catalogue points are placed only to 1-10 km and moved to the nearest road, so the matched road may not be the one that failed; no rain is used. The landslide penalty is now tuned (above); the folds 4-5 hold-out is spent, so any further tuning needs fresh landslides to mean anything.
- **Forecast-timed routing** reads each road's rain from the nearest of the 200 zones within 20 km, at the hour the route being searched reaches it. The search keeps only the cheapest way into each junction, so a dearer way that arrives earlier, before the rain, is not explored further. Waiting for rain to pass is not considered. Replay uses the rain that fell (ERA5), not the forecast issued that day.
- **Roads outside Uttarakhand** have no risk class and are shown as "no data", never as safe.
