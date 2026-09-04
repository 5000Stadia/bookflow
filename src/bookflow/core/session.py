"""What a command sees while it runs: the open databases, the actor, and helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from typing import TYPE_CHECKING

from bookflow.core.config import Config

if TYPE_CHECKING:  # pragma: no cover
    from bookflow.storage.engine import Database


def now_iso() -> str:
    from bookflow.core import clock
    return clock.now_iso()


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
    hub: "Database | None" = None
    company: "Database | None" = None
    company_id: str | None = None
    company_row: dict[str, Any] | None = None
    actor: Actor | None = None
    dry_run: bool = False
    warnings: list[str] = field(default_factory=list)
    memberships: list[dict[str, Any]] = field(default_factory=list)
    pending_config: bool = False
    company_tz: str | None = None
    hub_migrated: tuple | None = None
    completed_moves: list = field(default_factory=list)  # ids whose pending move this command finished on open
    directive_code: str | None = None
    hub_touched: list = field(default_factory=list)  # entries the dispatcher adds to the command's hub event (migrations, projection repair)
    company_touched: list = field(default_factory=list)
    company_info_row: dict | None = None
    company_opener: Any = None  # host hook: (row, writable, db_path) -> a Database the host owns; never closed here
    company_releaser: Any = None  # host hook: called with the company id when the session lets go of it

    def close_company(self, *, release: bool = True) -> None:
        """Let go of the selected company.

        A standalone session owns its context manager and always closes it. A
        hosted session normally leaves its pooled connection alive between
        requests; callers that are about to move or detach a company use the
        default ``release=True`` to make the host close that pooled handle.
        """
        cm = getattr(self, '_co_cm', None)
        if cm is not None:
            cm.__exit__(None, None, None)
            self._co_cm = None
        elif release and self.company is not None and self.company_releaser is not None:
            self.company_releaser(self.company_id)
        self.company = None

    def release_company(self, company_id: str) -> None:
        """Close a host-owned company handle whether or not it is selected."""
        if self.company is not None and self.company_id == company_id:
            self.close_company()
        elif self.company_releaser is not None:
            self.company_releaser(company_id)

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
