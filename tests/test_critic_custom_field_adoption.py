import json
import base64
from pathlib import Path

import pytest
from tests.test_row5_browser_acceptance import browser_site, CHROME

pytestmark = pytest.mark.skipif(not CHROME.exists(), reason="Chrome is unavailable")
from tests.test_row8_register_browser import register_browser, _command, _key
from tests.test_row8_custom_field_browser import _tab


@pytest.mark.parametrize('surface', ['dedicated', 'journal', 'register'])
@pytest.mark.parametrize('old_kind,new_kind,old_value,expected', [('bool', 'text', False, 'false'), ('text', 'bool', 'false', False)])
def test_explicit_adoption_preserves_displayed_value_and_new_wire_type(register_browser, surface, old_kind, new_kind, old_value, expected, tmp_path):
    env, b = register_browser, register_browser.browser
    b.viewport(390, 844)
    field = _command(b, env.site, 'custom-field.create', dict(name='Critic adopt boolean text', kind=old_kind, scopes=['journal_entry']))
    journal = _command(b, env.site, 'register.post', dict(account=env.bank['id'], category=env.expense['id'],
        date='2026-02-12', direction='decrease', amount='1.00'))
    if surface == 'dedicated':
        route = env.url + '?edit=' + journal['id']
        selector = '[data-custom-field="' + field['id'] + '"]'
    else:
        route = f'{env.site.base_url}/c/{env.site.company_id}/{surface}/{journal["id"]}/update'
        selector = '[name="cf:' + field['id'] + '"]'
    b.navigate(route)
    b.wait_for('!!document.querySelector(' + json.dumps(selector) + ')')
    b.evaluate('document.querySelector(' + json.dumps(selector) + ').value="false"; document.querySelector(' + json.dumps(selector) + ').dispatchEvent(new Event("input",{bubbles:true}))')
    b.evaluate("window.criticSettled=0; document.body.addEventListener('htmx:afterSettle',()=>window.criticSettled++)")
    b.evaluate("document.body.addEventListener('htmx:configRequest',e=>{const a=JSON.parse(sessionStorage.getItem('critic-requests')||'[]');a.push(JSON.parse(JSON.stringify(e.detail.parameters)));sessionStorage.setItem('critic-requests',JSON.stringify(a))})")
    _command(b, env.site, 'custom-field.update', dict(custom_field=field['id'], expected_version=1, kind=new_kind))
    if surface == 'dedicated':
        _tab(b, '#register-record'); _key(b, 'Enter')
        b.wait_for("document.querySelector('#register-error').textContent.includes('E_VALIDATION')")
    else:
        _tab(b, 'button[value="preview"]'); _key(b, 'Enter')
        b.wait_for('window.criticSettled > 0')
        assert 'E_VALIDATION' in b.evaluate('document.body.innerText')
    adoption = '[data-custom-adopt="' + field['id'] + '"]'
    _tab(b, adoption); _key(b, 'Enter')
    if surface != 'dedicated':
        b.wait_for("!document.querySelector('[data-generated-form]') || window.criticSettled > 1")
        adopted = _command(b, env.site, 'journal.show', {'journal': journal['id']})
        area = tmp_path
        (area / f'adopt-preview-{surface}-{old_kind}-{new_kind}.json').write_text(json.dumps(dict(shown=adopted,
            requests=b.evaluate("JSON.parse(sessionStorage.getItem('critic-requests')||'[]')"), body=b.evaluate('document.body.innerText')), indent=2))
        assert adopted['version'] == 1, 'Use current type and preview wrote the journal before Save'
    if surface == 'dedicated':
        _tab(b, '#register-record'); _key(b, 'Enter')
        b.wait_for("!document.querySelector('#register-pending').hidden || !!document.querySelector('#register-error').textContent || !document.querySelector('#register-receipt').hidden")
        b.wait_for("!document.querySelector('#register-fields').disabled")
    else:
        _tab(b, 'button[value="submit"]'); _key(b, 'Enter')
        # A top-level Save redirect briefly has no form and no body. Wait for
        # the authoritative detail (or a rendered error), not that empty frame.
        b.wait_for("document.readyState === 'complete' && !!document.querySelector('main') && ((!document.querySelector('[data-generated-form]') && !!document.querySelector('.ledger-table')) || !!document.querySelector('.error'))")
    shown = _command(b, env.site, 'journal.show', {'journal': journal['id']})
    name = f'adopt-{surface}-{old_kind}-{new_kind}'
    area = tmp_path
    (area / (name + '.json')).write_text(json.dumps(dict(shown=shown, body=b.evaluate('document.body.innerText')), indent=2))
    (area / (name + '.png')).write_bytes(base64.b64decode(b.call('Page.captureScreenshot', {'format':'png'})['data']))
    assert shown['version'] == 2, shown
    value = shown['revision']['custom_fields_snapshot'][field['id']]['value']
    assert type(value) is type(expected) and value == expected
