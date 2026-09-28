"""Thread-owned hub snapshots for a finite group of authenticated read commands.

Sessions and decisions are never shared. Publication opens a fresh group. The
native handles live only inside this synchronous scope. Active command owners
retain their reader pins; an idle hub-only snapshot never pins company folders.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from threading import current_thread
import time

from bookflow.core.errors import BookflowError
from bookflow.core.publication_admission import AdmissionCancelled
from bookflow.storage.engine import open_database

_current = ContextVar('bookflow_permission_read_package', default=None)

# An execution snapshot that races a commit is retaken on a fresh generation.
# Nothing has run on it yet, so the retry is invisible; the bound turns a commit
# that does not settle into a typed, retryable answer instead of a hang.
SNAPSHOT_WAIT_SECONDS = 5.0
SNAPSHOT_ATTEMPTS = 8


def authority_busy():
    """Typed, retryable: authority kept changing before this request could read it."""
    return BookflowError('E_DB_BUSY', message='Permissions or records were changing while this '
                         'request started; nothing was read or changed. Retry the request.',
                         details={'operation': 'authority_change'})


class _Package:
    def __init__(self, host, *, publication=False):
        self.host = host
        # A publication phase keeps AdmissionCancelled: its release loop owns
        # the asynchronous, disconnect-aware wait for admission to reopen.
        self.publication = publication
        self.thread = current_thread()
        self.root = host.data_root
        self.db = self.manager = self.generation = None
        self.borrowers = 0
        self.active = True

    def owned(self, host=None):
        return (self.active and current_thread() is self.thread
                and (host is None or host is self.host)
                and self.host.data_root == self.root)

    def discard(self):
        if not self.active or current_thread() is not self.thread:
            raise RuntimeError('Permission read package ownership changed')
        if self.borrowers:
            raise BookflowError('E_DB_BUSY', details={'operation': 'permission_read_package'})
        manager, self.manager = self.manager, None
        self.db = self.generation = None
        if manager is not None:
            try:
                manager.__exit__(None, None, None)
            finally:
                self.host.permission_snapshot_done()

    def database(self):
        if getattr(self.host, "_stopping", False):
            raise BookflowError("E_DB_BUSY", message="The host is stopping.")
        if self.db is not None:
            try:
                self.host.publication_admission.check_generation(self.generation)
            except AdmissionCancelled:
                self.discard()
        if self.db is None:
            if self.publication:
                self._snapshot()
            else:
                self._execution_snapshot()
        return self.db

    def _execution_snapshot(self):
        """Retake a snapshot that raced a commit; never surface the race untyped.

        A conservative commit (any write, a session issue, a company open) closes
        admission and starts a new generation. A snapshot opened across it is
        discarded before any command reads it, so taking a fresh one is the same
        request, not a repeat of one.
        """
        admission = self.host.publication_admission
        deadline = time.monotonic() + SNAPSHOT_WAIT_SECONDS
        for _attempt in range(SNAPSHOT_ATTEMPTS):
            try:
                self._snapshot()
                return
            except AdmissionCancelled:
                if getattr(self.host, "_stopping", False):
                    raise BookflowError("E_DB_BUSY", message="The host is stopping.") from None
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not admission.wait_open_blocking(remaining):
                    break
        raise authority_busy()

    def _snapshot(self):
        self.host.permission_snapshot_started()
        manager = open_database(self.root / 'hub.db', False)
        entered = False
        try:
            generation = self.host.publication_admission.begin_validation()
            db = manager.__enter__()
            entered = True
            self.host.publication_admission.check_generation(generation)
        except BaseException:
            try:
                if entered:
                    manager.__exit__(None, None, None)
            finally:
                self.host.permission_snapshot_done()
            raise
        self.db, self.manager, self.generation = db, manager, generation


@contextmanager
def read_package(host, *, fresh=False):
    """A fresh publication phase must pass fresh=True, even inside execution."""
    parent = _current.get()
    if not fresh and parent is not None and parent.owned(host):
        yield parent
        return
    package = _Package(host, publication=fresh)
    token = _current.set(package)
    try:
        yield package
    finally:
        try:
            package.discard()
        finally:
            package.active = False
            _current.reset(token)


@contextmanager
def open_read_hub(path):
    package = _current.get()
    if package is not None and package.owned() and package.borrowers and package.generation is not None:
        try:
            package.host.publication_admission.check_generation(package.generation)
        except AdmissionCancelled:
            raise BookflowError('E_DB_BUSY', details={'operation': 'permission_read_package'}) from None
    if (package is None or not package.owned() or path != package.root / 'hub.db'
            or package.borrowers):
        # Nested readers may own independent savepoints. Do not let an older
        # reader's RELEASE consume a newer reader's savepoint on one connection.
        with open_database(path, False) as db:
            yield db
        return
    db = package.database()
    package.borrowers += 1
    try:
        yield db
    finally:
        package.borrowers -= 1


def owns_observation(tx):
    package = _current.get()
    return package is not None and package.owned() and package.db is tx


def before_write(host):
    """Discard prior read facts before enqueueing work that can change authority."""
    package = _current.get()
    if package is not None and package.owned(host):
        package.discard()


def read_endpoint(host):
    """Wrap a synchronous endpoint without losing its resolved FastAPI types."""
    from functools import wraps
    from inspect import iscoroutinefunction, signature

    def decorate(function):
        if iscoroutinefunction(function):
            raise TypeError('Permission package endpoint must be synchronous')
        @wraps(function)
        def wrapped(*args, **kwargs):
            with read_package(host):
                return function(*args, **kwargs)
        wrapped.__signature__ = signature(function, eval_str=True)
        return wrapped
    return decorate
