"""Second-round artifact findings, each with a witness that reaches the guarded branch."""

import json
import os
import shutil
import sqlite3
from pathlib import Path

import pytest
import sqlalchemy as sa

import bookflow
from bookflow import BookflowError
from bookflow.hub import schema as h
from bookflow.storage.engine import open_database
from tests.conftest import as_user, make_actor


def _hub(root, writable=False):
    return open_database(root / "hub.db", writable=writable)


def _set_org_pending(root, org_id, new_rel, **extra):
    with _hub(root, True) as db:
        db.raw.execute("BEGIN IMMEDIATE")
        db.conn.execute(h.organizations.update().where(h.organizations.c.id == org_id).values(pending_path=new_rel, **extra))
        db.raw.execute("COMMIT")


def test_hub_commands_complete_pending_org_move(client, root):
    """A moved-but-uncommitted organization is finished by any writable hub command; reads report the folder that exists."""
    org = client.organization.list()["items"][0]
    with _hub(root) as db:
        orow = dict(db.conn.execute(sa.select(h.organizations).where(h.organizations.c.id == org["organization_id"])).mappings().first())
    new_rel = "organizations/Demo Moved Elsewhere"
    _set_org_pending(root, orow["id"], new_rel)
    shutil.move(root / orow["path"], root / new_rel)
    # read-only hub commands work and report the effective folder
    assert client.company.list()["count"] == 1
    assert Path(client.company.show(company="Demo Plumbing Co")["path"]).exists()
    # a writable hub command that never touches the company completes the move
    client.organization.new(name="Unrelated")
    with _hub(root) as db:
        o2 = dict(db.conn.execute(sa.select(h.organizations).where(h.organizations.c.id == orow["id"])).mappings().first())
        c2 = dict(db.conn.execute(sa.select(h.companies).where(h.companies.c.organization_id == orow["id"])).mappings().first())
    assert o2["path"] == new_rel and o2["pending_path"] is None and c2["path"].startswith(new_rel + "/")
    out = client.company.new(legal_name="After Move Co", home_currency="USD", organization=orow["id"], timezone="UTC")
    assert (Path(out["path"]) / "company.db").exists() and new_rel in out["path"]
    assert client.upgrade()["companies_missing"] == []


def test_org_move_rebases_child_pending(client, root):
    cid = client.company.list()["items"][0]["company_id"]
    with _hub(root) as db:
        crow = dict(db.conn.execute(sa.select(h.companies).where(h.companies.c.id == cid)).mappings().first())
        orow = dict(db.conn.execute(sa.select(h.organizations).where(h.organizations.c.id == crow["organization_id"])).mappings().first())
    child_pending = crow["path"].rsplit("/", 1)[0] + "/Pending Child"
    with _hub(root, True) as db:
        db.raw.execute("BEGIN IMMEDIATE")
        db.conn.execute(h.companies.update().where(h.companies.c.id == cid).values(pending_path=child_pending))
        db.raw.execute("COMMIT")
    client.organization.rename(organization=orow["id"], name="Rebased Org", move=True)
    with _hub(root) as db:
        c2 = dict(db.conn.execute(sa.select(h.companies).where(h.companies.c.id == cid)).mappings().first())
    assert c2["pending_path"] == "organizations/Rebased Org/Pending Child"
    out = client.company.rename(name="Pending Child", move=True, company=cid)
    assert out["moved"] and out["path"].endswith("/Rebased Org/Pending Child")


def test_demo_reset_ignores_stale_rename_pending(client, root):
    org = client.organization.list()["items"][0]
    _set_org_pending(root, org["organization_id"], "organizations/Demo Renamed")
    out = client.demo.reset()
    assert out["trashed_path"] and "/trash/" in out["trashed_path"]
    assert not (root / "organizations" / "Demo Renamed").exists()
    assert [p.name for p in (root / "organizations").iterdir()] == ["Demo Holdings LLC"]


def test_demo_reset_dry_run_preview_matches_real(client, root):
    preview = client.demo.reset(dry_run=True)
    real = client.demo.reset()
    assert preview["path"] == real["path"]


def test_init_dry_run_on_initialized_root(client, root):
    shutil.rmtree(root / "trash")
    out = client.init(dry_run=True)
    assert out["dry_run"] is True and out["created"] is False
    assert not (root / "trash").exists(), "a dry run creates nothing"


