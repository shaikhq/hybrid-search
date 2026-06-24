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
OWNER="${DB2_INSTANCE_OWNER:-db2inst1}"

# Stage copies the instance owner can read (eval.py imports hybrid_core.py).
cp "$HERE/eval.py" "$HERE/hybrid_core.py" /tmp/
chmod 644 /tmp/eval.py /tmp/hybrid_core.py

sudo -iu "$OWNER" bash -lc 'DB2_HOST=local python3 /tmp/eval.py'
