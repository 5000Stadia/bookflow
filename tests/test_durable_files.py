"""Filesystem commit ordering and recovery, using disposable files and data roots."""

import errno
import os
import sqlite3
import stat
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
import sqlalchemy as sa

from bookflow.core import durability
from bookflow.core.config import Config
from bookflow.core.errors import BookflowError
from bookflow.hub import schema as h
from bookflow.storage.engine import open_database
from bookflow.storage.paths import (
    read_company_marker,
    write_company_marker,
    write_org_marker,
)


@pytest.mark.skipif(os.name != "posix", reason="POSIX mode and directory-fsync witness")
def test_metadata_order_private_unique_temps_and_exact_cleanup(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    path.write_text("old")
    unrelated = tmp_path / "config.toml.tmp"
    unrelated.write_text("not owned by this operation")
    events = []
    temps = []
    real_fsync, real_replace = os.fsync, os.replace

    def fsync(fd):
        kind = "directory" if stat.S_ISDIR(os.fstat(fd).st_mode) else "file"
        events.append(kind)
        real_fsync(fd)

    def replace(src, dst):
        src, dst = Path(src), Path(dst)
        assert src.parent == dst.parent == tmp_path
        assert stat.S_IMODE(src.stat().st_mode) == 0o600
        assert src.read_text() == "new"
        temps.append(src)
        events.append("replace")
        real_replace(src, dst)

    monkeypatch.setattr(os, "fsync", fsync)
    monkeypatch.setattr(os, "replace", replace)
    old_umask = os.umask(0)
    try:
        durability.write_metadata(path, "new")
        durability.write_metadata(path, "new")
    finally:
        os.umask(old_umask)
    assert events == ["file", "replace", "directory"] * 2
    assert temps[0] != temps[1] and all(not p.exists() for p in temps)
    assert path.read_text() == "new" and stat.S_IMODE(path.stat().st_mode) == 0o600
    assert unrelated.read_text() == "not owned by this operation"
    assert set(tmp_path.iterdir()) == {path, unrelated}


@pytest.mark.parametrize("failure", ["file", "replace", "directory"])
def test_metadata_failures_report_actual_state_and_retry(tmp_path, monkeypatch, failure):
    if failure == "directory" and os.name != "posix":
        pytest.skip("POSIX directory-fsync failure witness")
    path = tmp_path / "marker.toml"
    path.write_text("old")
    unrelated = tmp_path / ".marker.toml.somebody-else.tmp"
    unrelated.write_text("leave alone")
    real_fsync, real_replace = os.fsync, os.replace

    def fsync(fd):
        kind = "directory" if stat.S_ISDIR(os.fstat(fd).st_mode) else "file"
        if kind == failure:
            raise OSError(errno.EIO, "injected synchronization failure")
        real_fsync(fd)

    def replace(src, dst):
        if failure == "replace":
            raise OSError(errno.EACCES, "injected replacement failure")
        real_replace(src, dst)

    with monkeypatch.context() as patch:
        patch.setattr(os, "fsync", fsync)
        patch.setattr(os, "replace", replace)
        with pytest.raises(OSError):
            durability.write_metadata(path, "new")
    assert path.read_text() == ("new" if failure == "directory" else "old")
    assert set(tmp_path.iterdir()) == {path, unrelated}
    durability.write_metadata(path, "new")
    assert path.read_text() == "new"


@pytest.mark.parametrize("caller", ["config", "company", "organization"])
def test_config_and_markers_use_shared_durable_writer(tmp_path, monkeypatch, caller):
    calls = []
    monkeypatch.setattr(durability, "sync_directory", lambda path: calls.append(path))
    if caller == "config":
        config = Config(tmp_path / "config.toml")
        config.set_user("test-login", "user-id")
        config.save()
        assert Config.load(config.path).user_table("test-login")["user_id"] == "user-id"
    elif caller == "company":
        write_company_marker(tmp_path, company_id="company-id", state="ready")
        assert read_company_marker(tmp_path)["company_id"] == "company-id"
    else:
        write_org_marker(tmp_path, organization_id="organization-id")
    assert calls == [tmp_path]


@pytest.mark.skipif(os.name != "posix", reason="POSIX directory-fsync witness")
def test_directory_fsync_failure_closes_descriptor(tmp_path, monkeypatch):
    descriptors = []

    def fail(fd):
        descriptors.append(fd)
        raise OSError(errno.EIO, "injected fsync failure")

    monkeypatch.setattr(os, "fsync", fail)
    with pytest.raises(OSError):
        durability.sync_directory(tmp_path)
    assert len(descriptors) == 1
    with pytest.raises(OSError) as error:
        os.fstat(descriptors[0])
    assert error.value.errno == errno.EBADF


def test_move_synchronizes_both_parents_after_rename(tmp_path, monkeypatch):
    from bookflow.core.moves import move_dir

    src_parent, dst_parent = tmp_path / "source", tmp_path / "destination"
    src_parent.mkdir()
    dst_parent.mkdir()
    src, dst = src_parent / "folder", dst_parent / "folder"
    src.mkdir()
    observed = []

    def sync(path):
        assert dst.exists() and not src.exists()
        observed.append(path)

    monkeypatch.setattr(durability, "sync_directory", sync)
    move_dir(src, dst)
    assert observed == [dst_parent, src_parent]


def _hub_row(root, table, row_id):
    with open_database(root / "hub.db", writable=False) as db:
        row = db.conn.execute(sa.select(table).where(table.c.id == row_id)).mappings().first()
        return dict(row) if row else None


@pytest.mark.parametrize("noun", ["company", "organization"])
def test_move_sync_failure_preserves_pending_and_already_moved_retry(client, root, monkeypatch, noun):
    from bookflow.core import moves as core_moves
    from bookflow.hub import moves as hub_moves

    api = getattr(client, noun)
    row_id = api.list()["items"][0][f"{noun}_id"]
    table = h.companies if noun == "company" else h.organizations
    before = _hub_row(root, table, row_id)
    name = "Durable Company" if noun == "company" else "Durable Organization"
    src = root / before["path"]
    dst = src.with_name(name)
    observed = []

    def fail(old, new):
        assert old == src and new == dst
        assert not src.exists() and dst.exists()
        pending = _hub_row(root, table, row_id)
        assert pending["pending_path"] == str(dst.relative_to(root))
        assert pending["path"] == before["path"]
        observed.append("pending")
        raise OSError(errno.EIO, "injected move sync failure")

    with monkeypatch.context() as patch:
        patch.setattr(core_moves, "sync_move_parents", fail)
        patch.setattr(hub_moves, "sync_move_parents", fail)
        for _ in range(2):
            with pytest.raises(BookflowError) as error:
                api.rename(**{noun: row_id}, name=name, move=True)
            assert error.value.code == "E_RENAME_INCOMPLETE"
    # Organization repair is attempted on open, then by the explicit rename.
    assert observed == ["pending"] * (3 if noun == "organization" else 2)
    observed.clear()
    real_sync = durability.sync_move_parents

    def sync(old, new):
        assert _hub_row(root, table, row_id)["pending_path"] is not None
        observed.append("sync")
        real_sync(old, new)

    monkeypatch.setattr(hub_moves, "sync_move_parents", sync)
    out = api.rename(**{noun: row_id}, name=name, move=True)
    after = _hub_row(root, table, row_id)
    assert observed == ["sync"] and out["moved"]
    assert after["pending_path"] is None and after["path"] == str(dst.relative_to(root))
    assert after["version"] == before["version"] + 2


@pytest.mark.parametrize("kind", ["company", "organization"])
@pytest.mark.parametrize("location", ["target", "hop"])
def test_recovery_refuses_unrelated_target_or_hop(tmp_path, kind, location):
    from bookflow.hub.moves import _do_move

    target = tmp_path / "new"
    actual = target if location == "target" else target.with_name("new.moving-expected")
    actual.mkdir()
    if kind == "company":
        write_company_marker(actual, company_id="other", state="ready")
    else:
        write_org_marker(actual, organization_id="other")
    session = SimpleNamespace(abs_path=lambda rel: tmp_path / rel)
    with pytest.raises(BookflowError) as error:
        _do_move(session, "old", "new", "expected", "E_RENAME_INCOMPLETE", f"{kind}_id", "expected")
    assert error.value.code == "E_RENAME_INCOMPLETE"
    assert actual.exists()


def test_pending_move_never_adopts_occupied_target_when_source_exists(tmp_path):
    from bookflow.hub.moves import _do_move

    for name in ("old", "new"):
        folder = tmp_path / name
        folder.mkdir()
        write_company_marker(folder, company_id="expected", state="ready")
    session = SimpleNamespace(abs_path=lambda rel: tmp_path / rel)
    with pytest.raises(BookflowError) as error:
        _do_move(session, "old", "new", "expected", "E_RENAME_INCOMPLETE", "company_id", "expected")
    assert error.value.code == "E_RENAME_INCOMPLETE" and error.value.details["errno"] == "EEXIST"
    assert (tmp_path / "old").exists() and (tmp_path / "new").exists()


def test_rollout_syncs_before_ready_and_registration(client, root, monkeypatch):
    from bookflow.company import rollout
    from bookflow.hub import companies

    events = []
    real_marker, real_register = rollout.write_company_marker, companies.register
    real_sync = durability.sync_directory

    def sync(path):
        events.append(("sync", path))
        real_sync(path)

    def marker(folder, **kwargs):
        if kwargs["state"] == "ready":
            assert ("sync", folder) in events and ("sync", folder.parent) in events
            assert all(("sync", folder / sub) in events for sub in ("attachments", "backups", "exports"))
            events.append(("ready", folder))
        return real_marker(folder, **kwargs)

    def register(*args, **kwargs):
        folder = root / kwargs["rel_path"]
        assert ("ready", folder) in events
        assert read_company_marker(folder)["state"] == "ready"
        events.append(("register", folder))
        return real_register(*args, **kwargs)

    monkeypatch.setattr(rollout, "sync_directory", sync)
    monkeypatch.setattr(rollout, "write_company_marker", marker)
    monkeypatch.setattr(companies, "register", register)
    result = client.company.new(legal_name="Durable Rollout", home_currency="USD", timezone="UTC")
    assert events[-1] == ("register", Path(result["path"]))


def test_rollout_parent_sync_failure_leaves_incomplete_unregistered_folder(client, root, monkeypatch):
    from bookflow.company import rollout

    org = Path(client.organization.list()["items"][0]["path"])
    real_sync = durability.sync_directory

    def sync(path):
        if path == org:
            raise OSError(errno.EIO, "injected parent sync failure")
        real_sync(path)

    monkeypatch.setattr(rollout, "sync_directory", sync)
    with pytest.raises(BookflowError) as error:
        client.company.new(legal_name="Incomplete Rollout", home_currency="USD", timezone="UTC")
    assert error.value.code == "E_ROLLOUT_INCOMPLETE"
    folder = org / "Incomplete Rollout"
    assert read_company_marker(folder)["state"] == "creating"
    with sqlite3.connect(folder / "company.db") as db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert not any(row["display_name"] == "Incomplete Rollout" for row in client.company.list()["items"])


def test_backup_file_and_directory_failure_retry(tmp_path, monkeypatch):
    from bookflow.storage import migrate

    path = tmp_path / "source.db"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE alembic_version (version_num TEXT)")
        db.execute("INSERT INTO alembic_version VALUES ('old')")
    backup_dir = tmp_path / "backups"
    events = []
    real_file, real_dir = durability.sync_file, durability.sync_directory

    def sync_file(path):
        events.append(("file", path))
        if os.name == "posix":
            assert stat.S_IMODE(path.stat().st_mode) == 0o600
        real_file(path)

    def fail_directory(path):
        events.append(("directory", path))
        assert len(list(backup_dir.glob("*-from-old.db"))) == 1
        raise OSError(errno.EIO, "injected backup directory sync failure")

    monkeypatch.setattr(migrate, "sync_file", sync_file)
    with monkeypatch.context() as patch:
        patch.setattr(migrate, "sync_directory", fail_directory)
        with pytest.raises(BookflowError) as error:
            migrate.backup(path, backup_dir, from_revision="old")
        assert error.value.code == "E_IO"
    target = next(backup_dir.glob("*-from-old.db"))
    assert [event[0] for event in events] == ["file", "directory"]
    assert set(backup_dir.iterdir()) == {target}
    events.clear()

    def sync_directory(path):
        events.append(("directory", path))
        real_dir(path)

    monkeypatch.setattr(migrate, "sync_directory", sync_directory)
    assert migrate.backup(path, backup_dir, from_revision="old") == target
    assert events == [("file", target), ("directory", backup_dir), ("directory", tmp_path)]
    assert set(backup_dir.iterdir()) == {target}


def test_bootstrap_syncs_nested_root_lineage_and_retries(tmp_path, monkeypatch):
    import bookflow
    from bookflow.commands import hub_cmds

    root = tmp_path / "a" / "b" / "root"
    client = bookflow.connect(data_root=str(root))
    calls = []
    real_sync = durability.sync_directory

    def fail(path):
        if path == tmp_path / "a":
            raise OSError(errno.EIO, "injected ancestor sync failure")
        real_sync(path)

    with monkeypatch.context() as patch:
        patch.setattr(hub_cmds, "sync_directory", fail)
        with pytest.raises(BookflowError) as error:
            client.init()
        assert error.value.code == "E_IO"
    assert not (root / "hub.db").exists()

    def sync(path):
        calls.append(path)
        real_sync(path)

    monkeypatch.setattr(hub_cmds, "sync_directory", sync)
    assert client.init()["created"]
    assert all(path in calls for path in (root, *root.parents))
    assert all(root / sub in calls for sub in ("organizations", "backups", "trash"))


def test_demo_trash_sync_failure_preserves_registry_and_retries_exact_target(client, root, monkeypatch):
    from bookflow.commands import hub_cmds
    from bookflow.core import moves

    org_id = client.organization.list()["items"][0]["organization_id"]
    row = _hub_row(root, h.organizations, org_id)
    src = root / row["path"]
    attempts = []

    def fail(old, new):
        pending = _hub_row(root, h.organizations, org_id)["pending_path"]
        assert old == src and new == root / pending
        assert not src.exists() and new.exists()
        attempts.append(new)
        raise OSError(errno.EIO, "injected trash sync failure")

    with monkeypatch.context() as patch:
        patch.setattr(moves, "sync_move_parents", fail)
        patch.setattr(hub_cmds, "sync_move_parents", fail)
        for _ in range(2):
            with pytest.raises(BookflowError) as error:
                client.demo.reset()
            assert error.value.code == "E_DEMO_RESET_INCOMPLETE"
    assert len(attempts) == 2 and attempts[0] == attempts[1]
    synced = []
    real_sync = durability.sync_move_parents

    def sync(old, new):
        assert _hub_row(root, h.organizations, org_id) is not None
        assert old == src and new == attempts[0]
        synced.append(new)
        real_sync(old, new)

    monkeypatch.setattr(hub_cmds, "sync_move_parents", sync)
    result = client.demo.reset()
    assert synced == attempts[:1]
    assert Path(result["trashed_path"]) == attempts[0]
    assert _hub_row(root, h.organizations, org_id) is None
    assert attempts[0].exists()


def test_demo_selects_collision_free_trash_path_before_persisting_it(client, root, monkeypatch):
    from bookflow.commands import hub_cmds

    monkeypatch.setattr(hub_cmds, "datetime", SimpleNamespace(now=lambda tz: datetime(2026, 1, 1, tzinfo=timezone.utc)))
    first = client.demo.reset()
    second = client.demo.reset()
    assert first["trashed_path"] != second["trashed_path"]
    assert Path(first["trashed_path"]).exists() and Path(second["trashed_path"]).exists()


def test_ready_marker_sync_failure_reports_actual_state_and_attach_resyncs(client, root, monkeypatch):
    from bookflow.commands import hub_cmds

    org = Path(client.organization.list()["items"][0]["path"])
    folder = org / "Ready Retry"
    real_sync = durability.sync_directory

    def fail_ready(path):
        if path == folder and read_company_marker(folder)["state"] == "ready":
            raise OSError(errno.EIO, "injected ready-marker directory sync failure")
        real_sync(path)

    with monkeypatch.context() as patch:
        patch.setattr(durability, "sync_directory", fail_ready)
        with pytest.raises(BookflowError) as error:
            client.company.new(legal_name="Ready Retry", home_currency="USD", timezone="UTC")
        assert error.value.code == "E_ROLLOUT_INCOMPLETE"
        assert error.value.details["state"] == "unregistered"
    company_id = read_company_marker(folder)["company_id"]
    assert _hub_row(root, h.companies, company_id) is None
    with monkeypatch.context() as patch:
        patch.setattr(hub_cmds, "sync_directory", fail_ready)
        with pytest.raises(BookflowError) as error:
            client.company.attach(path=str(folder))
        assert error.value.code == "E_IO"
    assert _hub_row(root, h.companies, company_id) is None
    observed = []

    def sync(path):
        assert _hub_row(root, h.companies, company_id) is None
        observed.append(path)
        real_sync(path)

    monkeypatch.setattr(hub_cmds, "sync_directory", sync)
    assert client.company.attach(path=str(folder))["company_id"] == company_id
    assert observed == [folder, org]


def test_case_only_hop_sync_failure_remains_recoverable(tmp_path, monkeypatch):
    from bookflow.core import moves
    from bookflow.hub.moves import _do_move

    src, dst = tmp_path / "Mixed", tmp_path / "MIXED"
    src.mkdir()
    write_company_marker(src, company_id="expected", state="ready")
    hop = tmp_path / "MIXED.moving-expected"
    real_resolve = Path.resolve

    def resolve(path, *args, **kwargs):
        # Simulate the case-insensitive identity test while exercising actual
        # Linux renames and directory entries for both hops.
        return src if path in (src, dst) else real_resolve(path, *args, **kwargs)

    def fail(old, new):
        assert old == src and new == hop and hop.exists()
        raise OSError(errno.EIO, "injected first-hop sync failure")

    with monkeypatch.context() as patch:
        patch.setattr(Path, "resolve", resolve)
        patch.setattr(moves, "sync_move_parents", fail)
        with pytest.raises(BookflowError) as error:
            moves.move_dir(src, dst, company_id="expected")
        assert error.value.code == "E_IO"
    assert hop.exists() and not src.exists() and not dst.exists()
    session = SimpleNamespace(abs_path=lambda rel: tmp_path / rel)
    _do_move(session, "Mixed", "MIXED", "expected", "E_RENAME_INCOMPLETE", "company_id", "expected")
    assert dst.exists() and not hop.exists()


def test_backup_file_sync_failure_does_not_publish_or_remove_other_files(tmp_path, monkeypatch):
    from bookflow.storage import migrate

    source, backups = tmp_path / "source.db", tmp_path / "backups"
    with sqlite3.connect(source) as db:
        db.execute("CREATE TABLE example (id INTEGER)")
    backups.mkdir()
    unrelated = backups / "another.partial"
    unrelated.write_text("owned by someone else")

    def fail(path):
        raise OSError(errno.EIO, "injected backup file sync failure")

    monkeypatch.setattr(migrate, "sync_file", fail)
    with pytest.raises(BookflowError) as error:
        migrate.backup(source, backups)
    assert error.value.code == "E_IO" and error.value.details["operation"] == "backup"
    assert set(backups.iterdir()) == {unrelated}
    assert unrelated.read_text() == "owned by someone else"


def _pending_config(root):
    with open_database(root / "hub.db", writable=False) as db:
        return db.conn.execute(sa.select(h.pending_config)).mappings().first()


def test_config_intent_and_audit_commit_together_and_reads_do_not_repair(client, root):
    from bookflow.core.audit import write_event_to
    from bookflow.core.config import os_login
    from bookflow.core.context import Context, Interface

    company_id = client.company.list()["items"][0]["company_id"]
    path = root / "config.toml"
    before = path.read_bytes()
    config = Config.load(path)
    config.set_default_company(os_login(), company_id)
    ctx = Context.new(Interface.python, "config-durability-test")
    with open_database(root / "hub.db", writable=True) as db:
        for commit in (False, True):
            db.raw.execute("BEGIN IMMEDIATE")
            write_event_to(db, ctx, "company use", "set a default company", [], actor_id=None, actor_kind=None)
            config.stage_pending(db, request_id=ctx.request_id)
            db.raw.execute("COMMIT" if commit else "ROLLBACK")
            assert (_pending_config(root) is not None) is commit
            assert db.raw.execute("SELECT count(*) FROM audit_events WHERE request_id = ?", (ctx.request_id,)).fetchone()[0] == int(commit)
            assert path.read_bytes() == before
        # A read sees the committed settings while leaving both the file and
        # outstanding intent intact, even before the caller can flush the copy.
        assert Config.load(path).user_table(os_login())["default_company"] == company_id
        assert path.read_bytes() == before and _pending_config(root) is not None
        assert config.flush_pending(db)
    assert _pending_config(root) is None
    assert Config.load(path).user_table(os_login())["default_company"] == company_id
    assert path.read_bytes() != before


def test_rolled_back_new_config_intent_preserves_previous_committed_intent(client, root):
    from bookflow.core.config import os_login

    config = Config.load(root / "config.toml")
    company_id = client.company.list()["items"][0]["company_id"]
    config.set_default_company(os_login(), company_id)
    with open_database(root / "hub.db", writable=True) as db:
        db.raw.execute("BEGIN IMMEDIATE")
        config.stage_pending(db, request_id="first-request")
        db.raw.execute("COMMIT")
        first = dict(_pending_config(root))
        config.set_default_company(os_login(), None)
        db.raw.execute("BEGIN IMMEDIATE")
        config.stage_pending(db, request_id="second-request")
        db.raw.execute("ROLLBACK")
    assert dict(_pending_config(root)) == first
    assert Config.load(root / "config.toml").user_table(os_login())["default_company"] == company_id


@pytest.mark.parametrize("failure", ["replace", "directory", "clear_intent"])
def test_config_projection_failure_remains_committed_and_retryable(client, root, monkeypatch, failure):
    from bookflow.core.config import os_login

    if failure == "directory" and os.name != "posix":
        pytest.skip("POSIX directory-fsync failure witness")
    company_id = client.company.list()["items"][0]["company_id"]
    path = root / "config.toml"
    config = Config.load(path)
    config.set_default_company(os_login(), company_id)
    before = path.read_bytes()
    with open_database(root / "hub.db", writable=True) as db:
        db.raw.execute("BEGIN IMMEDIATE")
        config.stage_pending(db, request_id="pending-request")
        db.raw.execute("COMMIT")
        real_execute = db.conn.execute

        def fail(*args, **kwargs):
            raise OSError(errno.EIO, "injected config projection failure")

        def execute(statement, *args, **kwargs):
            if getattr(statement, "is_delete", False) and statement.table.name == "pending_config":
                raise sqlite3.OperationalError("injected intent deletion failure")
            return real_execute(statement, *args, **kwargs)

        with monkeypatch.context() as patch:
            if failure == "replace":
                patch.setattr(os, "replace", fail)
            elif failure == "directory":
                patch.setattr(durability, "sync_directory", fail)
            else:
                patch.setattr(db.conn, "execute", execute)
            with pytest.raises(BookflowError) as error:
                config.flush_pending(db)
            assert error.value.code == "E_PARTIAL_WRITE"
            assert error.value.details == {"durable": ["config"], "projection_pending": True, "request_id": "pending-request", "cause": "E_IO"}
        assert not db.raw.in_transaction
        assert _pending_config(root) is not None
        assert (path.read_bytes() == before) is (failure == "replace")
        assert Config.load(path).user_table(os_login())["default_company"] == company_id
        assert config.flush_pending(db)
        assert not config.flush_pending(db)
    assert _pending_config(root) is None


def test_company_use_failed_projection_is_visible_to_reads_and_repaired_on_next_write(client, root, monkeypatch):
    company_id = client.company.list()["items"][0]["company_id"]
    path = root / "config.toml"
    before = path.read_bytes()
    events_before = client.hub.audit.list(command="company use")["count"]

    def fail(_config):
        raise OSError(errno.EIO, "injected config save failure")

    with monkeypatch.context() as patch:
        patch.setattr(Config, "save", fail)
        with pytest.raises(BookflowError) as error:
            client.company.use(company=company_id)
        assert error.value.code == "E_PARTIAL_WRITE" and error.value.details["projection_pending"]
        assert client.company.show()["company_id"] == company_id
        assert client.hub.audit.list(command="company use")["count"] == events_before + 1
        assert path.read_bytes() == before and _pending_config(root) is not None
    client.organization.new(name="Repairs Committed Settings")
    assert _pending_config(root) is None and path.read_bytes() != before
    assert client.hub.audit.list(command="company use")["count"] == events_before + 1


def test_bootstrap_config_failure_recovers_identity_without_duplicate_users(tmp_path, monkeypatch):
    import bookflow
    from bookflow.core.config import os_login

    root = tmp_path / "root"
    client = bookflow.connect(data_root=str(root))

    def fail(_config):
        raise OSError(errno.EIO, "injected initial config save failure")

    with monkeypatch.context() as patch:
        patch.setattr(Config, "save", fail)
        with pytest.raises(BookflowError) as error:
            client.init()
        assert error.value.code == "E_PARTIAL_WRITE"
    assert not (root / "config.toml").exists()
    user_id = Config.load(root / "config.toml").user_table(os_login())["user_id"]
    assert _pending_config(root) is not None
    assert client.init()["user_id"] == user_id
    assert _pending_config(root) is None and (root / "config.toml").exists()
    with open_database(root / "hub.db", writable=False) as db:
        assert db.raw.execute("SELECT count(*) FROM users WHERE kind = 'human'").fetchone()[0] == 1
        assert db.raw.execute("SELECT count(*) FROM audit_events WHERE command = 'init'").fetchone()[0] == 1


def test_config_pending_read_refuses_network_database_before_connect(tmp_path, monkeypatch):
    from bookflow.core import fs

    (tmp_path / "hub.db").touch()
    calls = []

    def refuse(path):
        calls.append(path)
        raise BookflowError("E_NETWORK_SHARE")

    monkeypatch.setattr(fs, "check_local", refuse)
    monkeypatch.setattr(sqlite3, "connect", lambda *args, **kwargs: pytest.fail("must refuse before opening SQLite"))
    with pytest.raises(BookflowError) as error:
        Config.load(tmp_path / "config.toml")
    assert error.value.code == "E_NETWORK_SHARE" and calls == [tmp_path / "hub.db"]


def test_pending_config_migration_is_additive_and_revision_local(tmp_path):
    from alembic import command
    from bookflow.storage.migrate import _config, migrate_to_head

    path = tmp_path / "hub.db"
    with open_database(path, writable=True, create=True) as db:
        command.upgrade(_config("hub", db.conn), "hub0005")
        original = dict(db.raw.execute("SELECT name, sql FROM sqlite_master WHERE type = 'table'").fetchall())
        assert "pending_config" not in original
        assert migrate_to_head(db, "hub", tmp_path / "backups") == ("hub0005", "hub0007")
        current = dict(db.raw.execute("SELECT name, sql FROM sqlite_master WHERE type = 'table'").fetchall())
        assert {key: value for key, value in current.items() if key != "pending_config"} == original
        assert db.raw.execute("SELECT count(*) FROM pending_config").fetchone()[0] == 0


def test_failed_demo_config_projection_does_not_skip_nested_seed_history(client, root, monkeypatch):
    before_customers = sorted(row["name"] for row in client.customer.list(company="Demo Plumbing Co", include_inactive=True)["items"])
    before_items = sorted(row["name"] for row in client.item.list(company="Demo Plumbing Co", include_inactive=True)["items"])
    old_company = client.company.show(company="Demo Plumbing Co")["company_id"]
    client.company.use(company=old_company)

    def fail(_config):
        raise OSError(errno.EIO, "injected demo settings projection failure")

    with monkeypatch.context() as patch:
        patch.setattr(Config, "save", fail)
        with pytest.raises(BookflowError) as error:
            client.demo.reset()
        assert error.value.code == "E_PARTIAL_WRITE"
    assert _pending_config(root) is not None
    client.organization.new(name="Repair After Demo Reset")
    assert _pending_config(root) is None
    assert client.company.show(company="Demo Plumbing Co")["company_id"] != old_company
    assert sorted(row["name"] for row in client.customer.list(company="Demo Plumbing Co", include_inactive=True)["items"]) == before_customers
    assert sorted(row["name"] for row in client.item.list(company="Demo Plumbing Co", include_inactive=True)["items"]) == before_items
