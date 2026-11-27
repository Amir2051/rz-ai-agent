# Release task register
Branch: feature/marketing-intelligence
1. DONE — Commit existing local fixes; full marketing suite. Command: cd marketing-intelligence && /home/ronzoro/dev-terminal/safenestt-marketing-intelligence/.venv-auth/bin/python scripts/offline_pytest.py -o 'pythonpath=. /home/ronzoro/dev-terminal/safenestt-marketing-intelligence/vendor/marketing-data-hub/src' tests -q. Output: 107 passed, 30 subtests passed in 0.79s. git diff --check clean. Partial implementation only; not release approval.
2. TODO — Resolve all ten RZ AI failures; full suite, no skips or weakening.
3. TODO — Complete Google sign-in button, callback, session, logout.
4. TODO — Complete MFA UI, challenge issuance, verification rate limiting, no premature session.
5. TODO — Secure owner linking by verified stable subject after existing credentials and MFA; no email-only linking.
6. TODO — Negative regression tests for enrollment, replacement, MFA session gating, unverified email, unauthorized linking, replay/expiry, issuer/audience.
7. TODO — All suites, lint, type checks green.
8. TODO — Scratch restore, migration, row counts/constraints/tests, tested rollback, verified restorable fresh backup before transactional production migration. Stop if backup unverified.
9. TODO — Push without force, PR, CI green, merge only after 1–8 DONE.
10. TODO — Final live Google/MFA acceptance instructions.
