"""A statement charge the customer has paid is readable and navigable as a paid receivable.

Settling a statement charge works: the money posts, the balance moves, and the void guard
refuses to unmake it. What did not work is everything a person does *afterwards*. The
product downstream of the settlement assumed every receivable was an invoice, so the person
who charged a customer and took their money could not see that it was paid:

    `statement-charge show` answered ``settlement_current: null`` on a charge that had been
    paid in full. `statement-charge query` answered the same on every row, so the list a
    person reaches for first carried no applied amount, no balance due and no status at all.
    The open receivables report listed the charge and linked it to ``/invoice/<id>``, which
    is not a page any statement charge has; the collections worklist did the same; the
    settlement page and the application page behind a receipt both read the settled document
    with `invoice show`, which does not accept a charge, so the whole page errored.

This file is the journey rather than a field check, because a field check is what let all of
that hide behind a passing suite: the charge is entered, the money is taken, and then the
charge is *read back and followed* over the surfaces a person actually uses -- the registered
commands over the running host, and the workbench pages over a logged-in session. Every link
asserted here is fetched, because a rendered href that nothing follows is exactly how this
family of defects shipped.

Nothing is settled by hand: what the reads must say is taken from `invoice settlement`, the
one command that has always answered correctly for both settleable receivables, so the
figures below are the product's own rather than a second set to keep in step.
"""
import os
import shutil

import pytest
from fastapi.testclient import TestClient

from tests.conftest import _seeded_template  # noqa: F401
from tests.test_identity_commands import WB
from tests.test_row3_host import PASSWORD, hosted  # noqa: F401
from tests.test_smoke_journeys import Company, a_customer, a_service
from tests.test_statement_drill_through import _anchors

# ------------------------------------------------------------------ the money, written out

RATE = '65.00'              # one charge entered straight onto the account, paid in full
RATE_UNITS = 6500
OPEN = '200.00'             # a second charge, part paid, still owed and now long overdue
OPEN_UNITS = 20000
PART = '50.00'
PART_UNITS = 5000
STILL_DUE_UNITS = 15000     # 200.00 - 50.00

PAID_ON = '2031-01-05'
OPEN_ON = '2031-01-07'
AS_OF = '2031-12-31'        # both charges age on their own date; the open one is over 90


# ----------------------------------------------------------------------------- the harness


@pytest.fixture(scope='module')
def company(tmp_path_factory, _seeded_template):  # noqa: F811
    """One migrated company on one running host, stood up the way the smoke journeys do."""
    root = tmp_path_factory.mktemp('charge-surfaces') / 'root'
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


def _readable(company, url):
    """One page of the journey, refused rather than followed if it did not come out readable."""
    answer = company.pages.get(url, headers=WB)
    assert answer.status_code == 200, (
        f'\n  A PAGE A PERSON REACHES DID NOT OPEN: {url}'
        f'\n  the product answered: HTTP {answer.status_code}'
        f'\n  saying: {answer.text[:600]}\n')
    body = answer.text.split('<main>', 1)[-1].split('</main>', 1)[0]
    assert 'class="error"' not in body, (
        f'\n  A PAGE A PERSON REACHES RENDERED AN ERROR: {url}'
        f'\n  {body[:600]}\n')
    return answer.text


def _report(company, verb, fields):
    """One report run exactly as the workbench runs it: the filter form, submitted."""
    opened = company.pages.get(f'/c/{company.id}/report/{verb}', headers=WB)
    assert opened.status_code == 200, f'the {verb} report would not open: {opened.text[:400]}'
    answer = company.pages.post(f'/c/{company.id}/report/{verb}', data=fields, headers=WB)
    assert answer.status_code == 200, f'the {verb} report would not run: {answer.text[:600]}'
    return answer.text


def _table(page, table_id):
    """One rendered table's own rows, so the surrounding page is never mistaken for one."""
    marker = f'id="{table_id}"'
    assert marker in page, f'the report rendered no {table_id} table at all'
    start = page.index(marker)
    return page[start:page.index('</table>', start)]


def _link_naming(fragment, text):
    """The one link in a fragment written on the given words, or a loud refusal."""
    found = [href for href, words in _anchors(fragment) if words == text]
    assert found, (f'nothing in this table is a link written on {text!r}; '
                   f'the links it does offer are {_anchors(fragment)}')
    return found[0]


