"""Shipments (R181 increment 1): ship an order before billing it, and bill the shipment later.

`shipment post` issues the order's units on the ship date and holds their cost in Shipped Not Invoiced;
`shipment invoice` (or `sales-order invoice`, which bills shipped units first) makes the invoice that moves
nothing and moves the cost to cost of goods sold. The bookkeeping lives in ``company/shipments.py``.
"""
from pydantic import BaseModel, ConfigDict, Field

from bookflow.company import shipment_returns, shipments
from bookflow.company.shipment_models import (
    ShipmentHistoryOutput, ShipmentInvoiceInput, ShipmentListOutput, ShipmentOutput, ShipmentPostInput,
    ShipmentQueryInput, ShipmentReturnInput, ShipmentReturnListOutput, ShipmentReturnOutput, ShipmentReturnQueryInput,
    ShipmentReturnSelector, ShipmentReturnVoidInput, ShipmentShowInput, ShipmentUpdateInput, ShipmentVoidInput,
    ShippedNotInvoicedInput, ShippedNotInvoicedOutput,
)
from bookflow.company.sales_outputs import SalesWriteOutput
from bookflow.core.registry import Plan, command

_POST = (
    'Ship a sales order before invoicing it: the units leave stock on the ship date (default today) and their cost '
    'waits in Shipped Not Invoiced, an asset, until the invoice moves it to cost of goods sold beside its income. '
    'Nothing reaches income or receivables. With no `lines` it ships every order line\'s quantity still to ship, up to '
    'what is available to the order (on hand less what other orders commit); the rest stays backordered on the order. '
    '`lines` name an order line (`order_line`, its line_id) or an `item`, a `quantity` in the order line\'s unit, and '
    'optionally `bin`, `lot` and `serial_numbers`; shipping past what is on hand is allowed with a warning, costed '
    'provisionally until the receipt arrives. Carrier and tracking numbers are recorded as text. The dry run shows '
    'each line\'s cost and what stays to ship. The result gives the next command, `shipment invoice <number>`. '
    'Example: {"sales_order": "1001"}.'
)
_UPDATE = (
    'Correct a shipment: its date, count position, lines (replacing them), ship method, carrier, tracking numbers or '
    'memo. A change of quantity, place or date reposts the stock at the ship date; a change of ship method, carrier, '
    'tracking or memo alone writes a new revision and reposts nothing. Refused below what invoices billed, later than '
    'an invoice billing it, or (for a repost) while a return gives back its units -- to take units off a shipment, '
    'record a return with `shipment return`.'
)
_VOID = ('Void a shipment with a reason: its units go back where they left from at the ship date and the order has them '
         'to ship again. Refused while an invoice bills it or a return gives back its units, naming them; void or '
         'correct those first.')
_SHOW = ('Show one shipment: the order and customer, ship date, site, each line with its places, numbers, cost, '
         'what invoices billed and what still waits in Shipped Not Invoiced, the invoices, and every revision.')
_QUERY = 'List shipments, newest first, by order, customer, status or date; `not_invoiced` keeps those not fully billed.'
_HISTORY = 'Every revision of one shipment: version, status, ship date, reason and who wrote it.'
_INVOICE = (
    'Invoice one shipment: its units not yet invoiced, at the order\'s prices, taxed as an ordinary invoice of its own '
    'date (default today, never before the ship date). The invoice moves no stock; it moves the units\' cost from '
    'Shipped Not Invoiced to cost of goods sold. The same as `sales-order invoice <order> --shipments <shipment>`.'
)
_REPORT = ('Shipped Not Invoiced on a date: by customer, order, shipment and line, the quantity and cost waiting for an '
           'invoice. Its total equals the Shipped Not Invoiced account, or it refuses.')

_WRITE_ERRORS = ['E_RECORD_NOT_FOUND', 'E_INACTIVE_REFERENCE', 'E_VALIDATION', 'E_VALUE_RANGE', 'E_PERIOD_CLOSED',
                 'E_VERSION_CONFLICT', 'E_WORK_DEPENDENCY', 'E_REASON_REQUIRED']


class ShipmentHistoryList(BaseModel):
    model_config = ConfigDict(extra='forbid')
    shipment_id: str
    number: str
    history: list[ShipmentHistoryOutput] = Field(default_factory=list)


