"""Receipt facts from the posted sale; no ledger or customer-account data is exposed."""
import json
from decimal import Decimal
import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict, Field
from bookflow.company import schema as c, pos_layer, pos_void, pos_sales, sales
from bookflow.company.sales_models import Selector
from bookflow.company.pos_sale_models import PosSaleOutput
from bookflow.core import unit_price
from bookflow.core.money import Money

class ReceiptInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    sale: Selector = Field(description='POS sale ID, ticket key, receipt ID or receipt number.')
    reprint: bool = Field(False, description='Mark this copy Reprint without changing the sale.')

class ReceiptLine(BaseModel):
    name: str
    quantity: str | None
    price: str | None
    amount: str

class ReceiptTax(BaseModel):
    name: str
    rate: str
    amount: str

class ReceiptOutput(BaseModel):
    sale: PosSaleOutput
    number: str = Field(description='The original posted receipt number printed on this copy and its barcode.')
    status: str
    reprint: bool
    width: int
    company_name: str
    address: list[str]
    phone: str | None
    clerk: str
    customer: str | None
    lines: list[ReceiptLine]
    subtotal: str
    discounts: str
    taxes: list[ReceiptTax]
    total: str
    message: str | None

def authorize(inp, ctx, s):
    pos_layer.require_enabled(s)
    pos_layer.require_seller(s)

def read(s, inp):
    from bookflow.hub.users import user_names
    from bookflow.documents.model import _issuer_party
    found = pos_void.resolve(s, inp.sale)
    row = dict(s.company.conn.execute(sa.select(c.pos_sales).where(c.pos_sales.c.id == found['sale_id'])).mappings().one())
    header = s.company.conn.execute(sa.select(c.transactions).where(c.transactions.c.id == row['document_id'])).mappings().one()
    # The original posted revision survives voids and subsequent receipt edits.
    revision = s.company.conn.execute(sa.select(c.transaction_revisions).where(
        c.transaction_revisions.c.transaction_id == row['document_id']).order_by(c.transaction_revisions.c.revision_number)).mappings().first()
    profile = sales.profile_row(s, revision)
    facts = json.loads(profile['profile_snapshot'])
    currency = revision['currency']
    money = lambda n: Money(n, currency).to_dict()['amount']
    lines, discounts = [], 0
    for line in sales.saved_lines(s, revision):
        snapshot = json.loads(line['item_snapshot'])
        shown = sales.shown_amount(line, currency)
        kind = shown.get('line_kind', 'item')
        amount = shown.get('amount', Money(line['net_minor_units'], currency).to_dict())
        if kind == 'discount':
            discounts += amount['minor_units']
        lines.append(ReceiptLine(name=snapshot['item']['label'],
            quantity=str(Decimal(line['quantity_microunits']) / 1000000) if line['quantity_microunits'] is not None else None,
            price=unit_price.to_dict(unit_price.row_units(line, 'unit_price'), currency)['amount'] if line['unit_price_minor_units'] is not None else None,
            amount=amount['amount']))
    taxes = {}
    for tax in s.company.conn.execute(sa.select(c.sales_tax_components).where(c.sales_tax_components.c.revision_id == revision['id'])).mappings():
        snap = json.loads(tax['component_snapshot'])
        key = (snap['tax_item']['label'], str(Decimal(tax['rate_percent_millionths']) / 1000000))
        taxes[key] = taxes.get(key, 0) + tax['tax_minor_units']
    info = dict(s.company.conn.execute(sa.select(c.company_info)).mappings().one())
    issuer = _issuer_party(json.loads(revision['issuer_snapshot']), info)
    reg = pos_layer.resolve(s, row['register_id'])
    return ReceiptOutput(sale=pos_sales.sale_output(s, row), number=('INV:' if row['document_type'] == 'invoice' else '') + revision['number'], status=header['status'],
        reprint=inp.reprint, width=reg['receipt_width'], company_name=issuer.name or '', address=list(issuer.lines),
        phone=info.get('phone'), clerk=user_names(s, {row['created_by']}).get(row['created_by'], row['created_by']),
        customer=facts['customer']['label'] if row['customer_named'] else None, lines=lines,
        subtotal=money(profile['subtotal_minor_units'] - discounts), discounts=money(discounts),
        taxes=[ReceiptTax(name=k[0], rate=k[1], amount=money(v)) for k, v in taxes.items()],
        total=money(revision['total_minor_units']), message=reg['receipt_message'])
