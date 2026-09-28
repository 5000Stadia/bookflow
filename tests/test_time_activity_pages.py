"""R144: recorded time has browser pages like the other documents.

The list reads as a timesheet (who, when, for whom, as what, how long, billable), each entry
opens as its document, the form speaks in hours and service items, and the Customers section
and the finder reach it.
"""
import re

from fastapi.testclient import TestClient

from tests.test_row3_host import PASSWORD, hosted  # noqa: F401


def _browser(hosted):
    browser = TestClient(hosted.handle.app)
    assert browser.post("/login", json={"username": hosted.login, "password": PASSWORD}).status_code == 200
    return browser


def test_time_entries_list_open_and_are_reached_like_documents(hosted):
    browser = _browser(hosted)
    company = f"/c/{hosted.company_id}"
    listing = browser.get(f"{company}/time-activity")
    assert listing.status_code == 200
    head = listing.text.split("<thead>", 1)[1].split("</thead>", 1)[0]
    for column in ("Employee", "Date", "Customer", "Service item", "Hours", "Billable", "Total", "Status"):
        assert f">{column}" in head, column
    body = listing.text.split("<tbody>", 1)[1].split("</tbody>", 1)[0]
    assert "Casey Worker" in body and "Mainline Clearing" in body and ">2.5<" in body and ">0.75<" in body
    entry = re.search(rf'href="{company}/time-activity/(\w+)"', body).group(1)

    shown = browser.get(f"{company}/time-activity/{entry}")
    assert shown.status_code == 200 and "Time entry" in shown.text and "Casey Worker" in shown.text

    form = browser.get(f"{company}/time-activity/create").text
    assert "Time entries</a>" in form  # the breadcrumb names the list it goes back to
    for label in ("Hours", "Service item", "What was done", "Class"):
        assert re.search(rf">\s*{label}\s*(\*|<)", form), label
    assert ">Class id" not in form

    section = browser.get(f"{company}/_group/customers").text
    assert f'href="{company}/time-activity"' in section and f'href="{company}/time-activity/create"' in section
    finder = browser.get(f"{company}/_finder").json()
    entries = finder if isinstance(finder, list) else finder.get("items", finder.get("entries", []))
    found = {entry["href"]: entry for entry in entries}
    assert found[f"{company}/time-activity"]["label"] == "Time entries"
    create = found[f"{company}/time-activity/create"]
    assert "New time entry" in (create["label"], *create["keywords"])
