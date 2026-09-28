"""Early-payment discounts: which settlement edge a discount was taken on, and for how much.

A discount is posted on the payment document itself -- a receipt debits its discount account
and credits Accounts Receivable, a bill payment debits Accounts Payable and credits its discount
account -- and it settles the invoice or bill through the same edge the cash does, whose amount
is the cash plus the discount. These two tables are the record that part of that edge was a
discount: the amount, the account, what the terms suggested and the discount date the payment
was measured against. They are written once, when the payment is written, and never change.

**Why the discount is capacity, not a separate edge.** The receipt's component (the bill
payment's source component) carries the cash and the discount together, so every reader of an
invoice's or a bill's open balance, and every reader of what a payment still has free, works
unchanged. When a discounted settlement is unapplied, the discount stays with the payment as
the customer's credit or the vendor's debit -- the anchor's rule ("the early payment discount
becomes a credit") -- and a void reverses the whole posting, discount included.

``payment_discounts`` rows are owned by a customer receipt and name its permanent component key,
because a receipt correction restates its postings under a new revision while the discount
stays. ``bill_payment_discounts`` rows are owned by one bill-payment revision, which has no
correcting revision, and name the exact attribution row of the discount leg.
"""

import sqlalchemy as sa


def define_tables(metadata, column, table):
    C, T = column, table

    def identifier(name, description, target=None, *, primary_key=False, nullable=False):
        constraints = [sa.ForeignKey(target)] if target else []
        return C(name, sa.String(26), description, *constraints, primary_key=primary_key, nullable=nullable)

    def integer(name, description, nullable=False):
        return C(name, sa.BigInteger, description, nullable=nullable)

    def created():
        return [C('created_at', sa.String(32), 'UTC time this discount was recorded.', nullable=False),
                identifier('created_by', 'Company principal that recorded this discount.'),
                C('created_via', sa.String(16), 'Interface that recorded this discount.', nullable=False),
                C('audit_event_id', sa.String(26), 'Audit event that committed this discount.',
                  sa.ForeignKey('audit_events.id'), nullable=False)]

    def terms(prefix):
        return [integer('suggested_minor_units', 'Discount the terms suggested for the payment date; zero after the discount date.'),
                C('discount_date', sa.String(10), 'Discount date the payment was measured against; null when the terms offer none.', nullable=True),
                integer('terms_percent_millionths', 'Terms discount percent in millionths of one percentage point; null when the terms offer none.', True),
                sa.CheckConstraint("typeof(amount_minor_units) = 'integer' AND amount_minor_units > 0", name=f'ck_{prefix}_discount_amount'),
                sa.CheckConstraint("typeof(suggested_minor_units) = 'integer' AND suggested_minor_units >= 0", name=f'ck_{prefix}_discount_suggested'),
                sa.CheckConstraint("terms_percent_millionths IS NULL OR terms_percent_millionths BETWEEN 0 AND 100000000", name=f'ck_{prefix}_discount_percent')]

    payment_discounts = T('payment_discounts',
        identifier('id', 'Stable ULID of this discount.', primary_key=True),
        identifier('transaction_id', 'Customer receipt that took the discount.'),
        identifier('component_key_id', 'Permanent receipt component whose capacity carries the discount.'),
        identifier('application_id', 'Settlement edge the discount was taken on; its amount is cash plus discount.', 'applications.id'),
        identifier('invoice_id', 'Receivable document the discount settled.', 'transactions.id'),
        identifier('discount_account_id', 'Account debited for the discount.', 'accounts.id'),
        integer('amount_minor_units', 'Positive discount amount.'),
        C('currency', sa.String(3), 'Home currency of the discount.', nullable=False),
        *terms('payment'), *created(),
        sa.UniqueConstraint('application_id', name='uq_payment_discount_application'),
        sa.ForeignKeyConstraint(['transaction_id', 'component_key_id'],
            ['payment_component_keys.transaction_id', 'payment_component_keys.id'], name='fk_payment_discount_component'),
        sa.Index('ix_payment_discounts_payment', 'transaction_id', 'id'),
        sa.Index('ix_payment_discounts_invoice', 'invoice_id', 'id'),
        description='Immutable early-payment discounts a customer receipt took, one per settlement edge.')

    bill_payment_discounts = T('bill_payment_discounts',
        identifier('id', 'Stable ULID of this discount.', primary_key=True),
        identifier('transaction_id', 'Bill payment that took the discount.'),
        identifier('revision_id', 'Bill-payment revision that posted the discount.'),
        identifier('source_component_id', 'Source component whose capacity carries the discount.'),
        identifier('application_id', 'Settlement edge the discount was taken on; its amount is cash plus discount.', 'ap_applications.id'),
        identifier('bill_id', 'Bill the discount settled.', 'transactions.id'),
        identifier('discount_account_id', 'Account credited for the discount.', 'accounts.id'),
        identifier('posting_source_id', 'Exact attribution row of the discount credit.'),
        integer('amount_minor_units', 'Positive discount amount.'),
        C('currency', sa.String(3), 'Home currency of the discount.', nullable=False),
        *terms('bill_payment'), *created(),
        sa.UniqueConstraint('application_id', name='uq_bill_payment_discount_application'),
        sa.ForeignKeyConstraint(['transaction_id', 'source_component_id'],
            ['ap_source_components.transaction_id', 'ap_source_components.id'], name='fk_bill_payment_discount_component'),
        sa.ForeignKeyConstraint(['transaction_id', 'posting_source_id'],
            ['posting_line_sources.transaction_id', 'posting_line_sources.id'], name='fk_bill_payment_discount_attribution'),
        sa.ForeignKeyConstraint(['transaction_id', 'revision_id'],
            ['transaction_revisions.transaction_id', 'transaction_revisions.id'], name='fk_bill_payment_discount_revision'),
        sa.Index('ix_bill_payment_discounts_payment', 'transaction_id', 'id'),
        sa.Index('ix_bill_payment_discounts_bill', 'bill_id', 'id'),
        description='Immutable early-payment discounts a bill payment took, one per settlement edge.')

    return {name: value for name, value in locals().items() if isinstance(value, sa.Table)}


