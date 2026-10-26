"""Secure Nous Portal OAuth credential refresh and inference for MIA-RZ.

Bootstrap credentials are supplied only through Render's secret environment. The
rotating OAuth state is encrypted at rest in Postgres, so refresh-token rotation
survives restarts and multiple Render instances. No credential material is logged.
"""
from __future__ import annotations

import base64
import json
import os
import time
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import psycopg
from cryptography.fernet import Fernet, InvalidToken

PORTAL_URL = "https://portal.nousresearch.com"
INFERENCE_URL = "https://inference-api.nousresearch.com/v1"
TABLE = "rz_nous_oauth_credentials"


class NousOAuthError(RuntimeError):
    """Safe, non-secret-bearing provider authentication or inference error."""


def _fernet() -> Fernet:
    key = os.getenv("NOUS_OAUTH_ENCRYPTION_KEY", "").strip()
    if not key:
        raise NousOAuthError("Nous OAuth encryption key is not configured")
    try:
        return Fernet(key.encode("ascii"))
    except (ValueError, UnicodeEncodeError):
        raise NousOAuthError("Nous OAuth encryption key is invalid") from None


def _database_url() -> str:
    value = os.getenv("DATABASE_URL", "").strip()
    if not value:
        raise NousOAuthError("Persistent database is required for Nous OAuth token rotation")
    return value


def _bootstrap_state() -> dict:
    raw = os.getenv("NOUS_OAUTH_STATE_JSON", "").strip()
    if not raw:
        raise NousOAuthError("Nous OAuth bootstrap state is not configured")
    try:
        state = json.loads(raw)
    except (ValueError, TypeError):
        raise NousOAuthError("Nous OAuth bootstrap state is invalid JSON") from None
    if not isinstance(state, dict):
        raise NousOAuthError("Nous OAuth bootstrap state must be a JSON object")
    # Accept either a provider-state object or the official CLI auth-store envelope.
    providers = state.get("providers")
    if isinstance(providers, dict) and isinstance(providers.get("nous"), dict):
        state = dict(providers["nous"])
    elif isinstance(state.get("nous"), dict):
        state = dict(state["nous"])
    if not isinstance(state.get("refresh_token"), str) or not state["refresh_token"].strip():
        raise NousOAuthError("Nous OAuth bootstrap state has no refresh token")
    if not isinstance(state.get("client_id"), str) or not state["client_id"].strip():
        raise NousOAuthError("Nous OAuth bootstrap state has no client id")
    state["portal_base_url"] = PORTAL_URL
    state["inference_base_url"] = INFERENCE_URL
    return state


def _table_setup(conn) -> None:
    conn.execute(
        f"""CREATE TABLE IF NOT EXISTS {TABLE} (
            id SMALLINT PRIMARY KEY CHECK (id = 1),
            ciphertext BYTEA NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )"""
    )


def _is_expiring(state: dict, min_ttl: int = 120) -> bool:
    token = state.get("access_token")
    if not isinstance(token, str) or not token:
        return True
    try:
        parts = token.split(".")
        if len(parts) == 3:
            payload = parts[1] + "=" * (-len(parts[1]) % 4)
            claims = json.loads(base64.urlsafe_b64decode(payload.encode("ascii")))
            exp = claims.get("exp")
            if isinstance(exp, (int, float)):
                return float(exp) <= time.time() + min_ttl
    except (ValueError, TypeError, UnicodeDecodeError):
        pass
    expires_at = state.get("expires_at")
    if isinstance(expires_at, str):
        try:
            expiry = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=timezone.utc)
            return expiry.timestamp() <= time.time() + min_ttl
        except ValueError:
            pass
    # An opaque token without expiry metadata is refreshed rather than trusted indefinitely.
    return True


