"""Row 2 done sequence: two writers, the reason gate, directives, presence, the feed, and the budget."""

import json
import re
import sqlite3
from datetime import timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa

import bookflow
from bookflow import BookflowError
from bookflow.core import clock
from bookflow.hub import schema as h
from bookflow.storage.engine import open_database
from tests.conftest import as_user, make_actor


def _second_admin(root, client, login="second"):
    org = client.organization.list()["items"][0]
    make_actor(root, login, org_role=(org["organization_id"], "admin"))
    return as_user(root, login)


def test_two_writers(client, root, cli):
    other = _second_admin(root, client)
    show = client.company.show(company="Demo Plumbing Co")
    v = show["info_version"]
    # a blind write by the other admin, then a stale versioned write on the same field: conflict
    other.company.update(phone="555-0001", company="Demo Plumbing Co")
    with pytest.raises(BookflowError) as e:
        client.company.update(phone="555-0002", expected_version=v, company="Demo Plumbing Co")
    d = e.value.details
    assert e.value.code == "E_VERSION_CONFLICT" and d["changed_fields"] == ["phone"] and d["updated_by_name"] == "Second" and d["seconds_since_update"] < 60
    # a stale versioned write on a different field merges
    out = client.company.update(email="new@demo.example", expected_version=v, company="Demo Plumbing Co")
    assert out["merged_over_versions"] and out["warnings"] and "merged" in out["warnings"][0]
    # a blind write within the window warns about the other writer
    out = other.company.update(website="https://x.example", company="Demo Plumbing Co")
    assert out["recent_concurrent_activity"] is True and out["previous_updated_by_name"] == "k" and any("changed this record" in w for w in out["warnings"])
    # dry runs report the same outcomes
    with pytest.raises(BookflowError) as e:
        client.company.update(phone="555-0003", expected_version=v, company="Demo Plumbing Co", dry_run=True)
    assert e.value.code == "E_VERSION_CONFLICT"
    pre = client.company.update(fax="1", expected_version=v, company="Demo Plumbing Co", dry_run=True)
    assert pre["dry_run"] and pre["merged_over_versions"]
    # the audit list shows both writers with actor and interface
    events = client.audit.list(company="Demo Plumbing Co", command="company update")["items"]
    assert {(e["actor_name"], e["interface"]) for e in events} >= {("k", "python"), ("Second", "python")}
    # same rules through the CLI
    err, code = cli.error("company", "update", "--phone", "555-0004", "--expected-version", str(v), "--company", "Demo Plumbing Co")
    assert err["code"] == "E_VERSION_CONFLICT" and "phone" in err["details"]["changed_fields"]
    p = cli.run("company", "update", "--industry", "Plumbing", "--company", "Demo Plumbing Co", "--json")
    assert "warning:" in p.stderr and json.loads(p.stdout)["recent_concurrent_activity"] is True


def test_missing_history_forces_conflict(client, root):
    v = client.company.show(company="Demo Plumbing Co")["info_version"]
    client.company.update(phone="1", company="Demo Plumbing Co")
    p = Path(client.company.show(company="Demo Plumbing Co")["path"]) / "company.db"
    conn = sqlite3.connect(str(p))
    conn.execute("UPDATE company_info SET version = version + 1")  # a version with no audit entry
    conn.commit(); conn.close()
    with pytest.raises(BookflowError) as e:
        client.company.update(email="a@b.c", expected_version=v, company="Demo Plumbing Co")
    assert e.value.code == "E_VERSION_CONFLICT" and e.value.details.get("unknown_versions")


def test_noop_and_null_and_clear(client, cli):
    show = client.company.show(company="Demo Plumbing Co")
    out = client.company.update(phone=show["info"]["phone"], company="Demo Plumbing Co")
    assert out["changed_fields"] == [] and out["version"] == show["info_version"]
    out = client.company.update(phone=None, address={"line2": "Suite 5"}, company="Demo Plumbing Co")
    info = client.company.show(company="Demo Plumbing Co")["info"]
    assert info["phone"] is None and info["address_line2"] == "Suite 5" and info["address_line1"] == "100 Main St"
    cli.json("company", "update", "--clear", "address", "--company", "Demo Plumbing Co")
    info = client.company.show(company="Demo Plumbing Co")["info"]
    assert info["address_line1"] is None and info["legal_address_line1"] == "100 Main St"
    err, _ = cli.error("company", "update", "--clear", "bogus", "--company", "Demo Plumbing Co")
    assert err["code"] == "E_VALIDATION"
    err, _ = cli.error("company", "update", "--clear", "phone", "--phone", "1", "--company", "Demo Plumbing Co")
    assert err["code"] == "E_VALIDATION"
    with pytest.raises(BookflowError) as e:
        client.company.update(tax_id_kind="ssn", company="Demo Plumbing Co")  # stored tax id has the EIN shape
    assert e.value.code == "E_VALIDATION" and e.value.details["fields"][0]["field"] == "tax_id"


