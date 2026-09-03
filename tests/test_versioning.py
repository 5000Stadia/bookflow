from datetime import datetime, timedelta, timezone

import pytest

from bookflow.core import clock
from bookflow.core.errors import BookflowError
from bookflow.core.versioning import HistoryEntry, check_update, fold_field

T0 = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch):
    monkeypatch.setattr(clock, "now", lambda: T0)


def writer(uid="U2", at=None, principal=None):
    return HistoryEntry(version_after=3, changed_columns=["phone"], updated_by=uid, updated_by_name="Two", on_behalf_of=principal, updated_via="cli", at=at)


def history(entries):
    return lambda v: [e for e in entries if e.version_after > v]


def test_fold():
    assert fold_field("address_line1") == "address" and fold_field("legal_address_city") == "legal_address" and fold_field("phone") == "phone"


def test_versioned_ok_and_noop():
    m = check_update(current_version=3, current_updated_at=None, current_writer=None, changes={"phone"}, expected_version=3, history_since=history([]), actor_id="U1", window_seconds=60)
    assert m.version == 4 and m.changed_fields == ["phone"] and m.merged_over_versions == []
    m = check_update(current_version=3, current_updated_at=None, current_writer=None, changes=set(), expected_version=None, history_since=history([]), actor_id="U1", window_seconds=60)
    assert m.version == 3 and m.changed_fields == []


def test_merge_and_conflict():
    h = history([HistoryEntry(2, ["phone"]), HistoryEntry(3, ["address_line1"])])
    m = check_update(current_version=3, current_updated_at=None, current_writer=writer(), changes={"email"}, expected_version=1, history_since=h, actor_id="U1", window_seconds=60)
    assert m.merged_over_versions == [2, 3]
    with pytest.raises(BookflowError) as e:
        check_update(current_version=3, current_updated_at=None, current_writer=writer(), changes={"address"}, expected_version=1, history_since=h, actor_id="U1", window_seconds=60)
    assert e.value.code == "E_VERSION_CONFLICT" and e.value.details["changed_fields"] == ["address", "phone"] and e.value.details["updated_by_name"] == "Two"


def test_missing_history_is_conflict():
    h = history([HistoryEntry(3, ["phone"])])  # version 2 has no entry
    with pytest.raises(BookflowError) as e:
        check_update(current_version=3, current_updated_at=None, current_writer=writer(), changes={"email"}, expected_version=1, history_since=h, actor_id="U1", window_seconds=60)
    assert e.value.details["unknown_versions"] == [2]
    h2 = history([HistoryEntry(2, None), HistoryEntry(3, ["phone"])])
    with pytest.raises(BookflowError):
        check_update(current_version=3, current_updated_at=None, current_writer=writer(), changes={"email"}, expected_version=1, history_since=h2, actor_id="U1", window_seconds=60)


def test_higher_expected_is_conflict():
    with pytest.raises(BookflowError) as e:
        check_update(current_version=3, current_updated_at=None, current_writer=writer(), changes={"email"}, expected_version=9, history_since=history([]), actor_id="U1", window_seconds=60)
    assert e.value.code == "E_VERSION_CONFLICT"


def test_blind_write_reporting():
    recent = (T0 - timedelta(seconds=10)).isoformat()
    m = check_update(current_version=3, current_updated_at=recent, current_writer=writer(principal="P1"), changes={"email"}, expected_version=None, history_since=history([]), actor_id="U1", window_seconds=60)
    assert m.previous_version == 3 and m.previous_updated_by == "U2" and m.previous_on_behalf_of == "P1"
    assert m.seconds_since_previous_update == 10.0 and m.recent_concurrent_activity is True
    old = (T0 - timedelta(seconds=61)).isoformat()
    m = check_update(current_version=3, current_updated_at=old, current_writer=writer(), changes={"email"}, expected_version=None, history_since=history([]), actor_id="U1", window_seconds=60)
    assert m.recent_concurrent_activity is False
    m = check_update(current_version=3, current_updated_at=recent, current_writer=writer(uid="U1"), changes={"email"}, expected_version=None, history_since=history([]), actor_id="U1", window_seconds=60)
    assert m.recent_concurrent_activity is False, "own previous write is not concurrent activity"
