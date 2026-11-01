"""Tune Safest's landslide penalty on some landslides, judge it on others.

Inputs
    as backtest_routes.py
Outputs
    pipeline/out/tune_penalties.json

Why: backtest_routes.py found that the risk layer finds held-out landslides
(67% near a flagged road vs 23% of random road points) but Safest does not
avoid them more than any other stretch. One suspect is the landslide penalty,
x2, against flood-class penalties of up to x1000.

How
- The backtest's own trips, controls and per-fold landslide flags, with Safest
  judged under each candidate penalty table on the same trips.
- The split is fixed before any run: landslides in TEST_FOLDS (the last two of
  the five spatial folds train_landslide_model.py makes) are never used to
  choose. The penalty with the largest avoidance gap (landslides minus controls,
  where a way around existed) on the other folds wins; a tie goes to the
  smaller penalty. Only the winner and today's x2 are then scored on TEST_FOLDS.
- Changing the penalty after seeing the test result would spend the hold-out.
- out/tune_penalties.json was made while the fallback to mild penalties
  measured distance; it now measures time, so a rerun will differ.

Usage (after build_road_risk.py):
    python pipeline/tune_penalties.py
"""

import json
import os
import time

import backtest_routes as bt
from app.graph import RoadGraph
from app.routing import PENALTIES

LANDSLIDE = [2, 4, 8, 16, 32]   # x2 is today's
TEST_FOLDS = (3, 4)
RESULT_PATH = os.path.join(bt.OUT, "tune_penalties.json")


def table(m):
    return {level: dict(p, landslide=m) for level, p in PENALTIES.items()}


def gap(summary):
    w = summary["where_a_way_around_existed"]
    return w["landslides_avoided"] - w["controls_avoided"]


def brief(summary):
    w = summary["where_a_way_around_existed"]
    return {"landslides": summary["landslides"], "encounters": summary["encounters"],
            "median_time_ratio": summary["median_time_ratio"],
            "avoided_1km": summary["1000m"],
            "where_a_way_around_existed": {k: w[k] for k in
                ("landslides_avoided", "controls_avoided", "gap_95ci_paired", "paired_landslides")}}


def main():
    t0 = time.time()
    X, y, events_xy, folds = bt.fold_events()
    graph = RoadGraph(bt.GRAPH)
    names = {f"x{m}": m for m in LANDSLIDE}
    encounters, n_towns, _ = bt.route_backtest(graph, X, y, events_xy, folds, bt.road_points(),
                                                {n: table(m) for n, m in names.items()})
    tune = [e for e in encounters if e["fold"] not in TEST_FOLDS]
    test = [e for e in encounters if e["fold"] in TEST_FOLDS]

    tuning = {n: bt.summarise(bt.as_candidate(tune, n)) for n in names}
    chosen = max(names, key=lambda n: (round(gap(tuning[n]), 3), -names[n]))
    bt.log(f"chosen on the tuning folds: landslide {chosen}", t0)

    result = {
        "question": "Does a stronger landslide penalty make Safest avoid held-out landslides "
                    "more often than random stretches of the same routes?",
        "split": f"tuned on landslides in folds {[f + 1 for f in range(len(folds)) if f not in TEST_FOLDS]}, "
                 f"tested on folds {[f + 1 for f in TEST_FOLDS]} (of 5), fixed before any run",
        "routes": f"Fastest and Safest (no rain) between every pair of {n_towns} towns",
        "rule": "largest (landslides avoided - controls avoided) where a way around existed, "
                "on the tuning folds; a tie goes to the smaller penalty",
        "tuning": {n: brief(s) for n, s in tuning.items()},
        "chosen": chosen,
        "test": {n: brief(bt.summarise(bt.as_candidate(test, n))) for n in dict.fromkeys(["x2", chosen])},
        "all_folds_x2": brief(bt.summarise(bt.as_candidate(encounters, "x2"))),
    }
    with open(RESULT_PATH, "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=2)
    bt.log(f"wrote {RESULT_PATH}", t0)


if __name__ == "__main__":
    main()
