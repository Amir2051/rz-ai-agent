"""Every account may only reach its own chats and workspace.

Two signed-in accounts (Alice and Bob) are simulated by overriding the login
dependency, so each request runs exactly as that account would.
"""
import hashlib
import hmac
import json
import os
import sys
import time

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ALICE = {"id": "11111111-1111-4111-8111-111111111111", "email": "alice@example.com"}
BOB = {"id": "22222222-2222-4222-8222-222222222222", "email": "bob@example.com"}


@pytest.fixture()
def app(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("OLLAMA_BASE_URL", "")
    monkeypatch.setenv("RZ_API_KEY", "test-key")
    monkeypatch.setenv("OPENROUTER_API_KEY", "real")
    monkeypatch.setenv("RZ_LOCAL_DATA_PATH", str(tmp_path / "isolation.sqlite3"))
    monkeypatch.setenv("SLACK_SIGNING_SECRET", "unit-test-secret")
    monkeypatch.delenv("RZ_OPERATOR_EMAILS", raising=False)
    monkeypatch.delenv("RZ_OPERATOR_USER_IDS", raising=False)
    # Reload modules that hold a reference to app.main so they see this test's instance.
    for name in ("app.main", "app.slack_channel", "app.tools_hub"):
        sys.modules.pop(name, None)
    import app.main as module
    yield module
    module.app.dependency_overrides.clear()
    with module._store_lock:
        module._conversations.clear()
    sys.modules.pop("app.main", None)


class Accounts:
    """Switch which account the test client is signed in as."""

    def __init__(self, module):
        self.module = module
        self.client = TestClient(module.app)

    def as_user(self, user):
        for dep in (self.module.require_app_user, self.module.require_chat_user, self.module.require_mcp_user):
            self.module.app.dependency_overrides[dep] = (lambda u=user: {**u, "scopes": ["mcp:read", "mcp:call"]})
        return self.client


@pytest.fixture()
def accounts(app):
    return Accounts(app)


def _response(app, session_id):
    return app.ChatResponse(status="ok", session_id=session_id, model="test-model", provider="test",
                            request_id="r1", reply="ok", history_length=1, updated_at="now")


def _fake_reply(app, monkeypatch):
    seen = []

    def fake(system, user_content, session_id, model, temperature, max_tokens):
        seen.append(session_id)
        app._touch_session(session_id, "test-model")
        return _response(app, session_id)

    monkeypatch.setattr(app, "_complete_message", fake)
    return seen


# ------------------------------------------------------------------ chats

def test_chat_cannot_continue_another_accounts_conversation(app, accounts, monkeypatch):
    _fake_reply(app, monkeypatch)
    alice_session = accounts.as_user(ALICE).post("/v1/agent/session").json()["session_id"]
    assert accounts.as_user(ALICE).post("/v1/chat", json={"message": "hi", "session_id": alice_session}).status_code == 200

    bob = accounts.as_user(BOB)
    assert bob.post("/v1/chat", json={"message": "show me", "session_id": alice_session}).status_code == 403


def test_bare_session_name_is_always_scoped_to_the_caller(app, accounts, monkeypatch):
    seen = _fake_reply(app, monkeypatch)
    accounts.as_user(BOB).post("/v1/chat", json={"message": "hi", "session_id": "shared"})
    # A name without an owner prefix lands in Bob's own space, never a shared one.
    assert seen[-1] == f"{BOB['id']}:shared"


def test_saved_conversation_can_be_restored_only_by_its_owner(app, accounts):
    from app import capabilities
    session_id = f"{ALICE['id']}:restore-proof"
    history = [
        {"role": "user", "content": "Remember this question"},
        {"role": "assistant", "content": "This is the saved answer"},
    ]
    capabilities.save_conversation(ALICE["id"], session_id, history, "test-model")

    alice = accounts.as_user(ALICE)
    restored = alice.get(f"/v1/conversations/{session_id}")
    assert restored.status_code == 200
    assert restored.json()["conversation"]["history"] == history
    assert restored.json()["conversation"]["model"] == "test-model"
    listing = alice.get("/v1/conversations").json()
    assert session_id in listing["sessions"]
    assert any(item["session_id"] == session_id and item["model"] == "test-model" for item in listing["conversations"])
    home = alice.get("/")
    assert home.status_code == 200
    assert 'id="conversationHistory"' in home.text
    assert "miaRestoreConversation" in home.text

    bob = accounts.as_user(BOB)
    assert bob.get(f"/v1/conversations/{session_id}").status_code == 404
    assert bob.get("/v1/conversations").json()["sessions"] == []


def test_saved_conversation_restore_hides_expired_sessions(app, accounts, monkeypatch):
    from app import capabilities
    session_id = f"{ALICE['id']}:expired-proof"
    capabilities.save_conversation(ALICE["id"], session_id, [], "test-model")
    monkeypatch.setattr(app, "SESSION_TTL_SECONDS", 60)
    # Explicitly age the row in the isolated SQLite test database.
    conn, kind = capabilities._db()
    try:
        conn.execute("UPDATE rz_agent_conversations SET updated_at=? WHERE user_id=? AND session_id=?", (time.time()-120, ALICE["id"], session_id))
        conn.commit()
    finally:
        conn.close()
    assert accounts.as_user(ALICE).get(f"/v1/conversations/{session_id}").status_code == 404


def test_conversation_list_and_reset_only_touch_own_chats(app, accounts, monkeypatch):
    _fake_reply(app, monkeypatch)
    alice = accounts.as_user(ALICE)
    alice.post("/v1/chat", json={"message": "hi", "session_id": "a1"})
    alice_session = f"{ALICE['id']}:a1"

    bob = accounts.as_user(BOB)
    assert bob.get("/v1/conversations").json()["sessions"] == []
    assert bob.post(f"/v1/conversations/{alice_session}").status_code == 404
    assert alice_session in app._conversations  # Bob's attempt changed nothing

    assert accounts.as_user(ALICE).get("/v1/conversations").json()["sessions"] == [alice_session]


def test_agent_progress_rejects_another_accounts_session(app, accounts):
    alice_session = accounts.as_user(ALICE).post("/v1/agent/session").json()["session_id"]
    assert accounts.as_user(BOB).get("/v1/agent/progress", params={"session_id": alice_session}).status_code == 403


def test_mcp_chat_and_reset_refuse_another_accounts_session(app, accounts, monkeypatch):
    _fake_reply(app, monkeypatch)
    accounts.as_user(ALICE).post("/v1/chat", json={"message": "hi", "session_id": "a1"})
    alice_session = f"{ALICE['id']}:a1"
    bob = accounts.as_user(BOB)
    for tool, args in (("chat", {"message": "x", "session_id": alice_session}), ("reset_conversation", {"session_id": alice_session})):
        reply = bob.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": args}}).json()
        assert reply["result"]["isError"] is True
    assert alice_session in app._conversations


# ------------------------------------------------------------------ workspace data

def test_creator_projects_are_private_to_their_owner(app, accounts):
    alice = accounts.as_user(ALICE)
    pid = alice.post("/v1/creator/projects", json={"title": "Alice's book", "kind": "book"}).json()["project"]["id"]
    alice.put(f"/v1/creator/projects/{pid}/draft", json={"content": "secret chapter"})

    bob = accounts.as_user(BOB)
    assert bob.get("/v1/creator/projects").json()["projects"] == []
    assert bob.get(f"/v1/creator/projects/{pid}").status_code == 404
    assert bob.put(f"/v1/creator/projects/{pid}/draft", json={"content": "overwritten"}).status_code == 404
    assert bob.patch(f"/v1/creator/projects/{pid}", json={"title": "mine now"}).status_code == 404
    assert bob.get(f"/v1/creator/projects/{pid}/revisions").status_code == 404

    project = accounts.as_user(ALICE).get(f"/v1/creator/projects/{pid}").json()["project"]
    assert project["title"] == "Alice's book" and project["content"] == "secret chapter"


def test_memories_and_tasks_are_private_to_their_owner(app):
    caps = app.cap_services
    memory = caps.save_memory(ALICE["id"], "Alice prefers tea")
    task = caps.create_task(ALICE["id"], "Alice plan", ["step one"])

    assert caps.list_memories(BOB["id"], 50)["memories"] == []
    assert caps.list_tasks(BOB["id"], 50)["tasks"] == []
    assert caps.delete_memory(BOB["id"], memory["id"]) is False
    with pytest.raises(Exception):
        caps.update_task_step(BOB["id"], task["id"], 0, "completed")
    assert caps.list_memories(ALICE["id"], 50)["count"] == 1


# ------------------------------------------------------------------ operator-owned resources

def test_github_credential_is_operator_only(app, monkeypatch):
    from app import workspace_access
    calls = []

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self, *args):
            return json.dumps({"full_name": "Amir2051/rz-ai-agent"}).encode()

    monkeypatch.setattr(app, "urlopen", lambda req, timeout=None: calls.append(req.full_url) or FakeResponse())

    with workspace_access.acting_as(BOB["id"]):
        with pytest.raises(workspace_access.OperatorResourceDenied):
            app._github_request("/repos/Amir2051/rz-ai-agent")
        assert app._should_offer_github_tools("summarize the github repo rz-ai-agent") is False
    assert calls == []  # GitHub was never contacted for Bob

    # In local single-user mode the "local" principal is the operator.
    with workspace_access.acting_as("local"):
        app._github_request("/repos/Amir2051/rz-ai-agent")
    assert len(calls) == 1


