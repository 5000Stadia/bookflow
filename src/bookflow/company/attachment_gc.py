"""Bounded durable attachment collection under the caller's filesystem exclusion."""
from __future__ import annotations

import ctypes
import json
import os
import re
import stat
import sys
from pathlib import Path

import sqlalchemy as sa

from bookflow.company import schema as c
from bookflow.core import audit, idempotency
from bookflow.core.context import Context
from bookflow.core.durability import sync_directory
from bookflow.core.errors import BookflowError
from bookflow.core.ids import new_id
from bookflow.core.registry import Touched
from bookflow.core.session import now_iso

MAX_SCAN = 512
NAME_MAX = 255  # Linux directory-entry name bytes, excluding the terminating NUL.
DIGEST = re.compile(r"[0-9a-f]{64}\Z")
TEMP = re.compile(r"\.attachment-[a-z0-9_]{8}\.tmp\Z")


def pending(s) -> bool:
    return s.company.conn.execute(sa.select(c.attachment_collection.c.id).limit(1)).first() is not None


def _io():
    return BookflowError("E_IO", "Attachment collection could not finish; its durable intent is retained.")


def _unlinked():
    return ~sa.exists(sa.select(c.attachment_links.c.id).where(
        c.attachment_links.c.attachment_id == c.attachments.c.id,
        c.attachment_links.c.active.is_(True)))


def metadata_candidates(s, limit):
    rows = s.company.conn.execute(sa.select(c.attachments).where(
        c.attachments.c.collected_at.is_(None), _unlinked()).order_by(c.attachments.c.id).limit(limit + 1)).mappings().all()
    return [dict(r) for r in rows[:limit]], len(rows) > limit


def _directory(path):
    """Validate every ancestor before using a path, including an absent leaf."""
    for part in reversed((path, *path.parents)):
        try:
            mode = part.lstat().st_mode
        except FileNotFoundError:
            return False
        if not stat.S_ISDIR(mode):
            raise _io()
    return True


def _entry(path):
    if not _directory(path.parent):
        return None
    try:
        st = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(st.st_mode):
        raise _io()
    return st


def metadata_items(s, rows):
    """Measure initial bodies under exclusion, or read-only for a projection."""
    store = s.company.path.parent / "attachments"
    items = []
    try:
        for row in rows:
            item = {"relative": f"{row['sha256'][:2]}/{row['sha256']}",
                    "sha256": row["sha256"], "before": row}
            st = _entry(_candidate_path(store, item))
            item.update(initial_present=st is not None, size_bytes=st.st_size if st is not None else 0)
            items.append(item)
    except OSError as exc:
        raise _io() from exc
    return items


def _scan_page(path, cookie, budget):
    """Read at most budget directory entries, resuming a POSIX directory cookie.

    Cookies avoid materializing or rewalking an arbitrarily large directory.
    Directory identity accompanies the cookie; a replaced directory restarts.
    Native discovery is limited to 64-bit Linux. Other platforms report incomplete
    discovery instead of an exhaustive scan. Persisted cookies depend on the
    filesystem retaining their meaning across directory opens and mutations.
    """
    if sys.platform != "linux" or ctypes.sizeof(ctypes.c_void_p) != 8 or ctypes.sizeof(ctypes.c_long) != 8:
        return [], cookie, False, 0
    libc = ctypes.CDLL(None, use_errno=True)
    libc.opendir.argtypes, libc.opendir.restype = [ctypes.c_char_p], ctypes.c_void_p
    libc.readdir.argtypes, libc.readdir.restype = [ctypes.c_void_p], ctypes.c_void_p
    libc.telldir.argtypes, libc.telldir.restype = [ctypes.c_void_p], ctypes.c_long
    libc.seekdir.argtypes = [ctypes.c_void_p, ctypes.c_long]
    libc.closedir.argtypes = [ctypes.c_void_p]
    handle = libc.opendir(os.fsencode(path))
    if not handle:
        raise OSError(ctypes.get_errno(), "collection scan")
    try:
        if cookie:
            libc.seekdir(handle, cookie)
        names = []
        for used in range(budget):
            ctypes.set_errno(0)
            entry = libc.readdir(handle)
            if not entry:
                if ctypes.get_errno():
                    raise OSError(ctypes.get_errno(), "collection scan")
                return names, 0, True, used + 1
            # Linux LP64 dirent: ino64, off64, reclen16, type8, name.
            # Respect both the entry allocation and the maximum name length.
            offset = 19
            record_size = ctypes.c_ushort.from_address(entry + 16).value
            if record_size <= offset:
                raise OSError("invalid collection directory entry")
            raw_name = ctypes.string_at(entry + offset, min(record_size - offset, NAME_MAX + 1))
            name_bytes, terminator, _ = raw_name.partition(b"\0")
            if not terminator:
                raise OSError("invalid collection directory name")
            name = os.fsdecode(name_bytes)
            cookie = libc.telldir(handle)
            if cookie < 0:
                raise OSError("collection directory cookie")
            if name not in (".", ".."):
                names.append(name)
        return names, cookie, False, budget
    finally:
        libc.closedir(handle)


