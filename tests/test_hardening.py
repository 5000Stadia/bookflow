"""Findings from the row 1 artifact critiques, each kept red-able."""

import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
import sqlalchemy as sa

import bookflow
from bookflow import BookflowError
from bookflow.hub import schema as h
from bookflow.storage.engine import open_database
from tests.conftest import BIN, as_user, make_actor


@pytest.mark.parametrize("name", ["Joe's Plumbing #2", "Fifty % Off", "Why?", "Sp ace", "Group #1"])
def test_uri_unsafe_names(client, root, name):
    org = client.organization.new(name=f"Org {name}")
    co = client.company.new(legal_name=f"{name} LLC", display_name=name, home_currency="USD", organization=org["organization_id"], timezone="UTC")
    folder = Path(co["path"])
    assert (folder / "company.db").exists(), "database must be inside the company folder"
    strays = [p for p in folder.parent.iterdir() if p.is_file() and p.name != "bookflow-organization.toml"]
    assert strays == [], strays
    shown = client.company.show(company=co["company_id"])
    assert shown["info"]["legal_name"] == f"{name} LLC"


def test_data_root_with_hash(tmp_path):
    r = tmp_path / "weird#root"
    c = bookflow.connect(data_root=str(r)); c.init()
    assert (r / "hub.db").exists() and not (tmp_path / "weird").exists()
    c.demo.reset()
    assert c.company.list()["count"] == 1


def test_errors_redacted_for_non_admin(client, root):
    client.organization.new(name="Org R")
    a = client.company.new(legal_name="R Co", home_currency="USD", organization="Org R", timezone="UTC")
    make_actor(root, "rmember", org_role=(a["organization_id"], "admin"))
    m = as_user(root, "rmember")
    shutil.move(a["path"], a["path"] + ".gone")
    with pytest.raises(BookflowError) as e:
        m.company.show(company=a["company_id"])
    assert e.value.code == "E_COMPANY_MISSING" and e.value.details["path"] is None
    shutil.move(a["path"] + ".gone", a["path"])
    with pytest.raises(BookflowError) as e:
        client.company.show(company=a["company_id"] + "x")
    # admin sees paths in details where they exist
    (Path(a["path"]) / "company.db").unlink()
    with pytest.raises(BookflowError) as e:
        client.company.show(company=a["company_id"])
    assert e.value.code == "E_COMPANY_MISSING" and e.value.details["check"] == "database" and e.value.details["path"]


def test_missing_and_corrupt_database(client, root):
    cid = client.company.list()["items"][0]["company_id"]
    p = Path(client.company.show(company=cid)["path"]) / "company.db"
    p.unlink()
    with pytest.raises(BookflowError) as e:
        client.company.rename(name="Blanked", company=cid)
    assert e.value.code == "E_COMPANY_MISSING"
    assert not p.exists(), "a writable open must never create a database"
    p.write_bytes(b"garbage" * 100)
    for call in (lambda: client.company.show(company=cid), lambda: client.company.rename(name="X", company=cid)):
        with pytest.raises(BookflowError) as e:
            call()
        assert e.value.code == "E_IO" and e.value.details["errno"] in ("ENOTDB", "EIO")
    out = client.upgrade()
    assert out["companies_failed"] and out["companies_failed"][0]["code"] == "E_IO"


def test_os_errors_are_io(client, root, monkeypatch):
    lock = root / "root.lock"
    lock.chmod(0o400)
    try:
        with pytest.raises(BookflowError) as e:
            client.company.list()
        assert e.value.code in ("E_IO", "E_DB_BUSY")
    finally:
        lock.chmod(0o600)
    (root / "hub.db").chmod(0o400)
    try:
        with pytest.raises(BookflowError) as e:
            client.organization.new(name="Nope")
        assert e.value.code == "E_IO" and "INSERT" not in e.value.message and "Nope" not in json.dumps(e.value.details)
    finally:
        (root / "hub.db").chmod(0o600)
    import bookflow.storage.paths as paths
    real = os.mkdir
    def boom(p, *a, **k):
        if str(p).endswith("attachments"):
            raise PermissionError(13, "denied")
        return real(p, *a, **k)
    monkeypatch.setattr(os, "mkdir", boom)
    with pytest.raises(BookflowError) as e:
        client.company.new(legal_name="Fault Co", home_currency="USD", organization="Demo Holdings LLC", timezone="UTC")
    assert e.value.code == "E_IO" and e.value.details["errno"] == "EACCES"
    monkeypatch.undo()
    assert not (root / "organizations" / "Demo Holdings LLC" / "Fault Co").exists()


