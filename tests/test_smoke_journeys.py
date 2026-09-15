"""Nine complete business journeys, walked over the real product surface.

This file exists because the defects that shipped were not found by unit tests. They were
found by doing a whole job end to end: invoicing a customer and taking their money, buying
stock and selling it, deleting a document and going back to read it. Each journey below is
one such job, and each one ends on the figure a person would look at -- what the customer
owes, what is on the shelf, what the report prints -- rather than on a status code.

Three rules hold everywhere here, and they are what make it worth running:

*Through the real surface.* Every act goes over the running host: the registered commands
over HTTP with a bearer token, which is the transport an agent uses, and the workbench
pages over a logged-in session, which is the surface a person uses. Nothing reaches into
the process to write a row, and no record id is supplied by hand -- every id below came
out of the act that created it.

*Fast enough to be run.* The whole file is minutes, not hours. There is no browser here:
where rendering is the thing under test the workbench page is fetched and parsed, which is
the same HTML an engine would lay out. A suite nobody runs finds nothing.

*Loud when it breaks.* Every assertion says which business act failed and what was expected
of it, so a reader of CI output never needs the traceback to know what broke.

The figures are written out in full so a reader can add them up without running anything.
"""
import os
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bookflow.adapters.workbench.permissions import FIELDS
from bookflow.core.deletion_families import capability
from tests.conftest import _seeded_template  # noqa: F401
from tests.payment_raw_evidence import database
from tests.test_identity_commands import WB
from tests.test_row3_host import PASSWORD, hosted  # noqa: F401
from tests.test_statement_drill_through import (PERIOD, _document_links, _ledger_link,  # noqa: F401
                                                _page, _run_report)

# ------------------------------------------------------------------ the money, written out

CALLOUT = '250.00'          # 25000 minor units -- one service call, invoiced and paid
CALLOUT_UNITS = 25000

UNIT_COST = '8.00'          # what one part costs from the supplier
BOUGHT = '5'                # five of them received on a bill    -> 40.00 of stock
STOCK_IN_UNITS = 4000
UNIT_PRICE = '30.00'        # what one part is sold for
SOLD = '2'                  # two of them invoiced               -> 60.00 receivable
STOCK_SALE = '60.00'
STOCK_SALE_UNITS = 6000
LEFT_ON_HAND = '3'          # 5 - 2
LEFT_VALUE_UNITS = 2400     # 3 x 8.00

PARTS = '184.60'            # the two grids of one vendor bill
DELIVERY = '100.00'
BILL_TOTAL = '284.60'       # 184.60 + 100.00
BILL_UNITS = 28460

CREDIT_FIRST = '40.00'      # the goodwill credit as first entered
CREDIT_FIXED = '55.00'      # the same credit, corrected
OWED_AFTER_FIRST = 21000    # 250.00 - 40.00
OWED_AFTER_FIX = 19500      # 250.00 - 55.00

CHARGE = '65.00'            # one statement charge, settled, which may never be voided
CHARGE_UNITS = 6500


# ----------------------------------------------------------------------------- the harness


def _rows_of(page, table_id):
    """One rendered table's own rows, so the surrounding page is never mistaken for one."""
    marker = f'id="{table_id}"'
    assert marker in page, f'the report rendered no {table_id} table at all'
    start = page.index(marker)
    return page[start:page.index('</table>', start)]


def _loud(act, command, response):
    """What a reader of CI output gets instead of a traceback."""
    return (f"\n  BUSINESS ACT FAILED: {act}"
            f"\n  through command: {command}"
            f"\n  the product answered: HTTP {response.status_code}"
            f"\n  saying: {response.text[:900]}\n")


