"""Per-user OAuth/API connectors for Gmail, GitHub App, and Stripe."""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import time
import uuid
from urllib.parse import urlencode
import base64

import httpx
from cryptography.fernet import InvalidToken
from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from app import capabilities as cap
from app import plugin_manager

router = APIRouter(prefix="/v1/connectors", tags=["app-connectors"])
STATE_TTL = 600
MAX_RESPONSE = 1_000_000


def _user(request: Request, authorization: str = Header(default=""), x_api_key: str = Header(default="")):
    return __import__("app.main", fromlist=["require_app_user"]).require_app_user(request, authorization, x_api_key)


def _uid(user: dict) -> str:
    return str(user.get("id", ""))


def _db():
    conn, kind = cap._db()
    real = "DOUBLE PRECISION" if kind == "postgres" else "REAL"
    blob = "BYTEA" if kind == "postgres" else "BLOB"
    conn.execute(f"""CREATE TABLE IF NOT EXISTS rz_app_connector_states (
      state_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL, provider TEXT NOT NULL,
      expires_at {real} NOT NULL)""")
    conn.execute(f"""CREATE TABLE IF NOT EXISTS rz_app_connectors (
      user_id TEXT NOT NULL, provider TEXT NOT NULL, external_id TEXT NOT NULL,
      label TEXT NOT NULL, token_cipher {blob} NOT NULL, connected_at {real} NOT NULL,
      PRIMARY KEY(user_id, provider))""")
    conn.commit()
    return conn, kind


def _provider_config(provider: str) -> dict:
    if provider == "google":
        return {"client_id": os.getenv("GOOGLE_CLIENT_ID", "").strip(),
                "client_secret": os.getenv("GOOGLE_CLIENT_SECRET", "").strip(),
                "redirect_uri": os.getenv("GOOGLE_REDIRECT_URI", "").strip()}
    if provider == "github":
        return {"client_id": os.getenv("GITHUB_APP_CLIENT_ID", "").strip(),
                "client_secret": os.getenv("GITHUB_APP_CLIENT_SECRET", "").strip(),
                "redirect_uri": os.getenv("GITHUB_APP_REDIRECT_URI", "").strip()}
    if provider == "stripe":
        return {"client_id": os.getenv("STRIPE_CONNECT_CLIENT_ID", "").strip(),
                "client_secret": os.getenv("STRIPE_SECRET_KEY", "").strip(),
                "redirect_uri": os.getenv("STRIPE_CONNECT_REDIRECT_URI", "").strip()}
    raise HTTPException(404, "Connector not found")


def _configured(provider: str) -> bool:
    cfg = _provider_config(provider)
    if provider == "github":
        return all(cfg.get(k) for k in ("client_id", "client_secret", "redirect_uri")) and cfg["redirect_uri"].startswith("https://")
    return all(cfg.get(k) for k in ("client_id", "client_secret", "redirect_uri")) and cfg["redirect_uri"].startswith("https://")


def _state_create(user_id: str, provider: str) -> str:
    state = secrets.token_urlsafe(32)
    conn, kind = _db()
    try:
        q = "INSERT INTO rz_app_connector_states(state_hash,user_id,provider,expires_at) VALUES(%s,%s,%s,%s)" if kind == "postgres" else "INSERT INTO rz_app_connector_states(state_hash,user_id,provider,expires_at) VALUES(?,?,?,?)"
        conn.execute(q, (hashlib.sha256(state.encode()).hexdigest(), user_id, provider, time.time() + STATE_TTL))
        conn.commit()
    finally:
        conn.close()
    return state


def _state_consume(state: str, provider: str) -> str:
    if not state or len(state) > 200:
        raise HTTPException(400, "Invalid or expired authorization state")
    digest = hashlib.sha256(state.encode()).hexdigest()
    conn, kind = _db()
    try:
        q = "DELETE FROM rz_app_connector_states WHERE state_hash=%s AND provider=%s RETURNING user_id,expires_at" if kind == "postgres" else "DELETE FROM rz_app_connector_states WHERE state_hash=? AND provider=? RETURNING user_id,expires_at"
        row = conn.execute(q, (digest, provider)).fetchone()
        conn.commit()
        if not row or float(row[1]) < time.time():
            raise HTTPException(400, "Invalid or expired authorization state")
        return str(row[0])
    finally:
        conn.close()


