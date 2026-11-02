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
        json={"url": "https://example.com", "objective": "leads", "platforms": ["Facebook", "Google"],
              "provider_facts": {"listing_name": "Sunny Days", "city": "Albany", "street": "123 private street"},
              "provider_profile": {"description": "Play-based care", "hours": "Mon-Fri", "home_address": "123 private street"}},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["business"]["name"] == "Example Works"
    assert data["research"]["brand_assets"]["logo"].endswith("logo.png")
    assert data["research"]["brand_assets"]["images"] == ["https://example.com/team.jpg"]
    assert "Example Works" in captured[0]["user_content"]
    assert "https://example.com/logo.png" in captured[0]["user_content"]
    assert "90-day marketing plan" in captured[0]["user_content"]
    assert "Sunny Days" in captured[0]["user_content"]
    assert "Play-based care" in captured[0]["user_content"]
    assert "123 private street" not in captured[0]["user_content"]
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


def test_dedicated_drivenest_service_key_is_scoped_to_marketing_routes(app, monkeypatch):
    from fastapi import HTTPException
    from starlette.requests import Request
    monkeypatch.setattr(app, "DATABASE_AUTH_ENABLED", True)
    monkeypatch.setattr(app, "SERVICE_API_KEY", "drivenest-service-secret")
    def request(path):
        return Request({"type": "http", "method": "POST", "path": path, "headers": [], "query_string": b"", "server": ("test", 443), "scheme": "https", "client": ("127.0.0.1", 12345), "http_version": "1.1"})
    user = app.require_business_service_user(request("/v1/business/ads/draft"), "", "drivenest-service-secret")
    assert user["auth_type"] == "service_api_key"
    pdf_user = app.require_business_service_user(request("/v1/business/pdf/intake"), "", "drivenest-service-secret")
    assert pdf_user["auth_type"] == "service_api_key"
    try:
        app.require_business_service_user(request("/v1/business/story"), "", "drivenest-service-secret")
    except HTTPException as exc:
        assert exc.status_code == 403
    else:
        raise AssertionError("the DriveNest service credential must not access unrelated MIA endpoints")


def test_family_daycare_privacy_mode_redacts_street_addresses_from_research_and_prompts(app, monkeypatch):
    from types import SimpleNamespace
    from app import business_studio as studio
    captured = []
    async def fake_research(url, api_key):
        return {
            "status": "ok", "url": url, "domain": "example.com", "business_name": "Sunny Family Day Care",
            "description": "Visit us at 123 Main Street, Albany, NY 12203.", "brand_assets": {"images": []}, "page_count": 1,
            "pages": [{"url": url, "title": "123 Main Street Day Care", "markdown": "Our address is 123 Main Street, Albany, NY 12203. We offer family care."}],
        }
    def fake_complete(**kwargs):
        captured.append(kwargs["user_content"])
        return SimpleNamespace(reply="Draft")
    monkeypatch.setattr(studio, "research_company", fake_research)
    monkeypatch.setattr(app, "FIRECRAWL_API_KEY", "firecrawl-test-key")
    monkeypatch.setattr(app, "_complete_message", fake_complete)
    response = TestClient(app.app).post("/v1/business/marketing/plan", headers={"X-API-Key": "test-key"}, json={
        "url": "https://example.com", "platforms": ["Facebook"], "privacy_mode": True,
        "provider_facts": {"listing_name": "Family Day Care in Albany", "program_type": "Family Day Care", "city": "Albany"},
    })
    assert response.status_code == 200
    result = response.json()
    assert "123 Main Street" not in result["research"]["description"]
    assert "123 Main Street" not in result["research"]["pages"][0]["markdown"]
    assert all("123 Main Street" not in prompt for prompt in captured)
    assert "[street address withheld]" in result["research"]["pages"][0]["markdown"]


def test_pdf_intake_asks_only_for_required_missing_details(app):
    client = TestClient(app.app)
    response = client.post('/v1/business/pdf/intake', headers={'X-API-Key': 'test-key'}, json={
        'document_type': 'marketing_flyer',
        'known_fields': {'business_name': 'Sunny Days', 'service_summary': 'Licensed childcare', 'location': 'Albany, NY'},
    })
    assert response.status_code == 200
    data = response.json()
    assert data['status'] == 'needs_information'
    assert {x['key'] for x in data['missing_questions']} == {'contact_details', 'call_to_action'}
    assert all(x['required'] for x in data['missing_questions'])


def test_pdf_generate_refuses_to_create_until_required_information_is_complete(app, monkeypatch):
    response = TestClient(app.app).post('/v1/business/pdf/generate', headers={'X-API-Key': 'test-key'}, json={
        'document_type': 'marketing_flyer', 'title': 'Sunny Days Flyer',
        'answers': {'business_name': 'Sunny Days', 'service_summary': 'Childcare', 'location': 'Albany, NY'},
    })
    assert response.status_code == 422
    assert 'missing_questions' in response.json()['detail']


