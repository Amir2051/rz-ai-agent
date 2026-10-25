"""A bounded, provenance-preserving investigation toolset adapted from SafeNestT.

This module deliberately does not import SafeNestT's database, case store, or model
provider. It uses MIA-RZ's existing web research implementation and optional provider
credentials supplied to the RZ runtime. Provider errors are reported per-tool and do
not abort the investigation.
"""
from __future__ import annotations

import ipaddress
import json
import os
import re
import socket
import time
import uuid
from datetime import datetime, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlparse
from urllib.request import Request, urlopen

TOOL_CATALOG: dict[str, dict[str, Any]] = {
    "dns_lookup": {"label": "DNS lookup", "indicators": ["domain"]},
    "reverse_dns": {"label": "Reverse DNS", "indicators": ["ip_address"]},
    "osint_profile": {"label": "Public-source OSINT profile", "indicators": ["email", "domain", "ip_address", "username", "phone"]},
    "reputation_score": {"label": "Reputation signal summary", "indicators": ["email", "phone", "domain", "ip_address"]},
    "dehashed_search": {"label": "DeHashed exposure search", "indicators": ["email", "domain", "username"]},
    "darkweb_paste": {"label": "Public paste-site search", "indicators": ["email", "domain", "ip_address", "username", "phone"]},
    "shodan_domain": {"label": "Shodan domain lookup", "indicators": ["domain"]},
    "shodan_host": {"label": "Shodan host lookup", "indicators": ["ip_address"]},
    "virustotal": {"label": "VirusTotal lookup", "indicators": ["domain", "ip_address"]},
    "hibp_email": {"label": "Have I Been Pwned breach lookup", "indicators": ["email"]},
    "hunter_email": {"label": "Hunter email verification", "indicators": ["email"]},
    "hunter_domain": {"label": "Hunter domain search", "indicators": ["domain"]},
    "firecrawl_scrape": {"label": "Firecrawl URL scrape", "indicators": ["url"]},
    "crypto_trace": {"label": "Etherscan wallet trace", "indicators": ["wallet_address", "transaction_hash"]},
}

ENV_KEYS = {
    "DEHASHED_API_KEY": "DeHashed exposure search (requires DEHASHED_EMAIL too)",
    "DEHASHED_EMAIL": "DeHashed account email (requires DEHASHED_API_KEY too)",
    "SHODAN_API_KEY": "Shodan domain/host intelligence",
    "VT_API_KEY": "VirusTotal domain/IP intelligence",
    "HIBP_API_KEY": "Have I Been Pwned email breach lookup",
    "HUNTER_API_KEY": "Hunter email verification/domain search",
    "FIRECRAWL_API_KEY": "Firecrawl URL scraping",
    "ETHERSCAN_API_KEY": "Ethereum wallet tracing",
    "BRAVE_SEARCH_API_KEY": "Authenticated public-source web search used by OSINT and paste-search tools",
}

MAX_QUERY_LENGTH = 500

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()

def _safe_text(value: Any, limit: int = 500) -> str:
    return str(value or "").strip()[:limit]

def _request_json(url: str, *, headers: dict[str, str] | None = None, method: str = "GET", body: dict | None = None, timeout: float = 10) -> Any:
    data = json.dumps(body).encode() if body is not None else None
    req = Request(url, data=data, headers={"Accept": "application/json", "User-Agent": "MIA-RZ-Investigation/1.0", **(headers or {}), **({"Content-Type": "application/json"} if data else {})}, method=method)
    with urlopen(req, timeout=timeout) as response:
        raw = response.read(1_000_001)
    if len(raw) > 1_000_000:
        raise ValueError("Provider response exceeded the 1 MB safety limit")
    return json.loads(raw.decode("utf-8", "replace"))

def _http_error(exc: Exception) -> dict[str, Any]:
    if isinstance(exc, HTTPError):
        if exc.code in (401, 403): return {"status": "auth_failed", "error": "Provider rejected or lacks permission for this credential."}
        if exc.code == 404: return {"status": "not_found", "error": "Provider has no matching record."}
        if exc.code == 429: return {"status": "rate_limited", "error": "Provider rate limit reached."}
        return {"status": "provider_error", "error": f"Provider returned HTTP {exc.code}."}
    if isinstance(exc, (TimeoutError, URLError, socket.timeout)):
        return {"status": "unavailable", "error": "Provider request timed out or was unreachable."}
    if isinstance(exc, RuntimeError) and "web search" in str(exc).lower():
        return {"status": "unavailable", "error": "Public web search is currently unavailable. Configure BRAVE_SEARCH_API_KEY for more reliable search."}
    return {"status": "error", "error": f"{type(exc).__name__}: {str(exc)[:180]}"}

