"""SQLite tracing observes native API boundaries without changing database work."""

import json
import sqlite3

import pytest
import sqlalchemy as sa

from bookflow.core import performance
from bookflow.storage.engine import Database
from bookflow.storage.traced_sqlite import Connection, Cursor, _phase


@pytest.fixture
def recording(tmp_path, monkeypatch):
    monkeypatch.setattr(performance, "_roots", set())
    monkeypatch.setattr(performance, "_roots_failed", False)
    directory = tmp_path / "trace"
    directory.mkdir(mode=0o700)
    recorder = performance.start(directory)
    assert recorder is not None
    try:
        yield recorder
    finally:
        performance.close()


def names(recorder):
    return [event["name"] for event in recorder.snapshot()["traceEvents"]]


def test_raw_transaction_fetch_and_no_duplicate_execution(recording):
    conn = sqlite3.connect(":memory:", factory=Connection, isolation_level=None)
    try:
        conn.execute("CREATE TABLE t (value TEXT)")
        conn.execute("BEGIN IMMEDIATE")
        conn.executemany("INSERT INTO t VALUES (?)", [("a",), ("b",), ("c",)])
        conn.commit()
        cursor = conn.execute("SELECT value FROM t ORDER BY value")
        assert isinstance(cursor, Cursor)
        cursor.arraysize = 2
        assert cursor.fetchmany() == [("a",), ("b",)]
        assert cursor.fetchone() == ("c",)
        assert cursor.fetchall() == []
        assert list(conn.execute("SELECT value FROM t")) == [("a",), ("b",), ("c",)]
        conn.execute("BEGIN")
        conn.execute("ROLLBACK")
        conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
        phases = names(recording)
        assert phases.count("sql.execute") == 4
        assert phases.count("sql.begin") == 2
        assert phases.count("sql.commit") == 1
        assert phases.count("sql.rollback") == 1
        assert phases.count("sql.checkpoint") == 1
        assert phases.count("sql.fetch") == 7
        assert all(event["args"]["success"] for event in recording.snapshot()["traceEvents"])
    finally:
        conn.close()


@pytest.mark.parametrize("factory", [sqlite3.Connection, Connection])
def test_native_factories_defaults_scripts_and_errors(recording, factory):
    class CustomCursor(sqlite3.Cursor):
        pass

    conn = sqlite3.connect(":memory:", factory=factory)
    try:
        conn.row_factory = sqlite3.Row
        assert type(conn.cursor(factory=CustomCursor)) is CustomCursor
        assert type(conn.cursor(CustomCursor)) is CustomCursor
        with pytest.raises(TypeError):
            conn.cursor(factory=None)
        conn.execute("CREATE TABLE t (value INTEGER UNIQUE)")
        conn.execute("INSERT INTO t VALUES (1)")
        assert conn.in_transaction
        script = conn.executescript("INSERT INTO t VALUES (2); BEGIN; INSERT INTO t VALUES (3);")
        assert isinstance(script, sqlite3.Cursor)
        assert conn.in_transaction
        conn.rollback()
        rows = conn.execute("SELECT value FROM t ORDER BY value").fetchmany()
        assert len(rows) == 1 and rows[0]["value"] == 1
        assert [row[0] for row in conn.execute("SELECT value FROM t ORDER BY value")] == [1, 2]
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE constraint failed"):
            conn.execute("INSERT INTO t VALUES (1)")
        with pytest.raises(sqlite3.ProgrammingError):
            conn.execute("SELECT ?", ())
        with conn:
            conn.execute("INSERT INTO t VALUES (4)")
        assert not conn.in_transaction
        with pytest.raises(ValueError):
            with conn:
                conn.execute("INSERT INTO t VALUES (5)")
                raise ValueError("rollback")
        assert [row[0] for row in conn.execute("SELECT value FROM t ORDER BY value")] == [1, 2, 4]
    finally:
        conn.close()
    with pytest.raises(sqlite3.ProgrammingError):
        conn.execute("SELECT 1")


def test_engine_sqlalchemy_category_and_secret_privacy(recording, tmp_path):
    secret = "SECRET_SQL_PARAMETER_FILENAME_93471"
    folder = tmp_path / secret
    folder.mkdir()
    db = Database(folder / "company.db", writable=True, create=True)
    try:
        assert isinstance(db.raw, Connection)
        db.raw.execute("CREATE TABLE secret_values (value TEXT)")
        db.raw.execute("BEGIN")
        db.conn.execute(sa.text("INSERT INTO secret_values VALUES (:value)"), {"value": secret})
        db.raw.commit()
        assert db.conn.execute(sa.text("SELECT value FROM secret_values")).scalars().all() == [secret]
        with pytest.raises(sqlite3.OperationalError):
            db.raw.execute(f"SELECT {secret} FROM missing_table")
    finally:
        db.close()
    document = recording.snapshot()
    encoded = json.dumps(document)
    assert secret not in encoded
    assert "secret_values" not in encoded
    assert "missing_table" not in encoded
    assert "db.open" in names(recording) and "db.close" in names(recording)
    assert "sql.fetch" in names(recording) and "sql.commit" in names(recording)
    sql_events = [event for event in document["traceEvents"] if event["name"].startswith("sql.")]
    assert sql_events and all(event["args"]["database"] == "company" for event in sql_events)


def test_preexisting_native_handles_and_finished_capture(recording, tmp_path):
    performance.close()
    native = Database(tmp_path / "hub.db", writable=True, create=True)
    try:
        assert type(native.raw) is sqlite3.Connection
    finally:
        native.close()
    traced = sqlite3.connect(":memory:", factory=Connection)
    before = recording.snapshot()
    try:
        traced.execute("CREATE TABLE t (value)")
        traced.execute("INSERT INTO t VALUES (1)")
        traced.commit()
        assert traced.execute("SELECT value FROM t").fetchall() == [(1,)]
        assert recording.snapshot() == before
    finally:
        traced.close()


def test_recording_failure_preserves_durable_success_and_original_error(recording, tmp_path, monkeypatch):
    path = tmp_path / "hub.db"
    conn = sqlite3.connect(path, factory=Connection)

    def fail_finish(*args, **kwargs):
        raise RuntimeError("SECRET_RECORDING_FAILURE")

    monkeypatch.setattr(recording, "_finish", fail_finish)
    try:
        conn.execute("CREATE TABLE t (value INTEGER UNIQUE)")
        conn.execute("INSERT INTO t VALUES (1)")
        conn.commit()
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE constraint failed"):
            conn.execute("INSERT INTO t VALUES (1)")
        conn.rollback()
    finally:
        conn.close()
    with sqlite3.connect(path) as check:
        assert check.execute("SELECT value FROM t").fetchall() == [(1,)]


@pytest.mark.parametrize(("sql", "phase"), [
    (" -- comment\n /* comment */ BEGIN IMMEDIATE", "sql.begin"),
    ("END TRANSACTION", "sql.commit"),
    ("ROLLBACK TO SAVEPOINT a", "sql.rollback"),
    ("PRAGMA main.wal_checkpoint(TRUNCATE)", "sql.checkpoint"),
    ("SELECT 'COMMIT'", "sql.execute"),
    (" " * 256 + "COMMIT", "sql.execute"),
    (None, "sql.execute"),
])
def test_bounded_statement_labels(sql, phase):
    assert _phase(sql) == phase
