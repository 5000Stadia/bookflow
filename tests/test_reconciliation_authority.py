"""Which documents a reconciliation is authorized for, and why the money-out side was short.

`authority` expands the account's own documents into the whole graph a reader of them can
infer, and every settlement edge it does not know about is a document it silently leaves out.
It knew two -- customer payments applying to invoices, and deposits to their receipts -- and
neither of the money-out family's: a bill payment's closure stopped at the payment while a
customer payment's reached the invoice, and a customer refund never reached the credit memo it
paid out. The third money-out producer, a sales tax remittance, settles nothing at all, and its
own module says so: there is no document on the other side of a remittance. Two edges missing
out of three producers, and the one that is not missing is not an edge.

What it is NOT is a permission escalation, and this file says so rather than implying severity
it did not find: the only requirement the closure can add is `customer-work`, an allocation only
ever hangs off an invoice or a sales receipt, and an AP obligation is always a bill. Both halves
are asserted, because "this gap is harmless" is a claim that goes stale the day a vendor-side
capability is added.
"""
import pytest
import sqlalchemy as sa

from bookflow.company import payment_authority
from bookflow.company import reconciliation_adapters as adapters
from bookflow.company import reconciliation_loading as loading
from bookflow.company import reconciliation_preparation as preparation
from bookflow.company import schema as c
from tests.test_deposit_lifecycle import driver  # noqa: F401
from tests.test_reconciliation_storage_validation import (  # noqa: F401
    account, journal, pair, run, COMPANY,
)

# A table of this shape records one document settling another. Each is either an edge the
# closure follows or is named here with the reason it is not, so a new settlement family cannot
# be added without someone deciding which it is.
NOT_SETTLEMENT = {
    'deposit_drafts': 'a draft naming the deposit it copies and the one it edits, not a settlement',
    'application_allocations': 'allocation rows beneath an application the closure already follows',
    'deposit_current_memberships': 'the current projection of `deposit_memberships`, already an edge',
    'deposit_components': 'component detail beneath a membership the closure already follows',
    'payment_components': "a payment's own components, keyed to that same payment",
    'reconciliation_keys': 'our own storage, keyed to the bank effect of the same document',
    'reconciliation_deposit_versions': 'our own storage, keyed to the bank effect of the same document',
    # Settlement-shaped and deliberately not followed. A credit memo naming the sale it credits
    # is a real edge and adding it would widen the closure across two AR sales, either of which
    # can own a work allocation -- so unlike the money-out edges this one can change what the
    # gate demands, and that is a decision with a witness of its own rather than a tidy-up.
    'credit_source_claims': 'DEFERRED: a real edge whose closure could newly demand customer-work',
    'credit_line_profiles': 'DEFERRED: line detail beneath `credit_source_claims`',
}


def settlement_shaped():
    """Every table that names one document settling another, found rather than listed.

    Two shapes, because the second is how the money-out omission stayed invisible: a table with
    two transaction columns, and a table with one transaction column and a key belonging to
    another document. A search for only the first finds `ap_applications` and never finds
    `customer_refund_consumptions`.
    """
    keyed = {name for name, table in c.metadata.tables.items()
             if any(col.name == 'transaction_id' for col in table.columns)}
    found = {}
    for name, table in c.metadata.tables.items():
        columns = sorted({col.name for col in table.columns if col.name.endswith('transaction_id')})
        if not columns:
            continue
        through = sorted({fk.parent.name for fk in table.foreign_keys
                          if fk.parent.name.endswith('_key_id') and fk.column.table.name in keyed})
        if len(columns) >= 2 or through:
            found[name] = columns + through
    return found


def test_every_table_that_settles_one_document_with_another_is_an_edge_or_is_excused():
    declared = {name for name, _, _, _ in adapters.SETTLEMENT_EDGES}
    shaped = settlement_shaped()
    assert declared <= set(shaped), 'a declared edge that is not shaped like one'
    unaccounted = set(shaped) - declared - set(NOT_SETTLEMENT)
    assert not unaccounted, (
        'a table links two documents and is neither followed by `authority` nor excused: '
        + ', '.join(sorted(unaccounted)))
    assert not set(NOT_SETTLEMENT) - set(shaped), 'an excuse for a table that is not shaped like one'
    for name, settling, settled, _ in adapters.SETTLEMENT_EDGES:
        assert settling in shaped[name] and settled in shaped[name], name


