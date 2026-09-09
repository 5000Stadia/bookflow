"""Browser-first customer/job and shared reference interaction contracts."""

from __future__ import annotations

import html
import base64
import json
import re
import hashlib
from pathlib import Path

import pytest

from tests.test_row3_host import PASSWORD, WB, hosted, live
from tests.test_row5_workbench_forms import _browser, _page_originals
from tests.test_row5_browser_acceptance import CHROME, _Cdp


def test_company_context_and_content_versioned_assets(hosted):
    browser = _browser(hosted)
    customer = hosted.ok("customer create", {"name": "Header customer"}, company=hosted.company_id)
    for suffix in ("", "/create", f"/{customer['id']}", f"/{customer['id']}/update"):
        response = browser.get(f"/c/{hosted.company_id}/customer{suffix}")
        assert response.status_code == 200
        assert 'aria-label="Current company: Demo Plumbing Co"' in response.text
    for asset in ("style.css", "htmx.min.js", "workflow.js"):
        data = (Path(__file__).parents[1] / "src/bookflow/adapters/workbench/static" / asset).read_bytes()
        url = f"/static/{asset}?v={hashlib.sha256(data).hexdigest()[:16]}"
        assert url in response.text
        assert browser.get(url).content == data
    denied = browser.get("/c/01AAAAAAAAAAAAAAAAAAAAAAAA/customer")
    assert "Current company: Demo Plumbing Co" not in denied.text


def test_customer_pages_are_bounded_and_stale_page_has_filtered_restart(hosted):
    for name in ("Paged Workflow A", "Paged Workflow B", "Paged Workflow C"):
        hosted.ok("customer create", {"name": name}, company=hosted.company_id)
    browser = _browser(hosted)
    path = f"/c/{hosted.company_id}/customer"
    first = browser.get(path, params={"query": "Paged Workflow", "limit": 1, "sort": "full_name", "columns": "full_name,active"})
    assert first.status_code == 200
    assert "Paged Workflow A" in first.text and "Paged Workflow B" not in first.text
    next_url = html.unescape(re.search(r'href="([^"]+)" rel="next"', first.text).group(1))
    second = browser.get(next_url)
    assert "Paged Workflow B" in second.text and "Paged Workflow A" not in second.text
    hosted.ok("customer create", {"name": "Paged Workflow D"}, company=hosted.company_id)
    stale = browser.get(next_url)
    assert "E_QUERY_STALE" in stale.text and "Restart list with these filters" in stale.text
    assert 'query=Paged+Workflow' in stale.text and 'columns=full_name%2Cactive' in stale.text


def test_add_job_prefill_is_submitted_and_workspace_retains_technical_coverage(hosted):
    parent = hosted.ok("customer create", {"name": "Workflow Parent", "phone": "555-1200"}, company=hosted.company_id)
    browser = _browser(hosted)
    detail = browser.get(f"/c/{hosted.company_id}/customer/{parent['id']}")
    assert "customer-workspace" in detail.text and "All fields and technical details" in detail.text
    job_url = html.unescape(re.search(r'class="add-job" href="([^"]+)"', detail.text).group(1))
    form = browser.get(job_url)
    assert 'name="f:parent_id" value="' + parent["id"] + '"' in form.text
    assert "Workflow Parent" in form.text and "Commercial defaults" in form.text
    assert "parent_id" not in _page_originals(form)
    created = browser.post(f"/c/{hosted.company_id}/customer/create", headers=WB, data={
        "originals": json.dumps(_page_originals(form)), "f:name": "New bathroom",
        "f:parent_id": parent["id"], "action": "submit",
    })
    assert created.status_code == 303
    saved = browser.get(created.headers["location"])
    assert "Workflow Parent:New bathroom" in saved.text and "Saved successfully" in saved.text
    assert "Inherited values" in saved.text
    parent_page = browser.get(f"/c/{hosted.company_id}/customer/{parent['id']}")
    assert "Workflow Parent:New bathroom" in parent_page.text


