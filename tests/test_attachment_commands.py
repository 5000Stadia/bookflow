"""Attachment command witnesses over isolated roots and the shared binary API."""
import hashlib
import importlib
import io
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from pydantic import ValidationError

from bookflow import BookflowError
from bookflow.commands.attachment_cmds import AttachmentAddInput
from bookflow.company import schema
from bookflow.core.ids import new_id
from bookflow.storage.engine import open_database
from bookflow.storage.migrate import _config
from tests.conftest import as_user, make_actor

COMPANY = "Demo Plumbing Co"
BODY = b"%PDF-1.4\nattachment witness\x00\xff"


@pytest.fixture
def target(client):
    return client.customer.create(name="Attachment target", company=COMPANY)


def add(client, target, body=BODY, **kwargs):
    options = dict(record_type="customer", record_id=target["id"], original_filename="receipt.pdf", media_type="application/pdf", caption="Original caption")
    options.update(kwargs)
    return client.attachment.add(**options, input_stream=io.BytesIO(body), company=COMPANY)


def company_path(client):
    return Path(client.company.show(company=COMPANY)["path"]) / "company.db"


def test_bytes_deduplicate_and_retain_first_metadata(client, target):
    first = add(client, target)
    again = add(client, target, original_filename="renamed.bin", media_type="application/octet-stream", caption="New caption")
    assert first["attachment"] == again["attachment"]
    assert first["link"] == again["link"]
    assert first["attachment"]["sha256"] == hashlib.sha256(BODY).hexdigest()
    sink = io.BytesIO()
    metadata = client.attachment.get(attachment=first["attachment"]["id"], output_stream=sink, company=COMPANY)
    assert sink.getvalue() == BODY and metadata == first["attachment"]
    with open_database(company_path(client), writable=False) as db:
        assert db.raw.execute("SELECT count(*) FROM attachments WHERE sha256 = ?", (hashlib.sha256(BODY).hexdigest(),)).fetchone()[0] == 1
        assert db.raw.execute("SELECT count(*) FROM attachment_links WHERE record_type = ? AND record_id = ?", ("customer", target["id"])).fetchone()[0] == 1
    files = list((company_path(client).parent / "attachments").glob("*/" + hashlib.sha256(BODY).hexdigest()))
    assert len(files) == 1 and files[0].read_bytes() == BODY
    assert client.customer.show(customer=target["id"], company=COMPANY)["version"] == target["version"]


def test_unlink_version_history_and_new_occurrence(client, target):
    original = add(client, target)
    link = original["link"]
    result = client.attachment.unlink(link=link["id"], expected_version=1, company=COMPANY)
    assert not result["link"]["active"] and result["link"]["version"] == 2
    with pytest.raises(BookflowError) as error:
        client.attachment.unlink(link=link["id"], expected_version=1, company=COMPANY)
    assert error.value.code == "E_VERSION_CONFLICT"
    assert "active" in error.value.details["changed_fields"]
    assert client.attachment.unlink(link=link["id"], expected_version=2, company=COMPANY)["link"] == result["link"]
    assert client.attachment.list(record_type="customer", record_id=target["id"], company=COMPANY)["count"] == 0
    later = client.attachment.link(attachment=original["attachment"]["id"], record_type="customer", record_id=target["id"], caption="Later", company=COMPANY)
    assert later["link"]["id"] != link["id"] and later["link"]["version"] == 1
    events = client.audit.list(record_type="attachment_link", record_id=link["id"], company=COMPANY)["items"]
    assert len(events) == 2
    entry = client.audit.show(event=events[0]["id"], company=COMPANY)["entries"][0]
    assert entry["before"]["active"] is True and entry["after"]["active"] is False
    assert entry["before"]["caption"] == "Original caption"


def test_collected_body_requires_upload_to_restore(client, target):
    first = add(client, target)
    a, link = first["attachment"], first["link"]
    client.attachment.unlink(link=link["id"], expected_version=1, company=COMPANY)
    path = company_path(client)
    (path.parent / "attachments" / a["sha256"][:2] / a["sha256"]).unlink()
    with open_database(path, writable=True) as db:
        db.conn.execute(schema.attachments.update().where(schema.attachments.c.id == a["id"]).values(collected_at="2026-09-05T00:00:00Z", version=2))
    for operation in (lambda: client.attachment.get(attachment=a["id"], output_stream=io.BytesIO(), company=COMPANY),
                      lambda: client.attachment.link(attachment=a["id"], record_type="customer", record_id=target["id"], company=COMPANY)):
        with pytest.raises(BookflowError) as error:
            operation()
        assert error.value.code == "E_IO"
    restored = add(client, target, original_filename="new.pdf")
    assert restored["attachment"]["id"] == a["id"]
    assert restored["attachment"]["version"] == 3 and restored["attachment"]["collected_at"] is None
    assert restored["attachment"]["original_filename"] == "receipt.pdf"
    assert restored["attachment"]["uploaded_at"] == a["uploaded_at"]
    assert restored["link"]["id"] != link["id"]
    assert (path.parent / "attachments" / a["sha256"][:2] / a["sha256"]).read_bytes() == BODY


