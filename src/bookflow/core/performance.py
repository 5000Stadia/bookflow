"""Finite, opt-in local timings. Events contain only fixed diagnostic labels."""

from __future__ import annotations

import contextvars
import functools
import json
import os
from pathlib import Path
import secrets
import stat
import sys
import threading
from dataclasses import dataclass
from time import perf_counter_ns


PHASES = frozenset("""cli.import_app cli.invoke cli.parser cli.render cli.prompt
command command.execute command.validate command.resolve command.plan command.apply
command.audit command.projection command.serialize command.forward command.close
writer.submit writer.queue writer.execute writer.cleanup writer.maintenance
reader.open reader.drain lock.acquire db.open db.close sql.execute sql.fetch sql.begin
sql.commit sql.rollback sql.checkpoint file.publish file.sync directory.sync file.replace""".split())
_MODES = frozenset(("offline", "forwarded", "hosted", "maintenance"))
_DATABASES = frozenset(("hub", "company", "other"))
_LOCAL = frozenset("ext2 ext3 ext4 xfs btrfs f2fs zfs tmpfs overlay".split())
_lock = threading.RLock()
_active = None
_roots: set[Path] = set()
_roots_failed = False
_warned = False
_context = contextvars.ContextVar("bookflow_performance", default=None)


def _warn():
    global _warned
    if _warned:
        return
    _warned = True
    try:
        sys.stderr.write("Bookflow performance capture unavailable.\n")
    except BaseException:
        pass


@dataclass(frozen=True, slots=True)
class DiagnosticContext:
    recorder: Recorder
    operation: int
    span: int
    depth: int


@dataclass(slots=True)
class _Ticket:
    recorder: Recorder
    context: DiagnosticContext
    phase: str
    start: int
    tid: int
    parent: int | None
    attrs: dict


class _Nothing:
    def __enter__(self):
        return None

    def __exit__(self, *_):
        return False


_nothing = _Nothing()


def enabled() -> bool:
    return _active is not None


