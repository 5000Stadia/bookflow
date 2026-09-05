"""Immutable merged history, scoped continuation, and bounded decoding witnesses."""

import base64
import hashlib
import json
from pathlib import Path
from datetime import datetime, timezone

import pytest
import sqlalchemy as sa

from bookflow import BookflowError
from bookflow.company import schema as c
from bookflow.core import audit, registry
from bookflow.core.ids import new_id
from bookflow.hub.users import common
from bookflow.storage.engine import open_database
from tests.conftest import as_user, make_actor

COMPANY = "Demo Plumbing Co"


@pytest.fixture
def target(client):
    return client.customer.create(name="Activity target", company=COMPANY)


def feed(client, target, **kwargs):
    return client.run("activity", dict(record_type="customer", record_id=target["id"], **kwargs), company=COMPANY)


def note(client, target, body="Original"):
    return client.note.add(record_type="customer", record_id=target["id"], body=body, company=COMPANY)["note"]


def db_path(client):
    return Path(client.company.show(company=COMPANY)["path"]) / "company.db"


def seed_events(client, record_type, record_id, snapshots, *, at="2025-01-01T00:00:00.000Z", same_event=False):
    """Insert valid immutable audit fixtures without 10,000 command invocations."""
    with open_database(db_path(client), writable=True) as db:
        db.raw.execute("BEGIN IMMEDIATE")
        template = dict(db.conn.execute(sa.select(c.audit_events).order_by(c.audit_events.c.seq.desc()).limit(1)).mappings().one())
        seq = template["seq"]
        event_rows, entries = [], []
        event_id = None
        for i, snapshot in enumerate(snapshots):
            if not same_event or event_id is None:
                event_id = new_id()
                seq += 1
                event_rows.append(dict(template, id=event_id, seq=seq, at=at,
                    command="note edit" if record_type == "note" else "attachment unlink", undo_of_event_id=None))
            entries.append(dict(id=new_id(), event_id=event_id, record_type=record_type,
                record_id=record_id, action="create" if i == 0 else "update", version_before=None if i == 0 else i,
                version_after=i + 1, before=None, after=audit.encode_snapshot(snapshot)))
        db.conn.execute(c.audit_events.insert(), event_rows)
        db.conn.execute(c.audit_entries.insert(), entries)
        db.raw.execute("COMMIT")
    return [(at, e["event_id"], e["id"]) for e in entries]


def test_historical_note_bodies_and_one_item_per_action(client, target):
    original = "  Original <script>text</script>\n"
    n = note(client, target, original)
    client.note.edit(note=n["id"], body="Corrected", expected_version=1, company=COMPANY)
    page = feed(client, target)
    assert [i["kind"] for i in page["items"]] == ["audit", "note", "note"]
    assert [i["body"] for i in page["items"][1:]] == [original, "Corrected"]
    assert [i["action"] for i in page["items"][1:]] == ["create", "update"]
    assert all(i["actor_name"] for i in page["items"])
    assert len({i["entry_id"] for i in page["items"]}) == 3
    direct = client.run("activity", {"record_type": "note", "record_id": n["id"]}, company=COMPANY)
    assert direct["count"] == 2  # direct note history is not also a duplicate audit item


def test_link_history_survives_unlink_and_uses_snapshot_caption(client, target):
    actor = note(client, target)["author_id"]
    aid, lid = new_id(), new_id()
    at = "2025-01-01T00:00:00.000Z"
    linked = dict(id=lid, **common(actor, "python"), attachment_id=aid, record_type="customer",
        record_id=target["id"], linked_by=actor, linked_at=at, caption="Original caption", active=True)
    unlinked = dict(linked, version=2, active=False)
    with open_database(db_path(client), writable=True) as db:
        db.raw.execute("BEGIN IMMEDIATE")
        db.conn.execute(c.attachments.insert().values(id=aid, **common(actor, "python"),
            sha256="a" * 64, size_bytes=0, media_type="application/pdf", original_filename="receipt.pdf",
            uploaded_by=actor, uploaded_at=at, collected_at=None))
        db.conn.execute(c.attachment_links.insert().values(**dict(unlinked, caption="Current row must not replace history")))
        db.raw.execute("COMMIT")
    seed_events(client, "attachment_link", lid, [linked, unlinked])
    page = feed(client, target, kinds=["attachment"])
    assert page["count"] == 2
    assert [i["caption"] for i in page["items"]] == ["Original caption"] * 2
    assert [i["active"] for i in page["items"]] == [True, False]
    assert [i["action"] for i in page["items"]] == ["create", "update"]
    assert all(i["attachment_id"] == aid for i in page["items"])


