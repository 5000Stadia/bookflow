"""R182: an agent given only the repository installs Bookflow, sets up a company and connects to it.

The clean-install test builds the wheel from this checkout and installs it, without network, into a
fresh virtual environment (no extra named: MCP must come by default). From there it runs exactly what
docs/agent-install.md tells an agent to run, then connects with the MCP configuration `setup` wrote and
posts an invoice that the audit trail attributes to the agent, for the owner. Rerunning setup must
report rather than duplicate; the owner's password link must work once and expire.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import time
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pytest

from bookflow.hub import setup_links

REPO = Path(__file__).resolve().parents[1]
COMPANY = "Green Valley Landscaping"


# ---------------------------------------------------------------- the link store on its own

def test_a_setup_link_works_once_and_expires(tmp_path):
    root = tmp_path / "root"
    root.mkdir(mode=0o700)
    secret, expires = setup_links.create(root, "U1", minutes=5)
    assert datetime.fromisoformat(expires) > datetime.now(timezone.utc)
    folder = root / setup_links.DIRECTORY
    assert oct(folder.stat().st_mode & 0o777) == "0o700"
    [stored] = list(folder.glob("*.json"))
    assert oct(stored.stat().st_mode & 0o777) == "0o600"
    assert secret not in stored.read_text() and secret not in stored.name, "only the secret's hash is kept"

    assert setup_links.peek(root, secret)["user_id"] == "U1"
    assert setup_links.peek(root, secret + "x") is None
    claimed = setup_links.claim(root, secret)
    assert claimed is not None
    assert setup_links.claim(root, secret) is None, "a link in use cannot be claimed twice"
    setup_links.release(claimed[1], secret)  # the password was not saved: the link stays usable
    link, path = setup_links.claim(root, secret)
    setup_links.spend(path)
    assert setup_links.peek(root, secret) is None and setup_links.claim(root, secret) is None

    # A new link for the same person replaces the old; an expired link is gone.
    first, _ = setup_links.create(root, "U1")
    second, _ = setup_links.create(root, "U1")
    assert setup_links.peek(root, first) is None and setup_links.peek(root, second)["user_id"] == "U1"
    _expire(root, second)
    assert setup_links.peek(root, second) is None and setup_links.claim(root, second) is None
    with pytest.raises(ValueError):
        setup_links.create(root, "U1", minutes=24 * 60 + 1)


def _expire(root: Path, secret: str) -> None:
    path = setup_links._path(root, secret)
    data = json.loads(path.read_text())
    data["expires_at"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(timespec="seconds")
    path.write_text(json.dumps(data))
    os.chmod(path, 0o600)


# ---------------------------------------------------------------- from a clean install

def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _get(url: str) -> int:
    try:
        with urlopen(url, timeout=10) as response:
            return response.status
    except HTTPError as e:
        return e.code


def _post(url: str, fields: dict[str, str]) -> tuple[int, dict, dict]:
    request = Request(url, data=urlencode(fields).encode(), method="POST",
                      headers={"Content-Type": "application/x-www-form-urlencoded", "HX-Request": "true"})
    try:
        with urlopen(request, timeout=30) as response:
            return response.status, json.loads(response.read()), dict(response.headers)
    except HTTPError as e:
        return e.code, json.loads(e.read()), dict(e.headers)


@pytest.fixture(scope="module")
def installed(tmp_path_factory):
    """The wheel built from this checkout, installed offline into a fresh venv; `bookflow` from there."""
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("uv is needed to build and install the wheel offline")
    base = tmp_path_factory.mktemp("install")
    built = subprocess.run([uv, "build", "--wheel", "--offline", "--out-dir", str(base / "dist"), str(REPO)],
                           capture_output=True, text=True, timeout=300)
    assert built.returncode == 0, built.stderr[-2000:]
    [wheel] = list((base / "dist").glob("bookflow_core-*.whl"))
    venv = base / "venv"
    made = subprocess.run([uv, "venv", "--offline", "-q", "-p", sys.executable, str(venv)],
                          capture_output=True, text=True, timeout=120)
    assert made.returncode == 0, made.stderr[-2000:]
    # No extra: the agent's install command names none, so MCP must be in the default dependencies.
    got = subprocess.run([uv, "pip", "install", "--offline", "-q", "-p", str(venv / "bin" / "python"), str(wheel)],
                         capture_output=True, text=True, timeout=300)
    if got.returncode != 0 and "offline" in got.stderr.lower():
        pytest.skip("a dependency is not in the local uv cache: " + got.stderr[-300:])
    assert got.returncode == 0, got.stderr[-2000:]
    return venv / "bin" / "bookflow"


def _clean_env(home: Path) -> dict[str, str]:
    """Nothing from the test process's import path or data root reaches the installed product."""
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(home), "LANG": "C.UTF-8"}
    for name in ("USER", "LOGNAME", "XDG_RUNTIME_DIR", "TMPDIR", "TZ"):
        if name in os.environ:
            env[name] = os.environ[name]
    return env