@pytest.mark.parametrize("field,value", [
    ("original_filename", "../receipt.pdf"), ("original_filename", "a\\b"),
    ("original_filename", "."), ("original_filename", ".."), ("original_filename", "x\n.pdf"),
    ("original_filename", "x\x7f.pdf"), ("original_filename", "é"*128),
    ("caption", "é"*1025), ("media_type", "text/plain; charset=utf-8"),
    ("media_type", "é/plain"), ("media_type", "x"*126+"/x"),
])
def test_metadata_rejects_unsafe_or_oversize(field, value):
    raw = dict(record_type="customer", record_id=new_id(), original_filename="receipt.pdf")
    raw[field] = value
    with pytest.raises(ValidationError):
        AttachmentAddInput(**raw)


def test_exact_metadata_limits(client, target):
    result = add(client, target, original_filename="é"*127+"x", caption="é"*1024, media_type="a/"+"b"*125)
    assert len(result["attachment"]["original_filename"].encode()) == 255
    assert len(result["link"]["caption"].encode()) == 2048


def test_target_errors_precede_consumption(client, target):
    class Unreadable:
        def read(self, n):
            pytest.fail("invalid target consumed bytes")
    for kind, key, code in (("unknown", target["id"], "E_VALIDATION"), ("customer", new_id(), "E_RECORD_NOT_FOUND")):
        with pytest.raises(BookflowError) as error:
            client.attachment.add(record_type=kind, record_id=key, original_filename="x", input_stream=Unreadable(), company=COMPANY)
        assert error.value.code == code


def test_permissions_and_agent_reason_precede_consumption(client, target, root):
    company = client.company.show(company=COMPANY)
    make_actor(root, "attachment_reader", company_role=(company["company_id"], "readonly"))
    reader = as_user(root, "attachment_reader")
    first = add(client, target)
    sink = io.BytesIO()
    reader.attachment.get(attachment=first["attachment"]["id"], output_stream=sink, company=COMPANY)
    assert sink.getvalue() == BODY
    class Unreadable:
        def read(self, n):
            pytest.fail("unauthorized stream consumed")
    for operation in (
        lambda: reader.attachment.add(record_type="customer", record_id=target["id"], original_filename="x", input_stream=Unreadable(), company=COMPANY),
        lambda: reader.attachment.link(attachment=first["attachment"]["id"], record_type="customer", record_id=target["id"], company=COMPANY),
        lambda: reader.attachment.unlink(link=first["link"]["id"], expected_version=1, company=COMPANY),
    ):
        with pytest.raises(BookflowError) as error:
            operation()
        assert error.value.code == "E_PERMISSION"
    owner = make_actor(root, "attachment_owner", company_role=(company["company_id"], "standard"))
    make_actor(root, "attachment_agent", company_role=(company["company_id"], "standard"), kind="agent", owner_user_id=owner)
    agent = as_user(root, "attachment_agent")
    with pytest.raises(BookflowError) as error:
        agent.attachment.add(record_type="customer", record_id=target["id"], original_filename="x", input_stream=Unreadable(), company=COMPANY)
    assert error.value.code == "E_REASON_REQUIRED"


def test_idempotency_includes_bytes_and_noop_replays(client, target):
    first = add(client, target, idempotency_key="first")
    assert add(client, target, idempotency_key="first")["idempotent_replay"]
    with pytest.raises(BookflowError) as error:
        add(client, target, body=b"different", idempotency_key="first")
    assert error.value.code == "E_IDEMPOTENCY_MISMATCH"
    noop = add(client, target, idempotency_key="duplicate")
    assert not noop["idempotent_replay"]
    client.attachment.unlink(link=first["link"]["id"], expected_version=1, company=COMPANY)
    replay = add(client, target, idempotency_key="duplicate")
    assert replay["idempotent_replay"] and replay["link"] == noop["link"]
    assert client.attachment.list(record_type="customer", record_id=target["id"], company=COMPANY)["count"] == 0