class Company:
    """One running company and the two ways anybody reaches it.

    ``act``/``read`` are the registered commands over the host's HTTP transport, which is
    how an agent works. ``pages`` is a logged-in session against the same host, which is
    how a person works. Both are the shipped surface; neither is a shortcut.
    """

    def __init__(self, hosted):  # noqa: F811
        self.hosted, self.id, self._activated = hosted, hosted.company_id, False
        self.pages = TestClient(hosted.handle.app)
        signed_in = self.pages.post('/login', json={'username': hosted.login, 'password': PASSWORD})
        assert signed_in.status_code == 200, f'the installer could not sign in: {signed_in.text}'
        self.path = Path(hosted.ok('company.show', company=self.id)['path']) / 'company.db'

    # -- commands -----------------------------------------------------------------------

    def _send(self, command, body, headers, person):
        """One request, as whichever identity is asking.

        With no ``person`` this is the installer's bearer token, which is how an agent
        reaches the host. With one it is that person's own signed-in session and nothing
        else -- deliberately not ``Hosted.call``, which always attaches the installer's
        bearer and so would answer as the installer no matter who asked.
        """
        if person is None:
            return self.hosted.call(command, body or {}, company=self.id, headers=headers)
        return person.post(f'/companies/{self.id}/commands/{command}',
                           json=body or {}, headers={**WB, **(headers or {})})

    def act(self, what, command, body=None, *, because=None, client=None):
        answer = self._send(command, body, {'X-Bookflow-Reason': because or what}, client)
        assert answer.status_code == 200, _loud(what, command, answer)
        return answer.json()

    def read(self, what, command, body=None, *, client=None):
        """A read carries no reason: a report is not an act, and asking for one is refused."""
        answer = self._send(command, body, None, client)
        assert answer.status_code == 200, _loud(what, command, answer)
        return answer.json()

    def refused(self, what, command, body, *, because=None, client=None):
        """An act the books must not admit. Returns the refusal for the caller to name."""
        answer = self._send(command, body, {'X-Bookflow-Reason': because or what}, client)
        assert answer.status_code != 200, (
            f"\n  A DANGEROUS ACT WAS ADMITTED: {what}"
            f"\n  through command: {command}"
            f"\n  which answered 200 and did it: {answer.text[:900]}\n")
        return answer.json()

    # -- what the books say --------------------------------------------------------------

    def snapshot(self):
        """Every table of the company, so 'nothing changed' is a fact rather than a hope."""
        return database(self.path)

    def owed_by(self, customer, as_of='2035-12-31'):
        """What one customer owes, from the statement the customer would be sent."""
        statement = self.read(f'read the statement for {customer}', 'report.statement',
                              {'date_from': '2000-01-01', 'date_to': as_of, 'customer': customer})
        return statement['totals']['closing']['minor_units']

    def open_documents(self, customer, as_of='2035-12-31'):
        """Every receivable still open against one customer, by document number."""
        page = self.read(f'list what {customer} has not paid', 'report.open-invoices',
                         {'as_of': as_of, 'customer': customer, 'limit': 200})
        return {row['number']: row for row in page['rows']}

    def on_hand(self, item, as_of='2035-12-31'):
        """Quantity and value of one item, from the stock report the storeroom reads."""
        rows = self.read('read the stock report', 'report.stock-status',
                         {'as_of': as_of, 'limit': 200})['rows']
        row = next((row for row in rows if row['item_id'] == item), None)
        assert row is not None, f'the stock report does not carry item {item} at all'
        return row['quantity_on_hand'], row['asset_value']['minor_units']

    def settlement_of(self, invoice):
        return self.read('ask what is still owed on the invoice', 'invoice.settlement',
                         {'invoice': invoice})

    def unpaid_bills(self, as_of='2035-12-31'):
        """Every bill still owed, by the document's own id rather than its printed number."""
        page = self.read('list the bills still to pay', 'report.unpaid-bills',
                         {'as_of': as_of, 'limit': 200})
        return {row['transaction_id']: row for row in page['rows']}

    def owed_to(self, vendor, as_of='2035-12-31'):
        rows = self.read('age the payables', 'report.ap-aging', {'as_of': as_of, 'limit': 200})['rows']
        row = next((row for row in rows if row['display_vendor_label'] == vendor), None)
        return 0 if row is None else row['total']['minor_units']

    # -- setup a person would do once ----------------------------------------------------

    def references(self):
        accounts = {row['full_name']: row['id'] for row in
                    self.read('open the chart of accounts', 'account.query', {'limit': 200})['items']}
        methods = {row['name']: row['id'] for row in
                   self.read('list the ways money arrives', 'payment-method.query', {'limit': 50})['items']}
        codes = {row['code']: row['id'] for row in
                 self.read('list the sales tax codes', 'sales-tax-code.query', {'limit': 50})['items']}
        return accounts, methods, codes

    def activate_permissions(self):
        """Turn the reviewed policy on, once.

        Activation writes the whole executable catalog, which is the most expensive act in
        this file; it is also a property of the installation rather than of any one journey,
        so the first journey that needs it pays for it and the rest find it already on.
        """
        if self._activated:
            return
        state = self.hosted.ok('permission.show')
        self.hosted.ok('permission.activate',
                       {'expected_generation': state['generation'],
                        'expected_catalog_sha256': state['catalog_sha256']})
        self._activated = True

    def my_membership(self):
        return next(row for row in self.hosted.ok('membership.list', {'company': self.id})['items']
                    if row['scope_type'] == 'company' and row['scope_id'] == self.id)

    def grant_myself(self, *capabilities):
        """Give the signed-in person an explicit family grant, through the real command.

        ``role`` and the grants already held are both passed back deliberately: ``grants``
        replaces the whole set, and ``role`` defaults to ``standard`` when it is left out,
        so a grant made without them silently takes away everything else this person had.
        """
        self.activate_permissions()
        member = self.my_membership()
        self.hosted.ok('membership.grant',
                       {'user': member['user_id'], 'company': self.id, 'role': member['role'],
                        'expected_version': member['version'],
                        'grants': sorted(set(member['grants']) | set(capabilities))},
                       headers={'X-Bookflow-Reason': 'Let this person remove entry mistakes'})


