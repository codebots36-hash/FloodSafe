"""Calibrate the rainfall depths that turn a zone WATCH or CRITICAL.

Inputs
    pipeline/cache/era5/<district>_<year>.json   hourly rain, 13 districts x 24
                                                 monsoons (fetch_era5.py)
    pipeline/out/era5_points.csv                 the district points
    pipeline/raw/floods/India_Flood_Inventory_v3.csv
                                                 Saharia et al. 2021, CC BY-NC 4.0
Output
    api/data/thresholds.json   base WATCH and CRITICAL depths for 1 h, 3 h and
                               24 h, the hazard and soil adjustments, and the
                               measured skill

Method. For every district-day of the 2000-2023 monsoons, the worst moment of
the day is found (the hour where trailing 24 h rain peaks) and the trailing
1 h, 3 h, 24 h and antecedent 48 h depths at that hour are recorded. A day is
positive if the flood inventory lists a flood in that district on that date.
The threshold for each window is then the depth exceeded on only a target
share of non-flood days: 15% for WATCH, 5% for CRITICAL. This fixes the false
alarm rate first, which is the honest way round when floods are rare -- 1 day
in 250 here -- because a threshold chosen to catch most floods would warn on
most of the monsoon.

Thresholds are NOT tuned to raise the hit rate, and the measured skill is
written out whatever it says. Rainfall alone explains only part of flooding:
the catchment, the channel and the soil's wetness all matter, and a district
average cannot see a cloudburst over one valley.

Usage:
    python pipeline/calibrate_thresholds.py
"""

import csv
import datetime
import json
import os
import re

import numpy as np
import pandas as pd
from scipy.stats import rankdata

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(REPO, "pipeline", "cache", "era5")
POINTS_CSV = os.path.join(REPO, "pipeline", "out", "era5_points.csv")
INVENTORY = os.path.join(REPO, "pipeline", "raw", "floods", "India_Flood_Inventory_v3.csv")
OUT_FILE = os.path.join(REPO, "api", "data", "thresholds.json")

YEARS = range(2000, 2024)
SEASON = (6, 9)  # labelled days: June to September
WINDOWS = {"h1": 1, "h3": 3, "h24": 24}
ANTECEDENT_H = 48

# Share of non-flood district-days a level is allowed to fire on. Fixing this
# rather than the hit rate is what keeps a warning meaningful.
TARGET_POFD = {"watch": 0.15, "critical": 0.05}

# A MODELLING CHOICE, not calibrated: 13 district rain series and 160-odd
# flood days cannot fit a threshold per zone, so the calibrated statewide
# depth is shifted by what the terrain says. Steeper, flashier ground and
# soils that shed water flood on less rain. Following flash flood guidance
# practice, where the same rain means different things to different basins.
HAZARD_FACTOR = {"LOW": 1.1, "MODERATE": 1.0, "SIGNIFICANT": 0.9, "EXTREME": 0.8}
SOIL_FACTOR = {"A": 1.15, "B": 1.05, "C": 0.95, "D": 0.85}

# The inventory writes district names loosely. Everything it says is matched
# or reported; nothing is dropped in silence.
ALIASES = {
    "garhwal": "Pauri Garhwal",
    "paurigarhwal": "Pauri Garhwal",
    "pauri": "Pauri Garhwal",
    "udamsinghnagar": "Udham Singh Nagar",
    "udhamsinghnagar": "Udham Singh Nagar",
    "ussnagar": "Udham Singh Nagar",
    "rudraprayag": "Rudraprayag",
    "uttarkashi": "Uttarkashi",
    "uttarkashikashi": "Uttarkashi",
    "tehri": "Tehri Garhwal",
    "tehrigarhwal": "Tehri Garhwal",
    "dehradun": "Dehradun",
    "haridwar": "Haridwar",
    "hardwar": "Haridwar",
    "nainital": "Nainital",
    "almora": "Almora",
    "bageshwar": "Bageshwar",
    "champawat": "Champawat",
    "chamoli": "Chamoli",
    "pithoragarh": "Pithoragarh",
}


def log(msg):
    print(msg, flush=True)


def plain(text):
    return re.sub(r"[^a-z]", "", str(text).lower())


def districts():
    with open(POINTS_CSV, newline="", encoding="utf-8") as fh:
        return [row["district"] for row in csv.DictReader(fh)]


def cache_path(district, year):
    safe = "".join(c if c.isalnum() else "_" for c in district)
    return os.path.join(CACHE, f"{safe}_{year}.json")


# ---------------------------------------------------------------------------
# Rainfall features
# ---------------------------------------------------------------------------