def _save(user_id: str, provider: str, external_id: str, label: str, credentials: dict):
    cipher = plugin_manager._fernet().encrypt(json.dumps(credentials, separators=(",", ":")).encode())
    conn, kind = _db()
    try:
        q = ("INSERT INTO rz_app_connectors(user_id,provider,external_id,label,token_cipher,connected_at) VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT(user_id,provider) DO UPDATE SET external_id=EXCLUDED.external_id,label=EXCLUDED.label,token_cipher=EXCLUDED.token_cipher,connected_at=EXCLUDED.connected_at" if kind == "postgres" else
             "INSERT INTO rz_app_connectors(user_id,provider,external_id,label,token_cipher,connected_at) VALUES(?,?,?,?,?,?) ON CONFLICT(user_id,provider) DO UPDATE SET external_id=excluded.external_id,label=excluded.label,token_cipher=excluded.token_cipher,connected_at=excluded.connected_at")
        conn.execute(q, (user_id, provider, external_id[:255], label[:255], cipher, time.time())); conn.commit()
    finally:
        conn.close()


def _read(user_id: str, provider: str) -> dict:
    conn, kind = _db()
    try:
        q = "SELECT external_id,label,token_cipher,connected_at FROM rz_app_connectors WHERE user_id=%s AND provider=%s" if kind == "postgres" else "SELECT external_id,label,token_cipher,connected_at FROM rz_app_connectors WHERE user_id=? AND provider=?"
        row = conn.execute(q, (user_id, provider)).fetchone()
    finally:
        conn.close()
    if not row:
        raise KeyError("Connector not connected")
    try:
        credentials = json.loads(plugin_manager._fernet().decrypt(bytes(row[2])).decode())
    except (InvalidToken, ValueError):
        raise HTTPException(503, "Connector credentials cannot be decrypted; check the stable RZ_MCP_ENCRYPTION_KEY.") from None
    return {"provider": provider, "external_id": row[0], "label": row[1], "credentials": credentials, "connected_at": row[3]}


def _delete(user_id: str, provider: str):
    conn, kind = _db()
    try:
        q = "DELETE FROM rz_app_connectors WHERE user_id=%s AND provider=%s" if kind == "postgres" else "DELETE FROM rz_app_connectors WHERE user_id=? AND provider=?"
        conn.execute(q, (user_id, provider)); conn.commit()
    finally:
        conn.close()


def _http_json(method: str, url: str, *, data=None, headers=None, params=None, auth=None):
    try:
        response = httpx.request(method, url, data=data, headers=headers, params=params, auth=auth, timeout=20, follow_redirects=False)
        if len(response.content) > MAX_RESPONSE:
            raise HTTPException(502, "Connected service returned an oversized response")
        response.raise_for_status()
        payload = response.json() if response.content else {}
        if not isinstance(payload, dict):
            raise HTTPException(502, "Connected service returned an unexpected response")
        return payload
    except HTTPException:
        raise
    except httpx.HTTPStatusError as exc:
        code = exc.response.status_code
        raise HTTPException(502, f"Connected service request failed (HTTP {code})") from None
    except (httpx.HTTPError, ValueError):
        raise HTTPException(502, "Could not reach the connected service") from None


