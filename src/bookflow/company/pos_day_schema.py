"""Counted register sessions: insert, then exactly one immutable close."""
import sqlalchemy as sa

def define_tables(metadata, C, T):
    shifts = T('pos_shifts',
        C('id', sa.String(26), 'Shift identity.', primary_key=True),
        C('register_id', sa.String(26), 'Register being counted.', sa.ForeignKey('pos_registers.id'), nullable=False),
        C('date', sa.String(10), 'Company-local opening day.', nullable=False),
        C('opened_at', sa.String(32), 'UTC opening time.', nullable=False),
        C('opened_by', sa.String(26), 'Opening clerk.', nullable=False),
        C('currency', sa.String(3), 'Home currency.', nullable=False),
        C('float_minor_units', sa.BigInteger, 'Opening float.', nullable=False),
        C('open_key', sa.String(64), 'Permanent opening operation key.', nullable=False, unique=True),
        C('closed_at', sa.String(32), 'UTC closing time; null while open.'),
        C('closed_by', sa.String(26), 'Closing clerk.'),
        C('counted_minor_units', sa.BigInteger, 'Cash counted at close.'),
        C('expected_minor_units', sa.BigInteger, 'Expected drawer captured at close.'),
        C('over_short_minor_units', sa.BigInteger, 'Counted minus expected at close.'),
        C('close_key', sa.String(64), 'Permanent closing operation key.', unique=True),
        C('report_json', sa.Text, 'Frozen Z report captured at close.'),
        sa.CheckConstraint('float_minor_units >= 0', name='ck_pos_shift_float'),
        sa.CheckConstraint('counted_minor_units IS NULL OR counted_minor_units >= 0', name='ck_pos_shift_count'),
        sa.CheckConstraint('(closed_at IS NULL AND closed_by IS NULL AND counted_minor_units IS NULL AND expected_minor_units IS NULL AND over_short_minor_units IS NULL AND close_key IS NULL AND report_json IS NULL) OR (closed_at IS NOT NULL AND closed_by IS NOT NULL AND counted_minor_units IS NOT NULL AND expected_minor_units IS NOT NULL AND over_short_minor_units IS NOT NULL AND over_short_minor_units = counted_minor_units - expected_minor_units AND close_key IS NOT NULL AND report_json IS NOT NULL)', name='ck_pos_shift_close'),
        sa.Index('ix_pos_shifts_register_open', 'register_id', unique=True, sqlite_where=sa.text('closed_at IS NULL')),
        description='Register opening floats and immutable counted closes; counting posts nothing.')
    return {'pos_shifts': shifts}
