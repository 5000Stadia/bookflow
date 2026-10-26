"""Physical counts (R104 increment 2): count what is on the shelf, and post only the true difference.

The work lives in ``company/inventory_counts.py``.
"""
from bookflow.company import inventory_counts as counts
from bookflow.company import inventory_count_models as m
from bookflow.core.registry import Plan, command

_VERSION = ('inventory-count show', 'count', 'version')
_ERRORS = ['E_VALIDATION', 'E_RECORD_NOT_FOUND', 'E_INACTIVE_REFERENCE', 'E_PERIOD_CLOSED']


def plan_count_create(inp, ctx, s) -> Plan:
    return counts.plan_create(s, ctx, inp)


def plan_count_enter(inp, ctx, s) -> Plan:
    return counts.plan_enter(s, ctx, inp)


def plan_count_post(inp, ctx, s) -> Plan:
    return counts.plan_post(s, ctx, inp)


def plan_count_cancel(inp, ctx, s) -> Plan:
    return counts.plan_end('cancel')(s, ctx, inp)


def plan_count_void(inp, ctx, s) -> Plan:
    return counts.plan_end('void')(s, ctx, inp)


def plan_count_show(inp, ctx, s) -> Plan:
    return Plan(counts.show(s, inp))


def plan_count_sheet(inp, ctx, s) -> Plan:
    return Plan(counts.sheet(s, inp))


def plan_count_query(inp, ctx, s) -> Plan:
    return Plan(counts.query(s, inp))


def _write(name, description, inp, out, planner, applier, *, capability, errors, positional=('count',), version=None):
    cmd = command(name, scope='company', description=description, input_model=inp, output_model=out,
                  writes={'company'}, required_role='standard', capability=capability, accepts_idempotency_key=True,
                  positional=list(positional), version_source=version, error_codes=errors)(planner)
    cmd.applier(applier)
    return cmd


def _read(name, description, inp, out, planner, *, errors, positional=()):
    return command(name, scope='company', description=description, input_model=inp, output_model=out,
                   writes=set(), required_role='member', capability='item', positional=list(positional),
                   error_codes=errors)(planner)


count_create = _write(
    'inventory-count create',
    'Start a physical count (a stocktake) of one site: the anchor\'s Physical Inventory Worksheet, kept as a '
    'document. It lists one line per item and place the books hold at the site on the count date, and a line '
    'for each item you name. Select every stocked item with quantity or history at the site (the default), '
    'named items, or an item category. blind hides expected quantities from the sheet, so counters count '
    'rather than confirm; they show again in the post preview. Nothing posts until the count is recorded. '
    'Example: `bookflow inventory-count create --site "Van 1" --reason "Monday van count" --json`.',
    m.CountCreateInput, m.CountOutput, plan_count_create, counts.apply_create, capability='item',
    errors=[*_ERRORS, 'E_DUPLICATE_NUMBER'], positional=())
count_enter = _write(
    'inventory-count enter',
    'Enter what was counted on an open count: 1-500 entries, each an item (id, name or barcode) or a scanned '
    '`code`, and a quantity. mode set (the default) says the place holds this many; add counts this many more '
    '(a scan adds 1). Entries append, each call under its own idempotency key, so two people, or a person and an '
    'agent, can count one count at once and both land. An item not on the sheet adds a found line. Returns, per '
    'line counted, counted, expected (unless blind) and the difference, and the lines still uncounted. Example: '
    '`bookflow inventory-count enter C-1 --entries \'[{"item":"Widget","quantity":"12"}]\' --reason "Counted" --json`.',
    m.CountEnterInput, m.CountEnterOutput, plan_count_enter, counts.apply_enter, capability='item',
    errors=['E_VALIDATION', 'E_RECORD_NOT_FOUND'])
count_post = _write(
    'inventory-count post',
    'Record a count. Its counted quantities are fixed, and each line\'s difference is derived at the count\'s '
    'position in its day: counted less what the books hold at that place from documents dated before the count '
    'date, or on it and placed before the count (the default for every document; a document that happened after '
    'the count says count_position after). Per item the net posts as one movement -- an issue for less, found '
    'units valued at the average for more -- in one journal entry dated the count date against adjustment_account. '
    'Unchanged and uncounted lines post nothing. Run it as a dry run first: the preview shows each item\'s '
    'difference and value, the documents entered after counting began, and a preview_fingerprint; pass that '
    'fingerprint to post (E_PREVIEW_STALE if anything changed). A document later dated before the count derives '
    'the difference again, so the shelf stays as counted. Example: `bookflow inventory-count post C-1 '
    '--expected-version 1 --adjustment-account "Inventory Shrinkage" --preview-fingerprint <from the dry run> '
    '--reason "Van count" --json`.',
    m.CountPostInput, m.CountPostOutput, plan_count_post, counts.apply_post, capability='ledger.post',
    errors=[*_ERRORS, 'E_VERSION_CONFLICT', 'E_PREVIEW_STALE', 'E_UNBALANCED_ENTRY'], version=_VERSION)
