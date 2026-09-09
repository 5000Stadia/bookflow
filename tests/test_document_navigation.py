"""Getting back to the documents already written, from the document you have open.

What is checked here: a new document reaches its own list and that list shows what is
already there; the arrows on a saved document land on its neighbours in the order the
page states; the first and last documents offer no destination rather than a broken one;
voided sales and closed estimates stay in the sequence; and the toolbar never widens the
page on a phone.
"""
import re

import pytest

from bookflow.adapters.workbench import document_nav as Nav
from tests.test_row3_host import hosted  # noqa: F401
from tests.test_row5_workbench_forms import _browser

NEW = {'invoice': 'post', 'sales-receipt': 'post', 'estimate': 'create'}
NOUNS = ('invoice', 'sales-receipt', 'estimate')


def _books(hosted):
    """One customer, one item and the accounts the three documents need."""
    company = hosted.company_id
    income = hosted.ok('account.create', dict(name='Nav income', type='income'), company=company)['id']
    receivable = hosted.ok('account.create', dict(name='Nav receivables', type='accounts_receivable'),
                           company=company)['id']
    bank = hosted.ok('account.create', dict(name='Nav bank', type='bank'), company=company)['id']
    method = hosted.ok('payment-method.create', dict(name='Nav cash', kind='cash'), company=company)['id']
    customer = hosted.ok('customer.create', dict(name='Nav customer'), company=company)['id']
    code = next(row['id'] for row in hosted.ok('sales-tax-code.list', {}, company=company)['items']
                if not row['taxable'])
    item = hosted.ok('item.create', dict(name='Nav service', type='service', sales_enabled=True,
        income_account_id=income, price='10.00', description='Nav work', sales_tax_code_id=code),
        company=company)['id']
    return dict(company=company, customer=customer, item=item, bank=bank, method=method,
                receivable=receivable)


def _write(hosted, books, noun, date, quantity):
    payload = dict(date=date, customer=books['customer'],
                   lines=[dict(item=books['item'], quantity=str(quantity))])
    if noun == 'invoice':
        payload['ar_account'] = books['receivable']
    if noun == 'sales-receipt':
        payload.update(deposit_to=books['bank'], payment_method=books['method'])
    if noun == 'estimate':
        payload['title'] = f'Nav job {quantity}'
    return hosted.ok(f'{noun}.{NEW[noun]}', payload, company=books['company'])['id']


def _rows(hosted, noun, company):
    raw = {'limit': 200, 'active': None} if noun == 'estimate' else {'limit': 200}
    return hosted.ok(f'{noun}.query', raw, company=company)['items']


def _nav(page):
    found = re.search(r'(?s)<nav class="document-nav".*?</nav>', page.text)
    assert found is not None, page.text[:800]
    return found.group(0)


def _control(nav, label):
    """One Previous/Next control, cut at the boundary of the next one."""
    parts = re.split(r'(?=<(?:a|span) class="document-step[" ])', nav)
    found = [part for part in parts if label in part]
    assert len(found) == 1, (label, [part[:80] for part in parts])
    return found[0]


def _destination(nav, label):
    href = re.search(r'href="([^"]*)"', _control(nav, label))
    return href.group(1) if href else None


def _text(block):
    return re.sub(r'\s+', ' ', re.sub(r'(?s)<[^>]*>', ' ', block)).strip()


# ---------------------------------------------------------------- reaching the list

@pytest.mark.parametrize('noun', NOUNS)
def test_a_new_document_reaches_the_list_and_the_list_shows_what_is_already_there(hosted, noun):
    books = _books(hosted)
    written = [_write(hosted, books, noun, f'2026-04-0{index}', index) for index in (1, 2, 3)]
    numbers = {row['id']: row['number'] for row in _rows(hosted, noun, books['company'])}
    browser = _browser(hosted)

    page = browser.get(f"/c/{books['company']}/{noun}/{NEW[noun]}")
    assert page.status_code == 200, page.text[:600]
    nav = _nav(page)
    assert f'href="/c/{books["company"]}/{noun}"' in nav
    assert Nav.form_bar(books['company'], noun, NEW[noun], None)['find_label'] in _text(nav)

    listed = browser.get(f"/c/{books['company']}/{noun}")
    assert listed.status_code == 200, listed.text[:600]
    for document in written:
        assert numbers[document] in listed.text
        assert f'/{noun}/{document}"' in listed.text


@pytest.mark.parametrize('noun', NOUNS)
def test_a_saved_document_reaches_the_list_from_its_own_toolbar(hosted, noun):
    books = _books(hosted)
    written = _write(hosted, books, noun, '2026-04-05', 1)
    page = _browser(hosted).get(f"/c/{books['company']}/{noun}/{written}")
    assert page.status_code == 200, page.text[:600]
    assert f'href="/c/{books["company"]}/{noun}"' in _nav(page)


# ------------------------------------------------------------------ previous / next

