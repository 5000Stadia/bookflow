"""Credentials to user ids: session cookies and bearer tokens (row 3 plan, Authentication)."""

from __future__ import annotations
from bookflow.core.commit_hooks import CommitHooks

import threading
from datetime import timedelta
from typing import Any

from bookflow.core import clock
from bookflow.core.errors import BookflowError
from bookflow.hub import schema as h
from bookflow.hub.credentials import (
    SESSION_HOURS,
    issue_token as issue_token,
    new_secret as new_secret,
    resolve_token as resolve_token,
    token_hash as token_hash,
)

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


def needs_refresh(row: dict[str, Any]) -> bool:
    last = row.get("last_used_at")
    return not last or (clock.now() - clock.parse_iso(last)).total_seconds() > REFRESH_SECONDS


def refresh_token(db, token_id: str, kind: str, *, commits: CommitHooks | None = None) -> None:
    """Unversioned, unaudited liveness bump (row 3 plan): last_used_at and, for sessions, expires_at."""
    commits = commits if commits is not None else CommitHooks()
    with commits.operation("http.refresh_token", db):
        values: dict[str, Any] = {"last_used_at": clock.now_iso()}
        if kind == "session":
            values["expires_at"] = (clock.now() + timedelta(hours=SESSION_HOURS)).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        with commits.autocommit(db, "http.refresh_token"):
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
