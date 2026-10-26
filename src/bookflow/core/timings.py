"""Per-command timings (R180): one row of numbers and fixed labels per command, kept off the books.

`dispatch.execute` (and the public deposit reads) call `begin` and `end`. The request's own
thread only reads the clock and appends a tuple to a bounded deque; a daemon thread writes
the rows to `<data root>/telemetry/timings.db` every couple of seconds, and an atexit handler
flushes a one-shot process. Whatever goes wrong with the store, the command has already
returned: every store failure is swallowed and counted.

A row never holds an argument value, a name, an amount, a record id, a user or an error
message: only the command name, the surface, the principal kind, the outcome code, the
times, the SQL statement count, two list lengths, an opaque company id and a size bucket.
"""
from __future__ import annotations

import atexit
import collections
import contextvars
import os
import threading
import time
from pathlib import Path

QUEUE_LIMIT = 10_000
FLUSH_SECONDS = 2.0

_queue: collections.deque = collections.deque(maxlen=QUEUE_LIMIT)
_sql = contextvars.ContextVar("bookflow_timings_sql", default=None)
_lock = threading.Lock()
_writer: threading.Thread | None = None
_wake = threading.Event()
stats = {"recorded": 0, "dropped": 0, "write_failures": 0, "written": 0}


def disabled_by_env() -> bool:
    return os.environ.get("BOOKFLOW_PERF", "").strip().lower() in ("off", "0", "false", "no")


class _Call:
    __slots__ = ("start", "counter", "token")


def begin():
    """Start timing one command. Returns an opaque handle, or None when recording is off."""
    if disabled_by_env() or _sql.get() is not None:
        return None  # off, or nested inside a command already being timed
    try:
        call = _Call()
        call.counter = [0, 0]  # statements, nanoseconds in execute
        call.token = _sql.set(call.counter)
        call.start = time.perf_counter_ns()
        return call
    except BaseException:
        return None


def sql_counter():
    """The running command's [statements, ns] list, or None. Read by the SQLite cursor."""
    return _sql.get()


def end(call, *, command: str, ctx, session=None, raw_input=None, result=None, error=None) -> None:
    """Finish timing; never raises."""
    if call is None:
        return
    try:
        wall = time.perf_counter_ns() - call.start
        try:
            _sql.reset(call.token)
        except (ValueError, RuntimeError):
            _sql.set(None)
        statements, sql_ns = call.counter
        surface = _surface(ctx)
        principal = _principal(ctx, session)
        if error is None:
            outcome = "ok"
        else:
            code = getattr(error, "code", None)
            outcome = code if isinstance(code, str) and code.startswith("E_") and len(code) <= 64 else "E_INTERNAL"
        company_id, size = _company(ctx, session)
        root = getattr(session, "data_root", None)
        if root is None:
            return
        if len(_queue) >= QUEUE_LIMIT:
            stats["dropped"] += 1
        _queue.append((str(root), int(time.time()), command, surface, principal, outcome,
                       wall / 1e6, statements, sql_ns / 1e6, _longest(raw_input), _longest(result),
                       company_id, size))
        stats["recorded"] += 1
        _ensure_writer()
    except BaseException:
        stats["dropped"] += 1


def _surface(ctx) -> str:
    interface = getattr(getattr(ctx, "interface", None), "value", None) or "system"
    if interface == "http" and getattr(ctx, "client_name", None) == "bookflow-workbench":
        return "workbench"
    if interface == "gui":
        return "workbench"
    return interface if interface in ("cli", "http", "mcp", "python", "system") else "system"


def _principal(ctx, session) -> str:
    kind = getattr(getattr(ctx, "actor_kind", None), "value", None)
    if kind is None:
        actor = getattr(session, "actor", None)
        kind = getattr(actor, "kind", None)
    return {"human": "human", "agent": "agent"}.get(kind, "system")


def _company(ctx, session):
    company_id = getattr(session, "company_id", None) or getattr(ctx, "company_id", None)
    size = None
    db = getattr(session, "company", None)
    path = getattr(db, "path", None)
    if path is not None:
        try:
            size = os.stat(path).st_size
        except OSError:
            size = None
    return company_id, size


def _longest(value) -> int | None:
    """The length of the longest list among a dict's values, one level down too (lines, sources, items)."""
    if not isinstance(value, dict):
        return None
    best = None
    for v in value.values():
        if isinstance(v, list):
            best = max(best or 0, len(v))
        elif isinstance(v, dict):
            for w in v.values():
                if isinstance(w, list):
                    best = max(best or 0, len(w))
    return best


def size_class(size: int | None) -> str | None:
    if size is None:
        return None
    for limit, label in ((1 << 20, "<1MB"), (10 << 20, "<10MB"), (100 << 20, "<100MB"), (1 << 30, "<1GB")):
        if size < limit:
            return label
    return ">=1GB"


# ---------------------------------------------------------------- writer

def _ensure_writer() -> None:
    global _writer
    if _writer is not None and _writer.is_alive():
        return
    with _lock:
        if _writer is not None and _writer.is_alive():
            return
        _writer = threading.Thread(target=_loop, name="bookflow-timings", daemon=True)
        _writer.start()


def _loop() -> None:
    while True:
        _wake.wait(FLUSH_SECONDS)
        _wake.clear()
        flush()


def flush() -> int:
    """Write every queued row to its root's store. Returns rows written; never raises."""
    rows = []
    try:
        while True:
            rows.append(_queue.popleft())
    except IndexError:
        pass
    if not rows:
        return 0
    by_root: dict[str, list] = {}
    for row in rows:
        by_root.setdefault(row[0], []).append(row[1:])
    written = 0
    with _lock:
        for root, batch in by_root.items():
            try:
                from bookflow.perf import store
                written += store.write(Path(root), batch)
            except BaseException:
                stats["write_failures"] += 1
    stats["written"] += written
    return written


def pending() -> int:
    return len(_queue)


def _at_exit() -> None:
    try:
        flush()
    except BaseException:
        pass


atexit.register(_at_exit)
