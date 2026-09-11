"""Getting back to what was already written, from a list that reads like a list.

What is checked here: a document list opens on the most recent document and pages both
ways; a cursor minted in one order cannot silently continue in the other; no page is
titled with the name of the command behind it; no column is headed with the name of the
field behind it; the estimate list's availability filter can show active, inactive or
both; and the two document-toolbar controls that were disabled past one page answer
past one page.
"""
import re

import pytest

from bookflow.adapters.workbench import document_nav as Nav
from bookflow.adapters.workbench import list_paging as Paging
from bookflow.adapters.workbench import naming as Naming
from bookflow.core import registry
from tests.test_row3_host import hosted  # noqa: F401
from tests.test_row5_workbench_forms import _browser

NEW = {'invoice': 'post', 'sales-receipt': 'post', 'estimate': 'create'}
NOUNS = ('invoice', 'sales-receipt', 'estimate')


# ------------------------------------------------------------------------- the books

def _books(hosted):
    company = hosted.company_id
    income = hosted.ok('account.create', dict(name='Polish income', type='income'), company=company)['id']
    receivable = hosted.ok('account.create', dict(name='Polish receivables', type='accounts_receivable'),
                           company=company)['id']
    bank = hosted.ok('account.create', dict(name='Polish bank', type='bank'), company=company)['id']
    method = hosted.ok('payment-method.create', dict(name='Polish cash', kind='cash'), company=company)['id']
    customer = hosted.ok('customer.create', dict(name='Polish customer'), company=company)['id']
    code = next(row['id'] for row in hosted.ok('sales-tax-code.list', {}, company=company)['items']
                if not row['taxable'])
    item = hosted.ok('item.create', dict(name='Polish service', type='service', sales_enabled=True,
        income_account_id=income, price='10.00', description='Polish work', sales_tax_code_id=code),
        company=company)['id']
    return dict(company=company, customer=customer, item=item, bank=bank, method=method,
                receivable=receivable, income=income)


def _write(hosted, books, noun, date, quantity):
    payload = dict(date=date, customer=books['customer'],
                   lines=[dict(item=books['item'], quantity=str(quantity))])
    if noun == 'invoice':
        payload['ar_account'] = books['receivable']
    if noun == 'sales-receipt':
        payload.update(deposit_to=books['bank'], payment_method=books['method'])
    if noun == 'estimate':
        payload['title'] = f'Polish job {quantity}'
    return hosted.ok(f'{noun}.{NEW[noun]}', payload, company=books['company'])['id']


def _rows(hosted, noun, company):
    raw = {'limit': 200, 'active': None} if noun == 'estimate' else {'limit': 200}
    return [row['id'] for row in hosted.ok(f'{noun}.query', raw, company=company)['items']]


def _listed(page, noun):
    """The document ids the list shows, in the order the rows appear."""
    return list(dict.fromkeys(re.findall(r'href="/c/[^/"]+/' + noun + r'/([0-9A-HJKMNP-TV-Z]{26})"', page.text)))


# ------------------------------------------------- the list opens on the newest first

@pytest.mark.parametrize('noun', NOUNS)
def test_the_document_list_opens_on_the_most_recent_document(hosted, noun):
    books = _books(hosted)
    for index in (1, 2, 3):
        _write(hosted, books, noun, f'2026-04-0{index}', index)
    oldest_first = _rows(hosted, noun, books['company'])
    assert len(oldest_first) >= 3

    page = _browser(hosted).get(f"/c/{books['company']}/{noun}")
    assert page.status_code == 200, page.text[:600]
    assert _listed(page, noun) == list(reversed(oldest_first))


@pytest.mark.parametrize('noun', NOUNS)
def test_the_list_can_be_turned_back_to_oldest_first(hosted, noun):
    books = _books(hosted)
    for index in (1, 2, 3):
        _write(hosted, books, noun, f'2026-04-0{index}', index)
    oldest_first = _rows(hosted, noun, books['company'])

    page = _browser(hosted).get(f"/c/{books['company']}/{noun}?direction=asc")
    assert page.status_code == 200, page.text[:600]
    assert _listed(page, noun) == oldest_first


