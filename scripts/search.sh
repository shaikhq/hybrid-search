#!/usr/bin/env bash
# search.sh — fast hybrid search via a LOCAL Db2 connection.
#
# 6_search.py over TCP is slow on this setup (the ibm_db TCP connect spins).
# This wrapper runs it as the Db2 instance owner over a local connection
# (~0.4s connect instead of ~40s). The search itself is unchanged.
#
#   ./scripts/search.sh "your question"
#   ./scripts/search.sh                 # preset demo queries
#
# Requires: ibm_db installed for the instance owner, and passwordless sudo to it.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OWNER="${DB2_INSTANCE_OWNER:-db2inst1}"

# The instance owner can't read files under /home/<you>, so stage a copy it can.
cp "$HERE/6_search.py" /tmp/6_search.py
chmod 644 /tmp/6_search.py

# Run as the instance owner with a LOCAL connection (DB2_HOST=local).
# Build the command with the query safely shell-quoted (%q), because passing
# positional args through `sudo -i` is unreliable.
if [ $# -ge 1 ]; then
    CMD=$(printf 'DB2_HOST=local python3 /tmp/6_search.py %q' "$1")
else
    CMD='DB2_HOST=local python3 /tmp/6_search.py'
fi
sudo -iu "$OWNER" bash -lc "$CMD"
