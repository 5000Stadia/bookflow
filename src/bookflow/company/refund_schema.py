"""Paying a customer back: the document's header, and the credit capacity it uses up.

Two tables, and the seam between them is the whole point.

**The document.** ``customer_refund_profiles`` is the one-to-one header of a refund revision:
which customer was paid, out of which receivable account, from which bank account, by what
method, and for how much. Its accounting is a debit to the receivable and a credit to the
funding account, and nothing else -- no income leg and no tax leg, because a refund does not
reverse a sale. The credit memo already did that; refunding it a second time would take the
revenue down twice. ``ar_posting_source_id`` names the exact attribution row that debited the
receivable, the same outward-pointing attribution ``credit_components`` and
``sales_tax_payment_profiles`` use.

**The capacity.** ``customer_refund_consumptions`` is what stops the same credit being both
refunded and applied. A credit memo creates capacity; an application spends it against an
invoice, and a consumption spends it as cash. Available credit is therefore capacity less
active applications less active consumptions -- one subtraction per kind of spending, and a
consumption is not an ``applications`` row because an application settles an obligation and
a refund has no obligation on the other side of it: nothing to allocate against, no sales
profile for the receivable reports to join to, and no due to fall.

A consumption is immutable and a void writes its exact release, the same shape
``credit_source_claims`` uses for a returned interval: the residue that answers "what is this
credit still worth" is then a function of rows that are only ever added.
"""

import sqlalchemy as sa


def define_tables(metadata, column, table):
    C, T = column, table

    def identifier(name, description, target=None, *, primary_key=False, nullable=False):
        constraints = [sa.ForeignKey(target)] if target else []
        return C(name, sa.String(26), description, *constraints,
                 primary_key=primary_key, nullable=nullable)

    def created():
        return [C('created_at', sa.String(32), 'UTC time this refund history was written.', nullable=False),
                identifier('created_by', 'Company principal that wrote this refund history.'),
                C('created_via', sa.String(16), 'Interface that wrote this refund history.', nullable=False),
                C('audit_event_id', sa.String(26), 'Audit event that committed this record.',
                  sa.ForeignKey('audit_events.id'), nullable=False)]

    customer_refund_profiles = T('customer_refund_profiles',
        identifier('revision_id', 'Immutable revision owning this one-to-one refund header.', primary_key=True),
        identifier('transaction_id', 'Stable refund document owning this revision.'),
        *created(),
        C('type', sa.String(32), 'Document type; customer_refund is the only one.', nullable=False),
        identifier('party_id', 'Customer or job paid back.', 'customers.id'),
        identifier('ar_account_id', 'Receivable account debited for the refunded amount.', 'accounts.id'),
        identifier('funding_account_id', 'Bank account credited for the money paid out.', 'accounts.id'),
        identifier('payment_method_id', 'Captured payment method.', 'payment_methods.id'),
        C('check_number', sa.String(64), 'Number written on the paper check, as entered; null unless the method is a check.', nullable=True),
        C('reference', sa.String(128), 'Entered refund reference; null when blank.', nullable=True),
        C('amount_minor_units', sa.BigInteger, 'Positive home-currency amount paid back.', nullable=False),
        C('currency', sa.String(3), 'Home currency of the refunded amount.', nullable=False),
        identifier('ar_posting_source_id', 'Exact attribution row that debited the receivable.'),
        C('profile_snapshot', sa.Text, 'Versioned typed JSON object of resolved customer, account, method and source facts.', nullable=False),
        sa.UniqueConstraint('transaction_id', 'revision_id', name='uq_customer_refund_profile_owner'),
        sa.ForeignKeyConstraint(['transaction_id', 'revision_id'],
            ['transaction_revisions.transaction_id', 'transaction_revisions.id'],
            name='fk_customer_refund_profile_revision'),
        sa.ForeignKeyConstraint(['transaction_id', 'type'],
            ['transactions.id', 'transactions.type'], name='fk_customer_refund_profile_type'),
        sa.ForeignKeyConstraint(['transaction_id', 'ar_posting_source_id'],
            ['posting_line_sources.transaction_id', 'posting_line_sources.id'],
            name='fk_customer_refund_profile_attribution'),
        sa.CheckConstraint("type = 'customer_refund'", name='ck_customer_refund_profile_type'),
        # A check number belongs to a paper check; the service refuses one whose method is not
        # a check, which is a fact about the method list rather than about this shape.
        sa.CheckConstraint("typeof(amount_minor_units) = 'integer' AND amount_minor_units > 0",
                           name='ck_customer_refund_amount_positive'),
        sa.CheckConstraint("json_valid(profile_snapshot) AND json_type(profile_snapshot) = 'object'",
                           name='ck_customer_refund_profile_snapshot_object'),
        sa.Index('ix_customer_refund_profiles_party', 'party_id', 'ar_account_id', 'transaction_id'),
        sa.Index('ix_customer_refund_profiles_attribution', 'ar_posting_source_id'),
        description='Immutable one-to-one customer refund headers naming the customer paid and the account it came from.')

    customer_refund_consumptions = T('customer_refund_consumptions',
        identifier('id', 'Immutable consumption or release identity.', primary_key=True),
        C('kind', sa.String(16), 'consume or release.', nullable=False),
        identifier('reverses_consumption_id', 'Exact consumption released; null on a consumption.',
                   'customer_refund_consumptions.id', nullable=True),
        identifier('transaction_id', 'Refund document spending this capacity.'),
        identifier('revision_id', 'Exact refund revision spending this capacity.'),
        identifier('credit_source_key_id', 'Permanent credit source spent.', 'credit_source_keys.id'),
        identifier('credit_source_component_id', 'Exact revision-local credit capacity spent.', 'credit_components.id'),
        C('amount_minor_units', sa.BigInteger, 'Positive home-currency capacity spent by this row.', nullable=False),
        C('currency', sa.String(3), 'Home currency of the spent capacity.', nullable=False),
        C('effective_date', sa.String(10), 'Accounting date of the refund that spent it.', nullable=False),
        *created(),
        sa.UniqueConstraint('reverses_consumption_id', name='uq_customer_refund_release'),
        sa.ForeignKeyConstraint(['transaction_id', 'revision_id'],
            ['customer_refund_profiles.transaction_id', 'customer_refund_profiles.revision_id'],
            name='fk_customer_refund_consumption_revision'),
        sa.CheckConstraint("(kind = 'consume' AND reverses_consumption_id IS NULL) OR"
                           " (kind = 'release' AND reverses_consumption_id IS NOT NULL"
                           " AND reverses_consumption_id <> id)", name='ck_customer_refund_consumption_kind'),
        sa.CheckConstraint("typeof(amount_minor_units) = 'integer' AND amount_minor_units > 0",
                           name='ck_customer_refund_consumption_positive'),
        sa.Index('ix_customer_refund_consumptions_source', 'credit_source_key_id', 'id'),
        sa.Index('ix_customer_refund_consumptions_component', 'credit_source_component_id', 'id'),
        description='Immutable positive credit capacity spent as cash, and the exact releases that give it back.')

    return {name: value for name, value in locals().items() if isinstance(value, sa.Table)}