def test_a_list_that_is_not_a_document_list_keeps_the_order_it_had(hosted):
    """Only the documents a bookkeeper writes open on the newest one."""
    from bookflow.adapters.workbench import document_form as Document
    from bookflow.adapters.workbench import work as Work
    assert set(Paging.NEWEST_FIRST) == set(Document.NOUNS) | set(Work.NOUNS)

    books = _books(hosted)
    for day in ('2026-04-01', '2026-04-02'):
        hosted.ok('journal.post', dict(date=day, lines=[
            dict(account=books['bank'], side='debit', amount='10.00'),
            dict(account=books['income'], side='credit', amount='10.00')]),
            company=books['company'])
    natural = [row['id'] for row in
               hosted.ok('journal.query', {'limit': 200}, company=books['company'])['items']]
    assert len(natural) >= 2

    page = _browser(hosted).get(f"/c/{books['company']}/journal")
    assert page.status_code == 200, page.text[:600]
    assert _listed(page, 'journal') == natural


# ---------------------------------------------------------------------- paging, both ways

@pytest.mark.parametrize('noun', NOUNS)
def test_paging_backwards_reaches_the_page_it_came_from(hosted, noun):
    books = _books(hosted)
    for index in (1, 2, 3, 4):
        _write(hosted, books, noun, f'2026-04-0{index}', index)
    browser = _browser(hosted)
    base = f"/c/{books['company']}/{noun}?limit=2"

    first = browser.get(base)
    assert first.status_code == 200, first.text[:600]
    assert '← Previous page' not in first.text
    forward = re.search(r'href="([^"]*)" rel="next"', first.text)
    assert forward is not None, first.text[-1500:]

    second = browser.get(forward.group(1).replace('&amp;', '&'))
    assert second.status_code == 200, second.text[:600]
    assert _listed(second, noun) and _listed(second, noun) != _listed(first, noun)
    backward = re.search(r'href="([^"]*)" rel="prev"', second.text)
    assert backward is not None, second.text[-1500:]

    back = browser.get(backward.group(1).replace('&amp;', '&'))
    assert back.status_code == 200, back.text[:600]
    assert _listed(back, noun) == _listed(first, noun)


def test_paging_forward_and_back_keeps_every_other_control(hosted):
    books = _books(hosted)
    for index in (1, 2, 3, 4):
        _write(hosted, books, 'invoice', f'2026-04-0{index}', index)
    browser = _browser(hosted)

    first = browser.get(f"/c/{books['company']}/invoice?limit=2&direction=asc")
    forward = re.search(r'href="([^"]*)" rel="next"', first.text).group(1).replace('&amp;', '&')
    assert 'direction=asc' in forward
    second = browser.get(forward)
    backward = re.search(r'href="([^"]*)" rel="prev"', second.text).group(1).replace('&amp;', '&')
    assert 'direction=asc' in backward
    assert _listed(browser.get(backward), 'invoice') == _listed(first, 'invoice')


def test_the_walk_back_is_built_from_the_address_alone():
    """Page one needs no cursor, so page two steps back to the list with none."""
    from starlette.datastructures import QueryParams

    page_one = Paging.controls('/c/CO/invoice', QueryParams('limit=2&direction=desc'), 'CURSOR2')
    assert page_one['previous_url'] is None and page_one['first_url'] is None
    assert page_one['page'] == 1 and 'cursor=CURSOR2' in page_one['next_url']
    assert 'direction=desc' in page_one['next_url']

    page_two = Paging.controls('/c/CO/invoice', QueryParams('limit=2&cursor=CURSOR2&page=2'), 'CURSOR3')
    assert page_two['previous_url'] == '/c/CO/invoice?limit=2'
    assert 'trail=CURSOR2' in page_two['next_url']

    page_three = Paging.controls('/c/CO/invoice',
                                 QueryParams('limit=2&trail=CURSOR2&cursor=CURSOR3&page=3'), None)
    assert page_three['next_url'] is None
    assert 'cursor=CURSOR3' not in page_three['previous_url']
    assert 'cursor=CURSOR2' in page_three['previous_url']
    assert page_three['first_url'] == '/c/CO/invoice?limit=2'