def test_dry_run_never_completes_a_pending_move(client, root):
    cid = client.company.list()["items"][0]["company_id"]
    with _hub(root) as db:
        crow = dict(db.conn.execute(sa.select(h.companies).where(h.companies.c.id == cid)).mappings().first())
    new_rel = crow["path"].rsplit("/", 1)[0] + "/Dry Pending"
    with _hub(root, True) as db:
        db.raw.execute("BEGIN IMMEDIATE")
        db.conn.execute(h.companies.update().where(h.companies.c.id == cid).values(pending_path=new_rel))
        db.raw.execute("COMMIT")
    events_before = client.hub.audit.list()["count"]
    client.company.rename(name="Dry Name", company=cid, dry_run=True)
    with _hub(root) as db:
        c2 = dict(db.conn.execute(sa.select(h.companies).where(h.companies.c.id == cid)).mappings().first())
    assert c2["pending_path"] == new_rel and c2["version"] == crow["version"] and not (root / new_rel).exists()
    assert client.hub.audit.list()["count"] == events_before


def test_rename_copy_failure_is_a_warning(client, root):
    cid = client.company.list()["items"][0]["company_id"]
    folder = Path(client.company.show(company=cid)["path"])
    (folder / "bookflow-company.toml").chmod(0o400)
    folder.chmod(0o500)
    try:
        out = client.company.rename(name="RO Name", company=cid)
    finally:
        folder.chmod(0o700)
        (folder / "bookflow-company.toml").chmod(0o600)
    assert out["display_name"] == "RO Name" and out["warnings"] and "copy" in out["warnings"][0]
    assert client.company.show(company=cid)["display_name"] == "RO Name"


def test_not_found_suggestions(client, root):
    with pytest.raises(BookflowError) as e:
        client.company.show(company="Demo Plumbng Co")
    assert e.value.details["suggestions"] == ["Demo Plumbing Co"]
    with pytest.raises(BookflowError) as e:
        client.organization.show(organization="Demo Holdngs")
    assert e.value.details["suggestions"] == ["Demo Holdings LLC"]
    client.organization.new(name="Other"); client.company.new(legal_name="Demo Plumbing Co", home_currency="USD", organization="Other", timezone="UTC")
    with pytest.raises(BookflowError) as e:
        client.company.show(company="Demo Plumbing Co")
    assert sorted(e.value.details["suggestions"]) == ["Demo Holdings LLC/Demo Plumbing Co", "Other/Demo Plumbing Co"]


def test_open_database_never_creates(tmp_path):
    with pytest.raises(BookflowError) as e:
        with open_database(tmp_path / "nope.db", writable=True):
            pass
    assert e.value.code == "E_IO" and e.value.details["errno"] == "ENOENT" and not (tmp_path / "nope.db").exists()


def test_sibling_entries_filtered_on_visible_event(client, root):
    client.organization.new(name="Org F")
    a = client.company.new(legal_name="F One", home_currency="USD", organization="Org F", timezone="UTC")
    b = client.company.new(legal_name="F Two", home_currency="USD", organization="Org F", timezone="UTC")
    make_actor(root, "fone", company_role=(a["company_id"], "standard"))
    client.organization.rename(organization="Org F", name="Org F Moved", move=True)  # entries for both companies
    m = as_user(root, "fone")
    ev = [e for e in m.hub.audit.list(command="organization move")["items"]]
    assert ev, "the company-only member sees the event about their organization"
    shown = m.hub.audit.show(event=ev[0]["id"])
    ids = {en["record_id"] for en in shown["entries"]}
    assert a["company_id"] in ids and b["company_id"] not in ids and shown["entry_count"] == len(shown["entries"]) == 2


def test_own_entryless_events_visible(client, root):
    client.organization.new(name="Org O")
    a = client.company.new(legal_name="O Co", home_currency="USD", organization="Org O", timezone="UTC")
    make_actor(root, "omember", company_role=(a["company_id"], "standard"))
    m = as_user(root, "omember")
    m.company.use(company=a["company_id"])
    assert any(e["command"] == "company use" for e in m.hub.audit.list()["items"])


def test_creator_is_owner(client):
    client.organization.new(name="Org W")
    a = client.company.new(legal_name="W Co", home_currency="USD", organization="Org W", timezone="UTC")
    with _hub(Path(os.environ["BOOKFLOW_DATA_ROOT"])) as db:
        m = db.conn.execute(sa.select(h.memberships).where(h.memberships.c.scope_id == a["company_id"])).mappings().first()
    assert m and m["role"] == "owner" and m["scope_type"] == "company"


