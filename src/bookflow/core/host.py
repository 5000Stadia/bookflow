"""The host process: lock holder, one writer thread, per-request readers (row 3 plan, The host process)."""

from __future__ import annotations

import json
import logging
import math
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
from bookflow.core.errors import BookflowError
from bookflow.core.fs import check_local
from bookflow.core.locks import RootLock
from bookflow.core.session import Session
from bookflow.core import performance
from bookflow.storage.engine import Database
from bookflow.core.transfer_resources import TransferLease

log = logging.getLogger("bookflow.host")


@dataclass
class _Job:
    fn: Callable[[], Any]
    done: threading.Event = field(default_factory=threading.Event)
    result: Any = None
    error: BaseException | None = None
    trace_context: Any = None
    trace_wait: Any = None
    trace_mode: str = "maintenance"
    resource: TransferLease | None = None


class Host:
    """Owns the data-root lock and every connection while it runs. One writer thread; readers per request."""

    def __init__(self, data_root: Path, *, version: str, idle_checkpoint_seconds: float = 30.0,
                 sweep_seconds: float = 3600.0, filesystem_wait_seconds: float = 5.0,
                 transfer_limit: int = 8, principal_transfer_limit: int = 2,
                 transfer_lifetime_seconds: float = 300.0, shutdown_wait_seconds: float = 30.0):
        if (type(transfer_limit) is not int or not 0 < transfer_limit <= 8
                or type(principal_transfer_limit) is not int or not 0 < principal_transfer_limit <= 2
                or not math.isfinite(transfer_lifetime_seconds) or not 0 < transfer_lifetime_seconds <= 300
                or not math.isfinite(shutdown_wait_seconds) or not 0 < shutdown_wait_seconds <= 30):
            raise ValueError("Host transfer and shutdown limits must be positive and within their ceilings.")
        self.data_root = data_root
        performance.protect_root(data_root)
        self.version = version
        self.idle_checkpoint_seconds = idle_checkpoint_seconds
        self.sweep_seconds = sweep_seconds
        self.filesystem_wait_seconds = filesystem_wait_seconds
        self.transfer_limit = transfer_limit
        self.principal_transfer_limit = principal_transfer_limit
        self.transfer_lifetime_seconds = transfer_lifetime_seconds
        self.shutdown_wait_seconds = shutdown_wait_seconds
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
        self._transfers: set[TransferLease] = set()
        self._readers_lock = threading.Condition()
        self._stopping = False
        self._jobs_closed = False
        self._filesystem_exclusive = False
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
        self.submit(self._open_hub_on_writer, _maintenance=True)
        self._timer.start()

    def stop(self) -> None:
        self.begin_shutdown()
        # the timers submit to the writer, so they stop first: a job enqueued after the sentinel never completes
        self._timer_stop.set()
        if self._timer.is_alive():
            self._timer.join(timeout=min(10, self.shutdown_wait_seconds))
            if self._timer.is_alive():
                raise BookflowError("E_DB_BUSY", message="Host timers are still closing; retry shutdown.")
        self.retry_transfer_cleanup()
        # A read snapshot can prevent the final checkpoint. Keep the root
        # lock until admitted readers have actually closed their handles.
        with self._readers_lock:
            if not self._readers_lock.wait_for(
                    lambda: self._readers_attached == 0 and not self._transfers,
                    timeout=self.shutdown_wait_seconds):
                raise BookflowError("E_DB_BUSY", message="Host readers or transfers are still closing; retry shutdown.")
        if not self._jobs_closed:
            try:
                self.submit(self._shutdown_on_writer, timeout=self.shutdown_wait_seconds,
                            _during_shutdown=True, _maintenance=True)
            except TimeoutError:
                raise BookflowError("E_DB_BUSY", message="Host writer is still closing; retry shutdown.") from None
            with self._readers_lock:
                self._jobs_closed = True
                self._queue.put(None)
        self._writer.join(timeout=self.shutdown_wait_seconds)
        if self._writer.is_alive():
            raise BookflowError("E_DB_BUSY", message="Host writer is still closing; retry shutdown.")
        if self._lock is not None:
            self._lock.__exit__(None, None, None)
            self._lock = None
        if self._umask_old is not None:
            os.umask(self._umask_old)
            self._umask_old = None

    # ---------------------------------------------------------------- writer thread
    @performance.measured("writer.submit")
    def submit(self, fn: Callable[[], Any], timeout: float | None = None, *, _during_shutdown: bool = False,
               _maintenance: bool = False, resource: TransferLease | None = None) -> Any:
        """Run on the writer; an accepted job owns its resource beyond caller timeout."""
        if resource is not None:
            with self._readers_lock:
                if not isinstance(resource, TransferLease) or resource not in self._transfers:
                    raise BookflowError("E_VALIDATION", message="Transfer does not belong to this host.")
            # Never take a lease lock under the host condition: cleanup calls
            # back into the condition when it releases filesystem admission.
            resource.handoff()
        try:
            job = _Job(fn, resource=resource)
            with self._readers_lock:
                if self._jobs_closed or (self._stopping and not _during_shutdown):
                    raise BookflowError("E_DB_BUSY", message="The host is stopping; retry after shutdown.")
                job.trace_context = performance.current_context()
                job.trace_wait = performance.begin_wait("writer.queue")
                job.trace_mode = "maintenance" if _maintenance else "hosted"
                self._queue.put(job)
        except BaseException:
            if resource is not None:
                try:
                    resource.finish()
                except BaseException:
                    pass  # Retained in _transfers for explicit cleanup retry.
            raise
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
                performance.finish_wait(job.trace_wait)
                with performance.bind(job.trace_context), performance.span(
                        "writer.execute", mode=job.trace_mode):
                    try:
                        if job.resource is not None:
                            job.resource.check_start()
                        job.result = job.fn()
                    except BaseException as e:  # noqa: BLE001 - handed back to the submitter
                        job.error = e
                    finally:
                        try:
                            with performance.span("writer.cleanup"):
                                self._leave_clean()
                        except BaseException as e:  # noqa: BLE001 - cleanup errors also reach the submitter
                            if job.error is None:
                                job.error = e
                        finally:
                            with self._readers_lock:
                                self._filesystem_exclusive = False
                                self._readers_lock.notify_all()
                    if job.error is not None:
                        raise job.error
            except BaseException as e:  # handed back after recording the failed execution
                job.error = e
            finally:
                if job.resource is not None:
                    try:
                        job.resource.finish()
                    except BaseException as e:
                        if job.error is None:
                            job.error = e
                job.done.set()

    def _leave_clean(self) -> None:
        """A long-lived connection must never carry an open transaction into the next request."""
        for db in [self._hub, *self._companies.values()]:
            if db is None:
                continue
            try:
                if db.write_transaction:
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

    def release_company(self, company_id: str | None) -> None:
        """Fence folder changes against admitted reads, then close the writer's handle.

        None acquires the gate without selecting a company, including moves of
        empty organizations. The writer job retains this gate through cleanup;
        ordinary writes that do not release a handle remain concurrent with reads.
        """
        if threading.current_thread() is not self._writer:
            raise RuntimeError("company release must run on the host writer")
        with self._readers_lock:
            self._filesystem_exclusive = True
            self._readers_lock.notify_all()
            with performance.span("reader.drain"):
                drained = self._readers_lock.wait_for(
                    lambda: self._readers_attached == 0 and not self._transfers,
                    timeout=self.filesystem_wait_seconds)
            if not drained:
                raise BookflowError("E_DB_BUSY", message="Company readers or transfers are still active; retry the folder operation after they finish.",
                                    details={"operation": "filesystem_change"})
        if company_id is None:
            return
        db = self._companies.pop(company_id, None)
        if db is not None:
            db.close()

    @performance.measured("reader.open")
    def reader_session(self, user_id: str, login: str = "") -> Session:
        """A read-only session on the calling thread; closed by the caller through dispatch._close."""
        from bookflow.core.dispatch import _close, _open_hub, _load_actor_by_id
        self.reader_started()
        s = None
        try:
            s = Session(data_root=self.data_root, os_login=login, config=Config.load(self.data_root / "config.toml"))
            _open_hub(s, False, self._system_ctx())
            _load_actor_by_id(s, user_id)
        except BaseException:
            try:
                if s is not None:
                    _close(s)
            finally:
                self.reader_done()
            raise
        return s

    def reader_started(self) -> None:
        with self._readers_lock:
            if self._stopping:
                raise BookflowError("E_DB_BUSY", message="The host is stopping; retry after shutdown.")
            if self._filesystem_exclusive:
                raise BookflowError("E_DB_BUSY", message="Company folders are being changed; retry shortly.",
                                    details={"operation": "filesystem_change"})
            self._readers_attached += 1

    def reader_done(self) -> None:
        with self._readers_lock:
            self._readers_attached = max(0, self._readers_attached - 1)
            self._readers_lock.notify_all()

    def acquire_transfer(self, principal_id: str, company_id: str) -> TransferLease:
        """Reserve finite I/O and filesystem capacity after authorization, without a DB handle."""
        if not principal_id or not company_id:
            raise BookflowError("E_VALIDATION", message="Transfer principal and company are required.")
        with self._readers_lock:
            if self._stopping or self._jobs_closed or self._filesystem_exclusive:
                raise BookflowError("E_DB_BUSY", message="Transfers are unavailable during shutdown or folder changes.")
            count = sum(lease.principal_id == principal_id for lease in self._transfers)
            if len(self._transfers) >= self.transfer_limit or count >= self.principal_transfer_limit:
                raise BookflowError("E_DB_BUSY", message="Transfer capacity is busy; retry after a transfer finishes.")
            lease = TransferLease(principal_id, company_id, self._release_transfer,
                                  lifetime_seconds=self.transfer_lifetime_seconds)
            self._transfers.add(lease)
            return lease

    def _release_transfer(self, lease: TransferLease) -> None:
        with self._readers_lock:
            self._transfers.discard(lease)
            self._readers_lock.notify_all()

    def retry_transfer_cleanup(self) -> None:
        """Retry failed completed cleanup; never interrupt an active owner."""
        with self._readers_lock:
            leases = tuple(self._transfers)
        for lease in leases:
            if lease.cleanup_pending:
                if lease.state == "writer":
                    lease.finish()
                else:
                    lease.close()

    def run_write(self, user_id: str, login: str, fn: Callable[[Session], Any], *,
                  resource: TransferLease | None = None, timeout: float | None = None) -> Any:
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
        return self.submit(job, resource=resource, timeout=timeout)

    @performance.measured("writer.maintenance")
    def _after_write(self) -> None:
        self._last_write = time.monotonic()
        for name, db in [("hub", self._hub), *self._companies.items()]:
            if db is None:
                continue
            try:
                db.raw.execute("PRAGMA wal_checkpoint(PASSIVE)")
            except sqlite3.Error as error:
                log.warning("passive checkpoint of %s failed: %s", name, error)
            try:
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
        with self._readers_lock:
            self._stopping = True
            leases = tuple(self._transfers)
        for lease in leases:
            lease.cancel()
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

        with self._readers_lock:
            if self._stopping or self._jobs_closed:
                with self._refresh_lock:
                    self._refresh_pending.discard(token_id)
                return False
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
        return self.submit(self._checkpoint_on_writer, _maintenance=True)

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
        return self.submit(self._sweep_on_writer, _maintenance=True)

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
            if db.write_transaction:
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