@pytest.mark.timeout(600)
def test_install_setup_connect_and_post_an_invoice_as_the_agent_for_the_owner(installed, tmp_path):
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client
    import anyio

    home = tmp_path / "home"
    project = tmp_path / "project"
    home.mkdir()
    project.mkdir()
    root = tmp_path / "books"
    port = _free_port()
    env = _clean_env(home)
    commands = []  # every shell command an agent runs, from nothing to a working company

    def setup(*extra: str) -> dict:
        argv = [str(installed), "setup", "--company", COMPANY, "--currency", "USD", "--owner", "dana",
                "--owner-name", "Dana Ruiz", "--data-root", str(root), "--bind", f"127.0.0.1:{port}", "--json", *extra]
        commands.append(argv)
        done = subprocess.run(argv, cwd=project, env=env, capture_output=True, text=True, timeout=300)
        assert done.returncode == 0, done.stderr[-3000:]
        return json.loads(done.stdout)

    pid = None
    try:
        commands.append(["uv", "tool", "install", "git+https://github.com/5000Stadia/bookflow"])  # what the wheel stands in for
        first = setup()
        pid = first["host"]["pid"]
        url = first["url"]
        assert first["host"]["status"] == "started" and pid
        # Readiness is signalled: setup returned only once the host answered, and the host said so.
        assert _get(url + "health") == 200
        assert "Bookflow is ready at " + url in (root / "host.log").read_text()
        assert {s["step"]: s["status"] for s in first["steps"]} == {
            "owner": "created", "organization": "created", "company": "created", "agent": "created",
            "works for": "created", "access": "created", "authorized": "created", "token": "created",
            "mcp config": "created", "owner password": "created", "host": "created"}
        token_file = Path(first["token_file"])
        assert oct(token_file.stat().st_mode & 0o777) == "0o600"
        secret = token_file.read_text()
        config_text = (project / ".mcp.json").read_text()
        assert token_file.read_text().strip() not in config_text, "the configuration never holds the secret"
        server = json.loads(config_text)["mcpServers"]["bookflow"]
        assert server == first["mcp_server"] and Path(server["command"]) == installed

        # The agent connects with exactly that configuration (a client adds its own PATH/HOME).
        company_id = first["company"]["company_id"]

        async def work():
            # The freshly installed launcher owns provenance; never inject the checkout's import path.
            mcp_env = {**env, **server["env"]}
            assert "PYTHONPATH" not in mcp_env
            params = StdioServerParameters(command=server["command"], args=server["args"], env=mcp_env)
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.discover()

                    async def run(name, data, **context):
                        reply = await session.call_tool("bookflow_run", {"command": name, "input": data, **context})
                        assert not reply.is_error, (name, reply.structured_content)
                        return reply.structured_content

                    shown = await run("company show", {})  # the first check the install page names
                    assert shown["company_id"] == company_id and shown["home_currency"] == "USD"
                    income = next(a for a in (await run("account list", {}))["items"] if a["type"] == "income")
                    exempt = next(c["id"] for c in (await run("sales-tax-code list", {}))["items"] if not c["taxable"])
                    customer = await run("customer create", {"name": "Maple St HOA"}, reason="New customer")
                    item = await run("item create", {"name": "Cleanup", "type": "service", "sales_enabled": True,
                                                     "description": "Yard cleanup", "income_account_id": income["id"],
                                                     "price": "1200", "sales_tax_code_id": exempt}, reason="New service")
                    invoice = await run("invoice post", {"date": "2026-10-26", "customer": customer["id"],
                                                         "lines": [{"item": item["id"], "quantity": "1"}]},
                                        reason="Invoice Maple St HOA")
                    events = (await run("audit list", {"command": "invoice post", "limit": 5}))["items"]
                    return invoice, events

        invoice, events = anyio.run(work)
        assert invoice["total"]["amount"] == "1200.00"
        [event] = [e for e in events if invoice["id"] in json.dumps(e)] or events[:1]
        assert event["actor_kind"] == "agent" and event["actor_name"] == "Assistant"
        assert event["on_behalf_of"] == first["owner"]["user_id"] and event["on_behalf_of_name"] == "Dana Ruiz"
        assert event["interface"] == "mcp"
        # From nothing to a connected company that has posted: install, setup, and MCP calls only.
        shell_commands = len(commands)
        assert shell_commands == 2

        # Rerunning setup reports; it makes nothing twice and replaces the owner's unused link.
        second = setup()
        assert {s["status"] for s in second["steps"] if s["step"] != "owner password"} == {"exists"}
        assert second["owner_link"]["url"] != first["owner_link"]["url"]
        assert _get(first["owner_link"]["url"]) == 410, "the replaced link no longer opens"
        assert token_file.read_text() == secret, "a live token is kept"
        assert second["token_file"] == first["token_file"] and second["mcp_server"] == first["mcp_server"]
        listing = subprocess.run([str(installed), "user", "list", "--kind", "agent", "--data-root", str(root), "--json"],
                                 env=env, capture_output=True, text=True, timeout=120)
        assert [u["username"] for u in json.loads(listing.stdout)["items"]] == ["assistant"]
        companies = subprocess.run([str(installed), "company", "list", "--data-root", str(root), "--json"],
                                   env=env, capture_output=True, text=True, timeout=120)
        assert [c["company_id"] for c in json.loads(companies.stdout)["items"]] == [company_id]

        # The link: an expired one is refused; a live one sets the password once and signs the owner in.
        _expire(root, second["owner_link"]["url"].rsplit("/", 1)[1])
        assert _get(second["owner_link"]["url"]) == 410
        link = setup()["owner_link"]["url"]
        assert _get(link) == 200
        status, body, _ = _post(link, {"password": "garden-path-2026", "again": "garden-path-2027"})
        assert status == 400 and "match" in body["message"]
        assert _get(link) == 200, "a refused entry does not use the link up"
        status, body, headers = _post(link, {"password": "garden-path-2026", "again": "garden-path-2026"})
        assert status == 200 and body["username"] == "dana"
        assert "bookflow_session=" in headers.get("set-cookie", headers.get("Set-Cookie", ""))
        status, _, _ = _post(link, {"password": "garden-path-2099", "again": "garden-path-2099"})
        assert status == 410 and _get(link) == 410
        login = Request(url + "login", data=json.dumps({"username": "dana", "password": "garden-path-2026"}).encode(),
                        method="POST", headers={"Content-Type": "application/json"})
        with urlopen(login, timeout=30) as response:
            assert json.loads(response.read())["username"] == "dana"
        last = setup()
        assert last["owner_link"] is None
        assert {s["step"]: s["status"] for s in last["steps"]}["owner password"] == "exists"
    finally:
        if pid:
            os.kill(pid, signal.SIGINT)
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    break
                time.sleep(0.1)


