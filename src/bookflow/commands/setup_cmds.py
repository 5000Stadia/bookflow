"""`bookflow setup`: from nothing to a company an AI agent is connected to, in one command (R182).

Setup only strings together commands that already exist (`init`, `organization new`, `company new`,
`agent create`, `agent assign`, `membership grant`, `agent authorize`, `token issue`), run as the
person at this computer, so every step is audited as theirs. It adds no accounting behaviour. Then it
writes the agent's MCP client configuration (the token stays in an owner-only file the configuration
points at), gives the owner a one-time browser link to set their password, and starts the host in the
background, waiting until it answers.

Rerunning it repairs or reports: each step looks first and does only what is missing, so nothing is
ever made twice. The agent's token is kept while it is still live, and replaced (the old one revoked)
when it is not.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from bookflow.core.context import Context
from bookflow.core.errors import BookflowError
from bookflow.core.registry import command
from bookflow.core.session import Session

REASON = "bookflow setup"
SERVER_NAME = "bookflow"


class SetupInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    company: str = Field(min_length=1, max_length=200, description="The company's legal name; also its organization's name")
    currency: str = Field("USD", min_length=3, max_length=3, pattern="^[A-Z]{3}$", description="Home currency, ISO 4217 upper case")
    chart: Literal["general", "service", "product", "contractor", "retail", "nonprofit"] = Field(
        "general", description="Packaged chart of accounts to start from")
    owner: str | None = Field(None, max_length=64, description="The owner's username; defaults to this computer's login")
    owner_name: str | None = Field(None, max_length=200, description="The owner's name as people see it")
    agent: str = Field("assistant", min_length=1, max_length=64, description="Username for the AI agent's own identity")
    agent_name: str | None = Field(None, max_length=200, description="The agent's name in lists and the audit trail; defaults to Assistant")
    role: Literal["readonly", "standard", "admin"] | None = Field(None, description="The agent's role in the company: standard for a new agent; given for an existing one, it changes the role")
    mcp_config: str = Field(".mcp.json", min_length=1, description="MCP client configuration file to write or update (the `bookflow` server entry only)")
    bind: str = Field("127.0.0.1:8765", max_length=128, description="Address the host listens on, host:port (loopback)")
    serve: bool = Field(True, description="Start the host in the background if it is not answering, and wait until it does")
    link_minutes: int = Field(120, ge=1, le=1440, description="Minutes the owner's one-time password link stays valid")
    new_token: bool = Field(False, description="Issue the agent a new token even though its last one was revoked; a person's decision")


class SetupStep(BaseModel):
    step: str
    status: Literal["created", "exists", "updated", "skipped", "stopped"]
    detail: str


class OwnerStep(BaseModel):
    step: Literal["password", "backups"]
    done: bool
    detail: str
    command: str | None = Field(None, description="The exact command the owner runs; null when nothing is left to run")


class SetupOutput(BaseModel):
    dry_run: bool = False
    warnings: list[str] = Field(default_factory=list)
    data_root: str
    owner: dict[str, Any] = Field(description="user_id, username and display_name of the person setup ran for")
    company: dict[str, Any] = Field(description="company_id, display_name, home_currency and organization_id")
    agent: dict[str, Any] = Field(description="user_id, username and role of the agent's own identity, and the principal it acts for")
    mcp_config: str = Field(description="The MCP client configuration file written")
    mcp_server: dict[str, Any] = Field(description="The `bookflow` server entry in it: command, args and env (no secret)")
    token_file: str = Field(description="Owner-only file holding the agent's bearer; the configuration points at it")
    url: str = Field(description="Where the host answers; GET <url>health returns 200 once it is ready")
    host: dict[str, Any] = Field(description="status (running, started, not_started), pid and log")
    owner_link: dict[str, Any] | None = Field(description="url and expires_at of the owner's one-time password link; null when they have a password")
    owner_checklist: list[OwnerStep] = Field(description="What only the owner does, still to do: set their password, turn on daily backups; each with the exact command")
    steps: list[SetupStep]
    next_steps: list[str]
    content: str = Field(description="The same result as plain text, for a person")
    media_type: str = "text/plain"


def _plan_setup(*_args):  # never called; bootstrap has its own path
    raise NotImplementedError


setup_cmd = command(
    "setup", scope="hub",
    description=("Set up Bookflow for an AI agent in one step: the data root and its owner, a company with a chart of accounts, "
                 "the agent's own identity working for the owner, its MCP client configuration and token file, a one-time "
                 "link for the owner to set a password in the browser, and the host running in the background. Rerunning "
                 "it repairs what is missing and reports the rest; it never makes anything twice."),
    input_model=SetupInput, output_model=SetupOutput, writes={"hub", "config", "company"},
    error_codes=["E_INIT_CONFLICT", "E_VALIDATION", "E_PERMISSION", "E_IO", "E_NETWORK_NOT_ALLOWED"], bootstrap=True, local_only=True,
    # `init`'s own capability: setup is init plus existing commands, so it adds no capability (agent-setup-v1).
    capability="init",
    authorization="the local operating-system login, which becomes or already is the installation's administrator",
)(_plan_setup)


def _readable(moment: str) -> str:
    """An ISO UTC instant as `2026-10-27 10:10 UTC`."""
    return moment.replace("T", " ")[:16] + " UTC"


def launcher_command() -> list[str]:
    """How an MCP client starts this same installation's `bookflow`, as an absolute path."""
    import shutil
    beside = Path(sys.executable).parent / "bookflow"
    if beside.is_file() and os.access(beside, os.X_OK):
        return [str(beside)]
    found = shutil.which("bookflow")
    if found:
        return [str(Path(found).resolve())]
    return [sys.executable, "-c", "from bookflow.bootstrap import main; main()"]


