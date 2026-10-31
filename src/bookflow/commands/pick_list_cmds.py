"""Pick lists (R181 increment 3): which units of which open orders to take from which bins, confirmed by scanning,
then shipped with `shipment post --pick-list`. An optional layer on shipping, off until an admin turns on
`pick_and_pack_enabled` in company settings. The work lives in ``company/pick_lists.py``.
"""
from bookflow.company import pick_lists
from bookflow.company import pick_list_models as m
from bookflow.core.registry import Plan, command

_VERSION = ('pick-list show', 'pick_list', 'version')
_OFF = 'E_FEATURE_DISABLED'

_CREATE = (
    'Make a pick list at one site: for each open order line, what it has to ship and the site can give it, less '
    'what other open lists hold, from the item\'s default bin, then other bins (no bin last), lots earliest-expiring '
    'first, in pick order (bin, item, order date). Name `orders` (one list can be a wave) or `ready: true` for every '
    'order that can ship; `complete_only` keeps orders that ship whole. What the site cannot give is a short line '
    'naming other sites. Moves nothing. Off unless company setting pick_and_pack_enabled is on (E_FEATURE_DISABLED). '
    'Example: {"site": "Warehouse", "ready": true}.'
)
_ENTER = (
    'Confirm picks on an open list: 1-500 entries, each a `line` (position), an `item` (with `sales_order` when the '
    'list holds it for two orders) or a scanned `code`; `bin`, `lot`, `serial_number`, `quantity` (base units, '
    'default 1) and mode add or set. Entries append under their own idempotency key, so two pickers can pick one '
    'list at once. Picking past a line warns; a serial number the books hold elsewhere warns. With a staging bin at '
    'the site, picked units move there. Example: {"pick_list": "P-1", "entries": [{"code": "WIDGET", "bin": "A1"}]}.'
)
_CLOSE = ('End picking on an open list. What was not picked stays to ship on the order; ship the picks with '
          '`shipment post --pick-list`.')
_CANCEL = ('Cancel a list with a reason: it holds nothing any more, and units it staged and did not ship go back to '
           'their bins.')
_SHOW = 'Show a pick list: each line with its bin, lot, serial numbers in the bin, to pick and picked, and its orders.'
_QUERY = 'List pick lists, newest first, by status, site or order.'
_ERRORS = ['E_VALIDATION', 'E_RECORD_NOT_FOUND', 'E_INACTIVE_REFERENCE', _OFF]


def plan_pick_list_create(inp, ctx, s) -> Plan:
    return pick_lists.plan_create(s, ctx, inp)


def plan_pick_list_enter(inp, ctx, s) -> Plan:
    return pick_lists.plan_enter(s, ctx, inp)


def plan_pick_list_close(inp, ctx, s) -> Plan:
    return pick_lists.plan_end('close')(s, ctx, inp)


def plan_pick_list_cancel(inp, ctx, s) -> Plan:
    return pick_lists.plan_end('cancel')(s, ctx, inp)


def plan_pick_list_show(inp, ctx, s) -> Plan:
    return Plan(pick_lists.show(s, inp))


def plan_pick_list_query(inp, ctx, s) -> Plan:
    return Plan(pick_lists.query(s, inp))


def _write(name, description, inp, planner, applier, *, errors, positional=('pick_list',), version=None):
    cmd = command(name, scope='company', description=description, input_model=inp, output_model=m.PickListOutput,
                  writes={'company'}, required_role='standard', capability='item', accepts_idempotency_key=True,
                  positional=list(positional), version_source=version, error_codes=errors)(planner)
    cmd.applier(applier)
    cmd.resource_requirements = (('customer-work', 'member'),)
    return cmd


pick_list_create = _write('pick-list create', _CREATE, m.PickListCreateInput, plan_pick_list_create,
                          pick_lists.apply_create, errors=_ERRORS, positional=())
pick_list_enter = _write('pick-list enter', _ENTER, m.PickListEnterInput, plan_pick_list_enter,
                         pick_lists.apply_enter, errors=_ERRORS)
pick_list_close = _write('pick-list close', _CLOSE, m.PickListEndInput, plan_pick_list_close, pick_lists.apply_end,
                         errors=['E_VALIDATION', 'E_RECORD_NOT_FOUND', 'E_VERSION_CONFLICT', _OFF], version=_VERSION)
pick_list_cancel = _write('pick-list cancel', _CANCEL, m.PickListEndInput, plan_pick_list_cancel,
                          pick_lists.apply_end, version=_VERSION,
                          errors=['E_VALIDATION', 'E_RECORD_NOT_FOUND', 'E_VERSION_CONFLICT', 'E_REASON_REQUIRED', _OFF])
pick_list_show = command('pick-list show', scope='company', description=_SHOW, input_model=m.PickListShowInput,
                         output_model=m.PickListOutput, required_role='member', capability='item',
                         positional=['pick_list'], error_codes=['E_RECORD_NOT_FOUND', _OFF])(plan_pick_list_show)
pick_list_query = command('pick-list query', scope='company', description=_QUERY, input_model=m.PickListQueryInput,
                          output_model=m.PickListPageOutput, required_role='member', capability='item',
                          error_codes=['E_RECORD_NOT_FOUND', 'E_VALIDATION', _OFF])(plan_pick_list_query)
# A pick list names its orders' customers: reading one also needs `customer` (pos-v1).
pick_list_show.resource_requirements = pick_list_query.resource_requirements = (('customer', 'member'),)