def test_the_trail_is_bounded_and_says_so_by_offering_the_first_page():
    """A URL cannot grow forever, so past DEPTH steps the walk back ends honestly."""
    from starlette.datastructures import QueryParams

    trail = '&'.join(f'trail=C{index}' for index in range(Paging.DEPTH))
    deep = Paging.controls('/c/CO/invoice', QueryParams(f'{trail}&cursor=LAST&page=99'), 'NEXT')
    assert deep['next_url'].count('trail=') == Paging.DEPTH
    assert 'trail=C0' not in deep['next_url']

    lost = Paging.controls('/c/CO/invoice', QueryParams('cursor=LAST&page=99'), None)
    assert lost['previous_url'] is None
    assert lost['first_url'] == '/c/CO/invoice'


# ------------------------------------------------------- a cursor belongs to its order

@pytest.mark.parametrize('noun', NOUNS)
def test_a_cursor_cannot_silently_page_under_a_changed_direction(hosted, noun):
    books = _books(hosted)
    for index in (1, 2, 3, 4):
        _write(hosted, books, noun, f'2026-04-0{index}', index)
    raw = dict(limit=2, direction='asc', **({'active': None} if noun == 'estimate' else {}))
    first = hosted.ok(f'{noun}.query', raw, company=books['company'])
    assert first['next_cursor']

    same = hosted.ok(f'{noun}.query', dict(raw, cursor=first['next_cursor']), company=books['company'])
    assert same['items'] and same['items'][0]['id'] not in [row['id'] for row in first['items']]

    swapped = hosted.call(f'{noun}.query', dict(raw, direction='desc', cursor=first['next_cursor']),
                          company=books['company'])
    assert swapped.status_code == 422, swapped.text
    assert swapped.json()['code'] == 'E_VALIDATION', swapped.text
    assert 'cursor' in swapped.text


# ------------------------------------------------------ no page titled with a command

def test_no_registered_command_is_offered_as_a_page_title():
    registry.load_all()
    offenders = []
    for cmd in registry.all_commands(include_standalone=True):
        title = Naming.heading(cmd.noun, cmd.verb, registry.noun_meta(cmd.noun))
        if title == cmd.name or '_' in title or '-' in title:
            offenders.append((cmd.name, title))
    assert offenders == []


def test_the_ar_aging_page_is_titled_with_the_report_not_the_command(hosted):
    page = _browser(hosted).get(f'/c/{hosted.company_id}/report/ar-aging')
    assert page.status_code == 200, page.text[:600]
    assert re.findall(r'<h1>([^<]*)</h1>', page.text) == ['A/R aging summary']
    assert 'report ar-aging' not in page.text


@pytest.mark.parametrize('verb, title', [('trial-balance', 'Trial balance'),
                                         ('general-ledger', 'General ledger'),
                                         ('open-invoices', 'Open invoices'),
                                         ('ap-aging', 'A/P aging summary'),
                                         ('unpaid-bills', 'Unpaid bills'),
                                         ('profit-and-loss', 'Profit and loss'),
                                         ('balance-sheet', 'Balance sheet')])
def test_every_report_page_is_titled_with_the_report(hosted, verb, title):
    page = _browser(hosted).get(f'/c/{hosted.company_id}/report/{verb}')
    assert page.status_code == 200, page.text[:600]
    assert re.findall(r'<h1>([^<]*)</h1>', page.text) == [title]
    assert f'report {verb}' not in page.text


@pytest.mark.parametrize('noun, verb, title', [('customer', 'create', 'New customer or job'),
                                               ('invoice', 'post', 'New invoice'),
                                               ('estimate', 'create', 'New estimate'),
                                               ('journal', 'post', 'New journal')])
def test_a_form_page_is_titled_with_the_work_it_does(hosted, noun, verb, title):
    page = _browser(hosted).get(f'/c/{hosted.company_id}/{noun}/{verb}')
    assert page.status_code == 200, page.text[:600]
    assert title in re.findall(r'<h1>([^<]*)</h1>', page.text)
    assert f'{noun} {verb}' not in re.findall(r'<h1>([^<]*)</h1>', page.text)


