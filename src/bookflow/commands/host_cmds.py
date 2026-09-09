"""The host process and the identities it serves: `serve`, `user add`, `user set-password`,
`membership grant/revoke`, and `token issue/list/revoke`.

Nothing here imports FastAPI, uvicorn, or the workbench at module scope: the CLI loads this module for
`bookflow token --help` and `bookflow serve --help`, and cold start must not grow (row 3 plan, Edges).
"""

from __future__ import annotations

import errno
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from bookflow.commands.common import CommonOut, WriteOutput, common_out
from bookflow.core.context import Context, client_version
from bookflow.core.errors import BookflowError
from bookflow.core.ids import is_ulid, new_id, normalize_ulid
from bookflow.core.lazy import lazy
from bookflow.core.models import ListOutput
from bookflow.core.registry import Applied, Plan, Touched, command
from bookflow.core.session import Session, localize, now_iso

sa = lazy("sqlalchemy")
h = lazy("bookflow.hub.schema")
users = lazy("bookflow.hub.users")
audit = lazy("bookflow.hub.audit")
auth = lazy("bookflow.adapters.http.auth")
access = lazy("bookflow.hub.access")

log = logging.getLogger("bookflow.host")

VIA = lambda ctx: ctx.interface.value  # noqa: E731

DEFAULT_BIND = "127.0.0.1:8765"


# ---------------------------------------------------------------- shared lookups

def _find_user(s: Session, selector: str) -> dict[str, Any] | None:
    """A user by id or username; inactive users are not found."""
    row = None
    if is_ulid(selector):
        row = users.find_user(s, id=normalize_ulid(selector))
    if row is None:
        row = users.find_user(s, username=selector)
    return row if row and row["active"] else None


def _is_self(s: Session, selector: str) -> bool:
    a = s.actor
    if not a or not (users.username_key(selector) == users.username_key(a.username)
                     or (is_ulid(selector) and normalize_ulid(selector) == a.id)):
        return False
    row = _find_user(s, selector)
    return bool(row and row["id"] == a.id)


def _actor_row(s: Session) -> dict[str, Any]:
    row = users.find_user(s, id=s.actor.id)
    if row is None:  # pragma: no cover - the session could not have been built
        raise BookflowError("E_USER_NOT_FOUND", details={"user": s.actor.id})
    return row


# ---------------------------------------------------------------- serve

class ServeInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    bind: str = Field(DEFAULT_BIND, description="Address to listen on, host:port", max_length=128)
    allow_network: bool = Field(False, description="Allow a bind address outside loopback")
    secure_cookies: bool | None = Field(None, description="Set Secure on the session cookie; defaults on for non-loopback binds")


class ServeOutput(BaseModel):
    bind: str
    socket: str | None
    pid: int
    secure_cookies: bool
    companies_migrated: list[str]
    companies_failed: list[dict[str, str]]
    warnings: list[str] = Field(default_factory=list)


def _plan_serve(inp: ServeInput, ctx: Context, s: Session) -> Plan:  # never called; bootstrap has its own path
    raise NotImplementedError


serve_cmd = command("serve", scope="hub",
                    description="Serve every routed command over HTTP and the loopback socket, holding the data-root lock until interrupted.",
                    input_model=ServeInput, output_model=ServeOutput,
                    bootstrap=True, local_only=True,
                    error_codes=["E_NETWORK_NOT_ALLOWED", "E_VERSION_MISMATCH", "E_COMPANY_MISSING"],
                    authorization="human hub administrator")(_plan_serve)


def parse_bind(bind: str) -> tuple[str, int]:
    """`host:port`, with `[::1]:8765` for a bracketed IPv6 literal."""
    text = (bind or "").strip()
    host, port = "", ""
    if text.startswith("["):
        host, sep, rest = text[1:].partition("]")
        port = rest[1:] if rest.startswith(":") else ""
        if not sep:
            host = ""
    elif ":" in text:
        host, _, port = text.rpartition(":")
    bad = None
    if not host or not port:
        bad = "give an address as host:port"
    else:
        try:
            number = int(port)
        except ValueError:
            bad = "the port is not a number"
        else:
            if not 1 <= number <= 65535:
                bad = "the port is outside 1-65535"
    if bad:
        raise BookflowError("E_VALIDATION", details={"fields": [{"field": "bind", "problem": bad}]})
    return host, int(port)


