#!/usr/bin/env bash
#
# drop_pdf_chunks_index.sh
# ============================================================================
# Drops the Db2 Text Search index on myschema.pdf_chunks.
#
# Run this BEFORE re-running chunk_and_ingest.ipynb: a table cannot be dropped
# while a text index sits on it, and the notebook (remote connection) can't
# drop the index itself. Afterwards, rebuild the index with index_pdf_chunks.sh.
#
# HOW TO RUN  (log in as the Db2 instance owner — never with sudo):
#
#     su - db2inst1            # a LOGIN shell, so db2/db2ts are on the PATH
#     ./drop_pdf_chunks_index.sh
# ============================================================================

set -uo pipefail

DATABASE="sample"
SCHEMA="myschema"
INDEX_NAME="pdf_chunks_idx"

step() { echo; echo "==> $*"; }
quietly() { "$@" >/dev/null 2>&1 || true; }

step "Connecting to '$DATABASE'"
quietly db2start
export DB2DBDFT="$DATABASE"
db2 connect to "$DATABASE"

step "Dropping text index $SCHEMA.$INDEX_NAME (if it exists)"
# Harmless "index does not exist" on a clean database.
db2ts "DROP INDEX $SCHEMA.$INDEX_NAME FOR TEXT" 2>/dev/null \
    && echo "    dropped." \
    || echo "    nothing to drop (index did not exist)."

echo
echo "Done. You can now re-run chunk_and_ingest.ipynb, then ./index_pdf_chunks.sh."
