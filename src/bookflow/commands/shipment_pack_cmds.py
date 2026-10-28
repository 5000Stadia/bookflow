"""Draft shipments and packing (R181 increment 4): `shipment create`, `pack` and `cancel`.

The packing layer: off unless company setting `pick_and_pack_enabled` is on (E_FEATURE_DISABLED). The work lives in
``company/shipment_drafts.py`` and ``company/shipment_packing.py``.
"""
from bookflow.company import shipment_drafts
from bookflow.company.shipment_models import ShipmentCancelInput, ShipmentCreateInput, ShipmentOutput, ShipmentPackInput
from bookflow.core.registry import command

_OFF = 'E_FEATURE_DISABLED'
_CREATE = (
    'Make a draft shipment to pack before it ships: from an order (what `shipment post` would propose), its `lines`, '
    'or a `pick_list` (what was picked). Moves and posts nothing; its units are held from other shipments of the '
    'order. `packages` boxes it now. Ship it with `shipment post --shipment <number>`. Packing layer: off unless '
    'pick_and_pack_enabled (E_FEATURE_DISABLED). Example: {"sales_order": "1001"}.'
)
_PACK = (
    'Pack a draft or a shipped shipment; posts nothing. Either the whole packing (`packages`: box, package_type, '
    'weight, tare, dimensions, tracking_number, contents by line, item or serial_numbers), or a move: `unit` (a '
    'serial number, lot or item code) or `line`, `quantity` (default 1) into `box` N, made if new; box 0 unboxes. '
    'With no unit, the facts set box N. A box weighs its contents (item unit weight) plus tare, or its measured '
    'weight. Example: {"shipment": "1004", "unit": "WH-1001", "box": 2}.'
)
_CANCEL = 'End a draft shipment: it holds nothing any more and never ships. Packing layer.'


def _planner(operation):
    def planner(inp, ctx, s):
        return shipment_drafts.prepare(s, ctx, inp, operation)
    planner.__name__ = planner.__qualname__ = f'plan_shipment_{operation}'
    return planner


def _write(name, description, inp, operation, errors, positional=('shipment',)):
    cmd = command(name, scope='company', description=description, input_model=inp, output_model=ShipmentOutput,
                  writes={'company'}, required_role='standard', capability='item', accepts_idempotency_key=True,
                  positional=list(positional), error_codes=errors)(_planner(operation))
    cmd.applier(shipment_drafts.apply)
    cmd.resource_requirements = (('customer-work', 'member'),)
    return cmd


plan_shipment_create = _write('shipment create', _CREATE, ShipmentCreateInput, 'create', [
    'E_RECORD_NOT_FOUND', 'E_INACTIVE_REFERENCE', 'E_VALIDATION', 'E_VALUE_RANGE', 'E_WORK_DEPENDENCY', _OFF],
    positional=())
plan_shipment_pack = _write('shipment pack', _PACK, ShipmentPackInput, 'pack', [
    'E_RECORD_NOT_FOUND', 'E_VALIDATION', 'E_VERSION_CONFLICT', _OFF])
plan_shipment_cancel = _write('shipment cancel', _CANCEL, ShipmentCancelInput, 'cancel', [
    'E_RECORD_NOT_FOUND', 'E_VALIDATION', 'E_VERSION_CONFLICT', _OFF])
