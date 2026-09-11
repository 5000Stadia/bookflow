"""What a customer credit is: the document, the capacity it creates, and what it claims back.

Three families, and the seam between them is the point.

**The document.** ``credit_profiles``, ``credit_line_profiles`` and ``credit_tax_components``
mirror ``sales_profiles``, ``sales_line_profiles`` and ``sales_tax_components`` cell for cell,
because a credit memo is a sale read backwards: the same item, the same income account, the
same captured tax components, posted the other way round. Its accounting is a debit to each
line's captured income account, a debit to each captured tax liability, and one credit to the
customer's receivable for the gross.

**The capacity.** ``credit_source_keys`` is the permanent identity of the credit a document
carries -- one row per credit memo, with the customer, the receivable account and the currency
an application has to match exactly, deliberately the same dimensions
``payment_component_keys`` carries so that available credit is one projection over both.
``credit_components`` is that capacity broken into positive parts, one per entered line, each
naming the exact ``posting_line_sources`` row that credited the receivable for it. Attribution
points outward from here: ``posting_line_sources`` gains no column and knows nothing about
credits, exactly as ``ap_obligation_components`` established.

**The claim.** A credit that returns something claims a half-open quantity interval on the
source invoice line it returns, in base microunits, and ``credit_source_claims`` holds those
intervals immutably. Ownership is by endpoint -- an interval ``[a,b)`` of a captured line of
quantity ``Q`` and net ``N`` owns ``floor(N*b/Q) - floor(N*a/Q)`` -- so any disjoint set of
claims telescopes to exactly ``N``, whatever order they were made in, and releasing one span
and claiming it again returns the identical cents. That is why the interval is stored and the
money is not: the cents are a function of the interval and the capture, and two facts that can
disagree are worse than one fact and a rule.

Settlement itself is not here. A credit applies through the same ``applications`` and
``application_allocations`` rows a receipt uses, because the target is the same invoice with
the same settlement ordinals, the same recognition roles and the same inverse rules; those two
tables carry one nullable credit-source column each.
"""

import sqlalchemy as sa


