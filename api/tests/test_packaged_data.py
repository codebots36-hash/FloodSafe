"""Is the data a deploy runs on the same as the data the pipeline built?

A deploy has no pipeline/out, so it serves the copy in api/data/ made by
pipeline/package_api_data.py. If the pipeline is re-run and the copy is not
refreshed, the deploy would quietly serve old roads. This fails first.
"""

import filecmp
import os

import pytest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUILT = os.path.join(os.path.dirname(HERE), "pipeline", "out")
PACKAGED = os.path.join(HERE, "data")


@pytest.mark.parametrize("folder", ["graph", "layers"])
def test_the_packaged_copy_matches_the_build(folder):
    built = os.path.join(BUILT, folder)
    if not os.path.isdir(built):
        pytest.skip("no local build to compare with")
    packaged = os.path.join(PACKAGED, folder)
    names = sorted(os.listdir(built))
    stale = [n for n in names
             if not os.path.exists(os.path.join(packaged, n))
             or not filecmp.cmp(os.path.join(built, n), os.path.join(packaged, n), shallow=False)]
    assert not stale, f"api/data/{folder} is out of date ({stale}); run pipeline/package_api_data.py"


def test_the_packaged_data_has_everything_the_api_loads():
    for name in ("edge_u.npy", "edge_risk.npy", "edge_surveyed.npy", "edge_landslide.npy",
                 "node_lon.npy", "class_codes.json", "graph_meta.json"):
        assert os.path.exists(os.path.join(PACKAGED, "graph", name)), name
    for name in ("road_risk.geojson", "atlas.geojson"):
        assert os.path.exists(os.path.join(PACKAGED, "layers", name)), name


def test_the_stored_gzip_of_each_layer_matches_its_layer():
    import gzip
    for name in ("road_risk.geojson", "atlas.geojson"):
        path = os.path.join(PACKAGED, "layers", name)
        with open(path, "rb") as raw, open(path + ".gz", "rb") as packed:
            assert gzip.decompress(packed.read()) == raw.read(), f"{name}.gz is out of date"