def test_unresolved_name_cannot_silently_submit_old_selection(hosted):
    browser = _browser(hosted)
    response = browser.post(f"/c/{hosted.company_id}/customer/create", headers=WB, data={
        "originals": "{}", "f:name": "Unresolved workflow", "f:terms_id": "",
        "label:f:terms_id": "Ambiguous typed name", "ref-state:f:terms_id": "pending", "action": "submit",
    })
    assert response.status_code == 200 and "E_VALIDATION" in response.text
    assert 'value="Ambiguous typed name"' in response.text
    assert hosted.ok("customer query", {"query": "Unresolved workflow"}, company=hosted.company_id)["count"] == 0


def _login(browser: _Cdp, base: str, login: str) -> None:
    browser.navigate(base + "/login")
    browser.evaluate(f"""(() => {{
      document.querySelector('[name="username"]').value={json.dumps(login)};
      document.querySelector('[name="password"]').value={json.dumps(PASSWORD)};
      document.querySelector('form[hx-post="/login"]').requestSubmit();
    }})()""")
    browser.wait_for("document.readyState === 'complete' && !!document.querySelector('.nav-group')")
    browser.call("Page.addScriptToEvaluateOnNewDocument", {"source": "window.workflowErrors=[];addEventListener('error',e=>workflowErrors.push(e.message));addEventListener('unhandledrejection',e=>workflowErrors.push(String(e.reason)));"})


def _type(browser: _Cdp, name: str, value: str) -> None:
    browser.evaluate(f"""(() => {{const e=document.querySelector('[name='+CSS.escape({json.dumps(name)})+']');
      e.closest('details')?.setAttribute('open',''); e.focus(); e.value={json.dumps(value)};
      e.dispatchEvent(new Event('input',{{bubbles:true}}));}})()""")


