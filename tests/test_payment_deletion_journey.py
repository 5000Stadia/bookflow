"""The person-visible half of payment deletion: the grant, the journey, retained reads."""
import base64
import json
from pathlib import Path

import pytest

from bookflow.adapters.workbench.permissions import FIELDS
from bookflow.core.deletion_families import capability
from tests.test_bill_item_lines import books
from tests.test_purchase_deletion_http import office
from tests.test_row5_browser_acceptance import CHROME, browser_site
from tests.test_row8_register_browser import register_browser, _command
from tests.test_service_sales_browser import _contained
from tests.test_customer_payment_browser import recorded_state, wait

PAYMENT_DELETE = capability('payment')


def activate(office):
    state = office.admin('permission.show')
    office.admin('permission.activate', dict(expected_generation=state['generation'],
                                             expected_catalog_sha256=state['catalog_sha256']))


def member(office, company, user=None):
    rows = office.admin('membership.list', dict(company=company))['items']
    return next(row for row in rows if row['scope_type'] == 'company' and row['scope_id'] == company
                and (user is None or row['user_id'] == user))


def unapplied_receipt(office, books):
    """A receipt whose application has been released, so deletion is admissible.

    Everything is written over the running host: the in-process client and the host
    contend for the same data-root lock, and only one of them may hold it.
    """
    company = books['company']
    def admin(name, raw, reason):
        return office.admin(name, raw, company=company, headers={'X-Bookflow-Reason': reason})
    exempt = next(row['id'] for row in office.admin('sales-tax-code.list', {}, company=company)['items']
                  if not row['taxable'])
    item = admin('item.create', dict(name='Payment journey service', type='service', sales_enabled=True,
        description='Payment journey service', sales_tax_code_id=exempt,
        income_account_id=books['income'], price='25'), 'Sell this service')['id']
    bill = admin('invoice.post', dict(customer=books['customer'], date='2017-01-01',
        lines=[dict(item=item, quantity='1', unit_price='25.00')]), 'Bill the customer')
    post = admin('payment.receive', dict(customer=books['customer'], date='2017-01-02', amount='25.00',
        payment_method=books['methods']['Cash'], operation_key='journey-receive', deposit_to=books['bank'],
        applications=dict(mode='inline', items=[dict(invoice=bill['id'],
            expected_version=bill['version'], amount='25.00')])), 'Customer paid')
    application = post['effect']['applications'][0]['application_id']
    settled = office.admin('invoice.settlement', dict(invoice=bill['id']), company=company)
    admin('payment.unapply', dict(payment=post['id'], expected_version=post['version'],
        operation_key='release', applications=[dict(application_id=application,
            invoice_expected_version=settled['version'])]), 'Release the application')
    return post['id'], application