def _write_atomic(path: Path, text: str, mode: int) -> None:
    """Write beside the target under a fresh random name (never following or reusing a file), then rename."""
    import secrets
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            os.fchmod(out.fileno(), mode)
            out.write(text)
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _write_json_atomic(path: Path, data: dict[str, Any], mode: int) -> None:
    _write_atomic(path, json.dumps(data, indent=2) + "\n", mode)


def _write_secret(path: Path, secret: str) -> None:
    _write_atomic(path, secret + "\n", 0o600)


# ---------------------------------------------------------------- the owner's own steps (R183)

def default_backup_folder() -> Path:
    return Path.home() / "bookflow-backups"


def _root_flag(root: Path) -> str:
    """` --data-root ROOT` when the books are not in the default place, so a pasted command finds them."""
    from bookflow.storage.paths import default_data_root
    try:
        same = Path(root).resolve() == default_data_root().resolve()
    except OSError:
        same = False
    return "" if same else f" --data-root {_quoted(str(root))}"


def _quoted(value: str) -> str:
    import shlex
    return shlex.quote(value)


def owner_steps(root: Path, *, company_id: str, company_name: str, has_password: bool,
                backups_on: bool, link: dict[str, Any] | None = None) -> list[OwnerStep]:
    """The owner's checklist: the two things only they do, each with the command that does it."""
    flag = _root_flag(root)
    if has_password:
        password = OwnerStep(step="password", done=True, detail="The owner has a password and logs in in the browser.")
    elif link:
        password = OwnerStep(step="password", done=False, command=f"bookflow owner-link{flag}",
                             detail=f"Open the one-time link to choose a password (it works once, until "
                                    f"{_readable(link['expires_at'])}): {link['url']} . If it expires, this makes a fresh one:")
    else:
        password = OwnerStep(step="password", done=False, command=f"bookflow owner-link{flag}",
                             detail="Choose a password through a one-time link; `bookflow owner-link` makes one.")
    if backups_on:
        backups = OwnerStep(step="backups", done=True, detail="Daily backups are on (`bookflow backup list` shows them).")
    else:
        folder = _quoted(str(default_backup_folder()))
        backups = OwnerStep(step="backups", done=False,
                            command=(f"mkdir -p {folder} && bookflow backup schedule {_quoted(company_name)} "
                                     f"--daily-at 02:00 --destination {folder} --keep 14{flag}"),
                            detail="Turn on daily backups (a person's decision; an agent cannot). Another disk or "
                                   "machine is safer than this one: change --destination to it.")
    return [password, backups]


class OwnerLinkInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    bind: str = Field("127.0.0.1:8765", max_length=128, description="Address the host listens on, host:port (loopback); the link opens there")
    link_minutes: int = Field(120, ge=1, le=1440, description="Minutes the link stays valid")


class OwnerLinkOutput(BaseModel):
    username: str
    url: str = Field(description="The one-time link; it works once and only for a person with no password yet")
    expires_at: str
    host_running: bool = Field(description="Whether the host answers at the link's address now; start it with `bookflow serve --background`")
    content: str
    media_type: str = "text/plain"


owner_link_cmd = command(
    "owner-link", scope="hub",
    description=("Make a fresh one-time link for the owner (this computer's login) to choose their password in the "
                 "browser, as `setup` does: it works once, expires (two hours by default) and replaces any earlier "
                 "link. It never resets a password: once the owner has one, it is refused. Use it instead of "
                 "rerunning setup when a link expired or was lost."),
    input_model=OwnerLinkInput, output_model=OwnerLinkOutput, writes={"config"},
    error_codes=["E_VALIDATION", "E_NOT_INITIALIZED", "E_NETWORK_NOT_ALLOWED"], bootstrap=True, local_only=True,
    capability="init",
    authorization="the local operating-system login, which must be the installation's owner",
)(_plan_setup)


def run_owner_link(cmd, inp: OwnerLinkInput, ctx: Context, s: Session) -> dict[str, Any]:
    """The bootstrap path for `owner-link`: the same link store and rules as setup's step 6."""
    import sqlalchemy as sa
    from bookflow.commands.host_cmds import host_url, parse_bind, is_loopback, answers
    from bookflow.core.config import Config
    from bookflow.hub import setup_links
    from bookflow.hub import schema as h
    from bookflow.storage.engine import open_database

    if s.dry_run:
        raise BookflowError("E_USAGE", message="`owner-link` has no dry run.")
    host_name, _port = parse_bind(inp.bind)
    if not is_loopback(host_name):
        raise BookflowError("E_NETWORK_NOT_ALLOWED", details={"bind": inp.bind},
                            message="The link opens on this computer only; use a loopback address such as 127.0.0.1:8765.")
    root = Path(os.path.abspath(Path(s.data_root).expanduser()))
    if not (root / "hub.db").exists():
        raise BookflowError("E_NOT_INITIALIZED", details={"data_root": str(root)})
    mapped = Config.load(root / "config.toml").user_table(s.os_login) or {}
    owner_id = mapped.get("user_id")
    if not isinstance(owner_id, str):
        raise BookflowError("E_NO_ACTOR", message=f"This computer's login is not the owner of {root}; run it as the person who owns it.")
    with open_database(root / "hub.db", writable=False) as db:
        row = db.conn.execute(sa.select(h.users).where(h.users.c.id == owner_id)).mappings().first()
    if row is None or row["kind"] != "human" or not row["active"]:
        raise BookflowError("E_NO_ACTOR", message="This computer's login is not mapped to an active person.")
    if row["password_hash"]:
        raise BookflowError("E_VALIDATION", message=f"{row['username']} already has a password; a one-time link only sets a first one.",
                            details={"fields": [{"field": "owner", "problem": "already has a password"}]})
    url = host_url(inp.bind)
    secret, expires = setup_links.create(root, owner_id, minutes=inp.link_minutes)
    link = f"{url}setup/{secret}"
    running = answers(url)
    text = (f"One-time link for {row['username']} to choose a password (works once, until {_readable(expires)}):\n  {link}\n"
            + ("" if running else "The host is not answering yet; start it with `bookflow serve --background`.\n"))
    return OwnerLinkOutput(username=row["username"], url=link, expires_at=expires, host_running=running,
                           content=text).model_dump(mode="json")


