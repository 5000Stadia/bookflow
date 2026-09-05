"""Independent storage and portability witnesses for attachment migration."""

import io
from pathlib import Path
import shutil
import sqlite3

import bookflow
import pytest
import sqlalchemy as sa
from alembic import command

from bookflow.company import schema
from bookflow.storage.engine import open_database, sqlite_uri
from bookflow.storage.migrate import HEADS, _config, current_revision_raw, migrate_to_head
from tests.conftest import _seeded_template, root, client  # noqa: F401
from tests.test_migration_chain import _make_revision, _normalized_schema
from tests.test_row6_note_migration import _populate_principal, _note_values, _schema_semantics

COMPANY = "Demo Plumbing Co"


def _old_data(conn):
    _populate_principal(conn)
    values = _note_values()
    conn.execute(f"INSERT INTO notes ({','.join(values)}) VALUES ({','.join('?' for _ in values)})", tuple(values.values()))


def test_co6_preserves_co5_notes_principals_and_verified_backup(tmp_path, monkeypatch):
    monkeypatch.setitem(HEADS, "company", "co0006")
    old, fresh = tmp_path / "old.db", tmp_path / "fresh.db"
    _make_revision(old, "company", "co0005", _old_data)
    _make_revision(fresh, "company", "co0006", _old_data)
    with sqlite3.connect(old) as conn:
        before = _normalized_schema(conn)
        rows = {name: conn.execute(f"SELECT * FROM {name}").fetchall() for name in ("notes", "principals")}
    backups = tmp_path / "backups"
    with open_database(old, writable=True) as db:
        assert migrate_to_head(db, "company", backups) == ("co0005", "co0006")
        for name, expected in rows.items():
            assert db.raw.execute(f"SELECT * FROM {name}").fetchall() == expected
        assert db.raw.execute("PRAGMA foreign_key_check").fetchall() == []
        assert migrate_to_head(db, "company", backups) == ("co0006", "co0006")
    assert _schema_semantics(old) == _schema_semantics(fresh)
    saved, = backups.glob("*-from-co0005.db")
    assert current_revision_raw(saved) == "co0005"
    with sqlite3.connect(sqlite_uri(saved, "ro"), uri=True) as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert _normalized_schema(conn) == before
        for name, expected in rows.items():
            assert conn.execute(f"SELECT * FROM {name}").fetchall() == expected


def test_co6_ddl_does_not_follow_live_metadata(tmp_path, monkeypatch):
    baseline, changed = tmp_path / "baseline.db", tmp_path / "changed.db"
    _make_revision(baseline, "company", "co0006", _old_data)
    _make_revision(changed, "company", "co0005", _old_data)
    # Replace both likely sources of live DDL, only in this test process.
    unrelated = sa.MetaData()
    sa.Table("future_only", unrelated, sa.Column("id", sa.Integer, primary_key=True))
    monkeypatch.setattr(schema, "metadata", unrelated)
    for name in ("attachments", "attachment_links", "attachment_collection", "company_info"):
        monkeypatch.setattr(schema, name, unrelated.tables["future_only"])
    with open_database(changed, writable=True) as db:
        command.upgrade(_config("company", db.conn), "co0006")
    assert _schema_semantics(changed) == _schema_semantics(baseline)


def test_upgrade_existing_company_default_and_limit_constraints(tmp_path):
    path = tmp_path / "company.db"
    def populate(conn):
        _old_data(conn)
        conn.execute("INSERT INTO company_info (id, version, created_at, created_by, created_via, updated_at, updated_by, updated_via, legal_name, display_name, tax_id_kind, entity_type, income_tax_form, fiscal_year_start_month, tax_year_start_month, report_basis, home_currency, timezone, recent_activity_window_seconds) VALUES ('C1',1,'t','U1','cli','t','U1','cli','Preserved Company','Preserved Company','ein','other','other',1,1,'accrual','USD','UTC',60)")
    _make_revision(path, "company", "co0005", populate)
    with open_database(path, writable=True) as db:
        original = db.raw.execute("SELECT id, legal_name FROM company_info").fetchall()
        command.upgrade(_config("company", db.conn), "co0006")
        assert db.raw.execute("SELECT id, legal_name FROM company_info").fetchall() == original
        assert db.raw.execute("SELECT attachment_max_bytes FROM company_info").fetchall() == [(25_000_000,)]
        for invalid in (0, -1, 100_000_001, None):
            with pytest.raises(sqlite3.IntegrityError):
                db.raw.execute("UPDATE company_info SET attachment_max_bytes=?", (invalid,))
        for valid in (1, 100_000_000):
            db.raw.execute("UPDATE company_info SET attachment_max_bytes=?", (valid,))
            assert db.raw.execute("SELECT attachment_max_bytes FROM company_info").fetchone() == (valid,)


def _insert(conn, table, values):
    conn.execute(f"INSERT INTO {table} ({','.join(values)}) VALUES ({','.join('?' for _ in values)})", tuple(values.values()))


