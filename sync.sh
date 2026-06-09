#!/usr/bin/env bash
# gcr-sync: Google Classroom Synchronization
# Usage: ./sync.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Load environment variables if .env exists
if [ -f .env ]; then
    set -a
    source .env
    set +a
fi

# Run the sync
python -m src.main "$@"
