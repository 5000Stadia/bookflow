"""The POS layer (co0118, R114 increment 4; design/point-of-sale.md §2).

- ``company_info.pos_enabled``: the switch, off by default. While it is off every `pos*` command refuses with
  E_FEATURE_DISABLED and no POS surface shows.
- ``pos_registers``: a till's fixed facts. Turning POS on makes register Main. A register says where its stock leaves
  from, where its takings are deposited (default Undeposited Funds), the payment methods it offers in order, the
  walk-in customer a sale with no customer is recorded against, the bank account cash refunds leave from, the
  receipt's width and message, whether shifts are counted, and the camera decoder order. A register is not an
  account: cash sits in Undeposited Funds like every other receipt.
"""
import sqlalchemy as sa

SETTING = 'pos_enabled'
WIDTHS = (80, 58)


def company_info_column(C):
    return C(SETTING, sa.Boolean, 'Point of sale: the POS screen and its registers as an optional layer on sales '
             'receipts; off by default.', nullable=False, server_default='0')


def define_tables(metadata, C, T, common):
    registers = T('pos_registers',
        *common(),
        C('name', sa.String(200), 'Register display name (Main is made when POS is turned on).', nullable=False),
        C('name_key', sa.String(400), 'NFC-normalized and case-folded name key.', nullable=False),
        C('site_id', sa.String(26), 'Inventory site its stock leaves from.', sa.ForeignKey('inventory_sites.id'),
          nullable=False),
        C('deposit_account_id', sa.String(26), "Account its takings are deposited to: Undeposited Funds (the default) "
          'or a bank account.', sa.ForeignKey('accounts.id'), nullable=False),
        C('payment_method_ids', sa.Text, 'JSON list of the payment-method ids it offers, in the order shown.',
          nullable=False),
        C('walk_in_customer_id', sa.String(26), 'Customer a sale with no customer is recorded against; never shown '
          'on the POS screen or the receipt.', sa.ForeignKey('customers.id'), nullable=False),
        C('refund_account_id', sa.String(26), 'Bank account cash refunds leave from; null until the company has '
          'one.', sa.ForeignKey('accounts.id'), nullable=True),
        C('receipt_width', sa.Integer, 'Receipt paper width in millimetres: 80 or 58.', nullable=False),
        C('receipt_message', sa.String(500), 'Line printed at the foot of every receipt; null for none.',
          nullable=True),
        C('count_shifts', sa.Boolean, 'Whether the drawer is counted in shifts (float, count, over/short).',
          nullable=False),
        C('decoder_order', sa.String(200), "Camera decoder order for this register's devices (comma-separated "
          "names); null uses the host's scan_decoders.", nullable=True),
        sa.CheckConstraint('receipt_width IN (80, 58)', name='ck_pos_registers_receipt_width'),
        sa.UniqueConstraint('name_key', name='uq_pos_registers_name_key'),
        description='Point-of-sale registers: the fixed facts a POS sale takes from its till. Versioned; never '
                    'deleted.')
    return {'pos_registers': registers}
