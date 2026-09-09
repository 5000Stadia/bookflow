"""Real-browser witness for the saved deposit list.

Every rendered amount here is compared against what `deposit query` and
`deposit show` themselves returned, never against arithmetic invented in this
file, and the whole-filter totals are held byte-identical across page sizes and
across pages, because a page whose totals move with its page size looks
internally consistent on every page and is wrong on all of them.
"""
import base64
import json
from time import perf_counter

import pytest

from bookflow.core.money import Money
from tests.test_row5_browser_acceptance import CHROME, browser_site  # noqa: F401
from tests.test_row8_register_browser import register_browser, _command  # noqa: F401

pytestmark = pytest.mark.skipif(not CHROME.exists(), reason='Chrome unavailable')

SIGNATURE = 'Signature counter deposit'
PROSE = 'Full saved deposit prose remains readable on a phone. ' * 5
ROLES = ('table', 'row', 'columnheader', 'cell')


def _labels(page):
    """The eight whole-filter amounts the command returned, in the product's own format."""
    amounts = dict(page['totals'], effective_bank_total=page['effective_bank_total'])
    return {key: str(Money(value['minor_units'], value['currency'])) for key, value in amounts.items()}


def _rendered_totals(browser):
    return browser.evaluate('Object.fromEntries([...document.querySelectorAll("[data-total]")]'
                            '.map(e=>[e.dataset.total, e.querySelector("dd").textContent.trim()]))')


def _rendered_rows(browser):
    """Exact per-row text, taking each amount's own text node rather than the whole cell.

    The phone card shows a visually hidden column label inside the same cell, so
    `innerText` would carry it, and a substring check would let `10.00 USD`
    satisfy an assertion about `0.00 USD`.
    """
    return browser.evaluate("""[...document.querySelectorAll('#deposit-list-lines tbody tr')].map(tr => ({
      deposit: tr.dataset.deposit,
      number: tr.querySelector('.deposit-open').textContent.trim(),
      status: tr.children[2].lastChild.textContent.trim(),
      revision: tr.querySelector('[data-amount=revision]').lastChild.textContent.trim(),
      effective: tr.querySelector('[data-amount=effective]').lastChild.textContent.trim(),
      details: tr.querySelector('details').innerText}))""")


def _open_row_details(browser):
    """Ask every row for its composition, the way a reader does before comparing it."""
    browser.evaluate("[...document.querySelectorAll('#deposit-list-lines details')].forEach(d => d.open = true)")


def _subtree_backend_ids(browser, element_id):
    """Every backend node id inside one element, so the AX check cannot answer about another table."""
    document = browser.call('DOM.getDocument', dict(depth=-1))['root']

    def attributes(node):
        raw = node.get('attributes') or []
        return dict(zip(raw[0::2], raw[1::2]))

    def find(node):
        if attributes(node).get('id') == element_id:
            return node
        for child in node.get('children') or []:
            found = find(child)
            if found is not None:
                return found
        return None

    root = find(document)
    assert root is not None, f'{element_id} is not in the document'
    found = set()

    def collect(node):
        found.add(node['backendNodeId'])
        for child in node.get('children') or []:
            collect(child)

    collect(root)
    return found


def _list_roles(browser):
    """Counted accessible roles inside the list table alone."""
    browser.call('Accessibility.enable')
    inside = _subtree_backend_ids(browser, 'deposit-list-lines')
    counts = dict.fromkeys(ROLES, 0)
    for node in browser.call('Accessibility.getFullAXTree')['nodes']:
        if node.get('backendDOMNodeId') in inside and not node.get('ignored'):
            role = node.get('role', {}).get('value')
            if role in counts:
                counts[role] += 1
    return counts