# ---------------------------------------------------------------- R183: the agent acts at once; paths, links, the owner's list

@pytest.mark.timeout(600)
def test_owner_link_and_agent_run_from_a_relative_root_rerun(installed, tmp_path):
    home = tmp_path / "home"
    project = tmp_path / "project"
    home.mkdir()
    project.mkdir()
    port = _free_port()
    env = _clean_env(home)

    def cli(*argv: str, ok: bool = True) -> dict:
        done = subprocess.run([str(installed), *argv, "--json"], cwd=project, env=env, capture_output=True, text=True, timeout=300)
        assert (done.returncode == 0) == ok, done.stderr[-3000:] + done.stdout[-2000:]
        if ok:
            return json.loads(done.stdout)
        # A refusal is one JSON error object, on stderr beside the readable line.
        return next(json.loads(line) for line in (done.stdout + "\n" + done.stderr).splitlines() if line.startswith("{"))

    setup = ("setup", "--company", COMPANY, "--owner", "dana", "--owner-name", "Dana Ruiz",
             "--data-root", "../books", "--bind", f"127.0.0.1:{port}")
    pid = None
    try:
        first = cli(*setup)
        pid = first["host"]["pid"]
        again = cli(*setup)  # a rerun with the same relative root: every path written stays absolute
        root = (tmp_path / "books").resolve()
        for out in (first, again):
            assert Path(out["data_root"]).is_absolute() and Path(out["data_root"]).resolve() == root
            assert Path(out["token_file"]).is_absolute() and Path(out["mcp_config"]).is_absolute()
        args = json.loads((project / ".mcp.json").read_text())["mcpServers"]["bookflow"]["args"]
        token_path = args[args.index("--token-file") + 1]
        assert Path(token_path).is_absolute() and Path(token_path).resolve() == root / "agents" / "assistant.token"

        # The owner's checklist: a password through the link, then daily backups, each with its command.
        steps = {x["step"]: x for x in again["owner_checklist"]}
        assert not steps["password"]["done"] and "owner-link" in steps["password"]["command"]
        assert not steps["backups"]["done"] and "bookflow backup schedule" in steps["backups"]["command"]
        assert "--data-root" in steps["backups"]["command"] and "owner-link" in json.dumps(again["next_steps"])

        # Acting as the agent straight away, with no MCP client: attributed as an MCP call is.
        shown = cli("agent", "run", "company show")
        assert shown["company_id"] == first["company"]["company_id"]
        assert {x["step"] for x in shown["owner_steps"]} == {"password", "backups"}
        refused = cli("agent", "run", "customer create", "--input", '{"name": "Maple St HOA"}', ok=False)
        assert refused["code"] == "E_REASON_REQUIRED"
        previewed = cli("agent", "run", "customer create", "--input", '{"name": "Maple St HOA"}', "--reason", "New customer", "--dry-run")
        assert previewed.get("dry_run") is True
        made = cli("agent", "run", "customer create", "--input", '{"name": "Maple St HOA"}', "--reason", "New customer")
        assert made["id"]
        [event] = cli("agent", "run", "audit list", "--input", '{"command": "customer create", "limit": 5}')["items"]
        assert event["actor_kind"] == "agent" and event["interface"] == "mcp"
        assert event["on_behalf_of_name"] == "Dana Ruiz"

        # A fresh owner link replaces the old one, opens once, and is refused once a password is set.
        link = cli("owner-link", "--data-root", "../books", "--bind", f"127.0.0.1:{port}")
        assert link["username"] == "dana" and link["host_running"] is True
        assert _get(again["owner_link"]["url"]) == 410 and _get(link["url"]) == 200
        status, body, _ = _post(link["url"], {"password": "garden-path-2026", "again": "garden-path-2026"})
        assert status == 200 and body["username"] == "dana"
        refused = cli("owner-link", "--data-root", "../books", "--bind", f"127.0.0.1:{port}", ok=False)
        assert refused["code"] == "E_VALIDATION"
        assert [x["step"] for x in cli("agent", "run", "company show")["owner_steps"]] == ["backups"]
    finally:
        if pid:
            os.kill(pid, signal.SIGINT)
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    break
                time.sleep(0.1)


