"""Fixed-asset depreciation and loans (co0080, R119).

**Depreciation.** A fixed-asset item (the item list keeps its cost, accounts and sale fields) gains
a book depreciation setup: method, salvage value, in-service date, useful life in months and
whether it is depreciated by the month or by the year. `fixed-asset depreciate` posts ordinary
journal entries -- debit depreciation expense, credit accumulated depreciation -- and writes one
``fixed_asset_depreciations`` row per asset and period an entry covers. A period counts as posted
while its entry is posted: voiding the entry reopens the period, and nothing here is rewritten.
`fixed-asset dispose` writes one journal removing the asset's cost and accumulated depreciation
with the gain or loss, and one ``fixed_asset_disposals`` row; voiding that entry undoes the disposal.

**Loans.** A loan is the anchor's Loan Manager record: a liability account, the amount borrowed,
an annual rate, the number of payments, how often they fall due, the first due date, an optional
balloon and an escrow amount. `loan pay` writes a check (or a card charge) splitting each payment
into principal (to the liability), interest and escrow, and one ``loan_payments`` row recording
the split. A payment stands while its check does.

The two link tables are append-only: guards refuse an update or a delete.
"""
import sqlalchemy as sa

METHODS = ('straight_line', 'declining_balance')
PERIODS = ('month', 'year')
FREQUENCIES = ('weekly', 'biweekly', 'monthly', 'quarterly', 'semiannual', 'annual')


