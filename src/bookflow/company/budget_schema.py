"""Budgets and memorized reports (co0078, R109).

A budget is a named plan for one fiscal year: an amount per account per month, for the whole
company or for one customer:job or class at a time. It is a list-like record -- versioned,
audited, exact to the minor unit -- and it never posts: nothing here is read by the ledger, and
the ledger is never written by a budget command. Only cells that hold an amount are stored; a
cleared cell is a removed row, whose earlier value the audit event keeps.

A memorized report is a report command and its options kept under a name, optionally in a
group, shared by everyone in the company. A date range the person chose by name ("this month",
"last fiscal year") is stored as that name and resolved again on every run.
"""
import sqlalchemy as sa

BUDGET_KINDS = ('profit_and_loss', 'balance_sheet')
BUDGET_DIMENSIONS = ('none', 'customer', 'class')


def define_tables(metadata, C, T, common):
    budgets = T('budgets',
        *common(),
        C('name', sa.String(200), 'Name a person gave this budget, as entered.', nullable=False),
        C('name_key', sa.String(400), 'NFC-normalized, trimmed and case-folded name; unique within the company.', nullable=False),
        C('fiscal_year', sa.Integer, 'Fiscal year budgeted, named by the calendar year it starts in.', nullable=False),
        C('first_month', sa.String(7), 'First month of the budgeted fiscal year, YYYY-MM; the budget covers it and the eleven after it.', nullable=False),
        C('kind', sa.String(16), 'profit_and_loss (income and expense per month) or balance_sheet (ending balance per month).', nullable=False),
        C('dimension', sa.String(8), 'none for a company budget; customer or class when amounts are kept per customer:job or per class.', nullable=False),
        C('currency', sa.String(3), 'Home currency the amounts are in.', nullable=False),
        sa.UniqueConstraint('name_key', name='uq_budget_name_key'),
        sa.CheckConstraint("length(trim(name)) > 0", name='ck_budget_name_nonblank'),
        sa.CheckConstraint("kind IN ('profit_and_loss','balance_sheet')", name='ck_budget_kind'),
        sa.CheckConstraint("dimension IN ('none','customer','class')", name='ck_budget_dimension'),
        sa.CheckConstraint("kind = 'profit_and_loss' OR dimension = 'none'", name='ck_budget_balance_sheet_company'),
        sa.CheckConstraint("typeof(fiscal_year) = 'integer' AND fiscal_year BETWEEN 1 AND 9998", name='ck_budget_fiscal_year'),
        sa.CheckConstraint("length(first_month) = 7", name='ck_budget_first_month'),
        description='Named budgets: a plan for one fiscal year, per account and month, that never posts.')

    amounts = T('budget_amounts',
        C('id', sa.String(26), 'Stable ULID of this budget cell.', primary_key=True),
        C('budget_id', sa.String(26), 'Budget this amount belongs to.', sa.ForeignKey('budgets.id'), nullable=False),
        C('account_id', sa.String(26), 'Account budgeted.', sa.ForeignKey('accounts.id'), nullable=False),
        C('customer_id', sa.String(26), 'Customer or job this amount is for, on a customer budget; null otherwise.', sa.ForeignKey('customers.id'), nullable=True),
        C('class_id', sa.String(26), 'Class this amount is for, on a class budget; null otherwise.', sa.ForeignKey('classes.id'), nullable=True),
        C('dimension_key', sa.String(26), 'The customer or class id, or empty on a company budget; part of the cell identity.', nullable=False),
        C('month', sa.String(7), 'Month budgeted, YYYY-MM, within the budget fiscal year.', nullable=False),
        C('amount_minor_units', sa.BigInteger, 'Budgeted amount in home-currency minor units: the month net for profit and loss, the ending balance for balance sheet; statement sign, so income and expense are both positive.', nullable=False),
        C('updated_at', sa.String(32), 'UTC timestamp this amount was last set.', nullable=False),
        C('updated_by', sa.String(26), 'Principal who last set this amount.', nullable=False),
        C('updated_via', sa.String(16), 'Interface this amount was last set through.', nullable=False),
        sa.UniqueConstraint('budget_id', 'account_id', 'dimension_key', 'month', name='uq_budget_amount_cell'),
        sa.CheckConstraint("typeof(amount_minor_units) = 'integer' AND amount_minor_units != 0", name='ck_budget_amount_integer'),
        sa.CheckConstraint("dimension_key = coalesce(customer_id, class_id, '')", name='ck_budget_amount_dimension_key'),
        sa.CheckConstraint("customer_id IS NULL OR class_id IS NULL", name='ck_budget_amount_one_dimension'),
        sa.CheckConstraint("length(month) = 7", name='ck_budget_amount_month'),
        sa.Index('ix_budget_amounts_account', 'account_id'),
        description='Budget cells: one nonzero amount per budget, account, customer or class, and month.')

    memorized = T('memorized_reports',
        *common(),
        C('name', sa.String(200), 'Name a person gave this memorized report, as entered.', nullable=False),
        C('name_key', sa.String(400), 'NFC-normalized, trimmed and case-folded name; unique within the company.', nullable=False),
        C('group_name', sa.String(200), 'Memorized report group it is filed under; null when it is in no group.', nullable=True),
        C('report', sa.String(64), 'Report it runs, by its verb: profit-and-loss, budget-vs-actual, ...', nullable=False),
        C('filters', sa.Text, "Canonical JSON of the report's own inputs, without paging and without the dates a date_range supplies.", nullable=False),
        C('date_range', sa.String(32), 'Named relative date range resolved at every run, such as this_month or last_fiscal_year; null when the dates are fixed in filters.', nullable=True),
        sa.UniqueConstraint('name_key', name='uq_memorized_report_name_key'),
        sa.CheckConstraint("length(trim(name)) > 0", name='ck_memorized_report_name_nonblank'),
        sa.CheckConstraint("json_valid(filters) AND json_type(filters) = 'object'", name='ck_memorized_report_filters'),
        description='Memorized reports: a report and its options kept under a name, shared by the company.')
    return {'budgets': budgets, 'budget_amounts': amounts, 'memorized_reports': memorized}