# ---------------------------------------------------------------- a person's decisions survive a rerun (review F1-F4)

def _setup(client, root, project, **extra):
    # A free port, so a Bookflow host already running on this machine (the default 8765) never answers for this one.
    data = {"company": COMPANY, "owner_name": "Dana Ruiz", "serve": False, "bind": f"127.0.0.1:{_free_port()}",
            "mcp_config": str(project / ".mcp.json"), **extra}
    return client.run("setup", data)


def _steps(out):
    return {s["step"]: s["status"] for s in out["steps"]}


def _role(client, out):
    rows = client.run("membership list", {"user": out["agent"]["username"], "company": out["company"]["company_id"]})["items"]
    return [m["role"] for m in rows if m["active"]]


@pytest.fixture
def books(tmp_path, monkeypatch):
    import bookflow
    root = tmp_path / "books"
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setenv("BOOKFLOW_DATA_ROOT", str(root))
    monkeypatch.delenv("BOOKFLOW_COMPANY", raising=False)
    return bookflow.connect(data_root=str(root)), root, project


@pytest.mark.timeout(600)
def test_a_rerun_keeps_a_narrowed_role_and_never_reassigns_or_reauthorizes(books):
    client, root, project = books
    first = _setup(client, root, project)
    assert first["agent"]["role"] == "standard" and _role(client, first) == ["standard"]
    owner = first["owner"]["username"]

    # The owner narrows the agent to read-only; a plain rerun keeps it so and does not reauthorize it.
    client.run("membership grant", {"user": "assistant", "company": first["company"]["company_id"], "role": "readonly"},
               reason="owner narrows")
    narrowed = client.run("agent show", {"agent": "assistant"})["authority"]
    again = _setup(client, root, project)
    assert _role(client, again) == ["readonly"] and again["agent"]["role"] == "readonly"
    after = client.run("agent show", {"agent": "assistant"})["authority"]
    assert after["suspended"] == narrowed["suspended"] and after["fresh_context_ack_at"] == narrowed["fresh_context_ack_at"]
    if narrowed["suspended"]:
        assert _steps(again)["authorized"] == "stopped" and "--acknowledge-fresh-context" in json.dumps(again["next_steps"]) \
            or _steps(again)["authorized"] == "stopped"

    # The owner unassigns it; a rerun asking for admin changes nothing and says how a person restores it.
    shown = client.run("agent show", {"agent": "assistant"})
    client.run("agent unassign", {"agent": "assistant", "principal": owner,
                                  "expected_version": shown["authority"]["version"]}, reason="owner stops the agent")
    stopped = _setup(client, root, project, role="admin")
    assert _steps(stopped)["works for"] == "stopped" and stopped["agent"]["stopped"]
    assert any(f"bookflow agent assign assistant --principal {owner}" in n for n in stopped["next_steps"])
    assert client.run("agent show", {"agent": "assistant"})["principals"] == []
    assert _role(client, stopped) == ["readonly"]