def test_closing_date_needs_admin(client, root):
    org = client.organization.list()["items"][0]
    make_actor(root, "std", org_role=(org["organization_id"], "standard"))
    with pytest.raises(BookflowError) as e:
        as_user(root, "std").company.update(closing_date="2025-12-31", company="Demo Plumbing Co")
    assert e.value.code == "E_PERMISSION"
    client.company.update(closing_date="2025-12-31", company="Demo Plumbing Co")
    assert client.company.show(company="Demo Plumbing Co")["info"]["closing_date"] == "2025-12-31"


def test_legal_name_updates_projection_and_partial_write(client, root, monkeypatch):
    client.company.update(legal_name="Demo Plumbing Company, LLC", company="Demo Plumbing Co")
    assert client.company.list()["items"][0]["legal_name"] == "Demo Plumbing Company, LLC"
    import bookflow.core.dispatch as dispatch
    real = dispatch._repair_projection
    def boom(s, ctx):
        raise sqlite3.OperationalError("disk I/O error")
    monkeypatch.setattr(dispatch, "_repair_projection", boom)
    with pytest.raises(BookflowError) as e:
        client.company.update(legal_name="Demo Plumbing Company II", company="Demo Plumbing Co")
    assert e.value.code == "E_PARTIAL_WRITE" and e.value.details["durable"] == ["company_info"]
    monkeypatch.setattr(dispatch, "_repair_projection", real)
    assert client.company.show(company="Demo Plumbing Co")["info"]["legal_name"] == "Demo Plumbing Company II"
    assert client.company.list()["items"][0]["legal_name"] == "Demo Plumbing Company, LLC", "the projection lags"
    client.company.update(industry="Plumbing", company="Demo Plumbing Co")  # any real write converges it
    assert client.company.list()["items"][0]["legal_name"] == "Demo Plumbing Company II"


def _agent_session(root, client, owner_login="k"):
    """Row 7 fills Session.actor and Context.on_behalf_of from a token; until then tests build them the same way."""
    from bookflow.core.context import Context, Interface
    from bookflow.core.dispatch import run_in_session, _open_hub, _load_actor
    from bookflow.core.session import Actor, Session
    from bookflow.core.config import Config
    from bookflow.core.locks import RootLock
    from bookflow.core.perms import private_umask
    from bookflow.core import registry
    from bookflow.core.ids import new_id
    from bookflow.hub.users import common
    owner = client.init()["user_id"]
    with open_database(root / "hub.db", writable=True) as db:
        aid = new_id()
        db.raw.execute("BEGIN IMMEDIATE")
        db.conn.execute(h.users.insert().values(id=aid, kind="agent", username="claude-agent", display_name="Claude Agent", owner_user_id=owner, password_hash=None, hub_admin=False, timezone=None, active=True, **common(owner, "system")))
        org = db.conn.execute(sa.select(h.organizations)).mappings().first()
        db.conn.execute(h.memberships.insert().values(id=new_id(), user_id=aid, scope_type="organization", scope_id=org["id"], role="admin", granted_by=owner, granted_at=clock.now_iso(), revoked_at=None))
        db.raw.execute("COMMIT")

    def run(name, inp, **ctxkw):
        cmd = registry.get(name)
        ctx = Context.new(Interface.mcp, "test-agent", on_behalf_of=owner, **ctxkw)
        s = Session(data_root=root, os_login="k", config=Config.load(root / "config.toml"))
        with private_umask(), RootLock(root, name):
            _open_hub(s, True, ctx)
            s.actor = Actor(id=aid, kind="agent", username="claude-agent", display_name="Claude Agent", hub_admin=False)
            from bookflow.hub import access
            access.load_memberships(s)
            ctx = ctx.model_copy(update={"actor_id": aid, "actor_kind": "agent"})
            try:
                return run_in_session(cmd, cmd.input_model.model_validate(inp), ctx, s, company_selector="Demo Plumbing Co")
            finally:
                s.close_company()
                s._hub_cm.__exit__(None, None, None)
    return run


