"""R92 follow-up: a record's activity panel reads like the audit trail, worded on the server.

The panel's list comes from the workbench's `_activity` route, which returns the `activity`
command's own result with each item also carrying `sentence` and `who` from the one wording rule
(`workbench/activity.py`). The command's JSON is unchanged.
"""
from fastapi.testclient import TestClient

from tests.test_row3_host import PASSWORD, hosted  # noqa: F401


def test_record_activity_is_worded_like_the_audit_trail(hosted):
    browser = TestClient(hosted.handle.app)
    assert browser.post("/login", json={"username": hosted.login, "password": PASSWORD}).status_code == 200
    info = hosted.ok("company.show", {}, company=hosted.company_id)["info"]
    hosted.ok("company.update", {"negative_number_style": "parentheses"}, company=hosted.company_id)
    target = {"record_type": "company_info", "record_id": info["id"]}

    raw = hosted.ok("activity", {**target, "limit": 20}, company=hosted.company_id)
    assert raw["items"] and not any("sentence" in item or "who" in item for item in raw["items"])

    page = browser.get(f"/c/{hosted.company_id}/_activity", params={**target, "limit": "20"})
    assert page.status_code == 200 and page.headers["cache-control"] == "no-store"
    worded = page.json()
    assert [{k: v for k, v in item.items() if k not in ("sentence", "who")} for item in worded["items"]] == raw["items"]
    latest = next(item for item in worded["items"] if "negative_number_style" in (item["summary"] or ""))
    assert latest["sentence"] == "Updated company info: negative number style"
    assert latest["who"] == latest["actor_name"]

    # The panel asks the worded route and draws its sentence.
    script = browser.get("/static/annotations.js").text
    assert "/_activity?" in script and "item.sentence" in script