def test_date_bounds_use_the_viewer_zone(client, root):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from bookflow.core.dispatch import parse_when
    assert parse_when("2026-09-03", "Pacific/Kiritimati") == "2026-09-02T10:00:00.000Z"
    assert parse_when("2026-09-03", "Etc/GMT+12") == "2026-09-03T12:00:00.000Z"
    assert parse_when("2026-09-03", None) == "2026-09-03T00:00:00.000Z"
    for zone in ("Pacific/Kiritimati", "Etc/GMT+12"):
        with _hub(root, True) as db:
            db.raw.execute("BEGIN IMMEDIATE")
            db.conn.execute(h.users.update().where(h.users.c.hub_admin.is_(True)).values(timezone=zone))
            db.raw.execute("COMMIT")
        total = client.hub.audit.list()["count"]
        local_today = datetime.now(ZoneInfo(zone)).date().isoformat()
        from datetime import timedelta
        local_yesterday = (datetime.now(ZoneInfo(zone)).date() - timedelta(days=1)).isoformat()
        assert client.hub.audit.list(since=local_today)["count"] == total, "everything happened today in the viewer's zone"
        assert client.hub.audit.list(until=local_today)["count"] == total, "until names the end of that day"
        assert client.hub.audit.list(until=local_yesterday)["count"] == 0


def test_checkpoint_on_close(client, root):
    p = Path(client.company.show(company="Demo Plumbing Co")["path"]) / "company.db"
    client.company.rename(name="WAL Co", company="Demo Plumbing Co")
    wal = p.with_name(p.name + "-wal")
    assert not wal.exists() or wal.stat().st_size == 0


def test_display_name_copy_repaired_on_write(client, root):
    cid = client.company.list()["items"][0]["company_id"]
    p = Path(client.company.show(company=cid)["path"]) / "company.db"
    conn = sqlite3.connect(str(p)); conn.execute("UPDATE company_info SET display_name = 'stale'"); conn.commit(); conn.close()
    client.company.rename(name="Repaired Co", company=cid)
    conn = sqlite3.connect(str(p)); assert conn.execute("SELECT display_name FROM company_info").fetchone()[0] == "Repaired Co"; conn.close()


def test_legacy_summary_redacted_whole(client, root):
    client.organization.new(name="Org L")
    a = client.company.new(legal_name="L Co", home_currency="USD", organization="Org L", timezone="UTC")
    make_actor(root, "lmember", company_role=(a["company_id"], "standard"))
    with _hub(root, True) as db:
        db.raw.execute("BEGIN IMMEDIATE")
        db.conn.execute(h.audit_events.update().where(h.audit_events.c.command == "company new").values(summary="moved company L Co to organizations/Org L/L Co (2)"))
        db.raw.execute("COMMIT")
    m = as_user(root, "lmember")
    ev = [e for e in m.hub.audit.list()["items"] if e["command"] == "company new"][0]
    assert ev["summary"] == "moved company L Co to <path>"


def test_bootstrap_dbapi_error_is_io(tmp_path, monkeypatch):
    import bookflow.hub.users as users
    def boom(*a, **k):
        raise sa.exc.OperationalError("INSERT secret", {}, sqlite3.OperationalError("attempt to write a readonly database"))
    monkeypatch.setattr(users, "create_system_user", boom)
    c = bookflow.connect(data_root=str(tmp_path / "r"))
    with pytest.raises(BookflowError) as e:
        c.init()
    assert e.value.code == "E_IO" and "INSERT" not in e.value.message and "secret" not in json.dumps(e.value.details)


def test_empty_strings_are_null(client):
    out = client.company.new(legal_name="Blank Co", home_currency="USD", organization="Demo Holdings LLC", timezone="UTC", industry="", address={"country": ""})
    info = client.company.show(company=out["company_id"])["info"]
    assert info["industry"] is None and info["address_country"] == "US" and info["legal_address_country"] == "US"


def test_client_unknown_command_is_usage(client):
    with pytest.raises(BookflowError) as e:
        client.company.frobnicate()
    assert e.value.code == "E_USAGE"


