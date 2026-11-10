"""Posted paychecks (R116 increment 2): private facts beside a neutral ledger link.

Amounts, payees, notes and reasons live only in these company-local tables, read through payroll
commands under `payroll.read`. `payroll_ledger_links` is the one neutral row a ledger audit event
carries, so an event that posts or voids a paycheck needs `payroll.read` to be seen at all.
"""
import sqlalchemy as sa

_CENTS = ('gross', 'employee_tax', 'employee_deduction', 'employer_cost', 'net')


def define_tables(metadata, C, T):
    fk = lambda table, **kw: sa.ForeignKey(f'{table}.id', ondelete='RESTRICT', **kw)
    paychecks = T('payroll_paychecks',
        C('id', sa.String(26), 'Stable paycheck ULID.', primary_key=True),
        C('draft_id', sa.String(26), 'The prepared draft this paycheck was posted from; one paycheck per draft.',
          fk('payroll_drafts'), nullable=False, unique=True),
        C('draft_version', sa.Integer, 'Draft version whose calculation was posted.', nullable=False),
        C('employee_id', sa.String(26), 'Employee paid.', fk('employees'), nullable=False),
        C('transaction_id', sa.String(26), 'The payroll-owned journal entry.', fk('transactions'), nullable=False, unique=True),
        C('batch_id', sa.String(26), 'Reversible batch that holds this paycheck and its net payment.', fk('batches'), nullable=False),
        C('version', sa.Integer, 'Optimistic paycheck version; payment and void advance it.', nullable=False),
        C('status', sa.String(16), 'posted or voided.', nullable=False),
        C('currency', sa.String(3), 'USD only.', nullable=False),
        C('pay_date', sa.String(10), 'Actual pay date: the posting date and tax year.', nullable=False),
        C('period_start', sa.String(10), 'Service period start.', nullable=False),
        C('period_end', sa.String(10), 'Service period end.', nullable=False),
        C('tax_year', sa.Integer, 'Calendar year of the pay date.', nullable=False),
        *[C(f'{name}_minor_units', sa.BigInteger, f'PRIVATE {name.replace("_", " ")} in cents.', nullable=False)
          for name in _CENTS],
        *[C(name, sa.String(26), 'Payroll account captured at posting.', sa.ForeignKey('accounts.id', ondelete='RESTRICT'),
            nullable=False) for name in ('wage_account_id', 'employer_account_id', 'net_pay_account_id')],
        C('snapshot', sa.Text, 'PRIVATE JSON: the posted inputs and calculation trace, immutable.', nullable=False),
        C('created_at', sa.String(32), 'UTC posting time.', nullable=False),
        C('created_by', sa.String(26), 'Posting principal.', nullable=False),
        C('voided_at', sa.String(32), 'UTC void time, null while posted.', nullable=True),
        sa.CheckConstraint("version > 0 AND status IN ('posted', 'voided') AND currency = 'USD' AND "
                           + ' AND '.join(f'{n}_minor_units >= 0' for n in _CENTS), name='ck_payroll_paycheck'),
        sa.Index('ix_payroll_paycheck_employee_year', 'employee_id', 'tax_year', 'pay_date'),
        description='PRIVATE posted paychecks. Readable only with payroll.read; the ledger entry is a separate record.')
    components = T('payroll_paycheck_components',
        C('paycheck_id', sa.String(26), 'Paycheck.', fk('payroll_paychecks'), primary_key=True),
        C('position', sa.Integer, 'Component order.', primary_key=True),
        C('code', sa.String(60), 'PRIVATE owner-entered earning or item code.', nullable=False),
        C('kind', sa.String(32), 'cash_earning, employee_tax, employee_deduction, employer_tax, employer_contribution or net_pay.', nullable=False),
        C('method', sa.String(24), 'fixed, quantity_times_rate, percent or net.', nullable=False),
        C('amount_minor_units', sa.BigInteger, 'PRIVATE component cents.', nullable=False),
        C('debit_account_id', sa.String(26), 'Account debited, if any.', sa.ForeignKey('accounts.id', ondelete='RESTRICT'), nullable=True),
        C('credit_account_id', sa.String(26), 'Account credited, if any.', sa.ForeignKey('accounts.id', ondelete='RESTRICT'), nullable=True),
        C('payee', sa.String(200), 'PRIVATE liability payee, for items.', nullable=True),
        C('rate_millionths', sa.BigInteger, 'PRIVATE percentage rate in millionths of a percent.', nullable=True),
        C('base_minor_units', sa.BigInteger, 'PRIVATE percentage base in cents.', nullable=True),
        sa.CheckConstraint('amount_minor_units >= 0', name='ck_payroll_component_amount'),
        description='PRIVATE immutable components; their ledger legs sum to the paycheck journal.')
    payments = T('payroll_net_payments',
        C('id', sa.String(26), 'Stable net-payment ULID.', primary_key=True),
        C('paycheck_id', sa.String(26), 'Paycheck whose net pay this settles.', fk('payroll_paychecks'), nullable=False),
        C('transaction_id', sa.String(26), 'The payroll-owned check.', fk('transactions'), nullable=False, unique=True),
        C('account_id', sa.String(26), 'Bank account the check is drawn on.', sa.ForeignKey('accounts.id', ondelete='RESTRICT'), nullable=False),
        C('date', sa.String(10), 'Payment date.', nullable=False),
        C('amount_minor_units', sa.BigInteger, 'PRIVATE cents paid: the whole net.', nullable=False),
        C('status', sa.String(16), 'recorded or voided.', nullable=False),
        C('version', sa.Integer, 'Optimistic payment version.', nullable=False),
        C('created_at', sa.String(32), 'UTC recording time.', nullable=False),
        C('created_by', sa.String(26), 'Recording principal.', nullable=False),
        C('voided_at', sa.String(32), 'UTC void time.', nullable=True),
        sa.CheckConstraint("amount_minor_units > 0 AND status IN ('recorded', 'voided') AND version > 0",
                           name='ck_payroll_net_payment'),
        sa.Index('ix_payroll_net_payment_paycheck', 'paycheck_id', 'status'),
        description='PRIVATE net-pay settlements, each one check through the check machinery.')
    ytd = T('payroll_ytd_events',
        C('id', sa.String(26), 'Stable YTD event ULID.', primary_key=True),
        C('employee_id', sa.String(26), 'Employee.', fk('employees'), nullable=False),
        C('tax_year', sa.Integer, 'Calendar tax year.', nullable=False),
        C('paycheck_id', sa.String(26), 'Producing paycheck.', fk('payroll_paychecks'), nullable=False),
        C('kind', sa.String(16), 'paycheck or void.', nullable=False),
        C('reverses_id', sa.String(26), 'The event a void negates.', sa.ForeignKey('payroll_ytd_events.id', ondelete='RESTRICT'),
          nullable=True, unique=True),
        *[C(f'{name}_minor_units', sa.BigInteger, f'PRIVATE signed {name.replace("_", " ")} cents.', nullable=False)
          for name in _CENTS],
        C('items', sa.Text, 'PRIVATE JSON: signed cents per item code and kind.', nullable=False),
        C('created_at', sa.String(32), 'UTC event time.', nullable=False),
        sa.CheckConstraint("kind IN ('paycheck', 'void')", name='ck_payroll_ytd_kind'),
        sa.Index('ix_payroll_ytd_employee_year', 'employee_id', 'tax_year'),
        description='PRIVATE append-only year-to-date facts; a void appends the exact negation.')
    events = T('payroll_paycheck_events',
        C('id', sa.String(26), 'Event ULID.', primary_key=True),
        C('paycheck_id', sa.String(26), 'Paycheck.', fk('payroll_paychecks'), nullable=False),
        C('action', sa.String(32), 'post, pay_net, void_payment, void, or the same under batch reverse.', nullable=False),
        C('version', sa.Integer, 'Paycheck version after this event.', nullable=False),
        C('reason', sa.Text, 'PRIVATE reason supplied for this write.', nullable=False),
        C('directive_id', sa.String(26), 'Directive the write was made under, kept privately.', nullable=True),
        C('actor_id', sa.String(26), 'Acting principal.', nullable=False),
        C('created_at', sa.String(32), 'UTC time.', nullable=False),
        description='PRIVATE immutable paycheck history: reasons and directives never reach general audit.')
    links = T('payroll_ledger_links',
        C('id', sa.String(26), 'Link ULID.', primary_key=True),
        C('transaction_id', sa.String(26), 'Ledger transaction payroll owns.', fk('transactions'), nullable=False),
        C('role', sa.String(24), 'paycheck, net_payment, paycheck_void or net_payment_void.', nullable=False),
        C('paycheck_id', sa.String(26), 'Paycheck.', fk('payroll_paychecks'), nullable=False),
        sa.CheckConstraint("role IN ('paycheck', 'net_payment', 'paycheck_void', 'net_payment_void')", name='ck_payroll_link_role'),
        sa.UniqueConstraint('transaction_id', 'role', name='uq_payroll_ledger_link'),
        description='Neutral ownership marker: other writers refuse these transactions; audit events need payroll.read.')
    return {t.name: t for t in (paychecks, components, payments, ytd, events, links)}


