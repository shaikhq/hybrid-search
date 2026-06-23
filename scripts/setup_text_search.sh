#!/usr/bin/env bash
#
# setup_text_search.sh
# ============================================================================
# Sets up Db2 Text Search backed by OpenSearch on the SAMPLE database, then
# builds a searchable text index over a small demo "books" table.
#
# Safe to run as many times as you like: each run tears down what the previous
# run created and rebuilds everything from scratch.
#
# HOW TO RUN  (log in as the Db2 instance owner — never with sudo):
#
#     su - db2inst1            # a LOGIN shell, so db2/db2ts are on the PATH
#     ./setup_text_search.sh
#
# WHY A SHELL SCRIPT, not a plain .sql file?
#     A few steps happen OUTSIDE the SQL engine — starting Db2, the db2ts
#     text-search commands, and reading the generated server id into a
#     variable — so they can't run with `db2 -tvf somefile.sql`.
# ============================================================================

# Treat the use of an unset variable as an error, and make a failing command
# in a pipeline fail the whole pipeline. We deliberately do NOT use `set -e`:
# a few steps are *expected* to fail harmlessly (see the `quietly` helper).
set -uo pipefail


# ----------------------------------------------------------------------------
# Settings — the only things you'd normally change, all in one place.
# ----------------------------------------------------------------------------
DATABASE="sample"             # database we add text search to
SCHEMA="myschema"             # schema holding the demo table and index
TABLE="books"                 # the demo table
INDEX_NAME="titleidx_os"      # text index we build on books(title)

OPENSEARCH_HOST="localhost"   # where OpenSearch is running
OPENSEARCH_PORT="9200"        # OpenSearch's REST port


# ----------------------------------------------------------------------------
# Small helpers, so the steps below read like plain English.
# ----------------------------------------------------------------------------

# Announce what we're about to do, on its own line, so the output is easy
# to follow as the script runs.
step() {
    echo
    echo "==> $*"
}

# Print an error message and stop the script.
fail() {
    echo "ERROR: $*" >&2
    exit 1
}

# Run a command whose failure is harmless and expected on some runs — for
# example "object already exists" or "nothing to drop". Db2 prints even its
# error text to stdout, so we hide BOTH streams; the `|| true` then keeps the
# script going regardless of the result.
quietly() {
    "$@" >/dev/null 2>&1 || true
}


# ----------------------------------------------------------------------------
# Step 1 — Start Db2 and connect to the database.
# ----------------------------------------------------------------------------
step "Step 1: Starting Db2 and connecting to '$DATABASE'"

quietly db2start                      # harmless "already active" if it's running
export DB2DBDFT="$DATABASE"           # so the db2ts commands know which database
db2 connect to "$DATABASE"


# ----------------------------------------------------------------------------
# Step 2 — Prepare the database for text search.
#
# Text search keeps its bookkeeping in catalog tables (SYSIBMTS.*). Those live
# in the SYSTOOLSPACE tablespace and are created by "ENABLE DATABASE FOR TEXT".
# Both commands are safe to repeat, so we run them quietly.
# ----------------------------------------------------------------------------
step "Step 2: Enabling text search on '$DATABASE'"

quietly db2   "CREATE TABLESPACE systoolspace"
quietly db2ts "ENABLE DATABASE FOR TEXT CONNECT TO $DATABASE"


# ----------------------------------------------------------------------------
# Step 3 — Start from a clean slate by removing anything a previous run left.
#
# Order matters: a table that has a text index on it cannot be dropped, so we
# drop the index first, then the table, then the OpenSearch server entry (so
# repeated runs don't pile up duplicate registrations).
# ----------------------------------------------------------------------------
step "Step 3: Cleaning up anything left over from a previous run"

quietly db2ts "DROP INDEX $SCHEMA.$INDEX_NAME FOR TEXT"
quietly db2   "DROP TABLE $SCHEMA.$TABLE"
quietly db2   "DELETE FROM SYSIBMTS.SYSTSSERVERS
               WHERE SERVERPORT = $OPENSEARCH_PORT AND ENGINETYPE = 'OPENSEARCH'"


# ----------------------------------------------------------------------------
# Step 4 — Create the demo table and fill it with a few rows.
# ----------------------------------------------------------------------------
step "Step 4: Creating '$SCHEMA.$TABLE' and adding sample rows"

