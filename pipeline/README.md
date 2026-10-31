# Pipeline

Scripts that download the raw data and build the files the API serves. Raw downloads go in `pipeline/raw/`, build outputs in `pipeline/out/`; both stay out of git.

| Script | What it does | Inputs | Outputs | Source |
| --- | --- | --- | --- | --- |
| `train_landslide_model.py` | Trains a landslide model (gradient-boosted trees, scikit-learn) on NASA catalogue landslides vs other roads, with 25 km spatially blocked cross-validation, and scores the hand-weighted index on the same held-out points | DEM, slope, land cover, soil, forest, FFPI rasters; road graph; NASA GLC | `out/landslide_model.joblib`, `out/landslide_model_meta.json` | NASA Global Landslide Catalog |
