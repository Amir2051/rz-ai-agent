"""Live PostgreSQL integration tests; opt in with RZ_TEST_POSTGRES_URL."""

import importlib
import os
import sys
import uuid
from urllib.parse import urlparse

import pytest
from fastapi.testclient import TestClient


POSTGRES_URL = os.getenv("RZ_TEST_POSTGRES_URL", "").strip()
pytestmark = pytest.mark.skipif(
    not POSTGRES_URL,
    reason="set RZ_TEST_POSTGRES_URL to an isolated disposable PostgreSQL database",
)

if POSTGRES_URL:
    _parsed = urlparse(POSTGRES_URL)
    if (
        _parsed.hostname not in {"localhost", "127.0.0.1"}
        or _parsed.username != "rzai_test"
        or _parsed.path != "/rzai_integration_test"
    ):
        raise RuntimeError(
            "RZ_TEST_POSTGRES_URL must target localhost database rzai_integration_test "
            "as user rzai_test; refusing to run integration tests against any other database"
        )


@pytest.fixture
def postgres_capabilities(monkeypatch):
    from app import capabilities

    monkeypatch.setenv("DATABASE_URL", POSTGRES_URL)
    return capabilities


def test_live_postgres_conversation_save_restore_upsert_list_delete(postgres_capabilities):
    capabilities = postgres_capabilities
    user_id = f"integration-{uuid.uuid4().hex}"
    session_id = f"{user_id}:session"
    first_history = [
        {"role": "user", "content": "first message"},
        {"role": "assistant", "content": "first reply"},
    ]
    updated_history = [
        {"role": "user", "content": "updated message"},
        {"role": "assistant", "content": "updated reply"},
    ]

    try:
        saved = capabilities.save_conversation(user_id, session_id, first_history, "integration-model")
        assert saved["history_length"] == 2

        restored = capabilities.get_conversation(user_id, session_id, ttl_seconds=600)
        assert restored is not None
        assert restored["history"] == first_history
        assert restored["model"] == "integration-model"

        capabilities.save_conversation(user_id, session_id, updated_history, "updated-model")
        restored_after_upsert = capabilities.get_conversation(user_id, session_id, ttl_seconds=600)
        assert restored_after_upsert is not None
        assert restored_after_upsert["history"] == updated_history
        assert restored_after_upsert["model"] == "updated-model"

        listed = capabilities.list_conversations(user_id, ttl_seconds=600, limit=10)
        assert listed["count"] == 1
        assert listed["sessions"][0]["session_id"] == session_id
        assert listed["sessions"][0]["model"] == "updated-model"
    finally:
        capabilities.delete_conversation(user_id, session_id)

    assert capabilities.get_conversation(user_id, session_id, ttl_seconds=600) is None


def test_live_postgres_conversation_account_isolation(postgres_capabilities):
    capabilities = postgres_capabilities
    owner_id = f"owner-{uuid.uuid4().hex}"
    other_id = f"other-{uuid.uuid4().hex}"
    session_id = f"{owner_id}:private-session"

    try:
        capabilities.save_conversation(
            owner_id,
            session_id,
            [{"role": "assistant", "content": "private transcript"}],
            "integration-model",
        )
        assert capabilities.get_conversation(other_id, f"{other_id}:different-session") is None
        with pytest.raises(ValueError, match="does not belong"):
            capabilities.get_conversation(other_id, session_id)
        assert capabilities.list_conversations(other_id, ttl_seconds=600)["count"] == 0
    finally:
        capabilities.delete_conversation(owner_id, session_id)


def test_live_postgres_investigation_record_round_trip_and_owner_isolation(postgres_capabilities):
    capabilities = postgres_capabilities
    owner_id = f"investigator-{uuid.uuid4().hex}"
    other_id = f"observer-{uuid.uuid4().hex}"
    session_id = f"{owner_id}:case-session"
    payload = {
        "indicators": {"domain": "integration.example"},
        "findings": [
            {
                "finding_id": "F-001",
                "classification": "observed",
                "evidence": {
                    "source": "integration_fixture",
                    "collected_at": "2026-11-12T12:00:00Z",
                    "value": "203.0.113.20",
                },
            }
        ],
    }
    created = capabilities.create_investigation_record(
        owner_id, session_id, "run-integration-1", "six_phase_investigation", payload
    )
    try:
        listed = capabilities.list_investigation_records(owner_id, limit=10)
        assert listed["count"] == 1
        assert listed["records"][0]["id"] == created["id"]

        restored = capabilities.get_investigation_record(owner_id, created["id"])
        assert restored["payload"] == payload
        assert restored["run_id"] == "run-integration-1"
        assert capabilities.list_investigation_records(other_id)["count"] == 0
        with pytest.raises(KeyError, match="not found"):
            capabilities.get_investigation_record(other_id, created["id"])
    finally:
        conn, _ = capabilities._db()
        try:
            conn.execute(
                "DELETE FROM rz_investigation_records WHERE user_id=%s AND record_id=%s",
                (owner_id, created["id"]),
            )
            conn.commit()
        finally:
            conn.close()


