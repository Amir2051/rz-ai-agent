"""Outside MCP servers: config validation, read-only vs write tools, approvals, and operator-only access."""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TOOLS = [
    {"name": "list_cases", "description": "List cases", "annotations": {"readOnlyHint": True},
     "inputSchema": {"type": "object", "properties": {"limit": {"type": "integer"}}}},
    {"name": "delete_case", "description": "Delete a case",
     "inputSchema": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]}},
]


class FakeResponse:
    def __init__(self, body, content_type="application/json", session="sess-1"):
        self.body = body.encode() if isinstance(body, str) else body
        self.headers = {"Content-Type": content_type, "Mcp-Session-Id": session}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self, n=-1):
        return self.body


class FakeServer:
    def __init__(self, sse=False):
        self.sse = sse
        self.calls = []
        self.headers = []

    def __call__(self, req, timeout):
        msg = json.loads(req.data.decode())
        self.headers.append(dict(req.header_items()))
        self.calls.append(msg.get("method"))
        if "id" not in msg:
            return FakeResponse("")
        if msg["method"] == "initialize":
            result = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}}}
        elif msg["method"] == "tools/list":
            result = {"tools": TOOLS}
        elif msg["method"] == "tools/call":
            result = {"content": [{"type": "text", "text": f"ran {msg['params']['name']} {json.dumps(msg['params']['arguments'])}"}]}
        else:
            result = {}
        body = json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": result})
        if self.sse:
            return FakeResponse(f"event: message\ndata: {body}\n\n", "text/event-stream")
        return FakeResponse(body)


@pytest.fixture()
def app(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("RZ_API_KEY", "test-key")
    monkeypatch.setenv("RZ_LOCAL_DATA_PATH", str(tmp_path / "mcp.sqlite3"))
    monkeypatch.setenv("SAFE_MCP_TOKEN", "secret-token")
    for name in ("app.main", "app.tools_hub", "app.mcp_client", "app.slack_channel"):
        sys.modules.pop(name, None)
    import app.main as module
    from app import mcp_client
    mcp_client._state.clear()
    yield module
    mcp_client._state.clear()
    sys.modules.pop("app.main", None)


def configure(monkeypatch, allow_write=False):
    monkeypatch.setenv("RZ_MCP_SERVERS", json.dumps([
        {"name": "safenestt", "url": "https://mcp.example.com/mcp", "token_env": "SAFE_MCP_TOKEN", "allow_write": allow_write},
    ]))


def test_config_rejects_unsafe_entries(app, monkeypatch):
    from app import mcp_client
    monkeypatch.setenv("RZ_MCP_SERVERS", json.dumps([
        {"name": "plain", "url": "http://insecure.example.com/mcp"},
        {"name": "Bad Name", "url": "https://x.example.com/mcp"},
        {"name": "inline", "url": "https://x.example.com/mcp", "token_env": "sk-literal-token"},
        {"name": "good", "url": "https://x.example.com/mcp"},
        {"name": "good", "url": "https://dup.example.com/mcp"},
    ]))
    assert [s["name"] for s in mcp_client.configured_servers()] == ["good"]


@pytest.mark.parametrize("sse", [False, True])
def test_read_only_tools_exposed_and_token_sent(app, monkeypatch, sse):
    from app import mcp_client
    configure(monkeypatch)
    server = FakeServer(sse=sse)
    monkeypatch.setattr(mcp_client, "urlopen", server)
    names = [t["function"]["name"] for t in mcp_client.agent_tool_schemas()]
    assert names == ["mcp__safenestt__list_cases"]
    assert server.headers[0]["Authorization"] == "Bearer secret-token"
    result = mcp_client.agent_call("mcp__safenestt__list_cases", {"limit": 2}, "local")
    assert result["content"] == 'ran list_cases {"limit": 2}' and not result["is_error"]


def test_write_tool_needs_allow_write_and_then_approval(app, monkeypatch):
    from app import mcp_client, tools_hub
    configure(monkeypatch, allow_write=True)
    server = FakeServer()
    monkeypatch.setattr(mcp_client, "urlopen", server)
    names = [t["function"]["name"] for t in mcp_client.agent_tool_schemas()]
    assert "mcp__safenestt__delete_case" in names

    queued = mcp_client.agent_call("mcp__safenestt__delete_case", {"id": "c1"}, "local")
    assert queued["status"] == "approval_required" and queued["completed"] is False
    assert "tools/call" not in server.calls  # nothing ran yet

    from fastapi.testclient import TestClient
    app.app.dependency_overrides[app.require_app_user] = lambda: {"id": "local", "email": None}
    client = TestClient(app.app)
    decided = client.post(f"/v1/tool-center/approvals/{queued['approval_id']}/decision", json={"decision": "approve"})
    assert decided.status_code == 200, decided.text
    assert decided.json()["result"]["content"] == 'ran delete_case {"id": "c1"}'
    app.app.dependency_overrides.clear()


def test_write_tool_hidden_without_allow_write(app, monkeypatch):
    from app import mcp_client
    configure(monkeypatch, allow_write=False)
    monkeypatch.setattr(mcp_client, "urlopen", FakeServer())
    mcp_client.agent_tool_schemas()
    result = mcp_client.agent_call("mcp__safenestt__delete_case", {"id": "c1"}, "local")
    assert result["completed"] is False and "not available" in result["error"]


def test_non_operator_cannot_use_mcp(app, monkeypatch):
    from app import mcp_client
    configure(monkeypatch)
    server = FakeServer()
    monkeypatch.setattr(mcp_client, "urlopen", server)
    result = mcp_client.agent_call("mcp__safenestt__list_cases", {}, "someone-else")
    assert result["completed"] is False and server.calls == []


def test_agent_loop_offers_mcp_tools_to_operator_only(app, monkeypatch):
    from app import mcp_client
    configure(monkeypatch)
    monkeypatch.setattr(mcp_client, "urlopen", FakeServer())
    seen = {}

    def fake_completion(messages, model, temperature, max_tokens, tools=None, tool_choice="auto"):
        seen.setdefault("tools", [t["function"]["name"] for t in tools or []])
        return {"choices": [{"message": {"role": "assistant", "content": "done"}}]}

    app._complete_with_mia_agent_tools("sys", "use the mcp tools", "local:s1", None, None, None, completion_fn=fake_completion)
    assert "mcp__safenestt__list_cases" in seen["tools"]
    seen.clear()
    app._complete_with_mia_agent_tools("sys", "use the mcp tools", "someone-else:s1", None, None, None, completion_fn=fake_completion)
    assert not any(n.startswith("mcp__") for n in seen["tools"])


def test_unreachable_server_does_not_break_tool_list(app, monkeypatch):
    from urllib.error import URLError
    from app import mcp_client
    configure(monkeypatch)

    def down(req, timeout):
        raise URLError("down")

    monkeypatch.setattr(mcp_client, "urlopen", down)
    assert mcp_client.agent_tool_schemas() == []
    assert mcp_client.status()[0]["ok"] is False


def test_unreachable_server_is_skipped_for_a_while(app, monkeypatch):
    from urllib.error import URLError
    from app import mcp_client
    configure(monkeypatch)
    attempts = []

    def down(req, timeout):
        attempts.append(timeout)
        raise URLError("down")

    monkeypatch.setattr(mcp_client, "urlopen", down)
    mcp_client.agent_tool_schemas()
    mcp_client.agent_tool_schemas()
    assert len(attempts) == 1 and attempts[0] == mcp_client.LIST_TIMEOUT
