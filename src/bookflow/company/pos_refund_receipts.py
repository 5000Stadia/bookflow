"""Printable refund facts, restricted to the till's captured return documents."""
import json
from decimal import Decimal
from typing import Literal

import sqlalchemy as sa
from pydantic import BaseModel, Field
from bookflow.company.sales_models import Selector

from bookflow.company import pos_layer, schema as c
from bookflow.company.pos_receipts import ReceiptOutput, ReceiptInput, ReceiptLine, ReceiptTax
from bookflow.company.pos_sale_models import TenderOutput
from bookflow.company.pos_models import Named
from bookflow.company.items import MoneyOutput
from bookflow.core.errors import BookflowError
from bookflow.core.money import Money


class RefundReceiptInput(ReceiptInput):
    sale: Selector = Field(description='POS refund ID, operation key, credit memo ID or original credit memo number.')


class RefundReceiptSummary(BaseModel):
    id: str
    pos_register: Named
    created_at: str
    total: MoneyOutput
    tenders: list[TenderOutput]
    change: MoneyOutput = Field(description='Zero on a refund receipt; no change is due.')


class RefundReceiptOutput(ReceiptOutput):
    sale: RefundReceiptSummary = Field(description='Captured refund identity and payment, shared with the thermal renderer.')
    kind: Literal['refund'] = 'refund'
    store_credit: bool


def read(s, inp: RefundReceiptInput):
    from bookflow.hub.users import user_names
    from bookflow.documents.model import _issuer_party
    r, t, v = c.pos_refunds, c.transactions, c.transaction_revisions
    found = s.company.conn.execute(sa.select(r).join(t, t.c.id == r.c.credit_memo_id).where(sa.or_(
        r.c.id == inp.sale, r.c.operation_key == inp.sale, r.c.credit_memo_id == inp.sale,
        t.c.number == inp.sale, sa.exists(sa.select(1).where(v.c.transaction_id == t.c.id,
            v.c.revision_number == 1, v.c.number == inp.sale)))).distinct().limit(2)).mappings().all()
    if not found:
        raise BookflowError('E_RECORD_NOT_FOUND', details={'record_type':'pos_refund','selector':inp.sale,'suggestions':[]})
    if len(found) != 1:
        raise BookflowError('E_VALIDATION', message='This number names more than one refund; use its refund ID.',
            details={'fields':[{'field':'sale','problem':'ambiguous refund selector; use its refund ID'}]})
    row = found[0]
    facts = json.loads(row['result_json'])
    revision = s.company.conn.execute(sa.select(v).where(v.c.transaction_id == row['credit_memo_id'],
        v.c.revision_number == 1)).mappings().one()
    header = s.company.conn.execute(sa.select(t).where(t.c.id == row['credit_memo_id'])).mappings().one()
    info = dict(s.company.conn.execute(sa.select(c.company_info)).mappings().one())
    issuer = _issuer_party(json.loads(revision['issuer_snapshot']), info)
    register = pos_layer.resolve(s, row['register_id'])
    currency = facts['total']['currency']
    money = lambda n: Money(n, currency).to_dict()['amount']
    taxes = {}
    for tax in s.company.conn.execute(sa.select(c.credit_tax_components).where(
            c.credit_tax_components.c.revision_id == revision['id'])).mappings():
        snapshot = json.loads(tax['component_snapshot'])
        label = snapshot['tax_item']['label']
        rate = str(Decimal(tax['rate_percent_millionths']) / 1000000)
        taxes[(label, rate)] = taxes.get((label, rate), 0) + tax['tax_minor_units']
    store_credit = facts['settlement'] == 'store_credit'
    tenders = [] if store_credit else [
        dict(payment_method=p['payment_method'], amount=p['amount'], reference=p.get('reference'))
        for p in facts.get('tenders') or [dict(payment_method=facts['payment_method'], amount=facts['total'], reference=facts.get('reference'))]]
    sale = RefundReceiptSummary(id=row['id'], pos_register=facts['pos_register'],
        created_at=row['created_at'], total=facts['total'],
        change=Money(0,currency).to_dict(), tenders=tenders)
    return RefundReceiptOutput(sale=sale, number=revision['number'], status=header['status'], reprint=inp.reprint,
        width=register['receipt_width'],company_name=issuer.name or '',address=list(issuer.lines),phone=info.get('phone'),
        clerk=user_names(s,{row['created_by']}).get(row['created_by'],row['created_by']),
        customer=facts['customer']['name'] if facts['customer'] else None,
        lines=[ReceiptLine(name=line['item_name'],quantity=line['quantity'],
            price=line['unit_price']['amount'] if line.get('unit_price') else None,amount=line['net']['amount']) for line in facts['lines']],
        subtotal=facts['subtotal']['amount'], discounts=money(0),
        taxes=[ReceiptTax(name=label,rate=rate,amount=money(amount)) for (label,rate),amount in taxes.items()],
        total=facts['total']['amount'],message=register['receipt_message'],store_credit=store_credit)