def test_pages_bind_target_and_support_attachment_targets(client, target):
    first = add(client, target, body=b"first")
    second = add(client, target, body=b"second")
    page = client.attachment.list(record_type="customer", record_id=target["id"], limit=1, company=COMPANY)
    assert page["count"] == 1 and page["has_more"] and page["items"][0]["id"] == second["link"]["id"]
    tail = client.attachment.list(record_type="customer", record_id=target["id"], cursor=page["next_cursor"], company=COMPANY)
    assert tail["items"][0]["id"] == first["link"]["id"] and not tail["has_more"]
    with pytest.raises(BookflowError) as error:
        client.attachment.list(record_type="attachment", record_id=first["attachment"]["id"], cursor=page["next_cursor"], company=COMPANY)
    assert error.value.code == "E_VALIDATION"
    for kind, key in (("attachment", first["attachment"]["id"]), ("attachment_link", first["link"]["id"])):
        assert client.note.add(record_type=kind, record_id=key, body="File context", company=COMPANY)["note"]["record_id"] == key


def test_dry_run_and_actual_company_limit(client, target):
    path = company_path(client)
    with open_database(path, writable=True) as db:
        db.conn.execute(schema.company_info.update().values(attachment_max_bytes=3))
    client.company.show(company=COMPANY)  # Establish normal SQLite read sidecars before comparison.
    before = {p.relative_to(path.parent): p.read_bytes() for p in path.parent.rglob("*") if p.is_file()}
    dry = add(client, target, body=b"abc", dry_run=True)
    assert dry["dry_run"]
    after = {p.relative_to(path.parent): p.read_bytes() for p in path.parent.rglob("*") if p.is_file()}
    assert before == after
    with pytest.raises(BookflowError) as error:
        add(client, target, body=b"abcd")
    assert error.value.code == "E_VALUE_RANGE"
    assert add(client, target, body=b"abc")["attachment"]["size_bytes"] == 3


def test_missing_corrupt_bodies_reject_get_and_link(client, target):
    first = add(client, target)
    a = first["attachment"]
    body_path = company_path(client).parent / "attachments" / a["sha256"][:2] / a["sha256"]
    for mutation in (lambda: body_path.write_bytes(b"corrupt"), body_path.unlink):
        mutation()
        for op in (lambda: client.attachment.get(attachment=a["id"], output_stream=io.BytesIO(), company=COMPANY),
                   lambda: client.attachment.link(attachment=a["id"], record_type="customer", record_id=target["id"], company=COMPANY)):
            with pytest.raises(BookflowError) as error:
                op()
            assert error.value.code == "E_IO"