def test_dangling_mapping_repaired_by_init(client, root):
    cfg = (root / "config.toml").read_text()
    (root / "config.toml").write_text(cfg.replace(client.init()["user_id"], "01ARZ3NDEKTSV4RRFFQ69G5FAV"))
    with pytest.raises(BookflowError) as e:
        client.company.list()
    assert e.value.code == "E_NO_ACTOR" and "no longer exists" in e.value.message
    assert client.init()["created"] is False
    assert client.company.list()["count"] == 1


def test_no_op_rename_leaves_no_trace(client, root):
    client.organization.new(name="Org N")
    a = client.company.new(legal_name="N Co", home_currency="USD", organization="Org N", timezone="UTC")
    make_actor(root, "nadmin", org_role=(a["organization_id"], "admin"))
    events = client.hub.audit.list()["count"]
    as_user(root, "nadmin").company.rename(name="N Co", company=a["company_id"])
    assert client.hub.audit.list()["count"] == events
    with open_database(Path(a["path"]) / "company.db", writable=False) as db:
        names = {r[0] for r in db.raw.execute("SELECT username FROM principals").fetchall()}
    assert "nadmin" not in names, "a no-op write mirrors nobody"


def test_pending_org_recovery_respects_authorization(client, root):
    """A non-admin's command never completes a move of an organization they cannot see, and a rejected command leaves everything untouched."""
    client.organization.new(name="Org Mine"); client.organization.new(name="Secret Client LLC")
    a = client.company.new(legal_name="Mine Co", home_currency="USD", organization="Org Mine", timezone="UTC")
    with _hub(root) as db:
        secret = dict(db.conn.execute(sa.select(h.organizations).where(h.organizations.c.display_name == "Secret Client LLC")).mappings().first())
    _set_org_pending(root, secret["id"], "organizations/Secret Moved")
    shutil.move(root / secret["path"], root / "organizations/Secret Moved")
    make_actor(root, "mineadmin", company_role=(a["company_id"], "admin"))
    make_actor(root, "minero", company_role=(a["company_id"], "readonly"))
    as_user(root, "mineadmin").company.rename(name="Mine Co Two", company=a["company_id"])
    with _hub(root) as db:
        still = dict(db.conn.execute(sa.select(h.organizations).where(h.organizations.c.id == secret["id"])).mappings().first())
    assert still["pending_path"] == "organizations/Secret Moved", "an invisible organization was not touched"
    with pytest.raises(BookflowError):
        as_user(root, "minero").company.rename(name="Nope", company=a["company_id"])
    for e in as_user(root, "mineadmin").hub.audit.list()["items"]:
        assert "Secret" not in e["summary"]
    client.company.list()  # hub admin, read-only: still untouched
    client.organization.new(name="Trigger")  # hub admin write completes it, attributed to the system user on the admin's behalf
    with _hub(root) as db:
        done = dict(db.conn.execute(sa.select(h.organizations).where(h.organizations.c.id == secret["id"])).mappings().first())
    assert done["pending_path"] is None and done["path"] == "organizations/Secret Moved"
    ev = client.hub.audit.list(command="organization move")["items"][0]
    assert ev["actor_kind"] == "system" and ev["on_behalf_of_name"] == "k"


def test_overlapping_pending_moves_resolve_read_only(client, root):
    cid = client.company.list()["items"][0]["company_id"]
    with _hub(root) as db:
        crow = dict(db.conn.execute(sa.select(h.companies).where(h.companies.c.id == cid)).mappings().first())
        orow = dict(db.conn.execute(sa.select(h.organizations).where(h.organizations.c.id == crow["organization_id"])).mappings().first())
    co_new = crow["path"].rsplit("/", 1)[0] + "/New Company"
    with _hub(root, True) as db:
        db.raw.execute("BEGIN IMMEDIATE")
        db.conn.execute(h.companies.update().where(h.companies.c.id == cid).values(pending_path=co_new))
        db.raw.execute("COMMIT")
    shutil.move(root / crow["path"], root / co_new)  # company moved, not committed
    _set_org_pending(root, orow["id"], "organizations/New Org")
    shutil.move(root / orow["path"], root / "organizations/New Org")  # organization moved, not committed
    assert Path(client.company.list()["items"][0]["path"]) == root / "organizations/New Org/New Company"
    assert client.company.show(company=cid)["company_id"] == cid
    client.organization.new(name="Trigger")  # completes both
    with _hub(root) as db:
        c2 = dict(db.conn.execute(sa.select(h.companies).where(h.companies.c.id == cid)).mappings().first())
    assert c2["pending_path"] in (None, "organizations/New Org/New Company")
    assert client.company.rename(name="Settled", company=cid)["path"] == str(root / "organizations/New Org/New Company")


