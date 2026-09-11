"""What paid a bill: the money that left, the capacity it carried, and where it landed.

Three families, and the seam between them is the point.

**The document.** ``ap_payment_profiles`` is the one-to-one header of a bill-payment
revision: the vendor, the payable it settles, the bank or credit-card account it was funded
from, the method, and the check number when the method is a check. Its accounting is one
debit to Accounts Payable and one credit to the funding account, which is the whole ledger
effect -- nothing below moves a cent.

**The capacity.** ``ap_source_keys`` is the permanent identity of the money a payment
carries: one stable row per payment, with the vendor, the payable account and the currency an
application has to match exactly, mirroring ``ap_obligation_keys`` on the other side.
``ap_source_components`` is that capacity broken into positive parts, each naming the exact
``posting_line_sources`` row that debited AP for it. A component is capacity, not a bill: what
binds it to a particular payable is the application, which is why unapplying one frees it
rather than destroying it.

**The edge.** ``ap_applications`` is a dated settlement edge from a source key to an
obligation key, plus its exact whole-edge inverse. Applications post nothing: the cash left
the bank when the payment posted, and an application only says which payable it answered. So
a bill's open balance is its gross less its active applications, while the vendor's payable
balance is the signed posting sum either way -- an unapplied payment is an unapplied debit,
not missing money.
"""

import sqlalchemy as sa


