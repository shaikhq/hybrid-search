#!/usr/bin/env bash
#
# run_search_queries.sh
# ============================================================================
# A guided tour of Db2 Text Search (CONTAINS) over the demo "books" table.
#
# For each example it prints three things:
#     GOAL    - what the query is trying to find, in plain English
#     QUERY   - the exact SQL being run
#     OUTPUT  - the rows Db2 returned
#
# PREREQUISITE:
#     Run ./setup_text_search.sh first. That creates the books table and the
#     text index this script searches.
#
# HOW TO RUN  (log in as the Db2 instance owner — never with sudo):
#
#     su - db2inst1            # a LOGIN shell, so db2 is on the PATH
#     ./run_search_queries.sh
# ============================================================================

set -uo pipefail


# ----------------------------------------------------------------------------
# Settings — must match what setup_text_search.sh created.
# ----------------------------------------------------------------------------
DATABASE="sample"             # database that holds the text index
SCHEMA="myschema"             # schema holding the demo table
TABLE="books"                 # the demo table
COLUMN="title"                # the indexed column we search


# ----------------------------------------------------------------------------
# run_search — show one example, then run it.
#
#   $1  goal  : a plain-English description of what we're looking for
#   $2  expr  : the search expression that goes inside CONTAINS(title, '...')
#
# Every example differs only in that search expression, so the surrounding
# SELECT is written once, here.
# ----------------------------------------------------------------------------
run_search() {
    local goal="$1"
    local expr="$2"
    local sql="SELECT ID, TITLE, AUTHOR FROM ${SCHEMA}.${TABLE} WHERE CONTAINS(${COLUMN}, '${expr}') = 1"

    echo
    echo "=================================================================="
    echo "GOAL:   $goal"
    echo "QUERY:  $sql"
    echo "------------------------------------------------------------------"
    db2 "$sql"
}


# ----------------------------------------------------------------------------
# Connect to the database (quietly — we only care about the query output).
# ----------------------------------------------------------------------------
export DB2DBDFT="$DATABASE"
db2 connect to "$DATABASE" >/dev/null


# ----------------------------------------------------------------------------
# 1. Basic text search — match a single word anywhere in the title.
# ----------------------------------------------------------------------------
run_search "Books with the word 'Climbing' in the title" \
           "Climbing"

# ----------------------------------------------------------------------------
# 2. Phrase search — the words must appear together, in this exact order.
#    (Double quotes around the phrase tell CONTAINS to treat it as a phrase.)
# ----------------------------------------------------------------------------
run_search "Books containing the exact phrase 'Rock Climbing'" \
           '"Rock Climbing"'

# ----------------------------------------------------------------------------
# 3. Wildcard search — '*' matches any ending, so Climb* also finds Climbing.
# ----------------------------------------------------------------------------
run_search "Books with any word starting with 'Climb'" \
           "Climb*"

# ----------------------------------------------------------------------------
# 4. Boolean search — combine terms with AND / OR / NOT.
# ----------------------------------------------------------------------------
run_search "Books with BOTH 'Climbing' and 'Advanced'" \
           "Climbing AND Advanced"

run_search "Books with EITHER 'Climbing' or 'Mountain'" \
           "Climbing OR Mountain"

run_search "Books with 'Climbing' but NOT 'Indoor'" \
           "Climbing NOT Indoor"

# ----------------------------------------------------------------------------
# 5. Proximity search — the two words must be within 2 words of each other.
# ----------------------------------------------------------------------------
run_search "Books where 'Rock' and 'Climbing' appear within 2 words" \
           "NEAR((Rock, Climbing), 2)"


echo
echo "=================================================================="
echo "Done! That's the full tour of CONTAINS text-search queries."