def _contained(browser, width):
    """The established layout criterion: nothing scrolls sideways and no cell leaves the screen."""
    assert browser.evaluate('document.documentElement.scrollWidth===document.documentElement.clientWidth')
    assert browser.evaluate('[document.querySelector("#deposit-list-lines"),document.querySelector(".deposit-list-wrap")]'
                            '.every(e=>e.scrollWidth===e.clientWidth)')
    escaped = browser.evaluate("""[...document.querySelectorAll('#deposit-list-lines td, #deposit-list-lines th')]
      .flatMap(cell => [...cell.getClientRects()])
      .filter(box => box.left < -0.5 || box.right > window.innerWidth + 0.5).length""")
    assert escaped == 0, f'{escaped} cells left the {width}px viewport'
    assert browser.evaluate('getComputedStyle(document.querySelector("#deposit-list-lines tbody tr")).display') == (
        'grid' if width == 390 else 'table-row')


def _amounts_painted(browser):
    """Amounts are looked at, not read out of the DOM: a real box, visible, opaque."""
    return browser.evaluate("""[...document.querySelectorAll('[data-amount], .deposit-list-totals dd')].every(e => {
      const box = e.getBoundingClientRect(), style = getComputedStyle(e);
      return box.width > 0 && box.height > 0 && style.visibility === 'visible'
             && Number(style.opacity) > 0 && style.display !== 'none';})""")