def define_tables(metadata, column, table):
    C, T = column, table

    def identifier(name, description, target=None, *, primary_key=False, nullable=False):
        constraints = [sa.ForeignKey(target)] if target else []
        return C(name, sa.String(26), description, *constraints,
                 primary_key=primary_key, nullable=nullable)

    def created():
        return [C('created_at', sa.String(32), 'UTC time this settlement history was written.', nullable=False),
                identifier('created_by', 'Company principal that wrote this settlement history.'),
                C('created_via', sa.String(16), 'Interface that wrote this settlement history.', nullable=False),
                C('audit_event_id', sa.String(26), 'Audit event that committed this record.',
                  sa.ForeignKey('audit_events.id'), nullable=False)]

    def integer(name, description):
        return C(name, sa.BigInteger, description, nullable=False)

    def positive(name):
        return sa.CheckConstraint(f"typeof({name}) = 'integer' AND {name} > 0",
                                  name=f'ck_ap_{name}_positive')

    def object_check(name):
        return sa.CheckConstraint(f"json_valid({name}) AND json_type({name}) = 'object'",
                                  name=f'ck_ap_{name}_object')

    ap_payment_profiles = T('ap_payment_profiles',
        identifier('revision_id', 'Immutable revision owning this one-to-one bill-payment header.', primary_key=True),
        identifier('transaction_id', 'Stable bill-payment document owning this revision.'),
        *created(),
        C('type', sa.String(32), 'Payment document type; bill_payment is the only implemented one.', nullable=False),
        identifier('vendor_id', 'Vendor this payment is made out to.', 'vendors.id'),
        identifier('ap_account_id', 'Accounts payable account debited for the settled amount.', 'accounts.id'),
        identifier('funding_account_id', 'Bank or credit-card account credited for the amount paid.', 'accounts.id'),
        C('funding_kind', sa.String(16), 'bank_cash when a bank account paid it; card_liability when a credit card did.', nullable=False),
        identifier('payment_method_id', 'Captured payment method.', 'payment_methods.id'),
        C('check_number', sa.String(64), 'Number written on the paper check, as entered; null unless the method is a check.', nullable=True),
        C('reference', sa.String(128), 'Entered remittance reference; null when blank.', nullable=True),
        integer('amount_minor_units', 'Positive home-currency amount this payment is written for.'),
        C('profile_snapshot', sa.Text, 'Versioned typed JSON object of resolved vendor, account, method and origin facts.', nullable=False),
        sa.UniqueConstraint('transaction_id', 'revision_id', name='uq_ap_payment_profile_owner'),
        sa.ForeignKeyConstraint(['transaction_id', 'revision_id'],
            ['transaction_revisions.transaction_id', 'transaction_revisions.id'], name='fk_ap_payment_profile_revision'),
        sa.ForeignKeyConstraint(['transaction_id', 'type'],
            ['transactions.id', 'transactions.type'], name='fk_ap_payment_profile_type'),
        sa.CheckConstraint("type = 'bill_payment'", name='ck_ap_payment_profile_type'),
        sa.CheckConstraint("funding_kind IN ('bank_cash', 'card_liability')", name='ck_ap_payment_funding_kind'),
        # A credit card has no check to write a number on; the service also refuses a number
        # whose method is not a check, which is a fact about the method list rather than the shape.
        sa.CheckConstraint("check_number IS NULL OR funding_kind = 'bank_cash'", name='ck_ap_payment_check_number'),
        positive('amount_minor_units'), object_check('profile_snapshot'),
        sa.Index('ix_ap_payment_profiles_vendor', 'vendor_id', 'ap_account_id', 'transaction_id'),
        description='Immutable one-to-one bill-payment revision headers with captured vendor, payable, funding and method facts.')

    ap_source_keys = T('ap_source_keys',
        identifier('id', 'Stable ULID of this settlement source; an application names this, never a revision.', primary_key=True),
        identifier('transaction_id', 'Document that permanently owns this source.', 'transactions.id'),
        integer('ordinal', 'One-based source ordinal within the document; a bill payment has exactly one.'),
        C('source_type', sa.String(32), 'Kind of money this source is; bill_payment is the only implemented one.', nullable=False),
        identifier('vendor_id', 'Vendor an application must match exactly.', 'vendors.id'),
        identifier('ap_account_id', 'Payable account an application must match exactly.', 'accounts.id'),
        C('currency', sa.String(3), 'Home currency an application must match exactly.', nullable=False),
        *created(),
        sa.UniqueConstraint('transaction_id', 'id', name='uq_ap_source_owner'),
        sa.UniqueConstraint('transaction_id', 'ordinal', name='uq_ap_source_ordinal'),
        sa.CheckConstraint("source_type = 'bill_payment'", name='ck_ap_source_type'),
        positive('ordinal'),
        sa.Index('ix_ap_source_keys_vendor', 'vendor_id', 'ap_account_id', 'id'),
        description='Stable settlement sources; retired ordinals are never reused and a correction never moves one.')

    ap_source_components = T('ap_source_components',
        identifier('id', 'Stable ULID of this immutable source component.', primary_key=True),
        identifier('transaction_id', 'Stable document owning this component.'),
        identifier('revision_id', 'Exact immutable payment revision owning this component.'),
        identifier('key_id', 'Settlement source this capacity belongs to.'),
        identifier('document_line_id', 'Entered payment line whose amount this component carries.'),
        integer('ordinal', 'One-based component ordinal within the entered line.'),
        identifier('posting_source_id', 'Exact payable attribution row this component was posted through.'),
        integer('amount_minor_units', 'Positive home-currency capacity of this component.'),
        C('currency', sa.String(3), 'Home currency of this component.', nullable=False),
        *created(),
        sa.UniqueConstraint('revision_id', 'document_line_id', 'ordinal', name='uq_ap_source_component_occurrence'),
        sa.UniqueConstraint('transaction_id', 'revision_id', 'id', name='uq_ap_source_component_revision'),
        sa.UniqueConstraint('transaction_id', 'id', name='uq_ap_source_component_owner'),
        sa.ForeignKeyConstraint(['transaction_id', 'key_id'],
            ['ap_source_keys.transaction_id', 'ap_source_keys.id'], name='fk_ap_source_component_key'),
        sa.ForeignKeyConstraint(['transaction_id', 'revision_id', 'document_line_id'],
            ['document_lines.transaction_id', 'document_lines.revision_id', 'document_lines.id'], name='fk_ap_source_component_envelope'),
        sa.ForeignKeyConstraint(['transaction_id', 'posting_source_id'],
            ['posting_line_sources.transaction_id', 'posting_line_sources.id'], name='fk_ap_source_component_attribution'),
        positive('ordinal'), positive('amount_minor_units'),
        sa.Index('ix_ap_source_components_key', 'key_id', 'revision_id'),
        description='Immutable positive settlement capacity, tied to the exact posting attribution that created it.')

    ap_applications = T('ap_applications',
        identifier('id', 'Immutable apply or unapply identity.', primary_key=True),
        C('kind', sa.String(16), 'apply or unapply.', nullable=False),
        identifier('source_transaction_id', 'Document supplying the capacity.'),
        identifier('source_key_id', 'Stable settlement source consumed.'),
        identifier('source_component_id', 'Exact revision-local capacity consumed.'),
        identifier('obligation_transaction_id', 'Bill consuming the capacity.'),
        identifier('obligation_key_id', 'Stable payable settled.'),
        integer('amount_minor_units', 'Positive settled amount.'),
        C('currency', sa.String(3), 'Settlement currency.', nullable=False),
        C('effective_date', sa.String(10), 'Settlement accounting date.', nullable=False),
        identifier('reverses_application_id', 'Original apply being fully unapplied; null on an apply.',
                   'ap_applications.id', nullable=True),
        *created(),
        positive('amount_minor_units'),
        sa.CheckConstraint(
            "(kind = 'apply' AND reverses_application_id IS NULL) OR "
            "(kind = 'unapply' AND reverses_application_id IS NOT NULL AND reverses_application_id <> id)",
            name='ck_ap_application_kind'),
        sa.UniqueConstraint('reverses_application_id', name='uq_ap_application_inverse'),
        sa.ForeignKeyConstraint(['source_transaction_id', 'source_key_id'],
            ['ap_source_keys.transaction_id', 'ap_source_keys.id'], name='fk_ap_application_source'),
        sa.ForeignKeyConstraint(['source_transaction_id', 'source_component_id'],
            ['ap_source_components.transaction_id', 'ap_source_components.id'], name='fk_ap_application_component'),
        sa.ForeignKeyConstraint(['obligation_transaction_id', 'obligation_key_id'],
            ['ap_obligation_keys.transaction_id', 'ap_obligation_keys.id'], name='fk_ap_application_target'),
        sa.Index('ix_ap_applications_obligation', 'obligation_key_id', 'effective_date', 'id'),
        sa.Index('ix_ap_applications_source', 'source_key_id', 'effective_date', 'id'),
        description='Immutable dated payable settlement edges and their exact whole-edge inverses; no ledger posting.')

    return {name: value for name, value in locals().items() if isinstance(value, sa.Table)}


