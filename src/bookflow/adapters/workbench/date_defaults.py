"""Initial workbench dates, never command defaults or submitted-value repair.

Today uses the company timezone. Missing or unavailable timezone data falls back to
UTC, not the browser or host's local timezone. Historical/retry inputs stay untouched.
"""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


TRANSACTIONS = {
    **{name: 'date' for name in (
        'invoice post', 'sales-receipt post', 'bill post', 'check post',
        'card-charge post', 'transfer post', 'journal post', 'register post',
        'credit-memo post', 'customer-refund post', 'vendor-credit post',
        'item-receipt post', 'purchase-order post', 'inventory adjust',
        'statement-charge post', 'sales-tax pay', 'batch-invoice post',
        'estimate create', 'proposal create', 'work-order create',
        'time-activity create', 'payment receive', 'bill pay',
    )},
    'deposit post': 'document.date',
}
PERIOD_REPORTS = (
    'general-ledger', 'transaction-detail', 'profit-and-loss', 'cash-flows',
    'income-tax-summary', 'profit-and-loss-by-class', 'profit-and-loss-by-job',
    'sales-by-customer', 'sales-by-item', 'sales-by-rep', 'expenses-by-vendor',
    'statement',
)
AS_OF_REPORTS = (
    'ap-aging', 'ar-aging', 'collections', 'inventory-valuation', 'missing-checks',
    'open-invoices', 'stock-status', 'unbilled-costs', 'unpaid-bills',
)
REPORTS = {
    **{'report ' + name: ('date_from', 'date_to') for name in PERIOD_REPORTS},
    **{'report ' + name: ('as_of',) for name in AS_OF_REPORTS},
    'report balance-sheet': ('date_to',),
    'report trial-balance': ('date_to',),
    'sales-tax liability': ('as_of',),
}


def company_today(company, *, now=None):
    info = (company or {}).get('info') or {}
    name = info.get('timezone') or 'UTC'
    try:
        zone = ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        zone = timezone.utc
    return (now or datetime.now(timezone.utc)).astimezone(zone).date().isoformat()


def _present(values, path):
    for part in path.split('.'):
        if not isinstance(values, dict) or part not in values:
            return False
        values = values[part]
    return True


def seed(command, today, *, initial_get, query, originals, attempted):
    """Fill explicitly owned fields after route initializers, without replacing intent.

    Query-bearing pages can be conversions, drilldowns or continuations. Retain an
    explicit mapped date (including empty/invalid text), but never synthesize a range
    or accounting date for such a page. POST, edit and recovery callers opt out.
    """
    if not initial_get:
        return
    fields = REPORTS.get(command) or ((TRANSACTIONS[command],) if command in TRANSACTIONS else ())
    for field in fields:
        key = 'f:' + field
        if key in attempted or _present(originals, field):
            continue
        if key in query:
            attempted[key] = query[key]
        elif field in query:
            attempted[key] = query[field]
        elif not query:
            attempted[key] = today[:4] + '-01-01' if field == 'date_from' else today
