"""Actual Chrome custom-field witnesses; parent controls release from HOLD.

CDP keyboard/viewport checks do not claim to exercise a physical soft keyboard.
"""
import json

import pytest

from tests.test_row5_browser_acceptance import CHROME, browser_site  # noqa: F401
from tests.test_row8_register_browser import (  # noqa: F401
    register_browser, _command, _key, _type, _simple_draft,
)

pytestmark = pytest.mark.skipif(not CHROME.exists(), reason="Chrome is unavailable")


def _tab(browser, selector):
    for _ in range(240):
        if browser.evaluate(f"document.activeElement.matches({json.dumps(selector)})"):
            return
        _key(browser, "Tab")
    raise AssertionError(f"Keyboard could not reach {selector}")


def _save(browser):
    _tab(browser, "#register-record")
    _key(browser, "Enter")
    browser.wait_for("!document.querySelector('#register-receipt').hidden || !!document.querySelector('#register-error').textContent")
    assert browser.evaluate("document.querySelector('#register-error').textContent") == ""
    browser.wait_for("document.activeElement.id === 'register-date'")
    return browser.evaluate("document.querySelector('#register-receipt a').href.split('/').pop()")


def _definitions(env, *, many=False):
    fields = {}
    kinds = [("text", {}), ("bool", {}), ("number", {}), ("date", {}),
             ("choice", {"choices": [{"value": "Web"}, {"value": "Phone"}]})]
    if many:
        kinds += [(f"extra-{i}", {}) for i in range(43)]
    for i, (key, extra) in enumerate(kinds):
        kind = "text" if key.startswith("extra-") else key
        fields[key] = _command(env.browser, env.site, "custom-field.create", {
            "name": f"Field {key}", "kind": kind, "position": i, "scopes": ["journal_entry"], **extra,
        })
    return fields


@pytest.mark.parametrize("width", [1280, 390])
def test_keyboard_all_kinds_48_fields_splits_restore_clear(register_browser, width):
    env, b = register_browser, register_browser.browser
    fields = _definitions(env, many=True)
    b.viewport(width, 900 if width == 1280 else 844)
    b.navigate(env.url)
    b.wait_for("document.querySelectorAll('[data-custom-field]').length >= 48")
    _simple_draft(env, "12.00")
    _tab(b, "#register-splits-open"); _key(b, "Enter")
    b.wait_for("!document.querySelector('#register-splits').hidden")
    _key(b, "Escape")
    # An explicit empty text value is distinct from Keep; native select keyboard.
    _tab(b, f'[data-custom-action="{fields["text"]["id"]}"]'); _key(b, "ArrowDown")
    _tab(b, f'[data-custom-field="{fields["bool"]["id"]}"]')
    _key(b, "ArrowDown"); _key(b, "ArrowDown")
    _tab(b, f'[data-custom-field="{fields["number"]["id"]}"]'); _type(b, "0")
    _tab(b, f'[data-custom-field="{fields["choice"]["id"]}"]'); _key(b, "ArrowDown")
    # Date's native date control is checked separately from CDP text insertion.
    b.evaluate(f"""(() => {{const e=document.querySelector('[data-custom-field="{fields['date']['id']}"]');
      if(e.type !== 'date') throw Error('date control missing'); e.value='2026-02-12'; e.dispatchEvent(new Event('input',{{bubbles:true}}));}})()""")
    _tab(b, f'[data-custom-field="{fields["extra-42"]["id"]}"]'); _type(b, "last reachable")
    assert b.evaluate("document.documentElement.scrollWidth <= innerWidth")
    journal = _save(b)
    stored = _command(b, env.site, "journal.show", {"journal": journal})
    values = {f["definition_id"]: f["value"] for f in stored["revision"]["custom_fields"]}
    assert values[fields["text"]["id"]] == ""
    assert values[fields["bool"]["id"]] is False
    assert values[fields["number"]["id"]] == "0"
    assert values[fields["date"]["id"]] == "2026-02-12"
    assert values[fields["choice"]["id"]] == "Web"
    assert values[fields["extra-42"]["id"]] == "last reachable"
    assert b.evaluate(f"document.querySelector('[data-custom-field=\"{fields['extra-42']['id']}\"]').value") == ""
    b.navigate(env.url + "?edit=" + journal)
    b.wait_for("document.querySelectorAll('[data-custom-field]').length >= 48")
    number_selector = f'[data-custom-field="{fields["number"]["id"]}"]'
    _tab(b, number_selector); _type(b, "7")
    _tab(b, "#register-restore"); _key(b, "Enter")
    assert b.evaluate(f"document.querySelector({json.dumps(number_selector)}).value") == "0"
    _tab(b, f'[data-custom-action="{fields["bool"]["id"]}"]')
    _key(b, "ArrowDown"); _key(b, "ArrowDown")
    _save(b)
    updated = _command(b, env.site, "journal.show", {"journal": journal})
    assert fields["bool"]["id"] not in updated["revision"]["custom_fields_snapshot"]
    assert updated["revision"]["custom_fields_snapshot"][fields["text"]["id"]] == stored["revision"]["custom_fields_snapshot"][fields["text"]["id"]]


