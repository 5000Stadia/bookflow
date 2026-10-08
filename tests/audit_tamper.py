"""Test-only way to damage audit history on purpose (co0065 / hub0015 make it append-only).

A test that proves the code notices a missing or altered audit row must first take the
storage guard away, exactly as test_deposit_history_replay_followup does for the ledger
guards. `tampering(connection)` drops the audit triggers for the block and puts back the
very same DDL afterwards, so the rest of the test runs against the real schema.
"""
from contextlib import contextmanager


def _run(connection, sql, params=()):
    if hasattr(connection, 'exec_driver_sql'):  # a SQLAlchemy connection
        return connection.exec_driver_sql(sql, params)
    return connection.execute(sql, params)


@contextmanager
def tampering(connection):
    triggers = list(_run(
        connection, "SELECT name, sql FROM sqlite_schema WHERE type='trigger' AND tbl_name IN ('audit_events','audit_entries')"))
    assert triggers, 'the audit append-only triggers are missing'
    for name, _ in triggers:
        _run(connection, 'DROP TRIGGER "' + name + '"')
    try:
        yield
    finally:
        for _, ddl in triggers:
            _run(connection, ddl)


def disarm(connection):
    """Drop the audit triggers for the rest of a disposable fixture database."""
    for (name,) in list(_run(connection, "SELECT name FROM sqlite_schema WHERE type='trigger' "
                                         "AND tbl_name IN ('audit_events','audit_entries')")):
        _run(connection, 'DROP TRIGGER "' + name + '"')
