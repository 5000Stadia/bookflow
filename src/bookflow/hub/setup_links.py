"""One-time links that let a person set their first password in the browser (R182).

`bookflow setup` creates the owner without a password and hands back a link instead, so nobody types a
password into a terminal. The link is a bearer secret, so it is kept as small as it can be:

- the secret is 32 random bytes; only its SHA-256 is stored, as a file name under `<root>/setup-links/`
  (owner-only, mode 0700/0600), holding the user id and the expiry;
- it expires (two hours by default, at most a day) and works once: using it renames the file away
  before the password is written, so two submissions cannot both win;
- it sets a password only for a person who has none yet. It never resets one; it cannot be used
  after the password has been set any other way;
- the host answers it only from the loopback address (see the workbench route).

The store is files rather than a hub table so a link can be created while a host holds the data root,
and so nothing here needs a schema change. Expired and stale links are removed whenever links are made.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
from typing import Any

DIRECTORY = "setup-links"
DEFAULT_MINUTES = 120
MAX_MINUTES = 24 * 60
MIN_PASSWORD = 8
_HEX = frozenset("0123456789abcdef")


def _directory(root: Path) -> Path:
    folder = Path(root) / DIRECTORY
    folder.mkdir(mode=0o700, exist_ok=True)
    os.chmod(folder, 0o700)
    return folder


def _digest(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _read(path: Path) -> dict[str, Any] | None:
    try:
        st = path.lstat()
        if not path.is_file() or path.is_symlink() or st.st_uid != os.getuid() or st.st_mode & 0o077:
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) and isinstance(data.get("user_id"), str) else None
    except (OSError, ValueError):
        return None


def _expired(data: dict[str, Any]) -> bool:
    try:
        return datetime.fromisoformat(data["expires_at"]) <= _now()
    except (KeyError, TypeError, ValueError):
        return True


def create(root: Path, user_id: str, *, minutes: int = DEFAULT_MINUTES) -> tuple[str, str]:
    """A fresh link secret for this user and when it expires. Every older link for the user is removed."""
    if not 1 <= minutes <= MAX_MINUTES:
        raise ValueError("a setup link lasts between 1 minute and 1 day")
    folder = _directory(root)
    for old in folder.glob("*.json"):
        data = _read(old)
        if data is None or data["user_id"] == user_id or _expired(data):
            old.unlink(missing_ok=True)
    secret = secrets.token_urlsafe(32)
    expires = (_now() + timedelta(minutes=minutes)).isoformat(timespec="seconds")
    path = folder / f"{_digest(secret)}.json"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as out:
        json.dump({"user_id": user_id, "expires_at": expires}, out)
        out.flush()
        os.fsync(out.fileno())
    return secret, expires


def _path(root: Path, secret: str) -> Path | None:
    if not isinstance(secret, str) or not 20 <= len(secret) <= 128:
        return None
    digest = _digest(secret)
    if not set(digest) <= _HEX:
        return None
    return Path(root) / DIRECTORY / f"{digest}.json"


def peek(root: Path, secret: str) -> dict[str, Any] | None:
    """The link's user and expiry if it is live, without using it up."""
    path = _path(root, secret)
    if path is None:
        return None
    data = _read(path)
    if data is None:
        return None
    if _expired(data):
        path.unlink(missing_ok=True)
        return None
    return data


def claim(root: Path, secret: str) -> tuple[dict[str, Any], Path] | None:
    """Take the link for one use: the rename succeeds for exactly one caller.

    Returns the link and the claimed file; call `release` to give it back when the password was not
    saved (for example the person's two entries did not match), or `spend` once it was.
    """
    path = _path(root, secret)
    if path is None:
        return None
    claimed = path.with_name(f"{path.stem}.{secrets.token_hex(8)}.claimed")
    try:
        os.rename(path, claimed)
    except OSError:
        return None
    data = _read(claimed)
    if data is None or _expired(data):
        claimed.unlink(missing_ok=True)
        return None
    return data, claimed


def release(claimed: Path, secret: str) -> None:
    path = _path(claimed.parent.parent, secret)
    if path is not None:
        try:
            os.rename(claimed, path)
        except OSError:
            claimed.unlink(missing_ok=True)


def spend(claimed: Path) -> None:
    claimed.unlink(missing_ok=True)