def test_frozen_migration_constraints_and_downgrade(tmp_path):
    path = tmp_path / "company.db"
    with open_database(path, writable=True, create=True) as db:
        command.upgrade(_config("company", db.conn), "co0005")
        before = db.raw.execute("SELECT name, sql FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()
        command.upgrade(_config("company", db.conn), "co0006")
        for table in (schema.attachments, schema.attachment_links, schema.attachment_collection):
            reflected = sa.Table(table.name, sa.MetaData(), autoload_with=db.conn)
            assert [(c.name, str(c.type), c.nullable) for c in reflected.c] == [(c.name, str(c.type), c.nullable) for c in table.c]
        with pytest.raises(sa.exc.IntegrityError):
            db.conn.execute(schema.attachment_collection.insert().values(id=new_id(), payload='"'+"x"*262144+'"'))
        with pytest.raises(sa.exc.IntegrityError):
            db.conn.execute(schema.attachment_collection.insert().values(id=new_id(), payload="invalid"))
        command.downgrade(_config("company", db.conn), "co0005")
        assert db.raw.execute("SELECT name, sql FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%' ORDER BY name").fetchall() == before
    migration = importlib.import_module("bookflow.storage.company_migrations.versions.0006_attachments")
    assert all(isinstance(statement, str) for statement in migration.DDL)


def test_hub_capabilities_additive_and_reversible(tmp_path):
    with open_database(tmp_path / "hub.db", writable=True, create=True) as db:
        command.upgrade(_config("hub", db.conn), "hub0007")
        before = set(db.raw.execute("SELECT * FROM role_capabilities").fetchall())
        command.upgrade(_config("hub", db.conn), "hub0008")
        after = set(db.raw.execute("SELECT * FROM role_capabilities").fetchall())
        assert before < after
        assert ("readonly", "attachment", "member") in after
        assert ("readonly", "attachment", "standard") not in after
        assert ("admin", "attachment", "admin") in after
        assert ("standard", "activity", "member") in after
        command.downgrade(_config("hub", db.conn), "hub0007")
        assert set(db.raw.execute("SELECT * FROM role_capabilities").fetchall()) == before


def test_company_account_inactive_and_cross_company_targets(client, target):
    company = client.company.show(company=COMPANY)
    account = client.account.list(company=COMPANY)["items"][0]
    for kind, key in (("company_info", company["company_id"]), ("account", account["id"])):
        out = add(client, target, record_type=kind, record_id=key)
        assert out["link"]["record_type"] == kind and out["link"]["record_id"] == key
    client.customer.deactivate(customer=target["id"], expected_version=target["version"], company=COMPANY)
    assert add(client, target)["link"]["record_id"] == target["id"]
    other = client.company.new(organization=company["organization_id"], legal_name="Other attachment company", home_currency="USD", timezone="UTC", chart="none")
    with pytest.raises(BookflowError) as error:
        client.attachment.add(record_type="customer", record_id=target["id"], original_filename="x", input_stream=io.BytesIO(BODY), company=other["company_id"])
    assert error.value.code == "E_RECORD_NOT_FOUND"


def test_publication_rollback_retains_only_orphan_bytes(client, target, monkeypatch):
    from bookflow.core import audit
    path = company_path(client)
    original = audit.write_event_to
    def fail(db, ctx, command, *args, **kwargs):
        if command == "attachment add":
            raise RuntimeError("audit failure witness")
        return original(db, ctx, command, *args, **kwargs)
    monkeypatch.setattr(audit, "write_event_to", fail)
    with pytest.raises(RuntimeError, match="audit failure witness"):
        add(client, target)
    with open_database(path, writable=False) as db:
        assert db.raw.execute("SELECT count(*) FROM attachments WHERE sha256 = ?", (hashlib.sha256(BODY).hexdigest(),)).fetchone()[0] == 0
        assert db.raw.execute("SELECT count(*) FROM attachment_links WHERE record_type = ? AND record_id = ?", ("customer", target["id"])).fetchone()[0] == 0
    digest = hashlib.sha256(BODY).hexdigest()
    assert (path.parent / "attachments" / digest[:2] / digest).read_bytes() == BODY
    assert not list((path.parent / "attachments").glob(".attachment-*.tmp"))
    monkeypatch.setattr(audit, "write_event_to", original)
    assert add(client, target)["attachment"]["sha256"] == digest


def test_pending_intent_rejects_dry_upload_without_recovery(client, target):
    path = company_path(client)
    operation = new_id()
    with open_database(path, writable=True) as db:
        db.conn.execute(schema.attachment_collection.insert().values(id=operation, payload="{}"))
    with pytest.raises(BookflowError) as error:
        add(client, target, dry_run=True)
    assert error.value.code == "E_DB_BUSY"
    with open_database(path, writable=False) as db:
        assert db.raw.execute("SELECT id, payload FROM attachment_collection").fetchall() == [(operation, "{}")]
        assert db.raw.execute("SELECT count(*) FROM attachments WHERE sha256 = ?", (hashlib.sha256(BODY).hexdigest(),)).fetchone()[0] == 0
    assert not list((path.parent / "attachments").glob(".attachment-*.tmp"))


@pytest.mark.parametrize("version", [None, 0, -1, True, "1"])
def test_unlink_requires_positive_typed_version(version):
    from bookflow.commands.attachment_cmds import AttachmentUnlinkInput
    with pytest.raises(ValidationError):
        AttachmentUnlinkInput(link=new_id(), expected_version=version)


def test_storage_unique_active_association_and_restricted_attachment(client, target):
    first = add(client, target)
    with open_database(company_path(client), writable=True) as db:
        row = dict(db.conn.execute(sa.select(schema.attachment_links).where(schema.attachment_links.c.id == first["link"]["id"])).mappings().one())
        with pytest.raises(sa.exc.IntegrityError):
            db.conn.execute(schema.attachment_links.insert().values(**dict(row, id=new_id())))
        with pytest.raises(sa.exc.IntegrityError):
            db.conn.execute(schema.attachments.delete().where(schema.attachments.c.id == first["attachment"]["id"]))
        for limit in (0, 100000001):
            with pytest.raises(sa.exc.IntegrityError):
                db.conn.execute(schema.company_info.update().values(attachment_max_bytes=limit))
        db.conn.execute(schema.attachment_links.update().where(schema.attachment_links.c.id == first["link"]["id"]).values(active=False))
        db.conn.execute(schema.attachment_links.insert().values(**dict(row, id=new_id())))
        assert db.raw.execute("SELECT count(*) FROM attachment_links WHERE record_type = ? AND record_id = ?", ("customer", target["id"])).fetchone()[0] == 2