def trailing(values, hours):
    """Rain over the `hours` ending at each index."""
    cum = np.concatenate([[0.0], np.cumsum(values)])
    start = np.maximum(np.arange(len(values)) + 1 - hours, 0)
    return cum[1:] - cum[start]


def day_features(district, year):
    """One row per labelled day: the depths at the day's worst hour."""
    with open(cache_path(district, year), encoding="utf-8") as fh:
        hourly = json.load(fh)["hourly"]
    values = np.array([0.0 if v is None else v for v in hourly["precipitation"]])
    dates = np.array([datetime.date.fromisoformat(t[:10]) for t in hourly["time"]])

    bands = {name: trailing(values, h) for name, h in WINDOWS.items()}
    ante = trailing(values, ANTECEDENT_H)

    rows = []
    for day in sorted(set(dates)):
        if not SEASON[0] <= day.month <= SEASON[1]:
            continue  # May only provides the antecedent rain for early June
        hours = np.flatnonzero(dates == day)
        # Each window peaks at its own hour: live, a warning fires the moment
        # any one of them crosses, so the day's feature is each window's worst.
        peak24 = hours[int(np.argmax(bands["h24"][hours]))]
        before = peak24 - WINDOWS["h24"]
        rows.append({
            "district": district, "date": day,
            **{name: float(band[hours].max()) for name, band in bands.items()},
            "antecedent_48h": float(ante[before]) if before >= 0 else None,
        })
    return rows


def rainfall_table():
    rows = []
    for district in districts():
        for year in YEARS:
            if os.path.exists(cache_path(district, year)):
                rows.extend(day_features(district, year))
    table = pd.DataFrame(rows)
    log(f"rainfall: {len(table):,} district-days from {table['district'].nunique()} districts, "
        f"{table['date'].min()} to {table['date'].max()}")
    return table


# ---------------------------------------------------------------------------
# Flood days
# ---------------------------------------------------------------------------

def match_districts(text, known):
    """District names in one inventory cell -> our names, plus what did not match."""
    found, unknown = set(), set()
    for token in str(text).split(","):
        if not plain(token):
            continue
        key = plain(token)
        if key in ALIASES:
            found.add(ALIASES[key])
            continue
        hit = next((d for d in known if plain(d) in key or key in plain(d)), None)
        (found.add(hit) if hit else unknown.add(token.strip()))
    return found, unknown


def flood_days(known):
    """{(district, date)} from the inventory, and the events behind them."""
    data = pd.read_csv(INVENTORY, low_memory=False)
    uk = data[data["State"].fillna("").str.contains("Uttarakhand|Uttaranchal", case=False)]
    start = pd.to_datetime(uk["Start Date"], format="%d-%m-%Y %H:%M", errors="coerce")
    uk = uk.assign(start=start)
    uk = uk[uk["start"].dt.year.between(min(YEARS), max(YEARS))
            & uk["start"].dt.month.between(*SEASON)]

    positives, unknown, used = set(), set(), 0
    for _, row in uk.iterrows():
        hit, miss = match_districts(row["Districts"], known)
        unknown |= miss
        if not hit:
            continue
        used += 1
        days = int(row["Duration(Days)"]) if row["Duration(Days)"] == row["Duration(Days)"] else 1
        for offset in range(max(1, min(days, 14))):
            date = (row["start"] + pd.Timedelta(days=offset)).date()
            if SEASON[0] <= date.month <= SEASON[1]:
                positives |= {(d, date) for d in hit}

    log(f"floods: {used} of {len(uk)} monsoon events name a district we have rain for, "
        f"giving {len(positives):,} district-days")
    if unknown:
        log(f"  district names not matched (other states, or too garbled): {sorted(unknown)[:12]}")
    return positives


# ---------------------------------------------------------------------------
# Thresholds and skill
# ---------------------------------------------------------------------------

def auc(positive, negative):
    """ROC AUC by the rank-sum (Mann-Whitney) formula, ties averaged."""
    ranks = rankdata(np.concatenate([positive, negative]))
    n_pos, n_neg = len(positive), len(negative)
    return float((ranks[:n_pos].sum() - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg))


def skill(fired, flooded):
    """Hits, misses, false alarms and the usual forecast verification scores."""
    hits = int(np.sum(fired & flooded))
    misses = int(np.sum(~fired & flooded))
    false_alarms = int(np.sum(fired & ~flooded))
    correct = int(np.sum(~fired & ~flooded))
    pod = hits / max(hits + misses, 1)
    pofd = false_alarms / max(false_alarms + correct, 1)
    return {
        "hits": hits, "misses": misses, "false_alarms": false_alarms,
        "correct_negatives": correct,
        "POD": round(pod, 3),                                   # floods caught
        "POFD": round(pofd, 4),                                 # quiet days warned on
        "FAR": round(false_alarms / max(hits + false_alarms, 1), 3),  # warnings that were wrong
        "TSS": round(pod - pofd, 3),                            # skill over chance
    }