def _oauth_exchange(provider: str, code: str, code_verifier: str = "") -> dict:
    cfg = _provider_config(provider)
    if provider == "google":
        token_data = {"code": code, "client_id": cfg["client_id"], "client_secret": cfg["client_secret"], "redirect_uri": cfg["redirect_uri"], "grant_type": "authorization_code"}
        if code_verifier:
            token_data["code_verifier"] = code_verifier
        token = _http_json("POST", "https://oauth2.googleapis.com/token", data=token_data)
        if not token.get("access_token"):
            raise HTTPException(502, "Google did not return an access token")
        token["expires_at"] = time.time() + int(token.get("expires_in", 3600))
        headers = {"Authorization": "Bearer " + token["access_token"]}
        profile = _http_json("GET", "https://openidconnect.googleapis.com/v1/userinfo", headers=headers)
        return {"credentials": token, "external_id": profile.get("sub") or profile.get("email") or "google-user", "label": profile.get("email") or "Google account"}
    if provider == "github":
        token_data = {"client_id": cfg["client_id"], "client_secret": cfg["client_secret"], "code": code, "redirect_uri": cfg["redirect_uri"]}
        if code_verifier:
            token_data["code_verifier"] = code_verifier
        token = _http_json("POST", "https://github.com/login/oauth/access_token", data=token_data, headers={"Accept": "application/json"})
        if not token.get("access_token"):
            raise HTTPException(502, "GitHub did not return an access token")
        if token.get("expires_in"):
            token["expires_at"] = time.time() + int(token["expires_in"])
        profile = _http_json("GET", "https://api.github.com/user", headers={"Authorization": "Bearer " + token["access_token"], "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"})
        return {"credentials": token, "external_id": str(profile.get("id", "github-user")), "label": "@" + str(profile.get("login", "GitHub user"))}
    if provider == "stripe":
        token = _http_json("POST", "https://connect.stripe.com/oauth/token", data={"client_secret": cfg["client_secret"], "grant_type": "authorization_code", "code": code})
        if not token.get("access_token"):
            raise HTTPException(502, "Stripe did not return an access token")
        token["mode"] = "connect_oauth"
        return {"credentials": token, "external_id": str(token.get("stripe_user_id", "stripe-account")), "label": str(token.get("stripe_user_id", "Stripe account"))}
    raise HTTPException(404, "Connector not found")


class StripeKeyBody(BaseModel):
    restricted_key: str = Field(min_length=20, max_length=300)


@router.get("")
def list_connectors(user: dict = Depends(_user)):
    uid = _uid(user)
    connected = {}
    conn, kind = _db()
    try:
        q = "SELECT provider,external_id,label,connected_at FROM rz_app_connectors WHERE user_id=%s" if kind == "postgres" else "SELECT provider,external_id,label,connected_at FROM rz_app_connectors WHERE user_id=?"
        for row in conn.execute(q, (uid,)).fetchall():
            connected[row[0]] = {"connected": True, "external_id": row[1], "label": row[2], "connected_at": row[3]}
    finally:
        conn.close()
    providers = []
    for provider, label, description in (("google", "Gmail", "Search and read your Gmail messages"), ("github", "GitHub", "Repositories, issues, pull requests and code"), ("stripe", "Stripe", "Customers, payments, invoices and subscriptions")):
        item = {"id": provider, "name": label, "description": description,
                "configured": True if provider == "stripe" else _configured(provider),
                "oauth_configured": _configured(provider), "connected": provider in connected}
        item.update(connected.get(provider, {})); providers.append(item)
    return {"connectors": providers}


def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
    return verifier, challenge


def _pkce_cookie_name(state: str) -> str:
    return "rz_oauth_pkce_" + hashlib.sha256(state.encode("utf-8")).hexdigest()[:32]


