"""The host process and the credentials it serves: `serve`, `user set-password`, `token issue/list/revoke`.

Nothing here imports FastAPI, uvicorn, or the workbench at module scope: the CLI loads this module for
`bookflow token --help` and `bookflow serve --help`, and cold start must not grow (row 3 plan, Edges).
"""

from __future__ import annotations

import errno
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from bookflow.commands.common import CommonOut, WriteOutput, common_out
from bookflow.core.context import Context, client_version
from bookflow.core.errors import BookflowError
from bookflow.core.ids import is_ulid, normalize_ulid
from bookflow.core.lazy import lazy
from bookflow.core.models import ListOutput
from bookflow.core.registry import Applied, Plan, Touched, command
from bookflow.core.session import Session, localize, now_iso

sa = lazy("sqlalchemy")
h = lazy("bookflow.hub.schema")
users = lazy("bookflow.hub.users")
audit = lazy("bookflow.hub.audit")
auth = lazy("bookflow.adapters.http.auth")

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
        server = uvicorn.Server(uvicorn.Config(handle.app, log_level="warning", access_log=False))
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
                s.hub.raw.execute("COMMIT")
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
        from bookflow.core.dispatch import _close, execute, guard
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
        user_id = user_for_login(host, login)
        raw = envelope.get("input") or {}
        selector = envelope.get("company_selector")
        source = envelope.get("company_source") or "option"
        dry_run = bool(envelope.get("dry_run"))
        if (cmd.is_write and not dry_run) or cmd.kind == "advisory":
            return host.run_write(user_id, login, lambda s: execute(cmd, raw, ctx, s, company_selector=selector, company_source=source, dry_run=dry_run))
        s = host.reader_session(user_id, login)
        try:
            return execute(cmd, raw, ctx, s, company_selector=selector, company_source=source, dry_run=dry_run)
        finally:
            try:
                guard(lambda: _close(s), s.is_hub_admin)
            finally:
                host.reader_done()

    def transfer(login, envelope, socket, *, check=None):
        from pydantic import ValidationError
        from bookflow.adapters.http.local import validate_transfer_envelope
        from bookflow.core import registry
        from bookflow.core.transfer_protocol import FramedReader, _encode, send_body, send_json
        from bookflow.core.transfer_resources import TransferLease
        from bookflow.core.transfers import HostedTransfer

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
            user_id = user_for_login(host, login)
            hosted = HostedTransfer(host, cmd, envelope["input"], ctx, user_id, login,
                                    selector=envelope.get("company_selector"),
                                    source=envelope.get("company_source", "option"),
                                    dry_run=envelope.get("dry_run", False))

            def check_io():
                outer_check()
                hosted.resource.lease.check_io()

            if cmd.transfer.direction == "input":
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
            send_json(socket, {"output": output}, check=outer_check)
        except Exception as exc:
            if not output_started:
                error = exc if isinstance(exc, BookflowError) else BookflowError(
                    "E_IO" if isinstance(exc, (OSError, TimeoutError)) else "E_INTERNAL",
                    message="Local transfer failed.")
                try:
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


@user_set_password
def plan_set_password(inp: SetPasswordInput, ctx: Context, s: Session) -> Plan:
    if s.actor.kind != "human":
        raise BookflowError("E_PERMISSION", details={"capability": "user", "required_role": "human"})
    if not s.is_hub_admin and not _is_self(s, inp.username):
        raise BookflowError("E_PERMISSION", details={"capability": "user", "required_role": "self"})
    row = _find_user(s, inp.username)
    if row is None:
        raise BookflowError("E_USER_NOT_FOUND", details={"username": inp.username})
    if row["kind"] != "human":
        raise BookflowError("E_VALIDATION", details={"fields": [{"field": "username", "problem": "only human users have passwords"}]})
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


@token_issue
def plan_token_issue(inp: TokenIssueInput, ctx: Context, s: Session) -> Plan:
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


@token_revoke
def plan_token_revoke(inp: TokenSelector, ctx: Context, s: Session) -> Plan:
    row = None
    if is_ulid(inp.token):
        found = s.hub.conn.execute(sa.select(h.api_tokens).where(h.api_tokens.c.id == normalize_ulid(inp.token))).mappings().first()
        row = dict(found) if found else None
    if row is None:
        raise BookflowError("E_TOKEN_NOT_FOUND", details={"token": inp.token})
    if row["user_id"] != s.actor.id and not s.is_hub_admin:
        raise BookflowError("E_TOKEN_NOT_FOUND", details={"token": inp.token})  # the same answer as for no such token: nothing to enumerate
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