def _search(query: str, limit: int = 5) -> dict[str, Any]:
    # Reuse MIA-RZ's existing bounded, HTTPS-only public web search implementation.
    from app import capabilities as cap_services
    return cap_services.web_research(query[:MAX_QUERY_LENGTH], max(1, min(limit, 5)))

def _dns(domain: str) -> dict[str, Any]:
    if not domain or len(domain) > 253 or not re.fullmatch(r"[A-Za-z0-9.-]+", domain):
        return {"status": "invalid_input", "error": "Provide a valid domain name."}
    try:
        answers = sorted({item[4][0] for item in socket.getaddrinfo(domain, None, type=socket.SOCK_STREAM)})
        return {"status": "success", "source": "system_dns", "domain": domain, "addresses": answers, "timestamp": _now()}
    except Exception as exc:
        return {**_http_error(exc), "source": "system_dns", "domain": domain, "timestamp": _now()}

def _reverse_dns(value: str) -> dict[str, Any]:
    try:
        ip = ipaddress.ip_address(value)
        hostname, aliases, addresses = socket.gethostbyaddr(str(ip))
        return {"status": "success", "source": "system_reverse_dns", "ip_address": str(ip), "hostname": hostname, "aliases": aliases[:20], "timestamp": _now()}
    except ValueError:
        return {"status": "invalid_input", "error": "Provide a valid IP address."}
    except Exception as exc:
        return {**_http_error(exc), "source": "system_reverse_dns", "ip_address": value, "timestamp": _now()}

def _result_mentions_indicator(item: dict[str, Any], value: str, kind: str) -> bool:
    """Require a visible connection between a public-search result and its target."""
    title = _safe_text(item.get("title"), 2000).casefold()
    url = _safe_text(item.get("url"), 2000).casefold()
    snippet = _safe_text(item.get("snippet"), 2000).casefold()
    haystack = " ".join((title, url, snippet))
    needle = value.strip().casefold()
    if not needle or not haystack:
        return False
    if kind == "phone":
        digits = re.sub(r"\D", "", needle)
        return len(digits) >= 7 and digits in re.sub(r"\D", "", haystack)
    if kind == "ip_address":
        try:
            canonical = str(ipaddress.ip_address(value))
        except ValueError:
            return False
        return re.search(r"(?<![0-9A-Fa-f:.])" + re.escape(canonical) + r"(?![0-9A-Fa-f:.])", haystack) is not None
    if kind == "domain":
        # URLs can refer to the target or one of its subdomains. In prose, require
        # a whole-domain token so "notexample.com.evil" is not a false match.
        parsed = urlparse(url if "://" in url else "https://" + url)
        host = (parsed.hostname or "").casefold().rstrip(".")
        if host == needle or host.endswith("." + needle):
            return True
        return re.search(r"(?<![A-Za-z0-9.-])" + re.escape(needle) + r"(?![A-Za-z0-9.-])", title + " " + snippet) is not None
    # A direct textual match is a relevance gate, not proof that the result is adverse.
    return needle in haystack


def _is_reserved_example_domain(value: str) -> bool:
    """Recognize documentation example domains so they are never treated as risk evidence."""
    domain = value.strip().casefold().rstrip(".")
    return any(domain == reserved or domain.endswith("." + reserved) for reserved in ("example.com", "example.net", "example.org"))


def _osint(value: str, kind: str) -> dict[str, Any]:
    query = f'{kind} "{value}"'
    try:
        result = _search(query, 5)
        items = result.get("results", []) if isinstance(result, dict) else []
        candidates = [{"title": _safe_text(x.get("title"), 300), "url": _safe_text(x.get("url"), 1500), "snippet": _safe_text(x.get("snippet"), 700)} for x in items[:5] if isinstance(x, dict)]
        relevant = [item for item in candidates if _result_mentions_indicator(item, value, kind)]
        return {
            "status": "success" if relevant else "no_data",
            "source": result.get("source", "public_web_search") if isinstance(result, dict) else "public_web_search",
            "query_type": kind,
            "query": value,
            "result_count": len(relevant),
            "candidate_result_count": len(candidates),
            "ignored_irrelevant_result_count": len(candidates) - len(relevant),
            "results": relevant,
            "relevance_policy": "Only results visibly mentioning the indicator in title, URL, or snippet are retained.",
            "limitations": ["Public search results are leads, not verified identity or legal facts.", "A textual match does not establish adverse activity or identity.", "No results do not prove that no records exist."],
            "timestamp": _now(),
        }
    except Exception as exc:
        return {**_http_error(exc), "source": "public_web_search", "query_type": kind, "query": value, "timestamp": _now()}

