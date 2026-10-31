"""R114 increment 5: ringing up and completing a POS sale (design/point-of-sale.md §3, §4; criteria 1 and 7).

Criterion 1, same books: twin companies (byte-identical copies of the demo root) are given the same generated carts,
one through `pos sale complete` and the other through `sales-receipt post` with the input a person would type for it
(the register's deposit account, site and the walk-in or the chosen customer, today, the payment method). Stocked,
service, non-inventory, discount and subtotal items, a price level, a price rule, taxable and exempt customers and
the walk-in. Afterwards each receipt, the trial balance, the general ledger, stock and its value, the sales tax owed
and Undeposited Funds read the same in both.

Criterion 7, nothing doubled: completing again with the same ticket returns the sale already posted (``replayed``),
and the same ticket with another cart is refused; nothing posts twice.
"""
import json
import random
import re

import pytest

import bookflow
from bookflow.core import registry
from tests.conftest import copy_seeded_root
from tests.test_row3_host import hosted  # noqa: F401 - fixture

REASON = 'pos sale test'
_ULID = re.compile(r'\b[0-9A-HJKMNP-TV-Z]{26}\b')
_STAMP = re.compile(r'\d{4}-\d\d-\d\dT\d\d:\d\d[:\d.]*(Z|[+-]\d\d:?\d\d)?')
_SKIP = re.compile(r'snapshot|cursor|fingerprint|generated_at|as_posted|freshness|token|watermark|audit_event|'
                   r'request_id|^id$|_id$|_ids$|created_|updated_|idempotent|dry_run|ticket')


def _norm(value):
    if isinstance(value, dict):
        return {k: _norm(v) for k, v in value.items() if not _SKIP.search(k)}
    if isinstance(value, list):
        return [_norm(v) for v in value]
    if isinstance(value, str):
        return _STAMP.sub('<ts>', _ULID.sub('<id>', value))
    return value


class Company:
    def __init__(self, root):
        self.c = bookflow.connect(data_root=str(root))
        self.cid = self.c.company.list()['items'][0]['company_id']

    def run(self, name, data=None, **kw):
        if registry.get(name).is_write:
            kw.setdefault('reason', REASON)
        return self.c.run(name, data or {}, company=self.cid, **kw)

    def fails(self, name, data):
        with pytest.raises(bookflow.BookflowError) as caught:
            self.run(name, data)
        return caught.value


def _setup(co):
    """The same setup in each twin: POS on, price levels on, a price rule, and customers who are taxed, exempt and
    on a price level."""
    info = co.run('company show')
    co.run('company update', {'pos_enabled': True, 'enable_price_levels': True, 'expected_version': info['info_version']})
    codes = {code['code']: code['id'] for code in co.run('sales-tax-code list')['items']}
    levels = {level['name']: level['id'] for level in co.run('price-level list')['items']}
    co.run('customer create', {'name': 'Tina Taxed', 'sales_tax_code_id': codes['Tax']})
    co.run('customer create', {'name': 'Eddie Exempt', 'sales_tax_code_id': codes['EX']})
    co.run('customer create', {'name': 'Pat Preferred', 'sales_tax_code_id': codes['Tax'],
                               'price_level_id': levels['Preferred Customer']})
    co.run('price-rule create', {'name': 'Pipe by the length', 'items': ['Copper Pipe'], 'min_quantity': '3',
                                 'kind': 'percent', 'direction': 'down', 'value': '10', 'rounding': 'cent'})
    return co.run('pos-register show', {'pos_register': 'Main'})


ITEMS = ['Brass Shutoff Valve', 'Copper Pipe', 'Mainline Clearing', 'Copper Coupling', 'Commercial Example Service']
CUSTOMERS = [None, 'Tina Taxed', 'Eddie Exempt', 'Pat Preferred']
METHODS = ['Cash', 'Visa', 'Check']


def _carts(seed, count):
    rnd = random.Random(seed)
    carts = []
    for n in range(count):
        lines = [{'item': rnd.choice(ITEMS), 'quantity': str(rnd.choice([1, 1, 2, 3, 4]))}
                 for _ in range(rnd.randint(1, 4))]
        if rnd.random() < .4:
            lines.append({'item': 'Work Order Subtotal'})
            if rnd.random() < .7:
                lines.append({'item': 'Loyalty Discount'})
        elif rnd.random() < .3:
            lines.append({'item': 'Loyalty Discount'})
        if rnd.random() < .25:
            lines.append({'item': 'Service Call Kit', 'unit_price': '35.00'})   # an item priced 0.00, priced at the till
        carts.append(dict(customer=rnd.choice(CUSTOMERS), method=METHODS[n % len(METHODS)], lines=lines))
    return carts


