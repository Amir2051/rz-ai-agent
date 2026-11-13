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

import httpx
from cryptography.fernet import InvalidToken
from fastapi import APIRouter, Depends, Header, HTTPException, Request
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
                "redirect_uri": os.getenv("GITHUB_APP_REDIRECT_URI", "").strip(),
                "slug": os.getenv("GITHUB_APP_SLUG", "").strip()}
    if provider == "stripe":
        return {"client_id": os.getenv("STRIPE_CONNECT_CLIENT_ID", "").strip(),
                "client_secret": os.getenv("STRIPE_SECRET_KEY", "").strip(),
                "redirect_uri": os.getenv("STRIPE_CONNECT_REDIRECT_URI", "").strip()}
    raise HTTPException(404, "Connector not found")


def _configured(provider: str) -> bool:
    cfg = _provider_config(provider)
    if provider == "github":
        return all(cfg.get(k) for k in ("client_id", "client_secret", "redirect_uri", "slug")) and cfg["redirect_uri"].startswith("https://")
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


def _oauth_exchange(provider: str, code: str) -> dict:
    cfg = _provider_config(provider)
    if provider == "google":
        token = _http_json("POST", "https://oauth2.googleapis.com/token", data={"code": code, "client_id": cfg["client_id"], "client_secret": cfg["client_secret"], "redirect_uri": cfg["redirect_uri"], "grant_type": "authorization_code"})
        if not token.get("access_token"):
            raise HTTPException(502, "Google did not return an access token")
        token["expires_at"] = time.time() + int(token.get("expires_in", 3600))
        headers = {"Authorization": "Bearer " + token["access_token"]}
        profile = _http_json("GET", "https://openidconnect.googleapis.com/v1/userinfo", headers=headers)
        return {"credentials": token, "external_id": profile.get("sub") or profile.get("email") or "google-user", "label": profile.get("email") or "Google account"}
    if provider == "github":
        token = _http_json("POST", "https://github.com/login/oauth/access_token", data={"client_id": cfg["client_id"], "client_secret": cfg["client_secret"], "code": code, "redirect_uri": cfg["redirect_uri"]}, headers={"Accept": "application/json"})
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


@router.get("/{provider}/connect")
def connect(provider: str, user: dict = Depends(_user)):
    if provider not in {"google", "github", "stripe"}:
        raise HTTPException(404, "Connector not found")
    cfg = _provider_config(provider)
    if not _configured(provider):
        if provider == "stripe":
            raise HTTPException(503, "Use a Stripe restricted API key for your own account, or configure Stripe Connect OAuth for connected accounts.")
        raise HTTPException(503, f"{provider.title()} OAuth is not configured on RZ-AI yet")
    state = _state_create(_uid(user), provider)
    if provider == "google":
        params = {"client_id": cfg["client_id"], "redirect_uri": cfg["redirect_uri"], "response_type": "code", "scope": "openid email https://www.googleapis.com/auth/gmail.readonly", "access_type": "offline", "include_granted_scopes": "true", "prompt": "consent", "state": state}
        url = "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(params)
    elif provider == "github":
        url = "https://github.com/apps/" + cfg["slug"] + "/installations/new?" + urlencode({"state": state})
    else:
        url = "https://connect.stripe.com/oauth/authorize?" + urlencode({"response_type": "code", "client_id": cfg["client_id"], "scope": "read_only", "state": state, "redirect_uri": cfg["redirect_uri"]})
    return {"authorization_url": url, "expires_in_seconds": STATE_TTL}


@router.get("/{provider}/callback")
def callback(provider: str, code: str = "", state: str = "", error: str = "", installation_id: str = ""):
    if provider not in {"google", "github", "stripe"}:
        raise HTTPException(404, "Connector not found")
    if error:
        return RedirectResponse("/?connector=declined", status_code=303)
    if not code or len(code) > 4096:
        raise HTTPException(400, "Authorization code missing or invalid")
    user_id = _state_consume(state, provider)
    result = _oauth_exchange(provider, code)
    credentials = result["credentials"]
    if provider == "github":
        if not installation_id or not installation_id.isdigit():
            raise HTTPException(400, "GitHub App installation was not returned. Enable user authorization during installation and try again.")
        credentials["installation_id"] = installation_id
    _save(user_id, provider, result["external_id"], result["label"], credentials)
    return RedirectResponse("/?connector=" + provider + "_connected", status_code=303)


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
    schemas = []
    specs = {
        "google": [
            ("gmail_search_messages", "Search Gmail messages using Gmail query syntax", {"query": {"type": "string", "maxLength": 500}, "limit": {"type": "integer", "minimum": 1, "maximum": 25}}, ["query"]),
            ("gmail_get_message", "Read a Gmail message by message ID", {"message_id": {"type": "string", "maxLength": 200}}, ["message_id"]),
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
        for name, description, properties, required in items:
            schemas.append({"type": "function", "function": {"name": "appconn_" + name, "description": "[Connected app: " + provider.title() + " · read-only] " + description, "parameters": {"type": "object", "properties": properties, "required": required}}})
    return schemas


def _gmail_body(payload: dict) -> str:
    import base64
    found = []
    def walk(part):
        if not isinstance(part, dict):
            return
        mime = str(part.get("mimeType", ""))
        data = (part.get("body") or {}).get("data")
        if data and mime in {"text/plain", "text/html"}:
            try:
                decoded = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", "replace")
                found.append((mime, decoded))
            except Exception:
                pass
        for child in part.get("parts", []) or []:
            walk(child)
    walk(payload)
    # Prefer plain text; return bounded content only.
    return next((x[:12000] for x in found if x[0] == "text/plain"), next((x[:12000] for x in found if x[0] == "text/html"), ""))


def execute_tool(user_id: str, name: str, args: dict) -> dict:
    if not isinstance(args, dict) or len(json.dumps(args)) > 12000:
        raise HTTPException(422, "Connector tool arguments must be a JSON object under 12 KB")
    if name.startswith("appconn_gmail_"):
        token = _token(user_id, "google")
        if name == "appconn_gmail_search_messages":
            result = _get("https://gmail.googleapis.com/gmail/v1/users/me/messages", token, params={"q": str(args.get("query", ""))[:500], "maxResults": min(max(int(args.get("limit", 10)), 1), 25)})
            return {"resultSizeEstimate": result.get("resultSizeEstimate", 0), "messages": result.get("messages", []), "note": "Use gmail_get_message for message contents."}
        if name == "appconn_gmail_get_message":
            message_id = str(args.get("message_id", ""))
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", message_id):
                raise HTTPException(422, "Invalid Gmail message ID")
            result = _get("https://gmail.googleapis.com/gmail/v1/users/me/messages/" + message_id, token, params={"format": "full"})
            headers = {x.get("name", "").lower(): x.get("value", "") for x in result.get("payload", {}).get("headers", []) if isinstance(x, dict)}
            return {"id": result.get("id"), "threadId": result.get("threadId"), "snippet": result.get("snippet"), "from": headers.get("from"), "to": headers.get("to"), "subject": headers.get("subject"), "date": headers.get("date"), "body": _gmail_body(result.get("payload", {})), "note": "Message content is private user data; do not disclose outside this account."}
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
