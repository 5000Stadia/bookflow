"""Immutable captured purchase facts attached to check/card journal lines."""
import sqlalchemy as sa


def define_tables(metadata, column, table):
    C = column
    rows = table('money_out_item_lines',
        C('document_line_id', sa.String(26), 'Journal line carrying this purchased item.', primary_key=True),
        C('transaction_id', sa.String(26), 'Owning check or card transaction.', nullable=False),
        C('revision_id', sa.String(26), 'Immutable captured revision.', nullable=False),
        C('item_id', sa.String(26), 'Purchased item identity.', sa.ForeignKey('items.id'), nullable=False),
        C('line_snapshot', sa.Text, 'Captured purchase facts, quantity, cost and dimensions.', nullable=False),
        sa.ForeignKeyConstraint(['transaction_id'], ['money_out_documents.transaction_id']),
        sa.ForeignKeyConstraint(['transaction_id', 'revision_id', 'document_line_id'],
                                ['document_lines.transaction_id', 'document_lines.revision_id', 'document_lines.id']),
        sa.CheckConstraint("json_valid(line_snapshot) AND json_type(line_snapshot) = 'object'", name='ck_money_out_item_snapshot'),
        sa.Index('ix_money_out_item_revision', 'revision_id', 'document_line_id'),
        description='Captured check/card purchase item facts; accounting remains on its journal line.')
    return {'money_out_item_lines': rows}


def guards():
    return tuple(f"CREATE TRIGGER money_out_item_lines_no_{action.lower()} BEFORE {action} ON money_out_item_lines BEGIN SELECT RAISE(ABORT, 'captured purchase items are immutable'); END" for action in ('UPDATE', 'DELETE'))
