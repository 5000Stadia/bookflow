"""Sale effects remain typed, inspectable history and never register edits."""
import pytest

from bookflow import BookflowError
from bookflow.adapters.workbench.register import edit_projection
from tests.test_row8_register_browser import register_browser, _command  # noqa: F401
from tests.test_row5_browser_acceptance import CHROME, browser_site  # noqa: F401

COMPANY = 'Demo Plumbing Co'


def _create(run, document_type, bank=None):
    income = run('account create', {'name': 'Navigation income', 'type': 'income'})['id']
    customer = run('customer create', {'name': 'Navigation customer'})['id']
    code = next(row['id'] for row in run('sales-tax-code list', {})['items'] if not row['taxable'])
    item = run('item create', dict(name='Navigation service', type='service', sales_enabled=True,
        description='Navigation service work', income_account_id=income, price='10.00', sales_tax_code_id=code))['id']
    extra = {}
    if document_type == 'sales_receipt':
        bank = bank or run('account create', {'name': 'Navigation bank', 'type': 'bank'})['id']
        method = run('payment-method create', {'name': 'Navigation cash', 'kind': 'cash'})['id']
        extra = dict(deposit_to=bank, payment_method=method)
    noun = document_type.replace('_', '-')
    sale = run(noun + ' post', dict(date='2026-01-12', customer=customer,
        memo='Original sale', lines=[dict(item=item)], **extra))
    control = sale['revision']['profile']['control_account']['id']
    return sale, control, income


@pytest.mark.parametrize('document_type', ['invoice', 'sales_receipt'])
def test_sales_register_all_effects_and_journal_fences(client, document_type):
    run = lambda name, data, **kw: client.run(name, data, company=COMPANY, **kw)
    sale, control, income = _create(run, document_type)
    noun = document_type.replace('_', '-')
    changed = run(noun + ' update', {document_type: sale['id'], 'expected_version': 1,
        'date': '2026-02-01', 'memo': 'Corrected sale'})
    for account in (control, income):
        current = run('register query', dict(account=account, date_from='2026-01-01', date_to='2026-12-31'))
        assert current['current_balance']['balance']['minor_units'] == 1000
    run(noun + ' void', {document_type: sale['id'], 'expected_version': changed['version']}, reason='Navigation witness')
    for account in (control, income):
        for command in ('register query', 'report general-ledger'):
            rows, cursor = [], None
            while True:
                page = run(command, dict(account=account, date_from='2026-01-01',
                    date_to='2026-12-31', limit=2, **({'cursor': cursor} if cursor else {})))
                rows.extend(page['rows'])
                cursor = page['next_cursor']
                if not cursor:
                    break
            effects = [r for r in rows if r['kind'] == 'posting']
            assert len(effects) == 4
            assert {r['transaction_type'] for r in effects} == {document_type}
            assert {r['transaction_id'] for r in effects} == {sale['id']}
            assert sorted(r['batch_kind'] for r in effects) == ['original', 'replacement', 'reversal', 'reversal']
            assert {r['transaction_type'] for r in rows if r['kind'] != 'posting'} == {None}
            if command == 'register query':
                assert page['current_balance']['balance']['minor_units'] == 0
                assert {r['category_label'] for r in effects} == {'Invoice' if document_type == 'invoice' else 'Sales receipt'}
                assert {(r['revision_number'], r['memo']) for r in effects} == {(1, 'Original sale'), (2, 'Corrected sale')}
    assert edit_projection(sale, {'id': control}, lambda r: r['name']) is None
    for command, data in [
        ('journal show', {'journal': sale['id']}),
        ('journal update', {'journal': sale['id'], 'memo': 'Wrong editor'}),
        ('journal void', {'journal': sale['id']}),
        ('register update', dict(journal=sale['id'], account=control, date='2026-02-01',
            expected_version=changed['version'] + 1, selected_line_id=sale['revision']['lines'][0]['line_id'],
            direction='increase', amount='10.00', category=income)),
    ]:
        with pytest.raises(BookflowError) as error:
            run(command, data, reason='Type fence witness')
        assert error.value.code == 'E_RECORD_NOT_FOUND', (command, error.value.details)


@pytest.mark.skipif(not CHROME.exists(), reason='Chrome unavailable')
@pytest.mark.parametrize('document_type', ['invoice', 'sales_receipt'])
def test_browser_sale_history_links_and_no_journal_actions(register_browser, document_type):
    env = register_browser
    run = lambda name, data: _command(env.browser, env.site, name.replace(' ', '.'), data)
    sale, control, income = _create(run, document_type, env.bank['id'])
    noun = document_type.replace('_', '-')
    run(noun + ' update', {document_type: sale['id'], 'expected_version': 1, 'memo': 'Corrected sale'})
    path = f'/c/{env.site.company_id}/account/{control}/register'
    env.browser.navigate(env.site.base_url + path + '?date_from=2026-01-01&date_to=2026-12-31')
    env.browser.wait_for("document.querySelectorAll('#register-history tr[data-kind=posting]').length === 3")
    rows = env.browser.evaluate("""Array.from(document.querySelectorAll('#register-history tr[data-kind=posting]')).map(r => ({
        text: r.textContent, links: Array.from(r.querySelectorAll('a')).map(a => ({text:a.textContent, href:a.getAttribute('href')}))}))""")
    assert all(('Invoice' if document_type == 'invoice' else 'Sales receipt') in r['text'] for r in rows)
    assert all([a['text'] for a in r['links']] == ['History'] for r in rows)
    targets = [r['links'][0]['href'] for r in rows]
    assert set(targets) == {f'/c/{env.site.company_id}/{noun}/{sale["id"]}?revision_number={n}' for n in (1, 2)}
    # Check the route actually passes the historical revision to the show command.
    target = next(t for t in targets if t.endswith('=1'))
    env.browser.navigate(env.site.base_url + target)
    env.browser.wait_for("document.body.textContent.includes('Original sale')")
    assert 'Corrected sale' not in env.browser.evaluate('document.body.textContent')
    env.browser.navigate(env.site.base_url + path + '?edit=' + sale['id'])
    env.browser.wait_for("document.body.textContent.includes('E_RECORD_NOT_FOUND')")
    assert not env.browser.evaluate("!!document.querySelector('#register-form')")
