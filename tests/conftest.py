import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

os.environ.setdefault("BOOKFLOW_LOCK_TIMEOUT", "0.2")

import bookflow  # noqa: E402
from bookflow.core import registry  # noqa: E402
from bookflow.core.context import Context, Interface  # noqa: E402
from bookflow.core.dispatch import run as dispatch_run  # noqa: E402
from tests import provenance  # noqa: E402
from tests.demo_oracle import DEMO_AS_OF  # noqa: E402

#: The packaged launcher every CLI witness in this suite runs. One owner names
#: it (tests/provenance.py), so a test cannot quietly run a different build.
BIN = Path(provenance.launcher())


@pytest.fixture(scope="session")
def _seeded_template(tmp_path_factory):
    """One initialized, demo-seeded data root, built once per run and copied per test.

    `init` + `demo reset` runs the company migration chain, applies a chart and seeds the demo
    story, which takes minutes under load. Copying the finished tree takes milliseconds and yields
    a byte-identical root, so every test still gets its own isolated data root. Use
    `copy_seeded_root` to take a copy; never write to the template itself.
    """
    src = tmp_path_factory.mktemp("seed") / "root"
    previous = os.environ.get("BOOKFLOW_DATA_ROOT")
    os.environ["BOOKFLOW_DATA_ROOT"] = str(src)
    try:
        c = bookflow.connect(data_root=str(src))
        c.init()
        c.demo.reset(as_of=DEMO_AS_OF)
        del c
    finally:
        if previous is None:
            os.environ.pop("BOOKFLOW_DATA_ROOT", None)
        else:
            os.environ["BOOKFLOW_DATA_ROOT"] = previous
    _checkpoint_databases(src)
    return src


def _checkpoint_databases(root: Path) -> None:
    """Fold every database's write-ahead log into its main file, so a copy never depends on a
    -wal file being copied at the same instant as its database. Nothing holds the root open here."""
    import sqlite3
    for path in root.rglob("*.db"):
        with sqlite3.connect(path) as db:
            busy, _, _ = db.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        assert busy == 0, f"{path} is still open by another connection"
        db.close()
        wal = Path(f"{path}-wal")
        assert not wal.exists() or wal.stat().st_size == 0, f"{wal} was not folded in"


def copy_seeded_root(template: Path, target: Path) -> Path:
    """A private copy of the session's demo root at `target`. Company and organization folders
    are registered by paths relative to the data root, so the copy opens where it lands."""
    shutil.copytree(template, target)
    return target


@pytest.fixture(autouse=True)
def _demo_reset_as_written(monkeypatch):
    """A demo reset inside a test defaults to the day the seed is written as of, not today.

    `demo reset` moves the demo's dates back to the day it runs (R83), so without this the demo
    a test sees -- and every date and fiscal-year figure it pins -- would change with the day the
    suite runs. As of its written day the demo is exactly the seed as written. A test can still
    pass `as_of`; tests/test_demo_dates.py covers the moving itself and the today default. CLI
    and host children run the real default, so a child that pins demo dates passes `--as-of`.
    """
    from datetime import date
    from bookflow.demo import dates
    monkeypatch.setattr(dates, "reset_day", lambda zone: date.fromisoformat(DEMO_AS_OF))


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "legacy_permissions: the test's data root is put back in the never-activated `legacy` "
        "permission mode (an upgraded older install); see make_legacy.")


@pytest.fixture
def root(request, tmp_path, monkeypatch, _seeded_template):
    """A fresh, initialized data root with the demo organization and company.

    `init` starts it in the current permission mode (`policy_v1`), exactly as an install
    does. A test marked `legacy_permissions` exercises rules that only an upgraded,
    never-activated root still follows, and gets that root instead.
    """
    r = tmp_path / "root"
    monkeypatch.setenv("BOOKFLOW_DATA_ROOT", str(r))
    monkeypatch.delenv("BOOKFLOW_COMPANY", raising=False)
    copy_seeded_root(_seeded_template, r)
    if request.node.get_closest_marker("legacy_permissions"):
        make_legacy(r)
    return r


@pytest.fixture
def client(root):
    return bookflow.connect(data_root=str(root))


class Cli:
    def __init__(self, root: Path):
        self.root = root

    def run(self, *args: str, env: dict | None = None, expect: int | None = 0):
        # tests/provenance.py owns what a child is given: the pinned import path
        # for the product this process itself imported, a short list of variables
        # any process needs, and exactly what the caller named here. The parent's
        # environment is not copied, so a credential this test did not mention
        # cannot reach the child and change what it does.
        e = provenance.child_env(BOOKFLOW_DATA_ROOT=str(self.root), **(env or {}))
        try:
            p = subprocess.run([str(BIN), *args], capture_output=True, text=True, env=e,
                               timeout=provenance.CHILD_SECONDS)
        except subprocess.TimeoutExpired as waited:
            # A CLI witness that never returns is not a slow test, it is an
            # unmeasurable one: without this bound the suite waits for as long as
            # anyone lets it. Say which command, and what it had said so far.
            raise provenance.ChildProvenanceError(
                f"`bookflow {' '.join(args)}` did not finish within "
                f"{provenance.CHILD_SECONDS}s and was killed.\n"
                f"stdout: {(waited.stdout or b'')[-1000:]!r}\n"
                f"stderr: {(waited.stderr or b'')[-1000:]!r}") from None
        if expect is not None:
            assert p.returncode == expect, (p.stdout, p.stderr)
        return p

    def json(self, *args: str, env: dict | None = None):
        p = self.run(*args, "--json", env=env)
        return json.loads(p.stdout)

    def error(self, *args: str, env: dict | None = None):
        p = self.run(*args, "--json", env=env, expect=None)
        assert p.returncode != 0, p.stdout
        return json.loads(p.stderr.strip().splitlines()[-1]), p.returncode


