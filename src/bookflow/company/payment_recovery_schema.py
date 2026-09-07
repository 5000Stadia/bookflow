"""Additive durable preparation evidence. No financial tables are altered."""
import sqlalchemy as sa


def define_tables(metadata, C, T, common):
    def text(name, nullable=False, size=None):
        return C(name, sa.String(size) if size else sa.Text, name.replace('_', ' ') + '.', nullable=nullable)
    def ident(name, nullable=False, pk=False):
        return C(name, sa.String(26), name.replace('_', ' ') + '.', nullable=nullable, primary_key=pk)
    def integer(name, nullable=False):
        return C(name, sa.BigInteger, name.replace('_', ' ') + '.', nullable=nullable)
    def ck(expr, name):
        return sa.CheckConstraint('COALESCE((' + expr + '), 0)', name='ck_recovery_' + name)
    def num(name, minimum=0, nullable=False):
        expr = f"typeof({name}) = 'integer' AND {name} >= {minimum}"
        return ck(f'{name} IS NULL OR ({expr})' if nullable else expr, name)
    def obj(name, nullable=False):
        expr = f"CASE WHEN json_valid({name}) THEN json_type({name}) = 'object' ELSE 0 END"
        return ck(f'{name} IS NULL OR ({expr})' if nullable else expr, name)
    def fk(cols, targets):
        return sa.ForeignKeyConstraint(cols, targets)
    def created():
        return [text('created_at', size=32), ident('created_by'), text('created_via', size=16),
                C('audit_event_id', sa.String(26), 'Creation audit event.', sa.ForeignKey('audit_events.id'), nullable=False)]
    def revision_owner(field):
        return fk(['selection_id', field], ['payment_selection_revisions.selection_id', 'payment_selection_revisions.id'])
    def recovery_owner():
        return fk(['selection_id', 'recovery_id'], ['payment_selection_recoveries.selection_id', 'payment_selection_recoveries.id'])
    receipts = []
    checks = []
    for action in ('begin', 'seal', 'terminal'):
        for field in ('request_snapshot', 'receipt_snapshot'):
            name = action + '_' + field
            receipts.append(text(name, action != 'begin'))
            checks.append(obj(name, action != 'begin'))
        receipts.append(text(action + '_request_hash', action != 'begin', 64))
    recoveries = T('payment_selection_recoveries', *common(),
        ident('selection_id'), text('recovery_key', size=128), integer('request_schema_version'),
        text('attempt_generation', size=36), ident('local_baseline_revision_id'), ident('anchor_revision_id'),
        integer('anchor_selection_version'), text('header_intent'), integer('declared_entry_count'),
        text('intent_hash', size=64), text('state', size=16), ident('applied_revision_id', True),
        *receipts, C('audit_event_id', sa.String(26), 'Creation audit event.', sa.ForeignKey('audit_events.id'), nullable=False),
        *checks, obj('header_intent'), num('declared_entry_count'), num('anchor_selection_version', 1), num('version', 1),
        ck("typeof(request_schema_version) = 'integer' AND request_schema_version = 1", 'format'),
        ck("state IN ('uploading','sealed','applied','aborted','superseded') AND ((state = 'applied') = (applied_revision_id IS NOT NULL))", 'state'),
        ck("length(intent_hash)=64 AND intent_hash NOT GLOB '*[^0-9a-f]*'", 'intent_hash'),
        ck("length(attempt_generation)=36 AND substr(attempt_generation,9,1)='-' AND substr(attempt_generation,14,1)='-' AND substr(attempt_generation,19,1)='-' AND substr(attempt_generation,24,1)='-' AND replace(attempt_generation,'-','') NOT GLOB '*[^0-9a-f]*'", 'generation'),
        ck("(state IN ('applied','aborted','superseded')) = (terminal_receipt_snapshot IS NOT NULL) AND (terminal_receipt_snapshot IS NULL) = (terminal_request_snapshot IS NULL) AND (terminal_receipt_snapshot IS NULL) = (terminal_request_hash IS NULL)", 'terminal_receipt'),
        ck("(seal_receipt_snapshot IS NULL) = (seal_request_snapshot IS NULL) AND (seal_receipt_snapshot IS NULL) = (seal_request_hash IS NULL) AND (state <> 'uploading' OR seal_receipt_snapshot IS NULL) AND (state NOT IN ('sealed','applied') OR seal_receipt_snapshot IS NOT NULL)", 'seal_receipt'),
        sa.UniqueConstraint('recovery_key'), sa.UniqueConstraint('selection_id', 'id'),
        fk(['selection_id'], ['payment_selections.id']), revision_owner('local_baseline_revision_id'),
        revision_owner('anchor_revision_id'), revision_owner('applied_revision_id'),
        sa.Index('ix_recovery_selection', 'selection_id', 'created_at', 'id'),
        description='Complete attempted edits and immutable action receipts for one shared payment selection.')
    chunks = T('payment_selection_recovery_chunks', ident('id', pk=True), ident('selection_id'), ident('recovery_id'),
        integer('chunk_index'), text('request_hash', size=64), text('request_snapshot'), text('receipt_snapshot'),
        *created(), num('chunk_index'), obj('request_snapshot'), obj('receipt_snapshot'),
        sa.UniqueConstraint('recovery_id', 'chunk_index'), recovery_owner(),
        description='Immutable acknowledgements of deterministic ranges of up to 200 attempted edits.')
    items = T('payment_selection_recovery_items', ident('id', pk=True), ident('selection_id'), ident('recovery_id'),
        integer('entry_index'), ident('invoice_id'), text('invoice_type', size=32), text('action', size=16),
        integer('observed_invoice_version'), integer('amount_minor_units', True), text('currency', True, 3),
        text('amount_origin', True, 24), ident('retained_calculation_revision_id', True),
        integer('attempted_calculated_minor_units', True), *created(), num('entry_index', 1),
        num('observed_invoice_version', 1), num('amount_minor_units', nullable=True), num('attempted_calculated_minor_units', nullable=True),
        ck("invoice_type = 'invoice'", 'invoice_type'),
        ck("(action = 'remove' AND amount_minor_units IS NULL AND currency IS NULL AND amount_origin IS NULL AND retained_calculation_revision_id IS NULL AND attempted_calculated_minor_units IS NULL) OR (action = 'calculate' AND amount_minor_units IS NULL AND currency IS NULL AND amount_origin IS NULL AND retained_calculation_revision_id IS NULL) OR (action = 'set' AND currency IS NOT NULL AND attempted_calculated_minor_units IS NULL AND ((amount_origin = 'entered' AND amount_minor_units IS NOT NULL AND retained_calculation_revision_id IS NULL) OR (amount_origin = 'unresolved' AND amount_minor_units IS NULL AND retained_calculation_revision_id IS NULL) OR (amount_origin = 'calculated' AND amount_minor_units IS NOT NULL AND retained_calculation_revision_id IS NOT NULL)))", 'entry_shape'),
        sa.UniqueConstraint('recovery_id', 'entry_index'), sa.UniqueConstraint('recovery_id', 'invoice_id'),
        recovery_owner(), revision_owner('retained_calculation_revision_id'),
        fk(['invoice_id', 'invoice_type'], ['transactions.id', 'transactions.type']),
        sa.Index('ix_recovery_item_invoice', 'invoice_id', 'recovery_id'),
        sa.Index('ix_recovery_item_selection', 'selection_id', 'invoice_id'),
        description='Immutable nonfinancial attempted edits, including historical calculated provenance.')
    active = T('payment_selection_recovery_active', ident('selection_id', pk=True), ident('recovery_id'),
        *created(), sa.UniqueConstraint('recovery_id'), fk(['selection_id'], ['payment_selections.id']), recovery_owner(),
        description='One durable ordinary-mutation and fresh-consumption barrier per shared selection.')
    return {table.name: table for table in (recoveries, chunks, items, active)}


