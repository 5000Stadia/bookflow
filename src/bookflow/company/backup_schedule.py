"""Scheduled company backups: the schedule, when it is due, taking one, keeping N (blueprint 3.4.1).

The schedule for each company is host configuration, kept in ``config.toml`` under
``[backups."<company id>"]`` (``daily_at``, ``timezone``, ``destination``, ``keep``, ``since``,
``set_by``), because the destination is a folder on the machine running Bookflow. Only a person
who is an installation administrator changes it, through `backup schedule`, which records a hub
audit event.

The running host checks once a minute. A company is due when today's (or, before the time,
yesterday's) ``daily_at`` in its timezone has passed, falls after ``since``, and no backup has
succeeded since; after a failure it tries again at most hourly. A scheduled backup is the same
verified archive `company backup` writes, made in the destination, left read-only (0444) with
its read-only checkpoint beside it; then this company's oldest scheduled backups in that folder
beyond ``keep`` are removed. The newest is never removed. The last attempt, success and failure
are kept in ``<company folder>/backups/schedule-status.json``.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from bookflow.company import backup_archive as archive
from bookflow.company import backup_checkpoint as checkpoint

STATUS_FILE = "schedule-status.json"
RETRY_AFTER = timedelta(hours=1)
KEEP_MAX = 365
_TIME = re.compile(r"([01][0-9]|2[0-3]):([0-5][0-9])")


def parse_time(value: str) -> tuple[int, int] | None:
    m = _TIME.fullmatch(value or "")
    return (int(m.group(1)), int(m.group(2))) if m else None


def entry(config_data: dict[str, Any], company_id: str) -> dict[str, Any] | None:
    raw = (config_data.get("backups") or {}).get(company_id)
    if not isinstance(raw, dict):
        return None
    try:
        keep = int(raw.get("keep", 0))
    except (TypeError, ValueError):
        return None
    if parse_time(str(raw.get("daily_at", ""))) is None or not raw.get("destination") or not 1 <= keep <= KEEP_MAX:
        return None
    return {"daily_at": str(raw["daily_at"]), "timezone": str(raw.get("timezone") or "UTC"), "destination": str(raw["destination"]),
            "keep": keep, "since": raw.get("since"), "set_by": raw.get("set_by")}


def _parse(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _iso(when: datetime) -> str:
    return when.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def latest_occurrence(sched: dict[str, Any], now: datetime) -> datetime:
    hour, minute = parse_time(sched["daily_at"])  # type: ignore[misc]
    try:
        tz = ZoneInfo(sched["timezone"])
    except Exception:  # noqa: BLE001 - a bad zone name falls back to UTC rather than stopping backups
        tz = timezone.utc
    local = now.astimezone(tz)
    occ = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if occ > local:
        occ = (local - timedelta(days=1)).replace(hour=hour, minute=minute, second=0, microsecond=0)
    return occ.astimezone(timezone.utc)


def next_occurrence(sched: dict[str, Any], now: datetime) -> datetime:
    return latest_occurrence(sched, now + timedelta(days=1))


def is_due(sched: dict[str, Any], status: dict[str, Any], now: datetime) -> bool:
    occ = latest_occurrence(sched, now)
    since = _parse(sched.get("since"))
    if since is not None and occ <= since:
        return False
    success = _parse((status.get("last_success") or {}).get("at"))
    if success is not None and success >= occ:
        return False
    attempt = _parse(status.get("last_attempt_at"))
    return attempt is None or attempt < occ or now - attempt >= RETRY_AFTER


def status_path(company_folder: Path) -> Path:
    return company_folder / "backups" / STATUS_FILE


def read_status(company_folder: Path) -> dict[str, Any]:
    try:
        data = json.loads(status_path(company_folder).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def write_status(company_folder: Path, data: dict[str, Any]) -> None:
    from bookflow.core.durability import write_metadata
    (company_folder / "backups").mkdir(mode=0o700, exist_ok=True)
    write_metadata(status_path(company_folder), json.dumps(data, indent=2, sort_keys=True))


def inspect_snapshot(path: Path) -> dict[str, Any]:
    """The company half of a checkpoint, read from the backup's own database copy."""
    import sqlite3
    from contextlib import closing

    from bookflow.storage.engine import sqlite_uri
    with closing(sqlite3.connect(sqlite_uri(path, "ro"), uri=True)) as conn:
        return {"company_audit": checkpoint.audit_chain(conn), "trial_balance": checkpoint.trial_balance(conn)}


