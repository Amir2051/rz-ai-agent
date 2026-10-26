import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture()
def app(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("OLLAMA_BASE_URL", "")
    monkeypatch.setenv("RZ_API_KEY", "test-key")
    monkeypatch.setenv("OPENROUTER_API_KEY", "real")
    monkeypatch.setenv("NOUS_OAUTH_STATE_JSON", "")
    monkeypatch.setenv("NOUS_OAUTH_ENCRYPTION_KEY", "")
    monkeypatch.setenv("HERMES_MODEL", "upstage/solar-mini4")
    sys.modules.pop("app.main", None)
    sys.modules.pop("app.auth", None)
    import app.main as module
    yield module
    with module._store_lock:
        module._conversations.clear()


def test_firecrawl_tools_are_registered_for_the_agent(app):
    names = {item["function"]["name"] for item in app.MIA_AGENT_TOOLS}
    assert {"firecrawl_search", "firecrawl_scrape"} <= names


def test_neural_network_requests_route_to_agent_and_coding_model(app, monkeypatch):
    monkeypatch.setattr(app, "OPENROUTER_CODING_MODEL", "example/coding-model")
    assert app._should_use_mia_agent_tools("Can you help me build a neural network?")
    assert app._should_use_mia_agent_tools("Write a PyTorch training loop")
    assert app._select_model_for_task("Can you help me build a neural network?") == "example/coding-model"


def test_firecrawl_dispatch_executes_bounded_adapter_and_parses_json(app, monkeypatch):
    calls = []

    async def fake_execute(name, args, api_key):
        calls.append((name, args, api_key))
        return json.dumps({"provider": "firecrawl", "results": [{"url": "https://docs.example.org"}]})

    monkeypatch.setattr(app, "FIRECRAWL_API_KEY", "test-firecrawl-key")
    monkeypatch.setattr(app, "execute_firecrawl_tool", fake_execute)
    result = app._dispatch_mia_agent_tool("firecrawl_search", {"query": "neural network official documentation", "limit": 2}, "user-1", "user-1:session")
    assert result["provider"] == "firecrawl"
    assert calls == [("firecrawl_search", {"query": "neural network official documentation", "limit": 2}, "test-firecrawl-key")]


def test_firecrawl_dispatch_fails_closed_when_key_is_missing(app, monkeypatch):
    async def must_not_execute(*_args):
        raise AssertionError("Firecrawl must not be called without its configured key")

    monkeypatch.setattr(app, "FIRECRAWL_API_KEY", "")
    monkeypatch.setattr(app, "execute_firecrawl_tool", must_not_execute)
    result = app._dispatch_mia_agent_tool("firecrawl_search", {"query": "neural network docs"}, "user-1", "user-1:session")
    assert result["completed"] is False
    assert "not configured" in result["error"].lower()


def test_firecrawl_tool_arguments_are_server_validated(app):
    app._validate_mia_agent_tool_args("firecrawl_search", {"query": "neural network docs", "limit": 3})
    try:
        app._validate_mia_agent_tool_args("firecrawl_search", {"query": "x", "limit": 99})
    except ValueError:
        pass
    else:
        raise AssertionError("out-of-range Firecrawl limits must be rejected")
    try:
        app._validate_mia_agent_tool_args("firecrawl_search", {"query": "neural network docs", "limit": 2, "extra": "unexpected"})
    except ValueError:
        pass
    else:
        raise AssertionError("unexpected Firecrawl tool arguments must be rejected")


class _FakeFirecrawlResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.is_success = 200 <= status_code < 300

    def json(self):
        return self._payload


class _FakeFirecrawlClient:
    last_url = None
    last_headers = None
    last_json = None
    payload = {"success": True, "data": [{"title": "Docs", "url": "https://docs.example.org", "description": "Official documentation"}]}

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def post(self, url, **kwargs):
        type(self).last_url = url
        type(self).last_headers = kwargs.get("headers")
        type(self).last_json = kwargs.get("json")
        return _FakeFirecrawlResponse(type(self).payload)


def test_firecrawl_adapter_search_uses_v2_api_and_keeps_key_server_side(monkeypatch):
    from app import firecrawl_tools
    monkeypatch.setattr(firecrawl_tools.httpx, "AsyncClient", _FakeFirecrawlClient)
    result = asyncio.run(firecrawl_tools.execute("firecrawl_search", {"query": "neural network documentation", "limit": 2}, "private-test-key"))
    payload = json.loads(result)
    assert _FakeFirecrawlClient.last_url == "https://api.firecrawl.dev/v2/search"
    assert _FakeFirecrawlClient.last_headers["Authorization"] == "Bearer private-test-key"
    assert _FakeFirecrawlClient.last_json == {"query": "neural network documentation", "limit": 2}
    assert payload["result_count"] == 1


def test_firecrawl_adapter_blocks_private_targets_before_network(monkeypatch):
    from app import firecrawl_tools
    monkeypatch.setattr(firecrawl_tools.httpx, "AsyncClient", _FakeFirecrawlClient)
    before = _FakeFirecrawlClient.last_url
    result = asyncio.run(firecrawl_tools.execute("firecrawl_scrape", {"url": "https://127.0.0.1/admin"}, "private-test-key"))
    assert "rejected" in result.lower()
    assert _FakeFirecrawlClient.last_url == before
