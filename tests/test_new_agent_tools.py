import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402


@pytest.fixture()
def m(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("OLLAMA_BASE_URL", "")
    os.environ["RZ_API_KEY"] = "test-key"
    os.environ["OPENROUTER_API_KEY"] = "real"
    sys.modules.pop("app.main", None)
    sys.modules.pop("app.auth", None)
    import app.main as module
    yield module
    for key in ("RZ_API_KEY", "OPENROUTER_API_KEY"):
        os.environ.pop(key, None)


ADDR = "0x" + "a" * 40
TX = "0x" + "b" * 64


def test_new_tools_are_registered_and_schema_enforced(m):
    names = {t["function"]["name"] for t in m.MIA_AGENT_TOOLS}
    assert {"mia_generate_image", "mia_crypto_trace", "mia_research_brief"} <= names
    with pytest.raises(ValueError):
        m._validate_mia_agent_tool_args("mia_generate_image", {})
    with pytest.raises(ValueError):
        m._validate_mia_agent_tool_args("mia_research_brief", {"query": "x", "extra": 1})
    with pytest.raises(ValueError):
        m._validate_mia_agent_tool_args("mia_research_brief", {"query": "x", "max_sources": 21})


def test_crypto_trace_validates_and_routes(m, monkeypatch):
    calls = []
    monkeypatch.setattr("app.investigation.engine.run_tool", lambda tool, kind, value: calls.append((tool, kind, value)) or {"status": "success"})
    with pytest.raises(ValueError):
        m._dispatch_mia_agent_tool("mia_crypto_trace", {"wallet_address": "0x123"}, "u", "s")
    with pytest.raises(ValueError):
        m._dispatch_mia_agent_tool("mia_crypto_trace", {"wallet_address": ADDR, "transaction_hash": TX}, "u", "s")
    assert m._dispatch_mia_agent_tool("mia_crypto_trace", {"wallet_address": ADDR}, "u", "s")["status"] == "success"
    m._dispatch_mia_agent_tool("mia_crypto_trace", {"transaction_hash": TX}, "u", "s")
    assert calls == [("crypto_trace", "wallet_address", ADDR), ("crypto_trace", "transaction_hash", TX)]


def test_generate_image_strips_base64_from_chat_context(m, monkeypatch):
    monkeypatch.setattr(m, "_generate_image_from_payload", lambda payload: {"status": "ok", "image": {"url": None, "b64_json": "AAAA", "media_type": "image/png"}})
    result = m._dispatch_mia_agent_tool("mia_generate_image", {"prompt": "a cat"}, "u", "s")
    assert "b64_json" not in result["image"] and "note" in result["image"]


def test_research_brief_numbers_sources(m, monkeypatch):
    monkeypatch.setattr(m, "FIRECRAWL_API_KEY", "")
    monkeypatch.setattr(m.cap_services, "web_research", lambda q, n: {"source": "test", "results": [
        {"title": "A", "url": "https://a.example", "snippet": "s1"}, {"title": "B", "url": "https://b.example", "snippet": "s2"}]})
    brief = m._dispatch_mia_agent_tool("mia_research_brief", {"query": "topic", "max_sources": 2}, "u", "s")
    assert [s["id"] for s in brief["sources"]] == [1, 2] and brief["untrusted"] is True
