#!/usr/bin/env bash
#
# index_pdf_chunks.sh
# ============================================================================
# Builds an OpenSearch-backed Db2 Text Search index over the CONTENT of the
# PDF chunks loaded into myschema.pdf_chunks, so you can run full-text
# CONTAINS() searches across them.
#
# If the text index already exists, it is dropped and recreated from scratch.
#
# PREREQUISITES:
#   1. ./setup_text_search.sh         (enables text search, registers OpenSearch)
#   2. python3 pdf_to_markdown.py     (PDF -> Markdown)
#   3. run chunk_and_ingest.ipynb     (fills myschema.pdf_chunks)
#
# HOW TO RUN  (log in as the Db2 instance owner — never with sudo):
#
#     su - db2inst1            # a LOGIN shell, so db2/db2ts are on the PATH
#     ./index_pdf_chunks.sh
# ============================================================================

set -uo pipefail


# ----------------------------------------------------------------------------
# Settings — change these in one place if you need different names.
# ----------------------------------------------------------------------------
DATABASE="sample"             # database that holds the chunks + index
SCHEMA="myschema"             # schema holding the table
TABLE="pdf_chunks"           # table filled by chunk_and_ingest.ipynb
COLUMN="chunk_text"          # the column we index and search
INDEX_NAME="pdf_chunks_idx"   # the text index

OPENSEARCH_PORT="9200"        # used to find the registered OpenSearch server


# ----------------------------------------------------------------------------
# Small helpers, so the steps below read like plain English.
# ----------------------------------------------------------------------------
step() { echo; echo "==> $*"; }
fail() { echo "ERROR: $*" >&2; exit 1; }

# Run a command whose failure is harmless/expected (e.g. "nothing to drop").
# Db2 prints even its errors to stdout, so we hide BOTH streams.
quietly() { "$@" >/dev/null 2>&1 || true; }


# ----------------------------------------------------------------------------
# Step 1 — Connect to the database.
# ----------------------------------------------------------------------------
step "Step 1: Connecting to '$DATABASE'"

quietly db2start
export DB2DBDFT="$DATABASE"           # so the db2ts commands know which database
db2 connect to "$DATABASE"

# Make sure the table actually exists before we try to index it.
table_found=$(db2 -x "SELECT 1 FROM SYSCAT.TABLES
    WHERE TABSCHEMA = UPPER('$SCHEMA') AND TABNAME = UPPER('$TABLE')
    FETCH FIRST 1 ROW ONLY" | tr -d ' ')
[ -n "$table_found" ] || fail "Table $SCHEMA.$TABLE not found. Run chunk_and_ingest.ipynb first."


# ----------------------------------------------------------------------------
# Step 2 — Drop the text index if it already exists (start from scratch).
# ----------------------------------------------------------------------------
step "Step 2: Dropping the existing text index (if any)"

quietly db2ts "DROP INDEX $SCHEMA.$INDEX_NAME FOR TEXT"


# ----------------------------------------------------------------------------
# Step 3 — Find the OpenSearch server that setup_text_search.sh registered.
# ----------------------------------------------------------------------------
step "Step 3: Finding the registered OpenSearch server"

SERVERID=$(db2 -x "SELECT SERVERID FROM SYSIBMTS.SYSTSSERVERS
    WHERE SERVERPORT = $OPENSEARCH_PORT AND ENGINETYPE = 'OPENSEARCH'
    FETCH FIRST 1 ROW ONLY" | tr -d ' ')

[ -n "$SERVERID" ] || fail "No OpenSearch server found. Run ./setup_text_search.sh first."

echo "    OpenSearch server id = $SERVERID"


# ----------------------------------------------------------------------------
# Step 4 — Create, activate, and populate the text index on the chunk content.
# ----------------------------------------------------------------------------
step "Step 4: Building the text index on $SCHEMA.$TABLE($COLUMN)"

db2ts "CREATE INDEX $SCHEMA.$INDEX_NAME FOR TEXT ON $SCHEMA.$TABLE($COLUMN) SERVERID $SERVERID INACTIVE"
db2ts "ALTER INDEX $SCHEMA.$INDEX_NAME FOR TEXT SET ACTIVE"
db2ts "UPDATE INDEX $SCHEMA.$INDEX_NAME FOR TEXT"


# ----------------------------------------------------------------------------
# Step 5 — Test search. New documents become searchable only after OpenSearch
# refreshes (about a second), so we retry briefly instead of querying instantly.
# ----------------------------------------------------------------------------
step "Step 5: Test search — chunks mentioning 'embedding'"

hits=0
for attempt in 1 2 3 4 5; do
    hits=$(db2 -x "SELECT COUNT(*) FROM $SCHEMA.$TABLE
                   WHERE CONTAINS($COLUMN, 'embedding') = 1" | tr -d ' ')
    [ "${hits:-0}" -gt 0 ] && break
    sleep 2
done
echo "    matches for 'embedding': ${hits:-0}"

db2 "SELECT chunk_id, headings FROM $SCHEMA.$TABLE
     WHERE CONTAINS($COLUMN, 'embedding') = 1
     ORDER BY chunk_id
     FETCH FIRST 5 ROWS ONLY"

echo
echo "Done! Search the chunks with:"
echo "    db2 \"SELECT chunk_id, headings FROM $SCHEMA.$TABLE WHERE CONTAINS($COLUMN, 'YOUR TERMS') = 1\""
