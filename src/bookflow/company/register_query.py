"""Account register projections over immutable general-ledger effects."""
from __future__ import annotations

import base64
import hmac
import json
from typing import Literal

import sqlalchemy as sa
from pydantic import Field, ValidationError

from bookflow.company import accounts, ledger_reports as reports, schema
from bookflow.core.errors import BookflowError


class RegisterQueryInput(reports.GeneralLedgerInput):
    account: str = Field(min_length=1, max_length=1000)
    cursor: str | None = Field(default=None, max_length=8192)


class RegisterAccount(reports.StrictModel):
    id: str
    label: str
    name: str
    full_name: str
    number: str | None
    type: accounts.AccountType
    normal_balance: Literal['debit', 'credit']
    currency: str
    active: bool


class RegisterTotals(reports.StrictModel):
    opening: reports.MoneyOutput
    increases: reports.MoneyOutput
    decreases: reports.MoneyOutput
    closing: reports.MoneyOutput


class CurrentBalanceSnapshot(reports.StrictModel):
    balance: reports.MoneyOutput
    generation_time: str
    audit_watermark: int


class RegisterRow(reports.GeneralLedgerRow):
    increase: reports.MoneyOutput
    decrease: reports.MoneyOutput
    running_balance: reports.MoneyOutput
    revision_number: int | None = None
    memo: str | None = None
    category_label: str | None = None
    class_summary: str | None = None


class RegisterQueryOutput(reports.Page):
    account: RegisterAccount
    totals: RegisterTotals
    ledger_totals: reports.GeneralLedgerTotals
    current_balance: CurrentBalanceSnapshot
    rows: list[RegisterRow]


class _Cursor(reports.StrictModel):
    version: Literal[1] = 1
    account_id: str
    company_id: str
    query: str
    permissions: str
    display: str
    report_cursor: str = Field(max_length=4096)


def _invalid():
    return BookflowError('E_VALIDATION', details={'fields': [
        {'field': 'cursor', 'problem': 'invalid register continuation; restart without cursor'}]})


def _mac(db, payload):
    return hmac.digest(reports._cursor_key(db), b'bookflow.register.cursor.v1\x00' + payload, 'sha256')


def _decode(cursor, db):
    try:
        if not 1 <= len(cursor) <= 8192:
            raise ValueError
        payload, signature = cursor.encode('ascii').split(b'.')
        decode = lambda value: base64.b64decode(value + b'=' * (-len(value) % 4), altchars=b'-_', validate=True)
        payload, signature = decode(payload), decode(signature)
        if not hmac.compare_digest(signature, _mac(db, payload)):
            raise ValueError
        return _Cursor.model_validate_json(payload)
    except (ValueError, UnicodeError, ValidationError):
        raise _invalid() from None


def _encode(state, db):
    payload = state.model_dump_json().encode()
    encode = lambda value: base64.urlsafe_b64encode(value).decode().rstrip('=')
    return encode(payload) + '.' + encode(_mac(db, payload))


def _label(snapshot, info):
    label = snapshot.get('name') if info.get('show_lowest_subaccount_only') else snapshot.get('full_name')
    label = label or snapshot.get('name') or ''
    if info.get('use_account_numbers') and snapshot.get('number'):
        label = str(snapshot['number']) + ' · ' + label
    return label


def _revision_summaries(db, revision_ids, selected_id, info):
    """Stream at most one page's bounded entered lines into compact summaries."""
    if not revision_ids:
        return {}
    summaries = {row.id: {'memo': row.memo, 'revision_number': row.revision_number, 'selected': 0, 'offsets': 0,
                         'category': None, 'class': None, 'mixed_class': False}
        for row in db.conn.execute(sa.select(schema.transaction_revisions.c.id,
            schema.transaction_revisions.c.memo, schema.transaction_revisions.c.revision_number).where(schema.transaction_revisions.c.id.in_(revision_ids)))}
    lines = schema.document_lines
    for row in db.conn.execute(sa.select(lines.c.revision_id, lines.c.account_id,
        lines.c.account_snapshot, lines.c.class_id, lines.c.class_name)
        .where(lines.c.revision_id.in_(revision_ids), lines.c.kind == 'journal')
        .order_by(lines.c.revision_id, lines.c.position)):
        summary = summaries[row.revision_id]
        if row.account_id == selected_id:
            summary['selected'] += 1
            continue
        class_value = (row.class_id, row.class_name)
        if summary['offsets'] == 0:
            summary['category'] = _label(json.loads(row.account_snapshot), info)
            summary['class'] = class_value
        elif class_value != summary['class']:
            summary['mixed_class'] = True
        summary['offsets'] += 1
    return summaries