def test_retained_application_history_and_payment_links_survive_deletion(books, office):
    """Unapply, delete, then read the retained application's history through the workbench."""
    company = books['company']
    payment, application = unapplied_receipt(office, books)
    activate(office)
    person = office.admin('user.add', dict(username='payment-deleter', password='deleter fixture',
                                           company=company, role='standard'))
    office.admin('membership.grant', dict(user=person['user_id'], company=company, expected_version=1,
        grants=[PAYMENT_DELETE], denies=['ledger.post']))
    clerk = office.login_as('payment-deleter', 'deleter fixture')
    current = office.admin('payment.show', dict(payment=payment), company=company)
    # Deleting has its own authority: this clerk may not post at all.
    denied = office.call(clerk, 'payment.void', dict(payment=payment, expected_version=current['version'],
        operation_key='journey-denied-void'), company=company, headers={'X-Bookflow-Reason': 'Posting denied'})
    assert denied.json()['code'] == 'E_PERMISSION', denied.text
    deleted = office.ok(clerk, 'payment.delete', dict(payment=payment, expected_version=current['version'],
        operation_key='journey-delete'), company=company,
        headers={'X-Bookflow-Reason': 'Remove duplicate receipt'})
    assert deleted['status'] == 'deleted' and deleted['from_status'] == 'posted'

    # The owning read still hides a deleted receipt from any caller that has not
    # asked for it. Every page below reads one that has been deleted.
    hidden = office.call(office.installer, 'payment.show', dict(payment=payment), company=company)
    assert hidden.json()['code'] == 'E_RECORD_NOT_FOUND', hidden.text

    history = office.installer.get(f'/c/{company}/application/{application}/history')
    assert history.status_code == 200, history.text
    assert 'Remove duplicate receipt' in history.text
    assert 'aria-label="Deleted payment"' in history.text

    workspace = clerk.get(f'/c/{company}/receive-payments?payment={payment}')
    assert workspace.status_code == 200, workspace.text
    config = json.loads(workspace.text.split('id="payment-config">', 1)[1].split('</script>', 1)[0])
    assert config['initial']['status'] == 'deleted'
    assert config['initial']['deletion']['reason'] == 'Remove duplicate receipt'
    assert 'delete' in config['allowed']
    # Without the family grant the workspace offers no Delete journey.
    assert 'delete' not in json.loads(office.installer.get(
        f'/c/{company}/receive-payments?payment={payment}').text.split(
        'id="payment-config">', 1)[1].split('</script>', 1)[0])['allowed']

    settlement = office.installer.get(f'/c/{company}/application/{application}')
    assert settlement.status_code == 200, settlement.text
    # Current settlement names the receipt a person is actually looking at. The
    # `application show` accounting statuses are unchanged; the page shows the
    # deletion it has already loaded instead of calling a deleted receipt voided.
    assert '; payment deleted.' in settlement.text and 'payment voided' not in settlement.text

    # A saved editing link to a deleted receipt opens its retained history, not a form.
    asked = json.loads(clerk.get(f'/c/{company}/receive-payments?payment={payment}&mode=update').text.split(
        'id="payment-config">', 1)[1].split('</script>', 1)[0])
    assert asked['mode'] == 'show'
    # The record page the workspace links to reads the retained receipt.
    detail = office.installer.get(f'/c/{company}/payment/{payment}?include_deleted=1')
    assert detail.status_code == 200, detail.text


def test_company_setup_grants_and_displays_payment_deletion(books, office):
    company = books['company']
    activate(office)
    clerk = office.admin('user.add', dict(username='payment-clerk', password='clerk fixture',
                                          company=company, role='standard'))
    page = office.installer.get(f'/c/{company}/users?user={clerk["user_id"]}')
    assert page.status_code == 200, page.text
    assert 'name="payment_delete"' in page.text
    assert 'Grant customer payment deletion' in page.text
    assert f'{PAYMENT_DELETE}: Denied' in page.text

    saved = office.installer.post(f'/c/{company}/users', data=dict(user=clerk['user_id'], role='standard',
        expected_version='1', other_grants='[]', other_denies='[]', payment_delete='on', allow_read='on',
        action='save', reason='Allow this clerk to delete payments'),
        headers={'X-Bookflow-Workbench': '1'})
    assert saved.status_code == 200, saved.text
    granted = member(office, company, clerk['user_id'])
    assert PAYMENT_DELETE in granted['grants'] and 'ledger.post' in granted['denies']

    shown = office.installer.get(f'/c/{company}/users?user={clerk["user_id"]}')
    assert f'{PAYMENT_DELETE}: Allowed' in shown.text
    # Every family that has retained-deletion storage is grantable here, and no other.
    assert [field for field in FIELDS if f'name="{field}"' in shown.text] == list(FIELDS)
    assert shown.text.count('_delete"') == len(FIELDS)


