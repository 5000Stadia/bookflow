"""Bins and cycle counts (R104 increment 5): the bin and cycle-class lists, Items by Bin, and cycle-count
classify and due.

The work lives in ``company/bins.py`` and ``company/cycle_counts.py``; a line's bin is decided in
``company/inventory_effects.py`` and placed by ``company/serial_lots.place``.
"""
from bookflow.company import bin_models as m
from bookflow.company import bins
from bookflow.company import cycle_counts
from bookflow.core.registry import Plan, command

_LIST_ERRORS = ['E_VALIDATION', 'E_RECORD_NOT_FOUND', 'E_NAME_TAKEN', 'E_VERSION_CONFLICT']
_STATE_ERRORS = ['E_VALIDATION', 'E_RECORD_NOT_FOUND', 'E_VERSION_CONFLICT']


def _write(name, description, inp, out, planner, applier, *, errors, positional=(), version=None):
    cmd = command(name, scope='company', description=description, input_model=inp, output_model=out,
                  writes={'company'}, required_role='standard', capability='item', accepts_idempotency_key=True,
                  positional=list(positional), version_source=version, error_codes=errors)(planner)
    cmd.applier(applier)
    return cmd


def _read(name, description, inp, out, planner, *, errors, positional=(), capability='item'):
    return command(name, scope='company', description=description, input_model=inp, output_model=out,
                   writes=set(), required_role='member', capability=capability, positional=list(positional),
                   error_codes=errors)(planner)


# ---------------------------------------------------------------------------- bins

def plan_bin_create(inp, ctx, s) -> Plan:
    return bins.plan_create(s, ctx, inp)


def plan_bin_update(inp, ctx, s) -> Plan:
    return bins.plan_update(s, ctx, inp)


def plan_bin_activate(inp, ctx, s) -> Plan:
    return bins.plan_active(s, ctx, inp, True)


def plan_bin_deactivate(inp, ctx, s) -> Plan:
    return bins.plan_active(s, ctx, inp, False)


def plan_bin_show(inp, ctx, s) -> Plan:
    return Plan(bins.show(s, inp))


def plan_bin_list(inp, ctx, s) -> Plan:
    return Plan(bins.listing(s, inp))


def plan_items_by_bin(inp, ctx, s) -> Plan:
    return Plan(bins.items_by_bin(s, inp))


_BIN_VERSION = ('bin show', 'bin', 'version')

bin_create = _write(
    'bin create', "Add a bin to a site that tracks bins, as the anchor's Advanced Inventory bin locations do: up to "
    'three levels (an aisle, a shelf, a section -- A, A:3, A:3:Top), each named once per site, with an optional '
    'label code a scanner reads. Turn bins on for a site first with `inventory-site update <site> --tracks-bins '
    'true`. Example: `bookflow bin create --site Warehouse --name 3 --parent A --barcode 2000000000107`.',
    m.BinCreateInput, m.BinWriteOutput, plan_bin_create, bins.apply,
    errors=['E_VALIDATION', 'E_NAME_TAKEN', 'E_RECORD_NOT_FOUND', 'E_INACTIVE_REFERENCE'])
bin_update = _write(
    'bin update', 'Rename a bin (the bins inside it are renamed with it), or change its label code or description. '
    'A bin keeps its site and level.', m.BinUpdateInput, m.BinWriteOutput, plan_bin_update, bins.apply,
    errors=_LIST_ERRORS, positional=['bin'], version=_BIN_VERSION)
bin_activate = _write(
    'bin activate', 'Make an inactive bin available again for stock lines and transfers.',
    m.BinSelectInput, m.BinWriteOutput, plan_bin_activate, bins.apply, errors=_STATE_ERRORS, positional=['bin'],
    version=_BIN_VERSION)
bin_deactivate = _write(
    'bin deactivate', 'Make a bin inactive so new stock lines cannot name it. Refused (E_RECORD_IN_USE, naming the '
    'items) while it or a bin inside it holds stock, or while active bins sit inside it.',
    m.BinSelectInput, m.BinWriteOutput, plan_bin_deactivate, bins.apply, errors=[*_STATE_ERRORS, 'E_RECORD_IN_USE'],
    positional=['bin'], version=_BIN_VERSION)
bin_show = _read('bin show', 'Show a bin with what it and the bins inside it hold (item, lot or serial number, '
                 'quantity), as of today or as_of. A bin is named by id, full name (A:3:Top) or label code; name the '
                 'site when two sites have a bin of that name.', m.BinShowInput, m.BinOutput, plan_bin_show,
                 errors=['E_RECORD_NOT_FOUND', 'E_VALIDATION'], positional=['bin'])
bin_list = _read('bin list', 'The bins of a site (or of every site), by full name, active ones unless include_inactive, '
                 'each with how many items it holds.', m.BinListInput, m.BinPageOutput, plan_bin_list,
                 errors=['E_VALIDATION'])