@pytest.fixture
def cli(root):
    return Cli(root)


def make_actor(root: Path, username: str, *, hub_admin: bool = False, org_role: tuple[str, str] | None = None,
               company_role: tuple[str, str] | None = None, login: str | None = None,
               kind: str = "human", owner_user_id: str | None = None) -> str:
    """Test-only: insert a user and memberships directly, and map an OS login to them.

    `user add` and `membership grant` now do the first two parts as real commands
    (see tests/test_identity_commands.py); no command maps an OS login, which is what
    keeps this fixture. New tests about identity should use the commands.
    """
    from bookflow.core.config import Config
    from bookflow.core.ids import new_id
    from bookflow.core.session import now_iso
    from bookflow.hub import schema as h
    from bookflow.hub.users import common
    from bookflow.storage.engine import open_database
    login = login or username
    with open_database(root / "hub.db", writable=True) as db:
        uid = new_id()
        db.raw.execute("BEGIN IMMEDIATE")
        db.conn.execute(h.users.insert().values(id=uid, kind=kind, username=username, display_name=username.title(), owner_user_id=owner_user_id,
                                                password_hash=None, hub_admin=hub_admin, timezone=None, active=True, **common(uid, "system")))
        for scope, grant in (("organization", org_role), ("company", company_role)):
            if grant:
                db.conn.execute(h.memberships.insert().values(id=new_id(), user_id=uid, scope_type=scope, scope_id=grant[0], role=grant[1], granted_by=uid, granted_at=now_iso(), revoked_at=None))
        db.raw.execute("COMMIT")
    cfg = Config.load(root / "config.toml")
    cfg.set_user(login, uid)
    cfg.save()
    return uid


def as_user(root: Path, login: str) -> "bookflow.Client":
    """Test-only: act as another mapped login. The public Client has no such parameter (blueprint 4.4)."""
    from bookflow.client import Client
    c = Client(data_root=str(root))
    c._login = login
    return c


def make_legacy(root: Path) -> None:
    """Test-only: put a root back in the `legacy` permission mode an older install upgrades into.

    `init` now starts every new root in the current permission mode, so a never-activated
    root exists only as an upgraded one. Tests of that state build it here, the same way
    tests/test_permission_activation.py does: mode and catalog cleared, frozen role defaults.
    """
    import sqlite3
    from bookflow.hub import permission_catalog as c
    with sqlite3.connect(root / "hub.db") as db:
        db.execute("UPDATE permission_state SET mode='legacy',catalog_version=NULL,catalog_sha256=NULL,catalog_json=NULL")
        db.execute("DELETE FROM role_capabilities")
        db.executemany("INSERT INTO role_capabilities VALUES (?,?,?)",
                       [(x.role, x.requirement.capability, x.requirement.threshold) for x in c.FROZEN_DEFAULTS])


def add_membership(root: Path, user_id: str, scope_type: str, scope_id: str, role: str) -> str:
    """Test-only: write one active membership row directly, as make_actor does for a new user."""
    from bookflow.core.ids import new_id
    from bookflow.core.session import now_iso
    from bookflow.hub import schema as h
    from bookflow.storage.engine import open_database
    membership_id = new_id()
    with open_database(root / "hub.db", writable=True) as db:
        db.raw.execute("BEGIN IMMEDIATE")
        db.conn.execute(h.memberships.insert().values(id=membership_id, user_id=user_id, scope_type=scope_type,
                                                      scope_id=scope_id, role=role, granted_by=user_id,
                                                      granted_at=now_iso(), revoked_at=None))
        db.raw.execute("COMMIT")
    return membership_id


def make_agent(call, username: str, *, principals, company: str, role: str = "standard",
               owner: str | None = None, display_name: str | None = None) -> str:
    """Create an authorized agent through the public agent commands (row 7), and return its id.

    `call(name, body)` runs one command as a human installation administrator and returns its
    document: `hosted_call(hosted)`, `office_call(office)`, `browser_call(browser)`, or a library
    client's `run`. The agent acts for `principals` (humans with identical permissions) and holds
    `role` on `company`. Issue its token with `token issue --user <agent> --principal <human>`.
    """
    agent = call("agent create", {"username": username, **({"owner": owner} if owner else {}),
                                  **({"display_name": display_name} if display_name else {})})["agent"]["agent_id"]
    for person in ([principals] if isinstance(principals, str) else principals):
        call("agent assign", {"agent": agent, "principal": person, "confirm_permitted_use": True})
    call("membership grant", {"user": agent, "company": company, "role": role})
    call("agent authorize", {"agent": agent, "confirm_permitted_use": True})
    return agent


def hosted_call(hosted):
    """`make_agent`'s caller for a running host: the installer's own bearer over HTTP."""
    return lambda name, body: hosted.ok(name.replace(" ", "."), body)


def browser_call(browser):
    """`make_agent`'s caller through a logged-in workbench browser session."""
    def call(name, body):
        result = browser.evaluate(f"""fetch('/commands/{name.replace(" ", ".")}', {{method:'POST',
          credentials:'same-origin', headers:{{'Content-Type':'application/json','X-Bookflow-Workbench':'1'}},
          body: JSON.stringify({json.dumps(body)})}}).then(async r => ({{status:r.status, body:await r.json()}}))""",
                                  await_promise=True)
        assert result["status"] == 200, result
        return result["body"]
    return call
