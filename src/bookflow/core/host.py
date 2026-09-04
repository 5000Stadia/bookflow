"""The host process: lock holder, one writer thread, per-request readers (row 3 plan, The host process)."""

from __future__ import annotations

import json
import logging
import os
import queue
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any, Callable

from bookflow.core.config import Config
from bookflow.core.context import Context
from bookflow.core.fs import check_local
from bookflow.core.locks import RootLock
from bookflow.core.session import Session
from bookflow.storage.engine import Database

log = logging.getLogger("bookflow.host")


@dataclass
class _Job:
    fn: Callable[[], Any]
    done: threading.Event = field(default_factory=threading.Event)
    result: Any = None
    error: BaseException | None = None


class Host:
    """Owns the data-root lock and every connection while it runs. One writer thread; readers per request."""

    def __init__(self, data_root: Path, *, version: str, idle_checkpoint_seconds: float = 30.0,
                 sweep_seconds: float = 3600.0):
        self.data_root = data_root
        self.version = version
        self.idle_checkpoint_seconds = idle_checkpoint_seconds
        self.sweep_seconds = sweep_seconds
        self._lock: RootLock | None = None
        self._queue: "queue.Queue[_Job | None]" = queue.Queue()
        self._writer = threading.Thread(target=self._writer_loop, name="bookflow-writer", daemon=True)
        self._hub: Database | None = None  # writer's writable hub
        self._hub_cm = None  # the context manager that owns the hub connection; it closes the database when it is released
        self._companies: dict[str, Database] = {}  # writer's writable company connections by id
        self._seq: dict[str, int] = {}
        self._subscriptions: dict[str, tuple[str, Any, Any]] = {}
        self._subscriptions_lock = threading.Lock()
        self._refresh_pending: set[str] = set()
        self._refresh_lock = threading.Lock()
        self._readers_attached = 0
        self._readers_lock = threading.Lock()
        self._stopping = False
        self._umask_old: int | None = None
        self._last_write = time.monotonic()  # when the writer last committed; the idle checkpoint waits on it
        self._checkpointed_write = 0.0  # the _last_write value the last idle checkpoint answered, so one write earns one checkpoint
        self._timer_stop = threading.Event()
        self._timer = threading.Thread(target=self._timer_loop, name="bookflow-host-timers", daemon=True)

    # ---------------------------------------------------------------- lifecycle
    def start(self) -> None:
        check_local(self.data_root)
        self._umask_old = os.umask(0o077)
        self._lock = RootLock(self.data_root, "serve")
        self._lock.__enter__()
        self._writer.start()
        self.submit(self._open_hub_on_writer)
        self._timer.start()

    def stop(self) -> None:
        self.begin_shutdown()
        # the timers submit to the writer, so they stop first: a job enqueued after the sentinel never completes
        self._timer_stop.set()
        if self._timer.is_alive():
            self._timer.join(timeout=10)
        self.submit(self._shutdown_on_writer)
        self._queue.put(None)
        self._writer.join(timeout=30)
        if self._lock is not None:
            self._lock.__exit__(None, None, None)
            self._lock = None
        if self._umask_old is not None:
            os.umask(self._umask_old)

    # ---------------------------------------------------------------- writer thread
    def submit(self, fn: Callable[[], Any], timeout: float | None = None) -> Any:
        """Run ``fn`` on the writer thread and return its result; exceptions propagate to the caller."""
        job = _Job(fn)
        self._queue.put(job)
        if not job.done.wait(timeout):
            raise TimeoutError("the Bookflow writer did not finish the submitted job in time")
        if job.error is not None:
            raise job.error
        return job.result

    def _writer_loop(self) -> None:
        while True:
            job = self._queue.get()
            if job is None:
                return
            try:
                job.result = job.fn()
            except BaseException as e:  # noqa: BLE001 - handed back to the submitter
                job.error = e
            finally:
                self._leave_clean()
                job.done.set()

    def _leave_clean(self) -> None:
        """A long-lived connection must never carry an open transaction into the next request."""
        for db in [self._hub, *self._companies.values()]:
            if db is None:
                continue
            try:
                if db.raw.in_transaction:
                    db.raw.execute("ROLLBACK")
            except sqlite3.Error:
                self._discard(db)

    def _discard(self, db: Database) -> None:
        for cid, d in list(self._companies.items()):
            if d is db:
                self._companies.pop(cid, None)
        cm = self._hub_cm if db is self._hub else None
        try:
            if cm is not None:
                cm.__exit__(None, None, None)  # the context manager closes the database; closing it twice raises
            else:
                db.close()
        except Exception:  # noqa: BLE001
            pass
        if db is self._hub:
            self._hub = None
            self._hub_cm = None

    def _open_hub_on_writer(self) -> None:
        from bookflow.core.dispatch import _open_hub
        s = Session(data_root=self.data_root, os_login="", config=Config.load(self.data_root / "config.toml"))
        s.company_opener = self._company_for_writer
        s.company_releaser = self.release_company
        _open_hub(s, True, self._system_ctx())
        # the session's context manager owns the connection: hold it for the host's life, or the database
        # closes as soon as the session is collected and the writer finds itself without a hub.
        self._hub_cm = getattr(s, "_hub_cm", None)
        self._hub = s.hub
        s._hub_cm = None  # type: ignore[attr-defined]

    def _ensure_hub_on_writer(self) -> Database:
        """Reopen a discarded writer hub before the next queued database operation."""
        if self._hub is None:
            self._open_hub_on_writer()
        assert self._hub is not None
        return self._hub

    def _shutdown_on_writer(self) -> None:
        for cid, db in list(self._companies.items()):
            try:
                db.raw.execute("DELETE FROM presence WHERE interface = 'http'")
                db.raw.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except sqlite3.Error as e:
                log.warning("shutdown checkpoint for %s: %s", cid, e)
            db.close()
        self._companies.clear()
        if self._hub is not None:
            try:
                self._hub.raw.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except sqlite3.Error as e:
                log.warning("shutdown checkpoint for hub: %s", e)
            if self._hub_cm is not None:
                self._hub_cm.__exit__(None, None, None)
                self._hub_cm = None
            else:
                self._hub.close()
            self._hub = None

    # ---------------------------------------------------------------- sessions
    def _system_ctx(self) -> Context:
        from bookflow.core.context import Interface
        return Context.new(Interface.system, "bookflow-host")

    def _writer_session(self) -> Session:
        self._ensure_hub_on_writer()
        s = Session(data_root=self.data_root, os_login="", config=Config.load(self.data_root / "config.toml"))
        s.hub = self._hub
        s.company_opener = self._company_for_writer
        s.company_releaser = self.release_company
        return s

    def _company_for_writer(self, row: dict[str, Any], writable: bool, db_path: Path) -> Database:
        db = self._companies.get(row["id"])
        if db is None or db.path != db_path:
            if db is not None:
                self._discard(db)
            db = Database(db_path, True)
            self._companies[row["id"]] = db
        return db

    def release_company(self, company_id: str) -> None:
        """Close the writer's connection to a company before its folder moves or is trashed."""
        db = self._companies.pop(company_id, None)
        if db is not None:
            db.close()

    def reader_session(self, user_id: str, login: str = "") -> Session:
        """A read-only session on the calling thread; closed by the caller through dispatch._close."""
        from bookflow.core.dispatch import _open_hub, _load_actor_by_id
        s = Session(data_root=self.data_root, os_login=login, config=Config.load(self.data_root / "config.toml"))
        _open_hub(s, False, self._system_ctx())
        _load_actor_by_id(s, user_id)
        with self._readers_lock:
            self._readers_attached += 1
        return s

    def reader_done(self) -> None:
        with self._readers_lock:
            self._readers_attached = max(0, self._readers_attached - 1)

    def run_write(self, user_id: str, login: str, fn: Callable[[Session], Any]) -> Any:
        """Build the session on the writer, run ``fn(session)`` there, checkpoint passively, signal subscribers."""
        def job():
            from bookflow.core.dispatch import _load_actor_by_id
            s = self._writer_session()
            s.os_login = login
            _load_actor_by_id(s, user_id)
            try:
                return fn(s)
            finally:
                # The request session is ephemeral; the host's writable
                # company connection is deliberately pooled across requests.
                s.close_company(release=False)
                # A command can report E_PARTIAL_WRITE after one database has
                # committed. Those durable events still need a checkpoint and
                # must wake subscribers even though the command raises.
                self._after_write()
        return self.submit(job)

    def _after_write(self) -> None:
        self._last_write = time.monotonic()
        for name, db in [("hub", self._hub), *self._companies.items()]:
            if db is None:
                continue
            try:
                db.raw.execute("PRAGMA wal_checkpoint(PASSIVE)")
                row = db.raw.execute("SELECT max(seq) FROM audit_events").fetchone()
            except sqlite3.Error:
                continue
            seq = row[0] or 0
            with self._subscriptions_lock:
                changed = seq != self._seq.get(name)
                self._seq[name] = seq
            if changed:
                self._wake_subscribers(name)

    # ---------------------------------------------------------------- async stream notifications
    def subscribe(self, key: str, loop: Any, event: Any) -> tuple[str, int]:
        """Register an asyncio event without making the host thread depend on an event loop."""
        token = uuid.uuid4().hex
        with self._subscriptions_lock:
            self._subscriptions[token] = (key, loop, event)
            sequence = self._seq.get(key, 0)
        return token, sequence

    def unsubscribe(self, token: str) -> None:
        with self._subscriptions_lock:
            self._subscriptions.pop(token, None)

    def stream_sequence(self, key: str) -> int:
        with self._subscriptions_lock:
            return self._seq.get(key, 0)

    def _wake_subscribers(self, key: str | None = None) -> None:
        with self._subscriptions_lock:
            subscribers = list(self._subscriptions.values())
        for subscribed_key, loop, event in subscribers:
            if key is not None and subscribed_key != key:
                continue
            try:
                loop.call_soon_threadsafe(event.set)
            except RuntimeError:
                # A disconnected client may have closed its event loop before its
                # generator's finally block removes the subscription.
                pass

    def begin_shutdown(self) -> None:
        """Make shutdown observable immediately and wake every idle event stream."""
        self._stopping = True
        self._wake_subscribers()

    # ---------------------------------------------------------------- credential liveness
    def enqueue_token_refresh(self, token_id: str, kind: str) -> bool:
        """Queue at most one unaudited liveness refresh per token, without blocking a reader."""
        with self._refresh_lock:
            if self._stopping or token_id in self._refresh_pending:
                return False
            self._refresh_pending.add(token_id)

        def refresh() -> None:
            try:
                from bookflow.adapters.http import auth
                auth.refresh_token(self._ensure_hub_on_writer(), token_id, kind)
            except BaseException as e:  # noqa: BLE001 - a liveness bump never kills the host
                log.warning("credential refresh failed: %s", e)
            finally:
                with self._refresh_lock:
                    self._refresh_pending.discard(token_id)

        self._queue.put(_Job(refresh))
        return True

    # ---------------------------------------------------------------- timers
    def _timer_loop(self) -> None:
        """One daemon thread for both timers; every database touch is submitted to the writer."""
        tick = max(0.05, min(1.0, self.idle_checkpoint_seconds, self.sweep_seconds))
        last_sweep = time.monotonic()
        while not self._timer_stop.wait(tick):
            now = time.monotonic()
            with self._readers_lock:
                no_readers = self._readers_attached == 0
            if (no_readers and self._last_write > self._checkpointed_write
                    and now - self._last_write >= self.idle_checkpoint_seconds):
                self._checkpointed_write = self._last_write
                try:
                    self.checkpoint_now()
                except BaseException as e:  # noqa: BLE001 - a timer never kills the host
                    log.warning("idle checkpoint: %s", e)
            if now - last_sweep >= self.sweep_seconds:
                last_sweep = now
                try:
                    self.sweep_now()
                except BaseException as e:  # noqa: BLE001
                    log.warning("session sweep: %s", e)

    def checkpoint_now(self) -> dict[str, Any]:
        """RESTART checkpoint the hub and every pooled company connection, on the writer. Results are logged."""
        if not self._writer.is_alive():
            return {}
        return self.submit(self._checkpoint_on_writer)

    def _checkpoint_on_writer(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for name, db in [("hub", self._hub), *self._companies.items()]:
            if db is None:
                continue
            try:
                row = db.raw.execute("PRAGMA wal_checkpoint(RESTART)").fetchone()
            except sqlite3.Error as e:
                log.warning("idle checkpoint of %s failed: %s", name, e)
                out[name] = None
                continue
            result = tuple(row) if row is not None else None
            log.info("idle checkpoint of %s: %s", name, result)
            out[name] = result
        return out

    def sweep_now(self) -> int:
        """Delete session tokens expired more than a day, as one system audit event. Returns how many went."""
        if not self._writer.is_alive():
            return 0
        return self.submit(self._sweep_on_writer)

    def _sweep_on_writer(self) -> int:
        import sqlalchemy as sa

        from bookflow.core import clock
        from bookflow.core.audit import write_event_to
        from bookflow.core.registry import Touched
        from bookflow.hub import schema as h
        db = self._ensure_hub_on_writer()
        cutoff = (clock.now() - timedelta(days=1)).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        rows = [dict(r) for r in db.conn.execute(sa.select(h.api_tokens).where(
            h.api_tokens.c.kind == "session", h.api_tokens.c.expires_at.isnot(None),
            h.api_tokens.c.expires_at < cutoff)).mappings().all()]
        if not rows:
            return 0
        system = db.conn.execute(sa.select(h.users).where(h.users.c.kind == "system")).mappings().first()
        touched = [Touched("api_token", r["id"], "delete", r["version"], None, None,
                           before={k: v for k, v in r.items() if k != "token_hash"}) for r in rows]
        ctx = self._system_ctx()
        db.raw.execute("BEGIN IMMEDIATE")
        try:
            db.conn.execute(h.api_tokens.delete().where(h.api_tokens.c.id.in_([r["id"] for r in rows])))
            write_event_to(db, ctx, "session sweep", f"swept {len(rows)} expired browser session(s)", touched,
                           actor_id=system["id"] if system else None, actor_kind="system")
            db.raw.execute("COMMIT")
        except BaseException:
            if db.raw.in_transaction:
                db.raw.execute("ROLLBACK")
            raise
        log.info("session sweep removed %d expired session token(s)", len(rows))
        self._after_write()
        return len(rows)

    # ---------------------------------------------------------------- descriptor
    def write_descriptor(self, bind: str, socket_path: str) -> None:
        p = self.data_root / "host.json"
        p.write_text(json.dumps({"pid": os.getpid(), "bind": bind, "socket": socket_path, "version": self.version, "started": time.time()}), encoding="utf-8")
        os.chmod(p, 0o600)

    def remove_descriptor(self) -> None:
        try:
            (self.data_root / "host.json").unlink()
        except OSError:
            pass