@pytest.mark.timeout(600)
def test_a_revoked_token_stays_revoked_until_a_person_asks_and_the_config_keeps_its_mode(books):
    client, root, project = books
    first = _setup(client, root, project)
    config = project / ".mcp.json"
    os.chmod(config, 0o600)
    meta = json.loads((root / "agents" / "assistant.json").read_text())
    client.run("token revoke", {"token": meta["token_id"]}, reason="owner revokes the agent's token")
    again = _setup(client, root, project, bind="127.0.0.1:18799")  # the server entry changes, the file is rewritten
    assert _steps(again)["token"] == "stopped" and _steps(again)["mcp config"] == "updated"
    assert oct(config.stat().st_mode & 0o777) == "0o600"
    live = client.run("token list", {"user": first["agent"]["user_id"]})["items"]
    assert live == []
    renewed = _setup(client, root, project, bind="127.0.0.1:18799", new_token=True)
    assert _steps(renewed)["token"] == "updated"
    assert len(client.run("token list", {"user": first["agent"]["user_id"]})["items"]) == 1
    assert not list(project.glob(".*.tmp")) and not list((root / "agents").glob(".*.tmp"))


def test_a_token_path_that_is_a_directory_is_a_clean_error(books, tmp_path):
    from bookflow import BookflowError
    from bookflow.adapters.mcp.launcher import read_token_file
    client, root, project = books
    (root / "agents" / "assistant.token").mkdir(parents=True)
    with pytest.raises(BookflowError) as setup_error:
        _setup(client, root, project)
    assert setup_error.value.code == "E_VALIDATION"
    with pytest.raises(BookflowError) as launcher_error:
        read_token_file(str(root / "agents" / "assistant.token"))
    assert launcher_error.value.code == "E_UNAUTHENTICATED"


