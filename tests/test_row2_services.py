from datetime import datetime, timedelta, timezone

import pytest

from bookflow.company import directives, presence
from bookflow.core import clock, idempotency
from bookflow.core.errors import BookflowError
from bookflow.storage.engine import open_database
from bookflow.storage.migrate import migrate_to_head

T0 = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def cdb(tmp_path):
    p = tmp_path / "company.db"
    with open_database(p, writable=True, create=True) as db:
        migrate_to_head(db, "company", None)
        yield db


def test_idempotency_roundtrip(cdb, monkeypatch):
    monkeypatch.setattr(clock, "now", lambda: T0)
    h = idempotency.input_hash({"name": "x"}, "C1")
    assert idempotency.lookup(cdb, "U1", "k1", "directive add", h) is None
    idempotency.store(cdb, "U1", "k1", "directive add", h, "R1", {"ok": True})
    row = idempotency.lookup(cdb, "U1", "k1", "directive add", h)
    assert row["state"] == "done" and '"ok": true' in row["output"]
    with pytest.raises(BookflowError) as e:
        idempotency.lookup(cdb, "U1", "k1", "directive add", idempotency.input_hash({"name": "y"}, "C1"))
    assert e.value.code == "E_IDEMPOTENCY_MISMATCH"
    with pytest.raises(BookflowError):
        idempotency.lookup(cdb, "U1", "k1", "company update", h)
    monkeypatch.setattr(clock, "now", lambda: T0 + timedelta(days=31))
    assert idempotency.lookup(cdb, "U1", "k1", "directive add", h) is None, "expired at lookup"
    idempotency.store(cdb, "U1", "k1", "directive add", h, "R2", {"ok": 2})
    assert '"ok": 2' in idempotency.lookup(cdb, "U1", "k1", "directive add", h)["output"]


def test_presence_lifecycle(cdb, monkeypatch):
    monkeypatch.setattr(clock, "now", lambda: T0)
    presence.set_presence(cdb, record_type="company_info", record_id="X", user_id="U1", interface="gui")
    presence.set_presence(cdb, record_type="company_info", record_id="X", user_id="U1", interface="cli")
    live = presence.live_for(cdb, "company_info", "X")
    assert len(live) == 2 and {r["interface"] for r in live} == {"gui", "cli"}
    started = live[0]["started_at"]
    monkeypatch.setattr(clock, "now", lambda: T0 + timedelta(seconds=60))
    presence.set_presence(cdb, record_type="company_info", record_id="X", user_id="U1", interface="gui")
    assert [r for r in presence.live_for(cdb, "company_info", "X") if r["interface"] == "gui"][0]["started_at"] == started
    monkeypatch.setattr(clock, "now", lambda: T0 + timedelta(seconds=200))
    assert presence.live_for(cdb, "company_info", "X") == []
    presence.set_presence(cdb, record_type="company_info", record_id="Y", user_id="U2", interface="gui")
    assert cdb.conn.execute(__import__("sqlalchemy").text("SELECT count(*) FROM presence")).scalar() == 1, "stale rows pruned"


def test_directives(cdb):
    a, _ = directives.add(cdb, text="Post finished jobs", given_by="H1", recorded_by="A1", via="mcp")
    b, _ = directives.add(cdb, text="Attach receipts", given_by="H1", recorded_by="H1", via="cli")
    assert (a["code"], b["code"]) == ("SI-1", "SI-2")
    assert directives.resolve(cdb, "si-2")["id"] == b["id"] and directives.resolve(cdb, a["id"].lower())["code"] == "SI-1"
    with pytest.raises(BookflowError) as e:
        directives.resolve(cdb, "receipt")
    assert e.value.code == "E_DIRECTIVE_NOT_FOUND" and e.value.details["suggestions"] == ["SI-2"]
    directives.deactivate(cdb, b, "H1", "cli")
    with pytest.raises(BookflowError) as e:
        directives.resolve(cdb, "SI-2", include_inactive=False)
    assert e.value.code == "E_DIRECTIVE_INACTIVE" and e.value.details["deactivated_by"] == "H1"
    assert [d["code"] for d in directives.list_all(cdb, include_inactive=False)] == ["SI-1"]
    c3, _ = directives.add(cdb, text="Third", given_by="H1", recorded_by="H1", via="cli")
    assert c3["code"] == "SI-3", "codes are never reused"
