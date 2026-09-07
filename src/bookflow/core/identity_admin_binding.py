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
