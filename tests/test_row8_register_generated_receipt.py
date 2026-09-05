"""Generated register saves open the authoritative business document."""
import json

import pytest

from tests.test_row5_browser_acceptance import CHROME, browser_site  # noqa: F401
from tests.test_row8_register_browser import register_browser, _command, _key, _type  # noqa: F401
from tests.test_row8_custom_field_browser import _tab

pytestmark = pytest.mark.skipif(not CHROME.exists(), reason="Chrome is unavailable")


@pytest.mark.parametrize("width", [1280, 390])
@pytest.mark.parametrize("operation", ["post", "update"])
def test_generated_register_save_opens_journal_receipt(register_browser, width, operation):
    env, b = register_browser, register_browser.browser
    b.viewport(width, 900 if width == 1280 else 844)
    field = _command(b, env.site, "custom-field.create", {
        "name": "Receipt work order", "kind": "text", "scopes": ["journal_entry"]})
    payload = {"account": env.bank["id"], "category": env.expense["id"],
               "date": "2026-02-12", "direction": "decrease", "amount": "1.00"}
    previous = _command(b, env.site, "register.post", payload) if operation == "update" else None
    suffix = f"{previous['id']}/update" if previous else "post"
    b.navigate(f"{env.site.base_url}/c/{env.site.company_id}/register/{suffix}")
    selector = f'[name="cf:{field["id"]}"]'
    b.wait_for("!!document.querySelector(" + json.dumps(selector) + ")")
    if operation == "post":
        for name, value in payload.items():
            b.evaluate("document.getElementsByName(" + json.dumps("f:" + name) + ")[0].value = " + json.dumps(value))
    _tab(b, selector); _type(b, "WO-SAVED")
    _tab(b, 'button[value="submit"]'); _key(b, "Enter")
    b.wait_for("!document.querySelector('[data-generated-form]') && document.body.textContent.includes('Saved successfully')")
    href = b.evaluate("location.pathname")
    assert href.startswith(f"/c/{env.site.company_id}/journal/"), b.evaluate("document.body.innerText")
    identifier = href.rsplit("/", 1)[1]
    if previous:
        assert identifier == previous["id"]
    shown = _command(b, env.site, "journal.show", {"journal": identifier})
    assert shown["version"] == (2 if previous else 1)
    assert shown["revision"]["custom_fields_snapshot"][field["id"]]["value"] == "WO-SAVED"
    body = b.evaluate("document.body.innerText")
    assert "E_USAGE" not in body and "WO-SAVED" in body
