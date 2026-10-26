import base64
import json
import time

import pytest

from app import nous_oauth


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def _jwt_with_exp(exp):
    header = base64.urlsafe_b64encode(b'{"alg":"none"}').decode().rstrip("=")
    payload = base64.urlsafe_b64encode(json.dumps({"exp": exp}).encode()).decode().rstrip("=")
    return f"{header}.{payload}.signature"


def test_expiry_detection_refreshes_missing_and_near_expiry_tokens():
    assert nous_oauth._is_expiring({})
    assert nous_oauth._is_expiring({"access_token": _jwt_with_exp(time.time() + 30)})
    assert not nous_oauth._is_expiring({"access_token": _jwt_with_exp(time.time() + 3600)})


def test_refresh_sends_refresh_token_only_as_header_and_persists_rotation(monkeypatch):
    calls = []
    old_refresh = "refresh-token-never-log"

    def fake_urlopen(request, timeout=20):
        calls.append(request)
        assert request.full_url == "https://portal.nousresearch.com/api/oauth/token"
        headers = {key.lower(): value for key, value in request.header_items()}
        assert headers["x-nous-refresh-token"] == old_refresh
        assert old_refresh not in (request.data or b"").decode("utf-8", "ignore")
        assert timeout == 20
        return FakeResponse({
            "access_token": "new-access-token",
            "refresh_token": "rotated-refresh-token",
            "expires_in": 3600,
            "scope": "inference:invoke",
        })

    monkeypatch.setattr(nous_oauth, "urlopen", fake_urlopen)
    updated = nous_oauth._refresh_state({
        "client_id": "nous-client",
        "refresh_token": old_refresh,
        "access_token": "old-access-token",
        "scope": "inference:invoke",
    })

    assert updated["access_token"] == "new-access-token"
    assert updated["refresh_token"] == "rotated-refresh-token"
    assert updated["portal_base_url"] == "https://portal.nousresearch.com"
    assert updated["inference_base_url"] == "https://inference-api.nousresearch.com/v1"
    assert updated["expires_at"]
    assert len(calls) == 1


def test_bootstrap_requires_refresh_token_and_client_id(monkeypatch):
    monkeypatch.setenv("NOUS_OAUTH_STATE_JSON", json.dumps({"refresh_token": "r"}))
    with pytest.raises(nous_oauth.NousOAuthError, match="client id"):
        nous_oauth._bootstrap_state()


def test_encryption_key_is_required(monkeypatch):
    monkeypatch.delenv("NOUS_OAUTH_ENCRYPTION_KEY", raising=False)
    with pytest.raises(nous_oauth.NousOAuthError, match="encryption key"):
        nous_oauth._fernet()


def test_chat_completions_forwards_tool_schema_and_choice(monkeypatch):
    captured = {}
    monkeypatch.setattr(nous_oauth, "get_access_token", lambda force_refresh=False: "short-lived-test-token")

    class Response:
        def __enter__(self):
            return self
        def __exit__(self, *_args):
            return False
        def read(self):
            return b'{"choices":[{"message":{"content":"ok"}}]}'

    def fake_urlopen(request, timeout=45):
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        captured["auth"] = request.get_header("Authorization")
        return Response()

    monkeypatch.setattr(nous_oauth, "urlopen", fake_urlopen)
    tools = [{"type": "function", "function": {"name": "mia_web_search", "parameters": {"type": "object"}}}]
    result = nous_oauth.chat_completions([{"role": "user", "content": "search"}], "upstage/solar-mini4:free", 0, 200, tools=tools, tool_choice="required")
    assert result["choices"][0]["message"]["content"] == "ok"
    assert captured["payload"]["tools"] == tools
    assert captured["payload"]["tool_choice"] == "required"
    assert captured["auth"] == "Bearer short-lived-test-token"