@pytest.mark.timeout(900)
def test_saved_deposit_list_totals_paging_filters_and_journey(register_browser, tmp_path):
    env = register_browser
    b = env.browser
    run = lambda name, args: _command(b, env.site, name, args)
    base = f'{env.site.base_url}/c/{env.site.company_id}'
    timings = {}

    def follow(selector, ready):
        """Click a real control and wait for that exact destination to finish loading."""
        href = b.evaluate(f'document.querySelector({json.dumps(selector)}).href')
        b.evaluate(f'document.querySelector({json.dumps(selector)}).click()')
        b.wait_for(f'location.href === {json.dumps(href)} && document.readyState === "complete" '
                   f'&& !!document.querySelector({json.dumps(ready)})', timeout=30)
        return href

    def open_list(query):
        url = base + '/deposit' + query
        start = perf_counter()
        b.navigate(url)
        b.wait_for('!!document.querySelector("#deposit-list-lines") '
                   '|| !!document.querySelector(".deposit-unavailable") '
                   '|| document.body.innerText.includes("No deposits match")', timeout=30)
        return perf_counter() - start

    # ---------------------------------------------------------------- fixture
    income = run('account.create', dict(name='List income', type='income'))['id']
    fees = run('account.create', dict(name='List fees', type='expense'))['id']
    till = run('account.create', dict(name='List till', type='other_current_asset'))['id']
    savings = run('account.create', dict(name='List savings', type='bank'))['id']
    ada = run('customer.create', dict(name='Ada Waterworks'))['id']
    river = run('customer.create', dict(name='Riverside Cafe'))['id']
    code = next(row['id'] for row in run('sales-tax-code.list', {})['items'] if not row['taxable'])
    item = run('item.create', dict(name='List drain service', type='service', sales_enabled=True,
                                   description='Service labor', income_account_id=income,
                                   price='60.00', sales_tax_code_id=code))['id']

    def cash(party, account, amount):
        return dict(received_from=dict(kind='customer', id=party), from_account=account, amount=amount)

    def post(key, *, number, date, bank, memo, additional, cash_back=None, sources=None):
        document = dict(mode='inline', deposit_to=bank, date=date, number=number, memo=memo,
                        additional=additional)
        if cash_back:
            document['cash_back'] = cash_back
        if sources:
            document['sources'] = sources
        return run('deposit.post', dict(operation_key=key, document=document))['deposit']

    bank = env.bank['id']
    saved = {}
    for key, number, date, account, additional, back, memo in (
            ('list-1', 'SIG-001', '2026-06-01', bank, [cash(ada, income, '100.00')], None, SIGNATURE + ' one'),
            ('list-2', 'SIG-002', '2026-06-02', bank, [cash(ada, income, '250.00'), cash(ada, fees, '-25.00')],
             None, SIGNATURE + ' two'),
            ('list-3', 'SIG-003', '2026-06-02', savings, [cash(river, income, '400.00')],
             dict(account=till, amount='50.00', memo='Van float'), SIGNATURE + ' three'),
            ('list-4', 'SIG-004', '2026-06-03', bank, [cash(river, income, '60.00'), cash(river, fees, '-10.00')],
             dict(account=till, amount='50.00', memo='All of it to cash'), SIGNATURE + ' four'),
            ('list-5', 'SIG-005', '2026-06-04', savings, [cash(ada, income, '300.00')], None, SIGNATURE + ' five'),
            ('list-6', 'SIG-006', '2026-06-05', bank, [cash(ada, income, '12.34')], None,
             SIGNATURE + ' six. ' + PROSE),
            ('list-7', 'SIG-007', '2026-06-05', bank, [cash(river, income, '12.34')], None, SIGNATURE + ' seven')):
        saved[number] = post(key, number=number, date=date, bank=account, memo=memo,
                             additional=additional, cash_back=back)
    # One deposit built from a real receipt, so the contributing-receipt total is not always zero.
    undeposited = next(row['id'] for row in run('account.list', {})['items']
                       if row['system_role'] == 'undeposited_funds')
    method = next(row['id'] for row in run('payment-method.list', {})['items'] if row['kind'] == 'cash')
    receipt = run('sales-receipt.post', dict(customer=river, deposit_to=undeposited, payment_method=method,
        date='2026-06-06', lines=[dict(item=item, quantity='1', unit_price='60.00')]))['id']
    available = [row for row in run('deposit.sources', dict(date='2026-06-06', limit=200))['items']
                 if row['source'] == receipt]
    assert len(available) == 1, available
    saved['SIG-008'] = post('list-8', number='SIG-008', date='2026-06-06', bank=bank,
                            memo=SIGNATURE + ' eight', additional=[cash(river, fees, '-5.00')],
                            sources=[dict(source_type=row['source_type'], source=row['source'],
                                          expected_version=row['expected_version']) for row in available])
    for index in range(20):
        post(f'list-filler-{index}', number=None, date='2026-05-15', bank=bank,
             memo=f'Routine counter cash run {index}', additional=[cash(ada, income, '10.00')])
    def call(name, args, *, dry_run=False, reason='Saved deposit list witness'):
        result = b.evaluate(f"""fetch('/companies/{env.site.company_id}/commands/{name}?dry_run={str(dry_run).lower()}',
          {{method:'POST', credentials:'same-origin', headers:{{'Content-Type':'application/json',
            'X-Bookflow-Workbench':'1','X-Bookflow-Client-Name':'bookflow-workbench',
            'X-Bookflow-Reason':{json.dumps(reason)}}},
           body: JSON.stringify({json.dumps(args)})}}).then(async r => ({{status:r.status, body:await r.json()}}))""",
                            await_promise=True, timeout=60)
        assert result['status'] == 200, result
        return result['body']

    void = dict(deposit=saved['SIG-005']['id'], expected_version=1, operation_key='list-void')
    preview = call('deposit.void', void, dry_run=True, reason='Void a duplicate deposit')
    call('deposit.void', dict(void, dependency_guard=preview['dependency_guard']),
         reason='Void a duplicate deposit')

    sources = {number: run('deposit.show', dict(deposit=deposit['id']))
               for number, deposit in saved.items()}
    assert sources['SIG-005']['current']['status'] == 'voided'
    assert sources['SIG-005']['current']['revision_bank_total']['minor_units'] == 30000
    assert sources['SIG-005']['current']['effective_bank_total']['minor_units'] == 0
    assert sources['SIG-004']['current']['revision_bank_total']['minor_units'] == 0
    assert sources['SIG-008']['totals']['source_total']['minor_units'] == 6000

    # ------------------------------------------- 1. page size cannot move totals
    signature = dict(q='Signature', sort='bank_total', direction='asc')
    wide = run('deposit.query', dict(signature, page=dict(limit=100)))
    narrow = run('deposit.query', dict(signature, page=dict(limit=3)))
    single = run('deposit.query', dict(signature, page=dict(limit=1)))
    second = run('deposit.query', dict(signature, page=dict(limit=3, cursor=narrow['next_cursor'])))
    assert (len(wide['items']), len(narrow['items']), len(single['items']), len(second['items'])) == (8, 3, 1, 3)
    reference = json.dumps([wide['totals'], wide['effective_bank_total'], wide['total_count']], sort_keys=True)
    for other in (narrow, single, second):
        assert json.dumps([other['totals'], other['effective_bank_total'], other['total_count']],
                          sort_keys=True) == reference
    assert wide['total_count'] == 8
    # Every whole-filter amount is the sum of the same amount on the eight source documents.
    for key in wide['totals']:
        assert wide['totals'][key]['minor_units'] == sum(
            document['totals'][key]['minor_units'] for document in sources.values()), key
    assert wide['effective_bank_total']['minor_units'] == sum(
        document['current']['effective_bank_total']['minor_units'] for document in sources.values())
    # A void keeps its revision amount inside the totals and contributes no bank movement.
    assert wide['totals']['bank_total']['minor_units'] - wide['effective_bank_total']['minor_units'] == 30000

    # ------------------------------------------------- 2. banking entry, default list
    listing = json.dumps(base + '/deposit')
    entry = f'[...document.querySelectorAll("a")].find(a => a.href === {listing})'
    b.navigate(base + '/')
    b.wait_for(f'!!{entry}')
    assert 'Saved deposits' in b.evaluate(f'{entry}.textContent')
    b.evaluate(f'{entry}.click()')
    b.wait_for(f'location.href === {listing} && document.readyState === "complete" '
               '&& !!document.querySelector("#deposit-list-lines")', timeout=30)
    whole = run('deposit.query', dict(page=dict(limit=25)))
    assert whole['total_count'] == 28
    assert _rendered_totals(b) == _labels(whole)
    count_text = b.evaluate('document.querySelector("#deposit-page-count").textContent')
    assert '25 deposits on this page' in count_text and '28 matching deposits' in count_text
    # Date descending, and a same-date tie broken the same way the command broke it.
    assert [row['number'] for row in _rendered_rows(b)[:4]] == [
        row['selected']['number'] for row in whole['items'][:4]] == ['SIG-008', 'SIG-007', 'SIG-006', 'SIG-005']
    for width in (1280, 390):
        b.viewport(width, 900)
        _contained(b, width)
    b.viewport(1280, 900)

    # ------------------------------------------------- 3. next, previous, fresh filters
    timings['list_render_28_deposits_s'] = open_list('?q=counter&sort=number&direction=asc')
    counter = run('deposit.query', dict(q='counter', sort='number', direction='asc', page=dict(limit=25)))
    assert counter['total_count'] == 28
    first_page = _rendered_totals(b)
    assert first_page == _labels(counter)
    assert len(_rendered_rows(b)) == 25
    follow('a[rel=next]', '#deposit-list-lines')
    assert b.evaluate('location.search.includes("cursor=")')
    assert _rendered_totals(b) == first_page                      # totals do not move to page two
    assert '28 matching deposits' in b.evaluate('document.querySelector("#deposit-page-count").textContent')
    assert len(_rendered_rows(b)) == 3
    assert b.evaluate('document.querySelector("[name=q]").value') == 'counter'
    assert b.evaluate('document.querySelector("[name=sort]").value') == 'number'
    assert b.evaluate('document.querySelector("[name=direction]").value') == 'asc'
    previous_href = b.evaluate('document.querySelector("a[rel=prev]").href')

    # Changed filters start fresh: the form carries no cursor, so page two is left behind.
    b.evaluate("""(() => {const form = document.querySelector('#deposit-filters');
      form.querySelector('[name=q]').value = 'Signature';
      form.querySelector('[name=sort]').value = 'bank_total';
      form.querySelector('[name=direction]').value = 'asc';
      form.requestSubmit();})()""")
    b.wait_for('location.search.includes("q=Signature") && !location.search.includes("cursor") '
               '&& document.readyState === "complete" && !!document.querySelector("#deposit-list-lines")',
               timeout=30)

    # --------------------------------------- 4. every monetary witness against its source
    assert _rendered_totals(b) == _labels(wide)
    _open_row_details(b)
    rows = _rendered_rows(b)
    assert [row['number'] for row in rows] == [row['selected']['number'] for row in wide['items']] == [
        'SIG-004', 'SIG-006', 'SIG-007', 'SIG-008', 'SIG-001', 'SIG-002', 'SIG-005', 'SIG-003']
    for row in rows:
        document = sources[row['number']]
        assert row['deposit'] == document['deposit_id']
        assert row['revision'] == str(Money(**document['current']['revision_bank_total']))
        assert row['effective'] == str(Money(**document['current']['effective_bank_total']))
        assert row['status'].startswith(document['current']['status'])
        for key in ('source_total', 'cash_back', 'negative_additional_total'):
            assert str(Money(**document['totals'][key])) in row['details'], (row['number'], key)
        assert f"{document['counts']['sources']} receipts" in row['details']
    by_number = {row['number']: row for row in rows}
    assert by_number['SIG-005']['status'].startswith('voided')
    assert (by_number['SIG-005']['revision'], by_number['SIG-005']['effective']) == ('300.00 USD', '0.00 USD')
    assert (by_number['SIG-004']['revision'], by_number['SIG-004']['effective']) == ('0.00 USD', '0.00 USD')
    assert (by_number['SIG-006']['revision'], by_number['SIG-007']['revision']) == ('12.34 USD', '12.34 USD')
    assert '60.00 USD' in by_number['SIG-008']['details'] and '1 receipts' in by_number['SIG-008']['details']

    # ------------------------------------------------- 6. layout and scoped roles
    for width in (1280, 390):
        b.viewport(width, 900)
        _contained(b, width)
        assert PROSE.strip() in b.evaluate('document.querySelector("#deposit-list-lines").innerText')
        assert _amounts_painted(b)
        assert _list_roles(b) == dict(table=1, row=9, columnheader=5, cell=40), width
        (tmp_path / f'deposit-list-{width}.png').write_bytes(base64.b64decode(
            b.call('Page.captureScreenshot', dict(format='png', captureBeyondViewport=True))['data']))
    b.call('Emulation.setEmulatedMedia', dict(media='print'))
    assert b.evaluate('getComputedStyle(document.querySelector("#deposit-list-lines tbody tr")).display') == 'table-row'
    b.call('Emulation.setEmulatedMedia', dict(media='screen'))
    b.viewport(1280, 900)

    # ------------------------- 5. saved detail, composition, back to a retained list
    voided_row = f'tr[data-deposit="{saved["SIG-005"]["id"]}"] .deposit-open'
    follow(voided_row, '.deposit-heading')
    assert '300.00 USD' in b.evaluate('document.querySelector(".deposit-lede").innerText')
    assert 'voided' in b.evaluate('document.body.innerText').lower()
    follow('.deposit-kinds a[href*="kind=additional"]', '.deposit-heading')
    assert '300.00 USD' in b.evaluate('document.body.innerText')
    follow('.deposit-list-back a', '#deposit-list-lines')
    assert b.evaluate('document.querySelector("[name=q]").value') == 'Signature'
    assert b.evaluate('document.querySelector("[name=sort]").value') == 'bank_total'
    assert b.evaluate('document.querySelector("[name=direction]").value') == 'asc'
    assert _rendered_totals(b) == _labels(wide)

    # ------------------------------------------------- 3b. the remaining filters
    open_list('?number=SIG-004')
    exact = run('deposit.query', dict(number='SIG-004'))
    assert _rendered_totals(b) == _labels(exact)
    assert [row['number'] for row in _rendered_rows(b)] == ['SIG-004']
    assert exact['totals']['bank_total']['minor_units'] == 0

    open_list('?status=voided')
    voided = run('deposit.query', dict(status='voided'))
    assert _rendered_totals(b) == _labels(voided)
    assert [row['number'] for row in _rendered_rows(b)] == ['SIG-005']
    assert (voided['totals']['bank_total']['minor_units'],
            voided['effective_bank_total']['minor_units']) == (30000, 0)

    open_list('?deposit_to=List+savings&date_from=2026-06-02&date_to=2026-06-04&sort=number&direction=asc')
    scoped = run('deposit.query', dict(deposit_to='List savings', date_from='2026-06-02',
                                       date_to='2026-06-04', sort='number', direction='asc'))
    assert _rendered_totals(b) == _labels(scoped)
    assert [row['number'] for row in _rendered_rows(b)] == ['SIG-003', 'SIG-005']

    open_list('?q=Signature&date_from=2026-12-01')
    assert b.evaluate('!document.querySelector("#deposit-list-lines")')
    assert 'No deposits match these filters' in b.evaluate('document.body.innerText')

    # ------------------------------------------- previous retains the same filters
    b.navigate(previous_href)
    b.wait_for('!!document.querySelector("#deposit-list-lines")', timeout=30)
    assert b.evaluate('document.querySelector("[name=q]").value') == 'counter'
    assert b.evaluate('document.querySelector("[name=sort]").value') == 'number'
    assert _rendered_totals(b) == first_page
    assert len(_rendered_rows(b)) == 25

    # ------------------------------------ 4b. an audited change stales the continuation
    stale_href = b.evaluate('document.querySelector("a[rel=next]").href')
    post('list-late', number='SIG-009', date='2026-06-07', bank=bank,
         memo=SIGNATURE + ' nine, saved while a reader was paging',
         additional=[cash(ada, income, '5.00')])
    b.navigate(stale_href)
    b.wait_for('!!document.querySelector(".deposit-unavailable")', timeout=30)
    assert b.evaluate('!document.querySelector("#deposit-list-lines")')
    unavailable = b.evaluate('document.querySelector(".deposit-unavailable").innerText')
    assert 'E_QUERY_STALE' in unavailable
    assert 'This results page has changed or its continuation is invalid' in unavailable
    assert 'Restart with your retained filters' in unavailable
    assert b.evaluate('document.querySelector("[name=q]").value') == 'counter'
    assert b.evaluate('document.querySelector("[name=sort]").value') == 'number'
    assert b.evaluate('document.querySelector("[name=direction]").value') == 'asc'
    follow('#deposit-restart', '#deposit-list-lines')
    assert b.evaluate('document.querySelector("[name=q]").value') == 'counter'
    restarted = run('deposit.query', dict(q='counter', sort='number', direction='asc', page=dict(limit=25)))
    assert restarted['total_count'] == 29
    assert _rendered_totals(b) == _labels(restarted)
    assert '29 matching deposits' in b.evaluate('document.querySelector("#deposit-page-count").textContent')

    (tmp_path / 'browser-times.json').write_text(json.dumps(timings, indent=2) + '\n')
    print('Saved deposit list timings:', json.dumps(timings), flush=True)


