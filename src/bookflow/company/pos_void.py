"""Void only a POS-linked receipt through the ordinary receipt reversal core."""
from __future__ import annotations

from typing import Literal
import json

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict, Field

from bookflow.company import pos_layer, sales, schema as c
from bookflow.company.journal_models import _Version
from bookflow.company.sales_models import SalesReceiptVoidInput, Selector
from bookflow.core.errors import BookflowError
from bookflow.core.registry import Plan
from bookflow.core.models import WriteOutput


class PosSaleVoidInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    sale: Selector = Field(description='POS sale ID, ticket key, document ID, receipt number or INV:invoice-number.')
    expected_version: _Version = Field(description="The sale output's current version; a split invoice advances as its payments settle it.")


class PosSaleVoidOutput(WriteOutput):
    """Till facts only: no receipt snapshots, costs or accounting account references."""
    model_config = ConfigDict(extra='forbid')
    sale_id: str
    ticket: str
    id: str = Field(description='Posted sale document ID.')
    number: str = Field(description='Posted sale document number.')
    status: Literal['voided']
    version: int
    changed: bool


def _output(sale, receipt):
    return PosSaleVoidOutput(sale_id=sale['sale_id'], ticket=sale['ticket'],
        **{key: getattr(receipt, key) for key in ('id', 'number', 'status', 'version', 'changed', 'warnings')})


def authorize(inp, ctx, s):
    pos_layer.require_enabled(s)
    # Before catalog activation, POS seller capabilities require the standard role.
    pos_layer.require_seller(s, 'pos.void')


def resolve(s, selector):
    t, p, r = c.transactions, c.pos_sales, c.transaction_revisions
    # A split's printed identifier has an invoice namespace. Bare numbers keep
    # identifying sales receipts even when an invoice has the same sequence number.
    invoice_number = selector[4:] if selector.startswith('INV:') else None
    number = invoice_number if invoice_number is not None else selector
    kind = 'invoice' if invoice_number is not None else 'sales_receipt'
    numbered = sa.and_(p.c.document_type == kind, sa.or_(t.c.number == number,
        sa.exists(sa.select(1).where(r.c.transaction_id == t.c.id, r.c.revision_number == 1,
                                    r.c.number == number))))
    rows = s.company.conn.execute(sa.select(p.c.document_id, p.c.document_type, p.c.id.label('sale_id'), p.c.ticket).select_from(
        p.join(t, t.c.id == p.c.document_id)).where(
        p.c.document_type.in_(['sales_receipt', 'invoice']),
        sa.or_(p.c.id == selector, p.c.ticket == selector, t.c.id == selector, numbered,
            sa.and_(p.c.document_type == 'sales_receipt', sa.or_(t.c.number == selector,
                sa.exists(sa.select(1).where(r.c.transaction_id == t.c.id, r.c.revision_number == 1, r.c.number == selector)))))
        ).distinct().limit(2)).mappings().all()
    if not rows:
        raise BookflowError('E_RECORD_NOT_FOUND', details={'record_type': 'pos_sale', 'selector': selector, 'suggestions': []})
    if len(rows) != 1:
        raise BookflowError('E_VALIDATION', message='This selector names more than one POS sale; use its sale ID.',
                            details={'fields': [{'field': 'sale', 'problem': 'ambiguous selector; use the POS sale ID'}]})
    return dict(rows[0])