items_by_bin_report = _read(
    'report items-by-bin', "Items by Bin (the anchor's Bin Location reports): each item in each bin of a site, with "
    'its lot or serial number and quantity, and what the site holds at no bin; site totals show the bins adding up '
    'to the site. site, bin (with the bins inside it) and item narrow it; as_of reads an earlier date. Refuses '
    'when placements disagree with the stock ledger.', m.ItemsByBinInput, m.ItemsByBinOutput, plan_items_by_bin,
    errors=['E_VALIDATION', 'E_RECORD_NOT_FOUND', 'E_INTERNAL'], capability='reports')


# ---------------------------------------------------------------------------- cycle classes

def plan_class_create(inp, ctx, s) -> Plan:
    return cycle_counts.plan_create(s, ctx, inp)


def plan_class_update(inp, ctx, s) -> Plan:
    return cycle_counts.plan_update(s, ctx, inp)


def plan_class_activate(inp, ctx, s) -> Plan:
    return cycle_counts.plan_active(s, ctx, inp, True)


def plan_class_deactivate(inp, ctx, s) -> Plan:
    return cycle_counts.plan_active(s, ctx, inp, False)


def plan_class_show(inp, ctx, s) -> Plan:
    return Plan(cycle_counts.show(s, inp))


def plan_class_list(inp, ctx, s) -> Plan:
    return Plan(cycle_counts.listing(s, inp))


def plan_classify(inp, ctx, s) -> Plan:
    return cycle_counts.plan_classify(s, ctx, inp)


def plan_due(inp, ctx, s) -> Plan:
    return Plan(cycle_counts.due(s, inp))


_CLASS_VERSION = ('cycle-class show', 'cycle_class', 'version')

class_create = _write(
    'cycle-class create', 'Add a cycle-count class: a name and how many days may pass between counts of an item in '
    'it at a site. The company starts with A (30 days), B (90) and C (365), stored by the first write that needs '
    'them. Example: `bookflow cycle-class create --name D --interval-days 730`.',
    m.CycleClassCreateInput, m.CycleClassWriteOutput, plan_class_create, cycle_counts.apply,
    errors=['E_VALIDATION', 'E_NAME_TAKEN'])
class_update = _write(
    'cycle-class update', "Rename a cycle class or change its interval; items in it fall due by the new interval.",
    m.CycleClassUpdateInput, m.CycleClassWriteOutput, plan_class_update, cycle_counts.apply, errors=_LIST_ERRORS,
    positional=['cycle_class'], version=_CLASS_VERSION)
class_activate = _write(
    'cycle-class activate', 'Make an inactive cycle class available again.', m.CycleClassSelectInput,
    m.CycleClassWriteOutput, plan_class_activate, cycle_counts.apply, errors=_STATE_ERRORS, positional=['cycle_class'],
    version=_CLASS_VERSION)
class_deactivate = _write(
    'cycle-class deactivate', 'Make a cycle class inactive. Refused (E_RECORD_IN_USE) while items are in it.',
    m.CycleClassSelectInput, m.CycleClassWriteOutput, plan_class_deactivate, cycle_counts.apply,
    errors=[*_STATE_ERRORS, 'E_RECORD_IN_USE'], positional=['cycle_class'], version=_CLASS_VERSION)
class_show = _read('cycle-class show', 'Show a cycle class: its interval and how many items are in it.',
                   m.CycleClassShowInput, m.CycleClassOutput, plan_class_show,
                   errors=['E_VALIDATION', 'E_RECORD_NOT_FOUND'], positional=['cycle_class'])
class_list = _read('cycle-class list', 'The cycle classes, shortest interval first, each with how many items are in '
                   'it. Before any is stored it lists the defaults (A 30, B 90, C 365) with empty ids.',
                   m.CycleClassListInput, m.CycleClassPageOutput, plan_class_list, errors=[])
classify = _write(
    'cycle-count classify', 'Propose cycle classes from cost of goods issued over a period (default the last 12 '
    'months to today), the ABC way: ranked by issued cost, the items making the first 80% (a_share) are A, the next '
    '15% (b_share) B, the rest C, an item that issued nothing C. Count differences and inventory adjustments do not '
    'count as issued. The dry run lists each item, its share and its proposed class; running it sets the classes as '
    'item updates. Example: `bookflow cycle-count classify --dry-run`.',
    m.ClassifyInput, m.ClassifyOutput, plan_classify, cycle_counts.apply_classify,
    errors=['E_VALIDATION', 'E_RECORD_NOT_FOUND', 'E_INACTIVE_REFERENCE'])
due = _read(
    'cycle-count due', 'What should we count this week? The items due for a cycle count at a site: an item is due when '
    'its class interval has passed since its last recorded count there, or it has never been counted there '
    '(unclassified items never are) -- then it has been due since it was first stocked there. Ranked by days overdue '
    'over the class interval, so a late A item comes before an equally late C item. A count of bins counts an item '
    'only when every bin holding it was counted. Count the first ones with `inventory-count create --site <site> '
    '--cycle --limit 25`; places left over take items falling due within the next six days. Example: `bookflow cycle-count due --site Warehouse`.',
    m.DueInput, m.DueOutput, plan_due, errors=['E_VALIDATION', 'E_RECORD_NOT_FOUND'])