def write_checkpoint(result: dict[str, Any], hub_raw) -> Path:
    """Write the checkpoint beside a just-written archive (``archive.create`` with ``inspect_snapshot``)."""
    m = result["manifest"]
    inspected = result["inspected"]
    data = checkpoint.build(company_id=m["company_id"], backup_id=m["backup_id"], archive=Path(result["path"]),
                            archive_sha256=result["sha256"], created_at=m["created_at"],
                            company_section=inspected["company_audit"],
                            hub_section=checkpoint.audit_chain(hub_raw) if hub_raw is not None else None,
                            trial=inspected["trial_balance"])
    return checkpoint.write(Path(result["path"]), data)


def backups_in(folder: Path, company_id: str) -> list[dict[str, Any]]:
    """This company's archives in ``folder``, newest first, with their checkpoint facts when present."""
    out: list[dict[str, Any]] = []
    try:
        files = [p for p in folder.glob("*" + archive.SUFFIX) if p.is_file()]
    except OSError:
        return []
    checkpoints = {c.get("archive"): (p, c) for p, c in checkpoint.in_folder(folder, company_id)}
    for path in files:
        cp = checkpoints.get(path.name)
        created = cp[1].get("created_at") if cp else None
        order = (cp[1].get("taken_at") or created) if cp else None
        if cp is None:
            try:
                manifest = archive.read_manifest(path)
            except Exception:  # noqa: BLE001 - an unreadable file is not this company's backup
                continue
            if manifest.get("company_id") != company_id:
                continue
            created = order = manifest.get("created_at")
        try:
            st = path.stat()
        except OSError:
            continue
        out.append({"path": path, "file_name": path.name, "created_at": created, "size_bytes": st.st_size,
                    "read_only": not (st.st_mode & 0o222), "checkpoint": cp[0] if cp else None,
                    "checkpoint_data": cp[1] if cp else None, "order": order or ""})
    out.sort(key=lambda b: (b["order"], b["file_name"]), reverse=True)
    return out


def prune(folder: Path, company_id: str, keep: int) -> list[str]:
    """Remove this company's checkpointed archives in ``folder`` beyond the newest ``keep``.

    Only archives with a checkpoint (the ones the schedule wrote) are counted or removed, and
    the newest is never removed, whatever ``keep`` says.
    """
    keep = max(1, keep)
    scheduled = [b for b in backups_in(folder, company_id) if b["checkpoint"] is not None]
    removed = []
    for b in scheduled[keep:]:
        for p in (b["path"], b["checkpoint"]):
            try:
                p.unlink()
            except FileNotFoundError:
                pass
        removed.append(b["file_name"])
    if removed:
        from bookflow.core.durability import sync_directory
        sync_directory(folder)
    return removed


def take(db, hub_raw, *, company_folder: Path, company_id: str, display_name: str, destination: Path,
         created_by: dict[str, Any], bookflow_version: str, backup_id: str) -> dict[str, Any]:
    """Write one read-only archive and its checkpoint into ``destination``.

    The caller holds the company's write transaction (``db``), as `company backup` does, so the
    copy is one committed state.
    """
    result = archive.create(company_folder, company_id=company_id, display_name=display_name, created_by=created_by,
                            bookflow_version=bookflow_version, backup_id=backup_id, backups_dir=destination,
                            inspect=inspect_snapshot, read_only=True)
    result["checkpoint"] = write_checkpoint(result, hub_raw)
    return result


def destination_problem(path: Path, data_root: Path) -> str | None:
    if not path.is_absolute():
        return "must be an absolute path on the machine running Bookflow"
    if not path.is_dir():
        return "must be an existing folder"
    if not os.access(path, os.W_OK | os.X_OK):
        return "Bookflow cannot write to that folder"
    return None


def inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False
