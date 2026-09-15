"""The visible half of credit-memo deletion: the page a person actually confirms it on.

Nothing here reads the writer. It drives the workbench the way a browser does — GET the
confirmation page, post the form — and checks what a person sees and what the database
does while they are looking at it.
"""
import sqlite3
from pathlib import Path

from tests.payment_raw_evidence import database
from tests.test_credit_windows import _books, _browser, _documents
from tests.test_row3_host import WB, hosted  # noqa: F401  (fixture)


def _path(hosted):
    return Path(hosted.ok('company.show', company=hosted.company_id)['path']) / 'company.db'


def _grant_delete(hosted):
    """Activate the policy and grant this login the exact family Delete, nothing else."""
    state = hosted.ok('permission.show')
    hosted.ok('permission.activate', dict(expected_generation=state['generation'],
                                          expected_catalog_sha256=state['catalog_sha256']))
    rows = hosted.ok('membership.list', dict(company=hosted.company_id))['items']
    member = next(x for x in rows if x['scope_type'] == 'company'
                  and x['scope_id'] == hosted.company_id and x['username'] == hosted.login)
    hosted.ok('membership.grant', dict(user=member['user_id'], company=hosted.company_id,
                                       expected_version=member['version'],
                                       grants=['transaction.credit_memo.delete']))


def test_a_credit_memo_is_deleted_from_its_own_page_with_reason_and_confirmation(hosted):
    books = _books(hosted)
    made = _documents(books)
    company = hosted.company_id
    credit = made['credit']
    base = f'/c/{company}/credit-memo/{credit["id"]}'
    path = _path(hosted)
    browser = _browser(hosted)
    # Without the grant the page offers no way in at all; the link is authority, not decoration.
    assert f'href="{base}/delete"' not in browser.get(base).text
    _grant_delete(hosted)
    browser = _browser(hosted)

    # The record page offers the way in, and the confirmation page says what will happen.
    record = browser.get(base)
    assert record.status_code == 200 and f'href="{base}/delete"' in record.text
    page = browser.get(base + '/delete')
    assert page.status_code == 200, page.text[:400]
    assert 'Cancel this credit memo and retain its history' in page.text
    assert 'There is no restore action.' in page.text
    assert 'name="reason"' in page.text and 'name="confirmed"' in page.text
    assert credit['number'] in page.text
    key = page.text.split('name="operation_key" value="')[1].split('"')[0]

    before = database(path)
    form = dict(expected_version=credit['version'], operation_key=key,
                reason='Credited the wrong customer', action='preview')
    preview = browser.post(base + '/delete', data=form, headers=WB)
    assert preview.status_code == 200
    assert 'Preview only — nothing saved.' in preview.text
    assert database(path) == before

    # Confirmation is not a formality: the same post without the box ticked is refused.
    unconfirmed = browser.post(base + '/delete', data=dict(form, action='delete'), headers=WB)
    assert unconfirmed.status_code == 400
    assert 'Confirm cancellation before deleting this credit memo.' in unconfirmed.text
    assert database(path) == before

    saved = browser.post(base + '/delete', data=dict(form, action='delete', confirmed='yes'),
                         headers=WB, follow_redirects=False)
    assert saved.status_code == 303 and saved.headers['location'].endswith('?deleted=' + credit['id'])
    assert database(path) != before

    # It has left the ordinary list and is readable again only when explicitly asked for.
    listing = browser.get(f'/c/{company}/credit-memo')
    assert listing.status_code == 200 and credit['id'] not in listing.text
    assert 'Include deleted records' in listing.text
    with_deleted = browser.get(f'/c/{company}/credit-memo?include_deleted=true')
    assert with_deleted.status_code == 200 and credit['id'] in with_deleted.text
    retained = browser.get(base + '?include_deleted=1')
    assert retained.status_code == 200 and 'Credited the wrong customer' in retained.text
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT reason FROM credit_deletions').fetchone() == ('Credited the wrong customer',)


def test_the_page_names_the_invoice_that_will_not_let_a_credit_go(hosted):
    books = _books(hosted)
    made = _documents(books)
    company = hosted.company_id
    ok = books['ok']
    credit, invoice = made['credit'], made['invoice']
    ok('customer-credit.apply', dict(credit_memo=credit['id'], expected_version=credit['version'],
       applications=[dict(invoice=invoice['id'], expected_version=invoice['version'],
                          amount='30.00')]))
    _grant_delete(hosted)
    browser = _browser(hosted)
    base = f'/c/{company}/credit-memo/{credit["id"]}'
    path = _path(hosted)
    before = database(path)
    current = hosted.ok('credit-memo.show', dict(credit_memo=credit['id']), company=company)['version']
    refused = browser.post(base + '/delete', data=dict(
        expected_version=current, operation_key='page-refusal', confirmed='yes',
        reason='Credited the wrong customer', action='delete'), headers=WB)
    assert refused.status_code == 400
    assert 'E_HAS_APPLICATIONS' in refused.text
    # Actionable: the blocking invoice is named and linked, and the next step is spelled out.
    assert f'/c/{company}/invoice/{invoice["id"]}' in refused.text
    assert 'customer-credit unapply' in refused.text
    assert database(path) == before
