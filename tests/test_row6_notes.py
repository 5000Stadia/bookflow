"""Versioned annotations use existing company access, audit and retry boundaries."""

import hashlib
from pathlib import Path

import pytest
from bookflow import BookflowError
from bookflow.company import records, schema
from bookflow.core.ids import new_id
from bookflow.storage.engine import open_database
from tests.conftest import as_user, make_actor
from tests.test_row3_host import hosted, PASSWORD


COMPANY = "Demo Plumbing Co"


@pytest.fixture
def target(client):
    return client.customer.create(name="Note target", company=COMPANY)


def add(client, target, body="Original comment", **kwargs):
    return client.note.add(record_type="customer", record_id=target["id"], body=body, company=COMPANY, **kwargs)["note"]


def test_note_edit_history_and_target_version(client, target):
    text = "  **Keep source**\n<script>alert('x')</script>\n"
    note = add(client, target, text)
    assert note["body"] == text and note["version"] == 1
    edited = client.note.edit(note=note["id"], body="Corrected", expected_version=1, company=COMPANY)["note"]
    assert edited["version"] == 2 and edited["edited_at"]
    assert edited["author_id"] == note["author_id"] and edited["at"] == note["at"]
    assert client.customer.show(customer=target["id"], company=COMPANY)["version"] == target["version"]
    event = client.audit.list(record_type="note", record_id=note["id"], company=COMPANY)["items"][0]
    entry = client.audit.show(event=event["id"], company=COMPANY)["entries"][0]
    assert entry["before"]["body"] == text and entry["after"]["body"] == "Corrected"
    assert client.note.show(note=note["id"].lower(), company=COMPANY)["body"] == "Corrected"


def test_conflict_and_current_noop(client, target):
    note = add(client, target)
    client.note.edit(note=note["id"], body="New", expected_version=1, company=COMPANY)
    for value in ("Stale", "New"):
        with pytest.raises(BookflowError) as exc:
            client.note.edit(note=note["id"], body=value, expected_version=1, company=COMPANY)
        assert exc.value.code == "E_VERSION_CONFLICT"
        assert exc.value.details["current_version"] == 2
        assert "body" in exc.value.details["changed_fields"]
    before = client.audit.list(record_type="note", record_id=note["id"], company=COMPANY)
    noop = client.note.edit(note=note["id"], body="New", expected_version=2, company=COMPANY)
    assert noop["note"]["version"] == 2
    assert client.audit.list(record_type="note", record_id=note["id"], company=COMPANY) == before


def test_retry_replays_and_mismatch_rejects(client, target):
    first = add(client, target, idempotency_key="note-once")
    replay = client.note.add(record_type="customer", record_id=target["id"], body="Original comment", company=COMPANY, idempotency_key="note-once")
    assert replay["idempotent_replay"] and replay["note"] == first
    with pytest.raises(BookflowError) as exc:
        add(client, target, "Different", idempotency_key="note-once")
    assert exc.value.code == "E_IDEMPOTENCY_MISMATCH"
    assert client.note.list(record_type="customer", record_id=target["id"], company=COMPANY)["count"] == 1


@pytest.mark.parametrize("body", ["", " \n\t", "é" * 32769, "x" * 65537])
def test_body_limits(client, target, body):
    with pytest.raises(BookflowError) as exc:
        add(client, target, body)
    assert exc.value.code == "E_VALIDATION"


def test_exact_utf8_limit_and_required_version(client, target):
    note = add(client, target, "é" * 32768)
    for extra in ({}, {"expected_version": 0}, {"expected_version": True}, {"expected_version": "1"}):
        with pytest.raises(BookflowError) as exc:
            client.note.edit(note=note["id"], body="Changed", company=COMPANY, **extra)
        assert exc.value.code == "E_VALIDATION"
    assert client.note.show(note=note["id"], company=COMPANY)["body"] == "é" * 32768


def test_target_registry_and_invalid_identity(client, target):
    for table, key in records._TARGETS.values():
        assert key in schema.metadata.tables[table].c
    for kind, key, code in (("presence", target["id"], "E_VALIDATION"),
                            ("customers; DROP TABLE notes", target["id"], "E_VALIDATION"),
                            ("customer", new_id(), "E_RECORD_NOT_FOUND")):
        with pytest.raises(BookflowError) as exc:
            client.note.add(record_type=kind, record_id=key, body="test", company=COMPANY)
        assert exc.value.code == code
    with pytest.raises(BookflowError) as exc:
        client.note.add(record_type="customer", record_id=target["id"], body="forged", kind="system", company=COMPANY)
    assert exc.value.code == "E_VALIDATION"