def _refresh_state(state: dict) -> dict:
    refresh_token = state.get("refresh_token")
    client_id = state.get("client_id")
    if not isinstance(refresh_token, str) or not refresh_token or not isinstance(client_id, str) or not client_id:
        raise NousOAuthError("Nous OAuth credentials are incomplete")
    request = Request(
        f"{PORTAL_URL}/api/oauth/token",
        data=urlencode({"grant_type": "refresh_token", "client_id": client_id}).encode("utf-8"),
        headers={
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
            "x-nous-refresh-token": refresh_token,
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        if exc.code in (400, 401, 403):
            raise NousOAuthError("Nous Portal rejected OAuth refresh; re-authentication is required") from None
        raise NousOAuthError(f"Nous Portal OAuth refresh failed (HTTP {exc.code})") from None
    except (URLError, TimeoutError):
        raise NousOAuthError("Nous Portal OAuth refresh could not reach the provider") from None
    except (ValueError, UnicodeDecodeError):
        raise NousOAuthError("Nous Portal OAuth refresh returned an invalid response") from None
    if not isinstance(payload, dict) or not isinstance(payload.get("access_token"), str):
        raise NousOAuthError("Nous Portal OAuth refresh response has no access token")
    updated = dict(state)
    updated.update(payload)
    # Nous rotates refresh tokens; preserve the old one only if the provider omits a new one.
    if not updated.get("refresh_token"):
        updated["refresh_token"] = refresh_token
    updated["portal_base_url"] = PORTAL_URL
    updated["inference_base_url"] = INFERENCE_URL
    if not updated.get("expires_at") and isinstance(updated.get("expires_in"), (int, float)):
        updated["expires_at"] = datetime.fromtimestamp(
            time.time() + float(updated["expires_in"]), tz=timezone.utc
        ).isoformat()
    return updated


def get_access_token(force_refresh: bool = False) -> str:
    """Load encrypted OAuth state, refresh/rotate safely, and return a short-lived token."""
    cipher = _fernet()
    database_url = _database_url()
    try:
        with psycopg.connect(database_url, connect_timeout=8) as conn:
            with conn.transaction():
                # Serialize first-time bootstrap and refresh-token rotation across workers.
                conn.execute("SELECT pg_advisory_xact_lock(734820196)")
                _table_setup(conn)
                row = conn.execute(
                    f"SELECT ciphertext FROM {TABLE} WHERE id = 1 FOR UPDATE"
                ).fetchone()
                if row:
                    try:
                        state = json.loads(cipher.decrypt(bytes(row[0])).decode("utf-8"))
                    except (InvalidToken, ValueError, UnicodeDecodeError):
                        raise NousOAuthError("Stored Nous OAuth state cannot be decrypted") from None
                else:
                    state = _bootstrap_state()
                if not isinstance(state, dict):
                    raise NousOAuthError("Stored Nous OAuth state is invalid")
                if force_refresh or _is_expiring(state):
                    state = _refresh_state(state)
                token = state.get("access_token")
                if not isinstance(token, str) or not token:
                    raise NousOAuthError("Nous Portal did not provide an inference token")
                ciphertext = cipher.encrypt(
                    json.dumps(state, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
                )
                conn.execute(
                    f"""INSERT INTO {TABLE} (id, ciphertext, updated_at)
                        VALUES (1, %s, NOW())
                        ON CONFLICT (id) DO UPDATE
                        SET ciphertext = EXCLUDED.ciphertext, updated_at = NOW()""",
                    (ciphertext,),
                )
                return token
    except NousOAuthError:
        raise
    except Exception as exc:
        # Avoid leaking database URLs, SQL parameters, or provider credentials in errors.
        raise NousOAuthError(f"Nous OAuth credential storage failed ({type(exc).__name__})") from None


def chat_completions(messages: list[dict], model: str, temperature=None, max_tokens=None, tools: list[dict] | None = None, tool_choice: str = "auto") -> dict:
    """Call Nous inference API using a refreshed OAuth bearer token.

    Tool definitions are forwarded only when explicitly requested. This keeps
    ordinary chat payloads unchanged while enabling the bounded agent tool loop.
    """
    payload: dict = {"model": model, "messages": messages}
    if temperature is not None:
        payload["temperature"] = temperature
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = tool_choice
    for attempt in range(2):
        token = get_access_token(force_refresh=(attempt == 1))
        request = Request(
            f"{INFERENCE_URL}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=45) as response:
                result = json.loads(response.read().decode("utf-8"))
            if not isinstance(result, dict) or not isinstance(result.get("choices"), list):
                raise NousOAuthError("Nous inference returned an invalid response")
            return result
        except HTTPError as exc:
            if exc.code == 401 and attempt == 0:
                continue
            if exc.code in (400, 401, 403):
                raise NousOAuthError(f"Nous inference rejected the request (HTTP {exc.code})") from None
            raise NousOAuthError(f"Nous inference failed (HTTP {exc.code})") from None
        except (URLError, TimeoutError):
            raise NousOAuthError("Nous inference could not reach the provider") from None
        except (ValueError, UnicodeDecodeError):
            raise NousOAuthError("Nous inference returned invalid JSON") from None
    raise NousOAuthError("Nous inference authentication failed after refresh")