def define_tables(metadata, column, table):
    C, T = column, table

    def identifier(name, description, target=None, *, primary_key=False, nullable=False):
        constraints = [sa.ForeignKey(target)] if target else []
        return C(name, sa.String(26), description, *constraints,
                 primary_key=primary_key, nullable=nullable)

    def created():
        return [C('created_at', sa.String(32), 'UTC time this credit history was written.', nullable=False),
                identifier('created_by', 'Company principal that wrote this credit history.'),
                C('created_via', sa.String(16), 'Interface that wrote this credit history.', nullable=False),
                C('audit_event_id', sa.String(26), 'Audit event that committed this record.',
                  sa.ForeignKey('audit_events.id'), nullable=False)]

    def integer(name, description):
        return C(name, sa.BigInteger, description, nullable=False)

    def positive(name):
        return sa.CheckConstraint(f"typeof({name}) = 'integer' AND {name} > 0",
                                  name=f'ck_credit_{name}_positive')

    def nonnegative(name):
        return sa.CheckConstraint(f"typeof({name}) = 'integer' AND {name} >= 0",
                                  name=f'ck_credit_{name}_nonnegative')

    def object_check(name):
        return sa.CheckConstraint(f"json_valid({name}) AND json_type({name}) = 'object'",
                                  name=f'ck_credit_{name}_object')

    credit_profiles = T('credit_profiles',
        identifier('revision_id', 'Immutable revision owning this one-to-one credit header.', primary_key=True),
        identifier('transaction_id', 'Stable credit memo owning this revision.'),
        *created(),
        C('type', sa.String(32), 'Commercial type; credit_memo is the only one.', nullable=False),
        identifier('customer_id', 'Customer or job the credit belongs to.', 'customers.id'),
        identifier('ar_account_id', 'Receivable account credited for the gross.', 'accounts.id'),
        C('origin', sa.String(16), 'standalone when the credit names its own items; return when every line claims a source invoice line.', nullable=False),
        integer('subtotal_minor_units', 'Home-currency net total before tax.'),
        integer('tax_minor_units', 'Home-currency sum of captured component taxes.'),
        C('profile_snapshot', sa.Text, 'Versioned typed JSON object of resolved customer, account, tax and origin facts.', nullable=False),
        C('tax_attribution_snapshot', sa.Text, 'Typed JSON object of the tax calculation that produced these cells; null on a return, whose cells were captured by the source invoice and never calculated here.', nullable=True),
        sa.UniqueConstraint('transaction_id', 'revision_id', name='uq_credit_profile_owner'),
        sa.ForeignKeyConstraint(['transaction_id', 'revision_id'],
            ['transaction_revisions.transaction_id', 'transaction_revisions.id'], name='fk_credit_profile_revision'),
        sa.ForeignKeyConstraint(['transaction_id', 'type'],
            ['transactions.id', 'transactions.type'], name='fk_credit_profile_type'),
        sa.CheckConstraint("type = 'credit_memo'", name='ck_credit_profile_type'),
        sa.CheckConstraint("origin IN ('standalone', 'return')", name='ck_credit_profile_origin'),
        nonnegative('subtotal_minor_units'), nonnegative('tax_minor_units'), object_check('profile_snapshot'),
        sa.CheckConstraint("tax_attribution_snapshot IS NULL OR (json_valid(tax_attribution_snapshot)"
                           " AND json_type(tax_attribution_snapshot) = 'object')", name='ck_credit_attribution_object'),
        sa.CheckConstraint("(origin = 'return') = (tax_attribution_snapshot IS NULL)", name='ck_credit_attribution_origin'),
        sa.Index('ix_credit_profiles_customer', 'customer_id', 'ar_account_id', 'transaction_id'),
        description='Immutable one-to-one credit memo revision headers, resolved customer facts and commercial totals.')

    credit_line_profiles = T('credit_line_profiles',
        identifier('document_line_id', 'Revision-local credit envelope owning this one-to-one profile.', primary_key=True),
        identifier('transaction_id', 'Stable credit memo owning this commercial line.'),
        identifier('revision_id', 'Exact immutable credit revision owning this line.'),
        *created(),
        identifier('item_id', 'Captured credited item.', 'items.id'),
        C('quantity_microunits', sa.BigInteger, 'Exact credited selected-unit quantity in millionths.', nullable=False),
        identifier('unit_id', 'Captured selected unit conversion; null without a unit.', 'unit_conversions.id', nullable=True),
        integer('unit_factor_nanounits', 'Positive captured base units per selected unit in billionths.'),
        C('base_quantity_microunits', sa.BigInteger, 'Exact credited base quantity in millionths.', nullable=False),
        C('unit_price_minor_units', sa.BigInteger, 'Home-currency price per selected unit; null when the net is taken rather than priced.', nullable=True),
        C('pricing_basis', sa.String(16), 'Authoritative unit or amount pricing mode.', nullable=False),
        integer('net_minor_units', 'Home-currency line net before tax.'),
        integer('tax_minor_units', 'Home-currency sum of this line tax components.'),
        integer('gross_minor_units', 'Home-currency line net plus tax.'),
        C('item_snapshot', sa.Text, 'Versioned typed JSON object of resolved item, account, unit, pricing and tax rules.', nullable=False),
        identifier('posting_source_id', 'Exact income attribution row this line net was posted through; null only when the line is worth nothing and posts no leg.', nullable=True),
        identifier('source_transaction_id', 'Source invoice this line returns; null on a standalone credit.', nullable=True),
        identifier('source_revision_id', 'Exact captured source invoice revision; null on a standalone credit.', nullable=True),
        identifier('source_document_line_id', 'Exact captured source commercial line; null on a standalone credit.', nullable=True),
        identifier('source_line_id', 'Permanent source line occurrence; null on a standalone credit.', nullable=True),
        sa.UniqueConstraint('transaction_id', 'revision_id', 'document_line_id', name='uq_credit_line_profile_owner'),
        sa.ForeignKeyConstraint(['transaction_id', 'revision_id'],
            ['credit_profiles.transaction_id', 'credit_profiles.revision_id'], name='fk_credit_line_profile_revision'),
        sa.ForeignKeyConstraint(['transaction_id', 'revision_id', 'document_line_id'],
            ['document_lines.transaction_id', 'document_lines.revision_id', 'document_lines.id'], name='fk_credit_line_profile_line'),
        sa.ForeignKeyConstraint(['transaction_id', 'posting_source_id'],
            ['posting_line_sources.transaction_id', 'posting_line_sources.id'], name='fk_credit_line_profile_attribution'),
        sa.ForeignKeyConstraint(['source_transaction_id', 'source_revision_id', 'source_document_line_id'],
            ['sales_line_profiles.transaction_id', 'sales_line_profiles.revision_id', 'sales_line_profiles.document_line_id'],
            name='fk_credit_line_profile_source'),
        sa.ForeignKeyConstraint(['source_transaction_id', 'source_line_id'],
            ['document_line_identities.transaction_id', 'document_line_identities.id'], name='fk_credit_line_profile_source_identity'),
        positive('quantity_microunits'), positive('unit_factor_nanounits'), positive('base_quantity_microunits'),
        sa.CheckConstraint("pricing_basis IN ('unit', 'amount')", name='ck_credit_pricing_basis'),
        sa.CheckConstraint("(pricing_basis = 'unit' AND typeof(unit_price_minor_units) = 'integer' AND unit_price_minor_units >= 0)"
                           " OR (pricing_basis = 'amount' AND unit_price_minor_units IS NULL)", name='ck_credit_line_price'),
        # A returned line names its source completely or not at all: a half-named source is a
        # claim nobody can price and a cell nobody can trace.
        sa.CheckConstraint("(source_transaction_id IS NULL) = (source_revision_id IS NULL)"
                           " AND (source_transaction_id IS NULL) = (source_document_line_id IS NULL)"
                           " AND (source_transaction_id IS NULL) = (source_line_id IS NULL)", name='ck_credit_line_source_pair'),
        nonnegative('net_minor_units'), nonnegative('tax_minor_units'), nonnegative('gross_minor_units'),
        # A leg exists exactly when there is money to post. An endpoint-owned interval of a
        # one-cent line really can be worth nothing, and refusing that would make the first
        # unit of such a line unreturnable while all three units together were returnable.
        sa.CheckConstraint('(net_minor_units = 0) = (posting_source_id IS NULL)', name='ck_credit_line_attribution'),
        object_check('item_snapshot'),
        description='Immutable one-to-one credited item lines with exact quantities, captured rules and their source line.')

    credit_tax_components = T('credit_tax_components',
        identifier('id', 'Stable ULID of this immutable tax component.', primary_key=True),
        identifier('transaction_id', 'Stable credit memo owning this tax component.'),
        identifier('revision_id', 'Exact immutable credit revision owning this tax component.'),
        identifier('document_line_id', 'Exact revision-local credited line taxed by this component.'),
        *created(),
        identifier('tax_item_id', 'Captured individual sales tax item.', 'items.id'),
        identifier('agency_id', 'Captured tax agency vendor.', 'vendors.id'),
        identifier('liability_account_id', 'Captured sales tax payable posting account.', 'accounts.id'),
        integer('rate_percent_millionths', 'Nonnegative tax percentage in millionths of one percent.'),
        integer('taxable_minor_units', 'Home-currency amount subject to this component.'),
        integer('tax_minor_units', 'Captured home-currency component tax reversed by this credit; zero is retained.'),
        C('component_snapshot', sa.Text, 'Versioned typed JSON object of captured tax item, agency and liability labels.', nullable=False),
        identifier('posting_source_id', 'Exact tax liability attribution row this component was posted through; null only on a retained zero-tax cell, which posts no leg.', nullable=True),
        identifier('source_tax_component_id', 'Exact captured source invoice tax cell; null on a standalone credit.', nullable=True),
        sa.UniqueConstraint('transaction_id', 'revision_id', 'document_line_id', 'id', name='uq_credit_tax_component_owner'),
        sa.ForeignKeyConstraint(['transaction_id', 'revision_id', 'document_line_id'],
            ['credit_line_profiles.transaction_id', 'credit_line_profiles.revision_id', 'credit_line_profiles.document_line_id'],
            name='fk_credit_tax_component_line'),
        sa.ForeignKeyConstraint(['transaction_id', 'posting_source_id'],
            ['posting_line_sources.transaction_id', 'posting_line_sources.id'], name='fk_credit_tax_component_attribution'),
        nonnegative('rate_percent_millionths'), nonnegative('taxable_minor_units'), nonnegative('tax_minor_units'),
        sa.CheckConstraint('(tax_minor_units = 0) = (posting_source_id IS NULL)', name='ck_credit_tax_attribution'),
        object_check('component_snapshot'),
        description='Immutable captured tax components reversed by a credit, including zero-rate and rounded-to-zero facts.')

    credit_source_keys = T('credit_source_keys',
        identifier('id', 'Permanent exact-party credit identity; an application names this, never a revision.', primary_key=True),
        identifier('transaction_id', 'Credit memo that permanently owns this source.', 'transactions.id'),
        identifier('party_id', 'Customer an application must match exactly.', 'customers.id'),
        identifier('ar_account_id', 'Receivable account an application must match exactly.', 'accounts.id'),
        C('currency', sa.String(3), 'Home currency an application must match exactly.', nullable=False),
        *created(),
        sa.UniqueConstraint('transaction_id', 'party_id', 'ar_account_id', 'currency', name='uq_credit_source_dimensions'),
        sa.UniqueConstraint('transaction_id', 'id', name='uq_credit_source_owner'),
        sa.Index('ix_credit_source_keys_party', 'party_id', 'ar_account_id', 'id'),
        description='Permanent credit source ownership; never reassigned after the credit is issued.')

    credit_components = T('credit_components',
        identifier('id', 'Stable ULID of this immutable credit component.', primary_key=True),
        identifier('transaction_id', 'Stable credit memo owning this component.'),
        identifier('revision_id', 'Exact immutable credit revision owning this component.'),
        identifier('key_id', 'Credit source this capacity belongs to.'),
        identifier('document_line_id', 'Credited line whose gross this component carries.'),
        identifier('posting_source_id', 'Exact receivable attribution row this component was posted through.'),
        integer('amount_minor_units', 'Positive home-currency capacity of this component.'),
        C('currency', sa.String(3), 'Home currency of this component.', nullable=False),
        *created(),
        sa.UniqueConstraint('revision_id', 'document_line_id', name='uq_credit_component_occurrence'),
        sa.UniqueConstraint('transaction_id', 'revision_id', 'id', name='uq_credit_component_revision'),
        sa.UniqueConstraint('transaction_id', 'id', name='uq_credit_component_owner'),
        sa.ForeignKeyConstraint(['transaction_id', 'key_id'],
            ['credit_source_keys.transaction_id', 'credit_source_keys.id'], name='fk_credit_component_key'),
        sa.ForeignKeyConstraint(['transaction_id', 'revision_id', 'document_line_id'],
            ['document_lines.transaction_id', 'document_lines.revision_id', 'document_lines.id'], name='fk_credit_component_envelope'),
        sa.ForeignKeyConstraint(['transaction_id', 'posting_source_id'],
            ['posting_line_sources.transaction_id', 'posting_line_sources.id'], name='fk_credit_component_attribution'),
        positive('amount_minor_units'),
        sa.Index('ix_credit_components_key', 'key_id', 'revision_id'),
        description='Immutable positive credit capacity, tied to the exact receivable attribution that created it.')

    credit_source_claims = T('credit_source_claims',
        identifier('id', 'Immutable claim or release identity.', primary_key=True),
        C('kind', sa.String(16), 'claim or release.', nullable=False),
        identifier('reverses_claim_id', 'Exact claim released; null on a claim.', 'credit_source_claims.id', nullable=True),
        identifier('credit_transaction_id', 'Credit memo making this claim.'),
        identifier('credit_revision_id', 'Exact credit revision making this claim.'),
        identifier('credit_document_line_id', 'Credited line this interval was priced into.'),
        identifier('source_transaction_id', 'Source invoice claimed.'),
        identifier('source_revision_id', 'Exact captured source revision the interval was priced from.'),
        identifier('source_document_line_id', 'Exact captured source commercial line.'),
        identifier('source_line_id', 'Permanent source line occurrence, stable across source corrections.'),
        integer('start_microunits', 'Inclusive interval start in source base microunits.'),
        integer('end_microunits', 'Exclusive interval end in source base microunits.'),
        integer('source_base_quantity_microunits', 'Captured source base quantity the interval is measured against.'),
        integer('source_net_minor_units', 'Captured source line net the interval is priced against.'),
        C('effective_date', sa.String(10), 'Accounting date of the credit that made this claim.', nullable=False),
        *created(),
        sa.UniqueConstraint('reverses_claim_id', name='uq_credit_claim_release'),
        sa.ForeignKeyConstraint(['credit_transaction_id', 'credit_revision_id', 'credit_document_line_id'],
            ['credit_line_profiles.transaction_id', 'credit_line_profiles.revision_id', 'credit_line_profiles.document_line_id'],
            name='fk_credit_claim_line'),
        sa.ForeignKeyConstraint(['source_transaction_id', 'source_revision_id', 'source_document_line_id'],
            ['sales_line_profiles.transaction_id', 'sales_line_profiles.revision_id', 'sales_line_profiles.document_line_id'],
            name='fk_credit_claim_source'),
        sa.ForeignKeyConstraint(['source_transaction_id', 'source_line_id'],
            ['document_line_identities.transaction_id', 'document_line_identities.id'], name='fk_credit_claim_source_identity'),
        sa.CheckConstraint("(kind = 'claim' AND reverses_claim_id IS NULL) OR"
                           " (kind = 'release' AND reverses_claim_id IS NOT NULL AND reverses_claim_id <> id)",
                           name='ck_credit_claim_kind'),
        sa.CheckConstraint("typeof(start_microunits) = 'integer' AND start_microunits >= 0"
                           " AND typeof(end_microunits) = 'integer' AND end_microunits > start_microunits"
                           " AND end_microunits <= source_base_quantity_microunits", name='ck_credit_claim_interval'),
        positive('source_base_quantity_microunits'), nonnegative('source_net_minor_units'),
        sa.Index('ix_credit_source_claims_line', 'source_transaction_id', 'source_line_id', 'id'),
        description='Immutable half-open returned-quantity intervals and their exact releases; the cents are a function of these.')

    return {name: value for name, value in locals().items() if isinstance(value, sa.Table)}