def query(inp: RegisterQueryInput, s, *, principal_id=None) -> RegisterQueryOutput:
    # The outer snapshot outlives both the report and all enrichment/current reads.
    with reports._snapshot(s.company):
        db = s.company
        previous = _decode(inp.cursor, db) if inp.cursor is not None else None
        company = str(s.company_row['id'])
        query_hash = reports._hash(inp.model_dump(exclude={'cursor'}))
        permissions = reports._hash([reports.permission_fingerprint(s, principal_id), s.memberships])
        if previous and (previous.company_id, previous.query, previous.permissions) != (company, query_hash, permissions):
            raise _invalid()
        account = accounts.resolve_account(db, previous.account_id if previous else inp.account)
        if account['type'] == 'non_posting':
            raise BookflowError('E_VALIDATION', details={'fields': [
                {'field': 'account', 'problem': 'a non-posting account has no register'}]})
        info = dict(db.conn.execute(sa.select(schema.company_info)).mappings().one())
        currency = info['home_currency']
        side = accounts.NORMAL_BALANCE[account['type']]
        normal_sign = -1 if side == 'credit' else 1
        display = reports._hash([{key: account.get(key) for key in (
            'id', 'name', 'full_name', 'number', 'type', 'currency', 'active')},
            info.get('use_account_numbers'), info.get('show_lowest_subaccount_only')])
        if previous and previous.display != display:
            raise BookflowError('E_QUERY_STALE', details={'restart': 'Account display changed; restart without cursor.'})
        report_input = reports.GeneralLedgerInput(account=account['id'], date_from=inp.date_from,
            date_to=inp.date_to, basis=inp.basis, limit=inp.limit,
            cursor=previous.report_cursor if previous else None)
        result = reports.general_ledger(report_input, s, principal_id=principal_id)
        revision_ids = {row.revision_id for row in result.rows if row.revision_id is not None}
        summaries = _revision_summaries(db, revision_ids, account['id'], info)
        rows = []
        for row in result.rows:
            values = row.model_dump()
            summary = summaries.get(row.revision_id)
            category = class_label = memo = None
            if summary:
                memo = summary['memo']
                if row.transaction_type != 'journal_entry':
                    category = {'invoice': 'Invoice', 'sales_receipt': 'Sales receipt', 'payment': 'Payment', 'deposit': 'Deposit', 'credit_memo': 'Credit memo',
                                'customer_refund': 'Refund', 'statement_charge': 'Statement charge'}.get(row.transaction_type)
                    class_label = row.class_name
                elif summary['selected'] != 1:
                    category, class_label = 'General journal', row.class_name
                else:
                    category = summary['category'] if summary['offsets'] == 1 else 'Splits'
                    class_label = 'Mixed' if summary['mixed_class'] else (summary['class'] or (None, None))[1]
            rows.append(RegisterRow(**values,
                increase=row.credit if side == 'credit' else row.debit,
                decrease=row.debit if side == 'credit' else row.credit,
                running_balance=reports.money(row.signed_balance.minor_units * normal_sign, currency),
                revision_number=summary['revision_number'] if summary else None,
                memo=memo, category_label=category, class_summary=class_label))
        net = db.raw.execute('SELECT bookflow_sum_int(debit_minor_units-credit_minor_units) FROM posting_lines WHERE account_id=?',
                             (account['id'],)).fetchone()[0]
        current = CurrentBalanceSnapshot(balance=reports.money(int(net or 0) * normal_sign, currency),
            generation_time=reports.now_iso(),
            audit_watermark=db.raw.execute('SELECT coalesce(max(seq),0) FROM audit_events').fetchone()[0])
        next_cursor = None
        if result.next_cursor:
            next_cursor = _encode(_Cursor(account_id=account['id'], company_id=company,
                query=query_hash, permissions=permissions, display=display, report_cursor=result.next_cursor), db)
        return RegisterQueryOutput(account=RegisterAccount(
                id=account['id'], label=_label(account, info), name=account['name'], full_name=account['full_name'],
                number=account['number'], type=account['type'], normal_balance=side,
                currency=currency, active=bool(account['active'])),
            metadata=result.metadata, count=len(rows), next_cursor=next_cursor, rows=rows,
            ledger_totals=result.totals, current_balance=current,
            totals=RegisterTotals(opening=reports.money(result.totals.opening.minor_units * normal_sign, currency),
                increases=result.totals.period_credits if side == 'credit' else result.totals.period_debits,
                decreases=result.totals.period_debits if side == 'credit' else result.totals.period_credits,
                closing=reports.money(result.totals.closing.minor_units * normal_sign, currency)))
