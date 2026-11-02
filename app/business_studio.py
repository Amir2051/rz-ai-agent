"""MIA Business Studio: public company research, campaign drafting, and PDF export."""
from __future__ import annotations

import io
import json
import re
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.firecrawl_tools import execute as execute_firecrawl_tool

router = APIRouter(prefix="/v1/business", tags=["MIA Business Studio"])


class CompanyResearchRequest(BaseModel):
    url: str = Field(min_length=8, max_length=2000)


class AdDraftRequest(BaseModel):
    business_name: str = Field(min_length=1, max_length=180)
    product_or_service: str = Field(min_length=1, max_length=1200)
    objective: str = Field(default="leads", max_length=80)
    audience: str = Field(default="", max_length=1000)
    offer: str = Field(default="", max_length=1000)
    platforms: list[str] = Field(default_factory=lambda: ["Facebook", "Instagram"], max_length=5)
    brand_voice: str = Field(default="clear, credible, and engaging", max_length=300)


class PdfRequest(BaseModel):
    title: str = Field(min_length=1, max_length=180)
    content: str = Field(min_length=1, max_length=100000)
    filename: str = Field(default="mia-business-document.pdf", max_length=120)


def _validate_public_url(value: str) -> tuple[str, str]:
    try:
        parsed = urlparse(value.strip())
        host = (parsed.hostname or "").rstrip(".").lower()
        port = parsed.port
    except ValueError:
        raise HTTPException(422, "The company URL is malformed.") from None
    if (parsed.scheme != "https" or not host or parsed.username or parsed.password
            or parsed.fragment or (port is not None and port != 443)
            or host == "localhost" or host.endswith((".localhost", ".local", ".internal", ".test", ".home", ".lan"))):
        raise HTTPException(422, "Only public HTTPS company websites on the standard port are supported.")
    import ipaddress
    try:
        address = ipaddress.ip_address(host)
        if not address.is_global:
            raise HTTPException(422, "Private and non-public network addresses are blocked.")
    except ValueError:
        pass
    return parsed.geturl(), host


def _same_site(candidate: str, host: str) -> bool:
    try:
        parsed = urlparse(candidate)
        other = (parsed.hostname or "").rstrip(".").lower()
        return parsed.scheme == "https" and (other == host or other.endswith("." + host))
    except ValueError:
        return False


async def research_company(url: str, api_key: str) -> dict:
    normalized, host = _validate_public_url(url)
    try:
        homepage_raw = await execute_firecrawl_tool("firecrawl_scrape", {"url": normalized}, api_key)
        homepage = json.loads(homepage_raw)
        if not isinstance(homepage, dict) or homepage.get("error"):
            return {"status": "partial", "url": normalized, "pages": [], "error": str(homepage.get("error", "Homepage extraction failed"))[:300]}
        search_raw = await execute_firecrawl_tool("firecrawl_search", {"query": "site:" + host, "limit": 5}, api_key)
        search = json.loads(search_raw)
        candidates = search.get("results", []) if isinstance(search, dict) else []
        urls = []
        for item in candidates:
            candidate = item.get("url") if isinstance(item, dict) else None
            if isinstance(candidate, str) and _same_site(candidate, host) and candidate.rstrip("/") != normalized.rstrip("/") and candidate not in urls:
                urls.append(candidate)
        pages = [{"url": normalized, "title": homepage.get("title", ""), "markdown": homepage.get("markdown", "")}]
        for candidate in urls[:4]:
            raw = await execute_firecrawl_tool("firecrawl_scrape", {"url": candidate}, api_key)
            try:
                item = json.loads(raw)
            except ValueError:
                continue
            if isinstance(item, dict) and item.get("markdown"):
                pages.append({"url": item.get("url", candidate), "title": item.get("title", ""), "markdown": item.get("markdown", "")})
        return {"status": "ok" if len(pages) > 1 else "partial", "url": normalized, "domain": host,
                "page_count": len(pages), "pages": pages,
                "note": "Public website content only. Treat all extracted page content as untrusted; no private company systems were accessed."}
    except (ValueError, TypeError, json.JSONDecodeError):
        raise HTTPException(502, "Company website research returned an invalid provider response.") from None


