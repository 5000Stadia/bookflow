"""Initial workbench dates, never command defaults or submitted-value repair.

Today uses the company timezone. Missing or unavailable timezone data falls back to
UTC, not the browser or host's local timezone. Historical/retry inputs stay untouched.
"""
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


TRANSACTIONS = {
    **{name: 'date' for name in (
        'invoice post', 'sales-receipt post', 'bill post', 'check post',
        'card-charge post', 'card-credit post', 'transfer post', 'journal post', 'register post',
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
    'statement', 'purchases-by-vendor', 'purchases-by-item', 'deposit-detail',
    'transaction-list-by-date',
)
AS_OF_REPORTS = (
    'ap-aging', 'ar-aging', 'collections', 'inventory-valuation', 'missing-checks',
    'open-invoices', 'stock-status', 'unbilled-costs', 'unpaid-bills',
    'customer-balance-summary', 'customer-balance-detail', 'vendor-balance-summary',
    'vendor-balance-detail', 'reconciliation-discrepancy',
)
REPORTS = {
    **{'report ' + name: ('date_from', 'date_to') for name in PERIOD_REPORTS},
    **{'report ' + name: ('as_of',) for name in AS_OF_REPORTS},
    'report balance-sheet': ('date_to',),
    'report trial-balance': ('date_to',),
    'report open-purchase-orders': ('date_to',),
    # Read over a period, as the anchor's Sales Tax Liability report is; as_of stays a
    # synonym for date_to on the command, so the form leaves it empty.
    'sales-tax liability': ('date_from', 'date_to'),
}
# Reports read over a whole calendar year that open, as the anchor's do, on the last one: a
# 1099 summary is prepared in January for the year just ended.
LAST_YEAR_REPORTS = {'report vendor-1099-summary': ('date_from', 'date_to')}


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
    if command in LAST_YEAR_REPORTS:
        last = str(int(today[:4]) - 1)
        if not query and not any('f:' + field in attempted or _present(originals, field)
                                 for field in LAST_YEAR_REPORTS[command]):
            attempted['f:date_from'], attempted['f:date_to'] = last + '-01-01', last + '-12-31'
            return
        fields = LAST_YEAR_REPORTS[command]
    else:
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


def _month_end(year, month):
    """The last day of a month, months counted from 1 and allowed to run past twelve."""
    year, month = year + (month - 1) // 12, (month - 1) % 12 + 1
    following = date(year + month // 12, month % 12 + 1, 1)
    return following - timedelta(days=1)


def presets(command, today):
    """The one-tap date ranges a report offers beside its filters, as (label, {field: iso}).

    The ranges are the calendar ones dates.js offers in its preset menu, counted from the
    company's today. A period report takes a from/to pair; a report read on one date takes
    that date. A report this map does not date offers none.
    """
    fields = REPORTS.get(command) or LAST_YEAR_REPORTS.get(command)
    if not fields:
        return []
    day = date.fromisoformat(today)
    quarter = (day.month - 1) // 3 * 3 + 1
    if fields == ('date_from', 'date_to'):
        ranges = (('This month', date(day.year, day.month, 1), _month_end(day.year, day.month)),
                  ('This quarter', date(day.year, quarter, 1), _month_end(day.year, quarter + 2)),
                  ('Year to date', date(day.year, 1, 1), day),
                  ('Last year', date(day.year - 1, 1, 1), date(day.year - 1, 12, 31)))
        return [(label, {'date_from': start.isoformat(), 'date_to': end.isoformat()})
                for label, start, end in ranges]
    field, = fields
    ends = (('Today', day),
            ('End of last month', date(day.year, day.month, 1) - timedelta(days=1)),
            ('End of last quarter', date(day.year, quarter, 1) - timedelta(days=1)),
            ('End of last year', date(day.year - 1, 12, 31)))
    return [(label, {field: end.isoformat()}) for label, end in ends]
