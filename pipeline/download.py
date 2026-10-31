"""Download the raw data FloodSafe is built from, resuming where it left off.

Usage:
    python pipeline/download.py <source> [<source> ...]
    python pipeline/download.py status

Each source is saved under pipeline/raw/<source>/. A file is skipped when its
size already matches the server's Content-Length. Downloads go to a .part file
that is resumed on the next run and renamed when complete. Every file is tried
3 times. If a source fails, the URL and error are printed and nothing is
substituted. A finished source gets a .complete marker, which "status" reads.

Sources (Uttarakhand bounding box: lon 77.5 to 81.1, lat 28.7 to 31.5):
  osm         OpenStreetMap northern India extract, MD5-checked (Geofabrik)
              https://download.geofabrik.de/asia/india/northern-zone-latest.osm.pbf
  dem         Copernicus DEM GLO-30, 1-degree tiles N28-N31, E077-E081
              https://copernicus-dem-30m.s3.amazonaws.com/
  landcover   ESA WorldCover 2021 v200, 3-degree tiles
              https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/
  soil        SoilGrids v2.0 sand and clay mean, 0-5 cm and 5-15 cm, 1 km
              aggregated, cut to the bounding box in EPSG:4326 (ISRIC)
              https://files.isric.org/soilgrids/latest/data_aggregated/1000m/
  basins      HydroBASINS Asia level 8 (HydroSHEDS)
              https://data.hydrosheds.org/file/HydroBASINS/standard/hybas_as_lev08_v1c.zip
  landslides  NASA Global Landslide Catalog
              https://data.nasa.gov/api/views/dd9e-wu2v/rows.csv?accessType=DOWNLOAD
  floods      India Flood Inventory-Impacts (IFI-Impacts), every file in
              Zenodo record 10.5281/zenodo.4742142
              https://zenodo.org/api/records/4742142
"""

import hashlib
import subprocess
import sys
import time
import zipfile
from pathlib import Path

import requests

RAW = Path(__file__).resolve().parent / "raw"
BBOX = ("77.5", "28.7", "81.1", "31.5")  # lon min, lat min, lon max, lat max
TRIES = 3


