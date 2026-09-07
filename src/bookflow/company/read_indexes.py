"""Owned covering indexes for bounded payment reads."""
import sqlalchemy as sa

PAYER_LABEL_SQL = "coalesce(json_extract(profile_snapshot,'$.payer.label'),'')"


def define_indexes(metadata):
    table = metadata.tables['transactions']
    sa.Index('ix_co17_transactions_current', table.c.current_revision_id, table.c.type, table.c.status, table.c.id, table.c.version, table.c.number)
    table = metadata.tables['transactions']
    sa.Index('ix_co17_transactions_type', table.c.type, table.c.status, table.c.current_revision_id, table.c.id, table.c.version, table.c.number)
    table = metadata.tables['transaction_revisions']
    sa.Index('ix_co17_revisions_read', table.c.id, table.c.date, table.c.currency, table.c.total_minor_units, table.c.memo)
    table = metadata.tables['sales_profiles']
    sa.Index('ix_co17_sales_party', table.c.customer_id, table.c.control_account_id, table.c.revision_id)
    table = metadata.tables['sales_profiles']
    sa.Index('ix_co17_sales_revision', table.c.revision_id, table.c.customer_id, table.c.control_account_id, table.c.due_date)
    table = metadata.tables['applications']
    sa.Index('ix_co17_applications_invoice', table.c.paid_transaction_id, table.c.kind, table.c.amount_minor_units, table.c.id)
    table = metadata.tables['applications']
    sa.Index('ix_co17_applications_payment', table.c.paying_transaction_id, table.c.kind, table.c.amount_minor_units, table.c.id)
    table = metadata.tables['posting_lines']
    sa.Index('ix_co17_posting_party_ar', table.c.name_type, table.c.name_id, table.c.account_id, table.c.debit_minor_units, table.c.credit_minor_units)
    table = metadata.tables['payment_operations']
    sa.Index('ix_co17_operations_history', table.c.request_snapshot, table.c.id, table.c.audit_event_id, table.c.operation_key, table.c.command)
    table = metadata.tables['payment_profiles']
    sa.Index('ix_co17_payment_profile', table.c.revision_id, table.c.payer_id, table.c.payment_method_id, table.c.reference, sa.func.coalesce(sa.func.json_extract(table.c.profile_snapshot, sa.literal("$.payer.label")), sa.literal("")))
