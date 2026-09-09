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

BIN = Path(sys.executable).parent / "bookflow"


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


@pytest.fixture
def root(tmp_path, monkeypatch, _seeded_template):
    """A fresh, initialized data root with the demo organization and company."""
    r = tmp_path / "root"
    monkeypatch.setenv("BOOKFLOW_DATA_ROOT", str(r))
    monkeypatch.delenv("BOOKFLOW_COMPANY", raising=False)
    shutil.copytree(_seeded_template, r)
    return r


@pytest.fixture
def client(root):
    return bookflow.connect(data_root=str(root))


@pytest.fixture(scope="session")
def public_deposit_world(_seeded_template, tmp_path_factory):
    """One produced company shared by the public deposit detail witnesses.

    Building it runs the genuine deposit lifecycle several times, so it is built
    once per session and only when a test asks for it. Each module takes the
    data-root lock for itself and gives it back.
    """
    from tests import deposit_public_support as support
    world = support.build(_seeded_template, tmp_path_factory)
    try:
        yield world
    finally:
        support.stop(world)


class Cli:
    def __init__(self, root: Path):
        self.root = root

    def run(self, *args: str, env: dict | None = None, expect: int | None = 0):
        e = {**os.environ, "BOOKFLOW_DATA_ROOT": str(self.root), **(env or {})}
        p = subprocess.run([str(BIN), *args], capture_output=True, text=True, env=e)
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
    """Test-only fixture standing in for row 7's `user add` and `membership grant`."""
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
