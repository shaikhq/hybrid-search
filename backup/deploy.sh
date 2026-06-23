#!/usr/bin/env bash
#
# deploy.sh
# ============================================================================
# Copies the Db2 text-search scripts from this project into db2inst1's home
# directory, so the copy you edit and the copy you run never drift apart.
#
# HOW TO RUN  (as your normal user — the one with sudo, NOT db2inst1):
#
#     ./deploy.sh
#
# After deploying, run the scripts as the instance owner:
#
#     su - db2inst1
#     ./setup_text_search.sh
#     ./run_search_queries.sh
# ============================================================================

set -euo pipefail

# This script's own directory — survives renaming/moving the project.
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST_DIR="/home/db2inst1"                    # where db2inst1 runs them from
OWNER="db2inst1:db2iadm1"                     # user:group that should own them

SCRIPTS=(
    setup_text_search.sh
    run_search_queries.sh
    setup_llm_docs.sh
    index_pdf_chunks.sh
    drop_pdf_chunks_index.sh
)

for script in "${SCRIPTS[@]}"; do
    src="$SRC_DIR/$script"

    # Skip (with a warning) anything that isn't in the project yet.
    if [ ! -f "$src" ]; then
        echo "WARNING: $src not found — skipping" >&2
        continue
    fi

    sudo cp "$src" "$DEST_DIR/$script"
    sudo chown "$OWNER" "$DEST_DIR/$script"
    sudo chmod 755 "$DEST_DIR/$script"
    echo "deployed: $DEST_DIR/$script"
done

echo "Done."
