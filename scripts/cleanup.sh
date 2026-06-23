#!/usr/bin/env bash
#
# cleanup.sh — idempotent clean slate for ingest.py
# =============================================================================
# Drops the Db2 Text Search index FIRST, then the chunks table — the required
# dependency order, because a table cannot be dropped while a text index sits
# on it. Safe to run any number of times (missing objects are ignored).
#
# Run order:   ./cleanup.sh   ->   python3 ingest.py
#
# Run this where db2 / db2ts are available — i.e. as the Db2 instance owner:
#     su - db2inst1            # a LOGIN shell, so db2/db2ts are on the PATH
#     ./cleanup.sh
#
# Configuration (env vars; same names/defaults as ingest.py):
#     DB2_DATABASE   (default: sample)
#     DB2_SCHEMA     (default: myschema)
#     DB2_TABLE      (default: chunks)
#     DB2_INDEX_NAME (default: <DB2_TABLE>_text_idx)
# =============================================================================

set -uo pipefail

DATABASE="${DB2_DATABASE:-sample}"
SCHEMA="${DB2_SCHEMA:-myschema}"
TABLE="${DB2_TABLE:-chunks}"
INDEX_NAME="${DB2_INDEX_NAME:-${TABLE}_text_idx}"

export DB2DBDFT="$DATABASE"
db2 connect to "$DATABASE" >/dev/null 2>&1 || true

# 1) Text index first (db2ts). Db2 prints errors to stdout, so hide both streams.
echo "Dropping text index ${SCHEMA}.${INDEX_NAME} (if it exists) ..."
if db2ts "DROP INDEX ${SCHEMA}.${INDEX_NAME} FOR TEXT" >/dev/null 2>&1; then
    echo "  dropped."
else
    echo "  not present."
fi

# 2) Then the table.
echo "Dropping table ${SCHEMA}.${TABLE} (if it exists) ..."
if db2 "DROP TABLE ${SCHEMA}.${TABLE}" >/dev/null 2>&1; then
    echo "  dropped."
else
    echo "  not present."
fi

# Note: the registered watsonx external model is intentionally left in place
# (it is reusable across runs, and ingest.py reuses it if already registered).
# To also remove it, uncomment — replacing the name to match DB2_MODEL_NAME:
#   db2 "DROP EXTERNAL MODEL ${SCHEMA}.${TABLE}_embed" >/dev/null 2>&1 || true

echo "Clean slate ready."