@pytest.fixture(scope='module')
def company(tmp_path_factory, _seeded_template):  # noqa: F811
    """One migrated company on one running host, shared by every journey in this file.

    Standing the host up is the expensive part, and it is the same host each time, so it is
    stood up once. That costs nothing in isolation: each journey below works on records of
    its own -- its own customer, its own supplier, its own item and its own document numbers
    -- and every figure it asserts is read back filtered to those. A shared set of books is
    also the honest case, because a real company's reports are never run over one journey's
    data alone.

    The host itself is ``tests.test_row3_host.hosted``, called on a data root of this
    module's own rather than copied: one harness, one place it is defined.
    """
    root = tmp_path_factory.mktemp('smoke') / 'root'
    shutil.copytree(_seeded_template, root)
    previous = os.environ.get('BOOKFLOW_DATA_ROOT'), os.environ.get('BOOKFLOW_COMPANY')
    os.environ['BOOKFLOW_DATA_ROOT'] = str(root)
    os.environ.pop('BOOKFLOW_COMPANY', None)
    standing = hosted.__wrapped__(root)
    try:
        yield Company(next(standing))
    finally:
        standing.close()
        for name, value in zip(('BOOKFLOW_DATA_ROOT', 'BOOKFLOW_COMPANY'), previous):
            os.environ.pop(name, None) if value is None else os.environ.__setitem__(name, value)


# ------------------------------------------------------------ the acts the journeys share


def a_customer(company, name):
    return company.act(f'add the customer {name}', 'customer.create', {'name': name})['id']


def a_service(company, accounts, codes, name, price=CALLOUT):
    return company.act(f'add the service {name}', 'item.create', {
        'name': name, 'type': 'service', 'sales_enabled': True, 'price': price,
        'description': 'Emergency call-out, first hour',
        'sales_tax_code_id': codes['Non'], 'income_account_id': accounts['Service Income']})['id']


def an_invoice(company, customer, item, number, date, quantity='1', unit_price=CALLOUT):
    return company.act(f'invoice {customer} for {number}', 'invoice.post', {
        'customer': customer, 'date': date, 'number': number, 'customer_tax_code': 'Non',
        'lines': [{'item': item, 'quantity': quantity, 'unit_price': unit_price,
                   'description': 'Emergency call-out, first hour'}]},
        because=f'Bill the customer for {number}')


def receive_payment(company, customer, invoice, amount, methods, accounts, key, date):
    return company.act(f'take {amount} from the customer against {invoice["id"]}', 'payment.receive', {
        'customer': customer, 'date': date, 'amount': amount, 'operation_key': key,
        'payment_method': methods['Cash'], 'deposit_to': accounts['Checking'],
        'applications': {'mode': 'inline', 'items': [
            {'invoice': invoice['id'], 'expected_version': invoice['version'], 'amount': amount}]}},
        because='The customer paid')


# ============================================================ 1. sell something, get paid


def test_a_service_is_invoiced_and_paid_and_the_customer_then_owes_nothing(company):
    """The whole receivable cycle: bill 250.00, collect 250.00, owe nothing."""
    accounts, methods, codes = company.references()
    customer = a_customer(company, 'Smoke Sale Customer')
    item = a_service(company, accounts, codes, 'Smoke Sale Call-out')

    invoice = an_invoice(company, customer, item, 'SMOKE-SALE-1', '2030-02-01')
    assert invoice['total']['amount'] == CALLOUT, (
        f"invoicing one call-out at {CALLOUT} produced a document totalling "
        f"{invoice['total']['amount']} instead")
    assert company.owed_by(customer) == CALLOUT_UNITS, (
        f'after invoicing {CALLOUT} the customer should owe {CALLOUT_UNITS} minor units, '
        f'and the statement says {company.owed_by(customer)}')
    still_open = company.open_documents(customer)
    assert set(still_open) == {'SMOKE-SALE-1'}, f'the open list reads {sorted(still_open)}'
    assert still_open['SMOKE-SALE-1']['settlement_status'] == 'unpaid'

    receive_payment(company, customer, invoice, CALLOUT, methods, accounts,
                    'smoke-sale-receipt', '2030-02-05')

    settled = company.settlement_of(invoice['id'])
    assert settled['status'] == 'paid', (
        f"the customer paid {CALLOUT} in full and the invoice still reads "
        f"'{settled['status']}' rather than 'paid'")
    assert settled['due_minor_units'] == 0, (
        f"a fully paid invoice still shows {settled['due_minor_units']} minor units owing")
    assert company.owed_by(customer) == 0, (
        f'the customer paid in full and their statement still closes at '
        f'{company.owed_by(customer)} minor units')
    assert company.open_documents(customer) == {}, (
        'a paid invoice is still listed among what the customer has not paid')

    # And the page the person actually opens says the same thing.
    shown = company.pages.get(f"/c/{company.id}/invoice/{invoice['id']}")
    assert shown.status_code == 200, f'the invoice page did not open: {shown.text[:500]}'
    assert 'SMOKE-SALE-1' in shown.text, 'the invoice page does not name the invoice'
    assert 'aid' in shown.text, 'the invoice page of a fully paid invoice never says it is paid'