@pytest.mark.parametrize('noun, title', [('invoice', 'Invoices'), ('estimate', 'Estimates'),
                                         ('customer', 'Customers and jobs'), ('report', 'Reports')])
def test_a_list_page_is_titled_with_the_records(hosted, noun, title):
    page = _browser(hosted).get(f'/c/{hosted.company_id}/{noun}')
    assert page.status_code == 200, page.text[:600]
    assert re.findall(r'<h1>([^<]*)</h1>', page.text) == [title]


# ---------------------------------------------------- no column headed with a field name

@pytest.mark.parametrize('noun', ('invoice', 'sales-receipt', 'estimate', 'journal', 'rate'))
def test_no_list_column_is_headed_with_a_field_name(hosted, noun):
    books = _books(hosted)
    if noun in NOUNS:
        _write(hosted, books, noun, '2026-04-01', 1)
    page = _browser(hosted).get(f"/c/{books['company']}/{noun}")
    assert page.status_code == 200, page.text[:600]
    heads = re.findall(r'<th[^>]*>([^<]*)</th>', page.text)
    assert heads, page.text[:600]
    assert not [head for head in heads if '_' in head]


def test_the_invoice_list_names_its_columns_the_way_a_bookkeeper_does(hosted):
    books = _books(hosted)
    _write(hosted, books, 'invoice', '2026-04-01', 1)
    page = _browser(hosted).get(f"/c/{books['company']}/invoice")
    assert re.findall(r'<th[^>]*>([^<]*)</th>', page.text) == [
        'Number', 'Date', 'Customer', 'Due date', 'Total', 'Status']


def test_a_stored_amount_column_is_headed_with_what_it_holds():
    assert Naming.column_label('total_minor_units') == 'Total'
    assert Naming.column_label('customer_name') == 'Customer'
    assert Naming.column_label('due_date') == 'Due date'


# --------------------------------------------------------- availability shows both sets

def test_the_estimate_availability_filter_can_show_active_inactive_or_both(hosted):
    books = _books(hosted)
    written = [_write(hosted, books, 'estimate', f'2026-04-0{index}', index) for index in (1, 2)]
    hosted.ok('estimate.update', dict(estimate=written[0], expected_version=1, active=False),
              company=books['company'], headers={'X-Bookflow-Reason': 'Job cancelled'})
    browser = _browser(hosted)
    base = f"/c/{books['company']}/estimate"

    active = _listed(browser.get(f'{base}?active=true'), 'estimate')
    assert written[1] in active and written[0] not in active

    inactive = _listed(browser.get(f'{base}?active=false'), 'estimate')
    assert written[0] in inactive and written[1] not in inactive

    both = browser.get(f'{base}?active=unset')
    assert both.status_code == 200, both.text[:600]
    shown = _listed(both, 'estimate')
    assert written[0] in shown and written[1] in shown
    assert 'value="unset"' in both.text and '>All<' in both.text


def test_the_availability_control_shows_which_of_the_three_is_in_force(hosted):
    browser = _browser(hosted)
    base = f'/c/{hosted.company_id}/estimate'

    def chosen(page):
        block = re.search(r'(?s)<select name="active">(.*?)</select>', page.text).group(1)
        return re.findall(r'value="([a-z]+)"[^>]*selected', block)

    assert chosen(browser.get(base)) == ['true']
    assert chosen(browser.get(f'{base}?active=false')) == ['false']
    assert chosen(browser.get(f'{base}?active=unset')) == ['unset']


# ------------------------------------- the toolbar controls that were disabled past a page

@pytest.mark.parametrize('noun', NOUNS)
def test_the_step_back_is_answered_past_one_page(hosted, noun, monkeypatch):
    """Company size no longer decides whether the arrow before this one exists."""
    monkeypatch.setattr(Nav, 'PAGE', 2)
    books = _books(hosted)
    for index in (1, 2, 3, 4):
        _write(hosted, books, noun, f'2026-04-0{index}', index)
    order = _rows(hosted, noun, books['company'])
    base = f"/c/{books['company']}/{noun}"
    page = _browser(hosted).get(f'{base}/{order[2]}')
    assert page.status_code == 200, page.text[:600]
    nav = re.search(r'(?s)<nav class="document-nav".*?</nav>', page.text).group(0)

    assert f'href="{base}/{order[1]}"' in nav
    assert f'href="{base}/{order[3]}"' in nav
    assert 'more ' not in nav
    assert 'could not be placed' not in nav


