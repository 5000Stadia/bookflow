"""The row 1 done sequence through the library and the CLI, compared."""

import json
import re
import shutil

import pytest

from bookflow import BookflowError
from tests.conftest import as_user, make_actor

ULID = re.compile(r"^[0-9A-Z]{26}$")
TS = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}[+-]\d{2}:\d{2}$")


def normalize(obj):
    """Replace ULIDs and timestamps with placeholders after checking their shape."""
    if isinstance(obj, dict):
        return {k: normalize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [normalize(v) for v in obj]
    if isinstance(obj, str):
        if ULID.match(obj):
            return "<ulid>"
        if TS.match(obj):
            return "<ts>"
        if "/organizations/" in obj:
            return "<path>" + obj.split("/organizations/", 1)[1]
    return obj


def sequence(run):
    out = []
    out.append(run("organization new", {"name": "Acme Holdings"}))
    out.append(run("company new", {"legal_name": "Acme Plumbing LLC", "home_currency": "USD", "display_name": "Acme Plumbing", "organization": "Acme Holdings", "timezone": "America/Chicago", "address": {"line1": "1 Main St", "city": "Springfield"}}))
    out.append(run("company list", {}))
    out.append(run("company show", {}, company="Acme Holdings/Acme Plumbing"))
    out.append(run("company rename", {"name": "Acme Plumbing Inc"}, company="Acme Holdings/Acme Plumbing"))
    out.append(run("company rename", {"name": "Acme P and H", "move": True}, company="Acme Holdings/Acme Plumbing Inc"))
    out.append(run("company show", {}, company="Acme Holdings/Acme P and H"))
    return out


def test_library_and_cli_agree(tmp_path, monkeypatch):
    import bookflow
    from tests.conftest import Cli
    roots = []
    results = []
    for kind in ("lib", "cli"):
        r = tmp_path / kind
        monkeypatch.setenv("BOOKFLOW_DATA_ROOT", str(r))
        c = bookflow.connect(data_root=str(r)); c.init(); c.demo.reset()
        roots.append(r)
        if kind == "lib":
            results.append(sequence(lambda name, inp, company=None: c.run(name, inp, company=company)))
        else:
            cli = Cli(r)

            def run(name, inp, company=None):
                args = name.split(" ")
                for k, v in inp.items():
                    if isinstance(v, dict):
                        for k2, v2 in v.items():
                            args += [f"--{k}-{k2}".replace("_", "-"), str(v2)]
                    elif isinstance(v, bool):
                        args += [f"--{k}"] if v else []
                    else:
                        args += [f"--{k}".replace("_", "-"), str(v)]
                if company:
                    args += ["--company", company]
                return cli.json(*args)
            results.append(sequence(run))
    lib, cli_out = results
    for a, b in zip(lib, cli_out):
        a, b = normalize(a), normalize(b)
        # the interface field differs by construction
        strip = lambda d: json.loads(json.dumps(d).replace('"cli"', '"python"'))
        assert strip(a) == strip(b)


def test_demo_reset_repeats(client, root):
    first = client.company.list()["items"]
    out = client.demo.reset()
    assert out["trashed_path"] and (root / "trash").exists()
    second = client.company.list()["items"]
    assert [i["display_name"] for i in first] == [i["display_name"] for i in second]
    assert first[0]["company_id"] != second[0]["company_id"]
    assert len(list((root / "trash").iterdir())) == 1
    client.demo.reset()
    assert len(list((root / "trash").iterdir())) == 2


def test_portability(client, root, tmp_path, monkeypatch):
    src = client.company.show(company="Demo Holdings LLC/Demo Plumbing Co")
    show1 = src
    r2 = tmp_path / "second"
    import bookflow
    monkeypatch.setenv("BOOKFLOW_DATA_ROOT", str(r2))
    c2 = bookflow.connect(data_root=str(r2)); c2.init(); c2.organization.new(name="Demo Holdings LLC")
    dst = r2 / "organizations" / "Demo Holdings LLC" / "Demo Plumbing Co"
    shutil.copytree(src["path"], dst)
    out = c2.company.attach(path=str(dst))
    assert out["warnings"] == []
    show2 = c2.company.show(company="Demo Holdings LLC/Demo Plumbing Co")
    excluded = {"organization_id", "organization_name", "path", "access", "role", "registered_by_name", "is_demo", "id", "version", "created_at", "created_by", "created_via", "updated_at", "updated_by", "updated_via"}
    for k in show1:
        if k in excluded:
            continue
        assert show1[k] == show2[k], k
    assert show2["info_created_by_name"] == show1["info_created_by_name"]
    # full dump comparison, directory listing
    import sqlite3
    def dump(p):
        conn = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
        try:
            return "\n".join(conn.iterdump())
        finally:
            conn.close()
    assert dump(f"{src['path']}/company.db") == dump(dst / "company.db")
    listing = lambda p: sorted(x.name for x in __import__("pathlib").Path(p).iterdir() if not x.name.endswith(("-wal", "-shm")) and x.name != "backups")
    assert listing(src["path"]) == listing(dst)


def test_dry_run_writes_nothing(client, root):
    import hashlib
    def state():
        h = hashlib.sha256()
        for p in sorted(root.rglob("*")):
            if p.is_file() and p.name != "root.lock" and not p.name.endswith(("-wal", "-shm")):
                h.update(str(p.relative_to(root)).encode()); h.update(p.read_bytes())
        return h.hexdigest()
    before = state()
    out = client.company.new(legal_name="Dry Co", home_currency="USD", timezone="UTC", dry_run=True)
    assert out["dry_run"] is True and ULID.match(out["company_id"]) and out["path"].endswith("Dry Co")
    assert client.organization.new(name="Dry Org", dry_run=True)["dry_run"] is True
    assert client.company.rename(name="Dry Name", move=True, company="Demo Plumbing Co", dry_run=True)["moved"] is True
    assert client.demo.reset(dry_run=True)["dry_run"] is True
    assert client.company.use(company="Demo Plumbing Co", dry_run=True)["dry_run"] is True
    assert state() == before
    with pytest.raises(BookflowError) as e:
        client.company.list(dry_run=True)
    assert e.value.code == "E_USAGE"


def test_isolation(client, root):
    client.organization.new(name="Org A"); client.organization.new(name="Org B")
    a = client.company.new(legal_name="A Co", home_currency="USD", organization="Org A", timezone="UTC")
    a2 = client.company.new(legal_name="A2 Co", home_currency="USD", organization="Org A", timezone="UTC")
    b = client.company.new(legal_name="B Co", home_currency="USD", organization="Org B", timezone="UTC")
    make_actor(root, "amember", org_role=(a["organization_id"], "standard"))
    make_actor(root, "aonly", company_role=(a["company_id"], "readonly"))
    am, ao = as_user(root, "amember"), as_user(root, "aonly")
    assert [o["display_name"] for o in am.organization.list()["items"]] == ["Org A"]
    assert sorted(i["display_name"] for i in am.company.list()["items"]) == ["A Co", "A2 Co"]
    assert [i["display_name"] for i in ao.company.list()["items"]] == ["A Co"]
    assert ao.organization.list()["items"][0]["access"] == "company"
    for bad in ("B Co", "Org B/B Co", b["company_id"], "Nope"):
        with pytest.raises(BookflowError) as e:
            am.company.show(company=bad)
        assert e.value.code == "E_COMPANY_NOT_FOUND"
        with pytest.raises(BookflowError) as e:
            am.company.use(company=bad)
        assert e.value.code == "E_COMPANY_NOT_FOUND"
    for bad in ("Org B", "Nope"):
        with pytest.raises(BookflowError) as e:
            am.organization.show(organization=bad)
        assert e.value.code == "E_ORGANIZATION_NOT_FOUND"
        with pytest.raises(BookflowError) as e:
            am.company.new(legal_name="X", home_currency="USD", organization=bad, timezone="UTC")
        assert e.value.code == "E_ORGANIZATION_NOT_FOUND"
    with pytest.raises(BookflowError) as e:
        ao.company.show(company="A2 Co")
    assert e.value.code == "E_COMPANY_NOT_FOUND"
    # non-admins never see paths
    assert am.company.show(company="A Co")["path"] is None
    assert am.organization.list()["items"][0]["path"] is None
    # readonly cannot write; standard cannot create companies
    with pytest.raises(BookflowError) as e:
        ao.company.rename(name="Nope", company="A Co")
    assert e.value.code == "E_PERMISSION"
    with pytest.raises(BookflowError) as e:
        am.company.new(legal_name="X", home_currency="USD", organization="Org A", timezone="UTC")
    assert e.value.code == "E_PERMISSION"
    with pytest.raises(BookflowError) as e:
        am.organization.new(name="Mine")
    assert e.value.code == "E_PERMISSION"
    # audit visibility
    events = am.hub.audit.list()["items"]
    assert events and all("B Co" not in e["summary"] and "Org B" not in e["summary"] for e in events)
    b_event = [e for e in client.hub.audit.list()["items"] if "B Co" in e["summary"]][0]["id"]
    with pytest.raises(BookflowError) as e:
        am.hub.audit.show(event=b_event)
    assert e.value.code == "E_EVENT_NOT_FOUND"
    with pytest.raises(BookflowError) as e:
        am.hub.audit.show(event="01ARZ3NDEKTSV4RRFFQ69G5FAV")
    assert e.value.code == "E_EVENT_NOT_FOUND"
    shown = am.hub.audit.show(event=am.hub.audit.list()["items"][0]["id"])
    assert all(("path" not in (en["after"] or {}) or (en["after"] or {}).get("path") is None) for en in shown["entries"])


def test_audit_diff_and_delete_snapshots(client):
    client.company.rename(name="Renamed Demo", company="Demo Plumbing Co")
    ev = [e for e in client.hub.audit.list()["items"] if e["command"] == "company rename"][0]
    shown = client.hub.audit.show(event=ev["id"])
    entry = shown["entries"][0]
    assert entry["action"] == "update" and entry["diff"]["display_name"] == {"before": "Demo Plumbing Co", "after": "Renamed Demo"}
    cid = client.company.list()["items"][0]["company_id"]
    client.company.detach(company=cid)
    ev = client.hub.audit.list(command_name="company detach")["items"][0]
    entries = client.hub.audit.show(event=ev["id"])["entries"]
    assert any(e["action"] == "delete" and e["record_type"] == "company" and e["before"]["id"] == cid for e in entries)
    assert any(e["action"] == "delete" and e["record_type"] == "membership" for e in entries)


def test_company_selection(client, root, monkeypatch):
    items = client.company.list()["items"]
    cid = items[0]["company_id"]
    assert client.company.show(company=cid.lower())["company_id"] == cid
    assert client.company.show(company="demo holdings llc/DEMO PLUMBING CO")["company_id"] == cid
    with pytest.raises(BookflowError) as e:
        client.company.show()
    assert e.value.details["source"] == "none"
    monkeypatch.setenv("BOOKFLOW_COMPANY", "Nope")
    with pytest.raises(BookflowError) as e:
        client.company.show()
    assert e.value.details["source"] == "env"
    monkeypatch.delenv("BOOKFLOW_COMPANY")
    client.company.use(company=cid)
    assert client.company.show()["company_id"] == cid
    client.organization.new(name="Other"); client.company.new(legal_name="Demo Plumbing Co", home_currency="USD", organization="Other", timezone="UTC")
    with pytest.raises(BookflowError) as e:
        client.company.show(company="Demo Plumbing Co")
    assert e.value.code == "E_COMPANY_AMBIGUOUS"


def test_names_and_folders(client, root):
    client.organization.new(name="Folders")
    a = client.company.new(legal_name="Acme:Plumbing", home_currency="USD", organization="Folders", timezone="UTC")
    b = client.company.new(legal_name="ACME?PLUMBING", home_currency="USD", organization="Folders", timezone="UTC")
    assert a["path"].endswith("/Acme Plumbing") and b["path"].endswith("/ACME PLUMBING (2)")
    with pytest.raises(BookflowError) as e:
        client.company.new(legal_name="acme:plumbing", home_currency="USD", organization="Folders", timezone="UTC")
    assert e.value.code == "E_NAME_TAKEN"
    with pytest.raises(BookflowError) as e:
        client.company.new(legal_name="Slash", display_name="A/B", home_currency="USD", organization="Folders", timezone="UTC")
    assert e.value.code == "E_VALIDATION"
    with pytest.raises(BookflowError) as e:
        client.company.new(legal_name="A/B Legal", home_currency="USD", organization="Folders", timezone="UTC")
    assert e.value.details["fields"][0]["field"] == "display_name"
    assert client.company.rename(name="Acme:Plumbing", company=a["company_id"])["moved"] is False


def test_validation_matrix(client):
    cases = [
        ({"legal_name": "X", "home_currency": "usd"}, "home_currency"),
        ({"legal_name": "X", "home_currency": "USD", "email": "nope"}, "email"),
        ({"legal_name": "X", "home_currency": "USD", "tax_id": "12345"}, "tax_id"),
        ({"legal_name": "X", "home_currency": "USD", "fiscal_year_start_month": 13}, "fiscal_year_start_month"),
        ({"legal_name": "X", "home_currency": "USD", "timezone": "Mars/Olympus"}, "timezone"),
        ({"home_currency": "USD"}, "legal_name"),
        ({"legal_name": "X", "home_currency": "USD", "bogus": 1}, "bogus"),
    ]
    for inp, field in cases:
        with pytest.raises(BookflowError) as e:
            client.company.new(**inp, organization="Demo Holdings LLC")
        assert e.value.code == "E_VALIDATION"
        assert any(f["field"].startswith(field) for f in e.value.details["fields"]), (field, e.value.details)
    with pytest.raises(BookflowError) as e:
        client.run("company show", {"actor_id": "x"}, company="Demo Plumbing Co")
    assert e.value.code == "E_CONTEXT_IN_INPUT"
    with pytest.raises(BookflowError) as e:
        client.run("company frobnicate", {})
    assert e.value.code == "E_USAGE"


def test_init_edges(tmp_path, monkeypatch):
    import bookflow
    r = tmp_path / "r"
    c = bookflow.connect(data_root=str(r))
    with pytest.raises(BookflowError) as e:
        c.company.list()
    assert e.value.code == "E_NOT_INITIALIZED"
    pre = c.init(dry_run=True)
    assert pre["dry_run"] and not (r / "hub.db").exists()
    first = c.init(username="kay", display_name="Kay")
    assert first["created"] and first["username"] == "kay"
    again = c.init()
    assert again["created"] is False and again["hub_admin_user_id"] == first["hub_admin_user_id"]
    with pytest.raises(BookflowError) as e:
        c.init(username="other")
    assert e.value.code == "E_INIT_CONFLICT"
    ev = c.hub.audit.list(command_name="init")["items"][0]
    assert ev["actor_id"] == first["hub_admin_user_id"] and ev["interface"] == "python"
    (r / "config.toml").unlink()
    assert c.init()["created"] is False and (r / "config.toml").exists()
    (r / "config.toml").write_text("bad = [")
    with pytest.raises(BookflowError) as e:
        c.company.list()
    assert e.value.code == "E_CONFIG_INVALID"
    with pytest.raises(BookflowError) as e:
        c.init()
    assert e.value.code == "E_CONFIG_INVALID"


def test_umask_and_modes(tmp_path):
    import os, stat
    old = os.umask(0o022)
    try:
        import bookflow
        r = tmp_path / "r"
        c = bookflow.connect(data_root=str(r)); c.init(); c.demo.reset()
        for p in r.rglob("*"):
            if p.name == "root.lock":
                continue
            mode = stat.S_IMODE(p.stat().st_mode)
            assert mode & 0o077 == 0, (p, oct(mode))
    finally:
        os.umask(old)


def test_upgrade_and_rename_incomplete(client, root):
    out = client.upgrade()
    assert out["companies_migrated"] == [] and len(out["companies_skipped"]) == 1
    # simulate an interrupted move: set pending_path by renaming with move, then undo the folder move by hand
    cid = client.company.list()["items"][0]["company_id"]
    r = client.company.rename(name="Moved Co", move=True, company=cid)
    assert r["moved"]
    # a folder removed by hand is E_COMPANY_MISSING
    shutil.move(r["path"], r["path"] + ".gone")
    with pytest.raises(BookflowError) as e:
        client.company.show(company=cid)
    assert e.value.code == "E_COMPANY_MISSING"
    shutil.move(r["path"] + ".gone", r["path"])
    assert client.company.show(company=cid)["display_name"] == "Moved Co"
