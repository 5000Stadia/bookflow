"""Void only a POS-linked receipt through the ordinary receipt reversal core."""
from __future__ import annotations

from typing import Literal

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
    sale: Selector = Field(description='POS sale ID, ticket key, sales receipt ID or receipt number.')
    expected_version: _Version = Field(description='The current sales receipt version; a newly completed sale is version 1.')


class PosSaleVoidOutput(WriteOutput):
    """Till facts only: no receipt snapshots, costs or accounting account references."""
    model_config = ConfigDict(extra='forbid')
    sale_id: str
    ticket: str
    id: str = Field(description='Sales receipt ID.')
    number: str = Field(description='Sales receipt number.')
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
    rows = s.company.conn.execute(sa.select(p.c.document_id, p.c.id.label('sale_id'), p.c.ticket).select_from(
        p.join(t, t.c.id == p.c.document_id)).where(
        p.c.document_type == 'sales_receipt',
        sa.or_(p.c.id == selector, p.c.ticket == selector, t.c.id == selector,
               t.c.number == selector,
               sa.exists(sa.select(1).where(r.c.transaction_id == t.c.id, r.c.revision_number == 1,
                                           r.c.number == selector)))).distinct().limit(2)).mappings().all()
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
    receipt = SalesReceiptVoidInput(sales_receipt=document, expected_version=inp.expected_version)
    plan = sales.prepare(s, ctx, receipt, 'sales_receipt', 'void')
    if plan.preview.changed:
        _dependencies(s, document)
    return Plan(_output(sale, plan.preview), dict(pos_input=inp, sale=sale, inner=plan,
                                                 stock=plan.data.get('stock'), prospective=plan))


def apply(plan, ctx, s):
    fresh = prepare(s, ctx, plan.data['pos_input'])
    applied = sales.persist_prepared(fresh.data['inner'], ctx, s, command_name='pos sale void')
    applied.output = _output(fresh.data['sale'], applied.output)
    return applied