def _provider(name: str, value: str, kind: str, extra: dict | None = None) -> dict[str, Any]:
    extra = extra or {}
    try:
        if name == "dehashed_search":
            key, email = os.getenv("DEHASHED_API_KEY", "").strip(), os.getenv("DEHASHED_EMAIL", "").strip()
            if not key or not email: return {"status": "not_configured", "required_env": ["DEHASHED_API_KEY", "DEHASHED_EMAIL"]}
            import base64
            auth = base64.b64encode(f"{email}:{key}".encode()).decode()
            data = _request_json("https://api.dehashed.com/search?" + urlencode({"query": value}), headers={"Authorization": f"Basic {auth}"})
            return {"status": "success", "source": "dehashed", "data": data}
        if name in {"shodan_domain", "shodan_host"}:
            key = os.getenv("SHODAN_API_KEY", "").strip()
            if not key: return {"status": "not_configured", "required_env": ["SHODAN_API_KEY"]}
            if name == "shodan_host": url = f"https://api.shodan.io/shodan/host/{quote(value, safe='')}?key={quote(key)}"
            else: url = "https://api.shodan.io/dns/resolve?" + urlencode({"name": value, "key": key})
            return {"status": "success", "source": "shodan_api", "data": _request_json(url)}
        if name == "virustotal":
            key = os.getenv("VT_API_KEY", "").strip()
            if not key: return {"status": "not_configured", "required_env": ["VT_API_KEY"]}
            endpoint = "domains" if kind == "domain" else "ip_addresses"
            data = _request_json(f"https://www.virustotal.com/api/v3/{endpoint}/{quote(value, safe='')}", headers={"x-apikey": key})
            attrs = ((data.get("data") or {}).get("attributes") or {})
            return {"status": "success", "source": "virustotal", "data": {"id": (data.get("data") or {}).get("id"), "reputation": attrs.get("reputation"), "last_analysis_stats": attrs.get("last_analysis_stats"), "last_analysis_date": attrs.get("last_analysis_date"), "categories": attrs.get("categories"), "tags": (attrs.get("tags") or [])[:30]}}
        if name == "hibp_email":
            key = os.getenv("HIBP_API_KEY", "").strip()
            if not key: return {"status": "not_configured", "required_env": ["HIBP_API_KEY"]}
            data = _request_json("https://haveibeenpwned.com/api/v3/breachedaccount/" + quote(value, safe="") + "?truncateResponse=false", headers={"hibp-api-key": key, "User-Agent": "MIA-RZ-Investigation"})
            return {"status": "success", "source": "haveibeenpwned", "breach_count": len(data) if isinstance(data, list) else 0, "breaches": [{"name": x.get("Name"), "title": x.get("Title"), "domain": x.get("Domain"), "breach_date": x.get("BreachDate"), "data_classes": x.get("DataClasses", [])} for x in data[:50]] if isinstance(data, list) else []}
        if name == "hunter_email":
            key = os.getenv("HUNTER_API_KEY", "").strip()
            if not key: return {"status": "not_configured", "required_env": ["HUNTER_API_KEY"]}
            data = _request_json("https://api.hunter.io/v2/email-verifier?" + urlencode({"email": value, "api_key": key}))
            return {"status": "success", "source": "hunter", "data": data.get("data", {})}
        if name == "hunter_domain":
            key = os.getenv("HUNTER_API_KEY", "").strip()
            if not key: return {"status": "not_configured", "required_env": ["HUNTER_API_KEY"]}
            data = _request_json("https://api.hunter.io/v2/domain-search?" + urlencode({"domain": value, "api_key": key, "limit": 10}))
            return {"status": "success", "source": "hunter", "data": data.get("data", {})}
        if name == "firecrawl_scrape":
            key = os.getenv("FIRECRAWL_API_KEY", "").strip()
            if not key: return {"status": "not_configured", "required_env": ["FIRECRAWL_API_KEY"]}
            parsed = urlparse(value)
            if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
                return {"status": "invalid_input", "error": "Only public HTTPS URLs are accepted."}
            host = parsed.hostname.rstrip(".").lower()
            if host in {"localhost", "localhost.localdomain"} or host.endswith((".localhost", ".local", ".internal")):
                return {"status": "invalid_input", "error": "Local or internal hosts are blocked."}
            try:
                resolved = {item[4][0] for item in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)}
                if not resolved or any((ipaddress.ip_address(addr).is_private or ipaddress.ip_address(addr).is_loopback or ipaddress.ip_address(addr).is_link_local or ipaddress.ip_address(addr).is_reserved or ipaddress.ip_address(addr).is_multicast or ipaddress.ip_address(addr).is_unspecified) for addr in resolved):
                    return {"status": "invalid_input", "error": "URLs resolving to non-public IP addresses are blocked."}
            except Exception:
                return {"status": "invalid_input", "error": "The target host could not be safely resolved."}
            data = _request_json("https://api.firecrawl.dev/v1/scrape", method="POST", headers={"Authorization": f"Bearer {key}"}, body={"url": value, "formats": ["markdown"], "onlyMainContent": True}, timeout=20)
            doc = data.get("data", {}) if isinstance(data, dict) else {}
            return {"status": "success", "source": "firecrawl", "title": (doc.get("metadata") or {}).get("title"), "url": (doc.get("metadata") or {}).get("sourceURL", value), "markdown": _safe_text(doc.get("markdown"), 12000)}
        if name == "crypto_trace":
            key = os.getenv("ETHERSCAN_API_KEY", "").strip()
            if not key: return {"status": "not_configured", "required_env": ["ETHERSCAN_API_KEY"]}
            if kind == "transaction_hash":
                data = _request_json("https://api.etherscan.io/api?" + urlencode({"module": "proxy", "action": "eth_getTransactionByHash", "txhash": value, "apikey": key}))
                return {"status": "success", "source": "etherscan", "data": data.get("result")}
            params = {"module": "account", "action": "txlist", "address": value, "startblock": 0, "endblock": 99999999, "page": 1, "offset": 20, "sort": "desc", "apikey": key}
            data = _request_json("https://api.etherscan.io/api?" + urlencode(params))
            result = data.get("result", [])
            if not isinstance(result, list): result = []
            return {"status": "success" if result else "no_data", "source": "etherscan", "address": value, "transactions": [{"hash": x.get("hash"), "from": x.get("from"), "to": x.get("to"), "value_wei": x.get("value"), "timestamp": x.get("timeStamp"), "block": x.get("blockNumber")} for x in result[:20]], "transaction_count_returned": len(result)}
    except Exception as exc:
        return _http_error(exc)
    return {"status": "unsupported_tool", "error": "Tool is not implemented."}