def test_ties_highwater_and_backdated_insert(client, target):
    n = note(client, target)
    tied = seed_events(client, "note", n["id"], [{"body": str(i)} for i in range(5)], same_event=True)
    tied += seed_events(client, "note", n["id"], [{"body": str(i)} for i in range(3)])
    expected = sorted(tied)
    page = feed(client, target, kinds=["note"], until="2025-01-01", limit=2)
    high_water = page["high_water"]
    seed_events(client, "note", n["id"], [{"body": "Later backdated write"}])
    seen = []
    while True:
        assert page["high_water"] == high_water
        seen.extend((datetime.fromisoformat(i["at"]).astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"), i["event_id"], i["entry_id"]) for i in page["items"])
        if not page["has_more"]:
            break
        page = feed(client, target, kinds=["note"], until="2025-01-01", limit=2, cursor=page["next_cursor"])
    assert seen == expected
    assert feed(client, target, kinds=["note"], until="2025-01-01")["count"] == 9


def test_order_uses_time_before_event_sequence(client, target):
    n = note(client, target)
    seed_events(client, "note", n["id"], [{"body": "Later"}], at="2025-02-01T00:00:00.000Z")
    seed_events(client, "note", n["id"], [{"body": "Earlier"}], at="2025-01-01T00:00:00.000Z")
    page = feed(client, target, kinds=["note"], until="2025-02-01")
    assert [i["body"] for i in page["items"]] == ["Earlier", "Later"]
    assert page["items"][0]["seq"] > page["items"][1]["seq"]
    assert feed(client, target, kinds=[])["count"] == 0
    assert feed(client, target, kinds=["note"], since="2025-02-01T00:00:00Z", until="2025-02-02T00:00:00Z")["count"] == 1


@pytest.mark.parametrize("extra", [
    {"limit": 0}, {"limit": 201}, {"limit": True}, {"cursor": "x" * 2049},
    {"cursor": "%%%"}, {"cursor": ""}, {"kinds": ["unknown"]}, {"since": "bad"},
    {"until": ""}, {"since": "2026-01-01", "until": "2025-01-01"},
])
def test_validation(client, target, extra):
    with pytest.raises(BookflowError) as exc:
        feed(client, target, **extra)
    assert exc.value.code == "E_VALIDATION"


def test_cursor_binds_filters_target_actor_permissions_and_cutoff(client, root, target):
    note(client, target)
    cursor = feed(client, target, limit=1)["next_cursor"]
    for extra in ({"kinds": ["note"]}, {"since": "2020-01-01"}, {"until": "2030-01-01"}, {"limit": 2}):
        with pytest.raises(BookflowError) as exc:
            feed(client, target, **{"limit": 1, "cursor": cursor, **extra})
        assert exc.value.code == "E_VALIDATION"
    other = client.customer.create(name="Other activity", company=COMPANY)
    with pytest.raises(BookflowError) as exc:
        feed(client, other, limit=1, cursor=cursor)
    assert exc.value.code == "E_VALIDATION"
    data = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
    data[0]["high_water"] += 1
    tampered = base64.urlsafe_b64encode(json.dumps(data).encode()).decode()
    with pytest.raises(BookflowError) as exc:
        feed(client, target, limit=1, cursor=tampered)
    assert exc.value.code == "E_VALIDATION"
    cid = client.company.show(company=COMPANY)["company_id"]
    uid = make_actor(root, "activity-reader", company_role=(cid, "readonly"))
    reader = as_user(root, "activity-reader")
    with pytest.raises(BookflowError) as exc:
        feed(reader, target, limit=1, cursor=cursor)
    assert exc.value.code == "E_VALIDATION"
    own = feed(reader, target, limit=1)["next_cursor"]
    from bookflow.hub import schema as h
    with open_database(root / "hub.db", writable=True) as db:
        db.raw.execute("BEGIN IMMEDIATE")
        db.conn.execute(h.memberships.update().where(h.memberships.c.user_id == uid).values(role="standard"))
        db.raw.execute("COMMIT")
    with pytest.raises(BookflowError) as exc:
        feed(reader, target, limit=1, cursor=own)
    assert exc.value.code == "E_VALIDATION"


def test_readonly_is_read_only_and_company_isolation(client, root, target):
    note(client, target)
    cid = client.company.show(company=COMPANY)["company_id"]
    make_actor(root, "activity-reader", company_role=(cid, "readonly"))
    reader = as_user(root, "activity-reader")
    feed(reader, target)  # establish the normal actor projection before comparing bytes
    def contents():
        return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob("*") if p.is_file() and p.name != "root.lock"}
    before = contents()
    assert feed(reader, target)["count"] == 2
    assert contents() == before
    org = client.organization.list()["items"][0]["organization_id"]
    other = client.company.new(organization=org, legal_name="Activity other company", home_currency="USD", timezone="UTC", chart="none")
    inp = {"record_type": "customer", "record_id": target["id"]}
    for actor, code in ((reader, "E_COMPANY_NOT_FOUND"), (client, "E_RECORD_NOT_FOUND")):
        with pytest.raises(BookflowError) as exc:
            actor.run("activity", inp, company=other["company_id"])
        assert exc.value.code == code
    cmd = registry.get("activity")
    assert cmd.required_role == "member" and cmd.capability == "activity" and not cmd.writes


def test_response_byte_budget_and_continuation(client, target):
    for i in range(5):
        note(client, target, str(i) + "é" * 32767)
    page = feed(client, target, kinds=["note"], limit=200)
    seen = []
    while True:
        assert len(json.dumps(page).encode()) <= 262144
        assert page["count"] > 0
        seen.extend(i["body"][0] for i in page["items"])
        if not page["has_more"]:
            break
        page = feed(client, target, kinds=["note"], limit=200, cursor=page["next_cursor"])
    assert seen == list("01234")
    note(client, target, "x" + "\x01" * 65535)
    pages = feed(client, target, kinds=["note"], limit=200)
    while pages["has_more"]:
        pages = feed(client, target, kinds=["note"], limit=200, cursor=pages["next_cursor"])
    assert len(json.dumps(pages).encode()) <= 262144
    assert pages["items"][-1]["text_truncated"]


def test_ten_thousand_events_decode_only_bounded_target_page(client, target, monkeypatch, record_property):
    import time
    n = note(client, target)
    seed_events(client, "note", n["id"], [{"body": f"Historic {i}"} for i in range(10000)])
    other = client.customer.create(name="Unrelated activity", company=COMPANY)
    alien = note(client, other, "Other company-local target")
    seed_events(client, "note", alien["id"], [{"body": "Must not decode"}] * 25, at="2024-01-01T00:00:00.000Z")
    decoded, statements = [], []
    original = audit.decode_snapshot
    def decode(blob):
        result = original(blob)
        decoded.append(result)
        return result
    def sql(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)
    monkeypatch.setattr(audit, "decode_snapshot", decode)
    sa.event.listen(sa.engine.Engine, "before_cursor_execute", sql)
    started = time.monotonic()
    try:
        page = feed(client, target, kinds=["note"], limit=10)
    finally:
        sa.event.remove(sa.engine.Engine, "before_cursor_execute", sql)
    record_property("activity_10000_seconds", time.monotonic() - started)
    assert page["count"] == 10 and page["has_more"]
    assert len(decoded) == 10 and all(d["body"].startswith("Historic ") for d in decoded)
    candidate_sql = [s for s in statements if "audit_entries.\"after\"" in s and "ORDER BY" in s]
    assert len(candidate_sql) == 1 and "LIMIT" in candidate_sql[0] and "JOIN audit_entries" in candidate_sql[0]
    principal_sql = [s for s in statements if "FROM principals" in s and " IN (" in s]
    assert len(principal_sql) == 1


def test_single_word_cli(client, target, cli):
    note(client, target)
    page = cli.json("activity", "customer", target["id"], "--company", COMPANY, "--limit", "1")
    assert page["count"] == 1 and page["next_cursor"]