def test_pdf_generate_returns_structured_upload_ready_pdf(app, monkeypatch):
    from types import SimpleNamespace
    generated = {
        'subtitle': 'A trusted childcare program for local families',
        'executive_summary': 'Sunny Days provides childcare services for families in Albany, New York.',
        'sections': [
            {'heading': 'Our program', 'paragraphs': ['We provide dependable childcare in a welcoming setting.'], 'bullets': ['Childcare services for local families']},
            {'heading': 'How to get in touch', 'paragraphs': ['Contact the program to discuss your childcare needs.'], 'bullets': ['Call or email to request information']},
        ],
        'call_to_action': 'Contact Sunny Days to learn more about the program.',
        'contact_details': 'hello@example.com · https://example.com',
        'sources': ['https://example.com'],
    }
    captured = {}
    def fake_complete(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(reply=json.dumps(generated))
    monkeypatch.setattr(app, '_complete_message', fake_complete)
    response = TestClient(app.app).post('/v1/business/pdf/generate', headers={'X-API-Key': 'test-key'}, json={
        'document_type': 'marketing_flyer', 'title': 'Sunny Days Childcare', 'filename': 'sunny-days.pdf',
        'answers': {'business_name': 'Sunny Days', 'service_summary': 'Childcare', 'location': 'Albany, NY',
                    'contact_details': 'hello@example.com · https://example.com', 'call_to_action': 'Contact us to learn more.'},
        'source_context': 'https://example.com public website information',
    })
    assert response.status_code == 200
    assert response.headers['content-type'].startswith('application/pdf')
    assert response.content.startswith(b'%PDF-')
    assert response.headers['content-disposition'].find('sunny-days.pdf') >= 0
    from io import BytesIO
    from pypdf import PdfReader
    pdf_text = "\n".join(page.extract_text() or "" for page in PdfReader(BytesIO(response.content)).pages)
    assert "Sunny Days Childcare" in pdf_text
    assert "Our program" in pdf_text
    assert "How to get in touch" in pdf_text
    assert "hello@example.com" in pdf_text
    assert "TBD" not in pdf_text and "Insert here" not in pdf_text
    assert 'Return ONLY valid JSON' in captured['user_content']
    assert 'never fabricate missing facts' in captured['system'].lower()


def test_pdf_generate_rejects_placeholder_answers_before_model_call(app, monkeypatch):
    called = []
    monkeypatch.setattr(app, '_complete_message', lambda **kwargs: called.append(kwargs))
    response = TestClient(app.app).post('/v1/business/pdf/generate', headers={'X-API-Key': 'test-key'}, json={
        'document_type': 'marketing_flyer', 'title': 'Flyer',
        'answers': {'business_name': 'TBD', 'service_summary': 'Childcare', 'location': 'Albany',
                    'contact_details': 'hello@example.com', 'call_to_action': 'Contact us.'},
    })
    assert response.status_code == 422
    assert not called


def test_legacy_pdf_export_rejects_placeholder_text(app):
    response = TestClient(app.app).post('/v1/business/pdf', headers={'X-API-Key': 'test-key'}, json={
        'title': 'Business flyer', 'content': '## Details\\n\\n[Insert business contact information here]'
    })
    assert response.status_code == 422


def test_pdf_privacy_mode_redacts_family_daycare_home_addresses(app, monkeypatch):
    from types import SimpleNamespace
    captured = {}
    output = {
        "subtitle": "A parent-friendly childcare guide", "executive_summary": "Sunny Family Day Care serves families in Albany.",
        "sections": [
            {"heading": "Program overview", "paragraphs": ["A welcoming family childcare program for local families."], "bullets": ["Age-appropriate care"]},
            {"heading": "Contact", "paragraphs": ["Reach out to discuss your childcare needs."], "bullets": ["Email the provider"]},
        ], "call_to_action": "Contact the provider for more information.", "contact_details": "hello@example.com", "sources": [],
    }
    def fake_complete(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(reply=json.dumps(output))
    monkeypatch.setattr(app, '_complete_message', fake_complete)
    response = TestClient(app.app).post('/v1/business/pdf/generate', headers={'X-API-Key': 'test-key'}, json={
        'document_type': 'childcare_info_pack', 'title': 'Family Day Care Parent Guide', 'privacy_mode': True,
        'answers': {'business_name': 'Sunny Family Day Care', 'services': 'Family childcare', 'age_range': 'Infants to preschool',
                    'hours': 'Monday-Friday', 'location': 'Albany, NY', 'enrollment_process': 'Email to request details',
                    'contact_details': 'hello@example.com'},
        'source_context': 'Our home is located at 123 Main Street, Albany, NY 12203.',
    })
    assert response.status_code == 200
    assert '123 Main Street' not in captured['user_content']
    assert '[street address withheld]' in captured['user_content']
