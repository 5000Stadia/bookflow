"""Token issuance and current credential eligibility in the caller's hub transaction."""

from __future__ import annotations

import hashlib
import secrets
from datetime import timedelta
from typing import Any

import sqlalchemy as sa

from bookflow.core import clock
from bookflow.core.errors import BookflowError
from bookflow.core.ids import new_id
from bookflow.hub import schema as h
from bookflow.hub.users import common

SESSION_HOURS = 12


def token_hash(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def new_secret() -> str:
    return secrets.token_urlsafe(32)


def _binding(db, user_id: str, principal_id: str | None) -> tuple[bool, int | None]:
    actor = db.conn.execute(sa.select(h.users.c.kind, h.users.c.active).where(h.users.c.id == user_id)).mappings().first()
    if actor is None or not actor["active"]:
        return False, None
    if actor["kind"] == "human":
        return principal_id is None, None
    if actor["kind"] != "agent" or principal_id is None:
        return False, None
    epoch = db.conn.execute(sa.select(h.agent_authority.c.epoch).select_from(
        h.agent_authority.join(h.agent_principals,
                              h.agent_principals.c.agent_user_id == h.agent_authority.c.agent_user_id)
        .join(h.users, h.users.c.id == h.agent_principals.c.principal_user_id)
    ).where(
        h.agent_authority.c.agent_user_id == user_id,
        h.agent_authority.c.suspended_at.is_(None),
        h.agent_authority.c.epoch >= 1,
        h.agent_principals.c.principal_user_id == principal_id,
        h.agent_principals.c.revoked_at.is_(None),
        h.users.c.kind == "human", h.users.c.active.is_(True),
    )).scalar_one_or_none()
    return (True, epoch) if type(epoch) is int else (False, None)


def issuance_epoch(db, *, user_id: str, on_behalf_of: str | None = None) -> int | None:
    """Check a target already resolved by an authorized issuer; reveal no other identities."""
    eligible, epoch = _binding(db, user_id, on_behalf_of)
    if not eligible:
        raise BookflowError("E_PERMISSION", details={"capability": "token", "reason": "credential target is not eligible"})
    return epoch


def issue_token(db, *, user_id: str, kind: str, label: str | None, days: int | None,
                via: str, actor_id: str, on_behalf_of: str | None = None) -> tuple[dict[str, Any], str]:
    """Validate and insert the complete binding with its hash under the hub writer lock."""
    if not db.write_transaction:
        raise RuntimeError("Token issuance requires a hub write transaction")
    issuer = db.conn.execute(sa.select(h.users.c.kind, h.users.c.active, h.users.c.hub_admin).where(h.users.c.id == actor_id)).mappings().first()
    if issuer is None or not issuer["active"] or issuer["kind"] != "human":
        raise BookflowError("E_PERMISSION", details={"capability": "token", "required_role": "human"})
    if actor_id != user_id and not issuer["hub_admin"]:
        raise BookflowError("E_PERMISSION", details={"capability": "token", "required_role": "hub_admin"})
    if kind not in ("bearer", "session"):
        raise BookflowError("E_VALIDATION", details={"fields": [{"field": "kind", "problem": "must be bearer or session"}]})
    epoch = issuance_epoch(db, user_id=user_id, on_behalf_of=on_behalf_of)
    secret = new_secret()
    expires = (clock.now() + timedelta(days=days)).isoformat(timespec="milliseconds").replace("+00:00", "Z") if days else None
    if kind == "session":
        expires = (clock.now() + timedelta(hours=SESSION_HOURS)).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    row = {"id": new_id(), "user_id": user_id, "on_behalf_of": on_behalf_of, "authority_epoch": epoch,
           "kind": kind, "token_hash": token_hash(secret), "label": label, "expires_at": expires,
           "last_used_at": clock.now_iso(), "revoked_at": None, **common(actor_id, via)}
    db.conn.execute(h.api_tokens.insert().values(**row))
    return row, secret


def resolve_token(db, secret: str) -> dict[str, Any]:
    """Resolve a live credential without returning hidden actor or principal identifiers on failure."""
    found = db.conn.execute(sa.select(h.api_tokens).where(h.api_tokens.c.token_hash == token_hash(secret))).mappings().first()
    if found is None:
        raise BookflowError("E_UNAUTHENTICATED", details={"reason": "unknown token"})
    row = dict(found)
    if row["kind"] not in ("bearer", "session"):
        raise BookflowError("E_UNAUTHENTICATED", details={"reason": "credential kind"})
    if row["revoked_at"]:
        raise BookflowError("E_UNAUTHENTICATED", details={"reason": "revoked"})
    validate_binding(db, row)
    return row


def validate_binding(db, row) -> None:
    """Shared kind, expiry and principal checks; callers separately check revocation.

    Publication of an exact, already committed self-revocation receipt can use
    these remaining checks after proving that operation's revocation effect.
    This is not an authentication entry point.
    """
    if row["kind"] not in ("bearer", "session"):
        raise BookflowError("E_UNAUTHENTICATED", details={"reason": "credential kind"})
    if row["expires_at"] and clock.parse_iso(row["expires_at"]) <= clock.now():
        raise BookflowError("E_UNAUTHENTICATED", details={"reason": "expired"})
    eligible, epoch = _binding(db, row["user_id"], row["on_behalf_of"])
    if not eligible or type(row["authority_epoch"]) is not type(epoch) or row["authority_epoch"] != epoch:
        raise BookflowError("E_UNAUTHENTICATED", details={"reason": "credential authority"})