IMMUTABLE = ('ap_payment_profiles', 'ap_source_keys', 'ap_source_components', 'ap_applications')

# The exact-inverse fence: an unapply must reverse a real apply, cell for cell, or the sums
# that answer "what is still open on this bill" stop meaning anything.
_INVERSE_FIELDS = ('source_transaction_id', 'source_key_id', 'source_component_id',
                   'obligation_transaction_id', 'obligation_key_id', 'amount_minor_units',
                   'currency', 'effective_date')


def guard_statements():
    """Storage fences for settlement history: immutable rows, right types, exact inverses."""
    for table in IMMUTABLE:
        for event in ('UPDATE', 'DELETE'):
            yield (f'CREATE TRIGGER {table}_immutable_{event.lower()} BEFORE {event} ON {table} '
                   "BEGIN SELECT RAISE(ABORT, 'immutable settlement history'); END")
    for table, field, kind in (('ap_source_keys', 'transaction_id', 'bill_payment'),
                               ('ap_applications', 'source_transaction_id', 'bill_payment'),
                               ('ap_applications', 'obligation_transaction_id', 'bill')):
        yield (f'CREATE TRIGGER {table}_{field}_type BEFORE INSERT ON {table}\n'
               f'WHEN NOT EXISTS (SELECT 1 FROM transactions WHERE id = NEW.{field} '
               f"AND type = '{kind}')\n"
               "BEGIN SELECT RAISE(ABORT, 'settlement reference has wrong document type'); END")
    same = ' AND '.join(f'a.{name} IS NEW.{name}' for name in _INVERSE_FIELDS)
    yield (f'CREATE TRIGGER ap_applications_exact_inverse BEFORE INSERT ON ap_applications\n'
           f"WHEN NEW.kind = 'unapply' AND NOT EXISTS (SELECT 1 FROM ap_applications a\n"
           f"WHERE a.id = NEW.reverses_application_id AND a.kind = 'apply' AND {same})\n"
           "BEGIN SELECT RAISE(ABORT, 'unapply must exactly reverse an original application'); END")
    yield ('CREATE TRIGGER ap_applications_exact_party BEFORE INSERT ON ap_applications\n'
           'WHEN NOT EXISTS (SELECT 1 FROM ap_source_keys s JOIN ap_obligation_keys o\n'
           'ON o.id = NEW.obligation_key_id AND o.transaction_id = NEW.obligation_transaction_id\n'
           'WHERE s.id = NEW.source_key_id AND s.transaction_id = NEW.source_transaction_id\n'
           'AND s.vendor_id = o.vendor_id AND s.ap_account_id = o.ap_account_id\n'
           'AND s.currency = o.currency AND s.currency = NEW.currency)\n'
           "BEGIN SELECT RAISE(ABORT, 'application source and target ownership differ'); END")
