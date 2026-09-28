"""R88: agent administration reads like the rest of the product.

Over a real host with a logged-in installer: `user list` and `membership list` page by cursor in
the command and the browser; the finder reaches the installation pages the viewer may run; agent,
token, user and membership pages read in names, dates and sentences rather than ids, timestamps
and codes; people are chosen from a list; refusals are plain; and a document's change history
says who did it, for whom and through what.
"""
import re

import pytest
from fastapi.testclient import TestClient

from tests.conftest import make_agent, hosted_call
from tests.test_row3_host import PASSWORD, WB, hosted  # noqa: F401  (fixture)

ULID = re.compile(r"\b[0-9A-HJKMNP-TV-Z]{26}\b")
ISO = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d")
REASON = {"X-Bookflow-Reason": "R88 witness"}

pytestmark = pytest.mark.timeout(600)


def _browser(hosted, username=None, password=PASSWORD):
    browser = TestClient(hosted.handle.app)
    assert browser.post("/login", json={"username": username or hosted.login, "password": password}).status_code == 200
    return browser


def _visible(html):
    """The page's text a person reads: no tags, no technical-details block, no attribute values."""
    html = re.sub(r"<details class=\"(technical-details|error-details).*?</details>", "", html, flags=re.S)
    html = re.sub(r"<(script|style)\b.*?</\1>", "", html, flags=re.S)
    return re.sub(r"<[^>]+>", " ", html)


def test_user_and_membership_lists_page_in_the_command_and_the_browser(hosted):
    for name in ("pager-a", "pager-b", "pager-c"):
        hosted.ok("user.add", {"username": name, "company": hosted.company_id, "role": "readonly",
                               "password": "pw-" + name + "-12345"}, headers=REASON)
    for noun, key in (("user", "username"), ("membership", "membership_id")):
        seen, cursor = [], None
        while True:
            page = hosted.ok(f"{noun}.list", {"limit": 2, **({"cursor": cursor} if cursor else {})})
            assert page["count"] == len(page["items"]) <= 2
            seen += [row[key] for row in page["items"]]
            cursor = page["next_cursor"]
            assert page["has_more"] is (cursor is not None)
            if not cursor:
                break
        whole = hosted.ok(f"{noun}.list", {"limit": 200})
        assert seen == [row[key] for row in whole["items"]] and len(seen) >= 5, noun
        wrong = hosted.call(f"{noun}.list", {"cursor": "not-a-cursor"})
        assert wrong.status_code == 422 and wrong.json()["details"]["fields"][0]["field"] == "cursor"

    browser = _browser(hosted)
    first = browser.get("/hub/user?limit=2")
    assert first.status_code == 200
    link = re.search(r'href="([^"]*cursor=[^"]*)"[^>]*rel="next"|rel="next"[^>]*href="([^"]*)"', first.text)
    following = (link.group(1) or link.group(2)).replace("&amp;", "&")
    second = browser.get(following)
    assert second.status_code == 200 and "Page 2" in second.text
    listed = _visible(first.text) + _visible(second.text)
    assert "Pager-A" in listed or "pager-a" in listed
    members = browser.get("/hub/membership")
    text = _visible(members.text)
    assert members.status_code == 200 and "Read only" in text and "Owner" in text
    revoke = browser.get("/hub/token/revoke")
    picker = re.search(r'<select[^>]*name="f:token"[^>]*>(.*?)</select>', revoke.text, re.S)
    assert picker and "robot-one" in picker.group(1) and not ULID.search(_visible(picker.group(1)))
    for page in (first, members, browser.get("/hub/token"), browser.get("/hub/agent")):
        text = _visible(page.text)
        assert not ULID.search(text) and not ISO.search(text), text


def test_the_finder_reaches_installation_pages_the_viewer_may_run(hosted):
    installer = _browser(hosted)
    found = {item["href"]: item for item in installer.get(f"/c/{hosted.company_id}/_finder").json()["items"]}
    for href in ("/hub/user", "/hub/membership", "/hub/token", "/hub/token/issue", "/hub/agent",
                 "/hub/agent/create", "/hub/organization"):
        assert href in found and found[href]["section"] == "Installation", href
        assert installer.get(href).status_code == 200, href

    hosted.ok("user.add", {"username": "clerk", "company": hosted.company_id, "role": "standard",
                           "password": "pw-clerk-12345"}, headers=REASON)
    clerk = _browser(hosted, "clerk", "pw-clerk-12345")
    theirs = {item["href"] for item in clerk.get(f"/c/{hosted.company_id}/_finder").json()["items"]}
    assert {"/hub/token", "/hub/token/issue", "/hub/user"} <= theirs
    assert not theirs & {"/hub/agent", "/hub/agent/create", "/hub/user/add"}
    # Page links only: no organization, company or person is named by the finder.
    from bookflow.adapters.workbench.admin import FINDER
    assert {i["label"] for i in clerk.get(f"/c/{hosted.company_id}/_finder").json()["items"]
            if i["section"] == "Installation"} <= {label for _, label, _, _ in FINDER}