@pytest.mark.parametrize('noun', NOUNS)
def test_the_recent_panel_is_answered_past_one_page(hosted, noun, monkeypatch):
    monkeypatch.setattr(Nav, 'PAGE', 2)
    books = _books(hosted)
    for index in (1, 2, 3, 4):
        _write(hosted, books, noun, f'2026-04-0{index}', index)
    order = _rows(hosted, noun, books['company'])
    panel = _browser(hosted).get(f"/c/{books['company']}/_recent/{noun}")
    assert panel.status_code == 200, panel.text[:600]
    shown = re.findall(r'href="/c/[^/"]+/' + noun + r'/([0-9A-HJKMNP-TV-Z]{26})"', panel.text)
    assert shown == list(reversed(order))[:Nav.RECENT]
    assert 'than one page holds' not in panel.text


def test_the_step_back_costs_two_bounded_reads_and_no_more():
    """Past a page: one whole-page probe, then one window either side of this document."""
    calls = []

    def read(name, raw, company):
        calls.append(raw)
        if len(calls) == 1:
            return {'items': [{'id': 'a'}, {'id': 'b'}], 'has_more': True}
        if raw.get('direction') == 'desc':
            return {'items': [{'id': 'c', 'number': '3', 'date': '2026-04-03'},
                              {'id': 'b', 'number': '2', 'date': '2026-04-03'},
                              {'id': 'a', 'number': '1', 'date': '2026-04-01'}], 'has_more': False}
        return {'items': [{'id': 'c', 'number': '3', 'date': '2026-04-03'},
                          {'id': 'd', 'number': '4', 'date': '2026-04-04'}], 'has_more': True}

    view = Nav.strip(read, 'CO', 'invoice', {'id': 'c', 'date': '2026-04-03'})
    assert len(calls) == 3
    assert [raw.get('direction') for raw in calls] == [None, 'desc', None]
    assert [raw.get('limit') for raw in calls] == [Nav.PAGE, Nav.PAGE, Nav.PAGE]
    assert view['previous']['url'] == '/c/CO/invoice/b'
    assert view['next']['url'] == '/c/CO/invoice/d'
    assert view['at_start'] is False and view['at_end'] is False
    assert view['unavailable'] is None
    # A position in the whole list would cost an unbounded count, so none is claimed.
    assert view['position'] is None and view['total'] is None


def test_the_earliest_document_past_a_page_still_says_it_is_the_earliest():
    calls = []

    def read(name, raw, company):
        calls.append(raw)
        if len(calls) == 1:
            return {'items': [{'id': 'a'}], 'has_more': True}
        if raw.get('direction') == 'desc':
            return {'items': [{'id': 'a', 'number': '1', 'date': '2026-04-01'}], 'has_more': False}
        return {'items': [{'id': 'a', 'number': '1', 'date': '2026-04-01'},
                          {'id': 'b', 'number': '2', 'date': '2026-04-02'}], 'has_more': True}

    view = Nav.strip(read, 'CO', 'invoice', {'id': 'a', 'date': '2026-04-01'})
    assert view['previous'] is None and view['at_start'] is True
    assert view['next']['url'] == '/c/CO/invoice/b'
    assert view['at_end'] is False


def test_the_recent_panel_asks_for_the_newest_directly():
    calls = []

    def read(name, raw, company):
        calls.append(raw)
        return {'items': [{'id': 'z', 'number': '9', 'date': '2026-04-09'}], 'has_more': True}

    view = Nav.recent(read, 'CO', 'invoice')
    assert [raw['direction'] for raw in calls] == ['desc']
    assert [raw['limit'] for raw in calls] == [Nav.RECENT]
    # More documents than a page holds no longer costs the panel its contents.
    assert view['unavailable'] is None
    assert [row['url'] for row in view['documents']] == ['/c/CO/invoice/z']
