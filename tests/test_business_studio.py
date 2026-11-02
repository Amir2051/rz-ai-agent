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
            return json.dumps({"url": args["url"], "title": "Example", "markdown": "Public page content", "screenshot": "https://cdn.firecrawl.dev/example.png"})
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
    assert len(result["screenshots"]) == 3
    assert all(item["image"].startswith("https://cdn.firecrawl.dev/") for item in result["screenshots"])


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


def test_marketing_plan_uses_extracted_company_research_and_returns_brand_assets(app, monkeypatch):
    from types import SimpleNamespace
    from app import business_studio as studio
    captured = []
    async def fake_research(url, api_key):
        assert url == "https://example.com"
        assert api_key == "firecrawl-test-key"
        return {
            "status": "ok", "url": url, "domain": "example.com", "business_name": "Example Works",
            "description": "A local home services company.",
            "brand_assets": {"logo": "https://example.com/logo.png", "og_image": "https://example.com/cover.jpg", "images": ["https://example.com/team.jpg"]},
            "page_count": 2,
            "pages": [{"url": url, "title": "Example Works", "markdown": "We provide plumbing and heating services."}],
        }
    def fake_complete(**kwargs):
        captured.append(kwargs)
        return SimpleNamespace(reply="# 90-day plan\\n\\nBuild local search visibility.")
    monkeypatch.setattr(studio, "research_company", fake_research)
    monkeypatch.setattr(app, "FIRECRAWL_API_KEY", "firecrawl-test-key")
    monkeypatch.setattr(app, "_complete_message", fake_complete)
    response = TestClient(app.app).post(
        "/v1/business/marketing/plan",
        headers={"X-API-Key": "test-key"},
        json={"url": "https://example.com", "objective": "leads", "platforms": ["Facebook", "Google"]},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["business"]["name"] == "Example Works"
    assert data["research"]["brand_assets"]["logo"].endswith("logo.png")
    assert data["research"]["brand_assets"]["images"] == ["https://example.com/team.jpg"]
    assert "Example Works" in captured[0]["user_content"]
    assert "https://example.com/logo.png" in captured[0]["user_content"]
    assert "90-day marketing plan" in captured[0]["user_content"]
    assert "customer-facing landing-page copy" in captured[1]["user_content"]
    assert data["marketing_page_copy"].startswith("# 90-day plan")
    assert data["published"] is False


def test_company_research_filters_brand_assets_to_public_https():
    from app.business_studio import _page_assets
    metadata, images = _page_assets({
        "metadata": {"favicon": "https://example.com/favicon.ico", "logo": "http://example.com/logo.png"},
        "images": ["https://cdn.example.net/brand.jpg", "http://example.com/private.png", "https://127.0.0.1/bad.png"],
        "markdown": "![hero](https://example.com/hero.webp)",
    }, "example.com")
    assert metadata == {"favicon": "https://example.com/favicon.ico"}
    assert images == ["https://cdn.example.net/brand.jpg", "https://example.com/hero.webp"]
