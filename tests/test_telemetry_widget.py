"""The main page ships the tactical theme and the Swarm Telemetry widget, wired to real endpoints."""
import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture()
def page(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("RZ_API_KEY", "test-key")
    monkeypatch.setenv("RZ_LOCAL_DATA_PATH", str(tmp_path / "ui.sqlite3"))
    for name in ("app.main", "app.slack_channel", "app.tools_hub"):
        sys.modules.pop(name, None)
    import app.main as module
    html = TestClient(module.app).get("/").text
    yield html
    sys.modules.pop("app.main", None)


def test_widget_opens_from_header_and_is_hidden_by_default(page):
    header = page[page.index('<header class="head">'):page.index("</header>")]
    assert 'id="miaTelemetryToggle"' in header and 'aria-controls="miaTelemetry"' in header
    assert '<section id="miaTelemetry" role="dialog"' in page and 'aria-labelledby="txTitle" hidden>' in page
    for node in ("ingest", "vector", "reason", "action"):
        assert page.count(f'data-node="{node}"') == 1


def test_widget_reads_only_real_account_endpoints(page):
    script = page[page.index("/* Swarm Telemetry widget: real data"):]
    for endpoint in ("/health", "/v1/agent/runs?limit=20", "/v1/memory?limit=50", "/v1/tasks?limit=50", "/v1/conversations"):
        assert f"api('{endpoint}')" in script
    assert "SIGN IN FOR LIVE DATA" in script
    assert "Math.random" not in script


def test_tactical_theme_is_scoped_to_dark_mode(page):
    theme = page[page.index('<style id="mia-tactical-hud">'):]
    theme = theme[:theme.index("</style>")]
    rules = [line for line in theme.splitlines() if line.startswith("html") and "{" in line]
    assert rules and all(r.startswith('html[data-mia-theme="dark"]') for r in rules)
