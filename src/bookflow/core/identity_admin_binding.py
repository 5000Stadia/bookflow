"""Private serialized B2/authority producer, not a registered administration API.

The actual command owner must enclose apply and its eventual commit/audit-output
projection. These scopes never commit: unresolved work is rolled back on exit.
Hosted entry is writer-only, after dequeue. Local entry obtains RootLock itself.
"""
from contextlib import contextmanager
from pathlib import Path
from threading import current_thread

from bookflow.hub import identity_admin as b, credentials, permission_runtime as runtime
from bookflow.hub.agent_authority import fail
from bookflow.storage.engine import open_database
from .config import os_login, _parse
from .locks import RootLock
from .publication import OSBinding


def _file(root):
    path = root / 'config.toml'
    try:
        return path.read_text(encoding='utf-8') if path.exists() else ''
    except (OSError, UnicodeError):
        fail('invalid_input', 'binding')


def _mapped(tx, root, captured_file, login):
    # Exact main connection/snapshot; never Config.load or a second connection.
    row = tx.raw.execute('SELECT contents FROM main.pending_config WHERE id=1').fetchone()
    try:
        data = _parse(row[0] if row is not None else captured_file, root / 'config.toml')
        table = data['users'].get(login)
        identifier = table.get('user_id') if isinstance(table, dict) else None
        if type(identifier) is not str or not identifier:
            fail('not_administrator', 'binding')
        return identifier
    except Exception:
        fail('not_administrator', 'binding')


def _os_current(tx, admitted, captured_file):
    mapped = _mapped(tx, admitted.root, captured_file, admitted.login)
    row = tx.raw.execute('SELECT kind,hub_admin,active FROM main.users WHERE id=?', (mapped,)).fetchone()
    eligible, epoch = credentials._binding(tx, mapped, admitted.on_behalf_of)
    if (mapped != admitted.user_id or row is None or not row[2] or
            (row[0], bool(row[1])) != (admitted.actor_kind, admitted.hub_admin) or
            not eligible or epoch != admitted.authority_epoch):
        fail('not_administrator', 'binding')
    return mapped


class BoundOperation:
    """Private invocation handle. No constructor is an authentication endpoint."""
    def __init__(self, tx, admitted, guard, captured_file):
        self._tx, self._admitted, self._guard, self._file = tx, admitted, guard, captured_file

    def _binding(self, purpose):
        self._guard.validate(self._tx, self._guard.request_id, purpose)
        b._require_tx(self._tx, purpose == 'apply')
        if type(self._admitted) is OSBinding:
            mapped = _os_current(self._tx, self._admitted, self._file)
            return b.OSBinding(self._guard, self._admitted.user_id, mapped)
        current = credentials.resolve_token(self._tx, self._admitted.secret)
        if (current['id'], current['user_id'], current['kind'], current['on_behalf_of']) != (
                self._admitted.token_id, self._admitted.user_id,
                self._admitted.kind, self._admitted.principal_id):
            fail('not_administrator', 'binding')
        return self._admitted

    def preview(self, intent):
        binding = self._binding('preview')
        return b.preview_edit(self._tx, binding=binding, intent=intent,
                              catalog=runtime.catalog_bundle(), visibility=runtime.VISIBILITY,
                              request_id=self._guard.request_id)

    def apply(self, intent, *, audit):
        if audit.request_id != self._guard.request_id:
            fail('invalid_input', 'binding')
        binding = self._binding('apply')
        return b.apply_edit(self._tx, binding=binding, intent=intent,
                            catalog=runtime.catalog_bundle(), visibility=runtime.VISIBILITY, audit=audit)

    def require_company(self, company, requirement):
        self._binding(self._guard.purpose)
        admitted = self._admitted
        principal = admitted.on_behalf_of if type(admitted) is OSBinding else admitted.principal_id
        return runtime.require_company(self._tx, actor=admitted.user_id, principal=principal,
                                       company=company, requirement=requirement)


@contextmanager
def _transaction(tx, root, admitted, request_id, purpose, captured_file, *, login=None, principal=None, read_started=False):
    if purpose not in ('preview', 'apply') or type(request_id) is not str or not request_id or len(request_id) > 26:
        fail('invalid_input', 'binding')
    if tx.path != root / 'hub.db' or (tx.raw.in_transaction and not (read_started and purpose == 'preview')):
        fail('invalid_input', 'transaction')
    if not tx.raw.in_transaction:
        tx.raw.execute('BEGIN IMMEDIATE' if purpose == 'apply' else 'BEGIN')
    try:
        b._require_tx(tx, purpose == 'apply')
        if admitted is None:
            # Only offline_operation supplies process-derived login here, while
            # owning RootLock continuously. Identity is captured on this snapshot.
            who = _mapped(tx, root, captured_file, login)
            row = tx.raw.execute('SELECT kind,hub_admin,active FROM main.users WHERE id=?', (who,)).fetchone()
            eligible, epoch = credentials._binding(tx, who, principal)
            if row is None or not row[2] or not eligible:
                fail('not_administrator', 'binding')
            admitted = OSBinding(root, who, login, row[0], bool(row[1]), principal, epoch)
        elif type(admitted) is OSBinding:
            if admitted.root != root:
                fail('invalid_input', 'binding')
        elif type(admitted) is b.TokenBinding:
            if admitted.root != tx.path or admitted.request_id != request_id:
                fail('invalid_input', 'binding')
        else:
            fail('invalid_input', 'binding')
        with b.OSOperation(tx, request_id=request_id, purpose=purpose) as guard:
            operation = BoundOperation(tx, admitted, guard, captured_file)
            operation._binding(purpose)
            yield operation
    finally:
        # No implicit commit, and caller errors cannot leak partially applied B2.
        if tx.raw.in_transaction:
            tx.raw.execute('ROLLBACK')


