"""Credentials to user ids: session cookies and bearer tokens (row 3 plan, Authentication)."""

from __future__ import annotations

import hashlib
import secrets
import threading
import time
from datetime import timedelta
from typing import Any

import sqlalchemy as sa

from bookflow.core import clock
from bookflow.core.errors import BookflowError
from bookflow.core.ids import new_id
from bookflow.hub import schema as h
from bookflow.hub.users import common

SESSION_HOURS = 12
REFRESH_SECONDS = 300
_DUMMY_HASH = None
_attempts: dict[str, int] = {}
_attempts_lock = threading.Lock()


def _hasher():
    from argon2 import PasswordHasher
    return PasswordHasher()


def hash_password(password: str) -> str:
    return _hasher().hash(password)


def verify_password(stored: str | None, password: str) -> bool:
    """Constant work whether or not the user has a password, so timing does not enumerate users."""
    global _DUMMY_HASH
    from argon2.exceptions import VerifyMismatchError, VerificationError
    ph = _hasher()
    if _DUMMY_HASH is None:
        _DUMMY_HASH = ph.hash("dummy-password-for-timing")
    try:
        return ph.verify(stored or _DUMMY_HASH, password) and stored is not None
    except (VerifyMismatchError, VerificationError):
        return False


def token_hash(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def new_secret() -> str:
    return secrets.token_urlsafe(32)


def issue_token(db, *, user_id: str, kind: str, label: str | None, days: int | None, via: str, actor_id: str) -> tuple[dict[str, Any], str]:
    secret = new_secret()
    expires = (clock.now() + timedelta(days=days)).isoformat(timespec="milliseconds").replace("+00:00", "Z") if days else None
    if kind == "session":
        expires = (clock.now() + timedelta(hours=SESSION_HOURS)).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    row = {"id": new_id(), "user_id": user_id, "on_behalf_of": None, "kind": kind, "token_hash": token_hash(secret), "label": label,
           "expires_at": expires, "last_used_at": clock.now_iso(), "revoked_at": None, **common(actor_id, via)}
    db.conn.execute(h.api_tokens.insert().values(**row))
    return row, secret


def resolve_token(db, secret: str) -> dict[str, Any]:
    """The token row for a secret, or E_UNAUTHENTICATED with a reason."""
    row = db.conn.execute(sa.select(h.api_tokens).where(h.api_tokens.c.token_hash == token_hash(secret))).mappings().first()
    if row is None:
        raise BookflowError("E_UNAUTHENTICATED", details={"reason": "unknown token"})
    row = dict(row)
    if row["revoked_at"]:
        raise BookflowError("E_UNAUTHENTICATED", details={"reason": "revoked"})
    if row["expires_at"] and clock.parse_iso(row["expires_at"]) < clock.now():
        raise BookflowError("E_UNAUTHENTICATED", details={"reason": "expired"})
    return row


def needs_refresh(row: dict[str, Any]) -> bool:
    last = row.get("last_used_at")
    return not last or (clock.now() - clock.parse_iso(last)).total_seconds() > REFRESH_SECONDS


def refresh_token(db, token_id: str, kind: str) -> None:
    """Unversioned, unaudited liveness bump (row 3 plan): last_used_at and, for sessions, expires_at."""
    values: dict[str, Any] = {"last_used_at": clock.now_iso()}
    if kind == "session":
        values["expires_at"] = (clock.now() + timedelta(hours=SESSION_HOURS)).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    db.conn.execute(h.api_tokens.update().where(h.api_tokens.c.id == token_id).values(**values))


def throttle_login(source: str) -> None:
    with _attempts_lock:
        n = _attempts.get(source, 0)
        if n >= 5:
            raise BookflowError("E_LOGIN_FAILED", details={"reason": "too many attempts in flight"})
        _attempts[source] = n + 1


def release_login(source: str) -> None:
    with _attempts_lock:
        _attempts[source] = max(0, _attempts.get(source, 1) - 1)