@pytest.fixture
def paid_bill(client):
    """A bill paid out of a bank account: the money-out edge, end to end."""
    bank = account(client, 'Authority bank')
    expense = account(client, 'Authority parts', 'expense')
    vendor = run(client, 'vendor create', dict(name='Authority Supply'))['id']
    method = next(row['id'] for row in run(client, 'payment-method query', dict(limit=50))['items'])
    bill = run(client, 'bill post', dict(vendor=vendor, date='2026-01-05',
                                         expenses=[dict(account=expense, amount='40.00')]))
    payment = run(client, 'bill pay', dict(date='2026-01-10', funding_account=bank, method=method,
                                           bills=[dict(bill=bill['id'])]))['payments'][0]
    return dict(bank=bank, bill=bill, payment=payment)


def test_the_closure_reaches_the_bill_a_payment_settled(paid_bill, driver):
    """The edge, doing the one thing it exists to do."""
    with driver.session() as s:
        snapshot = loading.load(s, paid_bill['bank'])
    authorized = set(snapshot.authority_transactions)
    assert paid_bill['payment']['id'] in authorized, 'the payment posts to the bank account'
    assert paid_bill['bill']['id'] in authorized, (
        'the bill it settled is what the payment reveals, and the closure has to reach it')


def test_the_bill_may_be_cited_as_evidence_for_the_opening(paid_bill, driver):
    """The reachable half: a citation refused before the edge existed.

    `validate_evidence` admits a reference only when the closure authorized it, so without the
    AP edge a bookkeeper could cite the invoice behind a customer payment and not the bill
    behind a bill payment. That asymmetry was the visible cost of the omission.
    """
    from bookflow.company import reconciliation_commands_models as m
    with driver.session() as s:
        snapshot = loading.load(s, paid_bill['bank'])
        reference = m.TransactionEvidence(kind='transaction', transaction_id=paid_bill['bill']['id'])
        preparation.validate_evidence(snapshot, (reference,))


def test_the_closure_reaches_the_credit_memo_a_refund_paid_out(client, driver):
    """The second missing edge, and the one a search for two transaction columns never finds.

    `customer_refund_consumptions` names its credit memo through a key rather than by
    transaction, so it is the same omission wearing a shape the obvious scan misses. That is
    why the guard above looks for both shapes.
    """
    bank = account(client, 'Refund bank')
    income = account(client, 'Refund income', 'income')
    customer = run(client, 'customer create', dict(name='Refund Customer'))['id']
    code = next(row['id'] for row in run(client, 'sales-tax-code list', {})['items']
                if not row['taxable'])
    item = run(client, 'item create', dict(name='Refund visit', type='service', sales_enabled=True,
                                           description='Refundable visit', income_account_id=income,
                                           price='25.00', sales_tax_code_id=code))['id']
    method = next(row['id'] for row in run(client, 'payment-method query', dict(limit=50))['items'])
    sale = run(client, 'invoice post', dict(customer=customer, date='2026-02-01',
                                            lines=[dict(item=item, quantity='1')]))
    credit = run(client, 'credit-memo post', dict(
        customer=customer, date='2026-02-05',
        lines=[dict(source_invoice=sale['id'], source_line=sale['revision']['lines'][0]['line_id'],
                    quantity='1')]))
    run(client, 'customer-refund post', dict(date='2026-02-10', funding_account=bank, method=method,
                                             sources=[dict(credit_memo=credit['id'])]))
    with driver.session() as s:
        snapshot = loading.load(s, bank)
    assert credit['id'] in set(snapshot.authority_transactions), (
        'the credit memo the refund paid out is what the refund reveals')


def test_reaching_the_bill_asks_for_no_capability_it_did_not_ask_for_before(paid_bill, driver):
    """The half that says how severe this was: not very, and here is why.

    The only requirement the closure can add is `customer-work`, and it is added when some
    transaction in the set owns a work billing allocation. Those hang off the sale a work
    document was converted into; an AP obligation is a bill. So widening the closure across the
    AP edge changes nothing the gate asks for -- until a vendor-side capability exists, at which
    point this test fails and the omission would have been an escalation.
    """
    with driver.session() as s:
        narrow = {paid_bill['payment']['id']}
        widened = narrow | {paid_bill['bill']['id']}
        assert payment_authority.requirements(s.company, sorted(narrow)) == \
            payment_authority.requirements(s.company, sorted(widened))
        owners = s.company.conn.execute(
            sa.select(c.transactions.c.type)
            .where(c.transactions.c.id.in_(sa.select(c.work_billing_allocations.c.transaction_id)))
            ).scalars().all()
        assert set(owners) <= {'invoice', 'sales_receipt'}, (
            'a work allocation now hangs off something other than a sale, so the AP closure can '
            'change what is demanded and the gap this file describes has become an escalation')
