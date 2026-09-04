"""The host process: lock holder, one writer thread, per-request readers (row 3 plan, The host process)."""

from __future__ import annotations

import json
import logging
import os
import queue
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from bookflow.core.config import Config
from bookflow.core.context import ActorKind, Context
from bookflow.core.errors import BookflowError
from bookflow.core.fs import check_local
from bookflow.core.locks import RootLock
from bookflow.core.perms import private_umask
from bookflow.core.session import Actor, Session
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

    def __init__(self, data_root: Path, *, version: str):
        self.data_root = data_root
        self.version = version
        self._lock: RootLock | None = None
        self._queue: "queue.Queue[_Job | None]" = queue.Queue()
        self._writer = threading.Thread(target=self._writer_loop, name="bookflow-writer", daemon=True)
        self._hub: Database | None = None  # writer's writable hub
        self._companies: dict[str, Database] = {}  # writer's writable company connections by id
        self._signals: dict[str, threading.Condition] = {}
        self._seq: dict[str, int] = {}
        self._readers_attached = 0
        self._readers_lock = threading.Lock()
        self._stopping = False
        self._umask_old: int | None = None

    # ---------------------------------------------------------------- lifecycle
    def start(self) -> None:
        check_local(self.data_root)
        self._umask_old = os.umask(0o077)
        self._lock = RootLock(self.data_root, "serve")
        self._lock.__enter__()
        self._writer.start()
        self.submit(self._open_hub_on_writer)

    def stop(self) -> None:
        self._stopping = True
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
        job.done.wait(timeout)
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
        try:
            db.close()
        except Exception:  # noqa: BLE001
            pass
        if db is self._hub:
            self._hub = None

    def _open_hub_on_writer(self) -> None:
        from bookflow.core.dispatch import _open_hub
        s = self._writer_session()
        _open_hub(s, True, self._system_ctx())
        self._hub = s.hub
        s._hub_cm = None  # type: ignore[attr-defined]

    def _shutdown_on_writer(self) -> None:
        from bookflow.company import schema as c
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
            self._hub.close()
            self._hub = None

    # ---------------------------------------------------------------- sessions
    def _system_ctx(self) -> Context:
        from bookflow.core.context import Interface
        return Context.new(Interface.system, "bookflow-host")

    def _writer_session(self) -> Session:
        s = Session(data_root=self.data_root, os_login="", config=Config.load(self.data_root / "config.toml"))
        s.hub = self._hub
        s.company_opener = self._company_for_writer
        s.company_releaser = lambda cid: None
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
                result = fn(s)
            finally:
                s.close_company()
            self._after_write()
            return result
        return self.submit(job)

    def _after_write(self) -> None:
        for name, db in [("hub", self._hub), *self._companies.items()]:
            if db is None:
                continue
            try:
                db.raw.execute("PRAGMA wal_checkpoint(PASSIVE)")
                row = db.raw.execute("SELECT max(seq) FROM audit_events").fetchone()
            except sqlite3.Error:
                continue
            seq = row[0] or 0
            if seq != self._seq.get(name):
                self._seq[name] = seq
                cond = self._signals.setdefault(name, threading.Condition())
                with cond:
                    cond.notify_all()

    def wait_for_commit(self, key: str, timeout: float) -> None:
        cond = self._signals.setdefault(key, threading.Condition())
        with cond:
            cond.wait(timeout)

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
