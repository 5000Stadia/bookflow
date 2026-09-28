"""R87: a newly issued token's secret is shown once in its own panel, and the company Agents panel
offers New agent to the people who may create one."""

from __future__ import annotations

import html
import json
import time
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from tests.test_row3_host import PASSWORD, WB, hosted  # noqa: F401 - fixture
from tests.test_row5_browser_acceptance import CHROME, PASSWORD as SITE_PASSWORD, _Cdp, browser_site  # noqa: F401

REASON = {"X-Bookflow-Reason": "r87 witness"}


def _login(app, username, password) -> TestClient:
    client = TestClient(app, follow_redirects=False)
    assert client.post("/login", json={"username": username, "password": password}).status_code == 200
    return client


def _secret_box(page: str) -> str | None:
    marker = 'id="one-time-secret-value"'
    if marker not in page:
        return None
    return html.unescape(page.split(marker, 1)[1].split(">", 1)[1].split("</textarea>", 1)[0])


@pytest.mark.timeout(900)
def test_issued_secret_shows_once_in_its_own_panel_and_never_on_revisit(hosted):
    owner = _login(hosted.handle.app, hosted.login, PASSWORD)
    issued = owner.post("/hub/token/issue", headers=WB,
                        data={"originals": "{}", "f:label": "panel-secret", "action": "submit"})
    assert issued.status_code == 303
    location = issued.headers["location"]
    assert set(parse_qs(urlsplit(location).query)) == {"flash"}

    shown = owner.get(location)
    assert shown.status_code == 200 and shown.headers["cache-control"] == "no-store"
    secret = _secret_box(shown.text)
    assert secret and secret not in location
    panel = shown.text.split('class="save-feedback one-time-secret"', 1)[1].split("</section>", 1)[0]
    assert "data-copy-secret" in panel and ">Copy</button>" in panel
    assert "shown only once" in panel and "only its hash" in panel
    # The panel carries the real, working secret.
    assert hosted.call("company.show", {}, company=hosted.company_id,
                       headers={"Authorization": "Bearer " + secret}).status_code == 200

    again = owner.get(location)
    assert again.status_code == 200
    assert _secret_box(again.text) is None and secret not in again.text
    assert owner.get(urlsplit(location).path).text.count(secret) == 0


@pytest.mark.timeout(900)
def test_new_agent_is_offered_to_agent_administrators_only_and_returns_to_the_panel(hosted):
    installer = _login(hosted.handle.app, hosted.login, PASSWORD)
    page = installer.get(f"/c/{hosted.company_id}/users")
    assert page.status_code == 200 and "data-new-agent" in page.text
    back = f"/c/{hosted.company_id}/users"
    link = html.unescape(page.text.split("data-new-agent", 1)[0].rsplit('href="', 1)[1].split('"', 1)[0])
    assert urlsplit(link).path == "/hub/agent/create" and parse_qs(urlsplit(link).query) == {"back": [back]}

    form = installer.get(link)
    assert form.status_code == 200 and f'name="_back" value="{back}"' in form.text
    made = installer.post("/hub/agent/create", headers=WB,
                          data={"originals": "{}", "f:username": "panel-agent", "_back": back, "action": "submit"})
    assert made.status_code == 303, made.text
    target = urlsplit(made.headers["location"])
    assert target.path == back and parse_qs(target.query)["user"] == ["panel-agent"]
    landed = installer.get(made.headers["location"])
    assert landed.status_code == 200 and "Saved successfully" in landed.text
    # The next step, granting access, is loaded for the new agent, named as it is named elsewhere.
    assert 'type="hidden" name="user" value="panel-agent"' in landed.text
    assert 'aria-label="Company user permissions"' in landed.text and "<b>panel-agent</b>" in landed.text

    # Anything but a company's Users & permissions page is ignored as a destination.
    stray = installer.post("/hub/agent/create", headers=WB,
                           data={"originals": "{}", "f:username": "stray-agent", "_back": "//elsewhere.test/c/x/users",
                                 "action": "submit"})
    assert stray.status_code == 303 and urlsplit(stray.headers["location"]).path.startswith("/hub/agent/")

    hosted.ok("user.add", {"username": "company-admin", "company": hosted.company_id, "role": "admin",
                           "password": "pw-company-admin-12345"}, headers=REASON)
    company_admin = _login(hosted.handle.app, "company-admin", "pw-company-admin-12345")
    theirs = company_admin.get(f"/c/{hosted.company_id}/users", headers=WB)
    assert theirs.status_code == 200 and 'id="company-agents"' in theirs.text
    assert "data-new-agent" not in theirs.text and "/hub/agent/create" not in theirs.text

    # A company no agent reaches still offers New agent to an administrator, and no panel to anyone else.
    other = hosted.ok("company.new", {"legal_name": "Agentless Books", "home_currency": "USD"}, headers=REASON)["company_id"]
    empty = installer.get(f"/c/{other}/users")
    assert empty.status_code == 200 and 'id="company-agents"' in empty.text and "data-new-agent" in empty.text
    hosted.ok("user.add", {"username": "other-admin", "company": other, "role": "admin",
                           "password": "pw-other-admin-12345"}, headers=REASON)
    other_admin = _login(hosted.handle.app, "other-admin", "pw-other-admin-12345")
    none_here = other_admin.get(f"/c/{other}/users", headers=WB)
    assert none_here.status_code == 200 and 'id="company-agents"' not in none_here.text