def test_open_hook_repairs_display_copy(client, root):
    cid = client.company.list()["items"][0]["company_id"]
    p = Path(client.company.show(company=cid)["path"]) / "company.db"
    conn = sqlite3.connect(str(p)); conn.execute("UPDATE company_info SET display_name = 'stale'"); conn.commit(); conn.close()
    client.company.update(industry="Repair", company=cid)  # a company write that does not touch the copy itself
    conn = sqlite3.connect(str(p)); assert conn.execute("SELECT display_name FROM company_info").fetchone()[0] == "Demo Plumbing Co"; conn.close()


def test_help_lists_output_fields_and_errors(cli):
    h = cli.run("company", "show", "--help").stdout
    assert "Output fields:" in h and "info" in h and "E_COMPANY_MISSING" in h and "E_COMPANY_NOT_FOUND" in h


def test_audit_table_shows_the_handle(cli):
    out = cli.run("hub", "audit", "list").stdout
    first = out.splitlines()[2].split()[0]
    assert cli.json("hub", "audit", "show", first)["id"] == first


def test_org_new_output_has_write_fields(client):
    out = client.organization.new(name="Fields Org")
    assert out["dry_run"] is False and out["warnings"] == [] and out["idempotent_replay"] is False


def test_rollout_failure_after_folder_is_named(client, root, monkeypatch):
    import bookflow.hub.companies as companies
    def boom(*a, **k):
        raise sqlite3.OperationalError("disk I/O error")
    monkeypatch.setattr(companies, "register", boom)
    with pytest.raises(BookflowError) as e:
        client.company.new(legal_name="Half Made Co", home_currency="USD", organization="Demo Holdings LLC", timezone="UTC")
    assert e.value.code == "E_ROLLOUT_INCOMPLETE" and e.value.details["state"] == "unregistered" and e.value.details["path"].endswith("Half Made Co")
    monkeypatch.undo()
    out = client.company.attach(path=e.value.details["path"])
    assert out["display_name"] == "Half Made Co"


def test_attach_tightens_modes(client, root, tmp_path, monkeypatch):
    src = client.company.show(company="Demo Plumbing Co")["path"]
    r2 = tmp_path / "r2"
    monkeypatch.setenv("BOOKFLOW_DATA_ROOT", str(r2))
    c2 = bookflow.connect(data_root=str(r2)); c2.init(); c2.organization.new(name="Demo Holdings LLC")
    dst = r2 / "organizations" / "Demo Holdings LLC" / "Demo Plumbing Co"
    shutil.copytree(src, dst)
    dst.chmod(0o755); (dst / "company.db").chmod(0o644)
    out = c2.company.attach(path=str(dst))
    assert out["warnings"] and "tightened" in out["warnings"][0]
    import stat
    assert stat.S_IMODE(dst.stat().st_mode) == 0o700 and stat.S_IMODE((dst / "company.db").stat().st_mode) == 0o600


def test_stuck_org_move_warns_admin_only(client, root):
    client.organization.new(name="Stuck Org")
    with _hub(root) as db:
        orow = dict(db.conn.execute(sa.select(h.organizations).where(h.organizations.c.display_name == "Stuck Org")).mappings().first())
    _set_org_pending(root, orow["id"], "organizations/Stuck Moved")
    shutil.move(root / orow["path"], root / "organizations" / "Parked Elsewhere")  # neither old nor new exists
    out = client.organization.new(name="Unrelated Two")
    assert out["warnings"] and "Stuck Org" in out["warnings"][0]
    assert client.organization.list()["count"] >= 3


def test_data_root_that_is_a_file(tmp_path):
    f = tmp_path / "file"; f.write_text("x")
    with pytest.raises(BookflowError) as e:
        bookflow.connect(data_root=str(f)).company.list()
    assert e.value.code == "E_IO" and e.value.details["errno"] == "ENOTDIR"


def test_client_signature_has_no_actor_parameter():
    import inspect
    from bookflow.client import Client, connect
    for fn in (Client.__init__, connect):
        assert not {"login", "act_as", "user", "actor"} & set(inspect.signature(fn).parameters)