def fetch(url, dest, skip_404=False):
    """Download url to dest, resuming dest.part. Returns True when dest is complete
    (or, with skip_404, when the server has no such file)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    for attempt in range(1, TRIES + 1):
        try:
            head = requests.head(url, allow_redirects=True, timeout=30)
            if head.status_code == 404 and skip_404:
                print(f"  no file at {url} (404), skipping")
                return True
            head.raise_for_status()
            total = int(head.headers.get("Content-Length", 0))
            if dest.exists() and (total == 0 or dest.stat().st_size == total):
                print(f"  {dest.name}: already complete")
                return True

            have = part.stat().st_size if part.exists() else 0
            if total and have > total:  # stale .part from an older file
                part.unlink()
                have = 0
            if not (total and have == total):
                headers = {"Range": f"bytes={have}-"} if have else {}
                with requests.get(url, headers=headers, stream=True, timeout=60) as r:
                    r.raise_for_status()
                    if r.status_code != 206:  # server ignored the Range: start over
                        have = 0
                    next_pct = 5
                    with open(part, "ab" if have else "wb") as f:
                        for chunk in r.iter_content(1 << 20):
                            f.write(chunk)
                            have += len(chunk)
                            while total and have * 100 >= next_pct * total and next_pct <= 100:
                                print(f"  {dest.name}: {next_pct}% of {total / 1e6:.0f} MB")
                                next_pct += 5
            if total and have != total:
                raise IOError(f"got {have} of {total} bytes")
            part.replace(dest)
            print(f"  {dest.name}: done")
            return True
        except Exception as e:
            print(f"  attempt {attempt}/{TRIES} failed for {url}\n    {e}")
            if attempt < TRIES:
                time.sleep(10 * attempt)
    print(f"  FAILED: {url}")
    return False


def md5_of(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def get_osm(out):
    url = "https://download.geofabrik.de/asia/india/northern-zone-latest.osm.pbf"
    pbf, md5 = out / "northern-zone-latest.osm.pbf", out / "northern-zone-latest.osm.pbf.md5"
    md5.unlink(missing_ok=True)  # always fetch the current checksum
    if not (fetch(url + ".md5", md5) and fetch(url, pbf)):
        return False
    expected = md5.read_text().split()[0]
    if md5_of(pbf) != expected:
        print(f"  MD5 mismatch for {pbf.name}; deleting it so the next run downloads it again")
        pbf.unlink()
        return False
    print("  MD5 matches")
    return True


def get_dem(out):
    base = "https://copernicus-dem-30m.s3.amazonaws.com"
    ok = True
    for lat in range(28, 32):
        for lon in range(77, 82):
            name = f"Copernicus_DSM_COG_10_N{lat:02d}_00_E{lon:03d}_00_DEM"
            ok &= fetch(f"{base}/{name}/{name}.tif", out / f"{name}.tif", skip_404=True)
    return ok


def get_landcover(out):
    base = "https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map"
    ok = True
    for tile in ["N27E075", "N27E078", "N27E081", "N30E075", "N30E078", "N30E081"]:
        name = f"ESA_WorldCover_10m_2021_v200_{tile}_Map.tif"
        ok &= fetch(f"{base}/{name}", out / name, skip_404=True)
    return ok


def get_soil(out):
    """Cut each SoilGrids layer to the bounding box with gdalwarp (needs the fs2 env)."""
    base = "https://files.isric.org/soilgrids/latest/data_aggregated/1000m"
    out.mkdir(parents=True, exist_ok=True)
    ok = True
    for prop in ["sand", "clay"]:
        for depth in ["0-5cm", "5-15cm"]:
            dest, tmp = out / f"{prop}_{depth}.tif", out / f"{prop}_{depth}.part.tif"
            if dest.exists():
                print(f"  {dest.name}: already complete")
                continue
            url = f"{base}/{prop}/{prop}_{depth}_mean_1000.tif"
            cmd = ["gdalwarp", "--config", "GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR",
                   "-overwrite", "-t_srs", "EPSG:4326", "-te", *BBOX, "-tr", "0.01", "0.01",
                   "-of", "GTiff", "-co", "COMPRESS=DEFLATE", f"/vsicurl/{url}", str(tmp)]
            for attempt in range(1, TRIES + 1):
                print(f"  {dest.name}: cutting from {url}")
                r = subprocess.run(cmd, capture_output=True, text=True)
                if r.returncode == 0:
                    tmp.replace(dest)
                    print(f"  {dest.name}: done")
                    break
                print(f"  attempt {attempt}/{TRIES} failed for {url}\n    {r.stderr.strip()}")
                if attempt < TRIES:
                    time.sleep(10 * attempt)
            else:
                print(f"  FAILED: {url}")
                ok = False
    return ok


def get_basins(out):
    url = "https://data.hydrosheds.org/file/HydroBASINS/standard/hybas_as_lev08_v1c.zip"
    zpath = out / "hybas_as_lev08_v1c.zip"
    if not fetch(url, zpath):
        return False
    with zipfile.ZipFile(zpath) as z:
        z.extractall(out)
    print("  unzipped")
    return True


def get_landslides(out):
    url = "https://data.nasa.gov/api/views/dd9e-wu2v/rows.csv?accessType=DOWNLOAD"
    if fetch(url, out / "nasa_global_landslide_catalog.csv"):
        return True
    print("  The NASA catalogue link failed. Download the HDX mirror by hand:\n"
          "    https://data.humdata.org/dataset/global-landslide-catalogue-nasa\n"
          f"  and save the zip in {out}")
    return False


def get_floods(out):
    record = "https://zenodo.org/api/records/4742142"
    try:
        r = requests.get(record, timeout=30)  # follows the redirect to the latest version
        r.raise_for_status()
        files = r.json()["files"]
    except Exception as e:
        print(f"  FAILED: {record}\n    {e}")
        return False
    print(f"  Zenodo record {r.json()['id']}: {len(files)} files")
    ok = True
    for f in files:
        dest = out / f["key"]
        if not fetch(f["links"]["self"], dest):
            ok = False
        elif md5_of(dest) != f["checksum"].removeprefix("md5:"):
            print(f"  MD5 mismatch for {dest.name}; deleting it so the next run downloads it again")
            dest.unlink()
            ok = False
    return ok


SOURCES = {
    "osm": get_osm,
    "dem": get_dem,
    "landcover": get_landcover,
    "soil": get_soil,
    "basins": get_basins,
    "landslides": get_landslides,
    "floods": get_floods,
}


def status():
    print(f"{'source':<12}{'files':>7}{'MB':>10}  complete")
    for name in SOURCES:
        folder = RAW / name
        files = [p for p in folder.rglob("*") if p.is_file()] if folder.exists() else []
        done = [p for p in files if not p.name.startswith(".") and ".part" not in p.name]
        mb = sum(p.stat().st_size for p in files) / 1e6  # includes .part so progress shows
        print(f"{name:<12}{len(done):>7}{mb:>10.1f}  {'yes' if (folder / '.complete').exists() else 'no'}")


def main(args):
    if args == ["status"]:
        return status()
    unknown = [a for a in args if a not in SOURCES]
    if not args or unknown:
        sys.exit(f"usage: download.py <source> [...] | status\nsources: {', '.join(SOURCES)}"
                 + (f"\nunknown: {', '.join(unknown)}" if unknown else ""))
    for name in args:
        out = RAW / name
        marker = out / ".complete"
        marker.unlink(missing_ok=True)
        print(f"== {name}")
        if SOURCES[name](out):
            marker.touch()
            print(f"== {name}: complete")
        else:
            print(f"== {name}: NOT complete (see errors above)")


if __name__ == "__main__":
    main(sys.argv[1:])