def run_tool(tool_name: str, indicator_type: str, value: str) -> dict[str, Any]:
    """Run one allowlisted investigation tool with bounded input and safe output."""
    if tool_name not in TOOL_CATALOG: return {"status": "unsupported_tool", "error": "Tool is not allowlisted."}
    value = _safe_text(value, MAX_QUERY_LENGTH)
    if not value: return {"status": "invalid_input", "error": "Indicator value is required."}
    if indicator_type not in TOOL_CATALOG[tool_name]["indicators"]: return {"status": "not_applicable", "error": "Tool does not support this indicator type."}
    if tool_name == "dns_lookup": return _dns(value)
    if tool_name == "reverse_dns": return _reverse_dns(value)
    if tool_name == "osint_profile": return _osint(value, indicator_type)
    if tool_name == "reputation_score":
        result = _osint(value, indicator_type)
        return {"status": result.get("status"), "source": result.get("source"), "indicator_type": indicator_type, "result_count": result.get("result_count", 0), "candidate_result_count": result.get("candidate_result_count", 0), "ignored_irrelevant_result_count": result.get("ignored_irrelevant_result_count", 0), "signals": result.get("results", [])[:3], "interpretation": "Search-result presence is not a validated reputation score; verify each source manually."}
    if tool_name == "darkweb_paste":
        try:
            result = _search(f'site:ahmia.fi OR site:pastebin.com "{value}"', 5)
            items = result.get("results", []) if isinstance(result, dict) else []
            candidates = [{"title": _safe_text(x.get("title"), 300), "url": _safe_text(x.get("url"), 1500), "snippet": _safe_text(x.get("snippet"), 700)} for x in items[:5] if isinstance(x, dict)]
            relevant = [item for item in candidates if _result_mentions_indicator(item, value, indicator_type)]
            return {
                "status": "success" if relevant else "no_data",
                "source": result.get("source", "public_web_search") if isinstance(result, dict) else "public_web_search",
                "result_count": len(relevant),
                "candidate_result_count": len(candidates),
                "ignored_irrelevant_result_count": len(candidates) - len(relevant),
                "results": relevant,
                "relevance_policy": "A result is retained only when its title, URL, or snippet visibly contains the target indicator.",
                "limitations": ["This searches public web indexes only; it does not access private or authenticated dark-web services.", "A direct match is a lead, not proof of exposure or wrongdoing."],
            }
        except Exception as exc:
            return _http_error(exc)
    return _provider(tool_name, value, indicator_type)

