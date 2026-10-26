"""A write refused by one of Bookflow's own guard triggers is E_GUARD_REFUSED, never E_IO.

The trigger fires only when the application's own checks let a forbidden write through, so the
refusal is a Bookflow bug: it must say so plainly, carry the guard's own message and the tables it
guards, and leave nothing saved. A genuine I/O failure, and a raw constraint violation that is not
one of our guards, keep their old E_IO.
"""
import json
import sqlite3
from pathlib import Path

import anyio
import pytest

import bookflow
from bookflow import BookflowError
from bookflow.core import audit
from bookflow.core.dispatch import guard
from bookflow.core.errors import INFRASTRUCTURE_CODES

PLANTED = "planted guard refuses this vendor"
REFUSED = "Refused Vendor"


def _company_db(client) -> Path:
    cid = client.company.list()["items"][0]["company_id"]
    return Path(client.company.show(company=cid)["path"]) / "company.db"


def _plant(path: Path) -> None:
    """A guard of the shape every Bookflow guard has, on the vendors table, that the app does not know."""
    db = sqlite3.connect(path)
    try:
        db.execute(f"CREATE TRIGGER vendors_planted_guard BEFORE INSERT ON vendors WHEN NEW.name = '{REFUSED}' "
                   f"BEGIN SELECT RAISE(ABORT, '{PLANTED}'); END")
        db.commit()
        db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        db.close()


def _vendor_names(path: Path) -> list[str]:
    db = sqlite3.connect(path)
    try:
        return [row[0] for row in db.execute("SELECT name FROM vendors")]
    finally:
        db.close()


def _assert_refusal(document, surface, *, guard_text=PLANTED, table="vendors"):
    assert document["code"] == "E_GUARD_REFUSED", (surface, document)
    message = document["message"]
    assert "safety check" in message and "nothing was saved" in message and "bug" in message, (surface, message)
    assert guard_text in message and "filesystem" not in message, (surface, message)
    details = document["details"]
    assert details["guard"] == guard_text and table in details["tables"], (surface, details)
    assert details["command"] == "vendor create" and details["request_id"], (surface, details)


def test_planted_guard_is_named_on_python_cli_http_and_mcp(client, root, tmp_path):
    pytest.importorskip("mcp")
    from tests.mcp_matrix_support import Matrix
    path = _company_db(client)
    _plant(path)
    before = _vendor_names(path)

    async def witness():
        matrix = Matrix()
        try:
            await matrix.open(root, tmp_path / "surfaces")
            for surface in ("python", "cli", "http", "mcp"):
                document = await matrix.call(surface, "vendor create", {"name": REFUSED}, rejected=True)
                _assert_refusal(document, surface)
                kept = Path(matrix.roots[surface]) / path.relative_to(root)
                assert _vendor_names(kept) == before, surface
        finally:
            await matrix.close()

    anyio.run(witness)


def test_cli_exit_and_document(client, root, cli):
    path = _company_db(client)
    _plant(path)
    cid = client.company.list()["items"][0]["company_id"]
    document, code = cli.error("--company", cid, "vendor", "create", "--name", REFUSED)
    assert code == 1
    _assert_refusal(document, "cli")


def test_real_guard_bypassed_inside_a_command_and_the_retry(client, root, monkeypatch, caplog):
    """An existing history guard, reached by a direct write inside a command the app let through."""
    path = _company_db(client)
    real = audit.write_event_to

    def bypassing(db, *args, **kwargs):
        if getattr(db, "raw", None) is not None and db.raw.execute(
                "SELECT 1 FROM sqlite_master WHERE name = 'work_lines'").fetchone():
            db.raw.execute("UPDATE work_lines SET id = id")
        return real(db, *args, **kwargs)

    monkeypatch.setattr(audit, "write_event_to", bypassing)
    before = _vendor_names(path)
    cid = client.company.list()["items"][0]["company_id"]
    for _ in range(2):  # the retry with the same key meets the same check: nothing was stored
        with pytest.raises(BookflowError) as refused:
            client.vendor.create(company=cid, name="Bypass Vendor", idempotency_key="guard-retry")
        _assert_refusal(refused.value.to_dict(), "python", guard_text="immutable work history", table="work_lines")
        assert _vendor_names(path) == before
    assert any("guard trigger refused a write" in r.getMessage() and "immutable work history" in r.getMessage()
               for r in caplog.records)
    monkeypatch.setattr(audit, "write_event_to", real)
    made = client.vendor.create(company=cid, name="Bypass Vendor", idempotency_key="guard-retry")
    again = client.vendor.create(company=cid, name="Bypass Vendor", idempotency_key="guard-retry")
    assert again["id"] == made["id"] and _vendor_names(path).count("Bypass Vendor") == 1


def test_raw_constraint_and_io_failures_stay_e_io():
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE t (a UNIQUE)")
    db.execute("INSERT INTO t VALUES (1)")
    with pytest.raises(BookflowError) as unique:
        guard(lambda: db.execute("INSERT INTO t VALUES (1)"), True)
    assert unique.value.code == "E_IO" and unique.value.details["errno"] == "EIO"
    assert "UNIQUE" not in json.dumps(unique.value.to_dict())

    def locked():
        raise sqlite3.OperationalError("database is locked")
    with pytest.raises(BookflowError) as busy:
        guard(locked, True)
    assert busy.value.code == "E_IO" and busy.value.details["errno"] == "EBUSY"

    def full():
        raise OSError(28, "No space left on device")
    with pytest.raises(BookflowError) as disk:
        guard(full, True)
    assert disk.value.code == "E_IO" and disk.value.details["errno"] == "ENOSPC"
    assert "E_GUARD_REFUSED" in INFRASTRUCTURE_CODES
