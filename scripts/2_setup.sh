#!/usr/bin/env bash
# 2_setup.sh — one-time setup (run once).
#
# Prepares Db2 for keyword search:
#   1. enables Db2 Text Search on the database, and
#   2. registers OpenSearch as the search backend the keyword index will use.
#
# Run as the Db2 instance owner:  ./2_setup.sh

set -uo pipefail

DB="${DB2_DATABASE:-sample}"
OS_HOST="${OPENSEARCH_HOST:-localhost}"
OS_PORT="${OPENSEARCH_PORT:-9200}"

export DB2DBDFT="$DB"
db2 connect to "$DB" >/dev/null

echo "Enabling Db2 Text Search on '${DB}' ..."
db2   "CREATE TABLESPACE systoolspace" >/dev/null 2>&1 || true   # needed by text search; ok if it exists
db2ts "ENABLE DATABASE FOR TEXT CONNECT TO ${DB}" >/dev/null 2>&1 || true   # ok if already enabled

echo "Registering OpenSearch (${OS_HOST}:${OS_PORT}) as the search backend ..."
db2 "INSERT INTO SYSIBMTS.SYSTSSERVERS
       (SERVERHOST, SERVERPORT, SERVERAUTHTOKEN, SERVERMASTERKEY,
        SERVERTYPE, SERVERSTATUS, ENGINETYPE, ENCRYPTIONENABLED)
     VALUES ('${OS_HOST}', ${OS_PORT}, 'admin:admin', 'master_key',
             2, 0, 'OPENSEARCH', 0)" >/dev/null 2>&1 \
  && echo "  registered." || echo "  already registered (skipped)."

echo "OpenSearch servers known to Db2:"
db2 "SELECT SERVERID, SERVERHOST, SERVERPORT FROM SYSIBMTS.SYSTSSERVERS WHERE ENGINETYPE='OPENSEARCH'"
