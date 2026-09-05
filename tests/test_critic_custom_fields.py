"""Independent rejection witnesses: intentionally red if a candidate reproduces."""
import base64
import json
from pathlib import Path

import pytest

from bookflow import BookflowError
from bookflow.company import journals, journal_custom_fields as custom
from bookflow.company.custom_fields import CustomFieldValuePatch
from tests.test_row8_journal import COMPANY, journal_accounts
from tests.test_row8_custom_field_integration import definition, post, complete_state
from tests.test_row5_browser_acceptance import browser_site, CHROME
from tests.test_row8_register_browser import register_browser, _command, _simple_draft, _key, _type
from tests.test_row8_custom_field_browser import _tab

EVIDENCE = None


@pytest.fixture(autouse=True)
def _evidence_path(tmp_path, monkeypatch):
    monkeypatch.setitem(globals(), "EVIDENCE", tmp_path)


def evidence(name, value):
    (EVIDENCE / (name + '.json')).write_text(json.dumps(value, indent=2, default=str))


@pytest.mark.parametrize('damage', ['creating', 'patch'])
def test_corrupt_plan_cannot_rewrite_authorized_custom_intent(client, journal_accounts, monkeypatch, damage):
    field = definition(client, 'Critic guarded required field', required=True, default='default')
    before = complete_state(client)
    original = journals.prepare

    def corrupt(s, ctx, inp, operation):
        result = original(s, ctx, inp, operation)
        if s.company.write_transaction:
            data = result.data
            forged = custom.prepare(s.company, data['header']['id'],
                CustomFieldValuePatch({} if damage == 'creating' else {field['id']: 'UNREQUESTED'}),
                {}, creating=damage != 'creating')
            data['custom_plan'] = forged
            data['pending']['transaction_revisions'][0]['custom_fields_snapshot'] = json.dumps(forged.snapshot)
        return result

    failure = None
    with monkeypatch.context() as m:
        m.setattr(journals, 'prepare', corrupt)
        try:
            saved = post(client, journal_accounts, custom_fields={field['id']: 'AUTHORIZED'},
                         idempotency_key='critic-seam-' + damage)
        except BookflowError as exc:
            failure = exc.code
    after = complete_state(client)
    observed = dict(damage=damage, error=failure, unchanged=after == before)
    if failure is None:
        observed['receipt'] = saved
        observed['shown'] = client.journal.show(journal=saved['id'], company=COMPANY)
    evidence('candidate-plan-' + damage, observed)
    assert failure == 'E_INTERNAL' and after == before, observed


@pytest.mark.skipif(not CHROME.exists(), reason='Chrome is unavailable')
@pytest.mark.parametrize('width', [1280, 390])
@pytest.mark.parametrize('surface', ['journal', 'register', 'dedicated'])
def test_draft_kind_drift_requires_explicit_user_correction(register_browser, width, surface):
    env, b = register_browser, register_browser.browser
    b.viewport(width, 900 if width == 1280 else 844)
    field = _command(b, env.site, 'custom-field.create', {
        'name': 'Critic literal job code', 'kind': 'text', 'scopes': ['journal_entry']})
    journal = _command(b, env.site, 'register.post', {
        'account': env.bank['id'], 'category': env.expense['id'], 'date': '2026-02-12',
        'direction': 'decrease', 'amount': '1.00'})
    if surface == 'dedicated':
        b.navigate(env.url + '?edit=' + journal['id'])
        selector = '[data-custom-field="' + field['id'] + '"]'
    else:
        b.navigate(f'{env.site.base_url}/c/{env.site.company_id}/{surface}/{journal["id"]}/update')
        selector = '[name="cf:' + field['id'] + '"]'
    b.wait_for('!!document.querySelector(' + json.dumps(selector) + ')')
    b.evaluate("window.criticSettled=0; document.body.addEventListener('htmx:afterSettle',()=>window.criticSettled++)")
    _tab(b, selector); _type(b, '001')
    if surface != 'dedicated':
        settled = b.evaluate('window.criticSettled')
        _tab(b, 'button[value="preview"]'); _key(b, 'Enter')
        b.wait_for('window.criticSettled > ' + str(settled))
        b.wait_for("document.body.textContent.includes('Preview (nothing written)')")
    _command(b, env.site, 'custom-field.update', {
        'custom_field': field['id'], 'expected_version': field['version'], 'kind': 'number'})
    if surface != 'dedicated':
        settled = b.evaluate('window.criticSettled')
        _tab(b, 'button[value="preview"]'); _key(b, 'Enter')
        b.wait_for('window.criticSettled > ' + str(settled))
        b.wait_for("document.body.textContent.includes('Preview (nothing written)') || document.body.textContent.includes('E_VALIDATION')")
    name = f'candidate-kind-{surface}-{width}'
    before_submit = b.evaluate('document.body.innerText')
    (EVIDENCE / (name + '.png')).write_bytes(base64.b64decode(b.call('Page.captureScreenshot', {'format': 'png'})['data']))
    if surface == 'dedicated':
        _tab(b, '#register-record'); _key(b, 'Enter')
        b.wait_for("!document.querySelector('#register-receipt').hidden || !!document.querySelector('#register-error').textContent")
    else:
        settled = b.evaluate('window.criticSettled')
        _tab(b, 'button[value="submit"]'); _key(b, 'Enter')
        b.wait_for('window.criticSettled > ' + str(settled))
        b.wait_for("!document.querySelector('[data-generated-form]') || document.body.textContent.includes('E_VALIDATION')")
    shown = _command(b, env.site, 'journal.show', {'journal': journal['id']})
    evidence(name, dict(before_submit=before_submit, shown=shown,
        after_submit=b.evaluate('document.body.innerText')))
    assert shown['version'] == 1 and field['id'] not in shown['revision']['custom_fields_snapshot'], shown

    # Explicitly adopting the new type is a deliberate correction; the rejected
    # attempt stays intact until this action and can then be saved normally.
    if surface == 'dedicated':
        assert b.evaluate('document.querySelector(' + json.dumps(selector) + ').value') == '001'
        _tab(b, '[data-custom-adopt="' + field['id'] + '"]'); _key(b, 'Enter')
        _tab(b, '#register-record'); _key(b, 'Enter')
        b.wait_for("!document.querySelector('#register-receipt').hidden")
    else:
        assert b.evaluate('document.querySelector(' + json.dumps(selector) + ').value') == '001'
        settled = b.evaluate('window.criticSettled')
        _tab(b, '[data-custom-adopt="' + field['id'] + '"]'); _key(b, 'Enter')
        b.wait_for('window.criticSettled > ' + str(settled))
        b.wait_for('document.querySelector(' + json.dumps('[name="cf-kind:' + field['id'] + '"]') + ')?.value === "number" && document.body.textContent.includes("Preview (nothing written)")')
        assert _command(b, env.site, 'journal.show', {'journal': journal['id']})['version'] == 1
        _tab(b, 'button[value="submit"]'); _key(b, 'Enter')
        b.wait_for("!document.querySelector('[data-generated-form]')")
    shown = _command(b, env.site, 'journal.show', {'journal': journal['id']})
    assert shown['version'] == 2
    assert shown['revision']['custom_fields_snapshot'][field['id']]['kind'] == 'number'
    assert shown['revision']['custom_fields_snapshot'][field['id']]['value'] == '1'
    assert _command(b, env.site, 'account.show', {'account': env.bank['id']})['balance']['minor_units'] == -100
