from app.investigation import engine


def test_catalog_has_safe_nestt_style_tool_groups():
    assert {"dns_lookup", "reverse_dns", "osint_profile", "reputation_score", "dehashed_search", "darkweb_paste", "shodan_domain", "shodan_host", "virustotal", "hibp_email", "hunter_email", "hunter_domain", "firecrawl_scrape", "crypto_trace"} <= set(engine.TOOL_CATALOG)


def test_run_tool_rejects_unlisted_and_mismatched_tools():
    assert engine.run_tool("arbitrary_shell", "domain", "example.com")["status"] == "unsupported_tool"
    assert engine.run_tool("crypto_trace", "domain", "example.com")["status"] == "not_applicable"


def test_dns_lookup_success_shape(monkeypatch):
    monkeypatch.setattr(engine.socket, "getaddrinfo", lambda *a, **k: [(None, None, None, None, ("93.184.216.34", 0))])
    result = engine.run_tool("dns_lookup", "domain", "example.com")
    assert result["status"] == "success"
    assert result["addresses"] == ["93.184.216.34"]
    assert result["source"] == "system_dns"


def test_reverse_dns_rejects_invalid_ip():
    result = engine.run_tool("reverse_dns", "ip_address", "not-an-ip")
    assert result["status"] == "invalid_input"


def test_missing_optional_provider_key_is_explicit(monkeypatch):
    monkeypatch.delenv("SHODAN_API_KEY", raising=False)
    result = engine.run_tool("shodan_domain", "domain", "example.com")
    assert result["status"] == "not_configured"
    assert result["required_env"] == ["SHODAN_API_KEY"]


def test_orchestrator_returns_six_phases_and_preserves_tool_status(monkeypatch):
    def fake_tool(name, kind, value):
        if name == "dns_lookup":
            return {"status": "success", "source": "test_dns", "addresses": ["203.0.113.7"]}
        return {"status": "not_configured", "required_env": ["TEST_KEY"]}
    monkeypatch.setattr(engine, "run_tool", fake_tool)
    result = engine.run_investigation({"domain": "example.com"})
    assert result["status"] == "completed"
    assert [phase["phase"] for phase in result["phases"]] == [1, 2, 3, 4, 5, 6]
    assert result["summary"]["tools_succeeded"] == 1
    assert result["findings"][0]["provenance"]["source"] == "test_dns"
    assert result["summary"]["providers_not_configured"] > 0


def test_orchestrator_validates_indicator_shape():
    try:
        engine.run_investigation({"password": "secret"})
    except ValueError as exc:
        assert "No supported" in str(exc)
    else:
        raise AssertionError("unsupported indicators must be rejected")


def test_optional_provider_adapters_accept_normalized_success(monkeypatch):
    monkeypatch.setenv("SHODAN_API_KEY", "test")
    monkeypatch.setenv("VT_API_KEY", "test")
    monkeypatch.setenv("HIBP_API_KEY", "test")
    monkeypatch.setenv("HUNTER_API_KEY", "test")
    monkeypatch.setenv("ETHERSCAN_API_KEY", "test")
    monkeypatch.setenv("DEHASHED_API_KEY", "test")
    monkeypatch.setenv("DEHASHED_EMAIL", "test@example.com")
    monkeypatch.setattr(engine, "_request_json", lambda url, **kwargs: {
        "data": {"id": "example.com", "attributes": {"reputation": 0, "last_analysis_stats": {"malicious": 0}}},
        "result": [], "status": "success", "breaches": [], "meta": {},
    })
    cases = [
        ("dehashed_search", "domain", "example.com"),
        ("shodan_domain", "domain", "example.com"),
        ("shodan_host", "ip_address", "8.8.8.8"),
        ("virustotal", "domain", "example.com"),
        ("hibp_email", "email", "test@example.com"),
        ("hunter_email", "email", "test@example.com"),
        ("hunter_domain", "domain", "example.com"),
        ("crypto_trace", "wallet_address", "0x" + "1" * 40),
        ("crypto_trace", "transaction_hash", "0x" + "2" * 64),
    ]
    for tool, kind, value in cases:
        assert engine.run_tool(tool, kind, value)["status"] in {"success", "no_data"}, tool


def test_firecrawl_blocks_internal_targets_before_provider_call(monkeypatch):
    monkeypatch.setenv("FIRECRAWL_API_KEY", "test")
    monkeypatch.setattr(engine.socket, "getaddrinfo", lambda *a, **k: [(None, None, None, None, ("127.0.0.1", 443))])
    result = engine.run_tool("firecrawl_scrape", "url", "https://internal.example/path")
    assert result["status"] == "invalid_input"
    assert "non-public" in result["error"]


def test_public_paste_search_failure_is_reported_as_unavailable(monkeypatch):
    def fail_search(*args, **kwargs):
        raise RuntimeError("Web search could not retrieve usable results")
    monkeypatch.setattr(engine, "_search", fail_search)
    result = engine.run_tool("darkweb_paste", "domain", "example.com")
    assert result["status"] == "unavailable"
    assert "BRAVE_SEARCH_API_KEY" in result["error"]
