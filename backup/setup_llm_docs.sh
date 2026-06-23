#!/usr/bin/env bash
#
# setup_llm_docs.sh
# ============================================================================
# Loads 5 documents taken from Chapter 10 ("Large Language Model integration")
# of eap101_release_notes.pdf into a Db2 table, then builds an OpenSearch-backed
# text index over their CONTENT so you can run full-text search across them.
#
# The 5 documents describe these Db2 SQL elements:
#     CREATE EXTERNAL MODEL, ALTER EXTERNAL MODEL, DROP EXTERNAL MODEL,
#     TO_EMBEDDING, TEXT_GENERATION
#
# Safe to re-run: it drops and rebuilds the table and index each time.
#
# PREREQUISITE:
#     Run ./setup_text_search.sh first. That enables text search and registers
#     OpenSearch as the backend; this script reuses that registration.
#
# HOW TO RUN  (log in as the Db2 instance owner — never with sudo):
#
#     su - db2inst1            # a LOGIN shell, so db2/db2ts are on the PATH
#     ./setup_llm_docs.sh
# ============================================================================

set -uo pipefail


# ----------------------------------------------------------------------------
# Settings — change these in one place if you need different names.
# ----------------------------------------------------------------------------
DATABASE="sample"             # database that holds the text index
SCHEMA="myschema"             # schema for the table and index
TABLE="llm_docs"             # table holding the 5 documents
INDEX_NAME="llm_docs_idx"     # text index built on llm_docs(content)

OPENSEARCH_PORT="9200"        # used to find the server setup_text_search.sh made


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


# ----------------------------------------------------------------------------
# Step 2 — Start from a clean slate (drop the index before the table).
# ----------------------------------------------------------------------------
step "Step 2: Cleaning up anything left over from a previous run"

quietly db2ts "DROP INDEX $SCHEMA.$INDEX_NAME FOR TEXT"
quietly db2   "DROP TABLE $SCHEMA.$TABLE"


# ----------------------------------------------------------------------------
# Step 3 — Create the documents table and load the 5 documents.
#
# Each row is one document: a SECTION name plus the CONTENT we want to search.
# The CONTENT text is taken from Chapter 10 of eap101_release_notes.pdf.
# ----------------------------------------------------------------------------
step "Step 3: Creating '$SCHEMA.$TABLE' and loading the 5 documents"

db2 "CREATE TABLE $SCHEMA.$TABLE (
    id      INT NOT NULL PRIMARY KEY,
    section VARCHAR(64),
    content VARCHAR(2000)
)"

db2 "INSERT INTO $SCHEMA.$TABLE (id, section, content) VALUES
(1, 'CREATE EXTERNAL MODEL',
 'The CREATE EXTERNAL MODEL statement registers an external AI model in Db2. The model definition includes credentials and metadata for an external model, such as watsonx.ai or OpenAI. The model can then be referenced by a logical name in built-in functions such as TO_EMBEDDING or TEXT_GENERATION.'),
(2, 'ALTER EXTERNAL MODEL',
 'The ALTER EXTERNAL MODEL statement updates, adds, or drops a metadata attribute in an existing external model.'),
(3, 'DROP EXTERNAL MODEL',
 'The DROP EXTERNAL MODEL statement removes an external model definition from the catalog. Once dropped, the model can no longer be referenced in SQL statements or built-in functions such as TO_EMBEDDING.'),
(4, 'TO_EMBEDDING',
 'The TO_EMBEDDING built-in function returns the embedding vector for an input string. It requires an external model with model type TEXT_EMBEDDING.'),
(5, 'TEXT_GENERATION',
 'The TEXT_GENERATION built-in function returns generated text based on an input string. TEXT_GENERATION is a non-deterministic function and requires an external model with model type TEXT_GENERATION.')"

db2 "SELECT id, section FROM $SCHEMA.$TABLE ORDER BY id"


# ----------------------------------------------------------------------------
# Step 4 — Find the OpenSearch server that setup_text_search.sh registered.
#
# `db2 -x` drops the column header; `tr -d ' '` trims the spaces. We reuse that
# one registration instead of adding a duplicate.
# ----------------------------------------------------------------------------
step "Step 4: Finding the registered OpenSearch server"

SERVERID=$(db2 -x "SELECT SERVERID FROM SYSIBMTS.SYSTSSERVERS
    WHERE SERVERPORT = $OPENSEARCH_PORT AND ENGINETYPE = 'OPENSEARCH'
    FETCH FIRST 1 ROW ONLY" | tr -d ' ')

[ -n "$SERVERID" ] || fail "No OpenSearch server found. Run ./setup_text_search.sh first."

echo "    OpenSearch server id = $SERVERID"


# ----------------------------------------------------------------------------
# Step 5 — Build the text index on the document CONTENT.
#
# Created INACTIVE, then turned ACTIVE, then populated by UPDATE INDEX.
# ----------------------------------------------------------------------------
step "Step 5: Building the text index on $SCHEMA.$TABLE(content)"

db2ts "CREATE INDEX $SCHEMA.$INDEX_NAME FOR TEXT ON $SCHEMA.$TABLE(content) SERVERID $SERVERID INACTIVE"
db2ts "ALTER INDEX $SCHEMA.$INDEX_NAME FOR TEXT SET ACTIVE"
db2ts "UPDATE INDEX $SCHEMA.$INDEX_NAME FOR TEXT"


# ----------------------------------------------------------------------------
# Step 6 — Quick proof it works: search the content for "embedding".
# ----------------------------------------------------------------------------
step "Step 6: Test search — documents mentioning 'embedding'"

db2 "SELECT id, section FROM $SCHEMA.$TABLE
     WHERE CONTAINS(content, 'embedding') = 1
     ORDER BY id"

echo
echo "Done! 5 documents indexed. Search them with:"
echo "    db2 \"SELECT id, section FROM $SCHEMA.$TABLE WHERE CONTAINS(content, 'YOUR TERMS') = 1\""