def is_loopback(host: str) -> bool:
    import ipaddress
    if host in ("localhost", "localhost.localdomain"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def cookie_security(host: str, explicit: bool | None) -> bool:
    """Resolve the tri-state cookie option without losing an explicit false."""
    return not is_loopback(host) if explicit is None else explicit


def _serve_actor(s: Session) -> dict[str, Any]:
    """The OS login's hub-admin user, read before the lock is taken. Never migrates."""
    from bookflow.core.config import Config
    from bookflow.storage.engine import open_database
    root = s.data_root
    if not (root / "hub.db").exists():
        raise BookflowError("E_NOT_INITIALIZED", details={"data_root": str(root)})
    s.config = Config.load(root / "config.toml")
    table = s.config.user_table(s.os_login)
    if not table or not isinstance(table.get("user_id"), str):
        raise BookflowError("E_NO_ACTOR")
    with open_database(root / "hub.db", writable=False) as db:
        row = db.raw.execute("SELECT id, kind, username, display_name, hub_admin, active FROM users WHERE id = ?", (table["user_id"],)).fetchone()
    if row is None or not row[5]:
        raise BookflowError("E_NO_ACTOR", details={"mapped_user_id": table["user_id"]})
    if row[1] != "human":
        raise BookflowError("E_PERMISSION", details={"capability": "serve", "required_role": "human"},
                            message="Only a human hub admin can run the host.")
    if not row[4]:
        raise BookflowError("E_PERMISSION", details={"capability": "serve", "required_role": "hub_admin"},
                            message="Only a hub admin can run the host.")
    return {"id": row[0], "kind": row[1], "username": row[2], "display_name": row[3], "hub_admin": bool(row[4])}


def run_serve(cmd, inp: ServeInput, ctx: Context, s: Session) -> dict[str, Any]:
    """The bootstrap path for `serve` (dispatch._run_bootstrap): no lock path, no plan/apply."""
    host_name, port = parse_bind(inp.bind)
    if not inp.allow_network and not is_loopback(host_name):
        raise BookflowError("E_NETWORK_NOT_ALLOWED", details={"bind": inp.bind})
    _serve_actor(s)
    secure = cookie_security(host_name, inp.secure_cookies)
    import socket as _socket
    import uvicorn
    handle = None
    tcp = None
    try:
        # The root lock decides whether another host owns this data root before
        # the TCP address can obscure that fact with EADDRINUSE. Descriptor
        # publication remains delayed until both listeners are ready.
        handle = start_serving(
            s.data_root, client_version(), bind=inp.bind,
            secure_cookies=secure, publish_descriptor=False,
        )
        tcp = _socket.socket(_socket.AF_INET6 if ":" in host_name else _socket.AF_INET, _socket.SOCK_STREAM)
        tcp.setsockopt(_socket.SOL_SOCKET, _socket.SO_REUSEADDR, 1)
        try:
            tcp.bind((host_name, port))
            tcp.listen(128)
        except OSError as e:
            raise BookflowError(
                "E_IO",
                message=f"Could not listen on {inp.bind}; choose another address or stop the process already using it.",
                details={
                    "operation": "bind", "address": inp.bind,
                    "errno": errno.errorcode.get(e.errno, str(e.errno)),
                },
            )
        handle.host.write_descriptor(inp.bind, str(handle.socket))
        log.warning("bookflow host listening on %s (socket %s)", inp.bind, handle.socket)
        from bookflow.adapters.http.admission import AdmissionProtocol
        server = uvicorn.Server(uvicorn.Config(handle.app, http=AdmissionProtocol, loop="asyncio",
                                               log_level="warning", access_log=False))
        import signal
        import threading
        from contextlib import nullcontext
        previous: dict[Any, Any] = {}

        def request_shutdown(signum, frame) -> None:  # noqa: ARG001 - signal handler signature
            handle.host.begin_shutdown()
            server.should_exit = True

        if threading.current_thread() is threading.main_thread():
            # Uvicorn releases have used both mechanisms. Disable whichever is
            # present so Bookflow's handler can wake idle streams before the
            # server begins waiting for active requests to finish.
            server.install_signal_handlers = lambda: None
            server.capture_signals = lambda: nullcontext()
            for signum in (signal.SIGINT, signal.SIGTERM):
                previous[signum] = signal.getsignal(signum)
                signal.signal(signum, request_shutdown)
        try:
            server.run(sockets=[tcp])
        finally:
            for signum, handler in previous.items():
                signal.signal(signum, handler)
    finally:
        if handle is not None:
            handle.stop()
        if tcp is not None:
            try:
                tcp.close()
            except OSError:  # pragma: no cover
                pass
    out = ServeOutput(bind=inp.bind, socket=str(handle.socket), pid=os.getpid(), secure_cookies=secure,
                      companies_migrated=list(handle.companies_migrated), companies_failed=list(handle.companies_failed))
    return out.model_dump(mode="json")


# ---------------------------------------------------------------- the running host

@dataclass
class ServeHandle:
    """Everything `serve` starts, so a test can drive the app without uvicorn."""

    host: Any
    app: Any
    listener: Any
    socket: Path
    bind: str
    companies_migrated: list[str] = field(default_factory=list)
    companies_failed: list[dict[str, str]] = field(default_factory=list)
    stopped: bool = False

    def stop(self) -> None:
        if self.stopped:
            return
        try:
            self.listener.stop()
        finally:
            # A busy reader can make host shutdown retryable while it still
            # holds the root lock. Keep the descriptor and this handle usable
            # until the actual host has finished stopping.
            self.host.stop()
            self.host.remove_descriptor()
            self.stopped = True


def start_serving(data_root: Path | str, version: str, *, bind: str = DEFAULT_BIND,
                  secure_cookies: bool = False, publish_descriptor: bool = True) -> ServeHandle:
    """Take the lock, migrate everything, build the app, listen on the socket, and write host.json."""
    from bookflow.adapters.http.app import create_app
    from bookflow.adapters.http.local import LocalListener
    from bookflow.core.forward import socket_path
    from bookflow.core.host import Host
    root = Path(data_root)
    host = Host(root, version=version)
    try:
        host.start()
    except BaseException as e:
        if host._writer.is_alive():
            host.stop()
        elif host._lock is not None:  # the hub failed to open after the lock was taken
            host._lock.__exit__(None, None, None)
            host._lock = None
        if isinstance(e, BookflowError) and e.code == "E_DB_BUSY":
            held = e.details.get("command")
            raise BookflowError("E_DB_BUSY", details=e.details, message=(
                "Another Bookflow host is already serving this data root; stop it before starting another."
                if held == "serve" else
                f"`{held}` holds the data-root lock; the host takes it for its whole run, so wait for that command to finish."))
        raise
    try:
        migrated, failed = migrate_everything(host)
        app = create_app(host, secure_cookies=secure_cookies)
        sock = socket_path(root)
        listener = LocalListener(host, sock, make_local_handler(host, version))
        listener.start()
        if publish_descriptor:
            host.write_descriptor(bind, str(sock))
    except BaseException:
        host.stop()
        raise
    return ServeHandle(host=host, app=app, listener=listener, socket=sock, bind=bind,
                       companies_migrated=migrated, companies_failed=failed)


def migrate_everything(host) -> tuple[list[str], list[dict[str, str]]]:
    """Migrate the hub and every registered company to head on the writer thread, attributed to the serve user.

    A company that will not migrate is logged and left behind; the host keeps serving the others.
    """
    from bookflow.core.config import Config, os_login
    from bookflow.core.context import ActorKind, Interface
    login = os_login()
    table = Config.load(host.data_root / "config.toml").user_table(login) or {}
    if not isinstance(table.get("user_id"), str):
        raise BookflowError("E_NO_ACTOR")
    ctx = Context.new(Interface.system, "bookflow-host")

    def job(s: Session):
        with s.commits.operation("host.migrate_everything", s.hub, s.company):
            from bookflow.core.dispatch import _migrate_hub, resolve_company_folder
            from bookflow.storage.migrate import migrate_company
            c2 = ctx.model_copy(update={"actor_id": s.actor.id, "actor_kind": ActorKind(s.actor.kind)})
            _migrate_hub(s, c2)
            migrated: list[str] = []
            failed: list[dict[str, str]] = []
            rows = [dict(r) for r in s.hub.conn.execute(sa.select(h.companies)).mappings().all()]
            for row in rows:
                s.hub_touched = []
                try:
                    folder = resolve_company_folder(s, c2, dict(row), True)
                    db_path = folder / "company.db"
                    if not db_path.exists():
                        raise BookflowError("E_COMPANY_MISSING", details={"company_id": row["id"]})
                    db = host._company_for_writer(row, True, db_path)
                    before, after = migrate_company(s, c2, db, folder, row)
                    if before == after:
                        host.release_company(row["id"])
                        continue
                    s.hub.raw.execute("BEGIN IMMEDIATE")
                    s.hub.conn.execute(h.companies.update().where(h.companies.c.id == row["id"]).values(schema_revision=after))
                    audit.write_event(s, c2, "serve", f"migrated company {row['display_name']} from {before} to {after}", list(s.hub_touched))
                    s.commits.commit(s.hub, "host.migrate_everything")
                    migrated.append(row["id"])
                except BookflowError as e:
                    if s.hub.raw.in_transaction:
                        s.hub.raw.execute("ROLLBACK")
                    host.release_company(row["id"])
                    log.warning("serve: company %s was not migrated (%s); it is served as it is", row["id"], e.code)
                    failed.append({"company_id": row["id"], "code": e.code})
            return migrated, failed

    return host.run_write(table["user_id"], login, job)


def make_local_handler(host, version: str):
    """The LocalListener handler: the peer's OS login is the identity, never the envelope's."""
    from bookflow.adapters.http.local import context_from_envelope

    def handler(login: str, envelope: dict[str, Any]) -> dict[str, Any]:
        from bookflow.core import registry
        ctx = context_from_envelope(envelope)
        sent = envelope.get("version") or ctx.client_version
        if sent != version:
            raise BookflowError("E_VERSION_MISMATCH", details={"host": version, "client": sent},
                                message="The running host and this client are different Bookflow versions; stop the host or upgrade the client.")
        name = envelope.get("command")
        cmd = registry.get(name) if isinstance(name, str) else None
        if cmd is None:
            raise BookflowError("E_USAGE", message=f"unknown command {name!r}")
        if cmd.bootstrap:
            raise BookflowError("E_USAGE", message=f"`{cmd.name}` runs in the calling process; it is never forwarded to the host.")
        from bookflow.core.publication import OSBinding
        from bookflow.adapters.http.execution import run_hosted
        cred = OSBinding.capture(host, login, ctx.on_behalf_of)
        return run_hosted(host, cmd, envelope.get("input") or {}, ctx, cred,
                          envelope.get("company_selector"), envelope.get("company_source") or "option",
                          bool(envelope.get("dry_run")))

    def transfer(login, envelope, socket, *, check=None):
        from pydantic import ValidationError
        import time
        from bookflow.adapters.http.local import validate_transfer_envelope
        from bookflow.core import registry
        from bookflow.core.transfer_protocol import FramedReader, _encode, send_body, send_json
        from bookflow.core.transfer_resources import TransferLease
        from bookflow.adapters.http.published_transfer import PublishedTransfer
        from bookflow.core.publication import OSBinding

        transport = TransferLease("", "", lambda lease: None)
        outer_check = check or transport.check_io
        hosted = None
        output_started = False
        try:
            validate_transfer_envelope(envelope)
            try:
                ctx = context_from_envelope(envelope)
            except (ValidationError, TypeError, ValueError) as exc:
                raise BookflowError("E_VALIDATION", message="Invalid transfer context.") from exc
            sent = envelope.get("version") or ctx.client_version
            if sent != version:
                raise BookflowError("E_VERSION_MISMATCH", details={"host": version, "client": sent})
            cmd = registry.get(envelope["command"])
            if (cmd is None or cmd.bootstrap or cmd.transfer is None
                    or cmd.transfer.direction != envelope["transfer"]["direction"]):
                raise BookflowError("E_USAGE", message="Command does not support this transfer direction.")
            cred = OSBinding.capture(host, login, ctx.on_behalf_of)
            user_id = cred.user_id
            hosted = PublishedTransfer(host, cmd, envelope["input"], ctx, user_id, login,
                                    selector=envelope.get("company_selector"),
                                    source=envelope.get("company_source", "option"),
                                    dry_run=envelope.get("dry_run", False), credential=cred,
                                    authorize_session=lambda session: cred.revalidate(session.hub))

            socket.response.deadline = time.monotonic() + hosted.resource.lease.remaining_seconds()

            def check_io():
                outer_check()
                hosted.resource.lease.check_io()

            if cmd.transfer.direction == "input":
                socket.bind_guard(hosted.check_output, before_wait=hosted.suspend_publication, after_wait=hosted.resume_publication)
                send_json(socket, {"ready": True, "limit": hosted.prepared.limit}, check=check_io)
                hosted.receive(FramedReader(socket, hosted.prepared.limit, check=check_io))
                output = hosted.finish_input()
            else:
                info = hosted.prepared.info
                ready = dict(hosted.prepared.metadata or {})
                ready.update(ready=True, sha256=info.sha256, size_bytes=info.size_bytes)
                # Once ready starts, failure must truncate rather than masquerade as a body frame.
                _encode(ready, 65536)
                output_started = True
                socket.bind_guard(hosted.output.check, before_wait=hosted.suspend_publication, after_wait=hosted.resume_publication)
                send_json(socket, ready, check=check_io)

                def check_output():
                    outer_check()
                    hosted.check_output()

                class VerifiedReader:
                    def __init__(self):
                        import hashlib
                        self.digest = hashlib.sha256()
                        self.size = 0

                    def read(self, size):
                        chunk = hosted.reader.read(size)
                        if not isinstance(chunk, bytes):
                            raise BookflowError("E_IO", message="Invalid download body.")
                        self.digest.update(chunk)
                        self.size += len(chunk)
                        if self.size > info.size_bytes or (not chunk and (
                                self.size != info.size_bytes or self.digest.hexdigest() != info.sha256)):
                            raise BookflowError("E_IO", message="Download body changed during transfer.")
                        return chunk

                send_body(socket, VerifiedReader(), info.size_bytes, check=check_output)
                check_output()
                output = hosted.output
            hosted.close()
            socket.bind_guard(output.check)
            send_json(socket, {"output": dict(output)}, check=outer_check)
        except Exception as exc:
            if not output_started:
                error = exc if isinstance(exc, BookflowError) else BookflowError(
                    "E_IO" if isinstance(exc, (OSError, TimeoutError)) else "E_INTERNAL",
                    message="Local transfer failed.")
                try:
                    rejected = getattr(exc, "publication_document", None)
                    if rejected is not None:
                        socket.bind_guard(rejected.check)
                    send_json(socket, {"error": error.to_dict()}, check=outer_check)
                except (BookflowError, OSError):
                    pass
        finally:
            try:
                if hosted is not None:
                    hosted.close()
            finally:
                transport.close()

    handler.transfer = transfer
    return handler


def user_for_login(host, login: str) -> str:
    """config.toml maps an OS login to a user id, exactly as dispatch._load_actor does."""
    from bookflow.core.config import Config
    table = Config.load(host.data_root / "config.toml").user_table(login)
    if not isinstance(table, dict) or not isinstance(table.get("user_id"), str):
        raise BookflowError("E_UNAUTHENTICATED", details={"reason": "this login is not mapped to a Bookflow user"})
    return table["user_id"]


# ---------------------------------------------------------------- named people and their access

RoleName = Literal["readonly", "standard", "admin", "owner"]
ROLE_HELP = "Role at that scope: readonly reads, standard does the bookkeeping, admin also manages members, owner is the final say"
INITIAL_PASSWORD_SHOWN_ONCE = ("This initial password is shown once; only its hash is stored. Give it to them, "
                               "and have them change it with `user set-password`.")


def _generate_password() -> str:
    import secrets
    return secrets.token_urlsafe(12)


class MembershipOut(BaseModel):
    membership_id: str
    user_id: str
    username: str
    scope_type: str
    scope_id: str
    scope_name: str
    organization_id: str
    role: RoleName
    granted_at: str | None
    revoked_at: str | None
    changed: bool


class Scope(BaseModel):
    """One resolved membership scope: a company, or a whole organization."""
    scope_type: str
    scope_id: str
    scope_name: str
    organization_id: str


def resolve_scope(s: Session, company: str | None, organization: str | None) -> Scope:
    """Exactly one scope, resolved through the actor's own visibility, so an invisible
    company answers the same way an absent one does."""
    from bookflow.core.dispatch import resolve_company, resolve_organization
    if (company is None) == (organization is None):
        raise BookflowError("E_VALIDATION", details={"fields": [
            {"field": "company", "problem": "give exactly one of company or organization"}]})
    if company is not None:
        row = resolve_company(s, company, "option")
        return Scope(scope_type="company", scope_id=row["id"], scope_name=row["display_name"],
                     organization_id=row["organization_id"])
    row = resolve_organization(s, organization)
    return Scope(scope_type="organization", scope_id=row["id"], scope_name=row["display_name"],
                 organization_id=row["id"])


def membership_floor(role: str) -> str:
    """`admin:members:<role>:<domain>` in the frozen catalog: admin to move a membership,
    owner to hand out or take away ownership."""
    return "owner" if role == "owner" else "admin"


def authorize_membership_scope(s: Session, scope: Scope, required: str) -> None:
    """A human administrator of that same scope. A hub administrator administers every scope."""
    if s.actor.kind != "human":
        raise BookflowError("E_PERMISSION", details={"capability": "membership", "required_role": "human"})
    if s.is_hub_admin:
        return
    if scope.scope_type == "company":
        _, actor_role = access.company_role(s, scope.scope_id, scope.organization_id)
    else:
        actor_role = access.org_role(s, scope.scope_id)
    if not access.role_satisfies(actor_role, scope.scope_type, required, False):
        raise BookflowError("E_PERMISSION", details={"capability": "membership",
                                                     "required_role": required, "role": actor_role})


def _membership_row(s: Session, user_id: str, scope: Scope) -> dict[str, Any] | None:
    row = s.hub.conn.execute(sa.select(h.memberships).where(
        h.memberships.c.user_id == user_id, h.memberships.c.scope_type == scope.scope_type,
        h.memberships.c.scope_id == scope.scope_id)).mappings().first()
    return dict(row) if row else None


def _membership_out(row: dict[str, Any], user: dict[str, Any], scope: Scope, *, changed: bool) -> MembershipOut:
    return MembershipOut(membership_id=row["id"], user_id=user["id"], username=user["username"],
                         scope_type=scope.scope_type, scope_id=scope.scope_id, scope_name=scope.scope_name,
                         organization_id=scope.organization_id, role=row["role"],
                         granted_at=row["granted_at"], revoked_at=row["revoked_at"], changed=changed)


def _grant(s: Session, ctx: Context, user: dict[str, Any], scope: Scope, role: str,
           existing: dict[str, Any] | None) -> tuple[dict[str, Any], Touched | None]:
    """The one place a membership is written. `user add` and `membership grant` share it."""
    at = now_iso()
    if existing is None:
        row = {"id": new_id(), "user_id": user["id"], "scope_type": scope.scope_type, "scope_id": scope.scope_id,
               "role": role, "grants": None, "denies": None, "granted_by": s.actor.id, "granted_at": at,
               "revoked_at": None, "version": 1, "updated_at": at, "updated_by": s.actor.id, "updated_via": VIA(ctx)}
        s.hub.conn.execute(h.memberships.insert().values(**row))
        return row, Touched("membership", row["id"], "create", None, 1, row)
    if existing["role"] == role and existing["revoked_at"] is None:
        return existing, None
    row = {**existing, "role": role, "revoked_at": None, "granted_by": s.actor.id, "granted_at": at,
           "version": existing["version"] + 1, "updated_at": at, "updated_by": s.actor.id, "updated_via": VIA(ctx)}
    s.hub.conn.execute(h.memberships.update().where(h.memberships.c.id == existing["id"]).values(
        role=role, revoked_at=None, granted_by=s.actor.id, granted_at=at, version=row["version"],
        updated_at=at, updated_by=s.actor.id, updated_via=VIA(ctx)))
    return row, Touched("membership", row["id"], "update", existing["version"], row["version"], row, before=existing)


# ---------------------------------------------------------------- user add

class UserAddInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    username: str = Field(description="Login name for the new person; unique, ignoring case", min_length=1, max_length=64)
    display_name: str | None = Field(None, description="Name shown in lists and the audit trail; defaults to the username", max_length=128)
    password: str | None = Field(None, description="Their first password; omit it and Bookflow makes one and shows it once",
                                 max_length=1024, json_schema_extra={"secret": True})
    hub_admin: bool = Field(False, description="Also let them administer this installation: add users, attach companies, see every organization")
    company: str | None = Field(None, description="Company to give them access to now; name or id")
    organization: str | None = Field(None, description="Organization to give them access to now, covering all its companies; name or id")
    role: RoleName = Field("standard", description=ROLE_HELP)


class UserAddOutput(WriteOutput):
    user_id: str
    username: str
    display_name: str
    hub_admin: bool
    password: str | None
    membership: MembershipOut | None
    message: str


user_add = command("user add", scope="hub",
                   description="Add a person who can log in from their own workstation, optionally giving them a company at the same time.",
                   input_model=UserAddInput, output_model=UserAddOutput, writes={"hub"},
                   positional=["username"], required_role="hub_admin",
                   error_codes=["E_VALIDATION", "E_PERMISSION", "E_COMPANY_NOT_FOUND", "E_COMPANY_AMBIGUOUS", "E_ORGANIZATION_NOT_FOUND"],
                   authorization="human hub administrator; the grant also needs administration of that scope")


def authorize_user_add(inp: UserAddInput, ctx: Context, s: Session) -> Scope | None:
    if s.actor.kind != "human":
        raise BookflowError("E_PERMISSION", details={"capability": "user", "required_role": "human"})
    if inp.company is None and inp.organization is None:
        if inp.role != "standard":
            raise BookflowError("E_VALIDATION", details={"fields": [
                {"field": "role", "problem": "a role needs a company or an organization to apply to"}]})
        return None
    scope = resolve_scope(s, inp.company, inp.organization)
    authorize_membership_scope(s, scope, membership_floor(inp.role))
    return scope


@user_add
def plan_user_add(inp: UserAddInput, ctx: Context, s: Session) -> Plan:
    scope = authorize_user_add(inp, ctx, s)
    if users.username_key(inp.username) == "system" or users.username_matches(s.hub, inp.username):
        raise BookflowError("E_VALIDATION", details={"fields": [{"field": "username", "problem": "already in use"}]})
    display_name = inp.display_name or inp.username
    membership = None if scope is None else MembershipOut(
        membership_id="", user_id="", username=inp.username, scope_type=scope.scope_type, scope_id=scope.scope_id,
        scope_name=scope.scope_name, organization_id=scope.organization_id, role=inp.role,
        granted_at=None, revoked_at=None, changed=True)
    preview = UserAddOutput(user_id="", username=inp.username, display_name=display_name, hub_admin=inp.hub_admin,
                            password=None, membership=membership, message="A dry run adds nobody.")
    return Plan(preview=preview, data={"input": inp, "display_name": display_name, "scope": scope})


@user_add.applier
def apply_user_add(plan: Plan, ctx: Context, s: Session) -> Applied:
    inp, scope = plan.data["input"], plan.data["scope"]
    generated = None if inp.password else _generate_password()
    row = users.create_human(s, username=inp.username, display_name=plan.data["display_name"],
                             created_by=s.actor.id, via=VIA(ctx), hub_admin=inp.hub_admin,
                             password_hash=auth.hash_password(inp.password or generated))
    touched = [Touched("user", row["id"], "create", None, 1, row)]
    membership = None
    if scope is not None:
        m, m_touched = _grant(s, ctx, row, scope, inp.role, None)
        membership = _membership_out(m, row, scope, changed=True)
        if m_touched is not None:
            touched.append(m_touched)
        message = f"{row['username']} can log in and open {scope.scope_name}."
    else:
        message = (f"{row['username']} can log in but has no company yet; "
                   f"give them one with `membership grant {row['username']} --company <company>`.")
    if generated is not None:
        message = INITIAL_PASSWORD_SHOWN_ONCE + " " + message
    out = UserAddOutput(user_id=row["id"], username=row["username"], display_name=row["display_name"],
                        hub_admin=bool(row["hub_admin"]), password=generated, membership=membership, message=message)
    summary = f"added the user {row['username']}"
    if scope is not None:
        summary += f" with {inp.role} access to {scope.scope_name}"
    return Applied(out, touched, summary)


# ---------------------------------------------------------------- membership grant / revoke

class MembershipGrantInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    user: str = Field(description="Username or id of the person receiving access", max_length=64)
    company: str | None = Field(None, description="Company they may open; name or id")
    organization: str | None = Field(None, description="Organization they may open, covering all its companies; name or id")
    role: RoleName = Field("standard", description=ROLE_HELP)


class MembershipSelector(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    user: str = Field(description="Username or id of the person losing access", max_length=64)
    company: str | None = Field(None, description="Company they may no longer open; name or id")
    organization: str | None = Field(None, description="Organization they may no longer open; name or id")


class MembershipOutput(WriteOutput, MembershipOut):
    """The membership itself, flat, so the target user id is the command's own output."""
    message: str


def _membership_target(s: Session, selector: str) -> dict[str, Any]:
    row = _find_user(s, selector)
    if row is None:
        raise BookflowError("E_USER_NOT_FOUND", details={"user": selector})
    if row["kind"] == "system":
        raise BookflowError("E_VALIDATION", details={"fields": [
            {"field": "user", "problem": "the system user holds no memberships"}]})
    return row


membership_grant = command("membership grant", scope="hub",
                           description="Give a person access to a company or a whole organization, at one role.",
                           input_model=MembershipGrantInput, output_model=MembershipOutput, writes={"hub"},
                           positional=["user"],
                           error_codes=["E_USER_NOT_FOUND", "E_VALIDATION", "E_PERMISSION",
                                        "E_COMPANY_NOT_FOUND", "E_COMPANY_AMBIGUOUS", "E_ORGANIZATION_NOT_FOUND"],
                           authorization="human administrator of that company or organization, or a hub administrator; owner to grant or move an owner")


def authorize_membership_grant(inp: MembershipGrantInput, ctx: Context, s: Session):
    # The scope and the authority to administer it come first, so that whether a
    # username exists is only ever answered to someone who administers that scope.
    scope = resolve_scope(s, inp.company, inp.organization)
    authorize_membership_scope(s, scope, membership_floor(inp.role))
    user = _membership_target(s, inp.user)
    existing = _membership_row(s, user["id"], scope)
    if existing is not None and existing["revoked_at"] is None:
        # Moving an owner off ownership is an owner's act too, not only granting it.
        authorize_membership_scope(s, scope, membership_floor(existing["role"]))
    return user, scope, existing


@membership_grant
def plan_membership_grant(inp: MembershipGrantInput, ctx: Context, s: Session) -> Plan:
    user, scope, existing = authorize_membership_grant(inp, ctx, s)
    changed = existing is None or existing["role"] != inp.role or existing["revoked_at"] is not None
    preview = _membership_out({"id": existing["id"] if existing else "", "role": inp.role,
                               "granted_at": now_iso(), "revoked_at": None}, user, scope, changed=changed)
    return Plan(preview=MembershipOutput(**preview.model_dump(), message=_grant_message(user, scope, inp.role, changed)),
                data={"user": user, "scope": scope, "existing": existing, "role": inp.role})


def _grant_message(user: dict[str, Any], scope: Scope, role: str, changed: bool) -> str:
    if not changed:
        return f"{user['username']} already had {role} access to {scope.scope_name}."
    return f"{user['username']} has {role} access to {scope.scope_name}."


@membership_grant.applier
def apply_membership_grant(plan: Plan, ctx: Context, s: Session) -> Applied:
    user, scope, existing = plan.data["user"], plan.data["scope"], plan.data["existing"]
    role = plan.data["role"]
    row, touched = _grant(s, ctx, user, scope, role, existing)
    changed = touched is not None
    out = MembershipOutput(**_membership_out(row, user, scope, changed=changed).model_dump(),
                           message=_grant_message(user, scope, role, changed))
    return Applied(out, [touched] if touched else [],
                   f"gave {user['username']} {role} access to {scope.scope_name}" if changed else "already granted")


membership_revoke = command("membership revoke", scope="hub",
                            description="Take away a person's access to a company or organization; it stops on their next request.",
                            input_model=MembershipSelector, output_model=MembershipOutput, writes={"hub"},
                            positional=["user"],
                            error_codes=["E_USER_NOT_FOUND", "E_RECORD_NOT_FOUND", "E_VALIDATION", "E_PERMISSION",
                                         "E_COMPANY_NOT_FOUND", "E_COMPANY_AMBIGUOUS", "E_ORGANIZATION_NOT_FOUND"],
                            authorization="human administrator of that company or organization, or a hub administrator; owner to revoke an owner")


def authorize_membership_revoke(inp: MembershipSelector, ctx: Context, s: Session):
    scope = resolve_scope(s, inp.company, inp.organization)
    authorize_membership_scope(s, scope, "admin")
    user = _membership_target(s, inp.user)
    existing = _membership_row(s, user["id"], scope)
    if existing is None:
        raise BookflowError("E_RECORD_NOT_FOUND", details={"user": inp.user, "scope": scope.scope_type})
    authorize_membership_scope(s, scope, membership_floor(existing["role"]))
    return user, scope, existing


@membership_revoke
def plan_membership_revoke(inp: MembershipSelector, ctx: Context, s: Session) -> Plan:
    user, scope, existing = authorize_membership_revoke(inp, ctx, s)
    changed = existing["revoked_at"] is None
    preview = _membership_out({**existing, "revoked_at": existing["revoked_at"] or now_iso()}, user, scope, changed=changed)
    return Plan(preview=MembershipOutput(**preview.model_dump(), message=_revoke_message(user, scope, changed)),
                data={"user": user, "scope": scope, "existing": existing})


def _revoke_message(user: dict[str, Any], scope: Scope, changed: bool) -> str:
    if not changed:
        return f"{user['username']} already had no access to {scope.scope_name}."
    return (f"{user['username']} can no longer open {scope.scope_name}. Any session or token they hold "
            f"keeps working for whatever else they are a member of, and carries no access to this one.")


@membership_revoke.applier
def apply_membership_revoke(plan: Plan, ctx: Context, s: Session) -> Applied:
    user, scope, existing = plan.data["user"], plan.data["scope"], plan.data["existing"]
    if existing["revoked_at"] is not None:
        out = MembershipOutput(**_membership_out(existing, user, scope, changed=False).model_dump(),
                               message=_revoke_message(user, scope, False))
        return Applied(out, [], "already revoked")
    at = now_iso()
    row = {**existing, "revoked_at": at, "version": existing["version"] + 1,
           "updated_at": at, "updated_by": s.actor.id, "updated_via": VIA(ctx)}
    s.hub.conn.execute(h.memberships.update().where(h.memberships.c.id == existing["id"]).values(
        revoked_at=at, version=row["version"], updated_at=at, updated_by=s.actor.id, updated_via=VIA(ctx)))
    out = MembershipOutput(**_membership_out(row, user, scope, changed=True).model_dump(),
                           message=_revoke_message(user, scope, True))
    return Applied(out, [Touched("membership", row["id"], "update", existing["version"], row["version"], row, before=existing)],
                   f"took away {user['username']}'s access to {scope.scope_name}")


def republish_membership(inp, ctx: Context, s: Session, *, revoke: bool) -> None:
    """The publication re-check for both membership commands.

    A member acting on their own membership has, by succeeding, changed the very
    authority and the very visibility this would ask about: after handing back a
    company, they can no longer resolve it. That case is settled exactly, and from
    this request's own audit, by the permit's membership reconciliation, so asking
    again here would refuse every honest self-revocation. Anyone acting on someone
    else is re-checked in full against current state.
    """
    if _membership_target(s, inp.user)["id"] == s.actor.id:
        return
    if revoke:
        authorize_membership_revoke(inp, ctx, s)
    else:
        authorize_membership_grant(inp, ctx, s)


# ---------------------------------------------------------------- user set-password

class SetPasswordInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(description="Username of the user whose password is being set", max_length=64)
    password: str | None = Field(None, description="The new password; on a terminal the CLI asks for it twice instead",
                                 max_length=1024, json_schema_extra={"secret": True})

    @field_validator("username")
    @classmethod
    def _strip(cls, v: str) -> str:
        return v.strip()


class SetPasswordOutput(WriteOutput):
    user_id: str
    username: str
    changed: bool


user_set_password = command("user set-password", scope="hub",
                            description="Set a user's password so they can log in to the workbench.",
                            input_model=SetPasswordInput, output_model=SetPasswordOutput, writes={"hub"},
                            positional=["username"],
                            error_codes=["E_USER_NOT_FOUND", "E_VALIDATION", "E_PERMISSION"],
                            authorization="human self-service; a human hub administrator may reset another human")


def authorize_set_password(inp: SetPasswordInput, ctx: Context, s: Session):
    if s.actor.kind != "human":
        raise BookflowError("E_PERMISSION", details={"capability": "user", "required_role": "human"})
    if not s.is_hub_admin and not _is_self(s, inp.username):
        raise BookflowError("E_PERMISSION", details={"capability": "user", "required_role": "self"})
    row = _find_user(s, inp.username)
    if row is None:
        raise BookflowError("E_USER_NOT_FOUND", details={"username": inp.username})
    if row["kind"] != "human":
        raise BookflowError("E_VALIDATION", details={"fields": [{"field": "username", "problem": "only human users have passwords"}]})
    return row


@user_set_password
def plan_set_password(inp: SetPasswordInput, ctx: Context, s: Session) -> Plan:
    row = authorize_set_password(inp, ctx, s)
    if not inp.password:
        raise BookflowError("E_VALIDATION", details={"fields": [{"field": "password", "problem": "required"}]})
    return Plan(preview=SetPasswordOutput(user_id=row["id"], username=row["username"], changed=True),
                data={"row": row, "password": inp.password})


@user_set_password.applier
def apply_set_password(plan: Plan, ctx: Context, s: Session) -> Applied:
    row = plan.data["row"]
    at = now_iso()
    after = {**row, "password_hash": auth.hash_password(plan.data["password"]), "version": row["version"] + 1,
             "updated_at": at, "updated_by": s.actor.id, "updated_via": VIA(ctx)}
    s.hub.conn.execute(h.users.update().where(h.users.c.id == row["id"]).values(
        password_hash=after["password_hash"], version=after["version"], updated_at=at, updated_by=s.actor.id, updated_via=VIA(ctx)))
    touched = [Touched("user", row["id"], "update", row["version"], after["version"], after, before=row)]
    sessions = [dict(r) for r in s.hub.conn.execute(sa.select(h.api_tokens).where(
        h.api_tokens.c.user_id == row["id"], h.api_tokens.c.kind == "session",
        h.api_tokens.c.revoked_at.is_(None), h.api_tokens.c.id != ctx.session_id,
    )).mappings().all()]
    for token in sessions:
        token_after = {**token, "revoked_at": at, "version": token["version"] + 1,
                       "updated_at": at, "updated_by": s.actor.id, "updated_via": VIA(ctx)}
        s.hub.conn.execute(h.api_tokens.update().where(h.api_tokens.c.id == token["id"]).values(
            revoked_at=at, version=token_after["version"], updated_at=at, updated_by=s.actor.id, updated_via=VIA(ctx)))
        touched.append(Touched("api_token", token["id"], "update", token["version"], token_after["version"],
                               {k: v for k, v in token_after.items() if k != "token_hash"},
                               before={k: v for k, v in token.items() if k != "token_hash"}))
    return Applied(SetPasswordOutput(user_id=row["id"], username=row["username"], changed=True), touched,
                   f"set the password for {row['username']}")


# ---------------------------------------------------------------- tokens

class TokenIssueInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    user: str | None = Field(None, description="Username or id of the user the token acts as; defaults to you")
    label: str = Field(description="What the token is for; it names the client in the audit trail", min_length=1, max_length=128)
    days: int | None = Field(None, ge=1, le=3650, description="Days until it expires; omitted means it never expires")
    principal: str | None = Field(None, description="The human an agent token acts on behalf of; only for agent users")


class TokenIssueOutput(WriteOutput):
    on_behalf_of: str | None
    authority_epoch: int | None
    token_id: str
    user_id: str
    username: str
    label: str | None
    expires_at: str | None
    secret: str
    message: str


class TokenOut(CommonOut):
    token_id: str
    user_id: str
    username: str | None
    on_behalf_of: str | None
    authority_epoch: int | None
    kind: str
    label: str | None
    expires_at: str | None
    last_used_at: str | None
    revoked_at: str | None


class TokenListInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    user: str | None = Field(None, description="Username or id whose tokens to list; hub admins only, for anyone but themselves")
    include_revoked: bool = Field(False, description="Also list revoked and expired tokens")


class TokenSelector(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    token: str = Field(description="Token id, as `token list` reports it")


class TokenRevokeOutput(WriteOutput):
    token_id: str
    user_id: str
    username: str | None
    label: str | None
    revoked_at: str | None
    changed: bool


SHOWN_ONCE = "This secret is shown once; only its hash is stored. Copy it now."


def _target_user(s: Session, selector: str | None) -> dict[str, Any]:
    """The user a token command acts on. A non-admin naming anyone else is E_PERMISSION, existing or not."""
    if selector is None:
        return _actor_row(s)
    if _is_self(s, selector):
        return _actor_row(s)
    if not s.is_hub_admin:
        raise BookflowError("E_PERMISSION", details={"capability": "token", "required_role": "hub_admin"})
    row = _find_user(s, selector)
    if row is None:
        raise BookflowError("E_USER_NOT_FOUND", details={"user": selector})
    return row


token_issue = command("token issue", scope="hub",
                      description="Issue a bearer token; the secret is shown once. Agents require an active assigned human principal and unsuspended authority. One agent identity per principal is recommended.",
                      input_model=TokenIssueInput, output_model=TokenIssueOutput, writes={"hub"},
                      error_codes=["E_USER_NOT_FOUND", "E_VALIDATION", "E_PERMISSION"],
                      authorization="human self-service; a human hub administrator may issue for another user")


def authorize_token_issue(inp: TokenIssueInput, ctx: Context, s: Session):
    from bookflow.hub import credentials
    if s.actor.kind != "human":
        raise BookflowError("E_PERMISSION", details={"capability": "token", "required_role": "human"})
    target = _target_user(s, inp.user)
    obo = None
    if target["kind"] == "agent" and inp.principal is None:
        raise BookflowError("E_VALIDATION", details={"fields": [{"field": "principal", "problem": "required for an agent token"}]})
    if inp.principal is not None:
        if target["kind"] != "agent":
            raise BookflowError("E_VALIDATION", details={"fields": [{"field": "principal", "problem": "only an agent's token acts on behalf of someone"}]})
        principal = _find_user(s, inp.principal)
        if principal is None:
            raise BookflowError("E_USER_NOT_FOUND", details={"user": inp.principal})
        if principal["kind"] != "human":
            raise BookflowError("E_VALIDATION", details={"fields": [{"field": "principal", "problem": "the principal must be a human user"}]})
        obo = principal["id"]
    epoch = credentials.issuance_epoch(s.hub, user_id=target["id"], on_behalf_of=obo)
    return target, obo, epoch


@token_issue
def plan_token_issue(inp: TokenIssueInput, ctx: Context, s: Session) -> Plan:
    target, obo, epoch = authorize_token_issue(inp, ctx, s)
    preview = TokenIssueOutput(on_behalf_of=obo, authority_epoch=epoch, token_id="", user_id=target["id"], username=target["username"], label=inp.label,
                               expires_at=None, secret="", message="A dry run issues nothing.")
    return Plan(preview=preview, data={"target": target, "label": inp.label, "days": inp.days, "on_behalf_of": obo})


@token_issue.applier
def apply_token_issue(plan: Plan, ctx: Context, s: Session) -> Applied:
    from bookflow.hub import credentials
    target = plan.data["target"]
    row, secret = credentials.issue_token(s.hub, user_id=target["id"], kind="bearer", label=plan.data["label"],
                                   days=plan.data["days"], via=VIA(ctx), actor_id=s.actor.id,
                                   on_behalf_of=plan.data["on_behalf_of"])
    after = {k: v for k, v in row.items() if k != "token_hash"}
    out = TokenIssueOutput(on_behalf_of=row["on_behalf_of"], authority_epoch=row["authority_epoch"], token_id=row["id"], user_id=target["id"], username=target["username"], label=row["label"],
                           expires_at=localize(s, row["expires_at"]), secret=secret, message=SHOWN_ONCE)
    return Applied(out, [Touched("api_token", row["id"], "create", None, 1, after)],
                   f"issued a bearer token for {target['username']} labelled {row['label']}")


token_list = command("token list", scope="hub", description="List the bearer tokens you may see; hub admins see everyone's.",
                     input_model=TokenListInput, output_model=ListOutput[TokenOut], error_codes=["E_USER_NOT_FOUND", "E_PERMISSION"],
                     authorization="own tokens; a hub administrator may list another user's tokens")


@token_list
def plan_token_list(inp: TokenListInput, ctx: Context, s: Session) -> Plan:
    q = sa.select(h.api_tokens).order_by(h.api_tokens.c.created_at.desc())
    if inp.user is not None:
        q = q.where(h.api_tokens.c.user_id == _target_user(s, inp.user)["id"])
    elif not s.is_hub_admin:
        q = q.where(h.api_tokens.c.user_id == s.actor.id)
    if not inp.include_revoked:
        q = q.where(h.api_tokens.c.revoked_at.is_(None),
                    sa.or_(h.api_tokens.c.expires_at.is_(None), h.api_tokens.c.expires_at >= now_iso()))
    rows = [dict(r) for r in s.hub.conn.execute(q).mappings().all()]
    names = users.user_names(s, {r["user_id"] for r in rows})
    items = [_token_out(s, r, names) for r in rows]
    return Plan(preview=ListOutput[TokenOut](items=items, count=len(items)))


def _token_out(s: Session, row: dict[str, Any], names: dict[str, str]) -> TokenOut:
    return TokenOut(**common_out(s, row), token_id=row["id"], user_id=row["user_id"], username=names.get(row["user_id"]),
                    on_behalf_of=row["on_behalf_of"], authority_epoch=row["authority_epoch"], kind=row["kind"], label=row["label"],
                    expires_at=localize(s, row["expires_at"]), last_used_at=localize(s, row["last_used_at"]),
                    revoked_at=localize(s, row["revoked_at"]))


token_revoke = command("token revoke", scope="hub", description="Revoke a bearer token so it stops working immediately.",
                       input_model=TokenSelector, output_model=TokenRevokeOutput, writes={"hub"},
                       positional=["token"], error_codes=["E_TOKEN_NOT_FOUND"],
                       authorization="own tokens; a hub administrator may revoke another user's token")


def authorize_token_revoke(inp: TokenSelector, ctx: Context, s: Session):
    row = None
    if is_ulid(inp.token):
        found = s.hub.conn.execute(sa.select(h.api_tokens).where(h.api_tokens.c.id == normalize_ulid(inp.token))).mappings().first()
        row = dict(found) if found else None
    if row is None:
        raise BookflowError("E_TOKEN_NOT_FOUND", details={"token": inp.token})
    if row["user_id"] != s.actor.id and not s.is_hub_admin:
        raise BookflowError("E_TOKEN_NOT_FOUND", details={"token": inp.token})  # the same answer as for no such token: nothing to enumerate
    return row


@token_revoke
def plan_token_revoke(inp: TokenSelector, ctx: Context, s: Session) -> Plan:
    row = authorize_token_revoke(inp, ctx, s)
    names = users.user_names(s, {row["user_id"]})
    preview = TokenRevokeOutput(token_id=row["id"], user_id=row["user_id"], username=names.get(row["user_id"]), label=row["label"],
                                revoked_at=localize(s, row["revoked_at"]) or now_iso(), changed=row["revoked_at"] is None)
    return Plan(preview=preview, data={"row": row, "names": names})


@token_revoke.applier
def apply_token_revoke(plan: Plan, ctx: Context, s: Session) -> Applied:
    row, names = plan.data["row"], plan.data["names"]
    if row["revoked_at"] is not None:
        out = TokenRevokeOutput(token_id=row["id"], user_id=row["user_id"], username=names.get(row["user_id"]), label=row["label"],
                                revoked_at=localize(s, row["revoked_at"]), changed=False)
        return Applied(out, [], "already revoked")
    at = now_iso()
    after = {k: v for k, v in row.items() if k != "token_hash"} | {"revoked_at": at, "version": row["version"] + 1,
                                                                  "updated_at": at, "updated_by": s.actor.id, "updated_via": VIA(ctx)}
    s.hub.conn.execute(h.api_tokens.update().where(h.api_tokens.c.id == row["id"]).values(
        revoked_at=at, version=after["version"], updated_at=at, updated_by=s.actor.id, updated_via=VIA(ctx)))
    out = TokenRevokeOutput(token_id=row["id"], user_id=row["user_id"], username=names.get(row["user_id"]), label=row["label"],
                            revoked_at=localize(s, at), changed=True)
    return Applied(out, [Touched("api_token", row["id"], "update", row["version"], after["version"], after)],
                   f"revoked the token labelled {row['label']}")