IMMUTABLE = ('credit_profiles', 'credit_line_profiles', 'credit_tax_components',
             'credit_source_keys', 'credit_components', 'credit_source_claims')

# The exact-inverse fence: a release must undo a real claim, cell for cell, or the residue
# that answers "what is still returnable on this line" stops meaning anything.
_RELEASE_FIELDS = ('credit_transaction_id', 'credit_revision_id', 'credit_document_line_id',
                   'source_transaction_id', 'source_revision_id', 'source_document_line_id',
                   'source_line_id', 'start_microunits', 'end_microunits',
                   'source_base_quantity_microunits', 'source_net_minor_units', 'effective_date')


def guard_statements():
    """Storage fences for credit history: immutable rows, right types, exact releases."""
    for table in IMMUTABLE:
        for event in ('UPDATE', 'DELETE'):
            yield (f'CREATE TRIGGER {table}_immutable_{event.lower()} BEFORE {event} ON {table} '
                   "BEGIN SELECT RAISE(ABORT, 'immutable credit history'); END")
    for table, field, kind in (('credit_source_keys', 'transaction_id', 'credit_memo'),
                               ('credit_source_claims', 'credit_transaction_id', 'credit_memo'),
                               ('credit_source_claims', 'source_transaction_id', 'invoice')):
        yield (f'CREATE TRIGGER {table}_{field}_type BEFORE INSERT ON {table}\n'
               f'WHEN NOT EXISTS (SELECT 1 FROM transactions WHERE id = NEW.{field} '
               f"AND type = '{kind}')\n"
               "BEGIN SELECT RAISE(ABORT, 'credit reference has wrong document type'); END")
    same = ' AND '.join(f'a.{name} IS NEW.{name}' for name in _RELEASE_FIELDS)
    yield (f'CREATE TRIGGER credit_source_claims_exact_release BEFORE INSERT ON credit_source_claims\n'
           f"WHEN NEW.kind = 'release' AND NOT EXISTS (SELECT 1 FROM credit_source_claims a\n"
           f"WHERE a.id = NEW.reverses_claim_id AND a.kind = 'claim' AND {same})\n"
           "BEGIN SELECT RAISE(ABORT, 'release must exactly undo an original claim'); END")
    # A component is capacity only because a receivable credit created it. Without this the
    # capacity could name any attribution row on the document, including a tax debit.
    yield ('CREATE TRIGGER credit_components_owned_attribution BEFORE INSERT ON credit_components\n'
           'WHEN NOT EXISTS (SELECT 1 FROM credit_source_keys k\n'
           'JOIN posting_line_sources ps ON ps.transaction_id = NEW.transaction_id AND ps.id = NEW.posting_source_id\n'
           'JOIN posting_lines pl ON pl.id = ps.posting_line_id\n'
           'WHERE k.id = NEW.key_id AND k.transaction_id = NEW.transaction_id\n'
           'AND k.currency = NEW.currency AND ps.revision_id = NEW.revision_id\n'
           'AND ps.document_line_id = NEW.document_line_id AND ps.reversed_source_id IS NULL\n'
           'AND ps.amount_minor_units = NEW.amount_minor_units\n'
           'AND pl.account_id = k.ar_account_id AND pl.credit_minor_units > 0\n'
           "AND pl.name_type = 'customer' AND pl.name_id = k.party_id)\n"
           "BEGIN SELECT RAISE(ABORT, 'credit capacity is not an owned receivable credit'); END")


