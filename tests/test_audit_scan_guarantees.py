"""Bounded visible-candidate audit scans with independently filtered output."""

from pathlib import Path

import pytest
import sqlalchemy as sa

from bookflow.core.audit import write_event_to
from bookflow.core.context import Context, Interface
from bookflow.core.errors import BookflowError
from bookflow.storage.engine import open_database
from tests.conftest import as_user, make_actor


def _append(path, commands, actors=None):
    sequences = []
    with open_database(path, writable=True) as db:
        db.raw.execute("BEGIN IMMEDIATE")
        for index, name in enumerate(commands):
            ctx = Context.new(Interface.python, "audit-scan-test")
            event_id = write_event_to(db, ctx, name, name, [], actor_id=actors[index] if actors else None, actor_kind="human")
            sequences.append(db.raw.execute("SELECT seq FROM audit_events WHERE id = ?", (event_id,)).fetchone()[0])
        db.raw.execute("COMMIT")
    return sequences


def test_filtered_scan_drains_multiple_pages_without_missing_boundary_matches(client):
    folder = Path(client.company.show(company="Demo Plumbing Co")["path"])
    start = client.audit.tail(company="Demo Plumbing Co")["high_water"]
    matching = {0, 99, 100, 199, 204}
    sequences = _append(folder / "company.db", ["company update" if i in matching else "customer create" for i in range(205)])
    cursor, found, counts, more = start, [], [], []
    for _ in range(3):
        page = client.audit.tail(company="Demo Plumbing Co", after=cursor, command="company update", limit=100, scan_limit=100)
        found.extend(event["seq"] for event in page["items"])
        counts.append(page["scanned_count"])
        more.append(page["scan_more"])
        assert page["next_after"] > cursor
        cursor = page["next_after"]
    assert counts == [100, 100, 5] and more == [True, True, False]
    assert found == [sequences[index] for index in sorted(matching)]
    assert cursor == sequences[-1]
    empty = client.audit.tail(company="Demo Plumbing Co", after=cursor, command="company update", scan_limit=100)
    assert empty["items"] == [] and empty["scanned_count"] == 0 and not empty["scan_more"]
    assert empty["next_after"] is None


def test_empty_filtered_page_advances_while_default_matching_pagination_is_unchanged(client):
    folder = Path(client.company.show(company="Demo Plumbing Co")["path"])
    start = client.audit.tail(company="Demo Plumbing Co")["high_water"]
    sequences = _append(folder / "company.db", ["customer create"] * 9 + ["company update"] * 2 + ["customer create"] * 4)
    normal = client.audit.tail(company="Demo Plumbing Co", after=start, command="company update", limit=2)
    assert [event["seq"] for event in normal["items"]] == sequences[9:11]
    assert normal["next_after"] == sequences[10]
    assert normal["scanned_count"] == 0 and not normal["scan_more"]
    cursor = start
    found = []
    for index in range(5):
        page = client.audit.tail(company="Demo Plumbing Co", after=cursor, command="company update", limit=3, scan_limit=100)
        if index < 3:
            assert page["items"] == []
        assert page["scanned_count"] == 3
        assert page["scan_more"] is (index < 4)
        assert page["next_after"] == sequences[index * 3 + 2]
        found.extend(event["seq"] for event in page["items"])
        cursor = page["next_after"]
    assert found == sequences[9:11]
    added = _append(folder / "company.db", ["company update"])
    page = client.audit.tail(company="Demo Plumbing Co", after=cursor, command="company update", scan_limit=100)
    assert [event["seq"] for event in page["items"]] == added


@pytest.mark.parametrize("limit,scan_limit", [(1, 100), (100, 1), (2, 3), (3, 2)])
def test_candidate_query_is_bounded_by_smaller_limit_before_filters(client, limit, scan_limit):
    folder = Path(client.company.show(company="Demo Plumbing Co")["path"])
    start = client.audit.tail(company="Demo Plumbing Co")["high_water"]
    _append(folder / "company.db", ["customer create"] * 8)
    queries = []

    def record(_connection, _cursor, statement, parameters, _context, _many):
        if statement.startswith("SELECT audit_events.seq "):
            queries.append((statement, parameters))

    sa.event.listen(sa.engine.Engine, "before_cursor_execute", record)
    try:
        page = client.audit.tail(company="Demo Plumbing Co", after=start, command="company update", limit=limit, scan_limit=scan_limit)
    finally:
        sa.event.remove(sa.engine.Engine, "before_cursor_execute", record)
    cap = min(limit, scan_limit)
    assert page["items"] == [] and page["scanned_count"] == cap and page["scan_more"]
    assert len(queries) == 1
    statement, parameters = queries[0]
    assert "LIMIT" in statement and parameters[-2:] == (cap + 1, 0)
    assert "audit_events.command" not in statement


def test_hub_scan_candidates_and_default_high_water_are_visibility_scoped(client, root):
    first = make_actor(root, "scan-first")
    second = make_actor(root, "scan-second")
    first_client = as_user(root, "scan-first")
    start = client.hub.audit.tail()["high_water"]
    sequences = _append(root / "hub.db", ["company use"] * 6, [first, second] * 3)
    page = first_client.hub.audit.tail(after=start, scan_limit=2)
    assert [event["seq"] for event in page["items"]] == sequences[0:3:2]
    assert page["scanned_count"] == 2 and page["scan_more"]
    assert page["next_after"] == sequences[2]
    last = first_client.hub.audit.tail(after=page["next_after"], scan_limit=2)
    assert [event["seq"] for event in last["items"]] == [sequences[4]]
    assert last["next_after"] == sequences[4] and not last["scan_more"]
    default = first_client.hub.audit.tail(scan_limit=2)
    assert default["high_water"] == sequences[4]
    assert default["items"] == [] and default["scanned_count"] == 0


@pytest.mark.parametrize("scan_limit", [0, 1001])
def test_scan_limit_validation(client, scan_limit):
    with pytest.raises(BookflowError) as error:
        client.audit.tail(company="Demo Plumbing Co", scan_limit=scan_limit)
    assert error.value.code == "E_VALIDATION"