def _last_cursor(s):
    # The previous completion's audit snapshot is durable, bounded operational
    # continuation. No extra row can masquerade as a pending collection intent.
    event = s.company.conn.execute(sa.select(c.audit_events.c.id).where(
        c.audit_events.c.command == "company compact").order_by(c.audit_events.c.seq.desc()).limit(1)).scalar()
    if event is None:
        return {"directory": 0, "cookie": 0}
    blob = s.company.conn.execute(sa.select(c.audit_entries.c.after).where(
        c.audit_entries.c.event_id == event,
        c.audit_entries.c.record_type == "attachment_collection").limit(1)).scalar()
    return (audit.decode_snapshot(blob) or {}).get("scan_cursor", {"directory": 0, "cookie": 0})


def discover(s, remaining, cursor):
    """Scan the store and its 256 recognized shards, within a separate entry budget."""
    store = s.company.path.parent / "attachments"
    selected, spent = [], 0
    cursor = dict(cursor)
    while spent < MAX_SCAN and remaining > 0:
        directory = cursor.get("directory", 0)
        path = store if directory == 0 else store / f"{directory - 1:02x}"
        if not _directory(path):
            done, used, names, cookie = True, 1, [], 0
        else:
            st = path.stat()
            identity = [st.st_dev, st.st_ino]
            old_cookie = cursor.get("cookie", 0) if cursor.get("identity") == identity else 0
            # At most one selectable candidate per returned entry; do not advance
            # a cookie over a candidate that does not fit this invocation.
            names, cookie, done, used = _scan_page(path, old_cookie, min(MAX_SCAN - spent, remaining))
            cursor["identity"] = identity
        spent += used
        for name in names:
            digest = name if directory and DIGEST.fullmatch(name) and name[:2] == path.name else None
            if not digest and not (directory == 0 and TEMP.fullmatch(name)):
                continue
            # A failed restore may publish bytes but roll back the metadata
            # transaction. Its still-collected row does not own those bytes;
            # collect them as an orphan without rewriting historical metadata.
            if digest and s.company.conn.execute(sa.select(c.attachments.c.id).where(
                c.attachments.c.sha256 == digest,
                sa.or_(c.attachments.c.collected_at.is_(None), ~_unlinked())).limit(1)).first():
                continue
            st = _entry(path / name)
            if st is not None:
                selected.append({"relative": str((path / name).relative_to(store)), "sha256": digest,
                                 "initial_present": True, "size_bytes": st.st_size, "before": None})
                remaining -= 1
        if not done:
            cursor["cookie"] = cookie
            if not used:  # unsupported platform: explicit incomplete discovery
                break
        else:
            cursor = {"directory": directory + 1, "cookie": 0}
            if directory == 256:
                return selected, {"directory": 0, "cookie": 0}, False
    return selected, cursor, True


def _gate(s, ctx):
    if s.transfer is not None:
        raise BookflowError("E_DB_BUSY", "Collection cannot run with a transfer lease.")
    if s.dry_run or s.company is None or not s.company.writable:
        raise BookflowError("E_DB_BUSY", "Collection requires an authorized writable session.")
    # _apply's provisional transaction may contain mirrored principals. Closing
    # rolls it back before the host drains readers/transfers and closes its pool.
    s.release_company(s.company_id)
    from bookflow.core.dispatch import open_company
    open_company(s, ctx, True)


def _candidate_path(store, item):
    relative = item["relative"]
    digest = item["sha256"]
    if digest is not None:
        if not DIGEST.fullmatch(digest) or relative != f"{digest[:2]}/{digest}":
            raise _io()
    elif not TEMP.fullmatch(relative):
        raise _io()
    return store / relative


def _validate_links(s, items):
    for item in items:
        digest = item["sha256"]
        if digest and s.company.conn.execute(sa.select(c.attachment_links.c.id).join(
            c.attachments, c.attachments.c.id == c.attachment_links.c.attachment_id).where(
                c.attachments.c.sha256 == digest, c.attachment_links.c.active.is_(True)).limit(1)).first():
            raise _io()


