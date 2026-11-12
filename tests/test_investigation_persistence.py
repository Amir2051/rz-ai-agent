import importlib
import os
import sys

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def app_with_store(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("RZ_LOCAL_DATA_PATH", str(tmp_path / "rz-test.sqlite3"))
    monkeypatch.setenv("RZ_API_KEY", "test-key")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-provider-key")
    sys.modules.pop("app.main", None)
    module = importlib.import_module("app.main")
    yield module
    with module._store_lock:
        module._conversations.clear()


def test_investigation_record_round_trip_and_owner_isolation(app_with_store):
    store = app_with_store.cap_services
    payload = {
        "indicators": {"domain": "example.com"},
        "findings": [{"finding_id": "F001", "evidence": {"source": "test", "value": "observed"}}],
    }
    created = store.create_investigation_record("alice", "alice:session-1", "run-1", "six_phase_investigation", payload)

    listed = store.list_investigation_records("alice")
    assert listed["count"] == 1
    assert listed["records"][0]["id"] == created["id"]
    assert store.get_investigation_record("alice", created["id"])["payload"] == payload
    assert store.list_investigation_records("bob")["records"] == []
    with pytest.raises(KeyError, match="not found"):
        store.get_investigation_record("bob", created["id"])


def test_investigation_record_rejects_oversized_payload_and_bad_type(app_with_store):
    store = app_with_store.cap_services
    with pytest.raises(ValueError, match="500 KB"):
        store.create_investigation_record("alice", "alice:s1", None, "targeted_lookup", {"data": "x" * 500_001})
    with pytest.raises(ValueError, match="record type"):
        store.create_investigation_record("alice", "alice:s1", None, "other", {})


def test_full_investigation_dispatch_persists_report_and_evidence(app_with_store, monkeypatch):
    import app.investigation.engine as engine

    report = {
        "status": "completed", "run_id": "investigation-run-1",
        "phases": [{"phase": i, "status": "success"} for i in range(1, 7)],
        "findings": [{"finding_id": "F001", "evidence": {"source": "test_dns", "addresses": ["203.0.113.5"]}}],
    }
    monkeypatch.setattr(engine, "run_investigation", lambda *args, **kwargs: report)
    result = app_with_store._dispatch_mia_agent_tool(
        "mia_safenestt_investigate", {"indicators": {"domain": "example.com"}}, "alice", "alice:session-1"
    )
    assert result["record_id"]
    saved = app_with_store.cap_services.get_investigation_record("alice", result["record_id"])
    assert saved["run_id"] == "investigation-run-1"
    assert saved["payload"]["findings"][0]["evidence"]["source"] == "test_dns"


def test_targeted_lookup_dispatch_persists_provider_result(app_with_store, monkeypatch):
    import app.investigation.engine as engine

    monkeypatch.setattr(engine, "run_tool", lambda *args: {"status": "success", "source": "test_dns", "addresses": ["203.0.113.9"]})
    result = app_with_store._dispatch_mia_agent_tool(
        "mia_osint_lookup", {"tool_name": "dns_lookup", "indicator_type": "domain", "value": "example.com"}, "alice", "alice:session-2"
    )
    saved = app_with_store.cap_services.get_investigation_record("alice", result["record_id"])
    assert saved["record_type"] == "targeted_lookup"
    assert saved["payload"]["result"]["addresses"] == ["203.0.113.9"]


def test_investigation_record_api_is_authenticated_and_owner_scoped(app_with_store):
    client = TestClient(app_with_store.app)
    assert client.get("/v1/investigations").status_code == 401
    record = app_with_store.cap_services.create_investigation_record(
        "local", "local:session", "run-2", "targeted_lookup", {"result": {"status": "success"}}
    )
    headers = {"X-API-Key": "test-key"}
    listing = client.get("/v1/investigations", headers=headers)
    assert listing.status_code == 200
    assert listing.json()["count"] >= 1
    detail = client.get(f"/v1/investigations/{record['id']}", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["record"]["payload"]["result"]["status"] == "success"
    assert client.get("/v1/investigations/not-a-valid-id", headers=headers).status_code == 400