def test_attachment_constraints_are_enforced_by_sqlite(tmp_path):
    path = tmp_path / "company.db"
    _make_revision(path, "company", "co0006", _old_data)
    common = dict(version=1, created_at="t", created_by="U1", created_via="python", updated_at="t", updated_by="U1", updated_via="python")
    attachment = dict(common, id="A1", sha256="a"*64, size_bytes=0, media_type="application/pdf", original_filename="receipt.pdf", uploaded_by="U1", uploaded_at="t")
    link = dict(common, id="L1", attachment_id="A1", record_type="principal", record_id="U1", linked_by="U1", linked_at="t", caption="", active=1)
    with open_database(path, writable=True) as db:
        for change in ({"sha256": "A"*64}, {"sha256": "a"*63}, {"size_bytes": -1}, {"original_filename": ""}, {"original_filename": "é"*128}):
            with pytest.raises(sqlite3.IntegrityError):
                _insert(db.raw, "attachments", {**attachment, **change})
        _insert(db.raw, "attachments", attachment)
        with pytest.raises(sqlite3.IntegrityError):
            _insert(db.raw, "attachments", {**attachment, "id": "A2"})
        for change in ({"attachment_id": "missing"}, {"caption": "é"*1025}):
            with pytest.raises(sqlite3.IntegrityError):
                _insert(db.raw, "attachment_links", {**link, **change})
        _insert(db.raw, "attachment_links", link)
        with pytest.raises(sqlite3.IntegrityError):
            _insert(db.raw, "attachment_links", {**link, "id": "L2"})
        _insert(db.raw, "attachment_links", {**link, "id": "L2", "active": 0})
        with pytest.raises(sqlite3.IntegrityError):
            db.raw.execute("UPDATE attachment_links SET active=1 WHERE id='L2'")
        with pytest.raises(sqlite3.IntegrityError):
            db.raw.execute("DELETE FROM attachments WHERE id='A1'")
        for invalid in ("not json", '"' + 'a'*262144 + '"'):
            with pytest.raises(sqlite3.IntegrityError):
                _insert(db.raw, "attachment_collection", {"id": "GC", "payload": invalid})
        _insert(db.raw, "attachment_collection", {"id": "GC", "payload": "{}"})


def test_copied_root_keeps_pdf_metadata_notes_and_audit(client, root, tmp_path):
    target = client.customer.create(name="Portable files", company=COMPANY)
    # Use the actual seeded PDF, then attach through the public client.
    folder = Path(client.company.show(company=COMPANY)["path"])
    with sqlite3.connect(folder / "company.db") as conn:
        digest, = conn.execute("SELECT sha256 FROM attachments WHERE media_type='application/pdf' LIMIT 1").fetchone()
    pdf = (folder / "attachments" / digest[:2] / digest).read_bytes()
    assert pdf.startswith(b"%PDF-")
    added = client.attachment.add(record_type="customer", record_id=target["id"], original_filename="Portable é.pdf", caption="Keep me", media_type="application/pdf", input_stream=io.BytesIO(pdf), company=COMPANY)
    client.note.add(record_type="customer", record_id=target["id"], body="Portable note é\n", company=COMPANY)
    def history(c):
        return (c.note.list(record_type="customer", record_id=target["id"], company=COMPANY), c.audit.list(company=COMPANY), c.attachment.list(record_type="customer", record_id=target["id"], company=COMPANY))
    before = history(client)
    copied = tmp_path / "copied"
    shutil.copytree(root, copied)
    # Make source unavailable, proving reads cannot silently use its absolute paths.
    root.rename(tmp_path / "source-unavailable")
    other = bookflow.connect(data_root=str(copied))
    sink = io.BytesIO()
    metadata = other.attachment.get(attachment=added["attachment"]["id"], output_stream=sink, company=COMPANY)
    assert sink.getvalue() == pdf
    assert metadata == added["attachment"]
    assert history(other) == before


def test_hub8_preserves_existing_capabilities_and_backup(tmp_path, monkeypatch):
    monkeypatch.setitem(HEADS, "hub", "hub0008")
    old = tmp_path / "hub.db"
    custom = ("owner", "local-extension", "owner")
    _make_revision(old, "hub", "hub0007", lambda conn: conn.execute("INSERT INTO role_capabilities VALUES (?,?,?)", custom))
    query = "SELECT role, capability, required_role FROM role_capabilities"
    with sqlite3.connect(old) as conn:
        original = set(conn.execute(query))
        before = _normalized_schema(conn)
    backups = tmp_path / "backups"
    with open_database(old, writable=True) as db:
        assert migrate_to_head(db, "hub", backups) == ("hub0007", "hub0008")
        added = set(db.raw.execute(query)) - original
        assert original <= set(db.raw.execute(query))
        expected = {(role, capability, required)
                    for capability, required, roles in (
                        ("attachment", "member", ("readonly", "standard", "admin", "owner", "hub_admin")),
                        ("attachment", "standard", ("standard", "admin", "owner", "hub_admin")),
                        ("attachment", "admin", ("admin", "owner", "hub_admin")),
                        ("activity", "member", ("readonly", "standard", "admin", "owner", "hub_admin")))
                    for role in roles}
        assert added == expected
        assert _normalized_schema(db.raw) == before
    saved, = backups.glob("hub-*-from-hub0007.db")
    with sqlite3.connect(sqlite_uri(saved, "ro"), uri=True) as conn:
        assert current_revision_raw(saved) == "hub0007"
        assert conn.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert set(conn.execute(query)) == original
        assert _normalized_schema(conn) == before
