"""Per-account isolation for operator-owned resources.

Chats, memories, tasks, creator projects and preferences are already stored per
account. Two resources are different: the server-side ``GITHUB_TOKEN`` (the
operator's GitHub account) and the local terminal bridge (the operator's own
computer). Those belong to the operator, so only operator accounts may use them.

Operators are configured with ``RZ_OPERATOR_EMAILS`` (comma-separated account
emails) and/or ``RZ_OPERATOR_USER_IDS`` (comma-separated principal ids, e.g. an
account UUID or ``service`` for the shared service key). In local API-key mode
(no ``DATABASE_URL``) there is a single principal, ``local``, which is the
operator. In account mode nobody is an operator unless configured, so a new
deployment fails closed.
"""
from __future__ import annotations

import contextlib
import contextvars
import os
import threading
import time

_CURRENT = contextvars.ContextVar("rz_workspace_owner", default=None)
_EMAIL_CACHE: dict[str, tuple[str, float]] = {}
_EMAIL_CACHE_TTL = 300
_EMAIL_CACHE_LOCK = threading.Lock()


class OperatorResourceDenied(ValueError):
    """Raised when an account tries to use an operator-owned resource."""


def _account_mode() -> bool:
    return bool(os.getenv("DATABASE_URL", "").strip())


def _csv_env(name: str, lower: bool = False) -> set[str]:
    values = {item.strip() for item in os.getenv(name, "").split(",") if item.strip()}
    return {item.lower() for item in values} if lower else values


def owner_from_session(session_id: str | None) -> str:
    if isinstance(session_id, str) and ":" in session_id:
        return session_id.split(":", 1)[0]
    return ""


def _lookup_email(owner_id: str) -> str:
    now = time.time()
    with _EMAIL_CACHE_LOCK:
        cached = _EMAIL_CACHE.get(owner_id)
        if cached and now - cached[1] < _EMAIL_CACHE_TTL:
            return cached[0]
    try:
        from app.auth import get_user_email
        email = (get_user_email(owner_id) or "").lower()
    except Exception:
        # Fail closed: an unavailable lookup never grants operator access.
        return ""
    with _EMAIL_CACHE_LOCK:
        _EMAIL_CACHE[owner_id] = (email, now)
    return email


def is_operator(owner_id: str | None, email: str | None = None) -> bool:
    owner_id = str(owner_id or "")
    if not owner_id:
        return False
    if not _account_mode():
        return owner_id == "local"
    if owner_id in _csv_env("RZ_OPERATOR_USER_IDS"):
        return True
    emails = _csv_env("RZ_OPERATOR_EMAILS", lower=True)
    if not emails:
        return False
    if email is None:
        email = _lookup_email(owner_id)
    return bool(email) and email.lower() in emails


@contextlib.contextmanager
def acting_as(owner_id: str | None, email: str | None = None):
    """Record which account the current request is acting for."""
    token = _CURRENT.set((str(owner_id or ""), email))
    try:
        yield
    finally:
        _CURRENT.reset(token)


def current_owner() -> str:
    value = _CURRENT.get()
    return value[0] if value else ""


def current_is_operator() -> bool:
    value = _CURRENT.get()
    if not value:
        return False
    return is_operator(value[0], value[1])


def require_operator(resource: str) -> None:
    if not current_is_operator():
        raise OperatorResourceDenied(f"{resource} is not available for this account.")