def revision_columns(C):
    return (C('directive_id', sa.String(26), 'Directive the revision was saved under, kept privately.', nullable=True),)


IMMUTABLE = ('payroll_paycheck_components', 'payroll_ytd_events', 'payroll_paycheck_events', 'payroll_ledger_links')
RETAINED = ('payroll_paychecks', 'payroll_net_payments')
TRIGGERS = tuple(
    f"CREATE TRIGGER {table}_no_{action.lower()} BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT, 'payroll history is immutable'); END"
    for table in IMMUTABLE for action in ('UPDATE', 'DELETE')
) + tuple(f"CREATE TRIGGER {table}_no_delete BEFORE DELETE ON {table} BEGIN SELECT RAISE(ABORT, 'posted payroll is retained'); END"
          for table in RETAINED)
TRIGGERS += ("CREATE TRIGGER payroll_reserved_posting BEFORE INSERT ON posting_lines WHEN NEW.reversed_line_id IS NULL "
             "AND NOT EXISTS (SELECT 1 FROM payroll_ledger_links l WHERE l.transaction_id = NEW.transaction_id) "
             # An unchanged line of an edited ordinary entry: its transaction already holds the same live leg.
             "AND NOT EXISTS (SELECT 1 FROM posting_lines o WHERE o.transaction_id = NEW.transaction_id AND "
             "o.account_id = NEW.account_id AND o.debit_minor_units = NEW.debit_minor_units AND "
             "o.credit_minor_units = NEW.credit_minor_units AND o.reversed_line_id IS NULL) "
             "AND (EXISTS (SELECT 1 FROM payroll_settings s WHERE s.enabled = 1 AND NEW.account_id IN "
             "(s.wage_account, s.employer_tax_account, s.net_pay_account)) OR EXISTS (SELECT 1 FROM payroll_paychecks p "
             "WHERE p.status = 'posted' AND NEW.account_id IN (p.wage_account_id, p.employer_account_id, p.net_pay_account_id))) "
             "BEGIN SELECT RAISE(ABORT, 'Account is reserved for payroll'); END",)
