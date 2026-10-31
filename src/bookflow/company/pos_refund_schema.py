"""Immutable POS return identities and permanent operation recovery (co0126)."""
import sqlalchemy as sa


def define_tables(metadata, C, T):
    return {'pos_refunds': T('pos_refunds',
        C('id', sa.String(26), 'Stable POS refund identity.', primary_key=True),
        C('operation_key', sa.String(64), 'Permanent device operation key.', nullable=False, unique=True),
        C('input_sha256', sa.String(64), 'Canonical request hash for safe retry.', nullable=False),
        C('register_id', sa.String(26), 'Register fixing site and funding account.', sa.ForeignKey('pos_registers.id'), nullable=False),
        C('sale_id', sa.String(26), 'Original POS sale; null for no receipt.', sa.ForeignKey('pos_sales.id')),
        C('credit_memo_id', sa.String(26), 'Credit memo created atomically.', sa.ForeignKey('transactions.id'), nullable=False),
        C('refund_id', sa.String(26), 'Customer refund; null for open store credit.', sa.ForeignKey('transactions.id')),
        C('customer_named', sa.Boolean, 'False for an anonymous walk-in return.', nullable=False),
        C('created_at', sa.String(32), 'UTC completion timestamp.', nullable=False),
        C('created_by', sa.String(26), 'Clerk who completed the return.', nullable=False),
        C('created_via', sa.String(16), 'Command interface.', nullable=False),
        C('result_json', sa.Text, 'Captured till facts, without costs or account details, for reprint and recovery.', nullable=False),
        sa.Index('ix_pos_refunds_sale_id', 'sale_id'),
        description='Completed POS refunds, linked to their credit memo and optional payout; insert-only.')}
