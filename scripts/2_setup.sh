#!/usr/bin/env bash
# 2_setup.sh — one-time setup (run once).
#
# Prepares Db2 for keyword search:
#   1. enables Db2 Text Search on the database, and
#   2. registers OpenSearch as the search backend the keyword index will use.
#
# Run as the Db2 instance owner:  ./2_setup.sh

set -uo pipefail

# These commands need the Db2 environment. Fail loudly if db2 isn't on PATH
# (e.g. run as your login user) instead of silently doing nothing.
command -v db2 >/dev/null 2>&1 || {
  echo "db2 not found — run this as the Db2 instance owner, e.g.:" >&2
  echo "  sudo -iu ${DB2_INSTANCE_OWNER:-db2inst1} bash -ls < scripts/2_setup.sh" >&2
  exit 1
}

DB="${DB2_DATABASE:-sample}"
OS_HOST="${OPENSEARCH_HOST:-localhost}"
OS_PORT="${OPENSEARCH_PORT:-9200}"

export DB2DBDFT="$DB"
db2 connect to "$DB" >/dev/null

echo "Enabling Db2 Text Search on '${DB}' ..."
db2 "CREATE TABLESPACE systoolspace" >/dev/null 2>&1 || true   # needed by text search; ok if it exists
# Enable text search via the stored procedure (replaces: db2ts ENABLE DATABASE FOR TEXT).
# Args: message_locale, OUT message. https://www.ibm.com/docs/en/db2/12.1.x?topic=indexes-enabling-database-text-search
db2 "CALL SYSPROC.SYSTS_ENABLE('en_US', ?)" >/dev/null 2>&1 || true   # ok if already enabled

echo "Registering OpenSearch (${OS_HOST}:${OS_PORT}) as the search backend ..."

# Register the server via the stored procedure (replaces: INSERT INTO SYSIBMTS.SYSTSSERVERS).
# SYSTS_CREATE_SERVER does not dedupe, so skip if this host:port is already registered.
# https://www.ibm.com/docs/en/db2/12.1.x?topic=routines-systs-create-server-procedure
EXISTING=$(db2 -x "SELECT SERVERID FROM SYSIBMTS.TSSERVERS WHERE ENGINETYPE='OPENSEARCH' AND HOST='${OS_HOST}' AND PORT=${OS_PORT} FETCH FIRST 1 ROW ONLY" 2>/dev/null | tr -d ' ')
if [ -n "$EXISTING" ]; then
  echo "  already registered (server id ${EXISTING}, skipped)."
else
  # Args: host, port, auth, master_key, engine_type, encryption_enabled,
  #       server_type, server_status, message_locale, OUT server_id, OUT message.
  db2 "CALL SYSPROC.SYSTS_CREATE_SERVER('${OS_HOST}', ${OS_PORT}, 'admin:admin', 'master_key', 'OPENSEARCH', 0, 2, 0, 'en_US', ?, ?)" >/dev/null \
    && echo "  registered." || echo "  registration failed."
fi

# List registered OpenSearch servers via the catalog view (replaces: SYSIBMTS.SYSTSSERVERS table).
# https://www.ibm.com/docs/en/db2/12.1.x?topic=views-sysibmtstsservers
echo "OpenSearch servers known to Db2:"
db2 "SELECT SERVERID, CAST(HOST AS VARCHAR(40)) AS HOST, PORT FROM SYSIBMTS.TSSERVERS WHERE ENGINETYPE='OPENSEARCH'"