@pytest.mark.timeout(300)
@pytest.mark.skipif(not CHROME.exists(), reason='Chrome unavailable')
@pytest.mark.parametrize('width', [1280, 390])
def test_payment_delete_journey_in_real_chrome(register_browser, width, tmp_path):
    env, b = register_browser, register_browser.browser
    site = env.site
    run = lambda name, data, **headers: _command(b, site, name.replace(' ', '.'), data, **headers)

    def hub(name, data=None):
        result = b.evaluate("fetch('/commands/" + name + "',{method:'POST',credentials:'same-origin',"
            "headers:{'Content-Type':'application/json','X-Bookflow-Workbench':'1'},body:JSON.stringify("
            + json.dumps(data or {}) + ")}).then(async r=>({status:r.status,body:await r.json()}))",
            await_promise=True)
        assert result['status'] == 200, result
        return result['body']

    b.viewport(width, 900)
    income = run('account create', dict(name='Delete journey income', type='income'))['id']
    payer = run('customer create', dict(name='Delete Journey Customer'))['id']
    method = run('payment-method create', dict(name='Delete journey cheque', kind='check'))['id']
    code = next(row['id'] for row in run('sales-tax-code list', {})['items'] if not row['taxable'])
    item = run('item create', dict(name='Delete journey labor', type='service', sales_enabled=True,
        income_account_id=income, price='100', description='Work completed', sales_tax_code_id=code))['id']
    bill = run('invoice post', dict(customer=payer, date='2026-06-01', number='DEL-JOURNEY-1',
        lines=[dict(item=item, quantity='1', net_amount='100.00')]))
    paid = run('payment receive', dict(customer=payer, date='2026-06-02', amount='100.00',
        payment_method=method, operation_key='journey-receive', applications=dict(mode='inline',
        items=[dict(invoice=bill['id'], expected_version=bill['version'], amount='100.00')])))
    application = paid['effect']['applications'][0]['application_id']
    settled = run('invoice settlement', dict(invoice=bill['id']))
    run('payment unapply', dict(payment=paid['id'], expected_version=paid['version'],
        operation_key='journey-unapply', applications=[dict(application_id=application,
        invoice_expected_version=settled['version'])]), **{'X-Bookflow-Reason': 'Release the application'})
    path = Path(run('company show', {})['path']) / 'company.db'

    state = hub('permission.show')
    hub('permission.activate', dict(expected_generation=state['generation'],
                                    expected_catalog_sha256=state['catalog_sha256']))
    base = f'{site.base_url}/c/{site.company_id}'
    workspace = base + f'/receive-payments?payment={paid["id"]}'
    action = ("Array.from(document.querySelectorAll('#payment-record button'))"
              ".find(x=>x.textContent==='Delete payment')")

    b.navigate(workspace)
    b.wait_for("document.querySelector('#payment-workspace')?.dataset.loaded==='true'")
    wait(b)
    # Activated but ungranted: the workspace offers no Delete journey at all.
    assert not b.evaluate('!!' + action)

    rows = hub('membership.list', dict(company=site.company_id))['items']
    mine = next(row for row in rows if row['scope_type'] == 'company' and row['scope_id'] == site.company_id)
    hub('membership.grant', dict(user=mine['user_id'], company=site.company_id,
        expected_version=mine['version'], grants=[PAYMENT_DELETE], denies=['ledger.post']))

    b.navigate(workspace)
    b.wait_for("document.querySelector('#payment-workspace')?.dataset.loaded==='true'")
    wait(b)
    assert b.evaluate('!!' + action)
    b.evaluate(action + '.click()')
    b.wait_for("!document.querySelector('#payment-workspace').hasAttribute('aria-busy')")
    assert b.evaluate("document.querySelector('#payment-title').innerText") == 'Delete this payment'
    assert not b.evaluate("document.querySelector('#payment-deletion-notice').hidden")
    assert not b.evaluate("document.querySelector('#payment-reason-label').hidden")
    assert b.evaluate("document.querySelector('#payment-preview').textContent") == 'Preview cancellation'
    assert b.evaluate("document.querySelector('#payment-save').textContent") == 'Delete payment'
    # The confirmation shows the receipt it is about to delete and no way to start
    # another action from inside it.
    assert 'Delete Journey Customer' in b.evaluate("document.querySelector('#payment-record').innerText")
    assert not b.evaluate("document.querySelectorAll('#payment-record button').length")

    before = recorded_state(path)
    b.evaluate("document.getElementById('payment-preview').click()")  # no reason yet
    b.wait_for("!document.querySelector('#payment-workspace').hasAttribute('aria-busy')")
    assert 'E_REASON_REQUIRED' in b.evaluate("document.querySelector('#payment-error').innerText")
    assert recorded_state(path) == before
    b.evaluate("(()=>{const x=document.getElementById('payment-reason');x.value='Duplicate receipt — keep this reason';"
               "x.dispatchEvent(new Event('change',{bubbles:true}));})()")
    b.evaluate("document.getElementById('payment-preview').click()")
    b.wait_for("!document.querySelector('#payment-workspace').hasAttribute('aria-busy')")
    assert 'Proposed cancellation' in b.evaluate("document.querySelector('#payment-preview-result').innerText")
    assert recorded_state(path) == before

    b.evaluate("document.getElementById('payment-save').click()")
    b.wait_for("!document.querySelector('#payment-workspace').hasAttribute('aria-busy')")
    assert 'Confirm cancellation' in b.evaluate("document.querySelector('#payment-error').innerText")
    assert recorded_state(path) == before
    assert b.evaluate("document.getElementById('payment-reason').value") == 'Duplicate receipt — keep this reason'
    folder = Path('notes/payment-deletion-screenshots'); folder.mkdir(parents=True, exist_ok=True)
    _contained(b, width)
    b.evaluate("document.getElementById('payment-deletion-notice').scrollIntoView()")
    (folder / f'delete-{width}.png').write_bytes(base64.b64decode(
        b.call('Page.captureScreenshot', {'captureBeyondViewport': True, 'fromSurface': True})['data']))

    b.evaluate("document.getElementById('payment-confirm').click()")
    b.evaluate("document.getElementById('payment-save').click()")
    b.wait_for("!document.querySelector('#payment-workspace').hasAttribute('aria-busy')")
    wait(b)
    b.wait_for("!!document.querySelector('[aria-label=\\\"Deleted payment\\\"]')")
    retained = b.evaluate("document.querySelector('#payment-record').innerText")
    assert 'Duplicate receipt — keep this reason' in retained and 'no restore action' in retained
    assert not b.evaluate('!!' + action)
    # Hidden from an ordinary read, exactly as the retained-deletion contract says.
    def command(name, body):
        return b.evaluate("fetch('/companies/" + site.company_id + "/commands/" + name + "',"
            "{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json',"
            "'X-Bookflow-Workbench':'1'},body:JSON.stringify(" + json.dumps(body) + ")})"
            ".then(async r=>({status:r.status,body:await r.json()}))", await_promise=True)

    ordinary = command('payment.show', dict(payment=paid['id']))
    assert ordinary['body']['code'] == 'E_RECORD_NOT_FOUND', ordinary
    listed = command('payment.query', {})
    assert listed['status'] == 200, listed
    assert paid['id'] not in [row['id'] for row in listed['body']['items']], listed
    _contained(b, width)
    (folder / f'deleted-{width}.png').write_bytes(base64.b64decode(
        b.call('Page.captureScreenshot', {'captureBeyondViewport': True, 'fromSurface': True})['data']))

    # The retained settlement history of the deleted receipt stays readable.
    b.navigate(f'{base}/application/{application}/history')
    b.wait_for("!!document.querySelector('[aria-label=\\\"Deleted payment\\\"]')")
    page = b.evaluate('document.body.innerText')
    assert 'Duplicate receipt — keep this reason' in page and 'Application and allocation history' in page
    _contained(b, width)
    (folder / f'retained-history-{width}.png').write_bytes(base64.b64decode(
        b.call('Page.captureScreenshot', {'captureBeyondViewport': True, 'fromSurface': True})['data']))