def test_agent_reason_gate_and_directives(client, root):
    run = _agent_session(root, client)
    with pytest.raises(BookflowError) as e:
        run("company update", {"phone": "1"})
    assert e.value.code == "E_REASON_REQUIRED" and "--reason" in e.value.message
    run("company update", {"phone": "2"}, reason="owner asked")
    out = run("directive add", {"text": "Post finished jobs"}, reason="standing order from the owner")
    assert out["directive"]["code"] == "SI-3" and out["directive"]["given_by_name"] == "k" and out["directive"]["recorded_by_name"] == "Claude Agent"
    run("company update", {"phone": "3"}, directive_id="si-3")
    ev = client.audit.list(company="Demo Plumbing Co", command="company update")["items"][0]
    assert ev["directive_code"] == "SI-3" and ev["directive_text"] == "Post finished jobs" and ev["actor_name"] == "Claude Agent" and ev["on_behalf_of_name"] == "k"
    client.directive.deactivate(directive="SI-3", company="Demo Plumbing Co")
    with pytest.raises(BookflowError) as e:
        run("company update", {"phone": "4"}, directive_id="SI-3")
    assert e.value.code == "E_DIRECTIVE_INACTIVE"
    with pytest.raises(BookflowError) as e:
        run("company update", {"phone": "4"}, directive_id="SI-99")
    assert e.value.code == "E_DIRECTIVE_NOT_FOUND"
    # the previous-writer fields name the principal
    out = client.company.update(fax="9", company="Demo Plumbing Co")
    assert out["previous_updated_by_name"] == "Claude Agent" and out["previous_on_behalf_of_name"] == "k"


def test_directives_seeded_and_idempotency(client):
    codes = [d["code"] for d in client.directive.list(company="Demo Plumbing Co")["items"]]
    assert codes == ["SI-1", "SI-2"]
    a = client.directive.add(text="Retry me", company="Demo Plumbing Co", idempotency_key="k1")
    b = client.directive.add(text="Retry me", company="Demo Plumbing Co", idempotency_key="k1")
    assert a["directive"]["id"] == b["directive"]["id"] and b["idempotent_replay"] is True and a["idempotent_replay"] is False
    assert len(client.directive.list(company="Demo Plumbing Co")["items"]) == 3
    with pytest.raises(BookflowError) as e:
        client.directive.add(text="Different", company="Demo Plumbing Co", idempotency_key="k1")
    assert e.value.code == "E_IDEMPOTENCY_MISMATCH"
    with pytest.raises(BookflowError) as e:
        client.company.update(phone="1", company="Demo Plumbing Co", idempotency_key="k2")
    assert e.value.code == "E_USAGE"
    dry = client.directive.add(text="Retry me", company="Demo Plumbing Co", idempotency_key="k1", dry_run=True)
    assert dry["dry_run"] and dry["idempotent_replay"]


def test_presence(client, root, monkeypatch):
    cid = client.company.list()["items"][0]["company_id"]
    before = client.audit.list(company=cid)["count"]
    out = client.presence.set(record_type="company_info", record_id=cid, company=cid)
    assert out["editing_by"][0]["name"] == "k" and out["editing_by"][0]["interface"] == "python"
    assert client.company.show(company=cid)["editing_by"][0]["user_id"] == client.init()["user_id"]
    assert client.audit.list(company=cid)["count"] == before, "presence is never audited"
    t0 = clock.now()
    monkeypatch.setattr(clock, "now", lambda: t0 + timedelta(seconds=120))
    assert client.company.show(company=cid)["editing_by"] == []
    monkeypatch.undo()
    with pytest.raises(BookflowError) as e:
        client.presence.set(record_type="directive", record_id="01ARZ3NDEKTSV4RRFFQ69G5FAV", company=cid)
    assert e.value.code == "E_RECORD_NOT_FOUND"
    client.presence.clear(record_type="company_info", record_id=cid, company=cid)
    assert client.company.show(company=cid)["editing_by"] == []


def test_tail_and_cursors(client, cli):
    first = client.audit.tail(company="Demo Plumbing Co", after=0)
    assert first["count"] >= 3 and [e["seq"] for e in first["items"]] == sorted(e["seq"] for e in first["items"])
    cursor = first["next_after"]
    assert client.audit.tail(company="Demo Plumbing Co")["count"] == 0, "no cursor means only new events"
    client.company.update(phone="tail", company="Demo Plumbing Co")
    nxt = client.audit.tail(company="Demo Plumbing Co", after=cursor)
    assert nxt["count"] == 1 and nxt["items"][0]["command"] == "company update"
    page = client.audit.list(company="Demo Plumbing Co", limit=2)
    assert page["count"] == 2 and page["next_before"] is not None
    page2 = client.audit.list(company="Demo Plumbing Co", limit=2, before=page["next_before"])
    assert page2["items"][0]["seq"] < page["items"][-1]["seq"]
    shown = client.audit.show(company="Demo Plumbing Co", event=nxt["items"][0]["id"])
    assert shown["entries"][0]["diff"]["phone"]["after"] == "tail"
    err, _ = cli.error("audit", "tail", "--company", "Demo Plumbing Co", "--since", "nonsense")
    assert err["code"] == "E_VALIDATION"


