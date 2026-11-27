# Release handoff

Worktree: /home/ronzoro/dev-terminal/rz-ai-marketing-integration on Kali.
Branch: feature/marketing-intelligence. Items 1 and 2 are done. Resume at item 3 in TASKLIST.md.
No branch was pushed, no PR was opened, and no production database or service was contacted.

## Verified results

- Item 1 marketing suite: 107 passed, 30 subtests passed. Exact command in TASKLIST.md.
- Item 2 full RZ AI suite: 309 passed, 1 warning in 20.23s. Prior CI baseline: 299 passed, 10 failed. Installed requirements-dev.txt in an isolated Python 3.13 environment and ran `python -m pytest -q --tb=short --disable-warnings tests` with dotenv loading disabled and an isolated PostgreSQL 18 test cluster bound to loopback. Test output on Kali: /tmp/rz-release.RC9SKE/pytest.log. No tests skipped. Python 3.12 CI remains unverified.
- Fixed damaged chat-composer markup, missing conversation status, run-history initialization, and stale image tool registry metadata. Image generation still requires per-account opt-in and an explicit approval for each action. The tests used fixtures and no paid provider requests.
- Kept conversation owner validation and the external 404 check. Updated tests to match early storage-level owner rejection and isolate Tool Center imports so test API keys cannot leak across fixtures.

## Resume

Continue at item 3 only when requested. Finish browser Google sign-in and MFA, enforce proof of existing password and MFA before owner linking, and run the requested negative regressions. No scratch restore, migration, rollback or backup verification has been performed. Keep real Google sign-in and MFA as the owner's final live check after automated verification.