@contextmanager
def hosted_operation(host, admitted, *, request_id, purpose):
    """Called inside the actual host writer job, never from a queued JSON body.

    admitted is the existing peer-derived OSBinding or a secret-bearing internal
    TokenBinding. No bare login, Actor, Session or verified=True is accepted.
    The existing writer's enclosing actual owner remains responsible for commit.
    """
    if (current_thread() is not host._writer or host._lock is None or
            host._lock._fh is None or host._hub is None):
        fail('invalid_input', 'binding')
    captured = _file(host.data_root)
    with _transaction(host._hub, host.data_root, admitted, request_id, purpose, captured) as operation:
        yield operation


@contextmanager
def offline_operation(root: Path, *, request_id, purpose, principal=None):
    """Actual process OS identity under one RootLock through operation exit.

    This private entry does not forward or expose an editor. The later CLI/Python
    command owner must choose forwarding before entering the offline root scope.
    """
    from .fs import check_local
    root = Path(root).resolve()
    check_local(root)
    with RootLock(root, 'permission private operation'):
        captured, login = _file(root), os_login()
        with open_database(root / 'hub.db', writable=purpose == 'apply') as tx:
            with _transaction(tx, root, None, request_id, purpose, captured,
                              login=login, principal=principal, read_started=purpose == 'preview') as operation:
                yield operation


# Concurrent projection readers are distinct from B2 writer invocations. They
# cannot prepare/apply administration or confer authority after their scope exits.
from dataclasses import dataclass
import sqlite3
from uuid import uuid4
from .publication_admission import AdmissionCancelled
from .config import Config
from .session import Session


@dataclass(frozen=True, slots=True)
class ReaderIdentity:
    root: Path
    credential_kind: str
    actor: str
    actor_kind: str
    hub_admin: bool
    principal: str | None
    epoch: int | None
    token_id: str | None
    os_login: str | None


@dataclass(frozen=True, slots=True)
class AuthenticatedObservation:
    identity: ReaderIdentity
    observation: runtime.s.ObservedPair
    _reader: "BoundReader"


_READER_SEAL = object()


class BoundReader:
    def __init__(self, session, admitted, captured_file, gate=None, generation=None, *, _seal=None):
        if _seal is not _READER_SEAL:
            fail('invalid_input', 'reader')
        self._session = session
        self._tx = session.hub
        self._admitted = admitted
        self._file = captured_file
        self._gate, self._generation = gate, generation
        self._thread = current_thread()
        self._active = True
        self._savepoint = 'bookflow_reader_' + uuid4().hex
        self._identity = None
        self._observation = None
        self._tx.raw.execute('SAVEPOINT ' + self._savepoint)

    @property
    def session(self):
        self._lifetime()
        return self._session

    def _lifetime(self):
        if (not self._active or current_thread() is not self._thread or
                self._session.hub is not self._tx or self._tx._closed or
                self._tx.writable or self._tx.path != self._session.data_root / 'hub.db'):
            fail('invalid_input', 'reader')
        if not self._tx.raw.in_transaction:
            self._active = False
            fail('invalid_input', 'reader')
        try:
            self._tx.raw.execute('RELEASE ' + self._savepoint)
            self._tx.raw.execute('SAVEPOINT ' + self._savepoint)
        except sqlite3.Error:
            self._active = False
            fail('invalid_input', 'reader')
        if self._gate is not None:
            self._gate.check_generation(self._generation)

    def authenticate(self):
        self._lifetime()
        admitted = self._admitted
        if type(admitted) is OSBinding:
            actor = _os_current(self._tx, admitted, self._file)
            kind, admin, principal, epoch, token = (admitted.actor_kind,
                admitted.hub_admin, admitted.on_behalf_of, admitted.authority_epoch, None)
            credential_kind = 'os'
        else:
            row = credentials.resolve_token(self._tx, admitted.secret)
            if (row['id'], row['user_id'], row['kind'], row['on_behalf_of']) != (
                    admitted.token_id, admitted.user_id, admitted.kind, admitted.principal_id):
                fail('not_administrator', 'binding')
            actor = row['user_id']
            user = self._tx.raw.execute('SELECT kind,hub_admin,active FROM main.users WHERE id=?', (actor,)).fetchone()
            if user is None or not user[2]:
                fail('not_administrator', 'binding')
            kind, admin = user[0], bool(user[1])
            principal, epoch, token = row['on_behalf_of'], row['authority_epoch'], row['id']
            credential_kind = row['kind']
        identity = ReaderIdentity(self._session.data_root, credential_kind, actor,
                                  kind, admin, principal, epoch, token, admitted.login if type(admitted) is OSBinding else None)
        if self._identity is not None and identity != self._identity:
            fail('not_administrator', 'binding')
        self._identity = identity
        if self._session.actor is not None and (
                self._session.actor.id, self._session.actor.kind, self._session.actor.hub_admin) != (actor, kind, admin):
            fail('not_administrator', 'binding')
        return identity

    def observe(self):
        identity = self.authenticate()
        if self._observation is None:
            self._observation = runtime.observe_current(self._tx)
        self.authenticate()
        return AuthenticatedObservation(identity, self._observation, self)

    def close(self):
        self._active = False