def _dependencies(s, document):
    from bookflow.company import deposit_dependencies
    try:
        deposit_dependencies.require_unclaimed(s, document)
    except BookflowError as error:
        if error.code != 'E_DEPOSIT_DEPENDENCY':
            raise
        # pos.void grants the identity of the deposit holding this POS sale, not the
        # deposit's other sources or accounting details. The general receipt helper
        # deliberately redacts those identities for callers without ledger.post.
        claim = deposit_dependencies.active_claim(s, document)
        if claim is None:
            raise
        deposit = claim['transaction_id']
        number = s.company.conn.execute(sa.select(c.transactions.c.number).where(
            c.transactions.c.id == deposit)).scalar_one()
        raise BookflowError('E_DEPOSIT_DEPENDENCY', message=(
            f'This sale is banked on deposit {number} ({deposit}) and cannot be voided. '
            'Ask the bookkeeper to resolve the deposit first.'),
            details={'source': document, 'deposit': deposit, 'deposit_number': number,
                     'next': 'Ask the bookkeeper to resolve this deposit before voiding the sale.'}) from None
    keys, members = c.reconciliation_keys, c.reconciliation_current_members
    held = s.company.conn.execute(sa.select(keys.c.id).join(members, members.c.key_id == keys.c.id).where(
        keys.c.transaction_id == document).limit(1)).scalar()
    if held:
        raise BookflowError('E_RECONCILIATION_DEPENDENCY', message=(
            'A finished bank reconciliation holds this sale. It cannot be voided at the till; ask the bookkeeper.'),
            details={'sales_receipt_id': document, 'reconciliation_key_id': held,
                     'next': 'Ask the bookkeeper to resolve the finished reconciliation.'})


def prepare(s, ctx, inp):
    pos_layer.require_enabled(s)
    sale = resolve(s, inp.sale)
    document = sale['document_id']
    returned = s.company.conn.execute(sa.select(c.pos_refunds).where(
        c.pos_refunds.c.sale_id == sale['sale_id']).limit(1)).mappings().first()
    if returned:
        facts = json.loads(returned['result_json'])
        name = (facts.get('refund') or facts['credit_memo'])['name']
        raise BookflowError('E_WORK_DEPENDENCY', message=(
            f'This sale has refund {name} ({returned["id"]}) and cannot be voided. '
            'Ask the bookkeeper to resolve the return.'), details={'refund_id': returned['id'], 'refund_number': name})
    if sale['document_type'] == 'invoice':
        return _prepare_split(s, ctx, inp, sale)
    receipt = SalesReceiptVoidInput(sales_receipt=document, expected_version=inp.expected_version)
    plan = sales.prepare(s, ctx, receipt, 'sales_receipt', 'void')
    if plan.preview.changed:
        _dependencies(s, document)
    return Plan(_output(sale, plan.preview), dict(pos_input=inp, sale=sale, inner=plan,
                                                 stock=plan.data.get('stock'), prospective=plan))


def apply(plan, ctx, s):
    fresh = prepare(s, ctx, plan.data['pos_input'])
    if fresh.data.get('split'):
        return _apply_split(fresh, ctx, s)
    applied = sales.persist_prepared(fresh.data['inner'], ctx, s, command_name='pos sale void')
    applied.output = _output(fresh.data['sale'], applied.output)
    return applied


