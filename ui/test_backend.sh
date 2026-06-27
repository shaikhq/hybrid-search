#!/usr/bin/env bash
# test_backend.sh — run the UI backend tests in-process (no server, no port).
#
# Stages the UI backend + test alongside hybrid_core, then runs them as the Db2
# instance owner over a LOCAL connection (same as build_fixtures.sh). Calls the
# FastAPI route functions and the search engine directly, so it tests the backend
# without uvicorn or the browser in the way.
#
#   ./ui/test_backend.sh

set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"      # ui/
REPO="$(dirname "$HERE")"
OWNER="${DB2_INSTANCE_OWNER:-db2inst1}"

STAGE=/tmp/hybrid-uitest
rm -rf "$STAGE"; mkdir -p "$STAGE"
cp "$HERE/api.py" "$HERE/build_fixtures.py" "$HERE/test_backend.py" \
   "$HERE/queries.json" "$REPO/scripts/hybrid_core.py" "$STAGE/"
cp -r "$HERE/static" "$STAGE/static"           # api.py mounts static/ at import
chmod -R a+rX "$STAGE"

sudo -iu "$OWNER" bash -lc "cd '$STAGE' && DB2_HOST=local python3 test_backend.py"
