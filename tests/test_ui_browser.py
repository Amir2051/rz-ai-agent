"""Browser-level regression tests for MIA's durable conversation and run history UI.

Requires the Playwright Python package and its Chromium browser:
    pip install -r requirements-dev.txt
    playwright install chromium
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from playwright.sync_api import Browser, Page, Route, sync_playwright


ROOT = Path(__file__).resolve().parents[1]
UI_HTML = (ROOT / "app" / "ui.html").read_text(encoding="utf-8")


class _UIHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
        body = UI_HTML.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_: Any) -> None:
        pass


@pytest.fixture(scope="module")
def ui_origin():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _UIHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


@pytest.fixture
def browser_page(ui_origin: str):
    with sync_playwright() as playwright:
        installed = sorted(Path.home().joinpath(".cache/ms-playwright").glob("chromium-*/chrome-linux64/chrome"))
        executable = playwright.chromium.executable_path
        if not Path(executable).exists() and installed:
            executable = str(installed[0])
        browser: Browser = playwright.chromium.launch(headless=True, executable_path=executable)
        page = browser.new_page()
        page_errors: list[str] = []
        page.on("pageerror", lambda error: page_errors.append(str(error)))
        # Keep this test hermetic: no external fonts, providers, or live APIs.
        page.route("https://**/*", lambda route: route.abort())
        yield page, page_errors, ui_origin
        browser.close()


def _api_fixture(page: Page, responses: dict[str, tuple[int, dict[str, Any]]]) -> None:
    def handle(route: Route) -> None:
        from urllib.parse import unquote, urlparse

        path = unquote(urlparse(route.request.url).path)
        status, payload = responses.get(path, (200, {"status": "ok"}))
        route.fulfill(status=status, content_type="application/json", body=json.dumps(payload))

    page.route("**/v1/**", handle)


def _boot(page: Page, origin: str) -> None:
    page.goto(origin, wait_until="domcontentloaded")


def test_saved_conversation_history_restores_transcript_in_browser(browser_page) -> None:
    page, page_errors, origin = browser_page
    session_id = "acct:history-test"
    _api_fixture(
        page,
        {
            "/v1/auth/mode": (200, {"mode": "account"}),
            "/v1/auth/me": (401, {"detail": "Sign in required"}),
            "/v1/user/preferences": (200, {"preferences": {}}),
            "/v1/conversations": (
                200,
                {
                    "status": "ok",
                    "count": 1,
                    "sessions": [session_id],
                    "items": [{"session_id": session_id, "updated_at": 1794500000, "model": "test-model"}],
                },
            ),
            f"/v1/conversations/{session_id}": (
                200,
                {
                    "status": "ok",
                    "conversation": {
                        "session_id": session_id,
                        "model": "test-model",
                        "history": [
                            {"role": "user", "content": "Persisted question from browser regression"},
                            {"role": "assistant", "content": "Persisted answer from browser regression"},
                        ],
                    },
                },
            ),
        },
    )
    _boot(page, origin)

    page.locator("#conversationHistory").click()
    page.get_by_role("button", name="Open conversation").wait_for()
    page.get_by_role("button", name="Open conversation").click()

    page.locator(".bubble.user").filter(has_text="Persisted question from browser regression").wait_for(timeout=5000)
    page.locator(".bubble.assistant p").filter(has_text="Persisted answer from browser regression").wait_for(timeout=5000)
    assert page.locator("#model").inner_text() == "test-model · Restored"
    assert not page_errors, "Uncaught browser errors: " + "; ".join(page_errors)


def test_agent_run_history_displays_result_and_tool_events_in_browser(browser_page) -> None:
    page, page_errors, origin = browser_page
    _api_fixture(
        page,
        {
            "/v1/auth/mode": (200, {"mode": "account"}),
            "/v1/auth/me": (401, {"detail": "Sign in required"}),
            "/v1/agent/runs": (
                200,
                {
                    "status": "ok",
                    "count": 1,
                    "runs": [
                        {
                            "id": "run-browser-test",
                            "status": "completed",
                            "request": "Investigate browser-regression.example",
                            "final_response": "Two findings were verified by the test fixture.",
                            "error": None,
                            "updated_at": 1794500000,
                            "events": [
                                {"type": "tool_started", "tool": "mia_domain_lookup"},
                                {"type": "tool_completed", "tool": "mia_domain_lookup"},
                            ],
                        }
                    ],
                },
            ),
        },
    )
    _boot(page, origin)

    page.locator('[data-capability="runs"]').click()
    page.get_by_text("COMPLETED", exact=True).wait_for()
    page.get_by_text("Investigate browser-regression.example", exact=True).wait_for()
    page.get_by_text("View result and activity", exact=True).click()

    card = page.locator(".md-item").last
    card.get_by_text("Two findings were verified by the test fixture.", exact=True).wait_for()
    assert "tool started · mia_domain_lookup" in card.inner_text()
    assert "tool completed · mia_domain_lookup" in card.inner_text()
    assert not page_errors, "Uncaught browser errors: " + "; ".join(page_errors)


def test_run_history_recovery_requires_confirmation_and_reports_no_replay(browser_page) -> None:
    page, page_errors, origin = browser_page
    recovery_calls: list[dict[str, Any]] = []
    _api_fixture(
        page,
        {
            "/v1/auth/mode": (200, {"mode": "account"}),
            "/v1/auth/me": (401, {"detail": "Sign in required"}),
            "/v1/agent/runs": (
                200,
                {
                    "status": "ok",
                    "count": 1,
                    "runs": [
                        {
                            "id": "run-recovery-browser",
                            "status": "running",
                            "request": "Resume review after interruption",
                            "final_response": None,
                            "error": None,
                            "updated_at": 1794500000,
                            "events": [{"type": "tool_started", "tool": "mia_external_action", "call_id": "call-unknown"}],
                        }
                    ],
                },
            ),
        },
    )

    def recover(route: Route) -> None:
        recovery_calls.append({"method": route.request.method, "body": route.request.post_data})
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"status": "ok", "count": 1, "recovered": [{"id": "run-recovery-browser", "status": "interrupted", "uncertain_tools": ["mia_external_action"]}]}),
        )

    page.route("**/v1/agent/runs/recover", recover)
    _boot(page, origin)
    page.locator('[data-capability="runs"]').click()
    page.get_by_text("RUNNING", exact=True).wait_for()

    accept_recovery = [False]
    page.on("dialog", lambda dialog: dialog.accept() if accept_recovery[0] else dialog.dismiss())
    page.get_by_role("button", name="Find interrupted runs").click()
    assert recovery_calls == []

    accept_recovery[0] = True
    page.get_by_role("button", name="Find interrupted runs").click()
    page.get_by_text("1 stale run(s) marked for review. No tools were replayed.", exact=True).wait_for()
    assert len(recovery_calls) == 1
    assert recovery_calls[0]["method"] == "POST"
    assert not page_errors, "Uncaught browser errors: " + "; ".join(page_errors)


def test_investigation_records_browser_viewer_opens_saved_evidence_safely(browser_page) -> None:
    page, page_errors, origin = browser_page
    record_id = "a" * 32
    hostile_text = '<img src=x onerror="window.__miaXss=true">'
    _api_fixture(
        page,
        {
            "/v1/auth/mode": (200, {"mode": "account"}),
            "/v1/auth/me": (401, {"detail": "Sign in required"}),
            "/v1/investigations": (
                200,
                {
                    "status": "ok",
                    "count": 1,
                    "records": [
                        {
                            "id": record_id,
                            "session_id": "acct:case-1",
                            "run_id": "investigation-1",
                            "record_type": "six_phase_investigation",
                            "created_at": 1794500000,
                        }
                    ],
                },
            ),
            f"/v1/investigations/{record_id}": (
                200,
                {
                    "status": "ok",
                    "record": {
                        "id": record_id,
                        "record_type": "six_phase_investigation",
                        "run_id": "investigation-1",
                        "payload": {
                            "findings": [
                                {
                                    "finding_id": "F-001",
                                    "classification": "observed",
                                    "evidence": {"source": "fixture", "value": hostile_text},
                                }
                            ]
                        },
                    },
                },
            ),
        },
    )
    _boot(page, origin)

    page.locator('[data-capability="investigations"]').click()
    page.get_by_text("SIX PHASE INVESTIGATION", exact=True).wait_for()
    page.get_by_role("button", name="Open report & evidence").click()
    evidence = page.locator(".md-item pre")
    evidence.wait_for()
    rendered = json.loads(evidence.inner_text())
    assert rendered["payload"]["findings"][0]["finding_id"] == "F-001"
    assert rendered["payload"]["findings"][0]["evidence"]["value"] == hostile_text
    assert page.locator(".md-item img").count() == 0
    assert page.evaluate("window.__miaXss === true") is False
    assert not page_errors, "Uncaught browser errors: " + "; ".join(page_errors)