IMMUTABLE = ('payment_discounts', 'bill_payment_discounts')


def guard_statements():
    """Immutable rows, and each discount no larger than the settlement edge it was taken on.

    An edge may be all discount: a document the payment gives no money settles by its discount.
    """
    for name in IMMUTABLE:
        for event in ('UPDATE', 'DELETE'):
            yield (f'CREATE TRIGGER {name}_immutable_{event.lower()} BEFORE {event} ON {name} '
                   "BEGIN SELECT RAISE(ABORT, 'immutable discount history'); END")
    yield ('CREATE TRIGGER payment_discounts_edge BEFORE INSERT ON payment_discounts '
           'WHEN NOT EXISTS (SELECT 1 FROM applications a WHERE a.id = NEW.application_id '
           "AND a.kind = 'apply' AND a.paying_transaction_id = NEW.transaction_id "
           'AND a.paid_transaction_id = NEW.invoice_id AND a.source_component_key_id = NEW.component_key_id '
           'AND a.currency = NEW.currency AND a.amount_minor_units >= NEW.amount_minor_units) '
           "BEGIN SELECT RAISE(ABORT, 'discount must be part of its own receipt settlement'); END")
    yield ('CREATE TRIGGER bill_payment_discounts_edge BEFORE INSERT ON bill_payment_discounts '
           'WHEN NOT EXISTS (SELECT 1 FROM ap_applications a WHERE a.id = NEW.application_id '
           "AND a.kind = 'apply' AND a.source_transaction_id = NEW.transaction_id "
           'AND a.source_component_id = NEW.source_component_id AND a.obligation_transaction_id = NEW.bill_id '
           'AND a.currency = NEW.currency AND a.amount_minor_units >= NEW.amount_minor_units) '
           "BEGIN SELECT RAISE(ABORT, 'discount must be part of its own bill settlement'); END")
