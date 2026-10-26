"""R180: every Database shares one engine, so a statement compiled once is reused by the next."""
import threading

import pytest
import sqlalchemy as sa

from bookflow.storage import engine


def _stats(db, statement):
    seen = []
    def hook(conn, cursor, text, params, context, many):
        seen.append(context._get_cache_stats().split(" ")[0])
    sa.event.listen(db.engine, "before_cursor_execute", hook)
    try:
        db.conn.execute(statement).fetchall()
    finally:
        sa.event.remove(db.engine, "before_cursor_execute", hook)
    return seen


def test_a_second_database_reuses_the_first_ones_compiled_statement(tmp_path):
    t = sa.table("t", sa.column("x"))
    query = sa.select(t.c.x).where(t.c.x > sa.bindparam("v", 1))
    first = engine.Database(tmp_path / "a.db", True, create=True)
    first.raw.execute("CREATE TABLE t (x INTEGER)")
    _stats(first, query)
    first.close()
    second = engine.Database(tmp_path / "b.db", True, create=True)
    try:
        second.raw.execute("CREATE TABLE t (x INTEGER)")
        assert second.engine is engine._engine()
        assert _stats(second, query) == ["cached"]
    finally:
        second.close()


def test_each_database_keeps_its_own_connection_and_close_closes_it(tmp_path):
    a = engine.Database(tmp_path / "a.db", True, create=True)
    b = engine.Database(tmp_path / "b.db", True, create=True)
    try:
        a.raw.execute("CREATE TABLE only_a (x)")
        assert a.conn.exec_driver_sql("SELECT count(*) FROM sqlite_master WHERE name='only_a'").scalar() == 1
        assert b.conn.exec_driver_sql("SELECT count(*) FROM sqlite_master WHERE name='only_a'").scalar() == 0
    finally:
        a.close()
    with pytest.raises(Exception):
        a.raw.execute("SELECT 1")
    b.close()


def test_the_engine_connects_only_while_a_database_opens():
    with pytest.raises(RuntimeError):
        engine._engine().connect()


def test_databases_open_in_parallel_threads_get_their_own_connections(tmp_path):
    results, errors = {}, []

    def open_one(i):
        try:
            db = engine.Database(tmp_path / f"{i}.db", True, create=True)
            try:
                db.raw.execute(f"CREATE TABLE t{i} (x)")
                results[i] = db.conn.exec_driver_sql("SELECT name FROM sqlite_master").scalars().all()
            finally:
                db.close()
        except Exception as e:  # pragma: no cover - reported below
            errors.append(e)

    threads = [threading.Thread(target=open_one, args=(i,)) for i in range(8)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert not errors
    assert all(results[i] == [f"t{i}"] for i in range(8))
