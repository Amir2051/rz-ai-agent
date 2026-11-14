import json
import sys
import time
from types import SimpleNamespace

import pytest


class FakeCursor:
    def __init__(self, connection, query, params):
        self.connection = connection
        self.query = query
        self.params = params
        self.rowcount = 1

    def fetchone(self):
        return self.connection.fetchone_result

    def fetchall(self):
        return self.connection.fetchall_result


class FakePostgresConnection:
    def __init__(self):
        self.statements = []
        self.fetchone_result = None
        self.fetchall_result = []
        self.commits = 0
        self.closed = False

    def execute(self, query, params=None):
        self.statements.append((query, params))
        return FakeCursor(self, query, params)

    def commit(self):
        self.commits += 1

    def close(self):
        self.closed = True


@pytest.fixture
def postgres_adapter(monkeypatch):
    from app import capabilities

    connections = []

    def connect(dsn, connect_timeout):
        assert dsn == "postgresql://isolated-test.invalid/db"
        assert connect_timeout == 5
        conn = FakePostgresConnection()
        connections.append(conn)
        return conn

    monkeypatch.setenv("DATABASE_URL", "postgresql://isolated-test.invalid/db")
    monkeypatch.setitem(sys.modules, "psycopg", SimpleNamespace(connect=connect))
    return capabilities, connections


def _business_queries(conn):
    return [q for q, _ in conn.statements if "rz_agent_conversations" in q]


def test_postgres_schema_and_conversation_upsert(postgres_adapter):
    capabilities, connections = postgres_adapter
    result = capabilities.save_conversation(
        "alice", "alice:session-1",
        [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "hi"}],
        "test-model",
    )
    conn = connections[-1]
    ddl = "\n".join(q for q, _ in conn.statements)
    assert "CREATE TABLE IF NOT EXISTS rz_agent_conversations" in ddl
    assert "history JSONB NOT NULL" in ddl
    upsert, params = next((q, p) for q, p in conn.statements if "INSERT INTO rz_agent_conversations" in q)
    assert "ON CONFLICT(user_id,session_id) DO UPDATE" in upsert
    assert "history=EXCLUDED.history" in upsert
    assert params[0:2] == ("alice", "alice:session-1")
    assert json.loads(params[3]) == [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
    ]
    assert params[4] == "test-model"
    assert result["history_length"] == 2
    assert conn.commits == 1 and conn.closed


def test_postgres_restore_and_list_are_owner_and_ttl_scoped(postgres_adapter):
    capabilities, connections = postgres_adapter
    history = [{"role": "assistant", "content": "persisted"}]
    conn = FakePostgresConnection()
    conn.fetchone_result = ("alice session title", json.dumps(history), "model-x", 100.0, 200.0)
    conn.fetchall_result = [("alice:session-1", "alice session title", "model-x", 200.0, 200.0)]
    connections.append(conn)
    # Route the two separate _db() calls to distinct deterministic connections.
    remaining = [conn, FakePostgresConnection()]
    remaining[1].fetchone_result = ("alice session title", json.dumps(history), "model-x", 100.0, 200.0)
    remaining[1].fetchall_result = [("alice:session-1", "alice session title", "model-x", 200.0, 200.0)]
    list_conn = remaining[1]
    def connect(dsn, connect_timeout):
        return remaining.pop(0)
    import sys
    sys.modules["psycopg"].connect = connect

    restored = capabilities.get_conversation("alice", "alice:session-1", ttl_seconds=600)
    assert restored["history"] == history and restored["model"] == "model-x"
    get_conn = next(c for c in [conn] if any("SELECT title" in q and "history" in q for q, _ in c.statements))
    get_query, get_params = next((q, p) for q, p in get_conn.statements if "SELECT title" in q and "history" in q)
    assert "user_id=%s AND session_id=%s" in get_query
    assert get_params[0:2] == ("alice", "alice:session-1")
    # created_at/updated_at are in the SELECT result, not query params

    listed = capabilities.list_conversations("alice", ttl_seconds=600, limit=10)
    assert listed["count"] == 1
    assert listed["conversations"][0]["session_id"] == "alice:session-1"
    list_query, list_params = next((q, p) for q, p in list_conn.statements if "SELECT session_id,title,model" in q)
    assert "WHERE user_id=%s AND updated_at >= now() - %s * interval '1 second'" in list_query
    assert list_params[0] == "alice" and list_params[2] == 10
    assert list_conn.closed


def test_postgres_delete_is_owner_scoped_and_committed(postgres_adapter):
    capabilities, connections = postgres_adapter
    assert capabilities.delete_conversation("alice", "alice:session-1") is True
    conn = connections[-1]
    query, params = next((q, p) for q, p in conn.statements if q.startswith("DELETE FROM rz_agent_conversations"))
    assert "user_id=%s AND session_id=%s" in query
    assert params == ("alice", "alice:session-1")
    assert conn.commits == 1 and conn.closed


def test_postgres_rejects_cross_account_session_before_connect(postgres_adapter):
    capabilities, connections = postgres_adapter
    with pytest.raises(ValueError, match="does not belong"):
        capabilities.get_conversation("bob", "alice:session-1")
    assert connections == []