def _directory(path: Path) -> tuple[Path, int]:
    # Linux has both a pinned directory API and an inspectable mount table.
    # Other platforms fail closed until equivalent verification is implemented.
    if not sys.platform.startswith("linux"):
        raise ValueError
    absolute = Path(os.path.abspath(path))
    for part in (absolute, *absolute.parents):
        if part.is_symlink():
            raise ValueError
    fd = os.open(absolute, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
            raise ValueError
        actual = Path(os.readlink(f"/proc/self/fd/{fd}"))
        if actual != absolute:
            raise ValueError
        selected = None
        length = -1
        with open("/proc/self/mountinfo", encoding="utf-8") as mounts:
            for line in mounts:
                fields = line.split()
                mount = Path(fields[4].replace("\\040", " ").replace("\\011", "\t").replace("\\012", "\n").replace("\\134", "\\"))
                if actual.is_relative_to(mount) and len(str(mount)) > length:
                    selected = fields[fields.index("-") + 1]
                    length = len(str(mount))
        if selected not in _LOCAL:
            raise ValueError
        return actual, fd
    except BaseException:
        os.close(fd)
        raise


def protect_root(path: Path) -> None:
    """Exclude every root used by this process, including roots used before start."""
    global _roots_failed
    try:
        with _lock:
            root = Path(path).resolve()
            if root not in _roots and len(_roots) >= 128:
                _roots_failed = True
            elif not _roots_failed:
                _roots.add(root)
    except BaseException:
        _roots_failed = True


def protect_selection(option: str | None = None) -> None:
    """Observe even a rejected invocation's root, without changing its error."""
    global _roots_failed
    try:
        raw = option or os.environ.get("BOOKFLOW_DATA_ROOT") or os.path.join(os.path.expanduser("~"), ".bookflow")
        protect_root(Path(raw).expanduser())
    except BaseException:
        _roots_failed = True


class Recorder:
    def __init__(self, directory, fd, event_limit, seconds, active_limit, depth_limit):
        self.directory = directory
        self._fd = fd
        self._origin = perf_counter_ns()
        self._deadline = self._origin + int(seconds * 1_000_000_000)
        self._event_limit = event_limit
        self._active_limit = active_limit
        self._depth_limit = depth_limit
        self._events = []
        self._pending = {}
        self._reserved = 0
        self._closed = False
        self._counters = dict(dropped=0, event_limit=0, active_limit=0, depth_limit=0,
                              deadline=0, unfinished=0, truncated=0, errors=0)

    def _begin(self, phase, command=None, mode=None, database=None):
        with _lock:
            if self._closed or self is not _active:
                return None
            if type(phase) is not str or phase not in PHASES:
                self._counters["dropped"] += 1
                return None
            parent = current_context()
            depth = parent.depth + 1 if parent else 1
            now = perf_counter_ns()
            reason = ("event_limit" if self._reserved >= self._event_limit else
                      "active_limit" if len(self._pending) >= self._active_limit else
                      "depth_limit" if depth > self._depth_limit else
                      "deadline" if now >= self._deadline else None)
            if reason:
                self._counters[reason] += 1
                self._counters["dropped"] += 1
                self._counters["truncated"] = 1
                return None
            attrs = {}
            if type(mode) is str and mode in _MODES:
                attrs["mode"] = mode
            if type(database) is str and database in _DATABASES:
                attrs["database"] = database
            registry = sys.modules.get("bookflow.core.registry")
            if type(command) is str and registry is not None and command in registry.REGISTRY:
                attrs["command"] = command
            self._reserved += 1
            ident = self._reserved
            context = DiagnosticContext(self, parent.operation if parent else ident, ident, depth)
            ticket = _Ticket(self, context, phase, now, threading.get_native_id(),
                             parent.span if parent else None, attrs)
            self._pending[ident] = ticket
            return ticket

    def _finish(self, ticket, failed):
        with _lock:
            if self._closed or self._pending.get(ticket.context.span) is not ticket:
                return
            end = max(ticket.start, perf_counter_ns())
            args = dict(ticket.attrs, operation_id=ticket.context.operation,
                        span_id=ticket.context.span, success=not failed)
            if ticket.parent is not None:
                args["parent_id"] = ticket.parent
            # Queue waits can outlive a timed-out caller and overlap its next call.
            # A dedicated virtual lane per wait avoids inventing a synchronous stack.
            tid = ticket.tid
            if ticket.phase == "writer.queue":
                args["submitting_thread_id"] = tid
                args["virtual_lane"] = 1
                # Positive uint32 track ids are interoperable with timeline viewers;
                # this range is above Linux's native process/thread-id range.
                tid = (1 << 30) + ticket.context.span
            self._events.append(dict(name=ticket.phase, ph="X", ts=(ticket.start-self._origin)/1000,
                                     dur=(end-ticket.start)/1000, pid=os.getpid(), tid=tid,
                                     args=args))
            del self._pending[ticket.context.span]

    def snapshot(self):
        """Return detached Chrome events and bounded numeric coverage counters."""
        with _lock:
            return {"traceEvents": [dict(e, args=dict(e["args"])) for e in self._events],
                    "metadata": dict(self._counters, reserved=self._reserved,
                                     active=len(self._pending), completed=len(self._events))}

    def close(self) -> Path | None:
        global _active
        with _lock:
            if self is not _active or self._closed:
                return None
            _active = None
            self._closed = True
            self._counters["unfinished"] = len(self._pending)
            if self._pending:
                self._counters["truncated"] = 1
            self._pending.clear()
            filename = None
            created = False
            try:
                actual = Path(os.readlink(f"/proc/self/fd/{self._fd}"))
                info = os.fstat(self._fd)
                if (actual != self.directory or info.st_uid != os.getuid()
                        or stat.S_IMODE(info.st_mode) & 0o077 or _roots_failed
                        or any(actual.is_relative_to(root) for root in _roots)):
                    raise ValueError
                # Revalidate current mount/path identity before writing via the pinned fd.
                _, check_fd = _directory(self.directory)
                try:
                    check = os.fstat(check_fd)
                    if (check.st_dev, check.st_ino) != (info.st_dev, info.st_ino):
                        raise ValueError
                finally:
                    os.close(check_fd)
                payload = json.dumps(self.snapshot(), separators=(",", ":")).encode("utf-8")
                filename = "bookflow-trace-" + secrets.token_hex(16) + ".json"
                fd = os.open(filename, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600, dir_fd=self._fd)
                created = True
                with os.fdopen(fd, "wb") as output:
                    output.write(payload)
                return self.directory / filename
            except BaseException:
                if created:
                    try:
                        os.unlink(filename, dir_fd=self._fd)
                    except BaseException:
                        pass
                _warn()
                return None
            finally:
                try:
                    os.close(self._fd)
                except BaseException:
                    pass


def start(directory: Path, *, event_limit=10000, seconds=60, active_limit=256,
          depth_limit=32) -> Recorder | None:
    global _active, _warned
    fd = None
    try:
        with _lock:
            if _active is not None:
                return None
            _warned = False
            if not (type(event_limit) is int and 1 <= event_limit <= 10000
                    and type(active_limit) is int and 1 <= active_limit <= 256
                    and type(depth_limit) is int and 1 <= depth_limit <= 32
                    and type(seconds) in (int, float) and 0 < seconds <= 60):
                raise ValueError
            path, fd = _directory(Path(directory))
            if _roots_failed or any(path.is_relative_to(root) for root in _roots):
                raise ValueError
            _active = Recorder(path, fd, event_limit, seconds, active_limit, depth_limit)
            return _active
    except BaseException:
        if fd is not None:
            try:
                os.close(fd)
            except BaseException:
                pass
        _warn()
        return None


def configure_from_env() -> Recorder | None:
    value = os.environ.get("BOOKFLOW_TRACE_DIR")
    if not value:
        return None
    protect_selection()
    return start(Path(value))


def close() -> Path | None:
    recorder = _active
    return recorder.close() if recorder is not None else None


def current_context() -> DiagnosticContext | None:
    if _active is None:
        return None
    value = _context.get()
    return value if type(value) is DiagnosticContext and value.recorder is _active else None


class _Binding:
    def __init__(self, value):
        self.value = value
        self.reset = None

    def __enter__(self):
        try:
            value = self.value
            self.reset = _context.set(value if type(value) is DiagnosticContext
                                      and value.recorder is _active else None)
        except BaseException:
            _warn()

    def __exit__(self, *_):
        try:
            if self.reset is not None:
                _context.reset(self.reset)
        except BaseException:
            _warn()
        return False


def bind(token):
    return _Binding(token)


def begin_wait(phase):
    recorder = _active
    if recorder is None:
        return None
    try:
        return recorder._begin(phase)
    except BaseException:
        _warn()
        return None


def finish_wait(ticket, failed=False):
    if ticket is None:
        return
    try:
        ticket.recorder._finish(ticket, bool(failed))
    except BaseException:
        try:
            with _lock:
                if not ticket.recorder._closed:
                    ticket.recorder._counters["errors"] += 1
        except BaseException:
            pass
        _warn()


class _Span:
    def __init__(self, ticket):
        self.ticket = ticket
        self.binding = None

    def __enter__(self):
        try:
            if self.ticket is not None:
                self.binding = bind(self.ticket.context)
                self.binding.__enter__()
        except BaseException:
            _warn()
        return None

    def __exit__(self, kind, value, traceback):
        try:
            finish_wait(self.ticket, failed=kind is not None)
        finally:
            if self.binding is not None:
                self.binding.__exit__(kind, value, traceback)
        return False


def span(phase: str, *, command=None, mode=None, database=None):
    recorder = _active
    if recorder is None:
        return _nothing
    try:
        return _Span(recorder._begin(phase, command, mode, database))
    except BaseException:
        _warn()
        return _nothing


def measured(phase):
    """Decorate a synchronous function without retaining its arguments/results."""
    def decorate(function):
        @functools.wraps(function)
        def wrapped(*args, **kwargs):
            if _active is None:
                return function(*args, **kwargs)
            with span(phase):
                return function(*args, **kwargs)
        return wrapped
    return decorate