def test_uncertain_reload_after_definition_change_retains_exact_wire(register_browser):
    env, b = register_browser, register_browser.browser
    fields = _definitions(env)
    b.navigate(env.url)
    b.wait_for("document.querySelectorAll('[data-custom-field]').length >= 5")
    _simple_draft(env, "3.00")
    _tab(b, f'[data-custom-field="{fields["text"]["id"]}"]'); _type(b, "private retained")
    _tab(b, f'[data-custom-field="{fields["bool"]["id"]}"]'); _key(b, "ArrowDown"); _key(b, "ArrowDown")
    _tab(b, f'[data-custom-field="{fields["choice"]["id"]}"]'); _key(b, "ArrowDown")
    b.evaluate("""window.actualFetch=window.fetch; window.fetch=async (...args)=>{
      const result=await window.actualFetch(...args);
      if(String(args[0]).endsWith('/commands/register.post')) throw new TypeError('dropped after commit');
      return result;
    };""")
    _tab(b, "#register-record"); _key(b, "Enter")
    b.wait_for("document.querySelector('#register-error').textContent.includes('TRANSPORT')")
    before = b.evaluate("JSON.parse(sessionStorage.getItem('bookflow-register-pending-v1'))")
    _command(b, env.site, "custom-field.deactivate", {"custom_field": fields["text"]["id"], "expected_version": fields["text"]["version"]})
    _command(b, env.site, "custom-field.update", {"custom_field": fields["choice"]["id"], "expected_version": fields["choice"]["version"],
        "choices": [{"id": c["id"], "value": "WEB" if c["value"] == "Web" else c["value"]} for c in fields["choice"]["choices"]]})
    b.navigate(env.url)
    b.wait_for("!document.querySelector('#register-retry').hidden")
    after = b.evaluate("JSON.parse(sessionStorage.getItem('bookflow-register-pending-v1'))")
    assert before["wire"] == after["wire"] and before["key"] == after["key"]
    assert b.evaluate(f"document.querySelector('[data-custom-field=\"{fields['choice']['id']}\"]').selectedOptions[0].textContent") == "WEB"
    assert after['payload']['custom_fields'][fields['choice']['id']] == 'Web'
    assert b.evaluate(f"document.querySelector('[data-custom-field=\"{fields['text']['id']}\"]').value") == "private retained"
    assert b.evaluate("document.querySelector('#register-fields').disabled")
    _tab(b, "#register-retry"); _key(b, "Enter")
    b.wait_for("!document.querySelector('#register-receipt').hidden")
    assert b.evaluate("sessionStorage.getItem('bookflow-register-pending-v1')") is None
    assert "private retained" not in b.evaluate("document.querySelector('#register-custom-fields').textContent")


@pytest.mark.parametrize("noun", ["journal", "register"])
def test_generated_preview_error_retains_attempts_after_inventory_changes(register_browser, noun):
    env, b = register_browser, register_browser.browser
    fields = _definitions(env)
    b.viewport(390, 844)
    b.navigate(f"{env.site.base_url}/c/{env.site.company_id}/{noun}/post")
    text_id, choice_id = fields["text"]["id"], fields["choice"]["id"]
    b.wait_for(f"!!document.querySelector('[name=\"cf:{text_id}\"]')")
    _tab(b, f'[name="cf:{text_id}"]'); _type(b, "attempt survives <escaped>")
    _tab(b, f'[name="cf:{choice_id}"]'); _key(b, "ArrowDown")
    _command(b, env.site, "custom-field.deactivate", {"custom_field": text_id, "expected_version": fields["text"]["version"]})
    _command(b, env.site, "custom-field.update", {"custom_field": choice_id, "expected_version": fields["choice"]["version"],
        "choices": [{"id": c["id"], "value": c["value"], "active": c["value"] != "Web"} for c in fields["choice"]["choices"]]})
    for _ in range(2):
        _tab(b, 'button[value="preview"]'); _key(b, "Enter")
        b.wait_for("document.body.textContent.includes('E_VALIDATION')")
        assert b.evaluate(f"document.querySelector('[name=\"cf:{text_id}\"]').value") == "attempt survives <escaped>"
        assert b.evaluate(f"document.querySelector('[name=\"cf:{choice_id}\"]').value") == "Web"
        assert "unavailable choice" in b.evaluate(f"document.querySelector('[name=\"cf:{choice_id}\"]').selectedOptions[0].textContent")
        assert b.evaluate(f"document.querySelector('[name=\"cf-state:{text_id}\"]').value") == "set"
    assert b.evaluate("document.documentElement.scrollWidth <= innerWidth")