@router.get("/{provider}/connect")
def connect(provider: str, response: Response, user: dict = Depends(_user)):
    if provider not in {"google", "github", "stripe"}:
        raise HTTPException(404, "Connector not found")
    cfg = _provider_config(provider)
    if not _configured(provider):
        if provider == "stripe":
            raise HTTPException(503, "Configure Stripe Connect OAuth before connecting a Stripe account.")
        raise HTTPException(503, f"{provider.title()} OAuth is not configured on RZ-AI yet")
    state = _state_create(_uid(user), provider)
    if provider in {"google", "github"}:
        verifier, challenge = _pkce_pair()
        response.set_cookie(_pkce_cookie_name(state), verifier, max_age=STATE_TTL,
                            httponly=True, secure=cfg["redirect_uri"].startswith("https://"),
                            samesite="lax", path=f"/v1/connectors/{provider}/callback")
    if provider == "google":
        params = {"client_id": cfg["client_id"], "redirect_uri": cfg["redirect_uri"], "response_type": "code", "scope": "openid email https://www.googleapis.com/auth/gmail.readonly", "access_type": "offline", "include_granted_scopes": "true", "prompt": "consent", "state": state, "code_challenge": challenge, "code_challenge_method": "S256"}
        url = "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(params)
    elif provider == "github":
        params = {"client_id": cfg["client_id"], "redirect_uri": cfg["redirect_uri"], "state": state, "code_challenge": challenge, "code_challenge_method": "S256", "allow_signup": "false"}
        url = "https://github.com/login/oauth/authorize?" + urlencode(params)
    else:
        url = "https://connect.stripe.com/oauth/authorize?" + urlencode({"response_type": "code", "client_id": cfg["client_id"], "scope": "read_only", "state": state, "redirect_uri": cfg["redirect_uri"]})
    return {"authorization_url": url, "expires_in_seconds": STATE_TTL}


@router.get("/{provider}/callback")
def callback(provider: str, request: Request, code: str = "", state: str = "", error: str = ""):
    if provider not in {"google", "github", "stripe"}:
        raise HTTPException(404, "Connector not found")
    if error:
        if state:
            try:
                _state_consume(state, provider)
            except HTTPException:
                pass
        response = RedirectResponse("/settings/mcp?connector=declined", status_code=303)
        if state and provider in {"google", "github"}:
            response.delete_cookie(_pkce_cookie_name(state), path=f"/v1/connectors/{provider}/callback")
        return response
    if not code or len(code) > 4096:
        raise HTTPException(400, "Authorization code missing or invalid")
    user_id = _state_consume(state, provider)
    verifier = ""
    if provider in {"google", "github"}:
        verifier = request.cookies.get(_pkce_cookie_name(state), "")
        if not verifier or len(verifier) > 200:
            raise HTTPException(400, "OAuth verifier cookie missing; restart the app connection")
    result = _oauth_exchange(provider, code, code_verifier=verifier)
    _save(user_id, provider, result["external_id"], result["label"], result["credentials"])
    response = RedirectResponse("/settings/mcp?connector=" + provider + "_connected", status_code=303)
    if provider in {"google", "github"}:
        response.delete_cookie(_pkce_cookie_name(state), path=f"/v1/connectors/{provider}/callback")
    return response


@router.post("/stripe/api-key")
def connect_stripe_key(body: StripeKeyBody, user: dict = Depends(_user)):
    key = body.restricted_key.strip()
    if not key.startswith(("rk_live_", "rk_test_")):
        raise HTTPException(422, "Use a Stripe restricted API key (rk_live_ or rk_test_); do not use a secret or publishable key.")
    account = _http_json("GET", "https://api.stripe.com/v1/account", auth=(key, ""))
    external_id = str(account.get("id", ""))
    if not external_id:
        raise HTTPException(502, "Stripe did not identify the connected account")
    profile = account.get("business_profile") or {}
    label = str(profile.get("name") or account.get("email") or external_id)
    _save(_uid(user), "stripe", external_id, label, {"access_token": key, "mode": "restricted_key"})
    return {"connected": True, "provider": "stripe", "external_id": external_id, "label": label}


@router.delete("/{provider}")
def disconnect(provider: str, user: dict = Depends(_user)):
    if provider not in {"google", "github", "stripe"}:
        raise HTTPException(404, "Connector not found")
    _delete(_uid(user), provider)
    return {"disconnected": True, "provider": provider}


