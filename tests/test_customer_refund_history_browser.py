"""People reach retained refund facts from the current document at desktop and phone widths."""

import pytest

from tests.test_row5_browser_acceptance import CHROME, browser_site  # noqa: F401
from tests.test_row8_register_browser import _command, register_browser  # noqa: F401
from tests.test_credit_windows_browser import _books, _credit, _contained, _name, _read, _set, _preview, _save, _text
from tests.test_customer_refund_update_browser import _click
from tests.payment_raw_evidence import database

pytestmark = pytest.mark.skipif(not CHROME.exists(), reason='Chrome unavailable')


def _refund(books, sources, **extra):
    return books['run']('customer-refund.post', dict(date='2026-03-20', funding_account=books['bank'],
        method=books['method'], sources=sources, **extra))


@pytest.mark.parametrize('width', [1280, 390])
@pytest.mark.timeout(180)
def test_refund_history_navigation_and_stale_restart(register_browser, width):
    env, b = register_browser, register_browser.browser
    b.viewport(width, 900)
    books = _books(b, env.site, 'History')
    books['run'] = lambda name, raw: _command(b, env.site, name, raw,
        **({'X-Bookflow-Reason': 'Retained history browser witness'}
           if name.endswith(('.update', '.void')) else {}))
    credit = _credit(books, '40.00')
    paid = _refund(books, [{'credit_memo': credit['id'], 'amount': '12.00'}], memo='<script>oldRefund()</script>')
    updated = books['run']('customer-refund.update', {'refund': paid['id'], 'expected_version': 1,
        'sources': [{'credit_memo': credit['id'], 'amount': '20.00'}], 'memo': 'Current refund'})
    url = f'{env.site.base_url}/c/{env.site.company_id}/customer-refund/{paid["id"]}'
    b.navigate(url)
    b.wait_for('!!document.querySelector(".refund-sources")')
    assert 'Current refund' in _text(b, '.sales-document')
    _click(b, 'nav[aria-label="Customer refund revisions"] a[href$="/history"]')
    b.wait_for('!!document.querySelector("[data-refund-history-revision]")')
    page = _text(b, 'main')
    assert '12.00' in page and '20.00' in page and '(current)' in page
    assert '<script>oldRefund()</script>' in page
    assert not b.evaluate('!!document.querySelector("article script")')
    _click(b, '[data-refund-history-revision="1"] details summary')
    assert b.evaluate('document.querySelector("article details").open')
    assert b.evaluate('''[...document.querySelectorAll("article td")].every(cell =>
        cell.querySelector(".document-cell-label")?.textContent === cell.dataset.label)''')
    if width == 390:
        assert b.evaluate('getComputedStyle(document.querySelector(".document-cell-label")).display') == 'block'
    _contained(b, width)
    _click(b, '[data-refund-history-revision="1"] h2 a')
    b.wait_for('location.search.includes("revision_number=1") && !!document.querySelector(".refund-sources")')
    assert '12.00' in _text(b, '.sales-document')
    assert 'Current refund' not in _text(b, '.sales-document')
    _click(b, 'nav[aria-label="Customer refund revisions"] a[href$="/history"]')
    b.wait_for('!!document.querySelector("[data-history-current]")')
    _click(b, '[data-history-current]')
    b.wait_for('!location.search && !!document.querySelector(".refund-sources")')
    assert 'Current refund' in _text(b, '.sales-document')
    b.navigate(url + '/history?limit=1')
    b.wait_for('!!document.querySelector("a[rel=next]")')
    next_url = b.evaluate('document.querySelector("a[rel=next]").href')
    _click(b, 'a[rel=next]')
    b.wait_for('!!document.querySelector("[data-refund-history-revision=\\"2\\"]")')
    assert not b.evaluate('!!document.querySelector("[data-refund-history-revision=\\"1\\"]")')
    books['run']('customer-refund.void', {'refund': paid['id'], 'expected_version': updated['version']})
    b.navigate(next_url)
    b.wait_for('document.body.innerText.includes("E_QUERY_STALE")')
    _click(b, 'a[href$="/history"]')
    b.wait_for('!!document.querySelector("[data-refund-history-revision]")')
    assert 'Voided' in _text(b, 'main')
    _contained(b, width)


@pytest.mark.timeout(180)
def test_blank_refund_amount_recomputes_after_credit_growth_before_confirmation(register_browser, tmp_path):
    env, b = register_browser, register_browser.browser
    books = _books(b, env.site, 'Growth')
    books['run'] = lambda name, raw: _command(b, env.site, name, raw,
        **({'X-Bookflow-Reason': 'Defaulted growth browser witness'}
           if name.endswith(('.update', '.void')) else {}))
    first, second = _credit(books, '18.00'), _credit(books, '7.00')
    paid = _refund(books, [{'credit_memo': first['id']}, {'credit_memo': second['id']}])
    assert paid['total_minor_units'] == 2500
    assert paid['revision']['profile']['origins']['amount']['kind'] == 'default'
    line = first['revision']['lines'][0]
    books['run']('credit-memo.update', {'credit_memo': first['id'], 'expected_version': first['version'],
        'lines': [{'line_id': line['line_id'], 'item': books['item'], 'quantity': '1', 'unit_price': '30.00'}]})
    url = f'{env.site.base_url}/c/{env.site.company_id}/customer-refund/{paid["id"]}'
    b.navigate(url)
    b.wait_for('!!document.querySelector("[data-refund-correct]")')
    _click(b, '[data-refund-correct]')
    b.wait_for('!!document.querySelector("[data-generated-form]")')
    assert _read(b, _name(b, 'sources', 0, 'amount')) == ''
    # Editing the other row explicitly resubmits the sources grid; the grown source stays blank.
    _set(b, _name(b, 'sources', 1, 'amount'), '5.00')
    _set(b, 'ctx:reason', 'Recalculate grown credit and pay five of the other')
    location = next((tmp_path / 'browser-root').rglob('company.db'))
    before = database(location)
    _preview(b)
    assert database(location) == before, 'preview wrote financial or audit state'
    assert _text(b, '.refund-sources tfoot .document-total').strip() == '35.00'
    assert _read(b, _name(b, 'sources', 0, 'amount')) == ''
    assert _save(b, 'customer-refund', '.refund-sources') == paid['id']
    shown = books['run']('customer-refund.show', {'refund': paid['id']})
    assert shown['total_minor_units'] == 3500 and shown['revision']['revision_number'] == 2
    history = books['run']('customer-refund.history', {'refund': paid['id']})
    assert history['count'] == 2
    assert history['items'][0]['profile'] == paid['revision']['profile']
    assert [r['total_minor_units'] for r in history['items']] == [2500, 3500]
    assert len([batch for row in history['items'] for batch in row['batches']]) == 3
    assert [(row['kind'], row['amount_minor_units']) for row in history['items'][1]['consumptions']] == [('consume', 3000), ('consume', 500)]
    assert '35.00' in _text(b, '.sales-document')