# The settlement fences as they stand once a credit can supply capacity. These are
# replacements for triggers first written by co0014 (and, for the document-line kind guard,
# last rewritten by co0026): one receivable edge with two kinds of source needs the same
# assertions made twice, once per branch, rather than one branch silently unguarded.
_INVERSE_FIELDS = ('paying_transaction_id', 'paid_transaction_id', 'source_component_key_id',
                   'credit_source_key_id', 'amount_minor_units', 'currency', 'effective_date')
_ALLOCATION_FIELDS = ('application_id', 'source_transaction_id', 'source_revision_id', 'source_component_id',
                      'credit_source_component_id', 'source_posting_source_id', 'target_transaction_id',
                      'target_revision_id', 'target_document_line_id', 'target_line_id', 'target_ordinal',
                      'logical_kind', 'tax_item_id', 'tax_component_id', 'target_ar_source_id',
                      'target_recognition_source_id', 'recognition_role', 'amount_minor_units',
                      'currency', 'effective_date', 'facts_snapshot')

_OWNED_SOURCE_ARM = """SELECT 1 FROM applications a
JOIN {components} c ON c.id = NEW.{component}
JOIN posting_line_sources ps ON ps.id = NEW.source_posting_source_id
JOIN posting_lines pl ON pl.id = ps.posting_line_id
JOIN {keys} k ON k.id = c.{key}
JOIN posting_line_sources ar ON ar.id = NEW.target_ar_source_id
JOIN posting_lines arl ON arl.id = ar.posting_line_id
JOIN posting_line_sources rec ON rec.id = NEW.target_recognition_source_id
JOIN posting_lines recl ON recl.id = rec.posting_line_id
JOIN document_lines d ON d.id = NEW.target_document_line_id
WHERE a.id = NEW.application_id AND a.kind = 'apply'
AND a.paying_transaction_id = NEW.source_transaction_id AND a.paid_transaction_id = NEW.target_transaction_id
AND a.{edge} = c.{key} AND a.currency = NEW.currency AND a.effective_date = NEW.effective_date
AND {attribution} AND ps.revision_id = NEW.source_revision_id
AND ps.reversed_source_id IS NULL AND pl.account_id = k.{ar} AND pl.credit_minor_units > 0
AND pl.name_type = 'customer' AND pl.name_id = k.party_id
AND ar.transaction_id = NEW.target_transaction_id AND rec.transaction_id = NEW.target_transaction_id
AND ar.revision_id = NEW.target_revision_id AND rec.revision_id = NEW.target_revision_id
AND ar.document_line_id = d.id AND rec.document_line_id = d.id AND d.line_id = NEW.target_line_id
AND ar.tax_component_id IS NEW.tax_component_id AND rec.tax_component_id IS NEW.tax_component_id
AND ar.reversed_source_id IS NULL AND rec.reversed_source_id IS NULL
AND arl.account_id = k.{ar} AND arl.debit_minor_units > 0 AND recl.credit_minor_units > 0
AND arl.name_type = 'customer' AND arl.name_id = k.party_id
AND (NEW.logical_kind = 'net' OR EXISTS (SELECT 1 FROM sales_tax_components tc
WHERE tc.id = NEW.tax_component_id AND tc.tax_item_id = NEW.tax_item_id AND tc.liability_account_id = recl.account_id))"""