def guards():
    statements = []
    for table in ('payment_selection_recovery_chunks', 'payment_selection_recovery_items'):
        for action in ('UPDATE', 'DELETE'):
            statements.append(f"CREATE TRIGGER {table}_no_{action.lower()} BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT, 'immutable recovery evidence'); END")
        statements.append(f"CREATE TRIGGER {table}_uploading BEFORE INSERT ON {table} WHEN NOT EXISTS (SELECT 1 FROM payment_selection_recoveries r JOIN payment_selection_recovery_active a ON a.recovery_id=r.id WHERE r.id=NEW.recovery_id AND r.state='uploading') BEGIN SELECT RAISE(ABORT, 'recovery is not uploading'); END")
    statements.append("CREATE TRIGGER recovery_no_delete BEFORE DELETE ON payment_selection_recoveries BEGIN SELECT RAISE(ABORT, 'immutable recovery identity'); END")
    immutable = ('id','selection_id','recovery_key','request_schema_version','attempt_generation','local_baseline_revision_id','anchor_revision_id','anchor_selection_version','header_intent','declared_entry_count','intent_hash','created_at','created_by','created_via','audit_event_id','begin_request_snapshot','begin_request_hash','begin_receipt_snapshot')
    changes = ' OR '.join(f'NEW.{key} IS NOT OLD.{key}' for key in immutable)
    statements.append(f"CREATE TRIGGER recovery_transition BEFORE UPDATE ON payment_selection_recoveries WHEN {changes} OR OLD.state IN ('applied','aborted','superseded') OR NEW.version <> OLD.version+1 OR (OLD.state='sealed' AND NEW.state NOT IN ('applied','aborted','superseded')) OR (OLD.state='uploading' AND NEW.state NOT IN ('uploading','sealed','aborted','superseded')) OR (OLD.seal_receipt_snapshot IS NOT NULL AND (NEW.seal_receipt_snapshot IS NOT OLD.seal_receipt_snapshot OR NEW.seal_request_hash IS NOT OLD.seal_request_hash OR NEW.seal_request_snapshot IS NOT OLD.seal_request_snapshot)) BEGIN SELECT RAISE(ABORT, 'invalid recovery transition'); END")
    statements.append("CREATE TRIGGER recovery_active_no_update BEFORE UPDATE ON payment_selection_recovery_active BEGIN SELECT RAISE(ABORT, 'replace barrier atomically'); END")
    statements.append("CREATE TRIGGER recovery_initial BEFORE INSERT ON payment_selection_recoveries WHEN NEW.state <> 'uploading' OR NEW.version <> 1 OR NOT EXISTS (SELECT 1 FROM payment_selections s WHERE s.id=NEW.selection_id AND s.state='open' AND s.version=NEW.anchor_selection_version AND s.current_revision_id=NEW.anchor_revision_id) BEGIN SELECT RAISE(ABORT, 'invalid recovery anchor'); END")
    statements.append("CREATE TRIGGER recovery_active_insert BEFORE INSERT ON payment_selection_recovery_active WHEN NOT EXISTS (SELECT 1 FROM payment_selection_recoveries r JOIN payment_selections s ON s.id=r.selection_id WHERE r.id=NEW.recovery_id AND r.selection_id=NEW.selection_id AND r.state='uploading' AND s.state='open') BEGIN SELECT RAISE(ABORT, 'invalid recovery barrier'); END")
    statements.append("CREATE TRIGGER recovery_active_delete BEFORE DELETE ON payment_selection_recovery_active WHEN EXISTS (SELECT 1 FROM payment_selection_recoveries r WHERE r.id=OLD.recovery_id AND r.state IN ('uploading','sealed')) BEGIN SELECT RAISE(ABORT, 'active recovery must be resolved atomically'); END")
    statements.append("CREATE TRIGGER recovery_item_range BEFORE INSERT ON payment_selection_recovery_items WHEN NEW.entry_index>(SELECT declared_entry_count FROM payment_selection_recoveries WHERE id=NEW.recovery_id) OR EXISTS (SELECT 1 FROM payment_selection_recovery_items i WHERE i.recovery_id=NEW.recovery_id AND ((i.entry_index<NEW.entry_index AND i.invoice_id>=NEW.invoice_id COLLATE BINARY) OR (i.entry_index>NEW.entry_index AND i.invoice_id<=NEW.invoice_id COLLATE BINARY))) BEGIN SELECT RAISE(ABORT, 'invalid recovery item range or order'); END")
    return statements
