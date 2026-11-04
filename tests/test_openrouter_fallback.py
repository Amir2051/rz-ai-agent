"""OpenRouter free models get rate-limited; MIA should move on to the fallback models instead of failing."""
import json
import os
import sys
from io import BytesIO
from urllib.error import HTTPError

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture()
def app(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("RZ_API_KEY", "test-key")
    monkeypatch.setenv("RZ_LOCAL_DATA_PATH", str(tmp_path / "or.sqlite3"))
    sys.modules.pop("app.main", None)
    import app.main as module
    monkeypatch.setattr(module, "OPENROUTER_FALLBACK_MODEL", "openrouter/free")
    monkeypatch.delenv("OPENROUTER_FALLBACK_MODELS", raising=False)
    yield module
    sys.modules.pop("app.main", None)


class Ok:
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False
    def read(self):
        return b'{"choices":[{"message":{"content":"hi"}}]}'


def fake(app, monkeypatch, failures):
    calls = []

    def urlopen(req, timeout):
        model = json.loads(req.data.decode())["model"]
        calls.append(model)
        if model in failures:
            code, body = failures[model]
            raise HTTPError(req.full_url, code, "err", {}, BytesIO(body))
        return Ok()

    monkeypatch.setattr(app, "urlopen", urlopen)
    return calls


RATE = b'{"error":{"code":429,"message":"temporarily rate-limited upstream"}}'
ZDR = b'{"error":{"message":"0 endpoints out of 1 requested; ZDR violation"}}'


def test_rate_limited_model_falls_back(app, monkeypatch):
    calls = fake(app, monkeypatch, {"deepseek/x:free": (429, RATE)})
    result = app._call_openrouter([{"role": "user", "content": "hi"}], "deepseek/x:free", None, None)
    assert result["choices"][0]["message"]["content"] == "hi"
    assert calls == ["deepseek/x:free", "openrouter/free"]


def test_rate_limit_falls_back_for_tool_requests_too(app, monkeypatch):
    calls = fake(app, monkeypatch, {"deepseek/x:free": (429, RATE)})
    tools = [{"type": "function", "function": {"name": "t", "parameters": {"type": "object"}}}]
    app._call_openrouter([{"role": "user", "content": "hi"}], "deepseek/x:free", None, None, tools=tools)
    assert calls == ["deepseek/x:free", "openrouter/free"]


def test_extra_fallback_models_are_tried_in_order(app, monkeypatch):
    monkeypatch.setenv("OPENROUTER_FALLBACK_MODELS", "a/one:free, b/two:free")
    calls = fake(app, monkeypatch, {"m:free": (503, b"down"), "openrouter/free": (429, RATE), "a/one:free": (429, RATE)})
    app._call_openrouter([{"role": "user", "content": "hi"}], "m:free", None, None)
    assert calls == ["m:free", "openrouter/free", "a/one:free", "b/two:free"]


def test_zdr_block_keeps_tool_requests_pinned(app, monkeypatch):
    calls = fake(app, monkeypatch, {"m:free": (404, ZDR)})
    tools = [{"type": "function", "function": {"name": "t", "parameters": {"type": "object"}}}]
    with pytest.raises(HTTPException):
        app._call_openrouter([{"role": "user", "content": "hi"}], "m:free", None, None, tools=tools)
    assert calls == ["m:free"]


def test_bad_request_is_not_retried(app, monkeypatch):
    calls = fake(app, monkeypatch, {"m:free": (400, b"bad")})
    with pytest.raises(HTTPException):
        app._call_openrouter([{"role": "user", "content": "hi"}], "m:free", None, None)
    assert calls == ["m:free"]
