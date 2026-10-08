"""R162: a closing date in the future is allowed (the anchor product allows any date) but said plainly."""
import json
from datetime import datetime, timezone

from tests.conftest import make_actor  # noqa: F401  (fixtures)
from tests.test_row3_host import PASSWORD, WB, hosted  # noqa: F401
from fastapi.testclient import TestClient

from bookflow.commands.company_cmds import _future_closing_warning

COMPANY = "Demo Plumbing Co"
FUTURE = "2099-12-31"
PAST = "2020-01-31"


def _text(date):
    return f"Closing date {date} is in the future: nothing dated on or before it can be posted until you move it back."


def test_command_warns_for_a_future_closing_date_and_not_a_past_one(client):
    preview = client.company.update(company=COMPANY, closing_date=FUTURE, dry_run=True)
    assert _text(FUTURE) in preview["warnings"]
    saved = client.company.update(company=COMPANY, closing_date=FUTURE)
    assert _text(FUTURE) in saved["warnings"]
    assert client.company.show(company=COMPANY)["info"]["closing_date"] == FUTURE  # allowed, not refused

    for dry in (True, False):
        past = client.company.update(company=COMPANY, closing_date=PAST, dry_run=dry)
        assert not [w for w in past["warnings"] if "in the future" in w]
    other = client.company.update(company=COMPANY, website="https://example.test")  # closing date untouched
    assert not [w for w in other["warnings"] if "in the future" in w]


def test_the_company_calendar_decides_what_today_is():
    now = datetime(2026, 10, 8, 23, 30, tzinfo=timezone.utc)  # already 9 Oct in Auckland, still 8 Oct in Los Angeles
    assert _future_closing_warning("2026-10-09", "America/Los_Angeles", now) == _text("2026-10-09")
    assert _future_closing_warning("2026-10-09", "Pacific/Auckland", now) is None
    assert _future_closing_warning("2026-10-08", "America/Los_Angeles", now) is None


def test_browser_form_shows_the_warning_on_preview_and_after_save(hosted):  # noqa: F811
    owner = TestClient(hosted.handle.app, follow_redirects=False)
    assert owner.post("/login", json={"username": hosted.login, "password": PASSWORD}).status_code == 200
    url = f"/c/{hosted.company_id}/company/self/update"

    def form(date, action):
        before = hosted.info()
        return owner.post(url, headers=WB, data={
            "originals": json.dumps({**before["info"], "expected_version": before["info_version"]}, default=str),
            "f:closing_date": date, "action": action})

    preview = form(FUTURE, "preview")
    assert preview.status_code == 200 and "data-preview-warnings" in preview.text and _text(FUTURE) in preview.text
    quiet = form(PAST, "preview")
    assert quiet.status_code == 200 and "in the future" not in quiet.text

    saved = form(FUTURE, "submit")
    assert saved.status_code == 303
    shown = owner.get(saved.headers["location"])
    assert "Saved successfully" in shown.text and _text(FUTURE) in shown.text