def test_mcp_hides_github_tools_from_non_operators(app, accounts):
    bob = accounts.as_user(BOB)
    listed = bob.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).json()["result"]["tools"]
    assert not {t["name"] for t in listed} & app.GITHUB_TOOL_NAMES
    called = bob.post("/mcp", json={"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                                    "params": {"name": "github_get_repository", "arguments": {"repo": "rz-ai-agent"}}}).json()
    assert called["result"]["isError"] is True


def test_tool_center_github_tools_are_operator_only(app, accounts):
    bob = accounts.as_user(BOB)
    ids = {t["id"] for t in bob.get("/v1/tool-center/registry").json()["tools"]}
    assert not ids & {"github_read", "github_create_issue", "github_comment_pr"}
    assert bob.post("/v1/tool-center/invoke/github_read", json={"repo": "rz-ai-agent"}).status_code == 403
    assert bob.post("/v1/tool-center/approvals", json={
        "tool_id": "github_create_issue", "summary": "x", "action": {"repo": "rz-ai-agent", "title": "t"}
    }).status_code == 403


def test_local_terminal_bridge_is_operator_only(app, monkeypatch):
    monkeypatch.setattr(app, "MIA_TERMINAL_BRIDGE_TOKEN", "bridge-token")
    queued = []
    monkeypatch.setattr(app.cap_services, "create_terminal_approval", lambda *a: queued.append(a))
    result = app._request_local_terminal_command("pwd", "rz-ai-agent", BOB["id"])
    assert result["status"] == "blocked"
    assert queued == [] and app._LOCAL_TERMINAL_JOBS == {}


def test_agent_loop_never_offers_operator_tools_to_other_accounts(app, monkeypatch):
    offered = []

    def fake_completion(messages, model, temperature, max_tokens, **kwargs):
        offered.append({t["function"]["name"] for t in kwargs["tools"]})
        return {"choices": [{"message": {"role": "assistant", "content": "done"}}]}

    monkeypatch.setattr(app.cap_services, "create_agent_run", lambda *a: {"id": "run-1"})
    monkeypatch.setattr(app.cap_services, "record_agent_run_event", lambda *a: None)
    monkeypatch.setattr(app.cap_services, "finish_agent_run", lambda *a, **k: None)
    app._complete_with_mia_agent_tools("s", "do a thing", f"{BOB['id']}:s1", "m", 0, 100, completion_fn=fake_completion)
    assert not offered[0] & app.OPERATOR_ONLY_AGENT_TOOLS
    app._complete_with_mia_agent_tools("s", "do a thing", "local:s1", "m", 0, 100, completion_fn=fake_completion)
    assert app.OPERATOR_ONLY_AGENT_TOOLS <= offered[1]


def test_operator_rules_in_account_mode(monkeypatch):
    from app import workspace_access
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.delenv("RZ_OPERATOR_USER_IDS", raising=False)

    monkeypatch.delenv("RZ_OPERATOR_EMAILS", raising=False)
    assert workspace_access.is_operator(ALICE["id"], ALICE["email"]) is False  # fails closed when unset
    assert workspace_access.is_operator("local") is False

    monkeypatch.setenv("RZ_OPERATOR_EMAILS", "Alice@Example.com, ops@example.com")
    assert workspace_access.is_operator(ALICE["id"], ALICE["email"]) is True
    assert workspace_access.is_operator(BOB["id"], BOB["email"]) is False

    import app.auth as auth
    monkeypatch.setattr(auth, "get_user_email", lambda uid: (_ for _ in ()).throw(RuntimeError("db down")))
    workspace_access._EMAIL_CACHE.clear()
    assert workspace_access.is_operator(ALICE["id"]) is False  # lookup failure never grants access

    monkeypatch.setenv("RZ_OPERATOR_USER_IDS", BOB["id"])
    assert workspace_access.is_operator(BOB["id"]) is True


# ------------------------------------------------------------------ Slack

def test_each_slack_user_gets_their_own_account(app, monkeypatch):
    seen = []

    def fake(**kw):
        seen.append(kw["session_id"])
        return _response(app, kw["session_id"])
    monkeypatch.setattr(app, "_complete_message", fake)
    slack = sys.modules["app.slack_channel"]
    # After test reloads, the Slack module may still reference an earlier app.main instance.
    monkeypatch.setattr(slack, "rz_main", app)
    monkeypatch.setattr(slack, "_post_message", lambda *a, **k: None)
    client = TestClient(app.app)
    for n, user in enumerate(("U111", "U222")):
        raw = json.dumps({"type": "event_callback", "event_id": f"Ev{n}", "team_id": "T9",
                          "event": {"type": "app_mention", "text": "<@BOT> hi", "channel": "C1", "user": user, "ts": "1"}}).encode()
        stamp = str(int(time.time()))
        sig = "v0=" + hmac.new(b"unit-test-secret", b"v0:" + stamp.encode() + b":" + raw, hashlib.sha256).hexdigest()
        resp = client.post("/v1/slack/events", content=raw, headers={"X-Slack-Request-Timestamp": stamp, "X-Slack-Signature": sig})
        assert resp.status_code == 200
    owners = {s.split(":", 1)[0] for s in seen}
    assert owners == {"slack-T9-U111", "slack-T9-U222"}


def test_conversation_history_survives_in_memory_session_loss(app, accounts):
    session_id = f"{ALICE['id']}:restart-proof"
    app._conversations[session_id] = {
        'history': [{'role': 'user', 'content': 'persist me'}, {'role': 'assistant', 'content': 'saved'}],
        'updated': time.time(), 'model': 'test/model',
    }
    app._touch_session(session_id, 'test/model')
    with app._store_lock:
        app._conversations.pop(session_id, None)
    restored = app._get_session(session_id)
    assert restored['history'] == [{'role': 'user', 'content': 'persist me'}, {'role': 'assistant', 'content': 'saved'}]
    assert restored['model'] == 'test/model'
    listed = accounts.as_user(ALICE).get('/v1/conversations').json()
    assert session_id in listed['sessions']


def test_conversation_reset_deletes_durable_history(app, accounts):
    alice = accounts.as_user(ALICE)
    session_id = f"{ALICE['id']}:reset-proof"
    app.cap_services.save_conversation(ALICE['id'], session_id, [{'role': 'user', 'content': 'private'}], 'test/model')
    with app._store_lock:
        app._conversations[session_id] = {'history': [{'role': 'user', 'content': 'private'}], 'updated': time.time(), 'model': 'test/model'}
    response = alice.post(f"/v1/conversations/{session_id}")
    assert response.status_code == 200
    assert app.cap_services.get_conversation(ALICE['id'], session_id) is None
    assert session_id not in app._conversations


def test_explicit_recovery_endpoint_recovers_only_callers_stale_runs(app, accounts, monkeypatch):
    from app import capabilities
    alice_session = f"{ALICE['id']}:recovery-session"
    bob_session = f"{BOB['id']}:recovery-session"
    alice_run = capabilities.create_agent_run(ALICE['id'], alice_session, 'Alice operation')
    bob_run = capabilities.create_agent_run(BOB['id'], bob_session, 'Bob operation')
    capabilities.record_agent_run_event(ALICE['id'], alice_run['id'], {'type': 'tool_started', 'tool': 'mia_lookup', 'call_id': 'alice-call'})
    capabilities.record_agent_run_event(BOB['id'], bob_run['id'], {'type': 'tool_started', 'tool': 'mia_lookup', 'call_id': 'bob-call'})
    real_time = capabilities.time.time
    monkeypatch.setattr(capabilities.time, 'time', lambda: real_time() + 2000)
    recovered = capabilities.recover_stale_agent_runs(ALICE['id'], 900)
    assert recovered['count'] == 1
    assert capabilities.list_agent_runs(ALICE['id'])['runs'][0]['status'] == 'interrupted'
    assert capabilities.list_agent_runs(BOB['id'])['runs'][0]['status'] == 'running'
