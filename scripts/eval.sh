#!/usr/bin/env bash
# eval.sh — run the retrieval-quality eval (eval.py) over a LOCAL Db2 connection.
#
# Reports MRR / Recall / Hits@1 per leg so you can judge changes to chunking,
# the embedding model, or the fusion gates/weights by the numbers.
#
#   ./scripts/eval.sh
#
# Requires: ibm_db installed for the instance owner, and passwordless sudo to it.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(dirname "$HERE")"
OWNER="${DB2_INSTANCE_OWNER:-db2inst1}"

# Stage copies the instance owner can read (eval.py imports hybrid_core.py and
# loads the shared ui/queries.json).
cp "$HERE/eval.py" "$HERE/hybrid_core.py" "$REPO/ui/queries.json" /tmp/
chmod 644 /tmp/eval.py /tmp/hybrid_core.py /tmp/queries.json

sudo -iu "$OWNER" bash -lc 'DB2_HOST=local python3 /tmp/eval.py'
