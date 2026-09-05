"""Company-scoped manual rates; exact-date lookup never accesses a network."""
from __future__ import annotations

import base64
import hmac

import sqlalchemy as sa

from bookflow.company import schema
from bookflow.company.query import continuation, page_state
from bookflow.company.rate_models import RateOutput, RatePageOutput, RateSetInput, RateWriteOutput, currency_code, iso_date
from bookflow.core.errors import BookflowError
from bookflow.core.exact import _require_i64
from bookflow.core.ids import new_id
from bookflow.core.registry import Applied, Plan, Touched
from bookflow.core.session import now_iso


def _pair(date, from_currency, home_currency):
    try:
        iso_date(date)
        currency_code(from_currency)
        currency_code(home_currency)
        if from_currency == home_currency:
            raise ValueError('from_currency must differ from home currency')
    except ValueError as exc:
        raise BookflowError('E_VALIDATION', details={'fields': [
            {'field': 'date/from_currency', 'problem': str(exc)}]}) from None
    t = schema.exchange_rates
    return (t.c.date == date, t.c.from_currency == from_currency, t.c.to_currency == home_currency)


def _find(s, date, from_currency, home_currency):
    row = s.company.conn.execute(sa.select(schema.exchange_rates).where(*_pair(date, from_currency, home_currency))).mappings().first()
    return dict(row) if row is not None else None


def lookup(s, date, from_currency, home_currency):
    """Return an exact date/pair row in the caller's company transaction or E_NO_EXCHANGE_RATE."""
    row = _find(s, date, from_currency, home_currency)
    if row is None:
        raise BookflowError('E_NO_EXCHANGE_RATE', details={
            'date': date, 'from_currency': from_currency, 'to_currency': home_currency})
    return row


def show(s, inp):
    if inp.rate_id is not None:
        found = s.company.conn.execute(sa.select(schema.exchange_rates).where(schema.exchange_rates.c.id == inp.rate_id)).mappings().first()
        row = dict(found) if found is not None else None
    else:
        row = _find(s, inp.date, inp.from_currency, s.company_info_row['home_currency'])
    if row is None:
        raise BookflowError('E_RECORD_NOT_FOUND', details={'record_type': 'exchange_rate', **inp.model_dump(exclude_none=True)})
    return RateOutput(**row)


def prepare(s, ctx, inp):
    inp = RateSetInput.model_validate(inp.model_dump())
    home = s.company_info_row['home_currency']
    old = _find(s, inp.date, inp.from_currency, home)
    version = old['version'] if old else 0
    if inp.expected_version != version:
        raise BookflowError('E_VERSION_CONFLICT', details={
            'record_type': 'exchange_rate', 'record_id': old['id'] if old else None,
            'expected_version': inp.expected_version, 'current_version': version,
            'updated_by': old['entered_by'] if old else None,
            'updated_at': old['entered_at'] if old else None, 'changed_fields': ['rate'] if old else [],
        })
    changed = old is None or old['rate'] != inp.rate
    row = old if not changed else dict(
        id=old['id'] if old else new_id(), version=_require_i64(version + 1, field='version', positive=True),
        date=inp.date, from_currency=inp.from_currency, to_currency=home,
        rate=inp.rate, source='manual', entered_by=s.actor.id, entered_at=now_iso())
    return Plan(RateWriteOutput(**row, changed=changed), {'input': inp, 'row': row, 'before': old, 'changed': changed})


def apply(plan, ctx, s):
    fresh = prepare(s, ctx, plan.data['input'])
    if not fresh.data['changed']:
        return Applied(fresh.preview, [], 'no change')
    row, old = fresh.data['row'], fresh.data['before']
    t = schema.exchange_rates
    if old:
        s.company.conn.execute(t.update().where(t.c.id == row['id']).values(**row))
    else:
        s.company.conn.execute(t.insert().values(**row))
    touched = Touched('exchange_rate', row['id'], 'update' if old else 'create',
        old['version'] if old else None, row['version'], row, old, db='company')
    return Applied(fresh.preview, [touched], f"set {row['date']} {row['from_currency']}/{row['to_currency']} rate")


def _cursor_signature(db, payload):
    from bookflow.company.ledger_reports import _cursor_key
    signature = hmac.digest(_cursor_key(db), b'bookflow.rate.cursor.v1\x00' + payload.encode('ascii'), 'sha256')
    return base64.urlsafe_b64encode(signature).decode().rstrip('=')


def _verified_cursor(db, cursor):
    if cursor is None:
        return None
    try:
        cursor.encode('ascii')
        payload, signature = cursor.split('.')
        if not hmac.compare_digest(signature, _cursor_signature(db, payload)):
            raise ValueError
        return payload
    except (ValueError, UnicodeError):
        raise BookflowError('E_VALIDATION', details={'fields': [
            {'field': 'cursor', 'problem': 'invalid rate continuation; restart without cursor'}]}) from None


def page(s, ctx, inp):
    contract = inp.model_copy(update={'cursor': _verified_cursor(s.company, inp.cursor)})
    state = page_state(s, 'rate query', contract, ctx.on_behalf_of)
    t = schema.exchange_rates
    q = sa.select(t)
    if inp.date_from:
        q = q.where(t.c.date >= inp.date_from)
    if inp.date_to:
        q = q.where(t.c.date <= inp.date_to)
    if inp.from_currency:
        q = q.where(t.c.from_currency == inp.from_currency)
    q = q.order_by(t.c.date, t.c.from_currency, t.c.id)
    rows = list(s.company.conn.execute(q.offset(state.offset).limit(inp.limit + 1)).mappings())
    more = len(rows) > inp.limit
    rows = rows[:inp.limit]
    cursor = continuation(state, len(rows), more)
    if cursor is not None:
        cursor += '.' + _cursor_signature(s.company, cursor)
    return RatePageOutput(items=[RateOutput(**row) for row in rows], count=len(rows), has_more=more,
        next_cursor=cursor, audit_watermark=state.sequence)
