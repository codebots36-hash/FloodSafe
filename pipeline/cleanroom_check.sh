#!/usr/bin/env bash
# Prove a fresh clone can run the API: copy only what git would commit, with no
# pipeline/out and no raw data, install requirements into an empty virtualenv,
# run the tests, then start the server and call it.
#
# Usage:  bash pipeline/cleanroom_check.sh <python> [port]
# Needs:  a Python 3.12 to build the virtualenv, and network access for pip.
set -euo pipefail

PY="${1:?usage: cleanroom_check.sh <python> [port]}"
PORT="${2:-8020}"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
WORK="$(mktemp -d)"
trap 'kill ${SERVER:-0} 2>/dev/null || true' EXIT

echo "== copying what a commit would hold into $WORK"
cd "$REPO"
git ls-files -c -o --exclude-standard -z | xargs -0 cp --parents -t "$WORK"
test ! -e "$WORK/pipeline/out" || { echo "pipeline/out leaked into the copy"; exit 1; }

echo "== installing requirements into an empty virtualenv"
"$PY" -m venv "$WORK/venv"
VPY="$WORK/venv/Scripts/python.exe"; [ -x "$VPY" ] || VPY="$WORK/venv/bin/python"
"$VPY" -m pip install --quiet --disable-pip-version-check -r "$WORK/api/requirements-dev.txt"

echo "== tests"
cd "$WORK/api"
"$VPY" -m pytest -q

echo "== server"
"$VPY" -m uvicorn app.main:app --port "$PORT" > "$WORK/server.log" 2>&1 &
SERVER=$!
for _ in $(seq 1 60); do curl -s "http://127.0.0.1:$PORT/health" > /dev/null && break; sleep 1; done
for path in health "route?from=30.0869,78.2676&to=30.729,78.443&mode=safest" layers/road-risk layers/atlas \
            shelters hospitals localities zones reports; do
  printf "%-62s %s\n" "${path:0:60}" "$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/$path")"
done
echo "== done; copy kept at $WORK"
