#!/bin/bash
set -euo pipefail
B=/usr/lib/postgresql/18/bin
D=$(mktemp -d /tmp/mi-pg.XXXXXX)
trap '"$B/pg_ctl" -D "$D/data" -m fast stop >/dev/null 2>&1 || true; rm -rf -- "$D"' EXIT
"$B/initdb" -D "$D/data" -A trust --no-locale --encoding=UTF8 >/dev/null
"$B/pg_ctl" -D "$D/data" -l "$D/log" -o "-c listen_addresses='' -k $D -p 55489" -w start
export PGHOST="$D" PGPORT=55489 PGDATABASE=postgres
P=("$B/psql" -X -v ON_ERROR_STOP=1)
"${P[@]}" -c 'CREATE ROLE marketing_owner;'
for f in database/migrations/00{1,2,3,4,5,6,7,8,9}_*.sql; do "${P[@]}" -f "$f" >/dev/null; done
"${P[@]}" -f database/migrations/010_identity_foundation.sql >/dev/null
"${P[@]}" -f database/migrations/011_registration_role.sql >/dev/null
"${P[@]}" -f database/migrations/012_activation.sql >/dev/null
"${P[@]}" -f database/migrations/014_automation.sql >/dev/null
PYTHONPATH=. .venv-auth/bin/python scripts/test_customer_registration.py
PYTHONPATH=. .venv-auth/bin/python scripts/test_mfa_postgres.py
"${P[@]}" -f database/tests/tenant_isolation.sql
"${P[@]}" -f database/tests/draft_isolation.sql

PYTHONPATH=. .venv-auth/bin/python scripts/test_api_postgres.py

"${P[@]}" -f database/tests/ai_isolation.sql
"${P[@]}" -f database/tests/activation_isolation.sql
PYTHONPATH=.:vendor/marketing-data-hub/src .venv-auth/bin/python scripts/offline_pytest.py -q tests

PYTHONPATH=. .venv-auth/bin/python scripts/test_ai_budget.py

PYTHONPATH=. .venv-hub/bin/python scripts/test_mcp_runtime.py

PYTHONPATH=. .venv-auth/bin/python scripts/test_dashboard_integrated.py