@pytest.mark.timeout(600)
def test_saved_deposit_list_keeps_companies_separate(register_browser):
    """Another company's deposits and its cursor cannot enter this company's list."""
    env = register_browser
    b = env.browser
    run = lambda name, args: _command(b, env.site, name, args)

    def hub(name, args):
        result = b.evaluate(f"""fetch('/commands/{name}', {{method:'POST', credentials:'same-origin',
          headers:{{'Content-Type':'application/json','X-Bookflow-Workbench':'1',
                    'X-Bookflow-Client-Name':'bookflow-workbench','X-Bookflow-Reason':'Isolation witness'}},
          body: JSON.stringify({json.dumps(args)})}}).then(async r => ({{status:r.status, body:await r.json()}}))""",
                           await_promise=True, timeout=60)
        assert result['status'] == 200, result
        return result['body']

    def elsewhere(company, name, args):
        result = b.evaluate(f"""fetch('/companies/{company}/commands/{name}', {{method:'POST',
          credentials:'same-origin', headers:{{'Content-Type':'application/json','X-Bookflow-Workbench':'1',
          'X-Bookflow-Client-Name':'bookflow-workbench'}},
          body: JSON.stringify({json.dumps(args)})}}).then(async r => ({{status:r.status, body:await r.json()}}))""",
                           await_promise=True, timeout=60)
        assert result['status'] == 200, result
        return result['body']

    income = run('account.create', dict(name='Alpha income', type='income'))['id']
    alpha_customer = run('customer.create', dict(name='Alpha payer'))['id']
    for index, amount in enumerate(('11.00', '22.00')):
        run('deposit.post', dict(operation_key=f'alpha-{index}', document=dict(mode='inline',
            deposit_to=env.bank['id'], date='2026-06-03', memo=f'Alpha counter deposit {index}',
            additional=[dict(received_from=dict(kind='customer', id=alpha_customer),
                             from_account=income, amount=amount)])))

    other = hub('company.new', dict(legal_name='List isolation books', home_currency='USD'))['company_id']
    assert other != env.site.company_id
    other_bank = elsewhere(other, 'account.create', dict(name='Bravo bank', type='bank'))['id']
    other_income = elsewhere(other, 'account.create', dict(name='Bravo income', type='income'))['id']
    other_customer = elsewhere(other, 'customer.create', dict(name='Bravo payer'))['id']
    for index, amount in enumerate(('777.00', '888.00')):
        elsewhere(other, 'deposit.post', dict(operation_key=f'bravo-{index}', document=dict(mode='inline',
            deposit_to=other_bank, date='2026-06-03', memo=f'Bravo counter deposit {index}',
            additional=[dict(received_from=dict(kind='customer', id=other_customer),
                             from_account=other_income, amount=amount)])))
    bravo = elsewhere(other, 'deposit.query', dict(page=dict(limit=1)))
    assert bravo['total_count'] == 2 and bravo['totals']['bank_total']['minor_units'] == 166500
    bravo_deposit = bravo['items'][0]['current']['deposit_id']

    alpha = run('deposit.query', dict(page=dict(limit=25)))
    assert alpha['total_count'] == 2 and alpha['totals']['bank_total']['minor_units'] == 3300
    base = f'{env.site.base_url}/c/{env.site.company_id}'
    b.navigate(base + '/deposit')
    b.wait_for('!!document.querySelector("#deposit-list-lines")', timeout=30)
    body = b.evaluate('document.body.innerText')
    assert _rendered_totals(b) == _labels(alpha)
    assert '2 matching deposits' in b.evaluate('document.querySelector("#deposit-page-count").textContent')
    assert len(_rendered_rows(b)) == 2
    for absent in ('Bravo', '777.00', '888.00', '1,665.00', '1665.00', other, bravo_deposit):
        assert absent not in body, absent

    b.navigate(base + '/deposit?q=Bravo')
    b.wait_for('!!document.body.innerText.includes("No deposits match")', timeout=30)
    empty = run('deposit.query', dict(q='Bravo'))
    assert empty['total_count'] == 0
    assert _rendered_totals(b) == _labels(empty)
    assert 'Bravo counter deposit' not in b.evaluate('document.body.innerText')

    # The other company's continuation is refused here, and refusing it discloses nothing.
    b.navigate(base + '/deposit?cursor=' + bravo['next_cursor'])
    b.wait_for('!!document.querySelector(".deposit-unavailable")', timeout=30)
    refused = b.evaluate('document.querySelector(".deposit-unavailable").innerText')
    assert 'This results page has changed or its continuation is invalid' in refused
    assert b.evaluate('!document.querySelector("#deposit-list-lines")')
    for absent in ('Bravo', '777.00', '888.00'):
        assert absent not in b.evaluate('document.body.innerText'), absent

    # And the other company's deposit is not openable through this company's detail route.
    b.navigate(base + '/deposit/' + bravo_deposit)
    b.wait_for('!!document.body.innerText.includes("Deposit details are unavailable")', timeout=30)
    denied = b.evaluate('document.body.innerText')
    assert '777.00' not in denied and 'Bravo' not in denied


