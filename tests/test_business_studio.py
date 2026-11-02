import json
from fastapi.testclient import TestClient
from test_main import app  # reuse the repository test fixture


def test_business_studio_page_is_served(app):
    response = TestClient(app.app).get("/business")
    assert response.status_code == 200
    assert "MIA · BUSINESS STUDIO" in response.text


def test_pdf_endpoint_returns_real_pdf(app):
    response = TestClient(app.app).post(
        "/v1/business/pdf",
        headers={"X-API-Key": "test-key"},
        json={"title": "Business Proposal", "content": "# Summary\n\nA real proposal.", "filename": "proposal.pdf"},
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/pdf")
    assert response.content.startswith(b"%PDF-")


def test_pdf_endpoint_requires_authentication(app):
    response = TestClient(app.app).post(
        "/v1/business/pdf",
        json={"title": "Test", "content": "Body"},
    )
    assert response.status_code == 401


def test_company_research_rejects_private_and_non_https_targets():
    from app.business_studio import _validate_public_url
    from fastapi import HTTPException
    for url in ("http://example.com", "https://127.0.0.1", "https://localhost", "https://example.com:8443"):
        try:
            _validate_public_url(url)
        except HTTPException as exc:
            assert exc.status_code == 422
        else:
            raise AssertionError(f"unsafe URL accepted: {url}")


def test_company_research_only_keeps_same_domain_pages(monkeypatch):
    import asyncio
    from app import business_studio as studio
    calls = []
    async def fake_execute(name, args, api_key):
        calls.append((name, args["url"] if "url" in args else args["query"]))
        if name == "firecrawl_scrape":
            return json.dumps({"url": args["url"], "title": "Example", "markdown": "Public page content"})
        return json.dumps({"results": [
            {"url": "https://example.com/about"},
            {"url": "https://www.example.com/team"},
            {"url": "https://evil.example.net/steal"},
            {"url": "http://example.com/insecure"},
        ]})
    monkeypatch.setattr(studio, "execute_firecrawl_tool", fake_execute)
    result = asyncio.run(studio.research_company("https://example.com", "test"))
    assert result["page_count"] == 3
    assert all("evil.example.net" not in page["url"] for page in result["pages"])
    assert all(page["url"].startswith("https://") for page in result["pages"])


def test_ad_draft_is_not_published_and_uses_model_draft(app, monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(app, "_complete_message", lambda **kwargs: SimpleNamespace(reply="Draft campaign copy"))
    response = TestClient(app.app).post(
        "/v1/business/ads/draft",
        headers={"X-API-Key": "test-key"},
        json={"business_name": "Acme", "product_or_service": "Bookkeeping", "platforms": ["Facebook"]},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "draft"
    assert response.json()["published"] is False
    assert response.json()["campaign"] == "Draft campaign copy"


def test_long_chat_request_and_agent_tool_are_supported(app):
    from app.main import ChatRequest, MIA_AGENT_TOOLS
    request = ChatRequest(message="x" * 12000)
    assert len(request.message) == 12000
    schema = next(t["function"]["parameters"] for t in MIA_AGENT_TOOLS if t["function"]["name"] == "mia_company_research")
    assert schema["properties"]["url"]["maxLength"] == 2000


def test_base_messages_do_not_duplicate_core_identity(app, monkeypatch):
    monkeypatch.setattr(app, "_get_session", lambda _sid: {"history": []})
    messages = app._base_messages(app.ASSISTANT_IDENTITY, "hello", "test-user:session")
    assert len(messages) == 2
    assert messages[0]["content"] == app.ASSISTANT_IDENTITY

def test_empty_length_completion_retries_same_provider_with_more_tokens(app, monkeypatch):
    calls = []
    def fake_provider(messages, model, temperature, max_tokens):
        calls.append(max_tokens)
        if len(calls) == 1:
            return {"choices": [{"finish_reason": "length", "message": {"content": ""}}]}
        return {"choices": [{"finish_reason": "stop", "message": {"content": "Recovered full answer"}}]}
    monkeypatch.setattr(app, "_call_openrouter", fake_provider)
    monkeypatch.setattr(app, "_should_use_mia_agent_tools", lambda _message: False)
    monkeypatch.setattr(app, "_should_offer_local_terminal_tools", lambda _message: False)
    monkeypatch.setattr(app, "_should_offer_github_tools", lambda _message: False)
    monkeypatch.setattr(app, "_should_research_web", lambda _message: False)
    response = app._complete_message("", "Please give me a detailed answer.", "test-user:retry", None, 0.2, 6000)
    assert response.reply == "Recovered full answer"
    assert calls == [6000, 12000]


def test_story_generation_returns_a_draft_and_preserves_character_bible(app, monkeypatch):
    from types import SimpleNamespace
    captured = {}
    def fake_complete(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(reply="# The Moonlit Road\n\nAmara opened the gate.")
    monkeypatch.setattr(app, "_complete_message", fake_complete)
    response = TestClient(app.app).post(
        "/v1/business/story",
        headers={"X-API-Key": "test-key"},
        json={
            "title": "The Moonlit Road",
            "story_notes": "Amara discovers a hidden road beneath the city.",
            "characters": "Amara: brave, silver hair, loyal to her brother.",
            "genre": "Fantasy",
            "length": "medium",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "draft"
    assert data["characters_preserved"] is True
    assert data["word_count"] > 0
    assert "Amara" in captured["user_content"]
    assert "silver hair" in captured["user_content"]
    assert "preserve every named character" in captured["user_content"].lower()


def test_story_generation_requires_authentication(app):
    response = TestClient(app.app).post(
        "/v1/business/story",
        json={"story_notes": "A story begins."},
    )
    assert response.status_code == 401


def test_story_generation_validates_length(app):
    response = TestClient(app.app).post(
        "/v1/business/story",
        headers={"X-API-Key": "test-key"},
        json={"story_notes": "A story begins.", "length": "unlimited"},
    )
    assert response.status_code == 422