def _choose(browser: _Cdp, name: str, text: str) -> None:
    _type(browser, "label:" + name, text)
    browser.wait_for(f"(() => {{const p=document.querySelector('[name='+CSS.escape({json.dumps(name)})+']').closest('[data-reference]');return !p.querySelector('[data-ref-options]').hidden && !![...p.querySelectorAll('[role=option]')].find(e=>e.textContent.includes({json.dumps(text)}));}})()")
    # Real keyboard events choose a displayed option without entering an id.
    browser.call("Input.dispatchKeyEvent", {"type": "keyDown", "key": "ArrowDown", "code": "ArrowDown"})
    browser.call("Input.dispatchKeyEvent", {"type": "keyUp", "key": "ArrowDown", "code": "ArrowDown"})
    browser.call("Input.dispatchKeyEvent", {"type": "keyDown", "key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13})
    browser.call("Input.dispatchKeyEvent", {"type": "keyUp", "key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13})
    browser.wait_for(f"!!document.querySelector('[name='+CSS.escape({json.dumps(name)})+']').value")


@pytest.mark.skipif(not CHROME.is_file(), reason="real Chrome is not installed")
def test_real_browser_job_edit_name_picker_preview_save_and_conflict(hosted, live, tmp_path):
    parent = hosted.ok("customer create", {"name": "Browser Workflow Parent", "company_name": "Harbor Services", "phone": "555-0100"}, company=hosted.company_id)
    browser = _Cdp(tmp_path / "customer-chrome")
    try:
        browser.viewport(1280, 800)
        _login(browser, live, hosted.login)
        browser.navigate(f"{live}/c/{hosted.company_id}/customer/{parent['id']}")
        for width, label in ((1280, "desktop"), (360, "narrow")):
            browser.viewport(width, 800)
            shot = browser.call("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": True})
            (tmp_path / f"customer-{label}.png").write_bytes(base64.b64decode(shot["data"]))
        browser.evaluate("document.querySelector('.add-job').click()")
        browser.wait_for("!!document.querySelector('[data-generated-form]')")
        _type(browser, "f:name", "Kitchen visit")
        _choose(browser, "f:terms_id", "Net 30")
        chosen_term = browser.evaluate("document.querySelector('[name=\"f:terms_id\"]').value")
        browser.evaluate("document.querySelector('button[value=preview]').click()")
        browser.wait_for("document.body.innerText.includes('Preview (nothing written)')")
        assert browser.evaluate("document.querySelector('[name=\"f:name\"]').value") == "Kitchen visit"
        assert browser.evaluate("document.querySelector('[name=\"f:terms_id\"]').value") == chosen_term
        browser.evaluate("document.querySelector('button[value=submit]').click()")
        browser.wait_for("!!document.querySelector('.save-feedback') && !!document.querySelector('.customer-workspace')")
        job = hosted.ok("customer list", {"query": "Kitchen visit"}, company=hosted.company_id)["items"][0]
        assert job["parent_id"] == parent["id"] and job["terms_id"] == chosen_term
        browser.evaluate("document.querySelector('.actions a[href$=\"/update\"]').click()")
        browser.wait_for("!!document.querySelector('[data-generated-form]')")
        version = browser.evaluate("document.querySelector('[name=\"f:expected_version\"]').value")
        _type(browser, "f:notes", "My unsaved note")
        hosted.ok("customer update", {"customer": job["id"], "expected_version": job["version"], "notes": "Other client's note"}, company=hosted.company_id)
        browser.evaluate("document.querySelector('button[value=submit]').click()")
        browser.wait_for("document.body.innerText.includes('E_VERSION_CONFLICT')")
        assert browser.evaluate("document.querySelector('[name=\"f:notes\"]').value") == "My unsaved note"
        assert browser.evaluate("document.querySelector('[name=\"f:expected_version\"]').value") == version
        assert browser.evaluate("Math.max(document.body.scrollWidth,document.documentElement.scrollWidth)-innerWidth") <= 1
        assert browser.evaluate("workflowErrors") == []
    finally:
        browser.close()


@pytest.mark.skipif(not CHROME.is_file(), reason="real Chrome is not installed")
def test_real_browser_add_new_callback_and_inactive_current_reference(hosted, live, tmp_path):
    browser = _Cdp(tmp_path / "callback-chrome")
    try:
        _login(browser, live, hosted.login)
        browser.navigate(f"{live}/c/{hosted.company_id}/customer/create")
        _type(browser, "f:name", "Preserved customer draft")
        browser.call("Runtime.evaluate", {"expression": """(() => {
          const link=document.querySelector('[name="f:terms_id"]').closest('[data-reference]').querySelector('[data-ref-add-target="term"]');
          link.closest('details').open=true; window.workflowChild=window.open('',link.target); link.click();
        })()""", "userGesture": True})
        browser.wait_for("!!window.workflowChild && !workflowChild.closed && workflowChild.document.readyState==='complete' && !!workflowChild.document.querySelector('[data-generated-form]')")
        browser.evaluate("""(() => {
          const doc=workflowChild.document;
          doc.querySelector('[name="f:name"]').value='Browser Callback Net 17';
          const kind=doc.querySelector('[name="f:kind"]');kind.value='standard';kind.dispatchEvent(new workflowChild.Event('change',{bubbles:true}));
          doc.querySelector('[name="f:due_days"]').value='17';
          doc.querySelector('[data-generated-form]').requestSubmit(doc.querySelector('button[value=submit]'));
        })()""")
        browser.wait_for("!!document.querySelector('[name=\"f:terms_id\"]').value")
        assert browser.evaluate("document.querySelector('[name=\"f:name\"]').value") == "Preserved customer draft"
        assert browser.evaluate("document.querySelector('[name=\"label:f:terms_id\"]').value") == "Browser Callback Net 17"
        term_id = browser.evaluate("document.querySelector('[name=\"f:terms_id\"]').value")
        browser.evaluate("document.querySelector('button[value=submit]').click()")
        browser.wait_for("!!document.querySelector('.customer-workspace')")
        customer = hosted.ok("customer list", {"query": "Preserved customer draft"}, company=hosted.company_id)["items"][0]
        term = hosted.ok("term show", {"term": term_id}, company=hosted.company_id)
        hosted.ok("term deactivate", {"term": term_id, "expected_version": term["version"]}, company=hosted.company_id)
        browser.navigate(f"{live}/c/{hosted.company_id}/customer/{customer['id']}/update")
        assert browser.evaluate("document.querySelector('[name=\"label:f:terms_id\"]').value") == "Browser Callback Net 17"
        assert browser.evaluate("document.querySelector('[name=\"f:terms_id\"]').type") == "hidden"
        _type(browser, "f:notes", "Unchanged inactive term retained")
        browser.evaluate("document.querySelector('button[value=submit]').click()")
        browser.wait_for("!!document.querySelector('.save-feedback')")
        assert hosted.ok("customer show", {"customer": customer["id"]}, company=hosted.company_id)["terms_id"] == term_id
        assert browser.evaluate("workflowErrors") == []
    finally:
        browser.close()


@pytest.mark.skipif(not CHROME.is_file(), reason="real Chrome is not installed")
def test_real_browser_nested_component_unit_vendor_and_dynamic_identity(hosted, live, tmp_path):
    company = hosted.company_id
    units = hosted.ok("unit-of-measure create", {"name": "Browser Units", "units": [
        {"name": "Each", "abbreviation": "ea", "is_base": True, "base_factor": "1"},
        {"name": "Pair", "abbreviation": "pr", "is_base": False, "base_factor": "2"},
    ]}, company=company)
    hosted.ok("company update", {"units_of_measure_mode": "multiple_related_units"}, company=company)
    account = hosted.ok("account create", {"name": "Browser Expense", "type": "expense"}, company=company)
    profile = {"type": "service", "sales_enabled": False, "purchase_enabled": True, "purchase_description": "Purchased component", "cost": "1", "expense_account_id": account["id"]}
    component = hosted.ok("item create", {"name": "Browser Unit Component", **profile, "unit_of_measure_set_id": units["id"]}, company=company)
    no_unit = hosted.ok("item create", {"name": "Browser Bare Component", **profile}, company=company)
    vendor = hosted.ok("vendor create", {"name": "Browser Supply Vendor"}, company=company)
    group = hosted.ok("item create", {"name": "Browser Group", "type": "group", "print_members": True, "members": [{"component_item_id": component["id"], "unit_id": units["units"][0]["id"], "quantity": "1"}]}, company=company)
    browser = _Cdp(tmp_path / "nested-chrome")
    try:
        _login(browser, live, hosted.login)
        browser.navigate(f"{live}/c/{company}/item/{group['id']}/update")
        assert browser.evaluate("[...document.querySelectorAll('[id]')].every(e=>document.querySelector('#'+e.id)===e)")
        assert browser.evaluate("document.querySelector('[name=\"label:c:members:0:component_item_id\"]').value") == component["full_name"]
        _choose(browser, "c:members:0:component_item_id", no_unit["name"])
        assert browser.evaluate("document.querySelector('[name=\"c:members:0:unit_id\"]').value") == ""
        _choose(browser, "c:members:0:component_item_id", component["name"])
        _choose(browser, "c:members:0:unit_id", "Pair")
        pair_id = next(unit["id"] for unit in units["units"] if unit["name"] == "Pair")
        assert browser.evaluate("document.querySelector('[name=\"c:members:0:unit_id\"]').value") == pair_id
        browser.evaluate("document.querySelector('[data-collection-path=members] > [data-collection-add]').click()")
        dynamic_name = browser.evaluate("[...document.querySelectorAll('[data-ref-value][name$=\":component_item_id\"]')].at(-1).name")
        _choose(browser, dynamic_name, no_unit["name"])
        _type(browser, dynamic_name.replace(":component_item_id", ":quantity"), "2")
        browser.evaluate("document.querySelector('[data-collection-path=members] > [data-collection-items]').lastElementChild.querySelector('[data-collection-up]').click()")
        browser.evaluate("document.querySelector('button[value=preview]').click()")
        browser.wait_for("document.body.innerText.includes('Preview (nothing written)')")
        assert browser.evaluate("document.querySelector('[name=\"c:members:0:component_item_id\"]').value") == no_unit["id"]
        browser.evaluate("document.querySelector('button[value=submit]').click()")
        browser.wait_for("!!document.querySelector('.save-feedback')")
        saved = hosted.ok("item show", {"item": group["id"]}, company=company)
        assert [row["component_item_id"] for row in saved["members"] if row["active"]] == [no_unit["id"], component["id"]]
        browser.navigate(f"{live}/c/{company}/item/{component['id']}/update")
        browser.evaluate("document.querySelector('[data-collection-path=vendor_profiles] > [data-collection-add]').click()")
        vendor_name = browser.evaluate("document.querySelector('[data-collection-path=vendor_profiles] [data-ref-value][name$=\":vendor_id\"]').name")
        _choose(browser, vendor_name, vendor["name"])
        assert browser.evaluate(f"document.querySelector('[name='+CSS.escape({json.dumps(vendor_name)})+']').value") == vendor["id"]
        assert browser.evaluate("workflowErrors") == []
    finally:
        browser.close()