@pytest.mark.timeout(600)
def test_saved_deposit_list_return_link_and_error_states(register_browser):
    """A return link only ever comes back to this company's own list, and refusals stay useful."""
    env = register_browser
    b = env.browser
    run = lambda name, args: _command(b, env.site, name, args)
    company = env.site.company_id
    base = f'{env.site.base_url}/c/{company}'
    listing = f'/c/{company}/deposit'

    income = run('account.create', dict(name='Return income', type='income'))['id']
    payer = run('customer.create', dict(name='Return payer'))['id']
    deposit = run('deposit.post', dict(operation_key='return-1', document=dict(mode='inline',
        deposit_to=env.bank['id'], date='2026-06-03', memo='Return counter deposit',
        additional=[dict(received_from=dict(kind='customer', id=payer),
                         from_account=income, amount='40.00')])))['deposit']['id']

    def status(path):
        return b.evaluate(f"""fetch({json.dumps(path)}, {{credentials:'same-origin'}})
          .then(r => r.status)""", await_promise=True, timeout=60)

    def back_link(raw):
        b.navigate(base + '/deposit/' + deposit + '?return_to=' + raw)
        b.wait_for('!!document.querySelector(".deposit-list-back a")', timeout=30)
        return b.evaluate("""(() => {const url = new URL(document.querySelector('.deposit-list-back a').href);
          return [url.origin, url.pathname, url.search];})()""")

    kept = back_link('%2Fc%2F' + company + '%2Fdeposit%3Fq%3DReturn%26sort%3Dnumber')
    assert kept == [env.site.base_url, listing, '?q=Return&sort=number']
    for hostile in ('https%3A%2F%2Fexample.invalid%2Fsteal',
                    '%2F%2Fexample.invalid%2Fsteal',
                    'javascript%3Aalert(1)',
                    '%2Fc%2F01ARZ3NDEKTSV4RRFFQ69G5FAV%2Fdeposit%3Fq%3Dx',
                    '%2Fc%2F' + company + '%2Faccount',
                    '%2Fc%2F' + company + '%2Fdeposit%23fragment',
                    '%0A%2Fc%2F' + company + '%2Fdeposit',
                    'http%3A%2F%2F%5B'):
        origin, path, query = back_link(hostile)
        assert (origin, path, query) == (env.site.base_url, listing, ''), hostile

    # A filter the query refuses, with no continuation involved, says so and keeps the form.
    b.navigate(base + '/deposit?status=deleted')
    b.wait_for('!!document.querySelector(".deposit-unavailable")', timeout=30)
    refused = b.evaluate('document.querySelector(".deposit-unavailable").innerText')
    assert 'Deposits could not be loaded' in refused and 'E_VALIDATION' in refused
    assert b.evaluate('!!document.querySelector("#deposit-filters")')
    assert status(listing + '?status=deleted') == 400

    # A mistyped bank name is a filter mistake, not a denial, and it must not
    # escape the closed-failure path and replace the page with a refusal document.
    refused_bank = b.evaluate(f"""fetch('/companies/{company}/commands/deposit.query',
      {{method:'POST', credentials:'same-origin', headers:{{'Content-Type':'application/json',
        'X-Bookflow-Workbench':'1','X-Bookflow-Client-Name':'bookflow-workbench'}},
       body: JSON.stringify({{deposit_to: 'A bank that does not exist'}})}})
      .then(async r => ({{status: r.status, body: await r.json()}}))""", await_promise=True, timeout=60)
    assert refused_bank['status'] == 404, refused_bank
    assert refused_bank['body']['code'] == 'E_RECORD_NOT_FOUND'
    assert refused_bank['body']['details'] == {'field': 'deposit_to'}
    b.navigate(base + '/deposit?deposit_to=A+bank+that+does+not+exist')
    b.wait_for('!!document.querySelector(".deposit-unavailable")', timeout=30)
    missing = b.evaluate('document.querySelector(".deposit-unavailable").innerText')
    assert 'No bank account here matches that name or ID' in missing
    assert 'E_RECORD_NOT_FOUND' in missing
    assert b.evaluate('document.querySelector("[name=deposit_to]").value') == 'A bank that does not exist'
    assert status(listing + '?deposit_to=A+bank+that+does+not+exist') == 400

    assert status(listing + '?cursor=not-a-real-cursor') == 400
    b.navigate(base + '/deposit?cursor=not-a-real-cursor')
    b.wait_for('!!document.querySelector(".deposit-unavailable")', timeout=30)
    assert 'This results page has changed or its continuation is invalid' in b.evaluate(
        'document.querySelector(".deposit-unavailable").innerText')
    assert b.evaluate('!!document.querySelector("#deposit-restart")')