db2 "CREATE TABLE $SCHEMA.$TABLE (
    id     INT NOT NULL PRIMARY KEY,
    title  VARCHAR(200),
    author VARCHAR(100),
    isbn   VARCHAR(20)
)"

db2 "INSERT INTO $SCHEMA.$TABLE VALUES
    (1, 'The Art of Rock Climbing',     'Joe Climber',    '978-1234567890'),
    (2, 'Advanced Climbing Techniques', 'Jeff Climber',   '978-0987654321'),
    (3, 'Mountain Adventures',          'Sarah Explorer', '978-1122334455'),
    (4, 'Indoor Climbing Guide',        'Mike Boulder',   '978-5566778899')"

db2 "SELECT * FROM $SCHEMA.$TABLE"


# ----------------------------------------------------------------------------
# Step 5 — Tell Db2 where OpenSearch lives.
#
# We add one row to SYSIBMTS.SYSTSSERVERS describing the backend:
#     SERVERTYPE        = 2  -> an external search server
#     SERVERSTATUS      = 0  -> active
#     ENCRYPTIONENABLED = 0  -> off (fine for a local, security-disabled setup)
# The 'admin:admin' token is ignored while OpenSearch security is disabled.
# ----------------------------------------------------------------------------
step "Step 5: Registering OpenSearch ($OPENSEARCH_HOST:$OPENSEARCH_PORT) as the backend"

db2 "INSERT INTO SYSIBMTS.SYSTSSERVERS (
    SERVERHOST, SERVERPORT, SERVERAUTHTOKEN, SERVERMASTERKEY,
    SERVERTYPE, SERVERSTATUS, ENGINETYPE, ENCRYPTIONENABLED
) VALUES (
    '$OPENSEARCH_HOST', $OPENSEARCH_PORT, 'admin:admin', 'my_master_key',
    2, 0, 'OPENSEARCH', 0
)"


# ----------------------------------------------------------------------------
# Step 6 — Look up the id Db2 just assigned to that server.
#
# `db2 -x` drops the column header so we get only the value; `tr -d ' '` trims
# the surrounding spaces. We need this id to attach the index to the right
# backend in the next step.
# ----------------------------------------------------------------------------
step "Step 6: Looking up the new OpenSearch server id"

SERVERID=$(db2 -x "SELECT SERVERID FROM SYSIBMTS.SYSTSSERVERS
    WHERE SERVERPORT = $OPENSEARCH_PORT AND ENGINETYPE = 'OPENSEARCH'
    FETCH FIRST 1 ROW ONLY" | tr -d ' ')

[ -n "$SERVERID" ] || fail "No OpenSearch server registration found on port $OPENSEARCH_PORT"

echo "    OpenSearch server id = $SERVERID"


# ----------------------------------------------------------------------------
# Step 7 — Build the text index on the book titles.
#
# A text index is created INACTIVE, then turned ACTIVE, then filled with the
# current table contents by UPDATE INDEX.
# ----------------------------------------------------------------------------
step "Step 7: Building the text index on $SCHEMA.$TABLE(title)"

db2ts "CREATE INDEX $SCHEMA.$INDEX_NAME FOR TEXT ON $SCHEMA.$TABLE(title) SERVERID $SERVERID INACTIVE"
db2ts "ALTER INDEX $SCHEMA.$INDEX_NAME FOR TEXT SET ACTIVE"
db2ts "UPDATE INDEX $SCHEMA.$INDEX_NAME FOR TEXT"


# ----------------------------------------------------------------------------
# Step 8 — Show the finished index so you can confirm it's ready.
# (ACTIVEFLAG = 1 means the index is active and searchable.)
# ----------------------------------------------------------------------------
step "Step 8: Verifying the index"

db2 "SELECT I.INDEXSCHEMA, I.INDEXNAME, I.SERVERID, C.ACTIVEFLAG
     FROM SYSIBMTS.SYSTSINDEXES I
     JOIN SYSIBMTS.SYSTSCOLUMNS C ON I.INDEXIDENTIFIER = C.INDEXIDENTIFIER
     WHERE I.INDEXSCHEMA = UPPER('$SCHEMA')
     ORDER BY I.INDEXNAME"

echo
echo "Done! Text index $SCHEMA.$INDEX_NAME is active on the '$DATABASE' database."