DOCUMENT_LINE_KINDS = (('journal_entry', 'journal'), (('invoice', 'sales_receipt'), 'sale'),
                       ('payment', 'payment'), ('deposit', 'deposit'), ('bill', 'purchase'),
                       ('bill_payment', 'bill_payment'), ('credit_memo', 'credit'),
                       ('sales_tax_payment', 'sales_tax_payment'))


def settlement_guard_statements():
    """Receivable settlement fences restated for two kinds of source."""
    yield ('CREATE TRIGGER applications_paying_transaction_id_type BEFORE INSERT ON applications\n'
           'WHEN NEW.paying_transaction_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM transactions '
           "WHERE id = NEW.paying_transaction_id AND type IN ('payment', 'credit_memo'))\n"
           "BEGIN SELECT RAISE(ABORT, 'payment reference has wrong document type'); END")
    yield ('CREATE TRIGGER applications_one_source BEFORE INSERT ON applications\n'
           'WHEN (NEW.source_component_key_id IS NOT NULL) + (NEW.credit_source_key_id IS NOT NULL) <> 1\n'
           'OR (NEW.source_component_key_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM payment_component_keys k\n'
           'WHERE k.id = NEW.source_component_key_id AND k.transaction_id = NEW.paying_transaction_id))\n'
           'OR (NEW.credit_source_key_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM credit_source_keys k\n'
           'WHERE k.id = NEW.credit_source_key_id AND k.transaction_id = NEW.paying_transaction_id))\n'
           "BEGIN SELECT RAISE(ABORT, 'an application has exactly one source, owned by the paying document'); END")
    yield ('CREATE TRIGGER applications_exact_party BEFORE INSERT ON applications\n'
           'WHEN NOT EXISTS (SELECT 1 FROM transactions t\n'
           'JOIN sales_profiles p ON p.revision_id = t.current_revision_id\n'
           'JOIN transaction_revisions r ON r.id = p.revision_id\n'
           'WHERE t.id = NEW.paid_transaction_id AND r.currency = NEW.currency\n'
           'AND (EXISTS (SELECT 1 FROM payment_component_keys k WHERE k.id = NEW.source_component_key_id\n'
           'AND k.party_id = p.customer_id AND k.ar_account_id = p.control_account_id AND k.currency = NEW.currency)\n'
           'OR EXISTS (SELECT 1 FROM credit_source_keys k WHERE k.id = NEW.credit_source_key_id\n'
           'AND k.party_id = p.customer_id AND k.ar_account_id = p.control_account_id AND k.currency = NEW.currency)))\n'
           "BEGIN SELECT RAISE(ABORT, 'application source and target ownership differ'); END")
    same = ' AND '.join(f'a.{name} IS NEW.{name}' for name in _INVERSE_FIELDS)
    yield ('CREATE TRIGGER applications_exact_inverse BEFORE INSERT ON applications\n'
           "WHEN NEW.kind = 'unapply' AND NOT EXISTS (SELECT 1 FROM applications a\n"
           f"WHERE a.id = NEW.reverses_application_id AND a.kind = 'apply' AND {same})\n"
           "BEGIN SELECT RAISE(ABORT, 'unapply must exactly reverse original application'); END")
    same = ' AND '.join(f'a.{name} IS NEW.{name}' for name in _ALLOCATION_FIELDS)
    yield ('CREATE TRIGGER application_allocations_exact_inverse BEFORE INSERT ON application_allocations\n'
           "WHEN NEW.kind = 'reversal' AND NOT EXISTS (SELECT 1 FROM application_allocations a\n"
           f"WHERE a.id = NEW.reverses_allocation_id AND a.kind = 'allocation' AND {same})\n"
           "BEGIN SELECT RAISE(ABORT, 'allocation inverse must preserve original attribution'); END")
    receipt = _OWNED_SOURCE_ARM.format(components='payment_components', component='source_component_id',
        keys='payment_component_keys', key='component_key_id', edge='source_component_key_id',
        attribution='ps.payment_component_id = c.id', ar='ar_account_id')
    # A credit's attribution points outward: the component names the receivable source row,
    # where a receipt's source row names the component. Same assertion, opposite direction.
    credit = _OWNED_SOURCE_ARM.format(components='credit_components', component='credit_source_component_id',
        keys='credit_source_keys', key='key_id', edge='credit_source_key_id',
        attribution='ps.id = c.posting_source_id AND ps.transaction_id = c.transaction_id', ar='ar_account_id')
    yield ('CREATE TRIGGER application_allocations_owned_sources BEFORE INSERT ON application_allocations\n'
           f'WHEN NOT EXISTS (\n{receipt})\nAND NOT EXISTS (\n{credit})\n'
           "BEGIN SELECT RAISE(ABORT, 'allocation references unrelated application or accounting source'); END")
    branches = '\n'.join(
        '({} AND NEW.kind <> {!r}){}'.format(
            "type IN ({})".format(', '.join(repr(name) for name in types)) if isinstance(types, tuple)
            else 'type = {!r}'.format(types), kind, ' OR' if index < len(DOCUMENT_LINE_KINDS) - 1 else '')
        for index, (types, kind) in enumerate(DOCUMENT_LINE_KINDS))
    yield ('CREATE TRIGGER document_lines_type_insert BEFORE INSERT ON document_lines\n'
           'WHEN EXISTS (SELECT 1 FROM transactions WHERE id = NEW.transaction_id AND\n'
           f'({branches}))\n'
           "BEGIN SELECT RAISE(ABORT, 'document line kind does not match transaction type'); END")