def test_config_malformed_table(client, root):
    (root / "config.toml").write_text('[users]\nk = 5\n')
    with pytest.raises(BookflowError) as e:
        client.company.list()
    assert e.value.code == "E_CONFIG_INVALID"


def test_client_has_no_login_parameter(root):
    from bookflow.client import Client
    with pytest.raises(TypeError):
        Client(data_root=str(root), login="somebody")


def test_since_until_in_viewer_zone(client, root):
    events = client.hub.audit.list()["items"]
    newest = events[0]
    assert client.hub.audit.list(since=newest["at"])["count"] == 1
    assert client.hub.audit.list(until=newest["at"])["count"] == len(events) - 1
    today = newest["at"][:10]
    assert client.hub.audit.list(since=today)["count"] == len(events)
    assert client.hub.audit.list(since="2099-01-01")["count"] == 0
    with pytest.raises(BookflowError) as e:
        client.hub.audit.list(since="yesterday")
    assert e.value.code == "E_VALIDATION"


def test_reason_length_validated(client, cli):
    with pytest.raises(BookflowError) as e:
        client.organization.new(name="Long", reason="x" * 141)
    assert e.value.code == "E_VALIDATION" and e.value.details["fields"][0]["field"] == "reason"
    err, code = cli.error("organization", "new", "--name", "Long", "--reason", "x" * 141)
    assert err["code"] == "E_VALIDATION"


def test_upgrade_dry_run_and_reporting(client, root):
    before = list((root / "backups").iterdir())
    out = client.upgrade(dry_run=True)
    assert out["dry_run"] and out["hub_migrated"] is False
    assert list((root / "backups").iterdir()) == before
    real = client.upgrade()
    assert real["hub_migrated"] is False and real["companies_skipped"]


def test_synthetic_migration(client, root, tmp_path):
    """Add a temporary company revision, migrate a behind-head company, and check events, marker, and backup."""
    import bookflow.storage.migrate as migrate
    versions = Path(migrate.__file__).parent / "company_migrations" / "versions"
    tmp = versions / "9999_synthetic.py"
    base = migrate.HEADS["company"]
    tmp.write_text(f'"""synthetic\n\nRevision ID: co9999\nRevises: {base}\n"""\nfrom alembic import op\nimport sqlalchemy as sa\nrevision = "co9999"\ndown_revision = "{base}"\n\ndef upgrade():\n    op.add_column("principals", sa.Column("synthetic", sa.String(4), nullable=True))\n\ndef downgrade():\n    pass\n')
    old_heads = dict(migrate.HEADS)
    try:
        migrate.HEADS["company"] = "co9999"
        migrate.known_revisions.cache_clear() if hasattr(migrate.known_revisions, "cache_clear") else None
        cid = client.company.list()["items"][0]["company_id"]
        with pytest.raises(BookflowError) as e:
            client.company.show(company=cid)
        assert e.value.code == "E_SCHEMA_BEHIND" and "upgrade" in e.value.message
        dry = client.upgrade(dry_run=True)
        assert dry["dry_run"] and dry["companies_migrated"] == [cid]
        path0 = Path(client.company.list()["items"][0]["path"])
        assert not list((path0 / "backups").iterdir()), "a dry run migrates nothing"
        out = client.upgrade()
        assert out["companies_migrated"] == [cid]
        path = Path(client.company.show(company=cid)["path"])
        assert list((path / "backups").iterdir())
        marker = (path / "bookflow-company.toml").read_text()
        assert 'schema_revision = "co9999"' in marker
        ev = client.hub.audit.list(command="upgrade")["items"][0]
        assert ev["actor_kind"] == "human", "the upgrade command is the human's; the migration itself is recorded in the company by the system user"
        shown = client.hub.audit.show(event=ev["id"])
        assert any(en["action"] == "migrate" for en in shown["entries"])
        assert client.company.list()["items"][0]["schema_revision"] == "co9999"
        import sqlite3 as _sq
        conn = _sq.connect(str(path / "company.db"))
        rows = conn.execute("SELECT e.actor_kind, e.on_behalf_of, n.action FROM audit_events e JOIN audit_entries n ON n.event_id = e.id WHERE e.command = 'upgrade'").fetchall()
        principals = {r[0] for r in conn.execute("SELECT user_id FROM principals").fetchall()}
        conn.close()
        assert any(r[0] == "system" and r[1] == client.init()["user_id"] and r[2] == "migrate" for r in rows)
        assert client.init()["user_id"] in principals, "the triggering user is mirrored with the system actor"
    finally:
        tmp.unlink()
        migrate.HEADS.clear(); migrate.HEADS.update(old_heads)
        for p in versions.glob("__pycache__/9999*"):
            p.unlink()


