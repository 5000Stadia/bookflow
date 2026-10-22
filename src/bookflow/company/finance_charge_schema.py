"""Finance charges: one row per late charge assessed on a customer's or job's overdue balance (co0074).

`finance-charge assess` writes each charge as an ordinary statement charge on the Finance Charge item,
so the customer's balance, aging and statement carry it like any other charge. This table is what says
that a statement charge *is* a finance charge: the date it was assessed as of, the overdue balance and
policy it was computed from, and what it was computed to before any change a person made. It is read
for the customer's last finance charge date (a later charge starts counting where this one stopped),
for keeping a second assessment on the same date from charging twice, and for leaving earlier finance
charges out of the overdue balance unless the company charges on them.

A row is written once and never changed. Voiding its statement charge (the normal correction) ends the
charge without touching this row: every reader joins the transaction and counts posted ones only.
"""
import sqlalchemy as sa


def define_tables(metadata, C, T):
    charges = T('finance_charges',
        C('id', sa.String(26), 'Stable ULID of this finance charge record.', primary_key=True),
        C('transaction_id', sa.String(26), 'Statement charge that put the finance charge on the account; the charge stands while it is posted.',
          sa.ForeignKey('transactions.id'), nullable=False),
        C('customer_id', sa.String(26), 'Customer or job charged.', sa.ForeignKey('customers.id'), nullable=False),
        C('assessment_date', sa.String(10), 'Date the charge was assessed as of and dated, YYYY-MM-DD.', nullable=False),
        C('overdue_balance_minor_units', sa.BigInteger, 'Overdue balance the charge was computed on, in home-currency minor units.', nullable=False),
        C('computed_minor_units', sa.BigInteger, 'Charge as computed by the policy, minimum applied, before any change a person made.', nullable=False),
        C('amount_minor_units', sa.BigInteger, 'Charge actually assessed, in home-currency minor units.', nullable=False),
        C('currency', sa.String(3), 'Home currency of the amounts.', nullable=False),
        C('annual_rate', sa.String(16), 'Annual interest rate in percent the charge was computed at.', nullable=False),
        C('grace_days', sa.Integer, 'Grace period in days the charge was computed with.', nullable=False),
        C('calculated_from', sa.String(12), 'Whether days were counted from the due_date or the invoice_date.', nullable=False),
        C('on_overdue_charges', sa.Boolean, 'Whether earlier overdue finance charges were part of the balance.', nullable=False),
        C('to_be_printed', sa.Boolean, 'Whether the charge was marked to be printed.', nullable=False),
        C('basis', sa.Text, 'Canonical JSON of the documents the charge was computed on: id, number, dates, open balance, days.', nullable=False),
        C('created_at', sa.String(32), 'UTC timestamp the charge was assessed.', nullable=False),
        C('created_by', sa.String(26), 'Principal who assessed it.', nullable=False),
        C('created_via', sa.String(16), 'Interface it was assessed through.', nullable=False),
        C('audit_event_id', sa.String(26), 'Audit event of the assessment that wrote it.', sa.ForeignKey('audit_events.id'), nullable=False),
        sa.UniqueConstraint('transaction_id', name='uq_finance_charge_transaction'),
        sa.CheckConstraint("typeof(amount_minor_units) = 'integer' AND amount_minor_units > 0", name='ck_finance_charge_amount'),
        sa.CheckConstraint("typeof(computed_minor_units) = 'integer' AND computed_minor_units >= 0", name='ck_finance_charge_computed'),
        sa.CheckConstraint("typeof(overdue_balance_minor_units) = 'integer'", name='ck_finance_charge_overdue'),
        sa.CheckConstraint("grace_days >= 0", name='ck_finance_charge_grace'),
        sa.CheckConstraint("calculated_from IN ('due_date','invoice_date')", name='ck_finance_charge_from'),
        sa.Index('ix_finance_charges_customer', 'customer_id', 'assessment_date'),
        description='Finance charges assessed on overdue customer balances, each written as a statement charge.')
    return {'finance_charges': charges}