@pytest.mark.parametrize("noun", ["journal", "register"])
def test_successful_generated_preview_then_definition_change(register_browser, noun):
    env, b = register_browser, register_browser.browser
    fields = _definitions(env, many=True)
    journal = _command(b, env.site, 'register.post', {
        'account': env.bank['id'], 'category': env.expense['id'], 'date': '2026-02-12',
        'direction': 'decrease', 'amount': '1.00',
    })
    b.navigate(f"{env.site.base_url}/c/{env.site.company_id}/{noun}/{journal['id']}/update")
    b.wait_for("document.querySelectorAll('[name^=\"cf:\"]').length >= 48")
    identifier = fields['text']['id']
    _tab(b, f'[name="cf:{identifier}"]'); _type(b, 'preview attempt')
    _tab(b, 'button[value="preview"]'); _key(b, 'Enter')
    b.wait_for("document.body.textContent.includes('Preview (nothing written)')")
    # Authoritative show proves a preview has not written the attempted value.
    shown = _command(b, env.site, 'journal.show', {'journal': journal['id']})
    assert identifier not in shown['revision']['custom_fields_snapshot']
    assert b.evaluate(f"document.querySelector('[name=\"cf:{identifier}\"]').value") == 'preview attempt'
    _command(b, env.site, 'custom-field.deactivate', {'custom_field': identifier, 'expected_version': fields['text']['version']})
    _tab(b, 'button[value="preview"]'); _key(b, 'Enter')
    b.wait_for("document.body.textContent.includes('E_INACTIVE_REFERENCE')")
    assert b.evaluate(f"document.querySelector('[name=\"cf-state:{identifier}\"]').value") == 'set'
    assert b.evaluate(f"document.querySelector('[name=\"cf:{identifier}\"]').value") == 'preview attempt'


def test_readonly_chrome_captured_inactive_values_and_forged_write(register_browser):
    import os
    from pathlib import Path
    from tests.conftest import make_actor
    from tests.test_row5_browser_acceptance import PASSWORD

    env, b = register_browser, register_browser.browser
    fields = _definitions(env)
    text = '<img src=x onerror=alert(1)> captured'
    journal = _command(b, env.site, 'register.post', {
        'account': env.bank['id'], 'category': env.expense['id'], 'date': '2026-02-12',
        'direction': 'decrease', 'amount': '1.00', 'custom_fields': {fields['text']['id']: text, fields['bool']['id']: False},
    })
    _command(b, env.site, 'custom-field.deactivate', {'custom_field': fields['text']['id'], 'expected_version': fields['text']['version']})
    b.navigate(env.url + '?edit=' + journal['id'])
    b.wait_for("document.querySelector('#register-custom-fields').textContent.includes('captured')")
    assert not b.evaluate(f"!!document.querySelector('[data-custom-field=\"{fields['text']['id']}\"]')")
    assert text in b.evaluate("document.querySelector('#register-custom-fields').textContent")
    assert not b.evaluate("!!document.querySelector('#register-custom-fields img')")
    data_root = Path(os.environ['BOOKFLOW_DATA_ROOT'])
    make_actor(data_root, 'cf-reader', company_role=(env.site.company_id, 'readonly'))
    setup = b.evaluate(f"""fetch('/commands/user.set-password', {{method:'POST',
      headers:{{'Content-Type':'application/json','X-Bookflow-Workbench':'1'}},
      body:JSON.stringify({{username:'cf-reader',password:{json.dumps(PASSWORD)}}})}}).then(async r=>({{status:r.status,body:await r.json()}}))""", await_promise=True)
    assert setup['status'] == 200, setup
    b.evaluate("document.querySelector('form[action=\"/logout\"]').requestSubmit()")
    b.wait_for("!!document.querySelector('[name=username]')")
    b.evaluate(f"""(() => {{document.querySelector('[name=username]').value='cf-reader';
      document.querySelector('[name=password]').value={json.dumps(PASSWORD)};
      document.querySelector('form[hx-post="/login"]').requestSubmit();}})()""")
    b.wait_for("!!document.querySelector('.group-grid')")
    b.navigate(env.url)
    b.wait_for("!!document.querySelector('#register-config')")
    assert not b.evaluate("!!document.querySelector('#register-form')")
    b.navigate(f"{env.site.base_url}/c/{env.site.company_id}/journal/{journal['id']}")
    b.wait_for("document.body.textContent.includes('captured')")
    assert text in b.evaluate('document.body.textContent')
    assert not b.evaluate("!!document.querySelector('.workspace-card img')")
    response = b.evaluate(f"""fetch('/companies/{env.site.company_id}/commands/register.post', {{method:'POST',
      headers:{{'Content-Type':'application/json','X-Bookflow-Workbench':'1'}},
      body:JSON.stringify({{account:{json.dumps(env.bank['id'])},category:{json.dumps(env.expense['id'])},date:'2026-02-12',
      direction:'decrease',amount:'1.00',custom_fields:{{{json.dumps(fields['bool']['id'])}:false}}}})}}).then(async r=>({{status:r.status,body:await r.json()}}))""", await_promise=True)
    assert response['status'] == 403, response