# ======================================================== 2. sell stock, get paid for it


def test_stock_bought_and_sold_moves_by_quantity_and_value_and_the_sale_settles(company):
    """Buy 5 at 8.00, sell 2 at 30.00, collect 60.00.

        received   5 x  8.00  ->  5 on hand,  40.00 of stock
        sold       2 x 30.00  ->  3 on hand,  24.00 of stock,  60.00 receivable
        paid        60.00     ->  3 on hand,  24.00 of stock,  nothing owed

    The settlement half of this is the path that shipped broken: a stocked invoice line
    leaves both a receivable and a cost behind, and paying it has to settle the first.
    """
    accounts, methods, codes = company.references()
    customer = a_customer(company, 'Smoke Stock Customer')
    part = company.act('add the stocked part', 'item.create', {
        'name': 'Smoke Stock Coupling', 'type': 'inventory_part',
        'description': '3/4in coupling', 'price': UNIT_PRICE, 'cost': UNIT_COST,
        'purchase_description': '3/4in coupling', 'sales_tax_code_id': codes['Non'],
        'income_account_id': accounts['Service Income'],
        'cogs_account_id': accounts['Cost of Goods Sold']})['id']

    company.act('receive five couplings on a vendor bill', 'bill.post', {
        'vendor': 'Central Supply', 'date': '2030-04-01', 'supplier_reference': 'SMOKE-STOCK-IN',
        'items': [{'item': part, 'quantity': BOUGHT, 'unit_cost': UNIT_COST}]},
        because='Receive the stock')
    assert company.on_hand(part) == (BOUGHT, STOCK_IN_UNITS), (
        f'after receiving {BOUGHT} at {UNIT_COST} the shelf should read '
        f'({BOUGHT}, {STOCK_IN_UNITS}) and it reads {company.on_hand(part)}')

    invoice = an_invoice(company, customer, part, 'SMOKE-STOCK-1', '2030-04-02',
                         quantity=SOLD, unit_price=UNIT_PRICE)
    assert invoice['total']['amount'] == STOCK_SALE, (
        f"selling {SOLD} at {UNIT_PRICE} produced {invoice['total']['amount']}, not {STOCK_SALE}")
    assert company.on_hand(part) == (LEFT_ON_HAND, LEFT_VALUE_UNITS), (
        f'selling {SOLD} of {BOUGHT} should leave ({LEFT_ON_HAND}, {LEFT_VALUE_UNITS}) '
        f'on the shelf, and it reads {company.on_hand(part)}')
    assert company.owed_by(customer) == STOCK_SALE_UNITS

    receive_payment(company, customer, invoice, STOCK_SALE, methods, accounts,
                    'smoke-stock-receipt', '2030-04-03')

    settled = company.settlement_of(invoice['id'])
    assert settled['status'] == 'paid' and settled['due_minor_units'] == 0, (
        f"a stocked invoice paid in full reads '{settled['status']}' with "
        f"{settled['due_minor_units']} minor units still owing")
    assert company.owed_by(customer) == 0, 'the stocked sale was paid and the customer still owes'
    # Cash arriving is not a stock movement: the shelf must not have moved.
    assert company.on_hand(part) == (LEFT_ON_HAND, LEFT_VALUE_UNITS), (
        f'taking the customer\'s money changed the stock to {company.on_hand(part)}, '
        f'when it should still read ({LEFT_ON_HAND}, {LEFT_VALUE_UNITS})')


# ================================================== 3. buy something, and pay for it


