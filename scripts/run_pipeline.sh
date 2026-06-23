#!/usr/bin/env bash
# run_pipeline.sh — run the whole ingestion pipeline (steps 1-5) on one PDF.
#
#   ./scripts/run_pipeline.sh path/to/document.pdf
#
# Runs, in order:
#   1_cleanup.sh   (as db2inst1)   drop old table + index — clean slate
#   2_setup.sh     (as db2inst1)   enable text search + register OpenSearch
#   3_extract.py   (as you)        PDF       -> document.md
#   4_chunk.py     (as you)        document.md -> document.chunks.csv
#   5_ingest.py    (as you)        document.chunks.csv -> Db2
#
# Run from the repo root as your normal user. The two shell steps are piped to
# db2inst1 via sudo (so db2/db2ts are available); the Python steps use the
# project's .venv. Search (step 6) is separate: python scripts/6_search.py "...".

set -euo pipefail

if [ $# -ne 1 ]; then
    echo "Usage: ./scripts/run_pipeline.sh path/to/document.pdf" >&2
    exit 1
fi

PDF="$1"
[ -f "$PDF" ] || { echo "PDF not found: $PDF" >&2; exit 1; }

SCRIPTS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"   # the scripts/ dir
REPO="$(dirname "$SCRIPTS")"                              # repo root
PY="$REPO/.venv/bin/python3"
OWNER="${DB2_INSTANCE_OWNER:-db2inst1}"

# Intermediate files — match each script's default output path.
MD="${PDF%.*}.md"
CSV="${PDF%.*}.chunks.csv"

echo "### 1/5  cleanup  (as $OWNER)"
sudo -iu "$OWNER" bash -ls < "$SCRIPTS/1_cleanup.sh"

echo "### 2/5  setup    (as $OWNER)"
sudo -iu "$OWNER" bash -ls < "$SCRIPTS/2_setup.sh"

echo "### 3/5  extract  $PDF -> $MD"
"$PY" "$SCRIPTS/3_extract.py" "$PDF"

echo "### 4/5  chunk    $MD -> $CSV"
"$PY" "$SCRIPTS/4_chunk.py" "$MD"

echo "### 5/5  ingest   $CSV -> Db2"
"$PY" "$SCRIPTS/5_ingest.py" "$CSV"

echo "### done — corpus is ready. Search it with:  python scripts/6_search.py \"your query\""