# ------------------------------------------------------------------------------ the journey


@pytest.mark.timeout(900)
def test_a_paid_statement_charge_is_readable_and_navigable_as_a_paid_receivable(company):
    """Charge a customer, take their money, then read the charge back and follow it."""
    accounts, methods, codes = company.references()
    customer = a_customer(company, 'Charge Surface Customer')
    item = a_service(company, accounts, codes, 'Charge Surface Call-out', price=RATE)

    # ---------------------------------------------------- charge the customer, take the money
    paid = company.act('charge the customer straight onto their account',
                       'statement-charge.post',
                       {'customer': customer, 'date': PAID_ON, 'number': 'SURF-SC-PAID',
                        'item': item, 'quantity': '1', 'rate': RATE,
                        'description': 'Call re lease'},
                       because='Enter the charge that gets paid')
    still_open = company.act('charge the customer a second time', 'statement-charge.post',
                             {'customer': customer, 'date': OPEN_ON, 'number': 'SURF-SC-OPEN',
                              'item': item, 'quantity': '1', 'rate': OPEN,
                              'description': 'Drafting the sublease'},
                             because='Enter the charge that stays open')

    receipt = company.act('take the money for the first charge in full', 'payment.receive', {
        'customer': customer, 'date': '2031-01-06', 'amount': RATE,
        'operation_key': 'charge-surface-paid', 'payment_method': methods['Cash'],
        'deposit_to': accounts['Checking'],
        'applications': {'mode': 'inline', 'items': [
            {'invoice': paid['id'], 'expected_version': paid['version'], 'amount': RATE}]}},
        because='The customer paid the charge')
    company.act('take part of the money for the second charge', 'payment.receive', {
        'customer': customer, 'date': '2031-01-08', 'amount': PART,
        'operation_key': 'charge-surface-part', 'payment_method': methods['Cash'],
        'deposit_to': accounts['Checking'],
        'applications': {'mode': 'inline', 'items': [
            {'invoice': still_open['id'], 'expected_version': still_open['version'],
             'amount': PART}]}},
        because='The customer paid part of the second charge')

    # What the settlement command -- the one read that always answered for both types --
    # says about each charge. Everything below is measured against these, not against a
    # figure written out twice.
    settled = company.read('ask what is owed on the paid charge', 'invoice.settlement',
                           {'invoice': paid['id']})
    outstanding = company.read('ask what is owed on the open charge', 'invoice.settlement',
                               {'invoice': still_open['id']})
    assert (settled['status'], settled['applied_minor_units'], settled['due_minor_units']) == (
        'paid', RATE_UNITS, 0), f'the charge was paid in full and settlement says {settled}'
    assert (outstanding['status'], outstanding['applied_minor_units'],
            outstanding['due_minor_units']) == ('partial', PART_UNITS, STILL_DUE_UNITS), (
        f'half the second charge was paid and settlement says {outstanding}')

    # --------------------------------------- 1. read the charge back: is it paid or is it not
    shown = company.read('read the settled charge back', 'statement-charge.show',
                         {'statement_charge': paid['id']})
    # `.get`, not `[...]`: the field is dropped from the wire when it is null, so a charge
    # that carries no settlement answers with the key missing rather than set to null.
    current = shown.get('settlement_current')
    assert current is not None, (
        '\n  A PAID STATEMENT CHARGE CANNOT BE SEEN TO BE PAID.'
        '\n  `statement-charge show` carried no settlement at all on a charge this same'
        '\n  customer paid in full a moment ago, over this same host. The person who charged'
        '\n  them and took their money has no way to read that it was settled.'
        f'\n  what the read did answer: {sorted(shown)}')
    assert (current['status'], current['applied_minor_units'], current['due_minor_units']) == (
        settled['status'], settled['applied_minor_units'], settled['due_minor_units']), (
        f'the charge reads back as {current} while `invoice settlement` on the same document '
        f'says {settled}')
    assert current['document_type'] == 'statement_charge', (
        'the settlement names the document as something other than the charge it belongs to')

    # --------------------------------------------- 2. the list a person reaches for first
    listed = company.read('list this customer\'s statement charges', 'statement-charge.query',
                          {'customer': customer, 'limit': 50})['items']
    rows = {row['number']: row for row in listed}
    assert {'SURF-SC-PAID', 'SURF-SC-OPEN'} <= set(rows), (
        f'the charge list does not carry both charges: {sorted(rows)}')
    for number, expected in (('SURF-SC-PAID', settled), ('SURF-SC-OPEN', outstanding)):
        row = rows[number].get('settlement_current')
        assert row is not None, (
            f'\n  EVERY ROW OF THE STATEMENT CHARGE LIST COMES BACK UNSETTLED.'
            f'\n  {number} has money against it and its row carries no settlement at all --'
            f'\n  no applied amount, no balance due and no status.'
            f'\n  what the row did answer: {sorted(rows[number])}')
        assert (row['status'], row['applied_minor_units'], row['due_minor_units']) == (
            expected['status'], expected['applied_minor_units'], expected['due_minor_units']), (
            f'{number} reads {row} in the list and {expected} in its own settlement')

    # ------------------------------ 3. the open receivables report, and the row's own document
    open_page = _report(company, 'open-invoices', {'f:as_of': AS_OF, 'f:limit': '200'})
    open_rows = _table(open_page, 'receivables-open')
    assert 'SURF-SC-OPEN' in open_rows, 'the open receivables report does not list the charge'
    charge_link = _link_naming(open_rows, 'SURF-SC-OPEN')
    assert charge_link == f'/c/{company.id}/statement-charge/{still_open["id"]}', (
        f'\n  THE OPEN RECEIVABLES REPORT LINKS A STATEMENT CHARGE WHERE IT DOES NOT LIVE.'
        f'\n  the row offers: {charge_link}'
        f'\n  a statement charge is not an invoice and has no page under /invoice/.')
    _readable(company, charge_link)

    # ------------------------------------- 4. the collections worklist, and the same document
    chase = _report(company, 'collections', {'f:as_of': AS_OF, 'f:limit': '200',
                                             'f:minimum_bucket': 'days_1_30'})
    chase_rows = _table(chase, 'receivables-collections')
    assert 'SURF-SC-OPEN' in chase_rows, (
        'the collections worklist does not carry the overdue charge at all')
    _readable(company, _link_naming(chase_rows, 'SURF-SC-OPEN'))
    assert _link_naming(chase_rows, 'SURF-SC-OPEN') == charge_link, (
        'the collections worklist and the open receivables report send a reader to two '
        'different places for the same document')

    # --------------------------------------------- 5. the settlement page for the paid charge
    settlement_page = _readable(company, f'/c/{company.id}/invoice/{paid["id"]}/settlement')
    assert 'Statement charge SURF-SC-PAID' in settlement_page, (
        'the settlement page calls a statement charge an invoice')
    assert f'href="/c/{company.id}/statement-charge/{paid["id"]}"' in settlement_page, (
        'the settlement page offers no way back to the charge it is the settlement of')

    # -------------------------------------- 6. the application page behind the receipt itself
    application = company.read('list what the receipt was applied to', 'payment.settlement',
                               {'payment': receipt['id'], 'kind': 'applications'})['items'][0]
    application_page = _readable(
        company, f'/c/{company.id}/application/{application["application_id"]}')
    assert f'href="/c/{company.id}/statement-charge/{paid["id"]}"' in application_page, (
        'the application page does not open the receivable the money was applied to')
    assert 'Statement charge SURF-SC-PAID' in application_page, (
        'the application page calls the settled statement charge an invoice')

    # ------------------------------------ 7. the statement the customer themselves would read
    statement = _report(company, 'statement', {'f:date_from': '2000-01-01', 'f:date_to': AS_OF,
                                               'f:customer': customer, 'f:limit': '200'})
    statement_rows = _table(statement, 'statement-lines')
    assert 'SURF-SC-PAID' in statement_rows, 'the statement does not print the charge'
    assert _link_naming(statement_rows, 'SURF-SC-PAID') == \
        f'/c/{company.id}/statement-charge/{paid["id"]}', (
        'a statement charge on the statement opens somewhere other than the charge')
    _readable(company, f'/c/{company.id}/statement-charge/{paid["id"]}')