def test_portability_names_are_real(client, root, tmp_path, monkeypatch):
    src = client.company.show(company="Demo Plumbing Co")
    assert src["info_created_by_name"] == "k" or src["info_created_by_name"]
    r2 = tmp_path / "r2"
    monkeypatch.setenv("BOOKFLOW_DATA_ROOT", str(r2))
    c2 = bookflow.connect(data_root=str(r2)); c2.init(username="other", display_name="Other Person"); c2.organization.new(name="Demo Holdings LLC")
    dst = r2 / "organizations" / "Demo Holdings LLC" / "Demo Plumbing Co"
    shutil.copytree(src["path"], dst)
    c2.company.attach(path=str(dst))
    s2 = c2.company.show(company="Demo Plumbing Co")
    assert s2["info_created_by_name"] is not None and s2["info_created_by_name"] == src["info_created_by_name"]
    assert s2["registered_by_name"] == "Other Person"
    with open_database(dst / "company.db", writable=False) as db:
        rows = db.raw.execute("SELECT user_id, display_name FROM principals").fetchall()
    assert any(r[1] == src["info_created_by_name"] for r in rows)


def test_parity_interfaces_differ(client, cli):
    cli.json("organization", "new", "--name", "Via CLI")
    client.organization.new(name="Via Lib")
    evs = {e["summary"]: e["interface"] for e in client.hub.audit.list(command="organization new")["items"]}
    assert evs["created organization Via CLI"] == "cli" and evs["created organization Via Lib"] == "python"


def test_help_metadata(cli):
    h = cli.run("company", "new", "--help").stdout
    assert "--legal-name TEXT" in h and "Required." in h
    assert "--fiscal-year-start-month INT" in h and "Default: 1." in h
    assert "--report-basis TEXT" in h and "One of: accrual" in h
    rh = cli.run("company", "rename", "--help").stdout
    assert "--move" in rh and "--no-move" in rh
    p = cli.run(expect=0)
    assert "Usage:" in p.stdout


def test_data_root_conflict(cli, root):
    err, code = cli.error("--data-root", "/nonexistent", "company", "list", "--data-root", str(root))
    assert err["code"] == "E_USAGE" and code == 2


def test_recovery_by_any_writable_command(client, root):
    """A pending move is finished by the first writable open, with a version bump and a move event."""
    cid = client.company.list()["items"][0]["company_id"]
    with open_database(root / "hub.db", writable=False) as db:
        row = dict(db.conn.execute(sa.select(h.companies).where(h.companies.c.id == cid)).mappings().first())
    new_rel = row["path"].rsplit("/", 1)[0] + "/Any Writer"
    with open_database(root / "hub.db", writable=True) as db:
        db.raw.execute("BEGIN IMMEDIATE")
        db.conn.execute(h.companies.update().where(h.companies.c.id == cid).values(pending_path=new_rel))
        db.raw.execute("COMMIT")
    before_events = client.hub.audit.list(command="company move")["count"]
    client.company.rename(name="Unrelated Name", company=cid)  # writable, not --move
    with open_database(root / "hub.db", writable=False) as db:
        after = dict(db.conn.execute(sa.select(h.companies).where(h.companies.c.id == cid)).mappings().first())
    assert after["path"] == new_rel and after["pending_path"] is None and (root / new_rel).exists()
    assert after["version"] >= row["version"] + 2
    assert client.hub.audit.list(command="company move")["count"] == before_events + 1


def test_org_hop_completed_by_rerun(client, root):
    org = client.organization.list()["items"][0]
    with open_database(root / "hub.db", writable=False) as db:
        orow = dict(db.conn.execute(sa.select(h.organizations).where(h.organizations.c.id == org["organization_id"])).mappings().first())
    parent, name = orow["path"].rsplit("/", 1)
    new_rel = f"{parent}/{name.upper()}"
    hop = root / f"{new_rel}.moving-{orow['id']}"
    with open_database(root / "hub.db", writable=True) as db:
        db.raw.execute("BEGIN IMMEDIATE")
        db.conn.execute(h.organizations.update().where(h.organizations.c.id == orow["id"]).values(display_name=name.upper(), name_key=name.lower(), pending_path=new_rel))
        db.raw.execute("COMMIT")
    shutil.move(root / orow["path"], hop)
    out = client.organization.rename(organization=orow["id"], name=name.upper(), move=True)
    assert out["moved"] and not hop.exists() and (root / new_rel).exists()
    ev = client.hub.audit.list(command="organization move")["items"][0]
    assert ev["entry_count"] == 2, "the organization row and the one company row it rewrote"


