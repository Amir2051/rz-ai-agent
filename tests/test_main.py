import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from fastapi import HTTPException  # noqa: E402
from unittest import mock  # noqa: E402


@pytest.fixture()
def app():
    os.environ["RZ_API_KEY"] = "test-key"
    os.environ["OPENROUTER_API_KEY"] = "real"
    os.environ["HERMES_API_KEY"] = "hermes-secret"
    os.environ["HERMES_MODEL"] = "upstage/solar-mini4"
    os.environ["HERMES_ENABLED"] = "1"
    sys.modules.pop("app.main", None)
    import app.main as m  # noqa: F401

    yield m

    with m._store_lock:
        m._conversations.clear()
    for key in ("RZ_API_KEY", "OPENROUTER_API_KEY", "HERMES_API_KEY", "HERMES_MODEL", "HERMES_ENABLED"):
        os.environ.pop(key, None)


def test_placeholder_openrouter_skips_to_502(app):
    try:
        app._complete_message("s", "hello", "sess1", None, None, None)
    except HTTPException as exc:
        assert exc.status_code == 502
    else:
        raise AssertionError("expected a 502 when no provider is usable")


def test_openrouter_conversation_memory(app, monkeypatch):
    calls = []

    def fake_openrouter(messages, model, temperature, max_tokens):
        calls.append(messages)
        return {"choices": [{"message": {"content": "hello back"}}]}

    monkeypatch.setattr(app, "_call_openrouter", fake_openrouter)
    resp = app._complete_message("s", "hello", "sess1", None, None, None)

    assert resp.status == "ok"
    assert resp.reply == "hello back"
    assert resp.session_id == "sess1"
    assert resp.history_length == 2
    assert resp.provider == "openrouter"
    # a fresh session does not leak history
    r2 = app._complete_message("s", "second", "sess1", None, None, None)
    assert r2.history_length == 4


def test_conversation_threading_keeps_context(app, monkeypatch):
    def fake(messages, model, temperature, max_tokens):
        return {"choices": [{"message": {"content": "prior: " + messages[-1]["content"]}}]}

    with mock.patch.object(app, "_call_openrouter", fake):
        app._complete_message("s", "hello", "sess1", None, None, None)
        resp = app._complete_message("s", "what did I just say", "sess1", None, None, None)

    assert resp.reply == "prior: what did I just say"
    assert resp.history_length == 4


def test_reset_clears_conversation(app, monkeypatch):
    def fake(messages, model, temperature, max_tokens):
        return {"choices": [{"message": {"content": "ok"}} ]}

    with mock.patch.object(app, "_call_openrouter", fake):
        app._complete_message("s", "reset this", "sess1", None, None, None)
        with app._store_lock:
            app._conversations.pop("sess1", None)
        resp = app._complete_message("s", "what did I just say", "sess1", None, None, None)

    assert resp.reply == "ok"
    assert resp.history_length == 2
    assert "prior" not in resp.reply  # reset removed prior context


def test_does_not_fallback_to_unverified_hermes_when_openrouter_is_configured(app, monkeypatch):
    def fake_openrouter(messages, model, temperature, max_tokens):
        raise RuntimeError("ZDR endpoint unavailable")

    def fake_hermes(messages, model, temperature, max_tokens):
        raise AssertionError("must not route to Hermes after OpenRouter failure")

    monkeypatch.setattr(app, "HERMES_ENABLED", True)
    with mock.patch.object(app, "_call_openrouter", fake_openrouter), \
         mock.patch.object(app, "_call_hermes", fake_hermes):
        with pytest.raises(HTTPException) as exc:
            app._complete_message(
                "s", "from hermes", "sess2", "example/openrouter-model", None, None
            )

    assert exc.value.status_code == 502
    assert "ZDR endpoint unavailable" in exc.value.detail