def _finish(s, operation_id, payload):
    items = payload["candidates"]
    if not isinstance(items, list) or len(items) > 200:
        raise _io()
    store = s.company.path.parent / "attachments"
    ctx = Context.model_validate(payload["context"])
    try:
        _validate_links(s, items)
        paths = [_candidate_path(store, item) for item in items]
        # Validate the entire batch before removing its first entry.
        for path in paths:
            _entry(path)
        for path in paths:
            if _entry(path) is not None:
                path.unlink()
        # Missing-on-retry still needs its original parent directory synced.
        for parent in sorted({path.parent for path in paths}):
            if _directory(parent):
                sync_directory(parent)
        if paths and _directory(store):
            sync_directory(store)
        s.company.raw.execute("BEGIN IMMEDIATE")
        _validate_links(s, items)
        touched = []
        for item in items:
            before = item["before"]
            if before is None:
                continue
            after = dict(before, collected_at=payload["at"], version=before["version"] + 1,
                         updated_at=payload["at"], updated_by=payload["actor_id"], updated_via=ctx.interface.value)
            changed = s.company.conn.execute(c.attachments.update().where(
                c.attachments.c.id == before["id"], c.attachments.c.version == before["version"],
                c.attachments.c.collected_at.is_(None)).values(**after))
            if changed.rowcount != 1:
                raise _io()
            touched.append(Touched("attachment", before["id"], "update", before["version"], after["version"], after, before, "company"))
        output = payload["output"]
        touched.append(Touched("attachment_collection", operation_id, "create", None, 1,
                               dict(output, scan_cursor=payload["scan_cursor"]), db="company"))
        audit.write_event_to(s.company, ctx, "company compact", f"collected {output['collected_count']} attachment bodies",
                            touched, actor_id=payload["actor_id"], actor_kind=payload["actor_kind"],
                            directive_code=payload["directive_code"])
        if ctx.idempotency_key:
            idempotency.store(s.company, payload["actor_id"], ctx.idempotency_key, "company compact",
                              payload["input_hash"], ctx.request_id, output)
        s.company.conn.execute(c.attachment_collection.delete().where(c.attachment_collection.c.id == operation_id))
        s.company.raw.execute("COMMIT")
        return output
    except Exception as exc:
        if s.company.write_transaction:
            s.company.raw.rollback()
        if isinstance(exc, BookflowError) and exc.code == "E_IO":
            raise
        raise _io() from exc


def recover_pending(s, ctx) -> None:
    """Called before transfer admission, after writable company authorization."""
    if s.transfer is not None:
        raise BookflowError("E_DB_BUSY", "Recovery cannot run with a transfer lease.")
    if s.dry_run or s.company is None or not s.company.writable:
        return
    if not pending(s):
        return
    _gate(s, ctx)
    row = s.company.conn.execute(sa.select(c.attachment_collection).limit(1)).mappings().first()
    if row:
        try:
            _finish(s, row["id"], json.loads(row["payload"]))
        except (ValueError, KeyError, TypeError) as exc:
            raise _io() from exc


def collect(s, ctx, limit, input_hash):
    _gate(s, ctx)
    if pending(s):
        raise BookflowError("E_DB_BUSY", "Attachment collection requires recovery.")
    try:
        s.company.raw.execute("BEGIN IMMEDIATE")
        from bookflow.core.dispatch import _upsert_principals
        _upsert_principals(s, ctx)
        rows, more = metadata_candidates(s, limit)
        items = metadata_items(s, rows)
        orphans, cursor, scan_more = discover(s, limit - len(items), _last_cursor(s))
        items.extend(orphans)
        operation_id = new_id()
        output = {"operation_id": operation_id, "collected_count": sum(item["initial_present"] for item in items),
                  "bytes_collected": sum(item["size_bytes"] for item in items), "has_more": more or scan_more,
                  "dry_run": False, "warnings": list(s.warnings), "idempotent_replay": False}
        if scan_more:
            output["warnings"].append("Orphan discovery is incomplete; run company compact again to continue.")
        payload = {"candidates": items, "context": ctx.model_dump(mode="json"), "actor_id": s.actor.id,
                   "actor_kind": s.actor.kind, "directive_code": s.directive_code, "input_hash": input_hash,
                   "at": now_iso(), "output": output, "scan_cursor": cursor}
        encoded = json.dumps(payload, ensure_ascii=False)
        if len(encoded.encode()) > 262144:
            raise _io()
        s.company.conn.execute(c.attachment_collection.insert().values(id=operation_id, payload=encoded))
        s.company.raw.execute("COMMIT")
        result = _finish(s, operation_id, payload)
        # run_in_session appends these; they are already part of the durable result.
        s.warnings.clear()
        return result
    except Exception as exc:
        if s.company.write_transaction:
            s.company.raw.rollback()
        if isinstance(exc, BookflowError):
            raise
        raise _io() from exc