def _token(user_id: str, provider: str) -> str:
    record = _read(user_id, provider)
    credentials = record["credentials"]
    access = credentials.get("access_token", "")
    expires = float(credentials.get("expires_at", 0) or 0)
    if expires and expires < time.time() + 60 and credentials.get("refresh_token") and provider in {"google", "github"}:
        cfg = _provider_config(provider)
        endpoint = "https://oauth2.googleapis.com/token" if provider == "google" else "https://github.com/login/oauth/access_token"
        payload = {"grant_type": "refresh_token", "refresh_token": credentials["refresh_token"], "client_id": cfg["client_id"], "client_secret": cfg["client_secret"]}
        refreshed = _http_json("POST", endpoint, data=payload, headers={"Accept": "application/json"})
        credentials.update(refreshed)
        credentials["expires_at"] = time.time() + int(refreshed.get("expires_in", 3600))
        _save(user_id, provider, record["external_id"], record["label"], credentials)
        access = credentials.get("access_token", "")
    if not access:
        raise HTTPException(401, f"{provider.title()} connection needs to be re-authorized")
    return str(access)


def _stripe_auth(user_id: str):
    record = _read(user_id, "stripe")
    credentials = record["credentials"]
    if credentials.get("mode") == "connect_oauth":
        platform_key = os.getenv("STRIPE_SECRET_KEY", "").strip()
        if not platform_key:
            raise HTTPException(503, "Stripe Connect platform key is not configured")
        expected_live = bool(credentials.get("livemode"))
        if platform_key.startswith("sk_live_") != expected_live:
            raise HTTPException(503, "Stripe platform key mode does not match the connected account authorization")
        return platform_key, record["external_id"]
    return str(credentials.get("access_token", "")), None


def _get(url: str, token: str, *, params=None, stripe=False, stripe_account=None):
    headers = {"Accept": "application/json"}
    auth = (token, "") if stripe else None
    if stripe_account:
        headers["Stripe-Account"] = stripe_account
    if not stripe:
        headers["Authorization"] = "Bearer " + token
    if "api.github.com" in url:
        headers.update({"X-GitHub-Api-Version": "2022-11-28", "Accept": "application/vnd.github+json"})
    try:
        response = httpx.get(url, params=params, headers=headers, auth=auth, timeout=20, follow_redirects=False)
        if len(response.content) > MAX_RESPONSE:
            raise HTTPException(502, "Connected service returned an oversized response")
        response.raise_for_status(); payload = response.json()
        if not isinstance(payload, (dict, list)):
            raise HTTPException(502, "Connected service returned an unexpected response")
        return payload
    except HTTPException:
        raise
    except httpx.HTTPStatusError as exc:
        raise HTTPException(502, f"Connected service request failed (HTTP {exc.response.status_code})") from None
    except (httpx.HTTPError, ValueError):
        raise HTTPException(502, "Could not reach the connected service") from None


