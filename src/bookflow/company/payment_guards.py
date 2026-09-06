"""Database insertion fences for immutable receipt and settlement ownership."""

IMMUTABLE = (
    'payment_profiles', 'payment_component_keys', 'payment_components',
    'applications', 'settlement_line_keys', 'application_allocations',
    'payment_operations', 'payment_operation_items',
    'payment_selection_revisions', 'payment_selection_items',
)


def statements():
    for table in IMMUTABLE:
        for event in ('UPDATE', 'DELETE'):
            yield f"CREATE TRIGGER {table}_no_{event.lower()} BEFORE {event} ON {table} BEGIN SELECT RAISE(ABORT, 'immutable payment history'); END"
    yield """CREATE TRIGGER payment_selection_terminal BEFORE UPDATE ON payment_selections
WHEN OLD.state = 'consumed' OR NEW.version < OLD.version OR NEW.created_at <> OLD.created_at
OR NEW.created_by <> OLD.created_by OR NEW.created_via <> OLD.created_via
BEGIN SELECT RAISE(ABORT, 'consumed selection and creation provenance are immutable'); END"""
    yield """CREATE TRIGGER payment_selections_no_delete BEFORE DELETE ON payment_selections
BEGIN SELECT RAISE(ABORT, 'immutable payment selection identity'); END"""
    yield """CREATE TRIGGER document_lines_type_insert BEFORE INSERT ON document_lines
WHEN EXISTS (SELECT 1 FROM transactions WHERE id = NEW.transaction_id AND
((type = 'journal_entry' AND NEW.kind <> 'journal') OR
(type IN ('invoice', 'sales_receipt') AND NEW.kind <> 'sale') OR
(type = 'payment' AND NEW.kind <> 'payment')))
BEGIN SELECT RAISE(ABORT, 'document line kind does not match transaction type'); END"""
    for table, field, kind in (
        ('payment_component_keys', 'transaction_id', 'payment'),
        ('settlement_line_keys', 'transaction_id', 'invoice'),
        ('applications', 'paying_transaction_id', 'payment'),
        ('applications', 'paid_transaction_id', 'invoice'),
        ('payment_selection_items', 'invoice_id', 'invoice'),
    ):
        yield f"""CREATE TRIGGER {table}_{field}_type BEFORE INSERT ON {table}
WHEN NEW.{field} IS NOT NULL AND NOT EXISTS (SELECT 1 FROM transactions WHERE id = NEW.{field} AND type = '{kind}')
BEGIN SELECT RAISE(ABORT, 'payment reference has wrong document type'); END"""
    yield """CREATE TRIGGER payment_components_owner BEFORE INSERT ON payment_components
WHEN NOT EXISTS (SELECT 1 FROM payment_component_keys k JOIN document_lines d
ON d.transaction_id = k.transaction_id AND d.line_id = k.line_id
WHERE k.id = NEW.component_key_id AND d.id = NEW.document_line_id
AND d.kind = 'payment' AND k.currency = NEW.currency)
BEGIN SELECT RAISE(ABORT, 'payment component has unrelated line or currency'); END"""
    yield """CREATE TRIGGER posting_sources_payment_owner BEFORE INSERT ON posting_line_sources
WHEN EXISTS (SELECT 1 FROM transactions t WHERE t.id = NEW.transaction_id AND
((t.type = 'payment' AND (NEW.payment_component_id IS NULL OR NEW.tax_component_id IS NOT NULL)) OR
(t.type <> 'payment' AND NEW.payment_component_id IS NOT NULL)))
OR (NEW.payment_component_id IS NOT NULL AND NOT EXISTS (
SELECT 1 FROM payment_components c WHERE c.id = NEW.payment_component_id AND c.currency = NEW.currency))
BEGIN SELECT RAISE(ABORT, 'posting attribution has wrong payment component'); END"""
    yield """CREATE TRIGGER applications_exact_party BEFORE INSERT ON applications
WHEN NOT EXISTS (SELECT 1 FROM payment_component_keys k JOIN transactions t ON t.id = NEW.paid_transaction_id
JOIN sales_profiles p ON p.revision_id = t.current_revision_id
JOIN transaction_revisions r ON r.id = p.revision_id
WHERE k.id = NEW.source_component_key_id AND k.party_id = p.customer_id
AND k.ar_account_id = p.control_account_id AND k.currency = NEW.currency AND r.currency = NEW.currency)
BEGIN SELECT RAISE(ABORT, 'application source and target ownership differ'); END"""
    inverse_fields = ('paying_transaction_id', 'paid_transaction_id', 'source_component_key_id',
                      'amount_minor_units', 'currency', 'effective_date')
    same = ' AND '.join(f'a.{name} IS NEW.{name}' for name in inverse_fields)
    yield f"""CREATE TRIGGER applications_exact_inverse BEFORE INSERT ON applications
WHEN NEW.kind = 'unapply' AND NOT EXISTS (SELECT 1 FROM applications a
WHERE a.id = NEW.reverses_application_id AND a.kind = 'apply' AND {same})
BEGIN SELECT RAISE(ABORT, 'unapply must exactly reverse original application'); END"""
    allocation_fields = ('application_id', 'source_transaction_id', 'source_revision_id', 'source_component_id',
        'source_posting_source_id', 'target_transaction_id', 'target_revision_id', 'target_document_line_id',
        'target_line_id', 'target_ordinal', 'logical_kind', 'tax_item_id', 'tax_component_id',
        'target_ar_source_id', 'target_recognition_source_id', 'recognition_role', 'amount_minor_units',
        'currency', 'effective_date', 'facts_snapshot')
    same = ' AND '.join(f'a.{name} IS NEW.{name}' for name in allocation_fields)
    yield f"""CREATE TRIGGER application_allocations_exact_inverse BEFORE INSERT ON application_allocations
WHEN NEW.kind = 'reversal' AND NOT EXISTS (SELECT 1 FROM application_allocations a
WHERE a.id = NEW.reverses_allocation_id AND a.kind = 'allocation' AND {same})
BEGIN SELECT RAISE(ABORT, 'allocation inverse must preserve original attribution'); END"""
    yield """CREATE TRIGGER application_allocations_owned_sources BEFORE INSERT ON application_allocations
WHEN NOT EXISTS (
SELECT 1 FROM applications a
JOIN payment_components c ON c.id = NEW.source_component_id
JOIN posting_line_sources ps ON ps.id = NEW.source_posting_source_id
JOIN posting_lines pl ON pl.id = ps.posting_line_id
JOIN payment_component_keys k ON k.id = c.component_key_id
JOIN posting_line_sources ar ON ar.id = NEW.target_ar_source_id
JOIN posting_lines arl ON arl.id = ar.posting_line_id
JOIN posting_line_sources rec ON rec.id = NEW.target_recognition_source_id
JOIN posting_lines recl ON recl.id = rec.posting_line_id
JOIN document_lines d ON d.id = NEW.target_document_line_id
WHERE a.id = NEW.application_id AND a.kind = 'apply'
AND a.paying_transaction_id = NEW.source_transaction_id AND a.paid_transaction_id = NEW.target_transaction_id
AND a.source_component_key_id = c.component_key_id AND a.currency = NEW.currency AND a.effective_date = NEW.effective_date
AND ps.payment_component_id = c.id AND ps.revision_id = NEW.source_revision_id
AND ps.reversed_source_id IS NULL AND pl.account_id = k.ar_account_id AND pl.credit_minor_units > 0
AND pl.name_type = 'customer' AND pl.name_id = k.party_id
AND ar.transaction_id = NEW.target_transaction_id AND rec.transaction_id = NEW.target_transaction_id
AND ar.revision_id = NEW.target_revision_id AND rec.revision_id = NEW.target_revision_id
AND ar.document_line_id = d.id AND rec.document_line_id = d.id AND d.line_id = NEW.target_line_id
AND ar.tax_component_id IS NEW.tax_component_id AND rec.tax_component_id IS NEW.tax_component_id
AND ar.reversed_source_id IS NULL AND rec.reversed_source_id IS NULL
AND arl.account_id = k.ar_account_id AND arl.debit_minor_units > 0 AND recl.credit_minor_units > 0
AND arl.name_type = 'customer' AND arl.name_id = k.party_id
AND (NEW.logical_kind = 'net' OR EXISTS (SELECT 1 FROM sales_tax_components tc
WHERE tc.id = NEW.tax_component_id AND tc.tax_item_id = NEW.tax_item_id AND tc.liability_account_id = recl.account_id)))
BEGIN SELECT RAISE(ABORT, 'allocation references unrelated application or accounting source'); END"""