def test_a_vendor_bill_is_entered_and_paid_and_payables_agree_at_every_step(company):
    """184.60 of parts plus 100.00 of delivery, owed and then paid, read off the reports."""
    accounts, _, _ = company.references()
    vendor = company.act('add the supplier', 'vendor.create', {'name': 'Smoke Supply Co'})['id']

    bill = company.act('enter the supplier bill', 'bill.post', {
        'vendor': vendor, 'date': '2030-03-03', 'supplier_reference': 'SMOKE-BILL-1',
        'memo': 'March parts and delivery',
        'expenses': [{'account': accounts['Job Materials'], 'amount': PARTS, 'memo': 'Fittings'},
                     {'account': accounts['Equipment Rental'], 'amount': DELIVERY, 'memo': 'Delivery'}]},
        because='Enter the March bill')
    assert bill['total']['amount'] == BILL_TOTAL, (
        f"{PARTS} of parts and {DELIVERY} of delivery came to {bill['total']['amount']}, "
        f'not {BILL_TOTAL}')

    owing = company.unpaid_bills()
    assert bill['id'] in owing, (
        'the bill just entered is not among the bills still to pay; the report lists '
        f'{sorted(row["number"] for row in owing.values())}')
    assert owing[bill['id']]['balance']['minor_units'] == BILL_UNITS, (
        f"the bill is listed owing {owing[bill['id']]['balance']['minor_units']} minor units "
        f'rather than {BILL_UNITS}')
    assert owing[bill['id']]['supplier_reference'] == 'SMOKE-BILL-1', (
        "the bill to pay does not carry the supplier's own reference number")
    assert company.owed_to('Smoke Supply Co') == BILL_UNITS, (
        f'the payables aging owes this supplier {company.owed_to("Smoke Supply Co")} '
        f'rather than {BILL_UNITS}')

    company.act('pay the supplier in full', 'bill.pay', {
        'date': '2030-03-10', 'bills': [{'bill': bill['id']}],
        'funding_account': accounts['Checking'], 'method': 'Check', 'check_number': '1041'},
        because='Pay the March bill')

    shown = company.read('open the paid bill', 'bill.show', {'bill': bill['id']})
    assert shown['settlement_current']['status'] == 'paid', (
        f"a bill paid in full reads '{shown['settlement_current']['status']}'")
    assert shown['settlement_current']['open']['amount'] == '0.00', (
        f"a bill paid in full still shows {shown['settlement_current']['open']['amount']} open")
    assert bill['id'] not in company.unpaid_bills(), (
        'a bill that has been paid in full is still listed among the bills to pay')
    assert company.owed_to('Smoke Supply Co') == 0, (
        f'this supplier was paid in full and the aging still owes them '
        f'{company.owed_to("Smoke Supply Co")}')


# =========================================== 4. correct a mistake, and keep what was wrong


def test_a_credit_is_corrected_to_a_new_revision_and_the_first_one_is_still_readable(company):
    """A 40.00 goodwill credit, corrected to 55.00. Both figures must survive.

        invoiced             250.00   -> owed 250.00
        credited  40.00               -> owed 210.00
        credit corrected to  55.00    -> owed 195.00, and revision 1 still says 40.00
    """
    accounts, _, codes = company.references()
    customer = a_customer(company, 'Smoke Correction Customer')
    item = a_service(company, accounts, codes, 'Smoke Correction Call-out')
    an_invoice(company, customer, item, 'SMOKE-FIX-1', '2030-05-01')
    assert company.owed_by(customer) == CALLOUT_UNITS

    credit = company.act('credit the customer for the call-out we botched', 'credit-memo.post', {
        'customer': customer, 'date': '2030-05-04', 'customer_tax_code': 'Non',
        'lines': [{'item': item, 'quantity': '1', 'unit_price': CREDIT_FIRST}]},
        because='Goodwill credit')
    assert credit['total']['amount'] == CREDIT_FIRST
    assert company.owed_by(customer) == OWED_AFTER_FIRST, (
        f'a {CREDIT_FIRST} credit against a {CALLOUT} invoice should leave '
        f'{OWED_AFTER_FIRST} minor units owing, and the statement says '
        f'{company.owed_by(customer)}')

    corrected = company.act('correct the credit to the agreed amount', 'credit-memo.update', {
        'credit_memo': credit['id'], 'expected_version': credit['version'],
        'lines': [{'line_id': credit['revision']['lines'][0]['line_id'], 'item': item,
                   'quantity': '1', 'unit_price': CREDIT_FIXED}]},
        because='The agreed credit was 55.00, not 40.00')
    assert corrected['total']['amount'] == CREDIT_FIXED, (
        f"correcting the credit to {CREDIT_FIXED} produced {corrected['total']['amount']}")
    assert corrected['version'] > credit['version'], (
        'a correction must reach a new revision; the version did not move')
    assert company.owed_by(customer) == OWED_AFTER_FIX, (
        f'after correcting the credit to {CREDIT_FIXED} the customer should owe '
        f'{OWED_AFTER_FIX} minor units, and the statement says {company.owed_by(customer)}')

    was = company.read('read the credit as it was first entered', 'credit-memo.show',
                       {'credit_memo': credit['id'], 'revision_number': 1})
    assert was['total']['amount'] == CREDIT_FIRST, (
        f"the first revision of the credit should still read {CREDIT_FIRST} and reads "
        f"{was['total']['amount']} -- the correction overwrote history")
    revisions = company.read('page the credit\'s revisions', 'credit-memo.history',
                             {'credit_memo': credit['id'], 'limit': 10})
    assert len(revisions['items']) >= 2, (
        f"a corrected credit should have at least two revisions and has "
        f"{len(revisions['items'])}")


