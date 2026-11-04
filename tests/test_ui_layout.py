"""The main page keeps its task-based sidebar and every element its scripts rely on."""
import os
import re
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


def test_sidebar_is_grouped_by_task_in_order(page):
    nav = page[page.index('<nav class="mia-side-nav rm-nav"'):page.index("</nav>")]
    groups = re.findall(r'<div class="nav-kicker" id="nav-[a-z]+">([^<]+)</div>', nav)
    assert groups == ["Create", "Plan", "Knowledge", "Connect"]
    order = re.findall(r'data-mia-nav="([a-z]+)"|href="(/[a-z/]+)"', nav)
    flat = [a or b for a, b in order]
    assert flat == ["chat", "creator", "/business", "tasks", "research", "memory", "/skills", "/tools", "/settings/mcp"]
    foot = page[page.index('<div class="rm-side-foot">'):]
    assert foot.index('data-mia-nav="customize"') < foot.index('data-mia-nav="account"') < foot.index("</aside>")


def test_every_scripted_element_exists_exactly_once(page):
    ids = ["miaAppLayout", "miaSidebar", "miaNavToggle", "newChat", "miaQuickTools", "creatorLaunch", "capabilityFile",
           "chatPanel", "messages", "chatForm", "message", "send", "creatorWorkspace",
           "miaDrawer", "miaDrawerScrim", "miaDrawerClose", "miaDrawerTitle", "miaAccountSummary",
           "accountConnect", "accountEmail", "accountPassword", "loginAccount", "registerAccount", "logoutAccount",
           "apiKey", "check", "connection", "miaPreferences", "saveMiaPreferences", "loadMiaPreferences"]
    for element_id in ids:
        assert page.count(f'id="{element_id}"') == 1, element_id


def test_account_and_preferences_live_in_the_panel_not_the_sidebar(page):
    sidebar = page[page.index('id="miaSidebar"'):page.index("</aside>", page.index('id="miaSidebar"'))]
    assert 'id="accountConnect"' not in sidebar and 'id="miaPreferences"' not in sidebar
    drawer = page[page.index('id="miaDrawer"'):]
    assert 'data-pane="account"' in drawer and 'id="accountConnect"' in drawer
    assert 'data-pane="customize"' in drawer and 'id="miaPreferences"' in drawer


def test_theme_layer_supports_dark_and_light(page):
    theme = re.search(r'<style id="mia-refined-midnight">(.*?)</style>', page, re.S).group(1)
    assert ":root{" in theme and 'html[data-mia-theme="light"]{' in theme
    assert 'id="mia-blue-bridge-visible-motion"' not in page