def test_hermes_can_be_primary_when_openrouter_is_not_configured(app, monkeypatch):
    monkeypatch.setattr(app, "OPENROUTER_API_KEY", "")
    monkeypatch.setattr(app, "HERMES_ENABLED", True)

    def fake_hermes(messages, model, temperature, max_tokens):
        assert model == app.HERMES_MODEL
        return {"choices": [{"message": {"content": "hermes primary reply"}}]}

    with mock.patch.object(app, "_call_hermes", fake_hermes):
        resp = app._complete_message("s", "hello", "sess3", None, None, None)

    assert resp.reply == "hermes primary reply"
    assert resp.provider == "hermes"
    assert resp.model == app.HERMES_MODEL


def test_openrouter_endpoint_accepts_regional_api_base_urls(app):
    assert app._openrouter_chat_completions_url(
        "https://us.openrouter.ai/api/v1"
    ) == "https://us.openrouter.ai/api/v1/chat/completions"
    assert app._openrouter_chat_completions_url(
        "https://eu.openrouter.ai/api/v1/"
    ) == "https://eu.openrouter.ai/api/v1/chat/completions"


def test_openrouter_endpoint_preserves_full_chat_url(app):
    endpoint = "https://openrouter.ai/api/v1/chat/completions"
    assert app._openrouter_chat_completions_url(endpoint) == endpoint


def test_openrouter_endpoint_accepts_host_only(app):
    assert app._openrouter_chat_completions_url(
        "https://us.openrouter.ai"
    ) == "https://us.openrouter.ai/api/v1/chat/completions"


def test_task_model_routing_uses_configured_coding_model(app, monkeypatch):
    monkeypatch.setattr(app, "OPENROUTER_CODING_MODEL", "example/coding-model")
    monkeypatch.setattr(app, "OPENROUTER_ANALYSIS_MODEL", "")
    assert app._select_model_for_task("Please debug this Python traceback") == "example/coding-model"


def test_task_model_routing_uses_configured_analysis_model(app, monkeypatch):
    monkeypatch.setattr(app, "OPENROUTER_CODING_MODEL", "")
    monkeypatch.setattr(app, "OPENROUTER_ANALYSIS_MODEL", "example/analysis-model")
    assert app._select_model_for_task("Compare these options and analyze the tradeoffs") == "example/analysis-model"


def test_task_model_routing_preserves_default_and_explicit_override(app, monkeypatch):
    monkeypatch.setattr(app, "OPENROUTER_MODEL", "example/default-model")
    monkeypatch.setattr(app, "OPENROUTER_CODING_MODEL", "example/coding-model")
    assert app._select_model_for_task("Tell me a joke") == "example/default-model"
    assert app._select_model_for_task("Debug this code", "example/manual-model") == "example/manual-model"