@pytest.fixture
def twins(tmp_path, monkeypatch, _seeded_template):
    pos = Company(copy_seeded_root(_seeded_template, tmp_path / 'pos-root'))
    typed = Company(copy_seeded_root(_seeded_template, tmp_path / 'typed-root'))
    monkeypatch.delenv('BOOKFLOW_COMPANY', raising=False)
    return pos, typed


def _pages(co, name, body):
    pages, cursor = [], None
    while True:
        page = co.run(name, dict(body, limit=200, **({'cursor': cursor} if cursor else {})))
        pages.append(page)
        cursor = page.get('next_cursor')
        if not cursor:
            return pages


def _reads(co):
    """What must read the same in both twins."""
    out = {}
    out['trial'] = co.run('report trial-balance', {'date_to': '2030-12-31'})
    out['ledger'] = _pages(co, 'report general-ledger', {'date_from': '2026-01-01', 'date_to': '2030-12-31'})
    out['stock'] = [(i['full_name'], i['quantity_on_hand'], i['average_cost'], i['inventory_value'])
                    for i in co.run('item list', {'include_inactive': True})['items']]
    out['valuation'] = _pages(co, 'report inventory-valuation', {'as_of': '2030-12-31'})
    out['tax'] = _pages(co, 'sales-tax liability', {'date_from': '2020-01-01', 'date_to': '2030-12-31'})
    out['undeposited'] = co.run('account show', {'account': 'Undeposited Funds'})
    return _norm(out)


@pytest.mark.timeout(1200)
def test_a_pos_sale_posts_exactly_what_sales_receipt_post_would(twins):
    pos, typed = twins
    reg_pos, reg_typed = _setup(pos), _setup(typed)
    walk_in = {'pos': reg_pos['walk_in_customer']['id'], 'typed': reg_typed['walk_in_customer']['id']}
    assert reg_pos['walk_in_customer']['name'] == reg_typed['walk_in_customer']['name'] == 'Walk-in'
    seen = set()
    for n, cart in enumerate(_carts(5114, 14)):
        customer = cart['customer']
        body = {'lines': cart['lines'], **({'customer': customer} if customer else {})}
        shown = pos.run('pos sale preview', body)
        assert shown['customer'] is None if customer is None else shown['customer']['name'] == customer
        total = shown['total']['amount']
        tender = {'payment_method': cart['method']}
        if cart['method'] == 'Cash':
            tender['amount'] = str(int(float(total)) + 20)
        else:
            tender['reference'] = f'ref {n}'
        sale = pos.run('pos sale complete', dict(body, ticket=f'twin-{n}', tender=tender, expected_total=total))
        assert sale['total']['amount'] == total and not sale['replayed']
        if cart['method'] == 'Cash':
            assert sale['change']['amount'] == f"{float(tender['amount']) - float(total):.2f}"
        else:
            assert sale['change']['amount'] == '0.00' and sale['tenders'][0]['reference'] == f'ref {n}'
        # The twin: what a person would type into the sales receipt form for this sale.
        typed_body = dict(customer=customer or walk_in['typed'], deposit_to=reg_typed['deposit_account']['id'],
                          site=reg_typed['site']['id'], payment_method=cart['method'], lines=cart['lines'])
        if cart['method'] != 'Cash':
            typed_body['payment_reference'] = f'ref {n}'
        receipt = typed.run('sales-receipt post', typed_body)
        mine = pos.run('sales-receipt show', {'sales_receipt': sale['sales_receipt']['id']})
        theirs = typed.run('sales-receipt show', {'sales_receipt': receipt['id']})
        assert _norm(mine) == _norm(theirs), n
        assert mine['customer_id'] == (walk_in['pos'] if customer is None else mine['customer_id'])
        seen.add((customer is None, cart['method']))
    assert {(True, 'Cash'), (False, 'Cash')} <= seen
    assert _reads(pos) == _reads(typed)