IMMUTABLE = ('customer_refund_profiles', 'customer_refund_consumptions')

# The exact-inverse fence: a release must undo a real consumption, cell for cell, or
# "what is this credit still worth" stops meaning anything.
_RELEASE_FIELDS = ('transaction_id', 'revision_id', 'credit_source_key_id',
                   'credit_source_component_id', 'amount_minor_units', 'currency', 'effective_date')


def guard_statements():
    """Storage fences for refund history: immutable rows, right type, exact releases, one party."""
    for name in IMMUTABLE:
        for action in ('UPDATE', 'DELETE'):
            yield (f'CREATE TRIGGER {name}_immutable_{action.lower()} BEFORE {action} ON {name} '
                   "BEGIN SELECT RAISE(ABORT, 'immutable refund history'); END")
    yield ('CREATE TRIGGER customer_refund_consumptions_transaction_id_type BEFORE INSERT ON customer_refund_consumptions\n'
           'WHEN NOT EXISTS (SELECT 1 FROM transactions WHERE id = NEW.transaction_id '
           "AND type = 'customer_refund')\n"
           "BEGIN SELECT RAISE(ABORT, 'refund reference has wrong document type'); END")
    same = ' AND '.join(f'a.{name} IS NEW.{name}' for name in _RELEASE_FIELDS)
    yield ('CREATE TRIGGER customer_refund_consumptions_exact_release BEFORE INSERT ON customer_refund_consumptions\n'
           f"WHEN NEW.kind = 'release' AND NOT EXISTS (SELECT 1 FROM customer_refund_consumptions a\n"
           f"WHERE a.id = NEW.reverses_consumption_id AND a.kind = 'consume' AND {same})\n"
           "BEGIN SELECT RAISE(ABORT, 'release must exactly undo an original consumption'); END")
    # Exact party, exact receivable, exact currency, and a component that really belongs to
    # the named source. This is `applications_exact_party` restated for the cash disposition:
    # without it a refund could spend one customer's credit and pay another customer.
    yield ('CREATE TRIGGER customer_refund_consumptions_exact_party BEFORE INSERT ON customer_refund_consumptions\n'
           'WHEN NOT EXISTS (SELECT 1 FROM customer_refund_profiles p\n'
           'JOIN credit_source_keys k ON k.id = NEW.credit_source_key_id\n'
           'JOIN credit_components c ON c.id = NEW.credit_source_component_id\n'
           'WHERE p.transaction_id = NEW.transaction_id AND p.revision_id = NEW.revision_id\n'
           'AND c.key_id = k.id AND c.transaction_id = k.transaction_id\n'
           'AND c.currency = NEW.currency AND k.currency = NEW.currency AND p.currency = NEW.currency\n'
           'AND p.party_id = k.party_id AND p.ar_account_id = k.ar_account_id)\n'
           "BEGIN SELECT RAISE(ABORT, 'refund and credit source ownership differ'); END")
    # The refund's own receivable attribution has to be a debit to that customer on that
    # account: the leg the refund posts and the capacity it spends are the same money.
    yield ('CREATE TRIGGER customer_refund_profiles_owned_attribution BEFORE INSERT ON customer_refund_profiles\n'
           'WHEN NOT EXISTS (SELECT 1 FROM posting_line_sources ps\n'
           'JOIN posting_lines pl ON pl.id = ps.posting_line_id\n'
           'WHERE ps.transaction_id = NEW.transaction_id AND ps.id = NEW.ar_posting_source_id\n'
           'AND ps.revision_id = NEW.revision_id AND ps.reversed_source_id IS NULL\n'
           'AND ps.amount_minor_units = NEW.amount_minor_units\n'
           'AND pl.account_id = NEW.ar_account_id AND pl.debit_minor_units > 0\n'
           "AND pl.name_type = 'customer' AND pl.name_id = NEW.party_id)\n"
           "BEGIN SELECT RAISE(ABORT, 'refund attribution is not an owned receivable debit'); END")
