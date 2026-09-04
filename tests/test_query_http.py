"""Bounded query transport errors and continuation recovery."""

from tests.test_row3_host import hosted


def test_stale_query_is_conflict_and_can_restart(hosted):
    cid = hosted.company_id
    first = hosted.ok("customer.query", {"limit": 1}, company=cid)
    assert first["next_cursor"]
    hosted.ok("customer.create", {"name": "New query customer"}, company=cid)
    stale = hosted.call("customer.query", {"limit": 1, "cursor": first["next_cursor"]}, company=cid)
    assert stale.status_code == 409
    assert stale.json()["code"] == "E_QUERY_STALE"
    restarted = hosted.ok("customer.query", {"limit": 1}, company=cid)
    assert restarted["count"] == 1
    assert restarted["next_cursor"] != first["next_cursor"]


def test_invalid_query_limit_is_validation_error(hosted):
    invalid = hosted.call("customer.query", {"limit": 201}, company=hosted.company_id)
    assert invalid.status_code == 422
    assert invalid.json()["code"] == "E_VALIDATION"
