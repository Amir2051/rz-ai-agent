# Release handoff

Worktree: /home/ronzoro/dev-terminal/rz-ai-marketing-integration on Kali.
Branch: feature/marketing-intelligence. Items 1–6 are done. Item 7 has unresolved static-check findings.
No branch was pushed, no PR was opened, and no production database or service was contacted.

## Verified results

- Item 1 marketing suite: 107 passed, 30 subtests passed. Exact command in TASKLIST.md.
- Item 2 full RZ AI suite: 309 passed, 1 warning in 20.23s. Prior CI baseline: 299 passed, 10 failed. Installed requirements-dev.txt in an isolated Python 3.13 environment and ran `python -m pytest -q --tb=short --disable-warnings tests` with dotenv loading disabled and an isolated PostgreSQL 18 test cluster bound to loopback. Test output on Kali: /tmp/rz-release.RC9SKE/pytest.log. No tests skipped. Python 3.12 CI remains unverified.
- Fixed damaged chat-composer markup, missing conversation status, run-history initialization, and stale image tool registry metadata. Image generation still requires per-account opt-in and an explicit approval for each action. The tests used fixtures and no paid provider requests.
- Kept conversation owner validation and the external 404 check. Updated tests to match early storage-level owner rejection and isolate Tool Center imports so test API keys cannot leak across fixtures.

- Items 3–4: Offline PostgreSQL HTTP flow passed against migrations 001–012 and against the equivalent scratch rollout from 001+002 using 013. The provider exchange was simulated using signed assertions and the real claim verifier. Chromium also verified the sign-in, MFA, and sign-out controls using intercepted responses. The offline marketing suite passed 107 tests and 30 subtests.

- Item 5: Owner linking now requires fresh password and authenticator proof in the existing owner session. The callback is session-bound and stores only the verified Google subject. Offline PostgreSQL integration passed against both schema bootstrap paths, and Chromium verified proof submission and the redirect with intercepted responses. The offline marketing suite passed 107 tests and 30 subtests.

- Item 6: PostgreSQL/OIDC coverage now exercises missing enrollment proof, confirmed factor replacement, expired state and challenges, and signed issuer/audience rejection. Earlier MFA gating and linking regressions also passed. Fixed the enabled password-registration route and verified regular registration, password sign-in and the owner restriction.
- Item 7: RZ `python -m pytest -q --tb=short --disable-warnings tests` with Python 3.13 and disposable PostgreSQL returned 309 passed, 1 warning. Marketing `bash scripts/test_local_postgres.sh` and `bash scripts/test_rollout.sh` with installed fixture dependency paths returned 108 passed, 32 subtests on each run. Mock dashboard and standalone HTTP/stdio MCP tests also passed. Logs: /tmp/rz-release.3TBuxe/pytest.log and /tmp/mi-release-checks.6jW2pz. Python compile checks, JS syntax for 12 inline scripts and 3 JS files, Bash syntax for 5 scripts, and git diff check passed.
- Static checks: `ruff check --no-cache app tests marketing-intelligence` reported 438 findings versus 442 at start. `python3.13 -m mypy --no-incremental --explicit-package-bases --ignore-missing-imports --python-executable <suite-venv> app tests` reported 71 RZ errors. The same check from marketing-intelligence for `auth_service marketing gateway tests scripts` reported 10 marketing errors, down from 18. Baseline and current logs are in /tmp/rz-release-static.qUhpUi.

## Resume

Item 7 is open because repository-wide lint and type checks still report findings. No verified production backup has been restored or checked. Scratch rollout used disposable PostgreSQL with synthetic data, independent of the production migration gate. Real Google sign-in and MFA remain the owner's final live check.