def _planner(operation):
    def planner(inp, ctx, s):
        return shipments.prepare(s, ctx, inp, operation)
    planner.__name__ = planner.__qualname__ = f'plan_shipment_{operation}'
    return planner


plan_shipment_post = command(
    'shipment post', scope='company', description=_POST, input_model=ShipmentPostInput, output_model=ShipmentOutput,
    writes={'company'}, required_role='standard', capability='ledger.post', accepts_idempotency_key=True,
    error_codes=_WRITE_ERRORS[:-1])(_planner('post'))
plan_shipment_update = command(
    'shipment update', scope='company', description=_UPDATE, input_model=ShipmentUpdateInput,
    output_model=ShipmentOutput, writes={'company'}, required_role='standard', capability='ledger.post',
    accepts_idempotency_key=True, positional=['shipment'], error_codes=_WRITE_ERRORS[:-1],
    version_source=('shipment show', 'shipment', 'version'))(_planner('update'))
plan_shipment_void = command(
    'shipment void', scope='company', description=_VOID, input_model=ShipmentVoidInput, output_model=ShipmentOutput,
    writes={'company'}, required_role='standard', capability='ledger.post', accepts_idempotency_key=True,
    positional=['shipment'], error_codes=_WRITE_ERRORS)(_planner('void'))


@command('shipment show', scope='company', description=_SHOW, input_model=ShipmentShowInput,
         output_model=ShipmentOutput, required_role='member', capability='item', positional=['shipment'],
         error_codes=['E_RECORD_NOT_FOUND'])
def plan_shipment_show(inp, ctx, s):
    return Plan(shipments.show(s, inp))


@command('shipment query', scope='company', description=_QUERY, input_model=ShipmentQueryInput,
         output_model=ShipmentListOutput, required_role='member', capability='item',
         error_codes=['E_RECORD_NOT_FOUND', 'E_VALIDATION'])
def plan_shipment_query(inp, ctx, s):
    return Plan(shipments.query(s, inp))


@command('shipment history', scope='company', description=_HISTORY, input_model=ShipmentShowInput,
         output_model=ShipmentHistoryList, required_role='member', capability='item', positional=['shipment'],
         error_codes=['E_RECORD_NOT_FOUND'])
def plan_shipment_history(inp, ctx, s):
    shown = shipments.show(s, inp)
    return Plan(ShipmentHistoryList(shipment_id=shown.id, number=shown.number, history=shown.history))


def _order_input(s, inp):
    from bookflow.company import billing_models, work
    from bookflow.core import clock
    header = shipments.resolve(s, inp.shipment)
    order = work.resolve(s, header['sales_order_id'], 'sales_order')
    billed = len({row['transaction_id'] for row in s.company.conn.execute(
        shipments.sa.select(shipments.c.shipment_billings.c.transaction_id).where(
            shipments.c.shipment_billings.c.shipment_id == header['id'])).mappings()})
    body = dict(sales_order=order['id'], expected_version=order['version'],
                conversion_key=inp.conversion_key or f'shipment {header["id"]} invoice {billed + 1}',
                date=inp.date or clock.today_iso(), shipments=[header['id']], apply_prepayment=inp.apply_prepayment)
    for name in ('number', 'memo', 'ar_account', 'terms'):
        if getattr(inp, name) is not None:
            body[name] = getattr(inp, name)
    return billing_models.SalesOrderInvoiceInput.model_validate(body)


def _inner():
    from bookflow.core import registry
    return registry.get('sales-order invoice')


@command('shipment invoice', scope='company', description=_INVOICE, input_model=ShipmentInvoiceInput,
         output_model=SalesWriteOutput, writes={'company'}, required_role='standard', capability='ledger.post',
         accepts_idempotency_key=True, positional=['shipment'],
         authorization='ledger.post and customer-work standard role',
         error_codes=['E_RECORD_NOT_FOUND', 'E_VERSION_CONFLICT', 'E_WORK_DEPENDENCY', 'E_CONVERSION_KEY_REUSED',
                      'E_PERIOD_CLOSED', 'E_DUPLICATE_NUMBER', 'E_INACTIVE_REFERENCE', 'E_VALUE_RANGE',
                      'E_AMOUNT_PRECISION', 'E_PREVIEW_STALE', 'E_FEATURE_DISABLED', 'E_VALIDATION'])