def _prepare_split(s, ctx, inp, sale):
    from bookflow.company import journals, payment_queries
    from bookflow.core.errors import require_reason
    header = sales.resolve(s, sale['document_id'], 'invoice')
    from bookflow.company.sales_deletions import require_not_deleted
    require_not_deleted(s, header['id'])
    sales._version(s, header, inp.expected_version)
    require_reason(ctx.reason)
    if len(ctx.reason.strip()) > 140:
        raise BookflowError('E_VALIDATION', message='The reason must be at most 140 characters.')
    changed = header['status'] != 'voided'
    row = s.company.conn.execute(sa.select(c.pos_sales).where(c.pos_sales.c.id == sale['sale_id'])).mappings().one()
    ids = [part['payment_id'] for part in json.loads(row['tenders'])]
    if changed:
        revision = journals.revision(s, header)
        if revision['revision_number'] != 1:
            raise BookflowError('E_WORK_DEPENDENCY', message='This split sale was corrected after checkout. Ask the bookkeeper to resolve it.')
        journals.open_dates(s, [revision['date']])
        _dependencies(s, header['id'])
        claims = c.credit_source_claims
        reverse = claims.alias('released_pos_claim')
        returned = s.company.conn.execute(sa.select(claims.c.id).where(
            claims.c.source_transaction_id == header['id'], claims.c.kind == 'claim',
            ~sa.exists(sa.select(reverse.c.id).where(reverse.c.reverses_claim_id == claims.c.id))).limit(1)).first()
        if returned:
            raise BookflowError('E_WORK_DEPENDENCY', message='A credit memo returns this sale. Ask the bookkeeper to resolve the return.')
        for payment_id in ids:
            _dependencies(s, payment_id)
        # Only the checkout graph belongs to pos.void. A bookkeeper's later allocations
        # or corrections must never be reversed as a side effect of voiding this sale.
        from bookflow.company.payment_authority import pos_scope
        with pos_scope(s, [header['id'], *ids], 'pos.void'):
            apps = payment_queries.active_applications(s, invoice=header['id'])
            if any(app['paying_transaction_id'] not in ids for app in apps):
                raise BookflowError('E_WORK_DEPENDENCY', message='Other payments now settle this sale. Ask the bookkeeper to resolve them.')
            for payment_id in ids:
                facts = payment_queries.payment_facts(s, payment_id, write=True)
                journals.open_dates(s, [facts['revision']['date'], *(app['effective_date'] for app in facts['applications'])])
                if facts['revision']['revision_number'] != 1 or facts['header']['version'] != 1 or facts['header']['status'] != 'posted' or facts['consumptions'] or any(
                        app['paid_transaction_id'] != header['id'] for app in facts['applications']):
                    raise BookflowError('E_WORK_DEPENDENCY', message='A payment for this sale has changed. Ask the bookkeeper to resolve it.')
    out = PosSaleVoidOutput(sale_id=sale['sale_id'], ticket=sale['ticket'], id=header['id'], number=header['number'],
        status='voided', version=header['version'] + (len(ids) + 1 if changed else 0), changed=changed)
    return Plan(out, dict(split=True, sale=sale, pos_input=inp, payment_ids=ids))


def _apply_split(plan, ctx, s):
    from bookflow.company import payments, payment_queries
    from bookflow.company.payment_models import PaymentVoidInput, PaymentUnapplyInput
    from bookflow.company.sales_models import InvoiceVoidInput
    from bookflow.company.payment_authority import pos_scope
    from bookflow.core.registry import Applied
    if not plan.preview.changed:
        return Applied(plan.preview, [], 'no change')
    invoice = plan.preview.id
    with pos_scope(s, [invoice, *plan.data['payment_ids']], 'pos.void'):
        for payment in plan.data['payment_ids']:
            header = sales.resolve(s, payment, 'payment')
            apps = payment_queries.active_applications(s, payment=payment)
            if apps:
                unapply = PaymentUnapplyInput(payment=payment, expected_version=header['version'],
                    applications=[dict(application_id=app['id'], invoice_expected_version=sales.resolve(s, invoice, 'invoice')['version']) for app in apps],
                    operation_key=f"pos-void:{plan.preview.sale_id}:{payment}:unapply")
                payments.apply(payments.prepare(s, ctx, unapply, 'unapply'), ctx, s, command_name='pos sale void')
            header = sales.resolve(s, payment, 'payment')
            void = PaymentVoidInput(payment=payment, expected_version=header['version'],
                operation_key=f"pos-void:{plan.preview.sale_id}:{payment}:void")
            payments.apply(payments.prepare(s, ctx, void, 'void'), ctx, s, command_name='pos sale void')
        header = sales.resolve(s, invoice, 'invoice')
        inner = sales.prepare(s, ctx, InvoiceVoidInput(invoice=invoice, expected_version=header['version']), 'invoice', 'void')
        applied = sales.persist_prepared(inner, ctx, s, command_name='pos sale void')
    applied.output = _output(plan.data['sale'], applied.output)
    return applied