# ================================= 5. delete a document, and still be able to read it


def test_a_deleted_invoice_leaves_the_ordinary_lists_and_is_still_readable_as_history(company):
    """Delete a sale, and then go back and read it -- the whole point of retained deletion."""
    accounts, _, codes = company.references()
    customer = a_customer(company, 'Smoke Deletion Customer')
    item = a_service(company, accounts, codes, 'Smoke Deletion Call-out')
    invoice = an_invoice(company, customer, item, 'SMOKE-DEL-1', '2030-06-01')
    assert company.owed_by(customer) == CALLOUT_UNITS

    reason = 'Entered twice from the same work order'
    company.grant_myself(capability('invoice'))
    company.act('delete the duplicate invoice', 'invoice.delete',
                {'invoice': invoice['id'], 'expected_version': invoice['version'],
                 'operation_key': 'smoke-invoice-delete'}, because=reason)

    assert company.owed_by(customer) == 0, (
        f'a deleted invoice still leaves {company.owed_by(customer)} minor units owing')
    assert company.open_documents(customer) == {}, 'a deleted invoice is still listed as open'
    ordinary = [row['id'] for row in
                company.read('list invoices', 'invoice.query', {'limit': 200})['items']]
    assert invoice['id'] not in ordinary, 'a deleted invoice is still in the ordinary list'

    # Retained history: the explicit read brings it back, through the command and the page.
    retained = [row['id'] for row in company.read(
        'list invoices including deleted ones', 'invoice.query',
        {'limit': 200, 'include_deleted': True})['items']]
    assert invoice['id'] in retained, (
        "'include deleted records' did not bring back a deleted invoice: the retained read "
        'returns the same list as the ordinary one')

    url = f'/c/{company.id}/invoice'
    listed = company.pages.get(url)
    assert listed.status_code == 200, listed.text[:500]
    assert f'href="{url}/{invoice["id"]}"' not in listed.text, (
        'the ordinary invoice list still links a deleted invoice')
    assert 'Include deleted records' in listed.text, (
        'the invoice list offers no way to see deleted records at all')
    with_deleted = company.pages.get(f'{url}?include_deleted=true')
    assert f'href="{url}/{invoice["id"]}?include_deleted=1"' in with_deleted.text, (
        "the 'Include deleted records' control did not list the invoice that was deleted")
    history = company.pages.get(f'{url}/{invoice["id"]}?include_deleted=1')
    assert history.status_code == 200, f'the retained invoice would not open: {history.text[:500]}'
    assert 'SMOKE-DEL-1' in history.text and reason in history.text, (
        'the retained invoice does not present itself as deleted history with its reason')


# ============================================ 6. refuse something dangerous, change nothing


def test_the_books_refuse_to_unmake_a_settled_sale_or_spend_applied_money(company):
    """Two guards, each with the whole database proved unchanged behind it.

    A receivable the customer has already paid may not be voided, and the receipt that paid
    it may not be deleted while its money is still applied. Both are refusals that must cost
    nothing: a guard that answers no and writes anyway is worse than no guard.
    """
    accounts, methods, codes = company.references()
    customer = a_customer(company, 'Smoke Guard Customer')
    item = a_service(company, accounts, codes, 'Smoke Guard Call-out')

    invoice = an_invoice(company, customer, item, 'SMOKE-GUARD-1', '2030-07-01')
    receipt = receive_payment(company, customer, invoice, CALLOUT, methods, accounts,
                              'smoke-guard-receipt', '2030-07-02')
    assert company.owed_by(customer) == 0, (
        f'the invoice was paid in full and the customer still owes '
        f'{company.owed_by(customer)} minor units')

    company.grant_myself(capability('invoice'), capability('payment'))
    before = company.snapshot()

    voided = company.refused('void an invoice the customer has already paid', 'invoice.void',
                             {'invoice': invoice['id'], 'expected_version': invoice['version']},
                             because='Try to unmake a settled sale')
    assert voided['code'] != 'E_RECORD_NOT_FOUND', (
        f'the settled invoice was refused for the wrong reason: {voided}')

    current = company.read('read the receipt back', 'payment.show', {'payment': receipt['id']})
    company.refused('delete a receipt whose money is still applied to an invoice', 'payment.delete',
                    {'payment': receipt['id'], 'expected_version': current['version'],
                     'operation_key': 'smoke-guard-delete'},
                    because='Try to remove money that is still spent')

    assert company.snapshot() == before, (
        'a refused act still changed the database: the guard answered no and wrote anyway')
    assert company.owed_by(customer) == 0, (
        f'after two refused acts the customer owes {company.owed_by(customer)} minor units '
        f'instead of nothing -- a refusal moved a balance')
    assert company.settlement_of(invoice['id'])['status'] == 'paid', (
        'a refused void left the invoice reading as something other than paid')