def test_mcp_initialize_and_tools_list_require_auth(app, monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setattr(app, "DATABASE_AUTH_ENABLED", False)
    monkeypatch.setattr(app, "API_KEY", "test-key")
    client = TestClient(app.app)
    unauthorized = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    assert unauthorized.status_code == 401

    initialized = client.post("/mcp", headers={"Authorization": "Bearer test-key"},
                              json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    assert initialized.status_code == 200
    assert initialized.json()["result"]["serverInfo"]["name"] == "rz-ai-agent"

    listed = client.post("/mcp", headers={"Authorization": "Bearer test-key"},
                         json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    assert listed.status_code == 200
    assert {tool["name"] for tool in listed.json()["result"]["tools"]} == {
        "chat", "list_conversations", "reset_conversation"
    }


def test_mcp_tools_call_executes_list_and_chat(app, monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setattr(app, "DATABASE_AUTH_ENABLED", False)
    monkeypatch.setattr(app, "API_KEY", "test-key")
    client = TestClient(app.app)
    headers = {"Authorization": "Bearer test-key"}

    listed = client.post("/mcp", headers=headers,
                         json={"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                               "params": {"name": "list_conversations", "arguments": {}}})
    assert listed.status_code == 200
    assert listed.json()["result"]["isError"] is False
    assert '"count": 0' in listed.json()["result"]["content"][0]["text"]

    fake_response = app.ChatResponse(status="ok", reply="tool reply", session_id="local:abc",
                                     model="test-model", provider="test", history_length=2,
                                     updated_at="2026-10-21T00:00:00Z")
    monkeypatch.setattr(app, "_complete_message", lambda **kwargs: fake_response)
    response = client.post("/mcp", headers=headers,
                           json={"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                                 "params": {"name": "chat", "arguments": {"message": "hello"}}})
    assert response.status_code == 200
    assert response.json()["result"]["isError"] is False
    assert "tool reply" in response.json()["result"]["content"][0]["text"]


def test_rest_tool_and_capabilities_endpoints(app, monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setattr(app, "DATABASE_AUTH_ENABLED", False)
    monkeypatch.setattr(app, "API_KEY", "test-key")
    client = TestClient(app.app)
    headers = {"Authorization": "Bearer test-key"}
    caps = client.get("/v1/capabilities", headers=headers)
    assert caps.status_code == 200
    assert caps.json()["mcp"]["endpoint"] == "/mcp"
    tools = client.get("/v1/tools", headers=headers)
    assert tools.status_code == 200
    assert len(tools.json()["tools"]) == 3
    call = client.post("/v1/tools/list_conversations", headers=headers, json={})
    assert call.status_code == 200
    assert call.json()["result"]["count"] == 0


def test_tool_calls_cannot_access_other_users_conversations(app, monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setattr(app, "DATABASE_AUTH_ENABLED", False)
    monkeypatch.setattr(app, "API_KEY", "test-key")
    client = TestClient(app.app)
    response = client.post("/mcp", headers={"Authorization": "Bearer test-key"},
                           json={"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                                 "params": {"name": "reset_conversation",
                                            "arguments": {"session_id": "other-user:secret"}}})
    assert response.status_code == 200
    assert response.json()["result"]["isError"] is True
    assert "not found" in response.json()["result"]["content"][0]["text"].lower() or \
           "does not belong" in response.json()["result"]["content"][0]["text"].lower()


def test_api_token_scope_and_revocation_validation(app, monkeypatch):
    import app.auth as auth

    class FakeResult:
        def __init__(self, row):
            self.row = row
        def fetchone(self):
            return self.row

    class FakeConnection:
        def __init__(self, row):
            self.row = row
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def execute(self, query, params=()):
            if query.lstrip().startswith("UPDATE auth_api_tokens"):
                return FakeResult(("token-id",))
            return FakeResult(self.row)

    monkeypatch.setattr(auth, "_db", lambda: FakeConnection(("user-1", "user@example.com", ["mcp:read"])))
    monkeypatch.setattr(auth, "_token_hash", lambda token: "hashed-token")
    with pytest.raises(HTTPException) as exc:
        auth.get_user_from_bearer("Bearer rzat_example", "mcp:call")
    assert exc.value.status_code == 403
    assert "mcp:call" in exc.value.detail

    monkeypatch.setattr(auth, "_db", lambda: FakeConnection(("user-1", "user@example.com", ["mcp:read", "mcp:call"])))
    user = auth.get_user_from_bearer("Bearer rzat_example", "mcp:call")
    assert user["id"] == "user-1"
    assert user["auth_type"] == "api_token"


def test_mcp_initialize_negotiates_supported_protocol_version(app, monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setattr(app, "DATABASE_AUTH_ENABLED", False)
    monkeypatch.setattr(app, "API_KEY", "test-key")
    client = TestClient(app.app)
    response = client.post("/mcp", headers={"Authorization": "Bearer test-key"},
                           json={"jsonrpc": "2.0", "id": 10, "method": "initialize",
                                 "params": {"protocolVersion": "unsupported-version"}})
    assert response.status_code == 200
    assert response.json()["result"]["protocolVersion"] == "2025-03-26"