def test_the_token_file_beats_an_exported_token_with_one_warning(tmp_path, monkeypatch, capsys):
    from bookflow.adapters.mcp.launcher import launcher_secret
    from bookflow.commands.mcp_cmds import McpInput
    token = tmp_path / "agent.token"
    token.write_text("from-the-file\n")
    os.chmod(token, 0o600)
    monkeypatch.setenv("BOOKFLOW_TOKEN", "from-the-shell")
    assert launcher_secret(McpInput(token_file=str(token))) == "from-the-file"
    assert capsys.readouterr().err.count("ignoring BOOKFLOW_TOKEN") == 1
    assert launcher_secret(McpInput()) == "from-the-shell"


def test_setup_will_not_take_another_program_for_its_host(books):
    import http.server
    import threading
    from bookflow import BookflowError
    client, root, project = books

    class Health(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args):
            pass

    stranger = http.server.HTTPServer(("127.0.0.1", 0), Health)
    threading.Thread(target=stranger.serve_forever, daemon=True).start()
    try:
        with pytest.raises(BookflowError) as error:
            _setup(client, root, project, serve=True, bind=f"127.0.0.1:{stranger.server_address[1]}")
        assert error.value.code == "E_IO" and "not this data root's host" in error.value.message
    finally:
        stranger.shutdown()


# ---------------------------------------------------------------- R183 run 3: opening balance, entity type, clean stdout