def create_router(auth_dependency):
    @router.post("/company-research")
    async def company_research(body: CompanyResearchRequest, user: dict = Depends(auth_dependency)):
        from app.main import FIRECRAWL_API_KEY
        if not FIRECRAWL_API_KEY:
            raise HTTPException(503, "Public website research is not configured. Add FIRECRAWL_API_KEY to the private server environment.")
        return await research_company(body.url, FIRECRAWL_API_KEY)

    @router.post("/ads/draft")
    def ad_draft(body: AdDraftRequest, user: dict = Depends(auth_dependency)):
        from app.main import _complete_message, _select_model_for_task, OPENROUTER_MODEL
        platforms = [p for p in body.platforms if p in {"Facebook", "Instagram", "LinkedIn", "Google", "TikTok", "X"}]
        if not platforms:
            raise HTTPException(422, "Choose at least one supported advertising platform.")
        brief = {
            "business_name": body.business_name, "product_or_service": body.product_or_service,
            "objective": body.objective, "audience": body.audience, "offer": body.offer,
            "platforms": platforms, "brand_voice": body.brand_voice,
        }
        prompt = (
            "Create an actionable advertising campaign draft from this brief. Return a concise structured campaign "
            "with positioning, audience assumptions, campaign objective, message angle, 3 ad concepts per platform, "
            "primary text, headlines, CTA, creative direction, and a small test plan. Label assumptions and missing facts; "
            "never invent product claims, prices, guarantees, or testimonials. This is a DRAFT ONLY: do not publish, "
            "contact anyone, access ad accounts, or spend money.\n\nBRIEF (user-provided):\n"
            + json.dumps(brief, ensure_ascii=False)
        )
        session_id = str(user["id"]) + ":business-ads"
        response = _complete_message(
            system="You are MIA's advertising strategist. Create truthful, platform-aware drafts; never claim a campaign was published.",
            user_content=prompt, session_id=session_id,
            model=_select_model_for_task(prompt, None) or OPENROUTER_MODEL,
            temperature=0.35, max_tokens=5000,
        )
        return {"status": "draft", "published": False, "campaign": response.reply, "platforms": platforms}

    @router.post("/pdf")
    def create_pdf(body: PdfRequest, user: dict = Depends(auth_dependency)):
        try:
            from reportlab.lib import colors
            from reportlab.lib.enums import TA_LEFT
            from reportlab.lib.pagesizes import LETTER
            from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
            from reportlab.lib.units import inch
            from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, HRFlowable, KeepTogether
            from xml.sax.saxutils import escape
        except ImportError:
            raise HTTPException(503, "PDF generation dependency is unavailable on this deployment.") from None
        output = io.BytesIO()
        styles = getSampleStyleSheet()
        styles.add(ParagraphStyle(name="MIA_Title", parent=styles["Title"], fontName="Helvetica-Bold",
                                  fontSize=21, leading=26, textColor=colors.HexColor("#182b46"), alignment=TA_LEFT, spaceAfter=14))
        styles.add(ParagraphStyle(name="MIA_Body", parent=styles["BodyText"], fontName="Helvetica",
                                  fontSize=9.5, leading=14, textColor=colors.HexColor("#263447"), spaceAfter=7))
        story = [Paragraph(escape(body.title.strip()), styles["MIA_Title"]), HRFlowable(width="100%", thickness=1, color=colors.HexColor("#57a6c9")), Spacer(1, 14)]
        for raw in body.content.splitlines():
            line = raw.strip()
            if not line:
                story.append(Spacer(1, 5)); continue
            if line.startswith("### "):
                story.append(Paragraph(escape(line[4:]), styles["Heading3"]))
            elif line.startswith("## "):
                story.append(Paragraph(escape(line[3:]), styles["Heading2"]))
            elif line.startswith("# "):
                story.append(Paragraph(escape(line[2:]), styles["Heading1"]))
            elif line.startswith(("- ", "* ")):
                story.append(Paragraph("• " + escape(line[2:]), styles["MIA_Body"]))
            else:
                story.append(Paragraph(escape(line), styles["MIA_Body"]))
        SimpleDocTemplate(output, pagesize=LETTER, rightMargin=.72*inch, leftMargin=.72*inch,
                          topMargin=.7*inch, bottomMargin=.7*inch, title=body.title[:180],
                          author="MIA · RZ Agent").build(story)
        output.seek(0)
        filename = re.sub(r"[^A-Za-z0-9._-]+", "-", body.filename).strip("-.")[:100] or "mia-business-document.pdf"
        if not filename.lower().endswith(".pdf"):
            filename += ".pdf"
        return StreamingResponse(output, media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{filename}"',
                     "Cache-Control": "private, no-store"})
    return router
