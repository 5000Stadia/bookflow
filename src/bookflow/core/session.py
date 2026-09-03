"""What a command sees while it runs: the open databases, the actor, and helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bookflow.core.config import Config
from bookflow.storage.engine import Database


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


@dataclass
class Actor:
    id: str
    kind: str
    username: str
    display_name: str
    hub_admin: bool
    timezone: str | None = None


@dataclass
class Session:
    data_root: Path
    os_login: str
    config: Config
    hub: Database | None = None
    company: Database | None = None
    company_id: str | None = None
    company_row: dict[str, Any] | None = None
    actor: Actor | None = None
    dry_run: bool = False
    warnings: list[str] = field(default_factory=list)
    memberships: list[dict[str, Any]] = field(default_factory=list)
    pending_config: bool = False
    company_tz: str | None = None

    def close_company(self) -> None:
        cm = getattr(self, '_co_cm', None)
        if cm is not None:
            cm.__exit__(None, None, None)
            self._co_cm = None
        self.company = None

    @property
    def organizations_dir(self) -> Path:
        return self.data_root / "organizations"

    @property
    def is_hub_admin(self) -> bool:
        return bool(self.actor and self.actor.hub_admin)

    def abs_path(self, rel: str) -> Path:
        return self.data_root / rel

    def rel_path(self, p: Path) -> str:
        return str(p.relative_to(self.data_root))


def localize(s: "Session", iso: str | None) -> str | None:
    """Render a stored UTC timestamp in the viewer's zone (blueprint 5.7)."""
    if iso is None:
        return None
    from zoneinfo import ZoneInfo
    zone = None
    if s.actor and s.actor.timezone:
        zone = s.actor.timezone
    elif s.company_tz:
        zone = s.company_tz
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    if zone:
        try:
            dt = dt.astimezone(ZoneInfo(zone))
        except Exception:
            pass
    return dt.isoformat(timespec="milliseconds")
