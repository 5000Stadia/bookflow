"""Serial and lot numbers (R104 increment 4): `inventory assign`, `lot` and `serial-number` show, list and
update, and the Serial/Lot stock, history and trace reports.

The work lives in ``company/serial_lot_service.py``; how a stock line's quantity splits into numbers lives in
``company/serial_lots.py``.
"""
from bookflow.company import serial_lot_models as m
from bookflow.company import serial_lot_service as service
from bookflow.core.registry import Plan, command

_ERRORS = ['E_VALIDATION', 'E_RECORD_NOT_FOUND', 'E_INACTIVE_REFERENCE', 'E_PERIOD_CLOSED']


def plan_inventory_assign(inp, ctx, s) -> Plan:
    return service.plan_assign(s, ctx, inp)


def plan_lot_show(inp, ctx, s) -> Plan:
    return service.show('lot')(s, ctx, inp)


def plan_serial_show(inp, ctx, s) -> Plan:
    return service.show('serial')(s, ctx, inp)


def plan_lot_list(inp, ctx, s) -> Plan:
    return service.listing('lot')(s, ctx, inp)


def plan_serial_list(inp, ctx, s) -> Plan:
    return service.listing('serial')(s, ctx, inp)


def plan_lot_update(inp, ctx, s) -> Plan:
    return service.plan_update('lot')(s, ctx, inp)


def plan_serial_update(inp, ctx, s) -> Plan:
    return service.plan_update('serial')(s, ctx, inp)


def plan_serial_lot_stock(inp, ctx, s) -> Plan:
    return service.stock_report(s, ctx, inp)


def plan_serial_lot_history(inp, ctx, s) -> Plan:
    return service.history_report(s, ctx, inp)


def plan_lot_trace(inp, ctx, s) -> Plan:
    return service.trace_report(s, ctx, inp)


def _write(name, description, inp, out, planner, applier, *, positional=(), errors=_ERRORS):
    cmd = command(name, scope='company', description=description, input_model=inp, output_model=out,
                  writes={'company'}, required_role='standard', capability='item', accepts_idempotency_key=True,
                  positional=list(positional), error_codes=list(errors))(planner)
    cmd.applier(applier)
    return cmd


def _read(name, description, inp, out, planner, *, capability='item', positional=()):
    return command(name, scope='company', description=description, input_model=inp, output_model=out,
                   writes=set(), required_role='member', capability=capability, positional=list(positional),
                   error_codes=['E_VALIDATION', 'E_RECORD_NOT_FOUND'])(planner)


assign = _write(
    'inventory assign',
    'Number tracked units after the fact; posts nothing. Moves units at one place between unassigned and serial '
    'numbers or a lot: units on hand on a date, or -- with document -- the units one document line moved, dated '
    'with that line ("which water heater did that sale take"). An agent that does not know a number leaves it out '
    'of the document and assigns it here later; never make one up. direction unassign takes numbers off units. '
    'Refused on or before the closing date. Example: `bookflow inventory assign --item "Water Heater" '
    '--document "invoice 1001" --serial-numbers \'["WH-7"]\' --reason "Serial from the job sheet"`.',
    m.AssignInput, m.AssignOutput, plan_inventory_assign, service.apply_assign)
lot_show = _read('lot show', 'Where a lot is now: quantity on hand by site, expiry, and the document that last moved it. '
                 'Example: `bookflow lot show L-2207 --item Sealant`.', m.NumberShowInput, m.NumberOutput, plan_lot_show,
                 positional=['number'])
serial_show = _read('serial-number show', 'Where a serial number is now: the site holding it (on hand 1), or 0 '
                    'when it has left, or -1 when it was sold before its receipt was recorded; and the document that '
                    'last moved it ("which customer got WH-1": see `report serial-lot-history`). Example: '
                    '`bookflow serial-number show WH-1`.', m.NumberShowInput, m.NumberOutput, plan_serial_show,
                    positional=['number'])
lot_list = _read('lot list', 'The lots (batches) on file by item, earliest expiry first, with where each is; in_stock, site and '
                 'expiring_by narrow it (lots expiring by a date).', m.NumberListInput, m.NumberPageOutput, plan_lot_list)
serial_list = _read('serial-number list', 'The serial numbers on file by item, with where each is; in_stock and site '
                    'narrow it.', m.NumberListInput, m.NumberPageOutput, plan_serial_list)
lot_update = _write('lot update', "Change a lot's expiry date (or clear it) or its memo; quantities move only with "
                    'stock documents.', m.LotUpdateInput, m.NumberOutput, plan_lot_update, service.apply_update('lot'),
                    positional=['number'], errors=['E_VALIDATION', 'E_RECORD_NOT_FOUND', 'E_VERSION_CONFLICT'])
serial_update = _write('serial-number update', "Change a serial number's memo (a warranty note, say).",
                       m.SerialUpdateInput, m.NumberOutput, plan_serial_update, service.apply_update('serial'),
                       positional=['number'], errors=['E_VALIDATION', 'E_RECORD_NOT_FOUND', 'E_VERSION_CONFLICT'])
stock_report = _read(
    'report serial-lot-stock', 'Serial/Lot Numbers in Stock (also Quantity on Hand by Serial/Lot Number and Stock '
    'Status by Lot Numbers): each tracked item by number -- or unassigned -- site, quantity and expiry, as of as_of; '
    'item, site and expiring_by narrow it. Refuses when placements disagree with the stock ledger.',
    m.StockReportInput, m.StockReportOutput, plan_serial_lot_stock, capability='reports')
history_report = _read(
    'report serial-lot-history', 'Transaction List by Serial/Lot Number: every move of one number in the order stock '
    'is worked out, assignments included, with the document, customer or vendor, site, in, out and running balance. '
    '"Which customer got water heater WH-1?" is its last sale row. Example: '
    '`bookflow report serial-lot-history --number WH-1`.',
    m.HistoryInput, m.HistoryOutput, plan_serial_lot_history, capability='reports')
trace_report = _read(
    'report lot-trace', "A recall's answer for one lot (or serial number): its history, the vendors it came from, "
    'every customer who got it -- directly or built into an assembly -- what builds it went into, and what went into '
    'it when it is an assembly\'s own number. Example: `bookflow report lot-trace --number L-2207`.',
    m.HistoryInput, m.TraceOutput, plan_lot_trace, capability='reports')
