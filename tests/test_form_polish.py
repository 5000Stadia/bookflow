"""R95: sales take today's date when none is given, the invoice form names the tax it will use,
and a line offers Clear Description only once it has a description to clear."""
import re

from bookflow.company.memorized_schedule import today
from tests.test_row3_host import WB, hosted  # noqa: F401
from tests.test_row5_workbench_forms import _browser


def _books(hosted):
    company = hosted.company_id
    ok = lambda name, body: hosted.ok(name, body, company=company)
    income = ok('account.create', dict(name='Polish income', type='income'))['id']
    code = next(row['id'] for row in ok('sales-tax-code.list', {})['items'] if not row['taxable'])
    described = ok('item.create', dict(name='Polish described', type='service', sales_enabled=True,
                                       income_account_id=income, price='10.00', description='Drain clearing',
                                       sales_tax_code_id=code))['id']
    customer = ok('customer.create', dict(name='Polish customer'))['id']
    return dict(ok=ok, customer=customer, described=described)


def test_sales_documents_default_to_the_company_today_over_http(hosted):
    books = _books(hosted)
    ok, customer, item = books['ok'], books['customer'], books['described']
    expected = today(ok('company.show', {})['info'].get('timezone'))
    line = [dict(item=item, quantity='1')]
    bank = ok('account.create', dict(name='Polish bank', type='bank'))['id']
    method = ok('payment-method.create', dict(name='Polish cash', kind='cash'))['id']
    # Every sales document the browser dates today takes today when the date is left out.
    assert ok('invoice.post', dict(customer=customer, lines=line))['revision']['date'] == expected
    receipt = ok('sales-receipt.post', dict(customer=customer, lines=line, deposit_to=bank, payment_method=method))
    assert receipt['revision']['date'] == expected
    credit = ok('credit-memo.post', dict(customer=customer, lines=[dict(item=item, quantity='1', unit_price='10.00')]))
    assert credit['revision']['date'] == expected
    assert ok('statement-charge.post', dict(customer=customer, item=item, quantity='1'))['revision']['date'] == expected
    # A preview dates the same day, and an explicit date is kept.
    preview = hosted.api.post(f'/companies/{hosted.company_id}/commands/invoice.post?dry_run=true',
                              json=dict(customer=customer, lines=line), headers=hosted.bearer)
    assert preview.status_code == 200 and preview.json()['revision']['date'] == expected
    assert ok('invoice.post', dict(customer=customer, lines=line, date='2026-01-05'))['revision']['date'] == '2026-01-05'


def test_invoice_defaults_to_the_company_today_from_python_and_the_cli(client, cli):
    from tests.test_service_sales_lifecycle import COMPANY
    info = client.run('company show', {}, company=COMPANY)['info']
    expected = today(info.get('timezone'))
    customer = client.customer.create(name='Polish python customer', company=COMPANY)['id']
    item = next(row['id'] for row in client.run('item list', {}, company=COMPANY)['items']
                if row.get('sales_enabled') and row['type'] == 'service')
    line = [dict(item=item, quantity='1')]
    assert client.run('invoice post', dict(customer=customer, lines=line), company=COMPANY)['revision']['date'] == expected
    shown = cli.json('invoice', 'post', '--company', COMPANY, '--customer', customer,
                     '--lines', f'[{{"item": "{item}", "quantity": "1"}}]')
    assert shown['revision']['date'] == expected


def test_the_new_invoice_names_its_tax_and_offers_clear_description_only_when_useful(hosted):
    books = _books(hosted)
    browser = _browser(hosted)
    route = f'/c/{hosted.company_id}/invoice/post'
    opened = browser.get(route).text
    # The demo's company default, named beside the empty Tax field before anything is saved.
    assert 'Left empty, this sale uses the company default, Combined Sales Tax' in opened
    # A new, empty line has no description to clear.
    assert 'clear:c:lines:__INDEX0__:description' not in opened
    form = {'f:customer': books['customer'], 'ref-state:f:customer': 'selected', 'collection:lines': '1',
            'c:lines:0:item': books['described'], 'ref-state:c:lines:0:item': 'selected',
            'c:lines:0:quantity': '1', 'action': 'preview'}
    previewed = browser.post(route, headers=WB, data=form)
    assert previewed.status_code == 200, previewed.text[:600]
    page = previewed.text
    assert 'Left empty, this sale uses Combined Sales Tax.' in page, re.findall(r'data-ref-default>[^<]*', page)
    # Once the line's item has given it a description, the line can clear it; a new empty line still cannot.
    assert re.findall(r'name="clear:c:lines:(\d+):description"', page) == ['0']
    assert 'clear:c:lines:__INDEX0__:description' not in page
