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

#: The packaged launcher every CLI witness in this suite runs. One owner names
#: it (tests/provenance.py), so a test cannot quietly run a different build.
BIN = Path(provenance.launcher())


@pytest.fixture(scope="session")
def _seeded_template(tmp_path_factory):
    """One initialized, demo-seeded data root, built once and copied per test.

    Rollout since row 5 runs the company migration chain, applies a chart, and installs the
    profile seed manifests, which costs about 2.5 s. Copying the finished tree costs about 2 ms
    and yields a byte-identical root, so every test still gets its own isolated data root.
    """
    src = tmp_path_factory.mktemp("seed") / "root"
    previous = os.environ.get("BOOKFLOW_DATA_ROOT")
    os.environ["BOOKFLOW_DATA_ROOT"] = str(src)
    try:
        c = bookflow.connect(data_root=str(src))
        c.init()
        c.demo.reset()
    finally:
        if previous is None:
            os.environ.pop("BOOKFLOW_DATA_ROOT", None)
        else:
            os.environ["BOOKFLOW_DATA_ROOT"] = previous
    return src


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
    shutil.copytree(_seeded_template, r)
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
