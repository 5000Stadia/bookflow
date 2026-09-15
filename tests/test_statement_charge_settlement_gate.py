"""A settled receivable cannot be voided out from under the money that settled it.

The invoice half of this was always guarded. The statement charge half was not: the guard in
``sales.prepare`` named ``invoice``, and a statement charge is a different document type
through the same writer, so a charge a customer had already paid could be voided without
refusal. What that left behind, measured before this gate existed, was not a near miss:

    SC-2 is 125.00 and the customer paid it in full, so 60.00 of SC-1 is all that is owed.
    Voiding the paid charge reversed its 125.00 of receivable and left the application that
    spent the customer's cash untouched, still naming a document now worth nothing. The
    customer's balance read -65.00; `report open-invoices` listed the voided charge at
    -125.00; `report ar-aging` put -125.00 in the 31-60 bucket; and `invoice settlement`
    answered `due_minor_units: -12500` on a voided document -- a negative amount owing on a
    charge that no longer exists.

The gate reads ``ledger_schema.SETTLEABLE_RECEIVABLE_TYPES``, which is where the settlement
contract already says which receivables a customer's money can attach to, so the refusal
covers every such type by construction rather than by a second list anyone has to remember
to widen. ``test_every_settleable_receivable_is_gated`` is the assertion that keeps those two
sets equal: a new settleable type fails it until it is settled and voided here too.
"""
import sqlite3
from pathlib import Path

import pytest

from bookflow.company.ledger_schema import SETTLEABLE_RECEIVABLE_TYPES
from bookflow.core.errors import BookflowError
from tests import payment_raw_evidence as evidence
# The company, chart, items and customer this needs are exactly the worked case that module
# already builds; a second copy of it here would be a second set of figures to keep in step.
from tests.test_statement_charge import (RATE, SC1, SC2, SC1_UNITS, SC2_UNITS, OWED_UNITS,
                                         books, _first, _second, _invoice_and_receipt)


def _settle(books, document, amount, key):
    """Pay one receivable in full, whatever type it is: both are named in `invoice` fields."""
    return books['run']('payment receive', dict(
        customer=books['customer'], date='2026-08-01', amount=amount, operation_key=key,
        deposit_to=books['bank'], payment_method=books['check'],
        applications={'mode': 'inline', 'items': [
            dict(invoice=document['id'], amount=amount, expected_version=document['version'])]}),
        reason='Settle ' + document['id'])


def _post_statement_charge(books):
    return _second(books)          # SC-2, a flat 125.00


def _post_invoice(books):
    return books['run']('invoice post', dict(
        customer=books['customer'], date='2026-07-21', number='GATE-1', due_date='2026-08-20',
        lines=[dict(item=books['filing'], quantity='1', unit_price=SC2,
                    description='County filing fee')]), reason='Enter the invoice')


# One poster per settleable receivable, checked against the contract itself below.
POSTERS = {'statement_charge': _post_statement_charge, 'invoice': _post_invoice}
NOUNS = {'statement_charge': 'statement-charge', 'invoice': 'invoice'}


def _databases(root):
    return {path: evidence.database(path) for path in sorted(Path(root).rglob('*.db'))}


def test_every_settleable_receivable_is_gated():
    """The gate's coverage is the settlement contract's own set, not a list kept here."""
    assert set(POSTERS) == set(NOUNS) == set(SETTLEABLE_RECEIVABLE_TYPES)


@pytest.mark.parametrize('document_type', SETTLEABLE_RECEIVABLE_TYPES)
def test_voiding_a_settled_receivable_is_refused_and_writes_nothing(books, tmp_path, document_type):
    """The check test: without the gate the statement-charge case voids and corrupts the books."""
    _first(books)
    document = POSTERS[document_type](books)
    receipt = _settle(books, document, SC2, 'gate-' + document_type)
    application = books['run']('payment settlement', {'payment': receipt['id'],
                                                      'kind': 'applications'})['items'][0]
    settled = books['run']('invoice settlement', {'invoice': document['id']})
    assert (settled['status'], settled['applied_minor_units'], settled['due_minor_units']) == (
        'paid', SC2_UNITS, 0)

    owed_before = books['run']('report ar-aging', dict(as_of='2026-08-31'))['totals']['total']['minor_units']
    assert owed_before == SC1_UNITS
    before = _databases(tmp_path / 'charges')
    assert before, 'no company database was found to witness'

    current = books['run'](NOUNS[document_type] + ' show', {document_type: document['id']})
    with pytest.raises(BookflowError) as refused:
        books['run'](NOUNS[document_type] + ' void',
                     {document_type: document['id'], 'expected_version': current['version']},
                     reason='Void the receivable that was already paid')

    # The refusal names what blocks it and what to do about it, by identity.
    assert refused.value.code == 'E_HAS_APPLICATIONS'
    assert refused.value.details[document_type + '_id'] == document['id']
    assert refused.value.details['application_ids'] == [application['application_id']]
    assert refused.value.details['action'] == 'unapply_first'
    assert 'payment unapply' in refused.value.details['next']

    # A refusal is not a partial write: every byte of every database is where it was.
    assert _databases(tmp_path / 'charges') == before
    assert books['run']('invoice settlement', {'invoice': document['id']})['status'] == 'paid'
    assert books['run'](NOUNS[document_type] + ' show',
                        {document_type: document['id']})['status'] == 'posted'
    assert books['run']('report ar-aging', dict(as_of='2026-08-31'))['totals']['total']['minor_units'] == owed_before


@pytest.mark.parametrize('document_type', SETTLEABLE_RECEIVABLE_TYPES)
def test_releasing_the_application_lets_the_void_through(books, document_type):
    """The refusal is a gate, not a wall: unapply first and the void behaves as it always did."""
    _first(books)
    document = POSTERS[document_type](books)
    receipt = _settle(books, document, SC2, 'release-' + document_type)
    application = books['run']('payment settlement', {'payment': receipt['id'],
                                                      'kind': 'applications'})['items'][0]
    books['run']('payment unapply', dict(payment=receipt['id'], expected_version=receipt['version'],
        operation_key='release-app-' + document_type,
        applications=[dict(application_id=application['application_id'],
                           invoice_expected_version=application['invoice_version'])]),
        reason='Release it before voiding')
    current = books['run'](NOUNS[document_type] + ' show', {document_type: document['id']})
    voided = books['run'](NOUNS[document_type] + ' void',
                          {document_type: document['id'], 'expected_version': current['version']},
                          reason='Now it may be voided')
    assert voided['status'] == 'voided'
    # Nothing is left pointing at a document worth nothing, and nothing is owed on it.
    settlement = books['run']('invoice settlement', {'invoice': document['id']})
    assert (settlement['gross_minor_units'], settlement['applied_minor_units'],
            settlement['due_minor_units']) == (0, 0, 0)
    assert [row['number'] for row in
            books['run']('report open-invoices', dict(as_of='2026-08-31'))['rows']] == ['SC-1']


def test_an_unsettled_statement_charge_voids_exactly_as_it_did(books):
    """The gate touches only a charge something is applied to; an ordinary void is unchanged."""
    _first(books)
    _invoice_and_receipt(books)
    second = _second(books)
    assert books['run']('report ar-aging', dict(as_of='2026-08-31'))['totals']['total']['minor_units'] == OWED_UNITS
    voided = books['run']('statement-charge void',
                          {'statement_charge': second['id'], 'expected_version': second['version']},
                          reason='Charged to the wrong client')
    assert voided['status'] == 'voided'
    assert books['run']('report ar-aging', dict(as_of='2026-08-31'))['totals']['total']['minor_units'] == SC1_UNITS
