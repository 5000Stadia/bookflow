"""The timings store: `<data root>/telemetry/timings.db`, telemetry kept apart from the books.

Raw rows (`calls`) are kept 30 days and at most RAW_CAP rows; every finished UTC day is rolled up
into `daily` (count, errors, p50, p95, max, totals) and sealed, and daily rows are kept 730 days.
Company ids are stored only as an HMAC under a random per-installation salt.
"""
from __future__ import annotations

import hashlib
import hmac
import math
import os
import secrets
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

RAW_DAYS = 30
RAW_CAP = 100_000
DAILY_DAYS = 730
MAINTAIN_SECONDS = 60

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS calls (
    at INTEGER NOT NULL, command TEXT NOT NULL, surface TEXT NOT NULL, principal TEXT NOT NULL,
    outcome TEXT NOT NULL, wall_ms REAL NOT NULL, sql_count INTEGER NOT NULL, sql_ms REAL NOT NULL,
    rows_in INTEGER, rows_out INTEGER, company TEXT, size_class TEXT);
CREATE INDEX IF NOT EXISTS calls_at ON calls(at);
CREATE INDEX IF NOT EXISTS calls_command ON calls(command, at);
CREATE TABLE IF NOT EXISTS daily (
    day TEXT NOT NULL, command TEXT NOT NULL, surface TEXT NOT NULL, count INTEGER NOT NULL,
    errors INTEGER NOT NULL, p50_ms REAL NOT NULL, p95_ms REAL NOT NULL, max_ms REAL NOT NULL,
    total_ms REAL NOT NULL, total_sql INTEGER NOT NULL, PRIMARY KEY (day, command, surface));