def test_a_statement_charge_is_settled_over_the_host_and_then_cannot_be_unmade(company):
    """Charge a customer's account directly, take their money, and refuse to unmake it.

    A statement charge is the other settleable receivable: the same money path as an
    invoice, entered straight onto the account. This journey settles one over the running
    host -- the transport the product actually serves -- before asking the void guard to
    refuse, because a guard is only worth testing on a document the money really reached.

    The invoice above it is deliberate. It settles first, through the same transport, in the
    same request style, by the same person. If the invoice settles and the charge does not,
    what differs is the document type and nothing else.
    """
    accounts, methods, codes = company.references()
    customer = a_customer(company, 'Smoke Charge Customer')
    item = a_service(company, accounts, codes, 'Smoke Charge Call-out')

    invoice = an_invoice(company, customer, item, 'SMOKE-CHARGE-INV', '2030-07-01')
    receive_payment(company, customer, invoice, CALLOUT, methods, accounts,
                    'smoke-charge-invoice-receipt', '2030-07-02')
    assert company.settlement_of(invoice['id'])['status'] == 'paid'

    charge = company.act('charge the customer straight onto their account',
                         'statement-charge.post', {
                             'customer': customer, 'date': '2030-07-03', 'number': 'SMOKE-SC-1',
                             'item': item, 'quantity': '1', 'rate': CHARGE,
                             'description': 'Call re lease'}, because='Enter the charge')
    assert charge['total']['amount'] == CHARGE
    assert company.owed_by(customer) == CHARGE_UNITS, (
        f'after a {CHARGE} charge the customer should owe {CHARGE_UNITS} minor units and '
        f'owes {company.owed_by(customer)}')

    taken = company._send('payment.receive', {
        'customer': customer, 'date': '2030-07-04', 'amount': CHARGE,
        'operation_key': 'smoke-charge-receipt', 'payment_method': methods['Cash'],
        'deposit_to': accounts['Checking'],
        'applications': {'mode': 'inline', 'items': [
            {'invoice': charge['id'], 'expected_version': charge['version'], 'amount': CHARGE}]}},
        {'X-Bookflow-Reason': 'The customer paid the charge'}, None)
    assert taken.status_code == 200, (
        "\n  BUSINESS ACT FAILED: take the customer's money for a statement charge"
        '\n  through command: payment.receive, over the running host'
        f'\n  the product answered: HTTP {taken.status_code}'
        f'\n  saying: {taken.text[:400]}'
        '\n'
        '\n  The identical receipt settled this same customer\'s invoice a few lines above,'
        '\n  through the same transport, in the same request, by the same person. The only'
        '\n  difference is the type of receivable the money was pointed at. A statement'
        '\n  charge is a settleable receivable and is named in `invoice` fields exactly as'
        '\n  an invoice is, so a customer paying one is an ordinary act that the books'
        '\n  accept in process and this transport does not.\n')
    assert company.owed_by(customer) == 0, (
        f'the charge was paid in full and the customer still owes '
        f'{company.owed_by(customer)} minor units')

    settled = company.read('read the settled charge back', 'statement-charge.show',
                           {'statement_charge': charge['id']})
    before = company.snapshot()
    refusal = company.refused(
        'void a statement charge the customer has already paid', 'statement-charge.void',
        {'statement_charge': charge['id'], 'expected_version': settled['version']},
        because='Try to unmake a settled charge')
    assert refusal['code'] != 'E_RECORD_NOT_FOUND', (
        f'the settled statement charge was refused for the wrong reason: {refusal}')
    assert company.snapshot() == before, (
        'the refused void of a settled charge still changed the database')
    assert company.owed_by(customer) == 0, (
        f'after the void was refused the customer owes {company.owed_by(customer)} minor '
        f'units; a settled charge voided out from under its money flips the balance negative')


# ================================== 7. read the books, and follow a figure to its document


