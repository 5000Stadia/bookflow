"""Company preference resolution for financial views, not posting policy."""
from typing import Literal
from pydantic import Field

Basis = Literal['accrual', 'cash']
FINANCIAL_VIEWS = frozenset({
    'profit-and-loss', 'balance-sheet', 'profit-and-loss-by-job',
    'profit-and-loss-by-class', 'income-tax-summary', 'cash-flows',
    'sales-by-customer', 'sales-by-item', 'sales-by-rep', 'expenses-by-vendor',
})


def basis_field():
    return Field(default=None, description=(
        'Cash or accrual for this report only. Omit to use the company report basis. '
        'Cash reports use current matching evidence and can restate earlier periods after matching changes.'),
        json_schema_extra={'choice_labels': {'cash': 'Cash', 'accrual': 'Accrual'}})


def resolve(db, inp, report):
    if report not in FINANCIAL_VIEWS:
        return 'accrual'
    selected = getattr(inp, 'basis', None)
    return selected or db.raw.execute('SELECT report_basis FROM company_info').fetchone()[0]