@pytest.mark.timeout(600)
def test_setup_posts_the_opening_balance_once_and_sets_the_entity_type(books):
    client, root, project = books
    # R183 run 4: without --opening-balance, the next steps give the exact `agent run` that records one.
    bare = _setup(client, root, project)
    [hint] = [n for n in bare["next_steps"] if "opening balance" in n]
    command = hint.split(": ", 1)[1]
    assert command.startswith('bookflow agent run "journal post" --mcp-config ') and command.endswith("--json")
    entry = json.loads(shlex.split(command)[shlex.split(command).index("--input") + 1])
    assert [(line["account"], line["side"]) for line in entry["lines"]] == [("Checking", "debit"), ("Opening Balance Equity", "credit")]
    assert entry["date"].endswith("-01-01")
    # And `setup --help` shows it near the top: in the description and as the fourth option.
    from bookflow.commands.setup_cmds import SetupInput, setup_cmd
    assert "--opening-balance ACCOUNT=AMOUNT" in setup_cmd.description
    assert list(SetupInput.model_fields)[:5] == ["company", "currency", "chart", "opening_balance", "opening_date"]
    first = _setup(client, root, project, entity_type="sole_proprietor", opening_balance="Checking=5,000.00")
    assert not [n for n in first["next_steps"] if "agent run \"journal post\"" in n]
    company = first["company"]["company_id"]
    opening = first["opening_balance"]
    assert opening["status"] == "created" and opening["amount"] == "5000.00" and opening["account"] == "Checking"
    assert opening["date"].endswith("-01-01"), "the books start on the first day of the fiscal year"
    # The bare run above made the company, so this one sets its type on an existing company.
    assert _steps(first)["entity type"] == "updated" and _steps(first)["opening balance"] == "created"
    assert "opening balance" in first["content"] and "5000.00 USD" in first["content"]
    assert client.run("company show", {}, company=company)["info"]["entity_type"] == "sole_proprietor"
    balances = {a["full_name"]: a["balance"] for a in client.run("account list", {}, company=company)["items"]}
    assert balances["Checking"]["amount"] == "5000.00" and balances["Opening Balance Equity"]["amount"] in ("5000.00", "-5000.00")
    # The move-in: the owner's review does not list it.
    assert client.run("report entries-to-review", {}, company=company)["count"] == 0

    # A rerun never posts it twice, even with another amount; it reports the balance the account has,
    # and keeps an entity type a person chose.
    again = _setup(client, root, project, entity_type="llc", opening_balance="Checking=6000", opening_date="2026-03-01")
    assert again["opening_balance"]["status"] == "exists" and again["opening_balance"]["amount"] == "5000.00"
    assert again["opening_balance"]["transaction_id"] == opening["transaction_id"]
    assert "journal update" in [s for s in again["steps"] if s["step"] == "opening balance"][0]["detail"]
    assert _steps(again)["entity type"] == "exists"
    assert client.run("company show", {}, company=company)["info"]["entity_type"] == "sole_proprietor"
    found = client.run("transaction find", {"account": opening["account_id"]}, company=company)["items"]
    assert len(found) == 1

    # A company left at `other` takes the type a rerun gives; an account that is not a bank is refused.
    client.run("company update", {"entity_type": "other"}, company=company, reason="owner resets it")
    later = _setup(client, root, project, entity_type="llc")
    assert _steps(later)["entity type"] == "updated"
    assert not [n for n in later["next_steps"] if "opening balance" in n], "the books already have one"
    from bookflow import BookflowError
    with pytest.raises(BookflowError) as refused:
        _setup(client, root, project, opening_balance="Savings Jar=10")
    assert refused.value.code == "E_VALIDATION" and "Checking" in json.dumps(refused.value.details)


def test_agent_run_stdout_is_one_json_document_even_with_a_warning(tmp_path, monkeypatch, capsys):
    """R183 run 3: a warning line once broke an agent's JSON parse. Stdout holds the result alone."""
    from types import SimpleNamespace
    from bookflow.adapters.cli import app as cli
    from bookflow.adapters.mcp import launcher

    token = tmp_path / "agent.token"
    token.write_text("secret\n")
    os.chmod(token, 0o600)
    config = tmp_path / ".mcp.json"
    config.write_text(json.dumps({"mcpServers": {"bookflow": {"command": "bookflow", "args": [
        "mcp", "--url", "http://127.0.0.1:9/", "--token-file", str(token)], "env": {"BOOKFLOW_COMPANY": "C1"}}}}))

    async def serve(*_args, **_kwargs):
        print("chatter a library printed on the way")  # never reaches stdout
        return SimpleNamespace(structured_content={"company_id": "C1", "warnings": ["the period is closed"]},
                               content=[], is_error=False)

    monkeypatch.setattr(launcher, "serve", serve)
    for extra in ([], ["--json"]):
        monkeypatch.setattr(sys, "argv", ["bookflow", "agent", "run", "company show", "--mcp-config", str(config), *extra])
        cli.main()
        out = capsys.readouterr()
        assert json.loads(out.out) == {"company_id": "C1", "warnings": ["the period is closed"]}
        assert "warning: the period is closed" in out.err and "chatter" in out.err
