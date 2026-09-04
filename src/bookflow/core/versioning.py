"""Versioned, blind, and merged updates (blueprint 6.2, 6.3). Pure rules; callers supply the history."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from bookflow.core import clock
from bookflow.core.errors import BookflowError

# column name -> top-level field name used by callers (blueprint: merges are per top-level field)
ADDRESS_PREFIXES = ("address", "legal_address", "ship_address")
BOOKKEEPING = {"id", "version", "created_at", "created_by", "created_via", "updated_at", "updated_by", "updated_via", "display_name"}


def fold_field(column: str) -> str:
    for prefix in ADDRESS_PREFIXES:
        if column.startswith(prefix + "_"):
            return prefix
    return column


@dataclass
class HistoryEntry:
    version_after: int
    changed_columns: list[str] | None  # None when the before-state is not derivable
    updated_by: str | None = None
    updated_by_name: str | None = None
    on_behalf_of: str | None = None
    on_behalf_of_name: str | None = None
    updated_via: str | None = None
    at: str | None = None


@dataclass
class UpdateMeta:
    version: int
    changed_fields: list[str]
    merged_over_versions: list[int] = field(default_factory=list)
    previous_version: int | None = None
    previous_updated_by: str | None = None
    previous_updated_by_name: str | None = None
    previous_on_behalf_of: str | None = None
    previous_on_behalf_of_name: str | None = None
    previous_updated_via: str | None = None
    seconds_since_previous_update: float | None = None
    recent_concurrent_activity: bool = False

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


def _conflict_message(details: dict, changed_fields: list[str] | None) -> str:
    who = details.get("updated_by_name") or details.get("updated_by") or "someone"
    if details.get("updated_on_behalf_of_name"):
        who += f" on behalf of {details['updated_on_behalf_of_name']}"
    what = ", ".join(changed_fields) if changed_fields else ("fields that cannot be determined" if changed_fields is None else "no fields")
    ago = details.get("seconds_since_update")
    when = f"{ago} s ago" if ago is not None else "since your read"
    return f"{who} changed {what} {when} (now version {details['current_version']}); re-read and retry with expected_version {details['current_version']}."


def _seconds_since(iso: str | None) -> float | None:
    if not iso:
        return None
    return round((clock.now() - clock.parse_iso(iso)).total_seconds(), 1)


def check_update(*, current_version: int, current_updated_at: str | None, current_writer: HistoryEntry | None,
                 changes: set[str], expected_version: int | None, history_since: Callable[[int], list[HistoryEntry]],
                 actor_id: str, window_seconds: int) -> UpdateMeta:
    """Decide whether an update may apply and what to report; raises E_VERSION_CONFLICT.

    ``changes`` are top-level field names the caller set. ``history_since(v)`` returns the audit entries
    with version_after in (v, current] in order. ``current_writer`` describes the write that produced
    the current version, for blind-write reporting.
    """
    changes = {fold_field(c) for c in changes}
    if not changes:
        return UpdateMeta(version=current_version, changed_fields=[])
    if expected_version is None:
        meta = UpdateMeta(version=current_version + 1, changed_fields=sorted(changes), previous_version=current_version)
        if current_writer is not None:
            meta.previous_updated_by = current_writer.updated_by
            meta.previous_updated_by_name = current_writer.updated_by_name
            meta.previous_on_behalf_of = current_writer.on_behalf_of
            meta.previous_on_behalf_of_name = current_writer.on_behalf_of_name
            meta.previous_updated_via = current_writer.updated_via
        meta.seconds_since_previous_update = _seconds_since(current_updated_at)
        if (current_writer is not None and current_writer.updated_by not in (None, actor_id)
                and meta.seconds_since_previous_update is not None and meta.seconds_since_previous_update <= window_seconds):
            meta.recent_concurrent_activity = True
        return meta
    if expected_version == current_version:
        return UpdateMeta(version=current_version + 1, changed_fields=sorted(changes))
    entries = history_since(expected_version) if expected_version < current_version else []
    details = {
        "current_version": current_version,
        "updated_by": current_writer.updated_by if current_writer else None,
        "updated_by_name": current_writer.updated_by_name if current_writer else None,
        "updated_on_behalf_of": current_writer.on_behalf_of if current_writer else None,
        "updated_on_behalf_of_name": current_writer.on_behalf_of_name if current_writer else None,
        "updated_via": current_writer.updated_via if current_writer else None,
        "seconds_since_update": _seconds_since(current_updated_at),
    }
    if expected_version > current_version:
        raise BookflowError("E_VERSION_CONFLICT", message=_conflict_message(details, []), details={**details, "changed_fields": [], "expected_version": expected_version})
    covered = {e.version_after for e in entries}
    missing = [v for v in range(expected_version + 1, current_version + 1) if v not in covered]
    if missing or any(e.changed_columns is None for e in entries):
        raise BookflowError("E_VERSION_CONFLICT", message=_conflict_message(details, None), details={**details, "changed_fields": [], "unknown_versions": missing or [e.version_after for e in entries if e.changed_columns is None]})
    changed_fields = sorted({fold_field(c) for e in entries for c in (e.changed_columns or [])})
    if changes & set(changed_fields):
        raise BookflowError("E_VERSION_CONFLICT", message=_conflict_message(details, changed_fields), details={**details, "changed_fields": changed_fields})
    return UpdateMeta(version=current_version + 1, changed_fields=sorted(changes), merged_over_versions=[e.version_after for e in entries])


def apply_update_meta(meta: "UpdateMeta") -> dict:
    return meta.as_dict()


def history_from_entries(db, record_type: str, record_id: str, since_version: int, decode) -> list[HistoryEntry]:
    """Audit entries for one record with version_after > since_version, oldest first, as HistoryEntry."""
    import sqlalchemy as sa
    from bookflow.company import schema as c
    q = (sa.select(c.audit_entries, c.audit_events.c.actor_id, c.audit_events.c.on_behalf_of, c.audit_events.c.interface, c.audit_events.c.at)
         .join(c.audit_events, c.audit_events.c.id == c.audit_entries.c.event_id)
         .where(c.audit_entries.c.record_type == record_type, c.audit_entries.c.record_id == record_id, c.audit_entries.c.version_after > since_version)
         .order_by(c.audit_entries.c.version_after.asc()))
    rows = [r for r in db.conn.execute(q).mappings().all() if r["action"] not in ("migrate",)]
    out: list[HistoryEntry] = []
    prev_after = None
    if rows:
        first = rows[0]
        if first["version_before"] is not None:
            prev = db.conn.execute(sa.select(c.audit_entries.c.after).where(c.audit_entries.c.record_type == record_type, c.audit_entries.c.record_id == record_id, c.audit_entries.c.version_after == first["version_before"], c.audit_entries.c.action != "migrate").order_by(c.audit_entries.c.id.desc())).first()
            prev_after = decode(prev[0]) if prev else None
    for r in rows:
        if r["action"] == "baseline":
            prev_after = decode(r["after"])
            continue
        after = decode(r["after"])
        changed = None
        if prev_after is not None and after is not None:
            changed = sorted(k for k in set(prev_after) | set(after) if prev_after.get(k) != after.get(k) and k not in BOOKKEEPING)
        out.append(HistoryEntry(version_after=r["version_after"], changed_columns=changed, updated_by=r["actor_id"], on_behalf_of=r["on_behalf_of"], updated_via=r["interface"], at=r["at"]))
        prev_after = after
    return out


def current_writer_from_entries(db, record_type: str, record_id: str, version: int) -> HistoryEntry | None:
    import sqlalchemy as sa
    from bookflow.company import schema as c
    q = (sa.select(c.audit_events.c.actor_id, c.audit_events.c.on_behalf_of, c.audit_events.c.interface, c.audit_events.c.at)
         .join(c.audit_events, c.audit_events.c.id == c.audit_entries.c.event_id)
         .where(c.audit_entries.c.record_type == record_type, c.audit_entries.c.record_id == record_id, c.audit_entries.c.version_after == version,
                c.audit_entries.c.action.in_(("create", "update", "deactivate", "activate")))
         .order_by(c.audit_entries.c.id.desc()))
    r = db.conn.execute(q).mappings().first()
    if r is None:
        return None
    return HistoryEntry(version_after=version, changed_columns=None, updated_by=r["actor_id"], on_behalf_of=r["on_behalf_of"], updated_via=r["interface"], at=r["at"])


def current_writer(db, record_type: str, record_id: str, row: dict[str, Any]) -> HistoryEntry | None:
    """The write that produced the row's current version: its audit entry when one exists, else the row's own updated_* columns
    (a record baselined by a migration has no writer entry, but its previous writer is still named on the row)."""
    found = current_writer_from_entries(db, record_type, record_id, row["version"])
    if found is not None:
        return found
    if row.get("updated_by") is None:
        return None
    return HistoryEntry(version_after=row["version"], changed_columns=None, updated_by=row["updated_by"], on_behalf_of=None, updated_via=row.get("updated_via"), at=row.get("updated_at"))
