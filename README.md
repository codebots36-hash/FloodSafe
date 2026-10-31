# FloodSafe v2

FloodSafe is flood-aware navigation for Uttarakhand, India. It routes people around roads at risk of flash floods and landslides, using real, published hazard, terrain and rainfall data, and gives live rain, official warnings, community reports, shelters and an SOS message for when things go wrong.

## Team

- Shiv: data pipeline and backend API
- Waheed: React app
- Shourya: React app

## How it works

1. **Risk map.** A Flash Flood Potential Index (Smith 2003) is computed for the whole state at 30 m from slope, soil, land cover and forest density. Where the state hazard atlas covers a place, its surveyed flood zones are used instead and marked `surveyed`; everywhere else the class is `modelled`.
2. **Road risk.** Every road segment in OpenStreetMap is sampled about every 30 m and takes the worst class along it, plus a landslide-prone flag.
3. **Routing.** Fastest minimises travel time. Safest minimises travel time multiplied by a risk penalty per segment (see `api/app/routing.py`); the penalties are a modelling choice, not an official rule.
4. **Zones.** 200 watched places turn WATCH or CRITICAL when live rain reaches depths calibrated on 24 monsoons of rainfall against recorded floods.
5. **Replay.** `?replay=YYYY-MM-DD` re-runs rain and zones for a past day, for example 17 June 2013.

## Run it

API (Python 3.11+):

```
cd api
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Tests: `pip install -r requirements-dev.txt`, then `python -m pytest -q` from `api/`.

The API needs the road graph in `pipeline/out/graph/` (or `api/data/graph/`, or the folder named by `GRAPH_DIR`), built by the pipeline below. Endpoints and their exact shapes are in [contracts/](contracts/README.md).

Web app: in `web/`, create `.env.local` containing `VITE_API_URL=` followed by the API's address (no trailing slash), then `npm install` and `npm run dev`. Restart the dev server after changing it.

## Rebuild the data

Run from the repository root, in this order. Raw files go to `pipeline/raw/`, which is not committed.

| Step | Script | Produces |
|---|---|---|
| 1 | `pipeline/download.py osm dem landcover soil basins landslides floods` | raw inputs |
| 2 | `pipeline/build_graph.py` | road graph |
| 3 | `pipeline/build_ffpi.py` | statewide flood index and classes |
| 4 | `pipeline/atlas_render.py`, `atlas_georef.py`, `atlas_classify.py` | surveyed flood zones from the atlas |
| 5 | `pipeline/build_road_risk.py` | per-road risk, landslide flag, map layers |
| 6 | `pipeline/build_places.py` | localities, shelters, hospitals, 200 zones |
| 7 | `pipeline/fetch_era5.py`, then `calibrate_thresholds.py` | rain thresholds |

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
| Landslide validation | NASA Global Landslide Catalog | open NASA data |
| Recorded floods | India Flood Inventory v3, Saharia et al. 2021, Zenodo 10.5281/zenodo.4742142 | CC BY-NC 4.0, non-commercial use |
| Historic rain (calibration, replay) | ERA5 reanalysis via the Open-Meteo archive API | Copernicus licence, CC BY 4.0 |
| Live rain | Open-Meteo forecast API | CC BY 4.0, free for non-commercial use. "Weather data by Open-Meteo.com" |

## What the numbers do and do not say

- **Rain thresholds.** WATCH fires on 15% of quiet monsoon days and CRITICAL on 5%. At those levels WATCH catches about 35% of the 206 recorded flood district-days and CRITICAL about 21%. Rainfall alone cannot see a cloudburst over one valley, and catching more floods means warning on far more quiet days. The full operating curve is in `api/data/thresholds.json`.
- **Per-zone adjustment.** Shifting each zone's depths by hazard class and soil group is a modelling choice, not a fitted result.
- **Atlas accuracy.** The atlas map is embedded at about 100 dpi, so one source pixel is about 400 m on the ground. Its zones are good to a few kilometres.
- **Landslide index.** A transparent index of slope, relief and land cover, with weights that were not tuned. Against the NASA catalogue it separates events from random points inside the state no better than chance (ROC AUC 0.49), and from random road points moderately (0.71). Treat the landslide flag as a hint, not a prediction.
- **Roads outside Uttarakhand** have no risk class and are shown as "no data", never as safe.
