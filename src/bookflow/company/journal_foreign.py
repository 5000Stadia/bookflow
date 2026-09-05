"""Exact foreign conversion and original-command validation for journal lines."""
from __future__ import annotations

import sqlalchemy as sa

from bookflow.company.journal_models import parse_tagged_amount
from bookflow.core.errors import BookflowError
from bookflow.core.exchange import canonical_rate, convert_money
from bookflow.core.exact import INT64_MAX
from bookflow.core.money import Money, is_currency

FACTS = ('original_minor_units', 'original_currency', 'rate_used', 'rate_source')


def original_input(line):
    if line.get('original_currency') is not None:
        return {'minor_units': line['original_minor_units'], 'currency': line['original_currency']}
    return {'minor_units': line['amount_minor_units'], 'currency': line['currency']}


def override_scope(entered, home, rate):
    if rate is None:
        return
    currencies = {parse_tagged_amount(line.amount, home).currency for line in entered}
    currencies.discard(home)
    if len(currencies) != 1:
        raise BookflowError('E_VALIDATION', details={'fields': [{'field': 'rate',
            'problem': 'a manual rate requires exactly one distinct foreign currency'}]})


def line_money(s, amount, home, date, old=None, rate=None, refresh=False, cache=None):
    original = parse_tagged_amount(amount, home)
    if original.currency == home:
        return {'amount_minor_units': original.minor_units, 'currency': home, **dict.fromkeys(FACTS)}
    if (old and rate is None and not refresh
            and old['original_currency'] == original.currency
            and old['original_minor_units'] == original.minor_units):
        return {key: old[key] for key in ('amount_minor_units', 'currency', *FACTS)}
    if rate is not None:
        selected, source = rate, 'manual'
    else:
        from bookflow.company import rates
        key = (date, original.currency, home)
        if cache is None:
            cache = {}
        if key not in cache:
            cache[key] = rates.lookup(s, date, original.currency, home)
        selected, source = cache[key]['rate'], 'table:manual'
    converted = convert_money(original, home, selected)
    return {'amount_minor_units': converted.minor_units, 'currency': home,
            'original_minor_units': original.minor_units, 'original_currency': original.currency,
            'rate_used': selected, 'rate_source': source}


def _require(condition, message):
    if not condition:
        raise BookflowError('E_INTERNAL', message='Invalid generated foreign journal: ' + message)


def validate_facts(row, home, amount):
    facts = [row[key] for key in FACTS]
    if all(value is None for value in facts):
        return
    _require(all(value is not None for value in facts), 'incomplete captured facts')
    original = row['original_minor_units']
    currency = row['original_currency']
    _require(type(original) is int and 0 < original <= INT64_MAX, 'invalid original amount')
    _require(is_currency(currency) and currency != home, 'invalid original currency')
    _require(row['rate_source'] in ('manual', 'table:manual'), 'invalid rate source')
    try:
        rate = canonical_rate(row['rate_used'])
        converted = convert_money(Money(original, currency), home, rate)
    except (BookflowError, ValueError, TypeError):
        _require(False, 'invalid captured rate or conversion')
    _require(rate == row['rate_used'] and converted.minor_units == amount,
             'captured conversion does not equal home amount')


def validate(s, header, pending, inp):
    """Bind generated money to caller input and saved state, not generated effects."""
    from bookflow.company import schema as c
    home = s.company_info_row['home_currency']
    for row in pending['document_lines']:
        validate_facts(row, home, row['amount_minor_units'])
    for row in pending['posting_lines']:
        validate_facts(row, home, row['debit_minor_units'] + row['credit_minor_units'])
    documents = pending['document_lines']
    if not documents:
        return
    supplied = getattr(inp, 'lines', None)
    relevant = (supplied is None or getattr(inp, 'rate', None) is not None
                or getattr(inp, 'refresh_rates', False)
                or any(row['original_currency'] is not None for row in documents)
                or any(parse_tagged_amount(line.amount, home).currency != home for line in supplied))
    if not relevant:
        return
    current = s.company.conn.execute(sa.select(c.transactions.c.current_revision_id)
                                    .where(c.transactions.c.id == header['id'])).scalar_one_or_none()
    previous = list(s.company.conn.execute(sa.select(c.document_lines)
                    .where(c.document_lines.c.revision_id == current)
                    .order_by(c.document_lines.c.position)).mappings()) if current else []
    prior = {row['line_id']: row for row in previous}
    if supplied is None:
        from bookflow.company.journal_models import JournalLineInput
        supplied = [JournalLineInput(line_id=row['line_id'], account=row['account_id'],
                    side=row['side'], amount=original_input(row)) for row in previous]
    override_scope(supplied, home, getattr(inp, 'rate', None))
    entered = sorted(documents, key=lambda row: row['position'])
    _require(len(entered) == len(supplied), 'input and generated line counts differ')
    date = getattr(inp, 'date', None)
    if date is None and current:
        date = s.company.conn.execute(sa.select(c.transaction_revisions.c.date)
                                      .where(c.transaction_revisions.c.id == current)).scalar_one()
    _require(pending['transaction_revisions'][0]['date'] == date, 'accounting date differs from input')
    cache = {}
    for raw, row in zip(supplied, entered):
        from bookflow.company import accounts
        _require(accounts.resolve_account(s.company, raw.account)['id'] == row['account_id'],
                 'entered account differs from input')
        _require(raw.side == row['side'], 'entered side differs from input')
        key = raw.line_id.upper() if raw.line_id else None
        if key:
            _require(key in prior and row['line_id'] == key, 'input line identity differs')
        expected = line_money(s, raw.amount, home, date, prior.get(key),
                              getattr(inp, 'rate', None), getattr(inp, 'refresh_rates', False), cache)
        _require(all(row[name] == expected[name] for name in expected),
                 'generated conversion differs from original command intent')
