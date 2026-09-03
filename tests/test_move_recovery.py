"""Crash boundaries of folder moves (blueprint 3.1): before the move, after the move, mid case-hop, and organization moves."""

import os
import shutil

import sqlalchemy as sa

from bookflow.hub import schema as h
from bookflow.storage.engine import open_database


def _hub_set(root, table, row_id, **values):
    with open_database(root / "hub.db", writable=True) as db:
        db.raw.execute("BEGIN IMMEDIATE")
        db.conn.execute(table.update().where(table.c.id == row_id).values(**values))
        db.raw.execute("COMMIT")


def _hub_get(root, table, row_id):
    with open_database(root / "hub.db", writable=False) as db:
        return dict(db.conn.execute(sa.select(table).where(table.c.id == row_id)).mappings().first())


def test_company_pending_before_move(client, root):
    cid = client.company.list()["items"][0]["company_id"]
    row = _hub_get(root, h.companies, cid)
    new_rel = row["path"].rsplit("/", 1)[0] + "/Renamed Co"
    _hub_set(root, h.companies, cid, display_name="Renamed Co", name_key="renamed co", pending_path=new_rel)
    # read-only open uses the old folder, which still exists, and changes nothing
    assert client.company.show(company=cid)["display_name"] == "Renamed Co"
    assert _hub_get(root, h.companies, cid)["pending_path"] == new_rel
    # rename --move with the current name completes the pending move
    out = client.company.rename(name="Renamed Co", move=True, company=cid)
    assert out["moved"] and out["path"].endswith("/Renamed Co")
    after = _hub_get(root, h.companies, cid)
    assert after["path"] == new_rel and after["pending_path"] is None and (root / new_rel).exists()


def test_company_pending_after_move(client, root):
    cid = client.company.list()["items"][0]["company_id"]
    row = _hub_get(root, h.companies, cid)
    new_rel = row["path"].rsplit("/", 1)[0] + "/Moved Co"
    _hub_set(root, h.companies, cid, pending_path=new_rel)
    shutil.move(root / row["path"], root / new_rel)  # the move happened; the second transaction did not
    # read-only open finds the moved folder without writing
    assert client.company.show(company=cid)["company_id"] == cid
    assert _hub_get(root, h.companies, cid)["pending_path"] == new_rel
    # a writable open completes it
    client.company.rename(name=row["display_name"], company=cid)
    after = _hub_get(root, h.companies, cid)
    assert after["path"] == new_rel and after["pending_path"] is None


def test_company_case_hop_interrupted(client, root):
    cid = client.company.list()["items"][0]["company_id"]
    row = _hub_get(root, h.companies, cid)
    parent, name = row["path"].rsplit("/", 1)
    new_rel = f"{parent}/{name.upper()}"
    hop = root / f"{new_rel}.moving-{cid}"
    _hub_set(root, h.companies, cid, display_name=name.upper(), name_key=name.lower(), pending_path=new_rel)
    shutil.move(root / row["path"], hop)  # crashed after the first rename of the hop
    assert client.company.show(company=cid)["company_id"] == cid
    client.company.rename(name=name.upper(), move=True, company=cid)
    after = _hub_get(root, h.companies, cid)
    assert after["pending_path"] is None and (root / after["path"]).exists() and not hop.exists()


def test_organization_move_interrupted(client, root):
    org = client.organization.list()["items"][0]
    cid = client.company.list()["items"][0]["company_id"]
    orow = _hub_get(root, h.organizations, org["organization_id"])
    new_rel = "organizations/Demo Moved"
    _hub_set(root, h.organizations, orow["id"], pending_path=new_rel)
    shutil.move(root / orow["path"], root / new_rel)  # crashed before the second transaction
    # read-only open of a company inside finds it under the moved organization folder
    assert client.company.show(company=cid)["company_id"] == cid
    assert _hub_get(root, h.organizations, orow["id"])["pending_path"] == new_rel
    # a writable open completes the organization move and rewrites every company path
    client.company.rename(name="Still Demo", company=cid)
    o2 = _hub_get(root, h.organizations, orow["id"])
    c2 = _hub_get(root, h.companies, cid)
    assert o2["path"] == new_rel and o2["pending_path"] is None and c2["path"].startswith(new_rel + "/")
    assert (root / c2["path"] / "company.db").exists()


def test_organization_rename_completes_pending(client, root):
    org = client.organization.list()["items"][0]
    orow = _hub_get(root, h.organizations, org["organization_id"])
    new_rel = "organizations/Demo Pending"
    _hub_set(root, h.organizations, orow["id"], display_name="Demo Pending", name_key="demo pending", pending_path=new_rel)
    out = client.organization.rename(organization=orow["id"], name="Demo Pending", move=True)
    assert out["moved"] and out["path"].endswith("/Demo Pending")
    o2 = _hub_get(root, h.organizations, orow["id"])
    assert o2["path"] == new_rel and o2["pending_path"] is None and os.path.isdir(root / new_rel)
