"""Collection durability, original attribution, exclusion, and bounded discovery."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import sqlalchemy as sa

from bookflow import BookflowError
from bookflow.company import attachment_gc as gc, schema as c
from bookflow.core.context import Context, Interface
from bookflow.core.ids import new_id
from bookflow.core.session import now_iso
from bookflow.storage.engine import open_database

COMPANY = "Demo Plumbing Co"


@pytest.fixture
def catalog(client):
    info = client.company.show(company=COMPANY)
    return Path(info["path"]) / "company.db", info


def body(catalog, content=b"body", *, linked=False):
    db_path, info = catalog
    digest = hashlib.sha256(content).hexdigest()
    path = db_path.parent / "attachments" / digest[:2] / digest
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    with open_database(db_path, True) as db:
        actor = db.conn.execute(sa.select(c.principals.c.user_id).limit(1)).scalar_one()
        at = now_iso()
        common = dict(created_at=at, updated_at=at, created_by=actor, updated_by=actor,
                      created_via="python", updated_via="python", version=1)
        row = dict(id=new_id(), sha256=digest, size_bytes=len(content), media_type="application/octet-stream",
                   original_filename="a.bin", uploaded_by=actor, uploaded_at=at, collected_at=None, **common)
        db.conn.execute(c.attachments.insert().values(**row))
        if linked:
            db.conn.execute(c.attachment_links.insert().values(id=new_id(), attachment_id=row["id"],
                record_type="company_info", record_id=info["company_id"], linked_by=actor,
                linked_at=at, caption="", active=True, **common))
    return path, row


def state(catalog):
    with open_database(catalog[0], False) as db:
        return {
            "intent": [dict(r) for r in db.conn.execute(sa.select(c.attachment_collection)).mappings()],
            "events": [dict(r) for r in db.conn.execute(sa.select(c.audit_events).where(c.audit_events.c.command == "company compact")).mappings()],
            "attachments": [dict(r) for r in db.conn.execute(sa.select(c.attachments)).mappings()],
            "keys": [dict(r) for r in db.conn.execute(sa.select(c.idempotency_keys).where(c.idempotency_keys.c.command == "company compact")).mappings()],
        }


def compact(client, **kw):
    return client.company.compact(company=COMPANY, **kw)


def recover(client):
    from bookflow.core import dispatch, registry
    from bookflow.core.config import Config, os_login
    from bookflow.core.locks import RootLock
    from bookflow.core.session import Session
    root = Path(client.data_root)
    ctx = Context.new(Interface.python, "recovery-test")
    s = Session(root, os_login(), Config.load(root / "config.toml"))
    with RootLock(root, "collection recovery"):
        try:
            dispatch._open_hub(s, True, ctx)
            dispatch._load_actor(s)
            ctx = dispatch.authorize(registry.get("company compact"), ctx, s, company_selector=COMPANY)
            gc.recover_pending(s, ctx)
        finally:
            dispatch._close(s)


def test_collect_keeps_links_and_metadata_and_replays_exact_result(client, catalog):
    path, row = body(catalog)
    live, _ = body(catalog, b"linked", linked=True)
    result = compact(client, idempotency_key="gc-once")
    assert result["operation_id"] and result["collected_count"] == 1 and result["bytes_collected"] == 4
    assert not path.exists() and live.read_bytes() == b"linked"
    snap = state(catalog)
    assert len(snap["events"]) == 1 and not snap["intent"]
    collected = next(r for r in snap["attachments"] if r["id"] == row["id"])
    assert collected["collected_at"] and collected["version"] == 2
    assert collected["uploaded_at"] == row["uploaded_at"]
    replay = compact(client, idempotency_key="gc-once")
    assert replay == dict(result, idempotent_replay=True)
    assert state(catalog) == snap
    with pytest.raises(BookflowError) as exc:
        compact(client, limit=1, idempotency_key="gc-once")
    assert exc.value.code == "E_IDEMPOTENCY_MISMATCH"


def test_dry_run_projects_without_files_or_intent(client, catalog):
    path, _ = body(catalog)
    before = state(catalog)
    result = compact(client, dry_run=True)
    assert result["operation_id"] is None and result["dry_run"]
    assert result["collected_count"] == 1 and result["bytes_collected"] == 4
    assert path.read_bytes() == b"body" and state(catalog) == before


@pytest.mark.parametrize("boundary", ["unlink", "sync", "audit", "idempotency"])
def test_failure_retains_intent_and_recovery_original_completion(client, catalog, monkeypatch, boundary):
    path, row = body(catalog)
    def fail(*a, **kw):
        raise OSError("injected crash boundary")
    with monkeypatch.context() as patch:
        if boundary == "unlink":
            patch.setattr(Path, "unlink", fail)
        elif boundary == "sync":
            patch.setattr(gc, "sync_directory", fail)
        elif boundary == "audit":
            patch.setattr(gc.audit, "write_event_to", fail)
        else:
            patch.setattr(gc.idempotency, "store", fail)
        with pytest.raises(BookflowError) as exc:
            compact(client, idempotency_key="interrupted", reason="original reason")
        assert exc.value.code == "E_IO"
    before = state(catalog)
    assert len(before["intent"]) == 1 and not before["events"] and not before["keys"]
    payload = json.loads(before["intent"][0]["payload"])
    assert payload["candidates"][0]["before"] == row
    assert path.exists() == (boundary == "unlink")
    # Dry-run must reject pending work without touching even already-missing bytes.
    with pytest.raises(BookflowError) as exc:
        compact(client, dry_run=True)
    assert exc.value.code == "E_DB_BUSY" and state(catalog) == before
    recover(client)
    done = state(catalog)
    original = next(event for event in done["events"] if event["request_id"] == payload["context"]["request_id"])
    assert original["reason"] == "original reason" and original["actor_id"] == payload["actor_id"]
    assert not done["intent"] and not path.exists() and len(done["events"]) == 1
    assert sum(e["request_id"] == original["request_id"] for e in done["events"]) == 1
    replay = compact(client, idempotency_key="interrupted", reason="different retry reason")
    assert replay == dict(payload["output"], idempotent_replay=True)
    assert next(r for r in done["attachments"] if r["id"] == row["id"])["version"] == 2


def test_recovery_halts_for_unexpected_active_link(client, catalog, monkeypatch):
    path, row = body(catalog)
    with monkeypatch.context() as patch:
        patch.setattr(gc, "_finish", lambda *a: (_ for _ in ()).throw(OSError("crash")))
        with pytest.raises(BookflowError):
            compact(client)
    with open_database(catalog[0], True) as db:
        common = {k: row[k] for k in ("created_at", "updated_at", "created_by", "updated_by", "created_via", "updated_via", "version")}
        db.conn.execute(c.attachment_links.insert().values(id=new_id(), attachment_id=row["id"],
            record_type="company_info", record_id=catalog[1]["company_id"], linked_by=row["uploaded_by"],
            linked_at=now_iso(), caption="", active=True, **common))
    before = state(catalog)
    with pytest.raises(BookflowError) as exc:
        recover(client)
    assert exc.value.code == "E_IO" and path.exists() and state(catalog) == before


@pytest.mark.parametrize("kind", ["file_symlink", "shard_symlink", "directory"])
def test_unsafe_digest_never_removed(client, catalog, tmp_path, kind):
    path, _ = body(catalog)
    path.unlink()
    outside = tmp_path / "outside"
    outside.mkdir()
    protected = outside / path.name
    protected.write_bytes(b"protected")
    if kind == "file_symlink":
        path.symlink_to(protected)
    elif kind == "shard_symlink":
        path.parent.rmdir()
        path.parent.symlink_to(outside, target_is_directory=True)
    else:
        path.mkdir()
    with pytest.raises(BookflowError) as exc:
        compact(client)
    assert exc.value.code == "E_IO" and protected.read_bytes() == b"protected"
    assert path.is_symlink() or path.is_dir() or path.parent.is_symlink()
    assert not state(catalog)["events"]


def test_gate_precedes_candidates_and_reopens(client, catalog, monkeypatch):
    body(catalog)
    from bookflow.core.session import Session
    original_release, original_candidates = Session.release_company, gc.metadata_candidates
    events = []
    def release(s, company_id):
        events.append("gate")
        original_release(s, company_id)
        assert s.company is None
    def candidates(s, limit):
        assert events == ["gate"] and s.company.writable
        events.append("select")
        return original_candidates(s, limit)
    monkeypatch.setattr(Session, "release_company", release)
    monkeypatch.setattr(gc, "metadata_candidates", candidates)
    compact(client)
    assert events == ["gate", "select"]


def test_transfer_resource_never_acquires_gate():
    s = SimpleNamespace(transfer=object())
    for call in (lambda: gc.recover_pending(s, None), lambda: gc.collect(s, None, 200, "hash")):
        with pytest.raises(BookflowError) as exc:
            call()
        assert exc.value.code == "E_DB_BUSY"


def test_orphan_and_temp_bounded_discovery_continues(client, catalog, monkeypatch):
    store = catalog[0].parent / "attachments"
    shard = store / "00"
    shard.mkdir(parents=True, exist_ok=True)
    paths = []
    for i in range(35):
        path = shard / ("00" + f"{i:062x}")
        path.write_bytes(b"orphan")
        paths.append(path)
    temp = store / ".attachment-abcd1234.tmp"
    temp.write_bytes(b"temporary")
    unknown = store / "company.db"
    unknown.write_bytes(b"keep")
    directory = store / ".attachment-abcdefgh.tmp"
    directory.mkdir()
    # Unrecognized directories stay untouched; recognized unsafe temp names halt.
    directory.rmdir()
    monkeypatch.setattr(gc, "MAX_SCAN", 16)
    counts = []
    for _ in range(50):
        result = compact(client, limit=7)
        counts.append(result["collected_count"])
        assert result["collected_count"] <= 7
        if not result["has_more"]:
            break
    assert not result["has_more"] and sum(counts) == 36
    assert not any(p.exists() for p in paths) and not temp.exists() and unknown.read_bytes() == b"keep"


@pytest.mark.parametrize("limit", [0, 201, True, "2"])
def test_limit_validation(client, limit):
    with pytest.raises(BookflowError) as exc:
        compact(client, limit=limit)
    assert exc.value.code == "E_VALIDATION"


def test_admin_role_and_agent_reason(client, root, catalog):
    from tests.conftest import make_actor, as_user
    from tests.test_row2_flow import _agent_session
    for role in ("readonly", "standard"):
        make_actor(root, role, company_role=(catalog[1]["company_id"], role))
        with pytest.raises(BookflowError) as exc:
            compact(as_user(root, role))
        assert exc.value.code == "E_PERMISSION"
    run = _agent_session(root, client)
    with pytest.raises(BookflowError) as exc:
        run("company compact", {})
    assert exc.value.code == "E_REASON_REQUIRED"
    result = run("company compact", {}, reason="Collect abandoned receipts")
    assert result["operation_id"]


def test_live_host_lease_blocks_collection_before_selection(client, catalog, root, monkeypatch):
    from bookflow.core.host import Host
    from bookflow.core.context import client_version
    from bookflow.core import dispatch, registry
    path, _ = body(catalog)
    actor = client.init()["user_id"]
    ctx = Context.new(Interface.python, "host-compact-test")
    host = Host(root, version=client_version(), filesystem_wait_seconds=0.02)
    host.start()
    cmd = registry.get("company compact")
    def invoke(s):
        return dispatch.run_in_session(cmd, cmd.input_model(), ctx, s, company_selector=COMPANY)
    try:
        before = state(catalog)
        with host.acquire_transfer(actor, catalog[1]["company_id"]):
            with monkeypatch.context() as patch:
                patch.setattr(gc, "metadata_candidates", lambda *a: pytest.fail("selected before exclusion"))
                with pytest.raises(BookflowError) as exc:
                    host.run_write(actor, "", invoke)
            assert exc.value.code == "E_DB_BUSY"
            assert state(catalog) == before and path.exists()
        result = host.run_write(actor, "", invoke)
        assert result["collected_count"] == 1 and not path.exists()
        assert not host._filesystem_exclusive
    finally:
        host.stop()


def test_hard_200_limit_retains_bounded_intent(client, catalog, monkeypatch):
    paths = [body(catalog, str(n).encode())[0] for n in range(201)]
    with monkeypatch.context() as patch:
        patch.setattr(gc, "_finish", lambda *a: (_ for _ in ()).throw(OSError("intent committed")))
        with pytest.raises(BookflowError):
            compact(client)
    intent = state(catalog)["intent"][0]
    payload = json.loads(intent["payload"])
    assert len(payload["candidates"]) == 200 and len(intent["payload"].encode()) <= 262144
    assert payload["output"]["has_more"] and all(p.exists() for p in paths)
    recover(client)
    assert sum(p.exists() for p in paths) == 1
    assert compact(client)["collected_count"] == 1


def test_readonly_recovery_is_inert(catalog):
    with open_database(catalog[0], False) as db:
        s = SimpleNamespace(company=db, dry_run=False, transfer=None)
        gc.recover_pending(s, None)
        s.dry_run = True
        gc.recover_pending(s, None)


def test_final_commit_failure_rolls_back_completion_not_intent(client, catalog, monkeypatch):
    path, _ = body(catalog)
    original = gc._finish
    def interrupted(s, operation_id, payload):
        raw = s.company.raw
        class FailCommit:
            def __getattr__(self, name):
                return getattr(raw, name)
            def execute(self, sql, *args):
                if sql == "COMMIT":
                    raise OSError("completion commit failed")
                return raw.execute(sql, *args)
        s.company.raw = FailCommit()
        try:
            return original(s, operation_id, payload)
        finally:
            s.company.raw = raw
    with monkeypatch.context() as patch:
        patch.setattr(gc, "_finish", interrupted)
        with pytest.raises(BookflowError) as exc:
            compact(client, idempotency_key="commit-failure")
    assert exc.value.code == "E_IO"
    snap = state(catalog)
    assert snap["intent"] and not snap["events"] and not snap["keys"] and not path.exists()
    recover(client)
    done = state(catalog)
    assert len(done["events"]) == 1 and len(done["keys"]) == 1 and not done["intent"]


def test_failed_restore_body_is_collected_without_rewriting_metadata(client, catalog, monkeypatch):
    import io
    from bookflow.company import attachment_store
    path, row = body(catalog, b"restorable")
    compact(client)
    collected = next(r for r in state(catalog)["attachments"] if r["id"] == row["id"])
    # Publication precedes the restoration metadata transaction. Simulate its
    # rollback after the exact body is published successfully.
    store = catalog[0].parent / "attachments"
    store.chmod(0o700)
    path.parent.chmod(0o700)
    with attachment_store.stage(store, io.BytesIO(b"restorable"), 100) as staged:
        attachment_store.publish(store, staged)
    with open_database(catalog[0], True) as db:
        db.raw.execute("BEGIN IMMEDIATE")
        db.conn.execute(c.attachments.update().where(c.attachments.c.id == row["id"]).values(collected_at=None))
        db.raw.rollback()
    assert path.read_bytes() == b"restorable"
    original = gc._finish
    def check_intent(s, operation_id, payload):
        candidates = [item for item in payload["candidates"] if item["sha256"] == row["sha256"]]
        assert len(candidates) == 1 and candidates[0]["before"] is None
        return original(s, operation_id, payload)
    monkeypatch.setattr(gc, "_finish", check_intent)
    result = compact(client)
    assert result["collected_count"] == 1 and result["bytes_collected"] == len(b"restorable")
    assert not path.exists()
    assert next(r for r in state(catalog)["attachments"] if r["id"] == row["id"]) == collected


def test_collected_metadata_with_active_link_is_not_an_orphan(client, catalog):
    path, row = body(catalog, b"protected collected row", linked=True)
    with open_database(catalog[0], True) as db:
        db.conn.execute(c.attachments.update().where(c.attachments.c.id == row["id"]).values(collected_at=now_iso()))
    before = state(catalog)["attachments"]
    compact(client)
    assert path.read_bytes() == b"protected collected row"
    assert state(catalog)["attachments"] == before


@pytest.mark.parametrize("platform,pointer_size,long_size", [("darwin", 8, 8), ("win32", 8, 4), ("linux", 4, 4), ("linux", 8, 4)])
def test_native_discovery_rejects_unsupported_abis(tmp_path, monkeypatch, platform, pointer_size, long_size):
    monkeypatch.setattr(gc.sys, "platform", platform)
    actual_sizeof = gc.ctypes.sizeof
    def sizeof(kind):
        if kind is gc.ctypes.c_void_p:
            return pointer_size
        if kind is gc.ctypes.c_long:
            return long_size
        return actual_sizeof(kind)
    monkeypatch.setattr(gc.ctypes, "sizeof", sizeof)
    monkeypatch.setattr(gc.ctypes, "CDLL", lambda *a, **kw: pytest.fail("unsupported native ABI reached libc"))
    assert gc._scan_page(tmp_path, 123, 5) == ([], 123, False, 0)


def test_native_directory_names_use_bounded_reads(tmp_path, monkeypatch):
    if gc.sys.platform != "linux" or gc.ctypes.sizeof(gc.ctypes.c_void_p) != 8:
        pytest.skip("native discovery requires Linux LP64")
    longest = "x" * gc.NAME_MAX
    (tmp_path / longest).write_bytes(b"bounded")
    original = gc.ctypes.string_at
    reads = []
    def bounded(address, size=None):
        assert size is not None and 0 < size <= gc.NAME_MAX + 1
        reads.append(size)
        return original(address, size)
    monkeypatch.setattr(gc.ctypes, "string_at", bounded)
    names, _, done, used = gc._scan_page(tmp_path, 0, 8)
    assert names == [longest] and done and used <= 8 and reads