def run_investigation(indicators: dict[str, Any], *, include_optional_providers: bool = True) -> dict[str, Any]:
    """Run the six-stage SafeNestT-style pipeline: validate, collect, enrich, correlate, assess, report."""
    if not isinstance(indicators, dict) or not indicators:
        raise ValueError("Provide at least one indicator, such as domain, email, ip_address, username, phone, url, wallet_address, or transaction_hash.")
    allowed_types = {"email", "domain", "ip_address", "username", "phone", "url", "wallet_address", "transaction_hash"}
    normalized = {}
    for key, raw in indicators.items():
        if key not in allowed_types or raw in (None, "", []): continue
        if not isinstance(raw, str) or len(raw.strip()) > MAX_QUERY_LENGTH:
            raise ValueError(f"Indicator '{key}' must be a string of at most {MAX_QUERY_LENGTH} characters.")
        normalized[key] = raw.strip()
    if not normalized: raise ValueError("No supported, non-empty indicators were supplied.")

    run_id = uuid.uuid4().hex
    started = time.monotonic()
    stages: list[dict[str, Any]] = []
    calls: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    stages.append({"phase": 1, "name": "input_validation", "status": "success", "indicator_types": sorted(normalized)})

    def call(tool: str, kind: str, value: str) -> None:
        t0 = time.monotonic()
        try:
            result = run_tool(tool, kind, value)
        except Exception as exc:
            # One unavailable adapter must not prevent the rest of the investigation.
            result = {"status": "error", "error": f"{type(exc).__name__}: {str(exc)[:180]}"}
        entry = {"tool": tool, "label": TOOL_CATALOG[tool]["label"], "indicator_type": kind, "target": value, "status": result.get("status", "unknown"), "duration_ms": round((time.monotonic() - t0) * 1000), "result": result}
        calls.append(entry)
        has_payload = result.get("status") == "success" and any(result.get(k) not in (None, "", [], {}, 0) for k in ("addresses", "hostname", "results", "data", "breaches", "transactions", "markdown", "result_count"))
        if has_payload:
            reserved_example = kind == "domain" and _is_reserved_example_domain(value)
            finding_type = "contextual_information" if reserved_example else "external_observation"
            findings.append({
                "finding_id": f"F{len(findings)+1:03d}",
                "type": finding_type,
                "title": f"{TOOL_CATALOG[tool]['label']} returned observations",
                "description": f"{TOOL_CATALOG[tool]['label']} returned data for the supplied {kind} indicator; this is an observation, not a validated risk conclusion.",
                "evidence_relevance": "contextual_only" if reserved_example else ("indicator_matched" if tool in {"dns_lookup", "reverse_dns", "osint_profile", "reputation_score", "darkweb_paste"} else "provider_record_needs_validation"),
                "confidence_level": "low",
                "confidence_basis": "Provider response was received; the response does not by itself establish maliciousness, identity, or wrongdoing.",
                "context": "Reserved documentation example domain; results must not be interpreted as adverse risk evidence." if reserved_example else None,
                "source": result.get("source", tool),
                "target": value,
                "tool": tool,
                "evidence": result,
                # Reserved documentation domains are context-only; do not inflate the
                # substantive human-review queue with their routine DNS observations.
                "requires_human_review": not reserved_example,
            })

    # Phase 2: collection and enrichment, routed only to applicable indicator types.
    for kind, value in normalized.items():
        for tool, spec in TOOL_CATALOG.items():
            if kind not in spec["indicators"]: continue
            if not include_optional_providers and tool not in {"dns_lookup", "reverse_dns", "osint_profile", "reputation_score", "darkweb_paste"}: continue
            call(tool, kind, value)
    stages.append({"phase": 2, "name": "tool_collection", "status": "success" if any(x["status"] == "success" for x in calls) else "partial", "tool_calls": len(calls), "successful_tools": sum(x["status"] == "success" for x in calls)})

    # Phase 3: provenance and evidence normalization; preserve each provider result.
    for item in findings:
        item["provenance"] = {"run_id": run_id, "tool": item["tool"], "source": item["source"], "collected_at": _now(), "indicator_type": next((k for k,v in normalized.items() if v == item["target"]), "unknown")}
    stages.append({"phase": 3, "name": "evidence_normalization", "status": "success", "findings": len(findings)})

    # Phase 4: lightweight correlation. It records shared targets only; it does not assert guilt.
    sources = sorted({f["source"] for f in findings})
    stages.append({"phase": 4, "name": "correlation", "status": "success", "unique_sources": len(sources), "correlation_note": "Multiple source observations are leads, not proof of identity, fraud, or wrongdoing."})

    # Phase 5: transparent assessment; never promote absence of data to a clean result.
    failures = [x for x in calls if x["status"] not in {"success", "no_data", "not_configured", "not_applicable"}]
    configured = {key: bool(os.getenv(key, "").strip()) for key in ENV_KEYS}
    contextual_count = sum(item.get("type") == "contextual_information" for item in findings)
    substantive_observation_count = len(findings) - contextual_count
    stages.append({"phase": 5, "name": "risk_assessment", "status": "success", "finding_count": len(findings), "contextual_findings": contextual_count, "observations_requiring_review": substantive_observation_count, "provider_errors": len(failures), "risk_level": "undetermined", "note": "Provider success and search-result presence are not risk signals by themselves. No automated guilt/innocence or fraud determination is made."})

    # Search accounting is explicit: unavailable searches are not counted as zero-result searches.
    search_calls = [item for item in calls if item["tool"] in {"osint_profile", "reputation_score", "darkweb_paste"}]
    completed_searches = [item for item in search_calls if item.get("status") in {"success", "no_data"} and isinstance(item.get("result"), dict) and "candidate_result_count" in item["result"]]
    search_metrics = {
        "searches_attempted": len(search_calls),
        "searches_with_candidate_counts": len(completed_searches),
        "candidate_results": sum(int(item["result"].get("candidate_result_count", 0) or 0) for item in completed_searches),
        "relevant_results_retained": sum(int(item["result"].get("result_count", 0) or 0) for item in completed_searches),
        "irrelevant_results_discarded": sum(int(item["result"].get("ignored_irrelevant_result_count", 0) or 0) for item in completed_searches),
        "unavailable_searches": sum(item.get("status") == "unavailable" for item in search_calls),
        "note": "Unavailable searches have no candidate count; they are not evidence of zero matches.",
    }

    # Phase 6: machine-readable report with explicit gaps and config readiness.
    stages.append({"phase": 6, "name": "report_generation", "status": "success", "report_sections": ["summary", "tool_calls", "findings", "configuration", "limitations", "search_metrics"]})
    return {
        "status": "completed" if any(x["status"] == "success" for x in calls) else "partial",
        "run_id": run_id,
        "started_at": datetime.fromtimestamp(time.time() - (time.monotonic() - started), timezone.utc).isoformat(),
        "completed_at": _now(),
        "duration_ms": round((time.monotonic() - started) * 1000),
        "pipeline": "safenestt_investigation_v1",
        "phases": stages,
        "indicators": normalized,
        "tool_calls": calls,
        "findings": findings,
        "summary": {"tools_attempted": len(calls), "tools_succeeded": sum(x["status"] == "success" for x in calls), "providers_not_configured": sum(x["status"] == "not_configured" for x in calls), "findings_requiring_review": sum(bool(item.get("requires_human_review")) for item in findings), "contextual_findings": contextual_count, "observations_requiring_review": substantive_observation_count, "sources": sources},
        "search_metrics": search_metrics,
        "configuration": {"providers": [{"env_key": key, "purpose": purpose, "configured": configured[key]} for key, purpose in ENV_KEYS.items()]},
        "limitations": ["External data can be incomplete, delayed, or incorrect.", "A provider success only confirms a response was received; it does not validate the conclusion.", "Public-search findings are retained only when the title, URL, or snippet visibly matches the indicator.", "Confidence is qualitative and describes the evidence status, not the probability of wrongdoing.", "Reserved example domains are classified as contextual information, not adverse risk evidence.", "Substantive findings require human review and corroboration; contextual-only observations do not count as adverse findings.", "Missing credentials are reported as not_configured and are never silently treated as a clean result."],
    }