count_cancel = _write(
    'inventory-count cancel', 'End an open count with nothing recorded. A reason is required.',
    m.CountEndInput, m.CountOutput, plan_count_cancel, counts.apply_end, capability='item',
    errors=['E_VALIDATION', 'E_RECORD_NOT_FOUND', 'E_VERSION_CONFLICT', 'E_REASON_REQUIRED'], version=_VERSION)
count_void = _write(
    'inventory-count void',
    'Void a recorded count: its posted differences are reversed at the count date and nothing is derived for it '
    'again. Refused inside a closed period. A reason is required.',
    m.CountEndInput, m.CountOutput, plan_count_void, counts.apply_end, capability='ledger.post',
    errors=['E_VALIDATION', 'E_RECORD_NOT_FOUND', 'E_VERSION_CONFLICT', 'E_REASON_REQUIRED', 'E_PERIOD_CLOSED'],
    version=_VERSION)
count_show = _read('inventory-count show', 'Show a count: its lines, what was counted, and once recorded the journal '
                   'entries its differences posted in. A blind count shows no expected quantities.',
                   m.CountSelectInput, m.CountOutput, plan_count_show, errors=['E_RECORD_NOT_FOUND', 'E_VALIDATION'],
                   positional=['count'])
count_sheet = _read('inventory-count sheet', 'The count sheet, printable as the Physical Inventory Worksheet: item, '
                    'description, place, expected, counted and difference; a blind count omits expected and '
                    'difference.', m.CountSelectInput, m.CountOutput, plan_count_sheet,
                    errors=['E_RECORD_NOT_FOUND', 'E_VALIDATION'], positional=['count'])
count_query = _read('inventory-count query', 'Counts, newest first, by site, status and date.',
                    m.CountQueryInput, m.CountPageOutput, plan_count_query, errors=['E_RECORD_NOT_FOUND', 'E_VALIDATION'])


# ---------------------------------------------------------------------------- reports

def plan_physical_inventory_worksheet(inp, ctx, s) -> Plan:
    from bookflow.company.inventory_count_reports import physical_inventory_worksheet
    return Plan(preview=physical_inventory_worksheet(inp, s, principal_id=ctx.on_behalf_of))


def plan_inventory_valuation_detail(inp, ctx, s) -> Plan:
    from bookflow.company.inventory_count_reports import inventory_valuation_detail
    return Plan(preview=inventory_valuation_detail(inp, s, principal_id=ctx.on_behalf_of))


def _report_models():
    from bookflow.company import inventory_count_reports as r
    return r.WorksheetInput, r.WorksheetOutput, r.ValuationDetailInput, r.ValuationDetailOutput


_WS_IN, _WS_OUT, _VD_IN, _VD_OUT = _report_models()
worksheet_report = command(
    'report physical-inventory-worksheet', scope='company', required_role='member', capability='reports',
    description="The Physical Inventory Worksheet, as of as_of: every active inventory item with its description, "
                'what the books hold (at one site when site is named) and a blank physical_count column to count '
                'into. `inventory-count create` makes the same sheet as a count document whose entries are kept. '
                'Rows are paged.',
    input_model=_WS_IN, output_model=_WS_OUT, writes=set(),
    error_codes=['E_QUERY_STALE', 'E_VALUE_RANGE', 'E_VALIDATION'])(plan_physical_inventory_worksheet)
valuation_detail_report = command(
    'report inventory-valuation-detail', scope='company', required_role='member', capability='reports',
    description='Inventory Valuation Detail from date_from to date_to: per item, its opening quantity and value, '
                'every stock movement in the period in the order cost is worked out (date, then before, at or '
                'after that day\'s physical counts, then recorded order) with the document, the running quantity, '
                'value and average cost, and its closing figures. Cost corrections and reversals are rows of their '
                'own, so the running value is what the balance sheet carries; item narrows it to one item. Refuses '
                'when the items\' value disagrees with the inventory asset account. Rows are paged.',
    input_model=_VD_IN, output_model=_VD_OUT, writes=set(),
    error_codes=['E_QUERY_STALE', 'E_VALUE_RANGE', 'E_VALIDATION', 'E_RECORD_NOT_FOUND'])(plan_inventory_valuation_detail)
