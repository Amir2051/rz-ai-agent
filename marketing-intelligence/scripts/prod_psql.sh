#!/bin/bash
# Run psql against the dedicated marketing database using the project secret file.
set -eo pipefail
cd "$(dirname "$0")/.."
URL=$(python3 scripts/db_url.py)
if [ -z "$URL" ]; then echo 'MI_DATABASE_URL missing' >&2; exit 1; fi
exec psql "$URL" -X "$@"
