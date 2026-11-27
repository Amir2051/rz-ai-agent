# Release handoff

Worktree: /home/ronzoro/dev-terminal/rz-ai-marketing-integration on Kali.
Branch: feature/marketing-intelligence. Item 1 is complete at ea4d610.
Item 2 is in progress. Items 3–10 remain pending under the original release gates.
No branch was pushed, no PR was opened, and no production database or service was contacted.

## Verified results

- Marketing: from marketing-intelligence run /home/ronzoro/dev-terminal/safenestt-marketing-intelligence/.venv-auth/bin/python scripts/offline_pytest.py -o 'pythonpath=. /home/ronzoro/dev-terminal/safenestt-marketing-intelligence/vendor/marketing-data-hub/src' tests -q. Output: 107 passed, 30 subtests passed in 0.81s.
- PostgreSQL: with a disposable PostgreSQL 18 cluster bound to loopback and RZ_TEST_POSTGRES_URL pointing at rzai_integration_test as rzai_test, run python3 -m pytest -q --tb=short tests/test_conversation_postgres_adapter.py tests/test_conversation_postgres_integration.py. Before: 2 failed, 7 passed. After: 9 passed, 133 warnings in 0.42s. Adapter assertions now verify epoch TTL and owner parameters. The integration test verifies both the session ID list and conversation metadata.
- Layout and telemetry: env -i HOME="$HOME" PATH="$PATH" python3 -m pytest -q --disable-warnings tests/test_telemetry_widget.py tests/test_ui_layout.py. Output: 7 passed, 981 warnings in 0.93s. Restored the missing style boundary and dark selector for existing tactical CSS.
- Inline JavaScript passed node --check after repairing the navigation wrapper and removing stray branch-name expressions. A loopback Playwright smoke check with mocked APIs reported no uncaught page errors after restoring the missing microphone control. The conversation history control was restored. Full browser coverage is still pending.
- Prior handoff reported CI at 299 passed and 10 failed. The local python3 -m pytest -q tests run with disposable PostgreSQL advanced into the browser tests and yielded no final count. See /tmp/rz-release.GogL5U/pytest.log on Kali. Separate browser-only pytest and a Playwright reproduction also stalled. Start with a bounded per-test run and instrument the sequence of Playwright calls to pinpoint the stall. Match CI Python 3.12 and Playwright/Chromium before judging environment-dependent failures.
- python3 -m pytest -q --tb=short --disable-warnings tests/test_telemetry_widget.py tests/test_tool_center.py tests/test_ui_layout.py, before the tactical CSS fix, returned 2 failed and 10 passed. The outstanding image approval test expects 403 until opt-in, then a pending approval and prompt validation. The registry currently marks image generation unavailable, returning 503 before permissions are checked. Trace the registry regression against the existing image adapter and preserve authorization, approval, execution gates and spending constraints.

## Resume

Finish item 2 by diagnosing the browser and approval failures, then rerun all RZ tests without skips or weaker assertions. Use disposable PostgreSQL on a fresh loopback port and a clean environment. Continue with item 3 only after recording the full result. Current auth changes cover only part of the flow. Complete browser sign-in and MFA, implement recently verified password-plus-MFA proof bound to owner linking and OAuth state, and run the requested negative regression tests.

No scratch restore, migration, rollback or backup verification was performed. Item 8 requires access to a fresh restorable production backup and a successful scratch migration and rollback before production. Keep real Google sign-in and MFA as the owner's final live check after release verification.