def test_a_report_renders_with_data_and_a_figure_reaches_the_document_behind_it(company):
    """The reports a person opens, on a company that has transactions in it.

    Rendering with data is the thing under test: the by-job statement raised a template
    error for any company that had any, while an empty one came out fine, so a report is
    only proved by running it over books that are not empty.
    """
    accounts, methods, codes = company.references()
    customer = a_customer(company, 'Smoke Report Customer')
    item = a_service(company, accounts, codes, 'Smoke Report Call-out')
    invoice = an_invoice(company, customer, item, 'SMOKE-REPORT-1', '2030-08-01')
    receive_payment(company, customer, invoice, CALLOUT, methods, accounts,
                    'smoke-report-receipt', '2030-08-02')

    period = '&'.join(f'{key}={value}' for key, value in PERIOD.items())
    statement = _run_report(company.pages, f'/c/{company.id}/report/profit-and-loss?{period}')
    assert 'Service Income' in statement, (
        'a profit and loss over books holding a 250.00 service sale does not name the '
        'income account it landed in')

    ledger_url = _ledger_link(statement, company.id)
    ledger = _run_report(company.pages, ledger_url)
    documents = _document_links(ledger, company.id)
    assert documents, (
        'a general ledger opened from a profit-and-loss figure names transactions but '
        'offers no way to open any of them')
    href = documents[0][0]
    named = href.split('?')[0].rsplit('/', 1)[-1]
    document = _page(company.pages, href)
    assert named in document, f'following {href} did not open the transaction the row named'

    # The by-job statement, over the same non-empty books. This is the report that raised a
    # template error for any company holding data while an empty one rendered perfectly, so
    # running it over books that have transactions in them is the whole assertion.
    by_job = _run_report(company.pages,
                         f'/c/{company.id}/report/profit-and-loss-by-job?{period}')
    assert 'Service Income' in by_job, (
        'the by-job profit and loss rendered over books with data but printed no income')
    printed = _rows_of(by_job, 'dimensional-lines')
    assert printed.count('<tr') >= 1, (
        'the by-job profit and loss came out with no account rows over books that have '
        'transactions in them')
    assert 'Net income' in by_job, 'the by-job profit and loss printed no net income'
    assert 'report/general-ledger' in printed, (
        'the by-job Total column printed figures that open nothing -- the column that '
        'shipped with nothing following it')


# ========================================== 8. a capability granted in setup, and refused


def test_a_capability_granted_through_user_setup_is_what_admits_the_action(company):
    """One clerk, one checkbox. Refused before it, allowed after it, and nothing in between."""
    accounts, _, codes = company.references()
    customer = a_customer(company, 'Smoke Permission Customer')
    item = a_service(company, accounts, codes, 'Smoke Permission Call-out')
    invoice = an_invoice(company, customer, item, 'SMOKE-PERM-1', '2030-09-01')

    company.activate_permissions()
    clerk = company.hosted.ok('user.add', {'username': 'smoke-clerk', 'password': 'clerk fixture',
                                           'company': company.id, 'role': 'standard'})
    session = TestClient(company.hosted.handle.app)
    assert session.post('/login', json={'username': 'smoke-clerk',
                                        'password': 'clerk fixture'}).status_code == 200

    delete = {'invoice': invoice['id'], 'expected_version': invoice['version'],
              'operation_key': 'smoke-permission-delete'}
    before = company.snapshot()
    denied = company.refused('delete an invoice with no grant to do so', 'invoice.delete', delete,
                             because='No grant yet', client=session)
    assert denied['code'] == 'E_PERMISSION', (
        f'an ungranted clerk was refused for the wrong reason: {denied}')
    assert company.snapshot() == before, 'a refused deletion still changed the database'
    # With no grant, the clerk's own page offers no way in either.
    detail = f'/c/{company.id}/invoice/{invoice["id"]}'
    ungranted = session.get(detail)
    assert ungranted.status_code == 200, ungranted.text[:400]
    assert f'{detail}/delete' not in ungranted.text, (
        'the invoice page offers a Delete journey to a person who may not delete')

    # The grant itself, made on the page an administrator actually uses.
    setup = company.pages.get(f'/c/{company.id}/users?user={clerk["user_id"]}')
    assert setup.status_code == 200, setup.text[:500]
    assert 'name="invoice_delete"' in setup.text, (
        'company setup offers no control for granting invoice deletion')
    assert [field for field in FIELDS if f'name="{field}"' in setup.text] == list(FIELDS), (
        'company setup does not offer every family that ships retained deletion')
    saved = company.pages.post(f'/c/{company.id}/users', headers=WB, data={
        'user': clerk['user_id'], 'role': 'standard', 'expected_version': '1',
        'other_grants': '[]', 'other_denies': '[]', 'invoice_delete': 'on', 'allow_read': 'on',
        'allow_post': 'on', 'action': 'save',
        'reason': 'Let this clerk remove invoices entered twice'})
    assert saved.status_code == 200, f'saving the grant failed: {saved.text[:600]}'
    effective = company.hosted.ok('membership.effective',
                                  {'company': company.id, 'user': clerk['user_id']})
    admitted = {row['requirement']['capability']: row['admitted'] for row in effective['permissions']}
    assert admitted.get(capability('invoice')) is True, (
        'the grant was saved on the setup page and the effective permissions still refuse it')

    # And the same act the clerk was refused now goes through.
    done = company.act('delete the invoice now that the clerk may', 'invoice.delete', delete,
                       because='Entered twice from the same work order', client=session)
    assert done['status'] == 'deleted', f'the granted deletion did not delete: {done}'
    assert company.owed_by(customer) == 0
