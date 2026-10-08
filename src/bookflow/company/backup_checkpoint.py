"""Audit checkpoints written beside each backup, and the checks that read them (design: blueprint 3.4.1).

A checkpoint is a small JSON file, ``<archive>.checkpoint.json``, written read-only beside a
backup archive. It holds, for the company audit trail (from the backup's own copy of the
database) and for the hub audit trail (live, when the backup was taken):

- ``last_seq`` and ``events``: the newest event's seq and how many events there were;
- ``hash``: a running SHA-256 over every audit row up to that seq (the definition is below);
- ``anchors``: the running hash at every ``ANCHOR_EVERY``-th event, keyed by that event's seq,
  so a mismatch can be narrowed to a stretch of the trail;
- ``columns``: the columns hashed, so a later migration that adds a column does not change the
  hash of rows written before it.

It also holds the backup's trial balance (per-account net in minor units, the debit and credit
totals and a digest), the archive's SHA-256, and ``previous``: the file name and SHA-256 of the
checkpoint that was newest in the same folder when this one was written. Each checkpoint thus
names its predecessor, and replacing or editing an older one breaks the link.

The running hash, for one database's ``audit_events`` and ``audit_entries``::

    h_0 = 64 zero hex digits
    for each event, in ascending seq:
        row   = [event values in `columns.events` order,
                 [[entry values in `columns.entries` order] for each entry of the event, by entry id]]
        h_i   = sha256(bytes.fromhex(h_{i-1}) + canonical_json(row)).hexdigest()

``canonical_json`` is ``json.dumps(..., separators=(",", ":"), ensure_ascii=False)`` encoded
as UTF-8; a BLOB value is written as ``{"hex": "<lowercase hex>"}``; integers, text and null are
JSON's own. Nothing here is secret: the hash proves the rows are the same, it is not a signature.
Anyone who can rewrite the books *and* every checkpoint can make them agree again, which is why
the copies belong somewhere the writer of the books cannot reach.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any, Iterator

FORMAT = "bookflow-backup-checkpoint"
FORMAT_VERSION = 1
SUFFIX = ".checkpoint.json"
ANCHOR_EVERY = 64
ZERO = "0" * 64


def checkpoint_path(archive: Path) -> Path:
    return archive.with_name(archive.name + SUFFIX)


def _canon(value: Any) -> Any:
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {"hex": bytes(value).hex()}
    return value


def _columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]


def _walk(conn: sqlite3.Connection, ev_cols: list[str], en_cols: list[str], last_seq: int | None) -> Iterator[tuple[int, str]]:
    """Yield (seq, running hash) for every event with seq <= last_seq (all when None)."""
    limit = "" if last_seq is None else " AND e.seq <= :last"
    params = {} if last_seq is None else {"last": last_seq}
    ev_sql = ", ".join(f'"{c}"' for c in ev_cols)
    en_sql = ", ".join(f'n."{c}"' for c in en_cols)
    events = conn.execute(f"SELECT seq, {ev_sql} FROM audit_events e WHERE seq IS NOT NULL{limit} ORDER BY seq", params)
    entries = conn.execute(f"SELECT e.seq, {en_sql} FROM audit_entries n JOIN audit_events e ON e.id = n.event_id "
                           f"WHERE e.seq IS NOT NULL{limit} ORDER BY e.seq, n.id", params)
    pending = entries.fetchone()
    h = ZERO
    for row in events:
        seq = row[0]
        while pending is not None and pending[0] < seq:
            pending = entries.fetchone()
        mine = []
        while pending is not None and pending[0] == seq:
            mine.append([_canon(v) for v in pending[1:]])
            pending = entries.fetchone()
        body = json.dumps([[_canon(v) for v in row[1:]], mine], separators=(",", ":"), ensure_ascii=False)
        h = hashlib.sha256(bytes.fromhex(h) + body.encode("utf-8")).hexdigest()
        yield seq, h


def audit_chain(conn: sqlite3.Connection) -> dict[str, Any]:
    """The checkpoint section for one database's audit tables, as they stand now."""
    ev_cols, en_cols = _columns(conn, "audit_events"), _columns(conn, "audit_entries")
    last, count, h, anchors = 0, 0, ZERO, {}
    for seq, h in _walk(conn, ev_cols, en_cols, None):
        count += 1
        last = seq
        if count % ANCHOR_EVERY == 0:
            anchors[str(seq)] = h
    return {"last_seq": last, "events": count, "hash": h, "anchors": anchors,
            "columns": {"events": ev_cols, "entries": en_cols}}


