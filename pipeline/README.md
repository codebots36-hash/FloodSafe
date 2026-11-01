# Pipeline

Scripts that download the raw data and build the files the API serves. Raw downloads go in `pipeline/raw/`, build outputs in `pipeline/out/`; both stay out of git. Run everything from the repository root, in the order below.

| Step | Script | What it does | Outputs |
| --- | --- | --- | --- |
| 1 | `download.py <source> ...` | Downloads the raw data (`osm dem landcover soil basins landslides floods`), resuming where it left off; `download.py status` shows what is done | `raw/<source>/` |
| 2 | `build_graph.py` | Builds the road graph from OpenStreetMap | `out/graph/` |
| 3 | `build_ffpi.py` | Computes the Flash Flood Potential Index (Smith 2003) for the whole state at 30 m from slope, soil, land cover and forest density | flood index and classes |
| 4 | `atlas_render.py`, `atlas_georef.py`, `atlas_classify.py` | Renders the state hazard atlas map, places it on the ground and reads its flood zones | surveyed flood zones |
| 5 | `train_landslide_model.py` | Trains a landslide model (gradient-boosted trees, scikit-learn) on NASA catalogue landslides vs other roads, with 25 km spatially blocked cross-validation, and scores the hand-weighted index on the same held-out points | `out/landslide_model.joblib`, `out/landslide_model_meta.json` |
| 6 | `build_road_risk.py` | Gives every road segment its risk class and landslide flag, and builds the map layers | `out/graph/`, `out/layers/` |
| 7 | `build_places.py` | Localities, shelters, hospitals and the 200 watched zones | places files |
| 8 | `fetch_era5.py`, then `calibrate_thresholds.py` | Fetches 24 monsoons of rain and calibrates the WATCH and CRITICAL depths against recorded floods | rain thresholds |
| 9 | `backtest_routes.py` | Tests whether Safest avoids NASA catalogue landslides it never trained on (5 spatial folds, landslide flags refitted per fold) more often than random stretches of the same Fastest routes, plus the Dharali 2025 and Kedarnath 2013 replays through the API | `out/backtest_routes.json` |
| 10 | `package_api_data.py` | Copies the graph and map layers into `api/data/` so a deployed API can start; commit the result | `api/data/graph/`, `api/data/layers/` |

`relay_rainfall.py` is not a build step: it fetches live rain and sends it to a running API whose host is over Open-Meteo's free quota (see the main [README](../README.md#run-it)).

Every source, its licence and what the numbers can and cannot claim are listed in the main [README](../README.md#data-sources-and-licences).