def test_live_postgres_stale_run_recovery_is_owner_scoped_and_never_retries(postgres_capabilities):
    capabilities = postgres_capabilities
    owner_id = f"recovery-owner-{uuid.uuid4().hex}"
    other_id = f"recovery-other-{uuid.uuid4().hex}"
    owner_session = f"{owner_id}:session"
    other_session = f"{other_id}:session"

    stale = capabilities.create_agent_run(owner_id, owner_session, "Check a deliberately interrupted workflow")
    fresh = capabilities.create_agent_run(owner_id, owner_session, "Leave this active workflow alone")
    other_stale = capabilities.create_agent_run(other_id, other_session, "Other account's stale workflow")

    try:
        capabilities.record_agent_run_event(
            owner_id, stale["id"],
            {"type": "tool_started", "tool": "mia_external_action", "call_id": "call-uncertain"},
        )
        capabilities.record_agent_run_event(
            owner_id, stale["id"],
            {"type": "tool_started", "tool": "mia_read_only_lookup", "call_id": "call-completed"},
        )
        capabilities.record_agent_run_event(
            owner_id, stale["id"],
            {"type": "tool_completed", "tool": "mia_read_only_lookup", "call_id": "call-completed"},
        )

        conn, _ = capabilities._db()
        try:
            cutoff = __import__("time").time() - 120
            conn.execute(
                "UPDATE rz_agent_runs SET updated_at=%s WHERE user_id=%s AND run_id IN (%s,%s)",
                (cutoff, owner_id, stale["id"], other_stale["id"]),
            )
            conn.commit()
        finally:
            conn.close()

        recovered = capabilities.recover_stale_agent_runs(owner_id, stale_seconds=60)
        assert recovered["count"] == 1
        assert recovered["recovered"][0]["id"] == stale["id"]
        assert recovered["recovered"][0]["uncertain_tools"] == ["mia_external_action"]
        assert recovered["recovered"][0]["uncertain_call_ids"] == ["call-uncertain"]

        owner_runs = {run["id"]: run for run in capabilities.list_agent_runs(owner_id)["runs"]}
        recovered_run = owner_runs[stale["id"]]
        assert recovered_run["status"] == "interrupted"
        assert recovered_run["error"]
        assert any(event.get("type") == "tool_outcome_unknown" for event in recovered_run["events"])
        assert not any(
            event.get("type") == "tool_retry" or event.get("type") == "tool_started" and event.get("call_id") == "call-uncertain"
            for event in recovered_run["events"][3:]
        )
        assert owner_runs[fresh["id"]]["status"] == "running"
        other_runs = capabilities.list_agent_runs(other_id)["runs"]
        assert next(run for run in other_runs if run["id"] == other_stale["id"])["status"] == "running"
    finally:
        conn, _ = capabilities._db()
        try:
            conn.execute(
                "DELETE FROM rz_agent_runs WHERE (user_id=%s AND run_id IN (%s,%s)) OR (user_id=%s AND run_id=%s)",
                (owner_id, stale["id"], fresh["id"], other_id, other_stale["id"]),
            )
            conn.commit()
        finally:
            conn.close()



def test_live_postgres_recovery_api_marks_only_stale_owned_runs(postgres_capabilities, monkeypatch):
    monkeypatch.setenv("RZ_API_KEY", "integration-api-key")
    monkeypatch.setenv("OPENROUTER_API_KEY", "integration-provider-placeholder")
    # Import the app in local API-key auth mode, then point its dynamic storage
    # adapter back to the disposable PostgreSQL database for the route calls.
    monkeypatch.setenv("DATABASE_URL", "")
    sys.modules.pop("app.main", None)
    module = importlib.import_module("app.main")
    monkeypatch.setenv("DATABASE_URL", POSTGRES_URL)
    capabilities = module.cap_services
    user_id = "local"  # Local API-key auth maps to the isolated local account in test mode.
    session_id = f"{user_id}:recovery-api-session"
    run = capabilities.create_agent_run(user_id, session_id, "Verify the authenticated recovery endpoint")

    try:
        capabilities.record_agent_run_event(
            user_id, run["id"],
            {"type": "tool_started", "tool": "mia_external_action", "call_id": "api-call-uncertain"},
        )
        conn, _ = capabilities._db()
        try:
            conn.execute(
                "UPDATE rz_agent_runs SET updated_at=%s WHERE user_id=%s AND run_id=%s",
                (__import__("time").time() - 120, user_id, run["id"]),
            )
            conn.commit()
        finally:
            conn.close()

        client = TestClient(module.app)
        headers = {"X-API-Key": "integration-api-key"}
        response = client.post("/v1/agent/runs/recover?stale_seconds=60", headers=headers)
        assert response.status_code == 200
        assert response.json()["count"] == 1
        assert response.json()["recovered"][0]["id"] == run["id"]

        history = client.get("/v1/agent/runs", headers=headers)
        assert history.status_code == 200
        recovered = next(item for item in history.json()["runs"] if item["id"] == run["id"])
        assert recovered["status"] == "interrupted"
        assert any(event.get("type") == "tool_outcome_unknown" for event in recovered["events"])
    finally:
        conn, _ = capabilities._db()
        try:
            conn.execute("DELETE FROM rz_agent_runs WHERE user_id=%s AND run_id=%s", (user_id, run["id"]))
            conn.commit()
        finally:
            conn.close()
        sys.modules.pop("app.main", None)