@pytest.mark.timeout(900)
@pytest.mark.skipif(not CHROME.exists(), reason="Chrome unavailable")
@pytest.mark.parametrize("width", [390, 1440])
def test_secret_panel_copies_in_chrome_and_is_gone_after_reload(browser_site, tmp_path, width):
    b = _Cdp(tmp_path / "chrome")
    try:
        b.navigate(browser_site.base_url + "/login")
        b.evaluate(f"""(() => {{
          document.querySelector('[name="username"]').value = {json.dumps(browser_site.login)};
          document.querySelector('[name="password"]').value = {json.dumps(SITE_PASSWORD)};
          document.querySelector('form[hx-post="/login"]').requestSubmit();
        }})()""")
        b.wait_for("!location.pathname.startsWith('/login')", timeout=20)
        b.viewport(width, 900)
        b.navigate(browser_site.base_url + "/hub/token/issue")
        b.wait_for('document.readyState === "complete" && !!document.getElementsByName("f:label")[0]')
        b.evaluate('''(() => {const e=document.getElementsByName("f:label")[0]; e.value="browser-secret";
            e.dispatchEvent(new Event("input",{bubbles:true}));
            document.querySelector("button[value=submit]").click();})()''')
        b.wait_for('document.readyState === "complete" && !!document.querySelector("[data-one-time-secret]")', timeout=20)
        secret = b.evaluate('document.querySelector("[data-one-time-secret]").value')
        assert len(secret) >= 20 and secret not in b.evaluate("location.href")
        box = b.evaluate('(() => {const r=document.querySelector("[data-one-time-secret]").getBoundingClientRect();'
                         'return {w:r.width,h:r.height,top:r.top}})()')
        assert box["w"] > 150 and box["h"] > 20 and box["top"] < 900, box  # visible above the fold
        assert b.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        b.call("Browser.grantPermissions", {"permissions": ["clipboardReadWrite", "clipboardSanitizedWrite"],
                                            "origin": browser_site.base_url})
        b.evaluate('document.querySelector("[data-copy-secret]").click()')
        b.wait_for('!!document.querySelector("[data-copy-status]").textContent')
        status = b.evaluate('document.querySelector("[data-copy-status]").textContent')
        if status == "Copied.":
            b.call("Page.bringToFront")
            copied = b.evaluate("navigator.clipboard.readText().catch(() => null)", await_promise=True)
            assert copied in (None, secret)
        else:  # no clipboard access: the secret is selected for the person to copy
            assert status.startswith("Selected") and b.evaluate(
                'document.activeElement.id === "one-time-secret-value" && '
                'document.activeElement.selectionEnd - document.activeElement.selectionStart === document.activeElement.value.length')

        b.evaluate("location.reload()")
        time.sleep(0.5)
        b.wait_for('document.readyState === "complete"', timeout=20)
        assert not b.evaluate('!!document.querySelector("[data-one-time-secret]")')
        assert secret not in b.evaluate("document.documentElement.outerHTML")
    finally:
        b.close()