def define_tables(metadata, C, T, common):
    setups = T('fixed_asset_setups',
        *common(),
        C('item_id', sa.String(26), 'Fixed-asset item this setup depreciates.', sa.ForeignKey('items.id'), nullable=False),
        C('method', sa.String(24), 'Book depreciation method: straight_line or declining_balance.', nullable=False),
        C('declining_percent', sa.Integer, 'Declining-balance rate as a percent of the straight-line rate: 150 or 200; null for straight line.', nullable=True),
        C('salvage_minor_units', sa.BigInteger, 'Salvage value the asset is never depreciated below, in home-currency minor units.', nullable=False),
        C('in_service_date', sa.String(10), 'Date the asset was placed in service, YYYY-MM-DD; depreciation starts with its month.', nullable=False),
        C('life_months', sa.Integer, 'Useful life in months.', nullable=False),
        C('period', sa.String(8), 'Depreciation period: month or year (twelve months from the in-service month).', nullable=False),
        C('currency', sa.String(3), 'Home currency of the amounts.', nullable=False),
        sa.UniqueConstraint('item_id', name='uq_fixed_asset_setup_item'),
        sa.CheckConstraint("method IN ('straight_line','declining_balance')", name='ck_fixed_asset_setup_method'),
        sa.CheckConstraint("(method = 'straight_line' AND declining_percent IS NULL) OR "
                           "(method = 'declining_balance' AND declining_percent IN (150, 200))", name='ck_fixed_asset_setup_declining'),
        sa.CheckConstraint("typeof(salvage_minor_units) = 'integer' AND salvage_minor_units >= 0", name='ck_fixed_asset_setup_salvage'),
        sa.CheckConstraint("life_months BETWEEN 1 AND 1200", name='ck_fixed_asset_setup_life'),
        sa.CheckConstraint("period IN ('month','year')", name='ck_fixed_asset_setup_period'),
        sa.CheckConstraint("length(in_service_date) = 10", name='ck_fixed_asset_setup_date'),
        description='Book depreciation setups: one per fixed-asset item that is depreciated.')

    depreciations = T('fixed_asset_depreciations',
        C('id', sa.String(26), 'Stable ULID of this posted depreciation period.', primary_key=True),
        C('item_id', sa.String(26), 'Fixed-asset item depreciated.', sa.ForeignKey('items.id'), nullable=False),
        C('period_number', sa.Integer, 'One-based period of the schedule this row posts.', nullable=False),
        C('period_start', sa.String(10), 'First day of the period, YYYY-MM-DD.', nullable=False),
        C('period_end', sa.String(10), 'Last day of the period, YYYY-MM-DD.', nullable=False),
        C('amount_minor_units', sa.BigInteger, 'Depreciation posted for the period, in home-currency minor units.', nullable=False),
        C('currency', sa.String(3), 'Home currency of the amount.', nullable=False),
        C('transaction_id', sa.String(26), 'Journal entry that posted it; the period stays posted while the entry is posted.',
          sa.ForeignKey('transactions.id'), nullable=False),
        C('created_at', sa.String(32), 'UTC timestamp the period was posted.', nullable=False),
        C('created_by', sa.String(26), 'Principal who posted it.', nullable=False),
        C('created_via', sa.String(16), 'Interface it was posted through.', nullable=False),
        C('audit_event_id', sa.String(26), 'Audit event of the depreciation run that wrote it.', sa.ForeignKey('audit_events.id'), nullable=False),
        sa.CheckConstraint("typeof(amount_minor_units) = 'integer' AND amount_minor_units >= 0", name='ck_fixed_asset_depreciation_amount'),
        sa.CheckConstraint("period_number >= 1", name='ck_fixed_asset_depreciation_period'),
        sa.Index('ix_fixed_asset_depreciations_item', 'item_id', 'period_number'),
        sa.Index('ix_fixed_asset_depreciations_transaction', 'transaction_id'),
        description='Depreciation periods posted, each linked to the journal entry that posted it.')

    disposals = T('fixed_asset_disposals',
        C('id', sa.String(26), 'Stable ULID of this disposal.', primary_key=True),
        C('item_id', sa.String(26), 'Fixed-asset item disposed of.', sa.ForeignKey('items.id'), nullable=False),
        C('disposal_date', sa.String(10), 'Date the asset was sold or disposed of, YYYY-MM-DD.', nullable=False),
        C('proceeds_minor_units', sa.BigInteger, 'What the asset sold for; zero when it was scrapped.', nullable=False),
        C('costs_minor_units', sa.BigInteger, 'Costs of the sale, taken from the proceeds.', nullable=False),
        C('cost_minor_units', sa.BigInteger, 'Cost basis removed from the asset account.', nullable=False),
        C('accumulated_minor_units', sa.BigInteger, 'Accumulated depreciation removed from its account.', nullable=False),
        C('gain_minor_units', sa.BigInteger, 'Gain (positive) or loss (negative) on disposal.', nullable=False),
        C('deposit_account_id', sa.String(26), 'Account the net proceeds were debited to; null when there were none.', sa.ForeignKey('accounts.id'), nullable=True),
        C('currency', sa.String(3), 'Home currency of the amounts.', nullable=False),
        C('transaction_id', sa.String(26), 'Journal entry that posted the disposal; it stands while the entry is posted.',
          sa.ForeignKey('transactions.id'), nullable=False),
        C('created_at', sa.String(32), 'UTC timestamp of the disposal.', nullable=False),
        C('created_by', sa.String(26), 'Principal who recorded it.', nullable=False),
        C('created_via', sa.String(16), 'Interface it was recorded through.', nullable=False),
        C('audit_event_id', sa.String(26), 'Audit event that recorded it.', sa.ForeignKey('audit_events.id'), nullable=False),
        sa.CheckConstraint("typeof(proceeds_minor_units) = 'integer' AND proceeds_minor_units >= 0", name='ck_fixed_asset_disposal_proceeds'),
        sa.CheckConstraint("typeof(costs_minor_units) = 'integer' AND costs_minor_units >= 0", name='ck_fixed_asset_disposal_costs'),
        sa.Index('ix_fixed_asset_disposals_item', 'item_id'),
        description='Fixed-asset disposals: the journal entry removing the asset with its gain or loss.')

    loans = T('loans',
        *common(),
        C('name', sa.String(200), 'Name a person gave this loan, as entered.', nullable=False),
        C('name_key', sa.String(400), 'NFC-normalized, trimmed and case-folded name; unique within the company.', nullable=False),
        C('lender_id', sa.String(26), 'Vendor lending the money; the payee on its payments. Null when not named.', sa.ForeignKey('vendors.id'), nullable=True),
        C('liability_account_id', sa.String(26), 'Liability account the loan balance is carried in.', sa.ForeignKey('accounts.id'), nullable=False),
        C('interest_account_id', sa.String(26), 'Expense account interest is charged to.', sa.ForeignKey('accounts.id'), nullable=False),
        C('escrow_account_id', sa.String(26), 'Account escrow is paid into; null when the loan has no escrow.', sa.ForeignKey('accounts.id'), nullable=True),
        C('payment_account_id', sa.String(26), 'Bank or credit card account payments come from unless another is named.', sa.ForeignKey('accounts.id'), nullable=True),
        C('principal_minor_units', sa.BigInteger, 'Original amount borrowed, in home-currency minor units.', nullable=False),
        C('annual_rate', sa.String(16), 'Annual interest rate in percent, exact decimal.', nullable=False),
        C('term_payments', sa.Integer, 'Number of payments over the full term.', nullable=False),
        C('frequency', sa.String(12), 'How often payments fall due: weekly, biweekly, monthly, quarterly, semiannual or annual.', nullable=False),
        C('origination_date', sa.String(10), 'Date the loan was taken out, YYYY-MM-DD; null when not given.', nullable=True),
        C('first_payment_date', sa.String(10), 'Due date of payment 1, YYYY-MM-DD.', nullable=False),
        C('payment_minor_units', sa.BigInteger, 'Regular principal and interest payment.', nullable=False),
        C('balloon_minor_units', sa.BigInteger, 'Balloon due with the last payment; zero when fully amortizing.', nullable=False),
        C('escrow_minor_units', sa.BigInteger, 'Escrow added to each scheduled payment; zero when none.', nullable=False),
        C('currency', sa.String(3), 'Home currency of the amounts.', nullable=False),
        sa.UniqueConstraint('name_key', name='uq_loan_name_key'),
        sa.CheckConstraint("length(trim(name)) > 0", name='ck_loan_name_nonblank'),
        sa.CheckConstraint("typeof(principal_minor_units) = 'integer' AND principal_minor_units > 0", name='ck_loan_principal'),
        sa.CheckConstraint("typeof(payment_minor_units) = 'integer' AND payment_minor_units > 0", name='ck_loan_payment'),
        sa.CheckConstraint("typeof(balloon_minor_units) = 'integer' AND balloon_minor_units >= 0", name='ck_loan_balloon'),
        sa.CheckConstraint("typeof(escrow_minor_units) = 'integer' AND escrow_minor_units >= 0", name='ck_loan_escrow'),
        sa.CheckConstraint("escrow_minor_units = 0 OR escrow_account_id IS NOT NULL", name='ck_loan_escrow_account'),
        sa.CheckConstraint("term_payments BETWEEN 1 AND 3000", name='ck_loan_term'),
        sa.CheckConstraint("frequency IN ('weekly','biweekly','monthly','quarterly','semiannual','annual')", name='ck_loan_frequency'),
        description='Loans: the amortization terms a schedule and its payments are computed from.')

    payments = T('loan_payments',
        C('id', sa.String(26), 'Stable ULID of this loan payment.', primary_key=True),
        C('loan_id', sa.String(26), 'Loan paid.', sa.ForeignKey('loans.id'), nullable=False),
        C('payment_number', sa.Integer, 'Scheduled payment this pays; null for an extra principal payment.', nullable=True),
        C('payment_date', sa.String(10), 'Date of the check or charge, YYYY-MM-DD.', nullable=False),
        C('principal_minor_units', sa.BigInteger, 'Principal paid, debited to the liability account.', nullable=False),
        C('interest_minor_units', sa.BigInteger, 'Interest paid, debited to the interest account.', nullable=False),
        C('escrow_minor_units', sa.BigInteger, 'Escrow paid, debited to the escrow account.', nullable=False),
        C('currency', sa.String(3), 'Home currency of the amounts.', nullable=False),
        C('transaction_id', sa.String(26), 'Check or card charge that paid it; the payment stands while that document is posted.',
          sa.ForeignKey('transactions.id'), nullable=False),
        C('created_at', sa.String(32), 'UTC timestamp the payment was recorded.', nullable=False),
        C('created_by', sa.String(26), 'Principal who recorded it.', nullable=False),
        C('created_via', sa.String(16), 'Interface it was recorded through.', nullable=False),
        C('audit_event_id', sa.String(26), 'Audit event that recorded it.', sa.ForeignKey('audit_events.id'), nullable=False),
        sa.CheckConstraint("typeof(principal_minor_units) = 'integer' AND principal_minor_units >= 0", name='ck_loan_payment_principal'),
        sa.CheckConstraint("typeof(interest_minor_units) = 'integer' AND interest_minor_units >= 0", name='ck_loan_payment_interest'),
        sa.CheckConstraint("typeof(escrow_minor_units) = 'integer' AND escrow_minor_units >= 0", name='ck_loan_payment_escrow'),
        sa.Index('ix_loan_payments_loan', 'loan_id', 'payment_date'),
        description='Loan payments: how each check split between principal, interest and escrow.')
    return {'fixed_asset_setups': setups, 'fixed_asset_depreciations': depreciations, 'fixed_asset_disposals': disposals,
            'loans': loans, 'loan_payments': payments}


GUARDS = tuple(
    f"CREATE TRIGGER {table}_immutable_{verb} BEFORE {verb.upper()} ON {table} BEGIN SELECT RAISE(ABORT, 'immutable {table}'); END"
    for table in ('fixed_asset_depreciations', 'fixed_asset_disposals', 'loan_payments') for verb in ('update', 'delete'))