def tool_schemas(user_id: str) -> list[dict]:
    # Always expose a harmless inventory tool so MIA can explain which apps are
    # connected, even when no provider-specific tools are available.
    schemas = [{
        "type": "function",
        "function": {
            "name": "appconn_list_connected_apps",
            "description": "[Connected app inventory · read-only] List this account's connected integrations and the read-only tools available for each. Never returns credentials.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    }]
    specs = {
        "google": [
            ("gmail_search_messages", "Search the authenticated user's real Gmail mailbox using Gmail query syntax. For latest/newest requests, use a broad query for the requested scope, request a sufficiently large page, and keep calling this tool with the returned page_token until the requested number of messages is collected or nextPageToken is absent. Every result is retrieved from Gmail and individually verified by its real message ID and internalDate; never invent, infer, or fill missing fields. A page is not the whole mailbox when nextPageToken is present. Clearly disclose the exact query, scope, number returned, and whether more pages remain.", {"query": {"type": "string", "maxLength": 500}, "limit": {"type": "integer", "minimum": 1, "maximum": 50}, "page_token": {"type": "string", "maxLength": 2000}}, ["query"]),
            ("gmail_get_message", "Read the complete available body and metadata of one real Gmail message by its exact message ID returned from Gmail search. Return body format, whether content was truncated, and attachment metadata. Never summarize content that was not returned; do not fabricate missing body text or attachment contents.", {"message_id": {"type": "string", "maxLength": 200}}, ["message_id"]),
        ],
        "github": [
            ("github_list_repositories", "List repositories accessible to the connected GitHub account", {"limit": {"type": "integer", "minimum": 1, "maximum": 50}}, []),
            ("github_list_issues", "List issues for a repository, excluding pull requests", {"owner": {"type": "string"}, "repo": {"type": "string"}, "state": {"type": "string", "enum": ["open", "closed", "all"]}, "limit": {"type": "integer", "minimum": 1, "maximum": 30}}, ["owner", "repo"]),
            ("github_list_pull_requests", "List pull requests for a repository", {"owner": {"type": "string"}, "repo": {"type": "string"}, "state": {"type": "string", "enum": ["open", "closed", "all"]}, "limit": {"type": "integer", "minimum": 1, "maximum": 30}}, ["owner", "repo"]),
        ],
        "stripe": [
            ("stripe_list_customers", "List customers in the connected Stripe account", {"limit": {"type": "integer", "minimum": 1, "maximum": 25}, "email": {"type": "string", "maxLength": 255}}, []),
            ("stripe_list_payments", "List recent payment intents in the connected Stripe account", {"limit": {"type": "integer", "minimum": 1, "maximum": 25}}, []),
            ("stripe_list_invoices", "List invoices in the connected Stripe account", {"limit": {"type": "integer", "minimum": 1, "maximum": 25}, "customer": {"type": "string", "maxLength": 100}}, []),
        ],
    }
    for provider, items in specs.items():
        try:
            _read(user_id, provider)
        except KeyError:
            continue
        except Exception as exc:
            # One broken/expired connector must not suppress every other app's
            # tools for this owner. The inventory tool reports reauthorization.
            print("RZ_APP_CONNECTOR_UNAVAILABLE", {"provider": provider, "error_type": type(exc).__name__}, flush=True)
            continue
        for name, description, properties, required in items:
            schemas.append({"type": "function", "function": {"name": "appconn_" + name, "description": "[Connected app: " + provider.title() + " · read-only] " + description, "parameters": {"type": "object", "properties": properties, "required": required}}})
    return schemas


def _gmail_body_details(payload: dict) -> dict:
    """Decode the complete available text body; explicitly mark any safety truncation."""
    import base64
    found = []
    def walk(part):
        if not isinstance(part, dict):
            return
        mime = str(part.get("mimeType", "")).lower()
        data = (part.get("body") or {}).get("data")
        if data and mime in {"text/plain", "text/html"}:
            try:
                decoded = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", "replace")
                found.append((mime, decoded))
            except (ValueError, TypeError):
                pass
        for child in part.get("parts", []) or []:
            walk(child)
    walk(payload)
    selected = next(((mime, body) for mime, body in found if mime == "text/plain"), next(((mime, body) for mime, body in found if mime == "text/html"), ("text/plain", "")))
    mime, body = selected
    max_chars = 50000
    return {"body": body[:max_chars], "body_format": mime, "body_truncated": len(body) > max_chars, "body_characters": len(body)}


def _gmail_attachment_metadata(payload: dict) -> list[dict]:
    attachments = []
    def walk(part):
        if not isinstance(part, dict):
            return
        body = part.get("body") or {}
        filename = part.get("filename")
        if filename or body.get("attachmentId"):
            attachments.append({
                "filename": str(filename or "unnamed attachment")[:255],
                "mime_type": str(part.get("mimeType") or "application/octet-stream")[:120],
                "attachment_id": body.get("attachmentId"),
                "size": body.get("size"),
                "content_retrieved": False,
            })
        for child in part.get("parts", []) or []:
            walk(child)
    walk(payload)
    return attachments[:50]


def execute_tool(user_id: str, name: str, args: dict) -> dict:
    if not isinstance(args, dict) or len(json.dumps(args)) > 12000:
        raise HTTPException(422, "Connector tool arguments must be a JSON object under 12 KB")
    if name == "appconn_list_connected_apps":
        conn, kind = _db()
        try:
            q = ("SELECT provider,label,connected_at FROM rz_app_connectors WHERE user_id=%s ORDER BY provider"
                 if kind == "postgres" else
                 "SELECT provider,label,connected_at FROM rz_app_connectors WHERE user_id=? ORDER BY provider")
            rows = conn.execute(q, (user_id,)).fetchall()
        finally:
            conn.close()
        tool_map = {
            "google": ["appconn_gmail_search_messages", "appconn_gmail_get_message"],
            "github": ["appconn_github_list_repositories", "appconn_github_list_issues", "appconn_github_list_pull_requests"],
            "stripe": ["appconn_stripe_list_customers", "appconn_stripe_list_payments", "appconn_stripe_list_invoices"],
        }
        labels = {"google": "Gmail", "github": "GitHub", "stripe": "Stripe"}
        integrations = []
        for provider, label, connected_at in rows:
            try:
                _read(user_id, provider)
                status = "connected"
                available_tools = tool_map.get(provider, [])
            except Exception:
                status = "needs_reauthorization"
                available_tools = []
            integrations.append({
                "provider": provider,
                "name": labels.get(provider, provider.title()),
                "account": label,
                "status": status,
                "available_read_only_tools": available_tools,
                "connected_at": connected_at,
            })
        return {
            "count": len(integrations),
            "integrations": integrations,
            "note": "This inventory is scoped to the authenticated account. It contains connection status and tool names only; no credentials or message/payment contents.",
        }
    if name.startswith("appconn_gmail_"):
        token = _token(user_id, "google")
        if name == "appconn_gmail_search_messages":
            query = str(args.get("query", ""))[:500]
            limit = min(max(int(args.get("limit", 10)), 1), 50)
            page_token = str(args.get("page_token", "") or "")
            if len(page_token) > 2000:
                raise HTTPException(422, "Invalid Gmail page token")
            params = {"q": query, "maxResults": limit}
            if page_token:
                params["pageToken"] = page_token
            result = _get("https://gmail.googleapis.com/gmail/v1/users/me/messages", token, params=params)
            candidates = result.get("messages", []) if isinstance(result, dict) else []
            verified = []
            for candidate in candidates[:limit]:
                message_id = candidate.get("id") if isinstance(candidate, dict) else None
                if not isinstance(message_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", message_id):
                    continue
                metadata = _get(
                    "https://gmail.googleapis.com/gmail/v1/users/me/messages/" + message_id,
                    token,
                    params={"format": "metadata", "metadataHeaders": ["From", "To", "Subject", "Date"]},
                )
                internal_date = metadata.get("internalDate") if isinstance(metadata, dict) else None
                try:
                    received_ms = int(internal_date)
                except (TypeError, ValueError):
                    continue
                headers = {
                    item.get("name", "").lower(): item.get("value", "")
                    for item in (metadata.get("payload", {}) or {}).get("headers", [])
                    if isinstance(item, dict)
                }
                # Only return IDs and timestamps supplied by Gmail itself. No
                # Date-header parsing or model-generated fallback is permitted.
                if metadata.get("id") != message_id:
                    continue
                verified.append({
                    "id": message_id,
                    "threadId": metadata.get("threadId"),
                    "internalDate": str(received_ms),
                    "from": headers.get("from"),
                    "to": headers.get("to"),
                    "subject": headers.get("subject"),
                    "date": headers.get("date"),
                })
            verified.sort(key=lambda item: int(item["internalDate"]), reverse=True)
            return {
                "resultSizeEstimate": result.get("resultSizeEstimate", 0),
                "returnedCount": len(verified),
                "query": query,
                "pageSizeRequested": limit,
                "pageTokenUsed": bool(page_token),
                "nextPageToken": result.get("nextPageToken"),
                "hasMoreResults": bool(result.get("nextPageToken")),
                "searchScope": "Gmail query scope: " + (query or "all non-spam/non-trash mail"),
                "sort": "internalDate_desc_within_page",
                "timestamps_verified_by": "Gmail API messages.get",
                "messages": verified,
                "note": "These are only messages returned by this Gmail API page and individually verified by message ID and internalDate. Sorting is within this page; when nextPageToken is present, fetch subsequent pages before claiming a complete newest-N result. Do not add, replace, or embellish message details. Fetch complete available bodies only with gmail_get_message.",
            }
        if name == "appconn_gmail_get_message":
            message_id = str(args.get("message_id", ""))
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", message_id):
                raise HTTPException(422, "Invalid Gmail message ID")
            result = _get("https://gmail.googleapis.com/gmail/v1/users/me/messages/" + message_id, token, params={"format": "full"})
            headers = {x.get("name", "").lower(): x.get("value", "") for x in result.get("payload", {}).get("headers", []) if isinstance(x, dict)}
            payload = result.get("payload", {}) or {}
            body = _gmail_body_details(payload)
            return {"id": result.get("id"), "threadId": result.get("threadId"), "internalDate": result.get("internalDate"), "snippet": result.get("snippet"), "from": headers.get("from"), "to": headers.get("to"), "cc": headers.get("cc"), "bcc": headers.get("bcc"), "subject": headers.get("subject"), "date": headers.get("date"), **body, "attachments": _gmail_attachment_metadata(payload), "labels": result.get("labelIds", []), "source": "Gmail API messages.get(format=full)", "note": "This is the actual Gmail message returned for this authenticated account. The body is the complete available text/plain part, or text/html when no plain-text part exists, up to the stated safety limit. Attachment metadata is listed but attachment file contents were not retrieved. Never invent missing fields or attachment contents."}
        raise HTTPException(404, "Gmail tool not found")
    if name.startswith("appconn_github_"):
        token = _token(user_id, "github")
        if name == "appconn_github_list_repositories":
            return _get("https://api.github.com/user/repos", token, params={"per_page": min(max(int(args.get("limit", 20)), 1), 50), "sort": "updated", "affiliation": "owner,collaborator,organization_member"})
        owner, repo = str(args.get("owner", "")), str(args.get("repo", ""))
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", owner) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", repo) or ".." in owner or ".." in repo:
            raise HTTPException(422, "Valid repository owner and name are required")
        state = args.get("state", "open")
        if state not in {"open", "closed", "all"}:
            raise HTTPException(422, "Invalid issue state")
        limit = min(max(int(args.get("limit", 10)), 1), 30)
        if name == "appconn_github_list_issues":
            result = _get(f"https://api.github.com/repos/{owner}/{repo}/issues", token, params={"state": state, "per_page": limit})
            return {"items": [x for x in result if "pull_request" not in x] if isinstance(result, list) else result}
        if name == "appconn_github_list_pull_requests":
            return _get(f"https://api.github.com/repos/{owner}/{repo}/pulls", token, params={"state": state, "per_page": limit})
        raise HTTPException(404, "GitHub tool not found")
    if name.startswith("appconn_stripe_"):
        token, stripe_account = _stripe_auth(user_id)
        if not token:
            raise HTTPException(401, "Stripe connection needs to be re-authorized")
        limit = min(max(int(args.get("limit", 10)), 1), 25)
        if name == "appconn_stripe_list_customers":
            params = {"limit": limit}
            if args.get("email"): params["email"] = str(args["email"])[:255]
            return _get("https://api.stripe.com/v1/customers", token, params=params, stripe=True, stripe_account=stripe_account)
        if name == "appconn_stripe_list_payments":
            return _get("https://api.stripe.com/v1/payment_intents", token, params={"limit": limit}, stripe=True, stripe_account=stripe_account)
        if name == "appconn_stripe_list_invoices":
            params = {"limit": limit}
            if args.get("customer"): params["customer"] = str(args["customer"])[:100]
            return _get("https://api.stripe.com/v1/invoices", token, params=params, stripe=True, stripe_account=stripe_account)
        raise HTTPException(404, "Stripe tool not found")
    raise HTTPException(404, "Connector tool not found")