def test_agent_pages_and_forms_read_in_names_and_sentences(hosted):
    call = hosted_call(hosted)
    hosted.ok("user.add", {"username": "pat", "display_name": "Pat Doe", "company": hosted.company_id,
                           "role": "owner", "password": "pw-pat-12345"}, headers=REASON)
    agent = hosted.ok("agent.create", {"username": "office-assistant", "display_name": "Office assistant"},
                      headers=REASON)["agent"]["agent_id"]
    browser = _browser(hosted)

    page = browser.get(f"/hub/agent/{agent}")
    text = _visible(page.text)
    assert page.status_code == 200 and "Not yet authorized" in text and "not_yet_authorized" not in text
    assert not ULID.search(text) and not ISO.search(text), text
    assert "Nobody yet" in text

    assign = browser.get(f"/hub/agent/{agent}/assign")
    assert assign.status_code == 200
    assert re.search(r'<select disabled[^>]*><option[^>]*selected>Office assistant</option>', assign.text)
    principal = re.search(r'<select[^>]*name="f:principal"[^>]*>(.*?)</select>', assign.text, re.S)
    assert principal and 'value="pat"' in principal.group(1) and "Pat Doe" in principal.group(1)
    assert 'type="checkbox" id="control-' in assign.text and "allow it to act for them" in assign.text
    refused = browser.post(f"/hub/agent/{agent}/assign", headers=WB,
                           data={"originals": "{}", "action": "submit", "f:agent": agent, "f:principal": "pat"})
    assert "Confirm that the people this agent acts for permit it" in _visible(refused.text)
    assert "E_VALIDATION" not in _visible(refused.text)
    done = browser.post(f"/hub/agent/{agent}/assign", headers=WB, follow_redirects=True,
                        data={"originals": "{}", "action": "submit", "f:agent": agent, "f:principal": "pat",
                              "f:confirm_permitted_use": "true"})
    assert done.status_code == 200, done.text

    authorize = browser.get(f"/hub/agent/{agent}/authorize")
    assert "acknowledge_fresh_context" not in authorize.text, "no fresh-context box before one is needed"
    call("membership grant", {"user": agent, "company": hosted.company_id, "role": "standard"})
    call("agent authorize", {"agent": agent, "confirm_permitted_use": True})
    call("agent unassign", {"agent": agent, "principal": "pat"})
    call("agent assign", {"agent": agent, "principal": "pat", "confirm_permitted_use": True})
    authorize = browser.get(f"/hub/agent/{agent}/authorize")
    assert 'name="f:acknowledge_fresh_context"' in authorize.text and "cannot erase what it already saw" in authorize.text
    refused = browser.post(f"/hub/agent/{agent}/authorize", headers=WB,
                           data={"originals": "{}", "action": "submit", "f:agent": agent,
                                 "f:confirm_permitted_use": "true"})
    words = _visible(refused.text)
    assert "fresh, isolated context" in words and "cannot erase what it already saw" in words
    assert "required" != words.strip() and "E_VALIDATION" not in words

    listing = _visible(browser.get("/hub/agent").text)
    assert "Office assistant" in listing and "Pat Doe" in listing
    users = _visible(browser.get(f"/c/{hosted.company_id}/users?user=office-assistant").text)
    assert "What Office assistant can do here" in users and "Can post, edit and void: yes" in users
    assert "ledger.post" not in users and "no_grant" not in users and not ULID.search(users)
    assert "Can delete invoices: no — needs an explicit grant" in users


def test_document_change_history_says_who_for_whom_and_through_what(hosted):
    agent = make_agent(hosted_call(hosted), "office-assistant", principals=hosted.login,
                       company=hosted.company_id, display_name="Office assistant")
    secret = hosted.ok("token.issue", {"user": agent, "principal": hosted.login, "label": "assistant"},
                       headers=REASON)["secret"]
    made = hosted.call("customer.create", {"name": "Posted by the assistant"}, company=hosted.company_id,
                       headers={"Authorization": "Bearer " + secret, **REASON})
    assert made.status_code == 200, made.text
    browser = _browser(hosted)
    page = browser.get(f"/c/{hosted.company_id}/customer/{made.json()['id']}")
    history = re.search(r'class="technical-details change-history">(.*?)</details>', page.text, re.S).group(1)
    person = hosted.ok("user.list", {"kind": "human"})["items"]
    name = next(row["display_name"] for row in person if row["username"] == hosted.login)
    assert f"Office assistant, for {name}, via agent over HTTP" in history
    activity = browser.get(f"/c/{hosted.company_id}/_activity",
                           params={"record_type": "customer", "record_id": made.json()["id"]}).json()
    assert f"Office assistant, for {name}, via agent over HTTP" in {item["who"] for item in activity["items"]}
