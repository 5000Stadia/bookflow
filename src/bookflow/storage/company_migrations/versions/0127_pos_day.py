"""co0127: counted POS shifts and captured sale/refund shift ownership."""
from alembic import op
revision = 'co0127'
down_revision = 'co0126'
branch_labels = depends_on = None
TABLES = ('pos_shifts',)
TRIGGERS = ('pos_shifts_no_delete', 'pos_shifts_close_once')

def upgrade():
    op.execute('\nCREATE TABLE pos_shifts (\n\tid VARCHAR(26) NOT NULL, \n\tregister_id VARCHAR(26) NOT NULL, \n\tdate VARCHAR(10) NOT NULL, \n\topened_at VARCHAR(32) NOT NULL, \n\topened_by VARCHAR(26) NOT NULL, \n\tcurrency VARCHAR(3) NOT NULL, \n\tfloat_minor_units BIGINT NOT NULL, \n\topen_key VARCHAR(64) NOT NULL, \n\tclosed_at VARCHAR(32), \n\tclosed_by VARCHAR(26), \n\tcounted_minor_units BIGINT, \n\texpected_minor_units BIGINT, \n\tover_short_minor_units BIGINT, \n\tclose_key VARCHAR(64), \n\treport_json TEXT, \n\tPRIMARY KEY (id), \n\tCONSTRAINT ck_pos_shift_float CHECK (float_minor_units >= 0), \n\tCONSTRAINT ck_pos_shift_count CHECK (counted_minor_units IS NULL OR counted_minor_units >= 0), \n\tCONSTRAINT ck_pos_shift_close CHECK ((closed_at IS NULL AND closed_by IS NULL AND counted_minor_units IS NULL AND expected_minor_units IS NULL AND over_short_minor_units IS NULL AND close_key IS NULL AND report_json IS NULL) OR (closed_at IS NOT NULL AND closed_by IS NOT NULL AND counted_minor_units IS NOT NULL AND expected_minor_units IS NOT NULL AND over_short_minor_units = counted_minor_units - expected_minor_units AND close_key IS NOT NULL AND report_json IS NOT NULL)), \n\tFOREIGN KEY(register_id) REFERENCES pos_registers (id), \n\tUNIQUE (open_key), \n\tUNIQUE (close_key)\n)\n\n')
    op.execute('CREATE UNIQUE INDEX ix_pos_shifts_register_open ON pos_shifts (register_id) WHERE closed_at IS NULL')
    op.execute('ALTER TABLE pos_sales ADD COLUMN shift_id VARCHAR(26) REFERENCES pos_shifts(id)')
    op.execute('ALTER TABLE pos_refunds ADD COLUMN shift_id VARCHAR(26) REFERENCES pos_shifts(id)')
    op.execute("CREATE TRIGGER pos_shifts_no_delete BEFORE DELETE ON pos_shifts BEGIN SELECT RAISE(ABORT, 'a POS shift is never deleted'); END")
    op.execute("""CREATE TRIGGER pos_shifts_close_once BEFORE UPDATE ON pos_shifts
      WHEN OLD.closed_at IS NOT NULL OR NEW.closed_at IS NULL
       OR NEW.id IS NOT OLD.id OR NEW.register_id IS NOT OLD.register_id
       OR NEW.date IS NOT OLD.date OR NEW.opened_at IS NOT OLD.opened_at
       OR NEW.opened_by IS NOT OLD.opened_by OR NEW.currency IS NOT OLD.currency
       OR NEW.float_minor_units IS NOT OLD.float_minor_units OR NEW.open_key IS NOT OLD.open_key
      BEGIN SELECT RAISE(ABORT, 'a POS shift can only be closed once'); END""")

def downgrade():
    raise RuntimeError('Company migrations are forward-only')