CREATE TABLE IF NOT EXISTS sealed (day TEXT PRIMARY KEY);
"""


def path(root: Path) -> Path:
    return Path(root) / "telemetry" / "timings.db"


def _open(root: Path, create: bool = True) -> sqlite3.Connection | None:
    p = path(root)
    if not p.exists():
        if not create:
            return None
        p.parent.mkdir(mode=0o700, exist_ok=True)
    db = sqlite3.connect(p, timeout=0.5, isolation_level=None)
    try:
        if create:
            os.chmod(p, 0o600)
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=NORMAL")
        db.executescript(SCHEMA)
    except BaseException:
        db.close()
        raise
    return db


def _meta(db, key, default=None):
    row = db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row[0] if row else default


def _set_meta(db, key, value) -> None:
    db.execute("INSERT INTO meta(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
               (key, str(value)))


def _salt(db) -> bytes:
    value = _meta(db, "salt")
    if value is None:
        value = secrets.token_hex(32)
        db.execute("INSERT OR IGNORE INTO meta(key, value) VALUES ('salt', ?)", (value,))
        value = _meta(db, "salt")
    return bytes.fromhex(value)


def _hash(salt: bytes, company_id: str | None) -> str | None:
    if not company_id:
        return None
    return hmac.new(salt, company_id.encode(), hashlib.sha256).hexdigest()[:16]


def company_hashes(root: Path, company_ids) -> set[str]:
    db = _open(root, create=False)
    if db is None:
        return set()
    try:
        salt = _salt(db)
        return {_hash(salt, c) for c in company_ids if c}
    finally:
        db.close()


def enabled(root: Path) -> bool:
    db = _open(root, create=False)
    if db is None:
        return True
    try:
        return _meta(db, "enabled", "1") == "1"
    finally:
        db.close()


def set_enabled(root: Path, on: bool) -> None:
    db = _open(root)
    try:
        _set_meta(db, "enabled", "1" if on else "0")
    finally:
        db.close()


def write(root: Path, batch: list[tuple], *, now: float | None = None) -> int:
    """Append rows (from core.timings) unless recording is off; run retention at most once a minute."""
    from bookflow.core.timings import size_class
    db = _open(root)
    try:
        if _meta(db, "enabled", "1") != "1":
            return 0
        salt = _salt(db)
        rows = [(at, command, surface, principal, outcome, round(wall, 3), statements, round(sql, 3),
                 rows_in, rows_out, _hash(salt, company), size_class(size))
                for (at, command, surface, principal, outcome, wall, statements, sql, rows_in, rows_out,
                     company, size) in batch]
        db.execute("BEGIN IMMEDIATE")
        try:
            db.executemany("INSERT INTO calls VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows)
            now = time.time() if now is None else now
            if float(_meta(db, "maintained", "0")) + MAINTAIN_SECONDS <= now:
                _maintain(db, now)
                _set_meta(db, "maintained", now)
            db.execute("COMMIT")
        except BaseException:
            db.execute("ROLLBACK")
            raise
        return len(rows)
    finally:
        db.close()


def maintain(root: Path, now: float | None = None) -> None:
    db = _open(root)
    try:
        db.execute("BEGIN IMMEDIATE")
        _maintain(db, time.time() if now is None else now)
        db.execute("COMMIT")
    finally:
        db.close()


def _day(at: int) -> str:
    return datetime.fromtimestamp(at, timezone.utc).date().isoformat()


def _day_start(day: str) -> int:
    return int(datetime.fromisoformat(day).replace(tzinfo=timezone.utc).timestamp())


def percentile(values: list[float], p: float) -> float:
    """Nearest rank: the smallest value with at least p of the values at or below it."""
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[max(0, math.ceil(p * len(ordered)) - 1)]


def _seal(db, day: str) -> None:
    if db.execute("SELECT 1 FROM sealed WHERE day=?", (day,)).fetchone():
        return
    start = _day_start(day)
    groups: dict[tuple, list] = {}
    for command, surface, outcome, wall, statements in db.execute(
            "SELECT command, surface, outcome, wall_ms, sql_count FROM calls WHERE at >= ? AND at < ?",
            (start, start + 86400)):
        groups.setdefault((command, surface), []).append((wall, statements, outcome != "ok"))
    for (command, surface), rows in groups.items():
        walls = [r[0] for r in rows]
        db.execute("INSERT OR REPLACE INTO daily VALUES (?,?,?,?,?,?,?,?,?,?)",
                   (day, command, surface, len(rows), sum(r[2] for r in rows), percentile(walls, 0.5),
                    percentile(walls, 0.95), max(walls), round(sum(walls), 3), sum(r[1] for r in rows)))
    db.execute("INSERT INTO sealed(day) VALUES (?)", (day,))


def _maintain(db, now: float) -> None:
    today = _day(int(now))
    # Roll up and seal every finished day that still has raw rows.
    days = {_day(at) for (at,) in db.execute(
        "SELECT DISTINCT (at / 86400) * 86400 FROM calls WHERE at < ?", (_day_start(today),))}
    for day in sorted(days):
        _seal(db, day)
    db.execute("DELETE FROM calls WHERE at < ?", (int(now) - RAW_DAYS * 86400,))
    total = db.execute("SELECT count(*) FROM calls").fetchone()[0]
    if total > RAW_CAP:
        cut = db.execute("SELECT at FROM calls ORDER BY at LIMIT 1 OFFSET ?", (total - RAW_CAP,)).fetchone()[0]
        for (at,) in db.execute("SELECT DISTINCT (at / 86400) * 86400 FROM calls WHERE at <= ?", (cut,)).fetchall():
            _seal(db, _day(at))  # rows leave early only after their day is rolled up
        db.execute("DELETE FROM calls WHERE rowid IN (SELECT rowid FROM calls ORDER BY at, rowid LIMIT ?)",
                   (total - RAW_CAP,))
    oldest = datetime.fromtimestamp(now - DAILY_DAYS * 86400, timezone.utc).date().isoformat()
    db.execute("DELETE FROM daily WHERE day < ?", (oldest,))
    db.execute("DELETE FROM sealed WHERE day < ?", (oldest,))


def info(root: Path) -> dict:
    p = path(root)
    out = {"path": str(p), "exists": p.exists(), "enabled": True, "bytes": 0, "raw_rows": 0, "daily_rows": 0,
           "raw_days": RAW_DAYS, "raw_cap": RAW_CAP, "daily_days": DAILY_DAYS}
    db = _open(root, create=False)
    if db is None:
        return out
    try:
        out["enabled"] = _meta(db, "enabled", "1") == "1"
        out["raw_rows"] = db.execute("SELECT count(*) FROM calls").fetchone()[0]
        out["daily_rows"] = db.execute("SELECT count(*) FROM daily").fetchone()[0]
    finally:
        db.close()
    out["bytes"] = sum(f.stat().st_size for f in p.parent.glob("timings.db*") if f.is_file())
    return out


def report(root: Path, *, since: int, command: str | None = None, surface: str | None = None,
           companies: set[str] | None = None, limit: int = 50, slowest: int = 10) -> dict:
    """Per (command, surface): count, errors, p50, p95, max, mean statements; and the slowest calls.

    `companies`, when given, keeps only rows for those company hashes (rows with no company drop out).
    """
    empty = {"items": [], "slowest": [], "calls": 0}
    db = _open(root, create=False)
    if db is None:
        return empty
    try:
        where, args = ["at >= ?"], [since]
        if command:
            where.append("command = ?"); args.append(command)
        if surface:
            where.append("surface = ?"); args.append(surface)
        rows = db.execute(
            "SELECT at, command, surface, principal, outcome, wall_ms, sql_count, sql_ms, rows_in, rows_out, "
            "company, size_class FROM calls WHERE " + " AND ".join(where), args).fetchall()
    finally:
        db.close()
    if companies is not None:
        rows = [r for r in rows if r[10] in companies]
    groups: dict[tuple, list] = {}
    for r in rows:
        groups.setdefault((r[1], r[2]), []).append(r)
    items = []
    for (cmd, surf), rs in groups.items():
        walls = [r[5] for r in rs]
        items.append({"command": cmd, "surface": surf, "count": len(rs), "errors": sum(r[4] != "ok" for r in rs),
                      "p50_ms": round(percentile(walls, 0.5), 1), "p95_ms": round(percentile(walls, 0.95), 1),
                      "max_ms": round(max(walls), 1), "total_ms": round(sum(walls), 1),
                      "mean_sql": round(sum(r[6] for r in rs) / len(rs), 1),
                      "max_rows": max(max(r[8] or 0, r[9] or 0) for r in rs)})
    items.sort(key=lambda x: (-x["p95_ms"], x["command"], x["surface"]))
    top = sorted(rows, key=lambda r: -r[5])[:slowest]
    return {"calls": len(rows), "items": items[:limit], "slowest": [
        {"at": datetime.fromtimestamp(r[0], timezone.utc).isoformat(timespec="seconds"), "command": r[1],
         "surface": r[2], "principal": r[3], "outcome": r[4], "wall_ms": round(r[5], 1), "sql_count": r[6],
         "sql_ms": round(r[7], 1), "rows_in": r[8], "rows_out": r[9], "size_class": r[11]} for r in top]}
