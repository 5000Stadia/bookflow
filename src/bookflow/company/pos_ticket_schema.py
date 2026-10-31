"""Versioned POS working carts. Holding never posts or reserves inventory."""
import sqlalchemy as sa


def define_tables(metadata, C, T, common):
    columns = common()
    columns[0] = C('id', sa.String(64), 'Device ticket key; also the completed sale operation key.', primary_key=True)
    tickets = T('pos_tickets', *columns,
        C('device', sa.String(128), 'Device identity at the latest hold, resume or take-over.', nullable=False, server_default='unknown'),
        C('register_id', sa.String(26), 'Register whose fixed facts apply to this cart.',
          sa.ForeignKey('pos_registers.id'), nullable=False),
        C('customer_id', sa.String(26), 'Chosen customer; null means the register walk-in.',
          sa.ForeignKey('customers.id'), nullable=True),
        C('label', sa.String(400), 'Clerk label, defaulting to hold time and first item.', nullable=False),
        C('status', sa.String(16), 'held, open after resume, completed, or discarded.', nullable=False),
        C('lines', sa.Text, 'Ordered cart JSON with stable item IDs and explicit prices only; no stock reservation.', nullable=False),
        C('currency', sa.String(3), 'Home currency of the last held total.', nullable=False),
        C('total_minor_units', sa.BigInteger, 'Last held total; informational, repriced when resumed.', nullable=False),
        sa.CheckConstraint("status IN ('held','open','completed','discarded')", name='ck_pos_tickets_status'),
        sa.Index('ix_pos_tickets_status_updated', 'status', 'updated_at'),
        description='Shared held POS carts and their versioned lifecycle. Holds, resumes and discards post nothing.')
    return {'pos_tickets': tickets}