def test_a_retry_with_the_same_ticket_posts_once(twins):
    co, _ = twins
    _setup(co)
    before = len(co.run('sales-receipt query', {'limit': 200})['items'])
    cart = {'lines': [{'item': 'Brass Shutoff Valve', 'quantity': '2'}, {'item': 'Mainline Clearing'}]}
    total = co.run('pos sale preview', cart)['total']['amount']
    body = dict(cart, ticket='01JTICKETRETRY0000000000AA', tender={'payment_method': 'Cash', 'amount': '500'},
                expected_total=total)
    first = co.run('pos sale complete', body)
    again = co.run('pos sale complete', body)
    assert not first['replayed'] and again['replayed']
    assert again['sales_receipt'] == first['sales_receipt'] and again['id'] == first['id']
    assert again['change'] == first['change'] and again['customer'] is None
    # The same ticket with another cart is refused; nothing more posts.
    other = dict(body, lines=[{'item': 'Mainline Clearing'}])
    assert co.fails('pos sale complete', other).code == 'E_IDEMPOTENCY_MISMATCH'
    assert len(co.run('sales-receipt query', {'limit': 200})['items']) == before + 1
    # A generic idempotency key on top changes nothing.
    keyed = co.run('pos sale complete', body, idempotency_key='retry-key-1')
    assert keyed['sales_receipt'] == first['sales_receipt']
    assert len(co.run('sales-receipt query', {'limit': 200})['items']) == before + 1


def test_the_register_fixes_the_facts_and_the_refusals(twins):
    co, _ = twins
    off = co.fails('pos sale preview', {'lines': [{'item': 'Mainline Clearing'}]})
    assert off.code == 'E_FEATURE_DISABLED'
    reg = _setup(co)
    cart = {'lines': [{'item': 'Mainline Clearing'}]}
    total = co.run('pos sale preview', cart)['total']['amount']
    before = len(co.run('sales-receipt query', {'limit': 200})['items'])
    # The facts a register fixes are not inputs: no account, date, site or document can be named.
    for extra in ({'deposit_to': 'Checking'}, {'date': '2020-01-01'}, {'site': 'Warehouse'}, {'number': 'X1'}):
        assert co.fails('pos sale complete', dict(cart, ticket='t-x', tender={'payment_method': 'Cash'},
                                                  expected_total=total, **extra)).code == 'E_VALIDATION'
    # What was shown is what posts: another total posts nothing.
    stale = co.fails('pos sale complete', dict(cart, ticket='t-stale', tender={'payment_method': 'Cash', 'amount': '500'},
                                               expected_total='1.00'))
    assert stale.code == 'E_PREVIEW_STALE' and stale.details['total'] == total
    short = co.fails('pos sale complete', dict(cart, ticket='t-short', tender={'payment_method': 'Cash', 'amount': '1'},
                                               expected_total=total))
    assert short.code == 'E_VALIDATION'
    part = co.fails('pos sale complete', dict(cart, ticket='t-part', tender={'payment_method': 'Visa', 'amount': '1'},
                                              expected_total=total))
    assert part.code == 'E_VALIDATION'
    gift = co.fails('pos sale complete', dict(cart, ticket='t-gift', tender={'payment_method': 'Gift Card'},
                                              expected_total=total))
    assert gift.code == 'E_VALIDATION' and 'does not offer' in json.dumps(gift.details)
    empty = co.fails('pos sale complete', dict(lines=[], ticket='t-empty', tender={'payment_method': 'Cash'},
                                               expected_total='0'))
    assert empty.code == 'E_VALIDATION'
    assert len(co.run('sales-receipt query', {'limit': 200})['items']) == before
    # A card sale: the total, with the reference stored as the receipt's payment reference.
    sale = co.run('pos sale complete', dict(cart, ticket='t-card', tender={'payment_method': 'Visa', 'reference': '4242'},
                                            expected_total=total))
    shown = co.run('sales-receipt show', {'sales_receipt': sale['sales_receipt']['id']})
    assert shown['revision']['profile']['payment_reference'] == '4242' if 'payment_reference' in shown['revision']['profile'] \
        else json.dumps(shown).count('4242') >= 1
    assert sale['tendered']['amount'] == total and sale['change']['amount'] == '0.00'
    assert json.dumps(shown).count(reg['deposit_account']['id']) >= 1
    # The audit names the POS command.
    events = co.run('audit list', {'limit': 20})['items']
    assert any(e['command'] == 'pos sale complete' for e in events)


