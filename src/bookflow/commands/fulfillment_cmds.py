"""The fulfillment worksheet and the shipments report (R181 increment 5): `report order-fulfillment`, `report shipments`.

The work lives in ``company/fulfillment.py``.
"""
from bookflow.company import fulfillment
from bookflow.company.fulfillment_models import (
    OrderFulfillmentInput, OrderFulfillmentOutput, ShipmentsReportInput, ShipmentsReportOutput,
)
from bookflow.core.registry import Plan, command

_WORKSHEET = (
    'The fulfillment worksheet: every open sales order with stock to ship or shipped and not invoiced -- its '
    'fulfillment status (to_pick, picking, picked, packed, partly_shipped, shipped) beside its billing progress, to '
    'ship, can ship now, picked, packed, shipped not invoiced, shippable amount, promised ship date and days late, per '
    'line and per site. Can ship now is exactly what `shipment post --sales-order <n>` would ship now (at `site`, or '
    'the site it picks); an order a pick list holds ships from the list and says so. Filters: site, customer, status '
    '(comma-separated), complete_only, can_ship, shipped_not_invoiced, newly_shippable (a date: can ship more now than '
    'with that day\'s stock); sort date, shippable_amount or customer. Example: {"status": "to_pick,picking"}.'
)
_SHIPMENTS = (
    'Posted shipments by ship date (date_to default today): order, customer, site, ship method, carrier, tracking '
    'numbers, boxes and weights, freight to bill. Filters: date_from, date_to, customer, site, ship_method.'
)


@command('report order-fulfillment', scope='company', description=_WORKSHEET, input_model=OrderFulfillmentInput,
         output_model=OrderFulfillmentOutput, required_role='member', capability='reports',
         error_codes=['E_RECORD_NOT_FOUND', 'E_VALIDATION', 'E_QUERY_STALE'])
def plan_order_fulfillment(inp, ctx, s):
    return Plan(fulfillment.worksheet(s, inp, principal_id=ctx.on_behalf_of))


@command('report shipments', scope='company', description=_SHIPMENTS, input_model=ShipmentsReportInput,
         output_model=ShipmentsReportOutput, required_role='member', capability='reports',
         error_codes=['E_RECORD_NOT_FOUND', 'E_VALIDATION', 'E_QUERY_STALE'])
def plan_shipments_report(inp, ctx, s):
    return Plan(fulfillment.shipments_report(s, inp, principal_id=ctx.on_behalf_of))