def test_tax_id_hashed_in_snapshots(client):
    client.company.update(tax_id="98-7654321", company="Demo Plumbing Co")
    ev = client.audit.list(company="Demo Plumbing Co", command="company update")["items"][0]
    shown = client.audit.show(company="Demo Plumbing Co", event=ev["id"])
    d = shown["entries"][0]["diff"]["tax_id"]
    assert d["before"].startswith("sha256:") and d["after"].startswith("sha256:") and "98-7654321" not in json.dumps(shown)


def test_principals_on_copy_and_baseline(client, root, tmp_path, monkeypatch):
    other = _second_admin(root, client)
    other.company.update(phone="copy", company="Demo Plumbing Co")
    src = client.company.show(company="Demo Plumbing Co")
    r2 = tmp_path / "r2"
    monkeypatch.setenv("BOOKFLOW_DATA_ROOT", str(r2))
    c2 = bookflow.connect(data_root=str(r2)); c2.init(username="third"); c2.organization.new(name="Demo Holdings LLC")
    import shutil
    dst = r2 / "organizations" / "Demo Holdings LLC" / "Demo Plumbing Co"
    shutil.copytree(src["path"], dst)
    c2.company.attach(path=str(dst))
    ev = c2.audit.list(company="Demo Plumbing Co", command="company update")["items"][0]
    assert ev["actor_name"] == "Second"
    out = c2.company.update(fax="c", company="Demo Plumbing Co")
    assert out["previous_updated_by_name"] == "Second"
    create = [e for e in c2.audit.list(company="Demo Plumbing Co", command="company new")["items"]]
    assert create and c2.audit.show(company="Demo Plumbing Co", event=create[0]["id"])["entries"][0]["action"] == "create"


def test_append_only_across_codebase():
    src = Path(__file__).resolve().parents[1] / "src" / "bookflow"
    offenders = []
    for p in src.rglob("*.py"):
        if p.name == "audit.py" or "migrations" in p.parts:
            continue
        text = p.read_text()
        if re.search(r"audit_(events|entries)\.(update|delete)\(", text):
            offenders.append(str(p))
    assert offenders == []


def test_budget(client, root):
    """5,000 creates and 5,000 updates, half versioned; audit tables at most 6x live tables (blueprint 18)."""
    import os
    n = int(os.environ.get("BOOKFLOW_BUDGET_N", "300"))  # the full fixture is 5,000; BOOKFLOW_BUDGET_N=5000 runs it
    cid = client.company.list()["items"][0]["company_id"]
    from bookflow.core import registry
    from bookflow.core.context import Context, Interface
    from bookflow.core.dispatch import run_in_session, _open_hub, _load_actor
    from bookflow.core.session import Session
    from bookflow.core.config import Config
    from bookflow.core.locks import RootLock
    from bookflow.core.perms import private_umask
    s = Session(data_root=root, os_login="k", config=Config.load(root / "config.toml"))
    ctx = Context.new(Interface.python, "budget")
    with private_umask(), RootLock(root, "budget"):
        _open_hub(s, True, ctx); _load_actor(s)
        ctx = ctx.model_copy(update={"actor_id": s.actor.id, "actor_kind": s.actor.kind})
        add, upd, deact = registry.get("directive add"), registry.get("company update"), registry.get("directive deactivate")
        codes = []
        for i in range(n):
            out = run_in_session(add, add.input_model(text=f"Directive number {i} about receipts and invoices"), ctx, s, company_selector=cid)
            codes.append(out["directive"]["code"])
        v = None
        for i in range(n // 2):
            fields = {"phone": f"555-{i:04d}"}
            if i % 2 == 0:
                from bookflow.company.info import read_info
                v = read_info(s.company)["version"]
                fields["expected_version"] = v
            run_in_session(upd, upd.input_model(**fields), ctx, s, company_selector=cid)
        for i in range(n // 2):
            run_in_session(deact, deact.input_model(directive=codes[i]), ctx, s, company_selector=cid)
        db = s.company
        def size(table):
            return db.raw.execute("SELECT sum(pgsize) FROM dbstat WHERE name = ?", (table,)).fetchone()[0] or 0
        live = size("directives") + size("company_info")
        audit = size("audit_events") + size("audit_entries") + size("ix_co_audit_entries_record") + size("ix_co_audit_entries_event")
        s.close_company(); s._hub_cm.__exit__(None, None, None)
    print(f"budget: live {live} bytes, audit {audit} bytes, ratio {audit / live:.2f}")
    assert audit <= 6 * live, f"audit/live = {audit / live:.2f}"
