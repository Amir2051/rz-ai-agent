#!/bin/bash
# Verify the idempotent production rollout from the documented 001+002 state.
set -euo pipefail
B=/usr/lib/postgresql/18/bin
D=$(mktemp -d /tmp/mi-pg.XXXXXX)
trap '"$B/pg_ctl" -D "$D/data" -m fast stop >/dev/null 2>&1 || true; rm -rf -- "$D"' EXIT
"$B/initdb" -D "$D/data" -A trust --no-locale --encoding=UTF8 >/dev/null
"$B/pg_ctl" -D "$D/data" -l "$D/log" -o "-c listen_addresses='' -k $D -p 55489" -w start
export PGHOST="$D" PGPORT=55489 PGDATABASE=postgres
P=("$B/psql" -X -v ON_ERROR_STOP=1)
"${P[@]}" -c 'CREATE ROLE marketing_owner;'
"${P[@]}" -f database/migrations/001_foundation.sql >/dev/null
"${P[@]}" -f database/migrations/002_tenant_reader.sql >/dev/null
"${P[@]}" -f database/migrations/013_production_rollout.sql >/dev/null
# Idempotency: applying twice must succeed.
"${P[@]}" -f database/migrations/013_production_rollout.sql >/dev/null
PYTHONPATH=. .venv-auth/bin/python scripts/test_customer_registration.py
PYTHONPATH=. .venv-auth/bin/python scripts/test_mfa_postgres.py
PYTHONPATH=. .venv-auth/bin/python scripts/test_google_mfa_postgres.py
"${P[@]}" -f database/tests/tenant_isolation.sql >/dev/null
"${P[@]}" -f database/tests/draft_isolation.sql >/dev/null
PYTHONPATH=. .venv-auth/bin/python scripts/test_api_postgres.py
"${P[@]}" -f database/tests/ai_isolation.sql >/dev/null
"${P[@]}" -f database/tests/activation_isolation.sql >/dev/null
PYTHONPATH=. .venv-auth/bin/python scripts/test_ai_budget.py
PYTHONPATH=. .venv-hub/bin/python scripts/test_mcp_runtime.py
PYTHONPATH=.:vendor/marketing-data-hub/src .venv-auth/bin/python scripts/offline_pytest.py -q tests
echo 'PASS: idempotent production rollout and all integration suites'