def test_pages_are_bounded_and_bound_to_target(client, target):
    for n in range(7):
        add(client, target, str(n))
    seen, cursor = [], None
    while True:
        page = client.note.list(record_type="customer", record_id=target["id"], limit=2, cursor=cursor, company=COMPANY)
        assert page["count"] <= 2
        seen.extend(r["id"] for r in page["items"])
        cursor = page["next_cursor"]
        assert bool(cursor) == page["has_more"]
        if not cursor:
            break
    assert len(seen) == len(set(seen)) == 7 and seen == sorted(seen, reverse=True)
    first = client.note.list(record_type="customer", record_id=target["id"], limit=2, company=COMPANY)
    other = client.customer.create(name="Other note target", company=COMPANY)
    for cursor in ("not-a-cursor", first["next_cursor"]):
        with pytest.raises(BookflowError) as exc:
            client.note.list(record_type="customer", record_id=other["id"], cursor=cursor, company=COMPANY)
        assert exc.value.code == "E_VALIDATION"


def test_page_body_budget_and_same_actor_scope(client, root, target):
    for _ in range(5):
        add(client, target, "x" * 65536)
    first = client.note.list(record_type="customer", record_id=target["id"], limit=200, company=COMPANY)
    assert first["count"] == 4 and first["has_more"]
    last = client.note.list(record_type="customer", record_id=target["id"], limit=200, cursor=first["next_cursor"], company=COMPANY)
    assert last["count"] == 1 and not last["has_more"]
    cid = client.company.show(company=COMPANY)["company_id"]
    make_actor(root, "different-reader", company_role=(cid, "readonly"))
    with pytest.raises(BookflowError) as exc:
        as_user(root, "different-reader").note.list(record_type="customer", record_id=target["id"], cursor=first["next_cursor"], company=cid)
    assert exc.value.code == "E_VALIDATION"


def test_inactive_target_and_child_retirement_retain_notes(client):
    customer = client.customer.create(name="Annotated child", contacts=[{"first_name": "Pat", "role": "primary"}], company=COMPANY)
    contact = customer["contacts"][0]
    note = client.note.add(record_type="customer_contact", record_id=contact["id"], body="Historic contact", company=COMPANY)["note"]
    updated = client.customer.update(customer=customer["id"], contacts=[], expected_version=customer["version"], company=COMPANY)
    client.customer.deactivate(customer=customer["id"], expected_version=updated["version"], company=COMPANY)
    assert client.note.list(record_type="customer_contact", record_id=contact["id"], company=COMPANY)["items"][0]["id"] == note["id"]
    assert add(client, customer)["record_id"] == customer["id"]


def test_undo_create_does_not_erase_or_block_annotation(client, target):
    event = client.audit.list(record_type="customer", record_id=target["id"], company=COMPANY)["items"][0]
    note = add(client, target)
    client.run("undo", {"event_id": event["id"]}, company=COMPANY)
    assert client.customer.show(customer=target["id"], company=COMPANY)["active"] is False
    assert client.note.show(note=note["id"], company=COMPANY)["record_id"] == target["id"]


def test_company_account_demo_and_cli(client, cli, target):
    cid = client.company.show(company=COMPANY)["company_id"]
    assert client.note.list(record_type="company_info", record_id=cid, company=COMPANY)["count"] == 1
    result = cli.json("note", "add", "customer", target["id"], "--body", "CLI comment", "--company", COMPANY)
    edited = cli.json("note", "edit", result["note"]["id"], "--body", "CLI corrected", "--expected-version", "1", "--company", COMPANY)
    assert edited["note"]["updated_via"] == "cli"
    assert cli.json("note", "list", "customer", target["id"], "--limit", "1", "--company", COMPANY)["count"] == 1


