#!/usr/bin/env bash
# 1_cleanup.sh — start from a clean slate.
#
# Drops the text-search index and then the chunks table. Run this BEFORE
# 5_ingest.py. Safe to run anytime: missing objects are ignored.
#
# The index is dropped first, because a table can't be dropped while a
# text-search index sits on it.
#
# Run as the Db2 instance owner:  ./1_cleanup.sh

set -uo pipefail

# These commands need the Db2 environment. Fail loudly if db2 isn't on PATH
# (e.g. run as your login user) instead of silently dropping nothing.
command -v db2 >/dev/null 2>&1 || {
  echo "db2 not found — run this as the Db2 instance owner, e.g.:" >&2
  echo "  sudo -iu ${DB2_INSTANCE_OWNER:-db2inst1} bash -ls < scripts/1_cleanup.sh" >&2
  exit 1
}

DB="${DB2_DATABASE:-sample}"
SCHEMA="${DB2_SCHEMA:-myschema}"
TABLE="${DB2_TABLE:-chunks}"
INDEX="${TABLE}_text_idx"

export DB2DBDFT="$DB"
db2 connect to "$DB" >/dev/null

echo "Dropping text index ${SCHEMA}.${INDEX} (if it exists) ..."
db2ts "DROP INDEX ${SCHEMA}.${INDEX} FOR TEXT" >/dev/null 2>&1 || true

echo "Dropping table ${SCHEMA}.${TABLE} (if it exists) ..."
db2 "DROP TABLE ${SCHEMA}.${TABLE}" >/dev/null 2>&1 || true

echo "Clean slate ready."