@pytest.mark.parametrize("platform,fn,mocked,expected", [
    ("darwin", "_macos_fs_type", "apfs", "apfs"),
    ("darwin", "_macos_fs_type", "smbfs", None),
    ("win32", "_windows_fs_type", "ntfs", "ntfs"),
    ("win32", "_windows_fs_type", "remote", None),
])
def test_other_platforms_mocked(monkeypatch, tmp_path, platform, fn, mocked, expected):
    import bookflow.core.fs as fs
    monkeypatch.setattr(fs.sys, "platform", platform)
    monkeypatch.setattr(fs, fn, lambda p: mocked)
    if expected:
        assert fs.check_local(tmp_path) == expected
    else:
        with pytest.raises(BookflowError) as e:
            fs.check_local(tmp_path)
        assert e.value.code == "E_NETWORK_SHARE"


def test_wheel_contains_data(tmp_path):
    out = subprocess.run(["uv", "build", "--wheel", "-o", str(tmp_path), "-q"], cwd=str(Path(__file__).resolve().parents[1]), capture_output=True, text=True, env={**os.environ, "PATH": os.environ["PATH"] + ":" + str(Path.home() / ".local/bin")})
    assert out.returncode == 0, out.stderr
    import zipfile
    wheel = next(tmp_path.glob("*.whl"))
    assert wheel.name.startswith("bookflow_core-")
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        metadata_name = next(name for name in names if name.endswith(".dist-info/METADATA"))
        entry_points_name = next(name for name in names if name.endswith(".dist-info/entry_points.txt"))
        metadata = archive.read(metadata_name).decode()
        entry_points = archive.read(entry_points_name).decode()
    assert "bookflow/data/currencies.csv" in names
    assert "bookflow/documentation/resources/concepts.md" in names
    assert "bookflow/documentation/resources/agent-guide.md" in names
    assert "Name: bookflow-core\n" in metadata
    assert "bookflow = bookflow.adapters.cli.app:main" in entry_points

    generated = tmp_path / "wheel-docs"
    script = """
import json
import sys
sys.path.insert(0, sys.argv[1])
import bookflow
assert '.whl/' in bookflow.__file__.replace('\\\\', '/')
from bookflow.documentation.generate import generate_docs
print(json.dumps(generate_docs(sys.argv[2], False)))
"""
    reproduced = subprocess.run(
        [sys.executable, "-c", script, str(wheel), str(generated)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert reproduced.returncode == 0, reproduced.stderr
    assert json.loads(reproduced.stdout) == sorted(
        path.relative_to(generated).as_posix()
        for path in generated.rglob("*")
        if path.is_file()
    )


def test_sdist_excludes_local_agent_metadata(tmp_path):
    import tarfile

    repo = Path(__file__).resolve().parents[1]
    source = tmp_path / "source"
    shutil.copytree(
        repo,
        source,
        ignore=shutil.ignore_patterns(
            ".git",
            ".venv",
            ".pytest_cache",
            "__pycache__",
            "dist",
            "notes",
        ),
    )
    (source / ".agentpost.toml").write_text(
        "[identity]\nmailbox = 'must-not-ship'\n",
        encoding="utf-8",
    )
    output = tmp_path / "dist"
    built = subprocess.run(
        ["uv", "build", "--sdist", "-o", str(output), "-q"],
        cwd=source,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "PATH": os.environ["PATH"] + ":" + str(Path.home() / ".local/bin"),
        },
    )
    assert built.returncode == 0, built.stderr
    archive = next(output.glob("*.tar.gz"))
    with tarfile.open(archive, "r:gz") as package:
        names = package.getnames()
    assert not any(Path(name).name == ".agentpost.toml" for name in names)


def test_sibling_companies_hidden_in_hub_audit(client, root):
    client.organization.new(name="Org S")
    a = client.company.new(legal_name="S One", home_currency="USD", organization="Org S", timezone="UTC")
    b = client.company.new(legal_name="S Two", home_currency="USD", organization="Org S", timezone="UTC")
    client.organization.rename(organization="Org S", name="Org S Moved", move=True)
    make_actor(root, "sone", company_role=(a["company_id"], "standard"))
    m = as_user(root, "sone")
    events = m.hub.audit.list()["items"]
    assert events, "the member sees the events about their own company"
    for e in events:
        assert "S Two" not in e["summary"], e["summary"]
        shown = m.hub.audit.show(event=e["id"])
        for en in shown["entries"]:
            assert en["record_id"] != b["company_id"]
            for snap in (en["before"], en["after"]):
                assert not snap or "S Two" not in json.dumps(snap)
        assert shown["entry_count"] == len(shown["entries"])
    two_event = [e for e in client.hub.audit.list(command="company new")["items"] if "S Two" in e["summary"]][0]
    with pytest.raises(BookflowError):
        m.hub.audit.show(event=two_event["id"])


def test_init_goes_through_the_boundary(tmp_path, monkeypatch):
    r = tmp_path / "r"
    c = bookflow.connect(data_root=str(r))
    with pytest.raises(BookflowError) as e:
        c.init(reason="x" * 141)
    assert e.value.code == "E_VALIDATION" and e.value.details["fields"][0]["field"] == "reason"
    real = Path.mkdir
    def boom(self, *a, **k):
        if self.name == "r":
            raise PermissionError(13, "denied")
        return real(self, *a, **k)
    monkeypatch.setattr(Path, "mkdir", boom)
    with pytest.raises(BookflowError) as e:
        c.init()
    assert e.value.code == "E_IO" and e.value.details["errno"] == "EACCES"


def test_principals_mirror_every_company_write(client, root):
    client.organization.new(name="Org P")
    a = client.company.new(legal_name="P Co", home_currency="USD", organization="Org P", timezone="UTC")
    make_actor(root, "padmin", org_role=(a["organization_id"], "admin"))
    as_user(root, "padmin").company.rename(name="P Co Renamed", company=a["company_id"])
    with open_database(Path(a["path"]) / "company.db", writable=False) as db:
        names = {r[0] for r in db.raw.execute("SELECT username FROM principals").fetchall()}
    assert "padmin" in names


def test_non_admin_summaries_have_no_paths(client, root):
    client.organization.new(name="Visible Org")
    a = client.company.new(legal_name="Visible Co", home_currency="USD", organization="Visible Org", timezone="UTC")
    client.company.rename(name="Moved Visible Co", move=True, company=a["company_id"])
    client.organization.rename(organization="Visible Org", name="Visible Org Moved", move=True)
    make_actor(root, "vmember", org_role=(a["organization_id"], "standard"))
    m = as_user(root, "vmember")
    for e in m.hub.audit.list()["items"]:
        assert "organizations/" not in e["summary"] and "/tmp" not in e["summary"], e["summary"]
        shown = m.hub.audit.show(event=e["id"])
        assert "organizations/" not in shown["summary"]


def test_org_move_versions_company_rows(client, root):
    client.organization.new(name="Ver Org")
    a = client.company.new(legal_name="Ver Co", home_currency="USD", organization="Ver Org", timezone="UTC")
    before = client.company.show(company=a["company_id"])["version"]
    client.organization.rename(organization="Ver Org", name="Ver Org Two", move=True)
    after = client.company.show(company=a["company_id"])
    assert after["version"] == before + 1 and "Ver Org Two" in after["path"]
    ev = client.hub.audit.list(command="organization move")["items"][0]
    entries = client.hub.audit.show(event=ev["id"])["entries"]
    assert any(en["record_type"] == "company" and en["record_id"] == a["company_id"] and en["version_after"] == before + 1 for en in entries)


def test_options_before_noun_and_verb(cli, root):
    out = cli.json("--dry-run", "organization", "new", "--name", "Early Dry")
    assert out["dry_run"] is True and cli.json("organization", "list")["count"] == 1
    cli.json("--reason", "early", "--source-ref", "ref-1", "organization", "new", "--name", "Early Reason")
    ev = cli.json("hub", "audit", "list", "--command", "organization new")["items"][0]
    assert ev["reason"] == "early" and ev["source_ref"] == "ref-1"
    assert cli.json("--company", "Demo Plumbing Co", "company", "show")["display_name"] == "Demo Plumbing Co"
    err, code = cli.error("--dry-run", "organization", "list")
    assert err["code"] == "E_USAGE"
    err, code = cli.error("--reason", "a", "organization", "new", "--name", "X", "--reason", "b")
    assert err["code"] == "E_USAGE"
    err, code = cli.error("--company", "Demo Plumbing Co", "organization", "list")
    assert err["code"] == "E_USAGE"