def plan_shipment_invoice(inp, ctx, s):
    order_input = _order_input(s, inp)
    plan = _inner().plan(order_input, ctx, s)
    return Plan(plan.preview, dict(plan.data, shipment_order_input=order_input))


plan_shipment_invoice.resource_requirements = (('customer-work', 'standard'),)
plan_shipment_invoice.ledger = True


@plan_shipment_invoice.applier
def _apply_shipment_invoice(plan, ctx, s):
    return _inner().apply(plan, ctx, s)


@command('report shipped-not-invoiced', scope='company', description=_REPORT, input_model=ShippedNotInvoicedInput,
         output_model=ShippedNotInvoicedOutput, required_role='member', capability='reports',
         error_codes=['E_RECORD_NOT_FOUND', 'E_VALIDATION'])
def plan_shipped_not_invoiced(inp, ctx, s):
    return Plan(shipments.report(s, inp))


for _write in (plan_shipment_post, plan_shipment_update, plan_shipment_void):
    _write.ledger = True
    _write.resource_requirements = (('customer-work', 'standard'),)
    _write.applier(shipments.apply)


# ---------------------------------------------------------------- returns (R181 increment 2)

_RETURN = (
    'Record units a customer sent back from a shipment before they were invoiced: they go back into stock on the '
    'return date (default today, never before the ship date), to the bin the shipment took them from unless a line '
    'names one, worth their share of the shipment line\'s cost, which leaves Shipped Not Invoiced. `reopen` (default '
    'true) leaves them to ship again on the order; false cancels them on it. `lines` name a shipment line (its '
    'position or line_identity) or an item, a quantity in the item\'s base unit, and serial numbers or a lot the '
    'shipment took; omitted, every unit still out and not invoiced comes back. Units an invoice billed come back on '
    'a credit memo naming the invoice line instead. Example: {"shipment": "1001", "lines": [{"shipment_line": "1", '
    '"quantity": "1"}]}.'
)
_RETURN_VOID = ('Void a shipment return with a reason: its units leave stock again at the return date and wait in '
                'Shipped Not Invoiced; a return that cancelled them on the order un-cancels them. Refused when the '
                'order no longer has them to ship (shipped again or billed since).')
_RETURN_SHOW = ('Show one shipment return: the shipment, order and customer, its date, whether the units are to ship '
                'again, each line with its place, numbers and value, and its history.')
_RETURN_QUERY = 'List shipment returns, newest first, by shipment, customer, status or date.'


def _return_planner(operation):
    def planner(inp, ctx, s):
        return shipment_returns.prepare(s, ctx, inp, operation)
    planner.__name__ = planner.__qualname__ = f'plan_shipment_{"return" if operation == "return" else "return_void"}'
    return planner


plan_shipment_return = command(
    'shipment return', scope='company', description=_RETURN, input_model=ShipmentReturnInput,
    output_model=ShipmentReturnOutput, writes={'company'}, required_role='standard', capability='ledger.post',
    accepts_idempotency_key=True, positional=['shipment'], error_codes=_WRITE_ERRORS)(_return_planner('return'))
plan_shipment_return_void = command(
    'shipment-return void', scope='company', description=_RETURN_VOID, input_model=ShipmentReturnVoidInput,
    output_model=ShipmentReturnOutput, writes={'company'}, required_role='standard', capability='ledger.post',
    accepts_idempotency_key=True, positional=['shipment_return'], error_codes=_WRITE_ERRORS)(_return_planner('void'))


@command('shipment-return show', scope='company', description=_RETURN_SHOW, input_model=ShipmentReturnSelector,
         output_model=ShipmentReturnOutput, required_role='member', capability='item', positional=['shipment_return'],
         error_codes=['E_RECORD_NOT_FOUND'])
def plan_shipment_return_show(inp, ctx, s):
    return Plan(shipment_returns.show(s, inp))


@command('shipment-return query', scope='company', description=_RETURN_QUERY, input_model=ShipmentReturnQueryInput,
         output_model=ShipmentReturnListOutput, required_role='member', capability='item',
         error_codes=['E_RECORD_NOT_FOUND', 'E_VALIDATION'])
def plan_shipment_return_query(inp, ctx, s):
    return Plan(shipment_returns.query(s, inp))


for _write in (plan_shipment_return, plan_shipment_return_void):
    _write.ledger = True
    _write.resource_requirements = (('customer-work', 'standard'),)
    _write.applier(shipment_returns.apply)
