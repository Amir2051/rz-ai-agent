#!/bin/bash
set -eo pipefail
cd "$(dirname "$0")/.."
URL=$(python3 scripts/db_owner_url.py)
if [ -z "$URL" ]; then echo 'owner MI_DATABASE_URL missing' >&2; exit 1; fi
export PGSSLROOTCERT=system
exec psql "$URL" -X "$@"