def run_setup(cmd, inp: SetupInput, ctx: Context, s: Session) -> dict[str, Any]:
    """The bootstrap path for `setup` (dispatch._run_bootstrap): existing commands, in order, each only if needed."""
    import sqlalchemy as sa
    from bookflow.client import Client
    from bookflow.commands.host_cmds import host_url, parse_bind, is_loopback, start_in_background, answers, serving_pid
    from bookflow.core.config import Config
    from bookflow.hub import setup_links
    from bookflow.hub import schema as h
    from bookflow.storage.engine import open_database

    if s.dry_run:
        raise BookflowError("E_USAGE", message="`setup` has no dry run; rerunning it is safe and only adds what is missing.")
    host_name, _port = parse_bind(inp.bind)
    if not is_loopback(host_name):
        raise BookflowError("E_NETWORK_NOT_ALLOWED", details={"bind": inp.bind},
                            message="`setup` serves on this computer only; use a loopback address such as 127.0.0.1:8765.")
    # Always absolute: the token file's path goes into .mcp.json, which an MCP client reads from
    # its own working directory, and a relative --data-root would point it somewhere else.
    root = Path(os.path.abspath(Path(s.data_root).expanduser()))
    url = host_url(inp.bind)
    steps: list[SetupStep] = []
    warnings: list[str] = []

    def step(name: str, status: str, detail: str) -> None:
        steps.append(SetupStep(step=name, status=status, detail=detail))

    client = Client(data_root=str(root), client_name="bookflow-setup")
    if s.os_login:
        client._login = s.os_login

    def run(name: str, data: dict[str, Any] | None = None, *, company: str | None = None, write: bool = False) -> dict[str, Any]:
        return client.run(name, data or {}, company=company, reason=REASON if write else None)

    # 1. The data root and its owner.
    if not (root / "hub.db").exists():
        made = run("init", {"username": inp.owner, "display_name": inp.owner_name})
        step("owner", "created", f"data root {root} and owner {made['username']}")
    mapped = Config.load(root / "config.toml").user_table(s.os_login) or {}
    owner_id = mapped.get("user_id")
    if not isinstance(owner_id, str):
        raise BookflowError("E_NO_ACTOR", message=f"This computer's login is not the owner of {root}; run setup as the person who owns it.")
    with open_database(root / "hub.db", writable=False) as db:
        owner_row = db.conn.execute(sa.select(h.users).where(h.users.c.id == owner_id)).mappings().first()
    owner_row = dict(owner_row)
    if inp.owner and inp.owner.casefold() != owner_row["username"].casefold():
        raise BookflowError("E_INIT_CONFLICT", details={"username": owner_row["username"]},
                            message=f"This data root already belongs to {owner_row['username']}; pass --owner {owner_row['username']} or use another data root.")
    if not steps:
        step("owner", "exists", f"data root {root} and owner {owner_row['username']}")

    # 2. The organization and the company.
    orgs = run("organization list")["items"]
    org = next((o for o in orgs if o["display_name"].casefold() == inp.company.casefold()), None)
    companies = run("company list")["items"]
    found = next((c for c in companies if c["display_name"].casefold() == inp.company.casefold()), None)
    if found is None:
        if org is None:
            org = run("organization new", {"name": inp.company}, write=True)
            step("organization", "created", org["display_name"])
        made = run("company new", {"legal_name": inp.company, "home_currency": inp.currency, "chart": inp.chart,
                                   "organization": org["organization_id"]}, write=True)
        company_id = made["company_id"]
        step("company", "created", f"{inp.company} ({inp.currency}, {inp.chart} chart)")
    else:
        company_id = found["company_id"]
        step("company", "exists", inp.company)
    shown = run("company show", company=company_id)
    if shown.get("home_currency") and shown["home_currency"] != inp.currency:
        raise BookflowError("E_VALIDATION", details={"fields": [{"field": "currency", "problem": f"{inp.company} keeps its books in {shown['home_currency']}"}]})

    # 3. The agent's own identity, working for the owner in this company.
    agent = None
    try:
        agent = run("agent show", {"agent": inp.agent})
    except BookflowError as e:
        if e.code not in ("E_USER_NOT_FOUND", "E_NOT_FOUND"):
            raise
    created = agent is None
    if created:
        run("agent create", {"username": inp.agent, "display_name": inp.agent_name or "Assistant"}, write=True)
        agent = run("agent show", {"agent": inp.agent})
        step("agent", "created", f"{inp.agent}, its own identity in the audit trail")
    else:
        step("agent", "exists", inp.agent)
    if not agent.get("active", True):
        raise BookflowError("E_VALIDATION", message=f"The agent {inp.agent} is deactivated; a person brings it back with `agent activate`.")
    # A person's stop (unassigning, narrowing, suspending, revoking) is never undone here: setup reports it
    # with the command a person runs to restore it. Only an agent this run created is assigned and authorized.
    stopped: list[str] = []

    def stop(name: str, detail: str, restore: str) -> None:
        step(name, "stopped", f"{detail}; to restore it a person runs: {restore}")
        stopped.append(restore)

    owner_name = owner_row["username"]
    if owner_id in {p["user_id"] for p in agent["principals"]}:
        step("works for", "exists", f"{inp.agent} acts for {owner_name}")
    elif created:
        run("agent assign", {"agent": inp.agent, "principal": owner_id, "confirm_permitted_use": True}, write=True)
        step("works for", "created", f"{inp.agent} acts for {owner_name}")
    else:
        stop("works for", f"{inp.agent} does not act for {owner_name}",
             f"bookflow agent assign {inp.agent} --principal {owner_name} --confirm-permitted-use")
    try:
        listed = run("membership list", {"user": agent["username"], "company": company_id, "include_inactive": True})["items"]
    except BookflowError as e:
        if e.code != "E_USER_NOT_FOUND":  # an agent with no access anywhere yet is not listed at all
            raise
        listed = []
    access = [m for m in listed if m["scope_type"] == "company" and m["scope_id"] == company_id]
    live_access = [m for m in access if m["active"]]
    role = inp.role or "standard"
    if stopped:
        step("access", "skipped", "left as it is while the agent is stopped")
        role = live_access[0]["role"] if live_access else None
    elif live_access and (inp.role is None or live_access[0]["role"] == inp.role):
        role = live_access[0]["role"]
        step("access", "exists", f"{role} in {inp.company}")
    elif access and not live_access and not created:
        role = None
        stop("access", f"{inp.agent}'s access to {inp.company} was revoked",
             f"bookflow membership grant {inp.agent} --company \"{inp.company}\" --role {inp.role or access[0]['role']}")
    else:
        run("membership grant", {"user": inp.agent, "company": company_id, "role": role}, write=True)
        step("access", "updated" if live_access else "created", f"{role} in {inp.company}")
    agent = run("agent show", {"agent": inp.agent})
    authority = agent["authority"]
    if not authority["suspended"]:
        step("authorized", "exists", f"{inp.agent} may act")
    elif authority["authorized_at"] is None and not authority["fresh_context_required"] and not stopped:
        run("agent authorize", {"agent": inp.agent, "confirm_permitted_use": True}, write=True)
        agent = run("agent show", {"agent": inp.agent})
        step("authorized", "created", f"{inp.agent} may act")
    else:
        fresh = " --acknowledge-fresh-context" if authority["fresh_context_required"] else ""
        stop("authorized", f"{inp.agent} is suspended ({authority['suspension_reason']})",
             f"bookflow agent authorize {inp.agent} --confirm-permitted-use{fresh}")

    # 4. The agent's token, kept in an owner-only file beside the books.
    folder = root / "agents"
    folder.mkdir(mode=0o700, exist_ok=True)
    os.chmod(folder, 0o700)
    token_file = folder / f"{agent['username']}.token"
    meta_file = folder / f"{agent['username']}.json"
    if token_file.exists() and not token_file.is_file():
        raise BookflowError("E_VALIDATION", details={"fields": [{"field": "token_file", "problem": f"{token_file} is not a regular file; move it aside"}]})
    live = {t["token_id"] for t in run("token list", {"user": agent["agent_id"]})["items"]}
    try:
        kept = json.loads(meta_file.read_text(encoding="utf-8")).get("token_id")
    except (OSError, ValueError, AttributeError):
        kept = None
    if kept in live and token_file.is_file():
        step("token", "exists", f"kept {token_file}")
    elif stopped:
        step("token", "skipped", "no token is issued while the agent is stopped")
    elif kept is not None and kept not in live and not inp.new_token:
        stop("token", f"{inp.agent}'s token was revoked",
             f"bookflow setup --company \"{inp.company}\" --new-token")
    else:
        if kept in live:
            run("token revoke", {"token": kept}, write=True)
        issued = run("token issue", {"user": agent["agent_id"], "label": f"{agent['username']} MCP", "principal": owner_id}, write=True)
        _write_secret(token_file, issued["secret"])
        _write_json_atomic(meta_file, {"token_id": issued["token_id"], "user_id": issued["user_id"],
                                       "on_behalf_of": issued["on_behalf_of"]}, 0o600)
        step("token", "created" if kept is None else "updated", f"written to {token_file} (mode 0600)")

    # 5. The MCP client configuration: no secret in it, only the token file's path.
    config_path = Path(os.path.abspath(Path(inp.mcp_config).expanduser()))
    command_line = launcher_command()
    server = {"command": command_line[0],
              "args": [*command_line[1:], "mcp", "--url", url, "--client-name", agent["username"],
                       "--token-file", str(token_file)],
              "env": {"BOOKFLOW_COMPANY": company_id}}
    existing: dict[str, Any] = {}
    if config_path.exists():
        try:
            existing = json.loads(config_path.read_text(encoding="utf-8") or "{}")
        except ValueError:
            raise BookflowError("E_VALIDATION", details={"fields": [{"field": "mcp_config", "problem": f"{config_path} is not valid JSON; fix or move it"}]}) from None
        if not isinstance(existing, dict) or not isinstance(existing.get("mcpServers", {}), dict):
            raise BookflowError("E_VALIDATION", details={"fields": [{"field": "mcp_config", "problem": f"{config_path} is not an MCP configuration"}]})
    before = (existing.get("mcpServers") or {}).get(SERVER_NAME)
    if before == server:
        step("mcp config", "exists", str(config_path))
    else:
        existing.setdefault("mcpServers", {})[SERVER_NAME] = server
        config_path.parent.mkdir(parents=True, exist_ok=True)
        # An existing file keeps its own permissions; a new one is readable, as it holds no secret.
        mode = config_path.stat().st_mode & 0o777 if config_path.exists() else 0o644
        _write_json_atomic(config_path, existing, mode)
        step("mcp config", "created" if before is None else "updated", f"{config_path} (server `{SERVER_NAME}`)")

    # 6. The owner's one-time password link, only while they have no password.
    owner_link = None
    with open_database(root / "hub.db", writable=False) as db:
        has_password = bool(db.conn.execute(sa.select(h.users.c.password_hash).where(h.users.c.id == owner_id)).scalar_one_or_none())
    if has_password:
        step("owner password", "exists", f"{owner_row['username']} already has a password")
    else:
        secret, expires = setup_links.create(root, owner_id, minutes=inp.link_minutes)
        owner_link = {"url": f"{url}setup/{secret}", "expires_at": expires}
        step("owner password", "created", f"one-time link, valid until {_readable(expires)}")

    # 7. The host.
    running = serving_pid(root, inp.bind)
    if running:
        host = {"status": "running", "pid": running, "log": None}
        step("host", "exists", f"answering at {url} (pid {running})")
    elif answers(url):
        raise BookflowError("E_IO", message=f"Something else answers at {url}, not this data root's host; choose another --bind.",
                            details={"operation": "serve", "address": inp.bind})
    elif inp.serve:
        started = start_in_background(root, inp.bind)
        host = {"status": "started" if started["started"] else "running", "pid": started["pid"] or None, "log": started["log"]}
        step("host", "created", f"answering at {url}" + (f" (pid {started['pid']}, log {started['log']})" if started["pid"] else ""))
    else:
        host = {"status": "not_started", "pid": None, "log": None}
        step("host", "skipped", "start it with `bookflow serve --background`")

    company_out = {"company_id": company_id, "display_name": shown.get("display_name", inp.company),
                   "home_currency": shown.get("home_currency", inp.currency), "organization_id": shown.get("organization_id")}
    next_steps = [f"The agent is stopped by a person's decision; only a person restores it: {r}" for r in stopped]
    warnings.extend(f"stopped: {r}" for r in stopped)
    if host["status"] == "not_started":
        next_steps.append(f"Start the host: bookflow serve --background --bind {inp.bind}")
    next_steps.append(f"Load the MCP server `{SERVER_NAME}` from {config_path} (restart or reload your MCP client).")
    next_steps.append('Check the connection: call bookflow_run with {"command": "company show", "input": {}}.')
    if owner_link:
        next_steps.append(f"Give the owner this link to set their password (it works once, until {_readable(owner_link['expires_at'])}): "
                          f"{owner_link['url']} . If it expires or is lost, `bookflow owner-link` makes a fresh one; there is no need to rerun setup.")
    else:
        next_steps.append(f"The owner logs in at {url} as {owner_row['username']}.")
    from bookflow.company import backup_schedule as sched
    checklist = owner_steps(root, company_id=company_id, company_name=company_out["display_name"], has_password=has_password,
                            backups_on=sched.entry(Config.load(root / "config.toml").data, company_id) is not None, link=owner_link)
    pending = [x for x in checklist if not x.done]
    if pending:
        next_steps.append("Hand the owner their checklist (owner_checklist): only they do these.")
    lines = [f"Bookflow is set up for {company_out['display_name']} ({company_out['home_currency']}).", ""]
    lines += [f"  {x.step:<15} {x.status:<8} {x.detail}" for x in steps]
    lines += ["", "Next:"] + [f"  {n}. {t}" for n, t in enumerate(next_steps, 1)]
    if pending:
        lines += ["", "Owner checklist (only the owner does these):"]
        for x in pending:
            lines.append(f"  [ ] {x.detail}")
            if x.command:
                lines.append(f"      {x.command}")
    return SetupOutput(
        warnings=warnings, data_root=str(root),
        owner={"user_id": owner_id, "username": owner_row["username"], "display_name": owner_row["display_name"]},
        company=company_out,
        agent={"user_id": agent["agent_id"], "username": agent["username"], "role": role, "stopped": bool(stopped),
               "acts_for": owner_row["username"], "acts_for_user_id": owner_id},
        mcp_config=str(config_path), mcp_server=server, token_file=str(token_file), url=url, host=host,
        owner_link=owner_link, owner_checklist=checklist, steps=steps, next_steps=next_steps, content="\n".join(lines) + "\n",
    ).model_dump(mode="json")