def test_customers_at_the_till(twins):
    co, _ = twins
    _setup(co)
    found = co.run('pos customer search', {'q': '555-0110'})['items']
    assert found and found[0]['name'] == 'Riverside Apartments' and found[0]['phone'] == '555-0110'
    assert co.run('pos customer search', {'q': 'walk'})['items'] == []   # the walk-in is never offered
    made = co.run('pos customer add', {'name': 'Sam Shopper', 'phone': '(555) 010-9988', 'email': 'sam@example.com'})
    assert co.run('pos customer search', {'q': '5550109988'})['items'] == [made]
    assert co.run('pos customer search', {'q': 'sam@'})['items'] == [made]
    assert co.fails('pos customer add', {'name': 'Sam Shopper'}).code == 'E_NAME_TAKEN'
    # Choosing a customer re-prices with their price level and tax code.
    cart = {'lines': [{'item': 'Brass Shutoff Valve', 'quantity': '4'}]}
    walk = co.run('pos sale preview', cart)
    exempt = co.run('pos sale preview', dict(cart, customer='Eddie Exempt'))
    preferred = co.run('pos sale preview', dict(cart, customer='Pat Preferred'))
    assert exempt['tax']['minor_units'] == 0 and walk['tax']['minor_units'] > 0
    assert preferred['subtotal']['minor_units'] < walk['subtotal']['minor_units']


def test_a_pos_clerk_rings_up_and_reaches_nothing_else(hosted):
    """The POS clerk preset (readonly, pos.sell) completes a sale over HTTP: the facts are the register's, a price
    typed over an item's own price is refused without pos.price-override, an unpriced item takes one, and the books'
    own command stays refused."""
    from fastapi.testclient import TestClient
    from bookflow.hub import pos_presets
    from tests.test_item_pictures_review import _membership_version
    from tests.test_pos_layer import H, REASON as HEADERS, _password, _pos
    cid = hosted.company_id
    _pos(hosted, True)
    clerk = pos_presets.preset('pos-clerk')
    user = hosted.ok('user.add', {'username': 'till', 'password': _password('till'), 'company': cid,
                                  'role': 'readonly'}, headers=HEADERS)['user_id']
    hosted.ok('membership.grant', {'user': user, 'company': cid, 'role': 'readonly', 'grants': clerk['grants'],
                                   'denies': clerk['denies'], 'expected_version': _membership_version(hosted, user)},
              headers=HEADERS)
    till = TestClient(hosted.handle.app)
    assert till.post('/login', json={'username': 'till', 'password': _password('till')}).status_code == 200

    def call(name, body):
        return till.post(f'/companies/{cid}/commands/{name}', headers=H, json=body)

    cart = {'lines': [{'item': 'Pipe Sealant', 'quantity': '2'}]}
    shown = call('pos.sale.preview', cart)
    assert shown.status_code == 200, shown.text
    total = shown.json()['total']['amount']
    sale = call('pos.sale.complete', dict(cart, ticket='clerk-1', expected_total=total,
                                          tender={'payment_method': 'Cash', 'amount': '100'}))
    assert sale.status_code == 200, sale.text
    assert sale.json()['customer'] is None and sale.json()['clerk_id'] == user
    receipt = hosted.ok('sales-receipt.show', {'sales_receipt': sale.json()['sales_receipt']['id']}, company=cid)
    assert receipt['customer_name'] == 'Walk-in' and receipt['total']['amount'] == total
    # A typed price over the sealant's own: pos.price-override, which the clerk does not hold.
    over = call('pos.sale.preview', {'lines': [{'item': 'Pipe Sealant', 'unit_price': '1.00'}]})
    assert over.status_code == 403 and over.json()['details']['capability'] == 'pos.price-override', over.text
    # An item with no price takes one typed at the till.
    unpriced = call('pos.sale.preview', {'lines': [{'item': 'Service Call Kit', 'unit_price': '35.00'}]})
    assert unpriced.status_code == 200 and unpriced.json()['subtotal']['amount'] == '35.00', unpriced.text
    # Customers at the till: found and added without the customer list.
    assert call('pos.customer.search', {'q': 'Riverside'}).json()['items'][0]['name'] == 'Riverside Apartments'
    assert call('pos.customer.add', {'name': 'Casey Counter'}).status_code == 200
    assert call('customer.list', {}).status_code == 403
    refused = call('sales-receipt.post', {'customer': 'Walk-in', 'deposit_to': 'Undeposited Funds',
                                          'lines': [{'item': 'Pipe Sealant'}]})
    assert refused.status_code == 403, refused.text
    # The POS screen opens for the clerk, carrying the items it rings up and no cost.
    page = till.get(f'/c/{cid}/pos')
    assert page.status_code == 200 and 'id="pos-data"' in page.text and 'average_cost' not in page.text
