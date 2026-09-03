"""ULID identifiers."""

from __future__ import annotations

import re

from ulid import ULID

_ULID_RE = re.compile(r"^[0-9A-HJKMNP-TV-Za-hjkmnp-tv-z]{26}$")


def new_id() -> str:
    """Return a new ULID as a 26-character upper-case string."""
    return str(ULID())


def is_ulid(value: str) -> bool:
    """True when ``value`` has the shape of a ULID, in either case."""
    return bool(_ULID_RE.match(value)) and value[0].upper() <= "7"


def normalize_ulid(value: str) -> str:
    """Return the canonical upper-case form of a ULID-shaped string."""
    return value.upper()