def fired_at(table, depths):
    """A warning fires when any window reaches its depth."""
    out = np.zeros(len(table), dtype=bool)
    for name, depth in depths.items():
        out |= table[name].to_numpy() >= depth
    return out


def fit_level(table, flooded, target):
    """Depths whose combination fires on `target` of quiet days.

    All three windows are set at the same quantile of their quiet-day
    distribution, and that one quantile is found by bisection. Setting each
    window at the target separately would overshoot, because a day only has
    to cross one of them to fire.
    """
    quiet = table[~flooded]
    low, high = 1 - target, 1 - target / len(WINDOWS) / 2
    for _ in range(40):
        middle = (low + high) / 2
        depths = {n: round(float(np.quantile(quiet[n], middle)), 1) for n in WINDOWS}
        if fired_at(quiet, depths).mean() > target:
            low = middle
        else:
            high = middle
    depths = {n: round(float(np.quantile(quiet[n], high)), 1) for n in WINDOWS}
    return depths, fired_at(table, depths)


def calibrate(table, positives):
    flooded = np.array([(d, t) in positives for d, t in zip(table["district"], table["date"])])
    log(f"labels: {int(flooded.sum()):,} flood district-days, {int((~flooded).sum()):,} without, "
        f"1 day in {len(flooded) / max(flooded.sum(), 1):.0f}")

    aucs = {name: round(auc(table[name][flooded], table[name][~flooded]), 3)
            for name in list(WINDOWS) + ["antecedent_48h"]
            if table[name].notna().all() or name != "antecedent_48h"}
    ante = table["antecedent_48h"]
    valid = ante.notna()
    aucs["antecedent_48h"] = round(auc(ante[valid & flooded], ante[valid & ~flooded]), 3)
    log(f"how well each window separates flood days (ROC AUC, 0.5 = no skill): {aucs}")

    levels = {}
    for level, target in TARGET_POFD.items():
        depths, fired = fit_level(table, flooded, target)
        levels[level] = {"mm": depths, "target_POFD": target, **skill(fired, flooded)}
        log(f"{level.upper():<8} {depths}  POD {levels[level]['POD']} "
            f"POFD {levels[level]['POFD']} TSS {levels[level]['TSS']}")

    # What the other false-alarm budgets would have bought, so the two chosen
    # points can be seen for what they are rather than taken on trust.
    curve = []
    for target in (0.02, 0.05, 0.1, 0.15, 0.2, 0.3):
        depths, fired = fit_level(table, flooded, target)
        curve.append({"target_POFD": target, "mm": depths, **skill(fired, flooded)})
    return levels, aucs, flooded, curve


def main():
    table = rainfall_table()
    positives = flood_days(districts())
    levels, aucs, flooded, curve = calibrate(table, positives)

    if levels["critical"]["mm"]["h1"] <= levels["watch"]["mm"]["h1"]:
        raise SystemExit("CRITICAL came out no higher than WATCH; check the calibration")

    out = {
        "built": datetime.date.today().isoformat(),
        "basis": ("Rain depths that only a set share of quiet monsoon days reach: "
                  f"{TARGET_POFD['watch']:.0%} for WATCH and {TARGET_POFD['critical']:.0%} for "
                  "CRITICAL, measured on 24 monsoons of ERA5 rainfall against the floods the "
                  "India Flood Inventory records for each district. Each zone's own depths are "
                  "then shifted by its hazard class and soil group."),
        "sources": {
            "rainfall": "ERA5 hourly precipitation via the Open-Meteo archive, 13 districts, "
                        f"{min(YEARS)}-{max(YEARS)} monsoons",
            "floods": "India Flood Inventory v3 (Saharia et al. 2021), CC BY-NC 4.0",
        },
        "labels": {"flood_district_days": int(flooded.sum()),
                   "quiet_district_days": int((~flooded).sum())},
        "roc_auc": aucs,
        "levels": levels,
        "operating_curve": curve,
        "hazard_factor": HAZARD_FACTOR,
        "soil_factor": SOIL_FACTOR,
        "adjustment_note": ("hazard_factor and soil_factor are a modelling choice following "
                            "flash flood guidance practice, not a fitted result: the data cannot "
                            "support a separate threshold per zone."),
    }
    os.makedirs(os.path.dirname(OUT_FILE), exist_ok=True)
    with open(OUT_FILE, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=1)
    log(f"\nwrote {os.path.relpath(OUT_FILE, REPO)}")


if __name__ == "__main__":
    main()