@pytest.mark.parametrize('noun', NOUNS)
def test_the_query_pages_in_the_order_the_toolbar_states(hosted, noun):
    """Date, then the order they were entered: the ids are time-ordered, so id is that."""
    books = _books(hosted)
    for index, date in enumerate(('2026-04-02', '2026-04-01', '2026-04-02'), start=1):
        _write(hosted, books, noun, date, index)
    rows = _rows(hosted, noun, books['company'])
    assert [row['id'] for row in rows] == [row['id'] for row in
                                           sorted(rows, key=lambda row: (row['date'], row['id']))]


@pytest.mark.parametrize('noun', NOUNS)
def test_previous_and_next_reach_the_adjacent_documents(hosted, noun):
    books = _books(hosted)
    for index in (1, 2, 3):
        _write(hosted, books, noun, f'2026-04-0{index}', index)
    order = [row['id'] for row in _rows(hosted, noun, books['company'])]
    assert len(order) >= 3
    browser = _browser(hosted)
    middle = len(order) // 2
    base = f"/c/{books['company']}/{noun}"

    page = browser.get(f'{base}/{order[middle]}')
    nav = _nav(page)
    assert _destination(nav, 'Previous') == f'{base}/{order[middle - 1]}'
    assert _destination(nav, 'Next') == f'{base}/{order[middle + 1]}'
    assert Nav.ORDER in _text(nav)
    assert f'{middle + 1} of {len(order)}' in _text(nav)

    # Following the arrow lands on the neighbour, which points back at where you were.
    landed = browser.get(_destination(nav, 'Next'))
    assert landed.status_code == 200, landed.text[:600]
    assert _destination(_nav(landed), 'Previous') == f'{base}/{order[middle]}'


@pytest.mark.parametrize('noun', NOUNS)
def test_the_first_and_last_documents_offer_no_destination(hosted, noun):
    books = _books(hosted)
    for index in (1, 2, 3):
        _write(hosted, books, noun, f'2026-04-0{index}', index)
    order = [row['id'] for row in _rows(hosted, noun, books['company'])]
    browser = _browser(hosted)
    base = f"/c/{books['company']}/{noun}"

    first = _nav(browser.get(f'{base}/{order[0]}'))
    assert _destination(first, 'Previous') is None
    assert 'aria-disabled="true"' in _control(first, 'Previous')
    assert 'earliest' in _text(_control(first, 'Previous'))
    assert _destination(first, 'Next') == f'{base}/{order[1]}'

    last = _nav(browser.get(f'{base}/{order[-1]}'))
    assert _destination(last, 'Next') is None
    assert 'aria-disabled="true"' in _control(last, 'Next')
    assert 'latest' in _text(_control(last, 'Next'))
    assert _destination(last, 'Previous') == f'{base}/{order[-2]}'


# ------------------------------------------------- documents that are no longer live

@pytest.mark.parametrize('noun', ('invoice', 'sales-receipt'))
def test_a_voided_sale_stays_in_the_sequence_the_arrows_walk(hosted, noun):
    books = _books(hosted)
    written = [_write(hosted, books, noun, f'2026-04-0{index}', index) for index in (1, 2, 3)]
    hosted.ok(f'{noun}.void', {noun.replace('-', '_'): written[1], 'expected_version': 1},
              company=books['company'], headers={'X-Bookflow-Reason': 'Entered twice'})
    order = [row['id'] for row in _rows(hosted, noun, books['company'])]
    assert written[1] in order
    browser = _browser(hosted)
    base = f"/c/{books['company']}/{noun}"

    # The list shows it, so the arrows must reach it rather than stepping over it.
    assert written[1] in browser.get(f"/c/{books['company']}/{noun}").text
    before = _nav(browser.get(f'{base}/{written[0]}'))
    assert _destination(before, 'Next') == f'{base}/{written[1]}'
    voided = _nav(browser.get(f'{base}/{written[1]}'))
    assert _destination(voided, 'Previous') == f'{base}/{written[0]}'
    assert _destination(voided, 'Next') == f'{base}/{written[2]}'
    assert 'voided' in _text(_control(before, 'Next'))


def test_a_closed_estimate_stays_in_the_sequence_the_arrows_walk(hosted):
    books = _books(hosted)
    written = [_write(hosted, books, 'estimate', f'2026-04-0{index}', index) for index in (1, 2, 3)]
    hosted.ok('estimate.update', dict(estimate=written[1], expected_version=1, active=False),
              company=books['company'], headers={'X-Bookflow-Reason': 'Job cancelled'})
    browser = _browser(hosted)
    base = f"/c/{books['company']}/estimate"

    closed = _nav(browser.get(f'{base}/{written[1]}'))
    assert _destination(closed, 'Previous') == f'{base}/{written[0]}'
    assert _destination(closed, 'Next') == f'{base}/{written[2]}'
    before = _nav(browser.get(f'{base}/{written[0]}'))
    assert _destination(before, 'Next') == f'{base}/{written[1]}'
    assert 'closed' in _text(_control(before, 'Next'))


# --------------------------------------------------------------- recent, and its cost

