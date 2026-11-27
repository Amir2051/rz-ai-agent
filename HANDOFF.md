# Release handoff

Worktree: /home/ronzoro/dev-terminal/rz-ai-marketing-integration on Kali.
Branch: feature/marketing-intelligence. Items 1–4 are done. Resume at item 5 in TASKLIST.md.
No branch was pushed, no PR was opened, and no production database or service was contacted.

## Verified results

- Item 1 marketing suite: 107 passed, 30 subtests passed. Exact command in TASKLIST.md.
- Item 2 full RZ AI suite: 309 passed, 1 warning in 20.23s. Prior CI baseline: 299 passed, 10 failed. Installed requirements-dev.txt in an isolated Python 3.13 environment and ran `python -m pytest -q --tb=short --disable-warnings tests` with dotenv loading disabled and an isolated PostgreSQL 18 test cluster bound to loopback. Test output on Kali: /tmp/rz-release.RC9SKE/pytest.log. No tests skipped. Python 3.12 CI remains unverified.
- Fixed damaged chat-composer markup, missing conversation status, run-history initialization, and stale image tool registry metadata. Image generation still requires per-account opt-in and an explicit approval for each action. The tests used fixtures and no paid provider requests.
- Kept conversation owner validation and the external 404 check. Updated tests to match early storage-level owner rejection and isolate Tool Center imports so test API keys cannot leak across fixtures.

- Items 3–4: Offline PostgreSQL HTTP flow passed against migrations 001–012 and against the equivalent scratch rollout from 001+002 using 013. The provider exchange was simulated using signed assertions and the real claim verifier. Chromium also verified the sign-in, MFA, and sign-out controls using intercepted responses. The offline marketing suite passed 107 tests and 30 subtests.

## Resume

Item 5 is secure owner-account linking after existing password and MFA proof. The current owner restriction remains in effect. Complete the remaining negative auth tests, full checks, verification of a fresh restorable backup, and scratch migration and rollback before moving toward production. Real Google sign-in and MFA remain the owner's final live check.