def compare_chain(conn: sqlite3.Connection, recorded: dict[str, Any]) -> dict[str, Any]:
    """Compare a database's audit trail with a recorded section.

    ``status`` is ``ok``; ``rollback`` when the live trail ends before the recorded seq (the
    database was put back to an earlier state); or ``changed`` when a row at or before the
    recorded seq was altered or removed, with ``from_seq``/``to_seq`` narrowing where.
    """
    live_last = conn.execute("SELECT max(seq) FROM audit_events").fetchone()[0] or 0
    out: dict[str, Any] = {"recorded_seq": recorded["last_seq"], "live_seq": live_last, "status": "ok",
                           "from_seq": None, "to_seq": None}
    if live_last < recorded["last_seq"]:
        out["status"] = "rollback"
        return out
    live_ev, live_en = set(_columns(conn, "audit_events")), set(_columns(conn, "audit_entries"))
    ev_cols, en_cols = recorded["columns"]["events"], recorded["columns"]["entries"]
    if not set(ev_cols) <= live_ev or not set(en_cols) <= live_en:
        out.update(status="changed", from_seq=1, to_seq=recorded["last_seq"])
        return out
    anchors = recorded.get("anchors") or {}
    good = 0  # the last seq through which the trail is known to match
    count, h = 0, ZERO
    for seq, h in _walk(conn, ev_cols, en_cols, recorded["last_seq"]):
        count += 1
        want = anchors.get(str(seq))
        if want is not None:
            if want != h:
                out.update(status="changed", from_seq=good + 1, to_seq=seq)
                return out
            good = seq
    if count != recorded["events"] or h != recorded["hash"]:
        out.update(status="changed", from_seq=good + 1, to_seq=recorded["last_seq"])
    return out


def trial_balance(conn: sqlite3.Connection) -> dict[str, Any]:
    """Every account's net (debits minus credits, home minor units), its totals and a digest."""
    nets: dict[str, int] = {}
    for account, debit, credit in conn.execute("SELECT account_id, debit_minor_units, credit_minor_units FROM posting_lines"):
        nets[account] = nets.get(account, 0) + int(debit) - int(credit)
    nets = {k: v for k, v in sorted(nets.items()) if v != 0}
    body = json.dumps(sorted(nets.items()), separators=(",", ":"))
    return {"accounts": len(nets), "debit_minor_units": sum(v for v in nets.values() if v > 0),
            "credit_minor_units": -sum(v for v in nets.values() if v < 0),
            "digest": hashlib.sha256(body.encode()).hexdigest()}


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while chunk := fh.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def read(path: Path) -> dict[str, Any] | None:
    """A checkpoint's contents, or None when the file is missing or not a checkpoint."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("format") != FORMAT or data.get("format_version") != FORMAT_VERSION:
        return None
    return data


def in_folder(folder: Path, company_id: str) -> list[tuple[Path, dict[str, Any]]]:
    """This company's checkpoints in one folder, oldest first."""
    found = []
    try:
        candidates = list(folder.glob("*" + SUFFIX))
    except OSError:
        return []
    for path in candidates:
        data = read(path)
        if data is not None and data.get("company_id") == company_id:
            found.append((path, data))
    found.sort(key=lambda pair: (pair[1].get("taken_at") or pair[1].get("created_at") or "", pair[0].name))
    return found


def build(*, company_id: str, backup_id: str, archive: Path, archive_sha256: str, created_at: str,
          company_section: dict[str, Any], hub_section: dict[str, Any] | None, trial: dict[str, Any]) -> dict[str, Any]:
    """The checkpoint for a new archive, linked to the newest checkpoint already beside it."""
    existing = [pair for pair in in_folder(archive.parent, company_id) if pair[0] != checkpoint_path(archive)]
    previous = None
    if existing:
        path, _ = existing[-1]
        previous = {"file": path.name, "sha256": file_sha256(path)}
    from bookflow.core.clock import now_iso
    return {"format": FORMAT, "format_version": FORMAT_VERSION, "company_id": company_id, "backup_id": backup_id,
            "archive": archive.name, "archive_sha256": archive_sha256, "created_at": created_at, "taken_at": now_iso(),
            "previous": previous, "company_audit": company_section, "hub_audit": hub_section, "trial_balance": trial,
            "hash_definition": "blueprint 3.4.1"}


def write(archive: Path, data: dict[str, Any]) -> Path:
    """Write the checkpoint beside ``archive``, synchronized, then make it read-only (0444)."""
    import os

    from bookflow.core.durability import write_metadata
    path = checkpoint_path(archive)
    write_metadata(path, json.dumps(data, indent=2, sort_keys=True))
    os.chmod(path, 0o444)
    return path


def chain_links(folder: Path, company_id: str) -> list[dict[str, Any]]:
    """Each retained checkpoint's link to its predecessor: ``ok``, ``pruned`` (gone) or ``broken``."""
    pairs = in_folder(folder, company_id)
    by_name = {p.name: p for p, _ in pairs}
    links = []
    for path, data in pairs:
        prev = data.get("previous")
        if not prev:
            continue
        target = by_name.get(prev.get("file"))
        if target is None:
            state = "broken" if (folder / str(prev.get("file"))).exists() else "pruned"
        else:
            state = "ok" if file_sha256(target) == prev.get("sha256") else "broken"
        links.append({"checkpoint": path.name, "previous": prev.get("file"), "state": state})
    return links