def test_access_cross_company_and_other_editor(client, root, target):
    cid = client.company.show(company=COMPANY)["company_id"]
    make_actor(root, "reader", company_role=(cid, "readonly"))
    make_actor(root, "editor", company_role=(cid, "standard"))
    note = add(client, target)
    reader, editor = as_user(root, "reader"), as_user(root, "editor")
    assert reader.note.show(note=note["id"], company=cid)["body"] == note["body"]
    with pytest.raises(BookflowError) as exc:
        reader.note.edit(note=note["id"], body="No", expected_version=1, company=cid)
    assert exc.value.code == "E_PERMISSION"
    changed = editor.note.edit(note=note["id"], body="Reviewed", expected_version=1, company=cid)["note"]
    assert changed["author_id"] == note["author_id"] and changed["updated_by_name"] == "Editor"
    org = client.organization.list()["items"][0]["organization_id"]
    other = client.company.new(organization=org, legal_name="Other company", home_currency="USD", timezone="UTC", chart="none")
    with pytest.raises(BookflowError) as exc:
        client.note.show(note=note["id"], company=other["company_id"])
    assert exc.value.code == "E_RECORD_NOT_FOUND"
    with pytest.raises(BookflowError) as exc:
        reader.note.show(note=note["id"], company=other["company_id"])
    assert exc.value.code == "E_COMPANY_NOT_FOUND"


def test_agent_reason_gate_and_source_attribution(client, root, target):
    from tests.test_row2_flow import _agent_session
    run = _agent_session(root, client)
    body = dict(record_type="customer", record_id=target["id"], body="Agent comment")
    with pytest.raises(BookflowError) as exc:
        run("note add", body)
    assert exc.value.code == "E_REASON_REQUIRED"
    note = run("note add", body, reason="record customer request", source_ref="message:demo")["note"]
    assert note["interface"] == "mcp" and note["author_name"] == "Claude Agent"


def test_dry_run_changes_no_database_or_files(client, root, target):
    def contents():
        return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in root.rglob("*") if p.is_file() and p.name != "root.lock"}
    # Resolve normally first so the dry run is compared against an opened/current root.
    note = add(client, target)
    client.note.show(note=note["id"], company=COMPANY)
    before = contents()
    out = client.note.add(record_type="customer", record_id=target["id"], body="Preview", company=COMPANY, dry_run=True)
    edited = client.note.edit(note=note["id"], body="Preview edit", expected_version=1, company=COMPANY, dry_run=True)
    assert out["dry_run"] and edited["dry_run"] and contents() == before


def test_http_hosted_cli_and_safe_workbench(hosted, cli):
    cid = hosted.company_id
    result = hosted.ok("note.add", {"record_type": "company_info", "record_id": cid, "body": "<script>unsafe()</script>"}, company=cid)
    nid = result["note"]["id"]
    assert cli.json("note", "show", nid, "--company", cid)["body"] == result["note"]["body"]
    hosted.api.post("/login", json={"username": hosted.login, "password": PASSWORD})
    page = hosted.api.get(f"/c/{cid}/note/{nid}")
    assert page.status_code == 200 and "<script>unsafe()</script>" not in page.text
    assert "&lt;script&gt;" in page.text
    assert hosted.api.get(f"/c/{cid}/note").status_code == 200


def test_large_history_pages_use_target_index(client, target):
    from bookflow.hub.users import common
    import sqlalchemy as sa

    sample = add(client, target)
    folder = Path(client.company.show(company=COMPANY)["path"])
    with open_database(folder / "company.db", writable=True) as db:
        db.raw.execute("BEGIN IMMEDIATE")
        rows = [dict(id=f"{n:026d}", **common(sample["author_id"], "python"),
            record_type="customer", record_id=target["id"], body=f"Historical comment {n}",
            author_id=sample["author_id"], interface="python", at=sample["at"],
            edited_at=None, kind="comment") for n in range(10000)]
        db.conn.execute(schema.notes.insert(), rows)
        db.raw.execute("COMMIT")
        query_plan = " ".join(str(r) for r in db.raw.execute(
            "EXPLAIN QUERY PLAN SELECT * FROM notes WHERE record_type=? AND record_id=? AND id<? ORDER BY id DESC LIMIT 201",
            ("customer", target["id"], sample["id"]),
        ))
        assert "ix_notes_target_id" in query_plan and "TEMP B-TREE" not in query_plan
    counts = []
    for limit in (10, 200):
        statements = []
        def record_sql(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement)
        sa.event.listen(sa.engine.Engine, "before_cursor_execute", record_sql)
        try:
            page = client.note.list(record_type="customer", record_id=target["id"], limit=limit, company=COMPANY)
        finally:
            sa.event.remove(sa.engine.Engine, "before_cursor_execute", record_sql)
        assert page["count"] == limit and page["has_more"]
        counts.append(len(statements))
    assert counts[0] == counts[1]