def _reader_session(root, login, captured, ctx):
    from .dispatch import _open_hub, _close
    session = Session(data_root=root, os_login=login,
                      config=Config(root / 'config.toml'))
    try:
        _open_hub(session, False, ctx)
        pending = session.hub.raw.execute('SELECT contents FROM main.pending_config WHERE id=1').fetchone()
        if pending is None:
            # Config.flush is nonreducing: it can replace the copy and clear its
            # intent without advancing admission generation. A no-pending hub
            # snapshot may use the earlier file only if that copy still agrees.
            # Every authority-changing intent is separately conservative; the
            # generation check also rejects an intervening A/B/A intent change.
            path = root / 'config.toml'
            try:
                current = path.read_text(encoding='utf-8') if path.exists() else ''
            except (OSError, UnicodeError):
                fail('invalid_input', 'binding')
            if current != captured:
                raise AdmissionCancelled()
        session.config.data = _parse(pending[0] if pending is not None else captured, root / 'config.toml')
        return session
    except BaseException:
        _close(session)
        raise


@contextmanager
def hosted_reader(host, admitted, *, request_id):
    """At most three fresh construction attempts; never retry yielded execution."""
    from .dispatch import _close, _load_actor_by_id
    if type(request_id) is not str or not request_id or len(request_id) > 26:
        fail('invalid_input', 'reader')
    from .host import Host
    if (type(host) is not Host or not host._writer.is_alive() or
            host._lock is None or host._lock._fh is None):
        fail('invalid_input', 'reader')
    root = host.data_root
    if type(admitted) is OSBinding:
        valid = admitted.root == root
    elif type(admitted) is b.TokenBinding:
        valid = admitted.root == root / 'hub.db' and admitted.request_id == request_id
    else:
        valid = False
    if not valid:
        fail('invalid_input', 'binding')
    reader = session = None
    for attempt in range(3):
        host.reader_started()
        try:
            generation = host.publication_admission.begin_validation()
            captured = _file(root)
            session = _reader_session(root, admitted.login if type(admitted) is OSBinding else '', captured, host._system_ctx())
            reader = BoundReader(session, admitted, captured, host.publication_admission, generation, _seal=_READER_SEAL)
            identity = reader.authenticate()
            _load_actor_by_id(session, identity.actor)
            reader.authenticate()
        except BaseException as exc:
            if reader is not None:
                reader.close()
            try:
                if session is not None:
                    _close(session)
            finally:
                host.reader_done()
            reader = session = None
            if isinstance(exc, AdmissionCancelled) and attempt < 2:
                continue
            raise
        break
    try:
        yield reader
    finally:
        reader.close()
        try:
            _close(session)
        finally:
            host.reader_done()


@contextmanager
def offline_reader(root, *, request_id, principal=None):
    """Process identity under RootLock; no hosted serialization fallback."""
    from .dispatch import _close
    from .context import Context, Interface
    from .fs import check_local
    if type(request_id) is not str or not request_id or len(request_id) > 26:
        fail('invalid_input', 'reader')
    root = Path(root).resolve()
    check_local(root)
    with RootLock(root, 'permission private reader'):
        captured, login = _file(root), os_login()
        session = _reader_session(root, login, captured, Context.new(Interface.python, 'private reader'))
        reader = None
        try:
            actor = _mapped(session.hub, root, captured, login)
            user = session.hub.raw.execute('SELECT kind,hub_admin,active FROM main.users WHERE id=?', (actor,)).fetchone()
            eligible, epoch = credentials._binding(session.hub, actor, principal)
            if user is None or not user[2] or not eligible:
                fail('not_administrator', 'binding')
            admitted = OSBinding(root, actor, login, user[0], bool(user[1]), principal, epoch)
            reader = BoundReader(session, admitted, captured, _seal=_READER_SEAL)
            reader.authenticate()
            from .dispatch import _load_actor_by_id
            _load_actor_by_id(session, actor)
            reader.authenticate()
            yield reader
        finally:
            if reader is not None:
                reader.close()
            _close(session)