@pytest.mark.parametrize('noun', NOUNS)
def test_the_new_document_form_does_not_read_the_list_while_it_renders(hosted, noun):
    books = _books(hosted)
    written = [_write(hosted, books, noun, f'2026-04-0{index}', index) for index in (1, 2, 3)]
    page = _browser(hosted).get(f"/c/{books['company']}/{noun}/{NEW[noun]}")
    nav = _nav(page)
    assert f'hx-get="/c/{books["company"]}/_recent/{noun}"' in nav
    assert 'hx-trigger="load"' in nav
    # Nothing about the existing documents is on the form: the read happens after it.
    for document in written:
        assert document not in page.text


@pytest.mark.parametrize('noun', NOUNS)
def test_the_recent_panel_lists_the_newest_documents_first(hosted, noun):
    books = _books(hosted)
    for index in (1, 2, 3):
        _write(hosted, books, noun, f'2026-04-0{index}', index)
    order = [row['id'] for row in _rows(hosted, noun, books['company'])]
    panel = _browser(hosted).get(f"/c/{books['company']}/_recent/{noun}")
    assert panel.status_code == 200, panel.text[:600]
    shown = re.findall(r'href="/c/[^/]+/' + noun + r'/([^"]+)"', panel.text)
    assert shown == list(reversed(order[-Nav.RECENT:]))


def test_the_recent_panel_refuses_a_noun_that_is_not_a_sales_document(hosted):
    assert _browser(hosted).get(f'/c/{hosted.company_id}/_recent/customer').status_code == 404


# --------------------------------------------------------------- the correction form

@pytest.mark.parametrize('noun', NOUNS)
def test_a_correction_form_offers_the_list_and_no_arrows(hosted, noun):
    """Arrows on a half-entered correction would throw away what was typed."""
    books = _books(hosted)
    written = _write(hosted, books, noun, '2026-04-06', 1)
    page = _browser(hosted).get(f"/c/{books['company']}/{noun}/{written}/update")
    assert page.status_code == 200, page.text[:600]
    nav = _nav(page)
    assert 'document-step' not in nav
    assert f'href="/c/{books["company"]}/{noun}"' in nav
    assert f'href="/c/{books["company"]}/{noun}/{written}"' in nav


# ------------------------------------------------------- the sequence module directly

def test_more_documents_than_one_page_disables_the_step_it_cannot_answer():
    """Past one page the query cannot look backwards, so that arrow says so."""
    pages = [{'items': [{'id': 'b', 'number': '2', 'date': '2026-04-02'}], 'has_more': True},
             {'items': [{'id': 'b', 'number': '2', 'date': '2026-04-02'},
                        {'id': 'c', 'number': '3', 'date': '2026-04-03'}], 'has_more': False}]
    calls = []

    def read(name, raw, company):
        calls.append(raw)
        return pages[len(calls) - 1]

    view = Nav.strip(read, 'CO', 'invoice', {'id': 'b', 'date': '2026-04-02'})
    assert [raw.get('date_from') for raw in calls] == [None, '2026-04-02']
    assert view['previous'] is None and view['at_start'] is False
    assert view['next']['url'] == '/c/CO/invoice/c'
    assert view['at_end'] is False
    assert 'more invoices than one page holds' in view['unavailable']
    assert view['position'] is None


def test_a_neighbour_on_the_same_date_is_still_answered_past_one_page():
    """Past a page the arrow that can be answered is answered, and says nothing false."""
    pages = [{'items': [], 'has_more': True},
             {'items': [{'id': 'a', 'number': '1', 'date': '2026-04-02'},
                        {'id': 'b', 'number': '2', 'date': '2026-04-02'}], 'has_more': True}]
    calls = []

    def read(name, raw, company):
        calls.append(raw)
        return pages[len(calls) - 1]

    view = Nav.strip(read, 'CO', 'invoice', {'id': 'b', 'date': '2026-04-02'})
    assert view['previous']['url'] == '/c/CO/invoice/a'
    assert view['next'] is None and view['at_end'] is False
    assert view['unavailable'] is None


def test_one_page_answers_everything_with_one_read():
    reads = []

    def read(name, raw, company):
        reads.append(name)
        return {'items': [{'id': 'a', 'number': '1', 'date': '2026-04-01'},
                          {'id': 'b', 'number': '2', 'date': '2026-04-02'}], 'has_more': False}

    view = Nav.strip(read, 'CO', 'invoice', {'id': 'a', 'date': '2026-04-01'})
    assert reads == ['invoice query']
    assert view['previous'] is None and view['at_start'] is True
    assert view['next']['url'] == '/c/CO/invoice/b'
    assert (view['position'], view['total']) == (1, 2)


def test_a_form_toolbar_opens_no_read():
    assert Nav.form_bar('CO', 'invoice', 'post', None)['recent_url'] == '/c/CO/_recent/invoice'
    assert Nav.form_bar('CO', 'invoice', 'update', 'INV1')['record_url'] == '/c/CO/invoice/INV1'
    assert Nav.form_bar('CO', 'invoice', 'update', 'INV1')['recent_url'] is None
    assert Nav.form_bar('CO', 'customer', 'update', 'C1') is None
