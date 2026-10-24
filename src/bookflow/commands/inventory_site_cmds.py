"""Inventory sites and inventory transfers (R104 increment 1): where stock is, and moving it.

The work lives in ``company/inventory_sites.py``; placements in ``company/placements.py``.
"""
from bookflow.company import inventory_sites as sites
from bookflow.company import inventory_site_models as m
from bookflow.core.registry import Plan, command

_LIST_ERRORS = ['E_VALIDATION', 'E_RECORD_NOT_FOUND', 'E_NAME_TAKEN', 'E_VERSION_CONFLICT']


def _write(name, description, inp, out, planner, applier, *, errors, positional=(), version=None):
    cmd = command(name, scope='company', description=description, input_model=inp, output_model=out,
                  writes={'company'}, required_role='standard', capability='item', accepts_idempotency_key=True,
                  positional=list(positional), version_source=version, error_codes=errors)(planner)
    cmd.applier(applier)
    return cmd


def _read(name, description, inp, out, planner, *, errors, positional=()):
    return command(name, scope='company', description=description, input_model=inp, output_model=out,
                   writes=set(), required_role='member', capability='item', positional=list(positional),
                   error_codes=errors)(planner)


# ---------------------------------------------------------------------------- sites

def plan_site_create(inp, ctx, s) -> Plan:
    return sites.plan_site_create(s, ctx, inp)


def plan_site_update(inp, ctx, s) -> Plan:
    return sites.plan_site_update(s, ctx, inp)


def plan_site_activate(inp, ctx, s) -> Plan:
    return sites.plan_site_active(s, ctx, inp, True)


def plan_site_deactivate(inp, ctx, s) -> Plan:
    return sites.plan_site_active(s, ctx, inp, False)


def plan_site_show(inp, ctx, s) -> Plan:
    return Plan(sites.site_show(s, inp))


def plan_site_list(inp, ctx, s) -> Plan:
    return Plan(sites.site_list(s, inp))


_SITE_VERSION = ('inventory-site show', 'site', 'version')

site_create = _write(
    'inventory-site create', "Add an inventory site, as the anchor's Advanced Inventory sites do: a warehouse, a "
    'staging area, a service van, a consignment location or goods on the water. The stock at a site is the '
    "company's own. Every company starts with one site, Main, the default; stock lines that name no site are "
    'placed at the default. is_default true makes the new site the default instead. Example: '
    '`bookflow inventory-site create --name "Van 1" --description "Ford Transit, plumber 1"`.',
    m.SiteCreateInput, m.SiteWriteOutput, plan_site_create, sites.apply_site, errors=['E_VALIDATION', 'E_NAME_TAKEN'])
site_update = _write(
    'inventory-site update', 'Rename a site (renaming Main is an ordinary update), change its description or '
    'address, or make it the default (is_default true); the old default stops being it.',
    m.SiteUpdateInput, m.SiteWriteOutput, plan_site_update, sites.apply_site, errors=_LIST_ERRORS,
    positional=['site'], version=_SITE_VERSION)
site_activate = _write(
    'inventory-site activate', 'Make an inactive site available again for stock lines and transfers.',
    m.SiteSelectInput, m.SiteWriteOutput, plan_site_activate, sites.apply_site, errors=_LIST_ERRORS,
    positional=['site'], version=_SITE_VERSION)
site_deactivate = _write(
    'inventory-site deactivate', 'Make a site inactive so new stock lines and transfers cannot name it. Refused '
    '(E_RECORD_IN_USE, naming the items) while it holds any stock; transfer it out first. The default site '
    'cannot be deactivated.',
    m.SiteSelectInput, m.SiteWriteOutput, plan_site_deactivate, sites.apply_site,
    errors=[*_LIST_ERRORS, 'E_RECORD_IN_USE'], positional=['site'], version=_SITE_VERSION)
site_show = _read('inventory-site show', "Show a site with each item it holds (on hand, as of today or as_of) and "
                  'its latest transfers in and out.', m.SiteShowInput, m.SiteOutput, plan_site_show,
                  errors=['E_RECORD_NOT_FOUND', 'E_VALIDATION'], positional=['site'])
site_list = _read('inventory-site list', 'The inventory sites by name, active ones unless include_inactive, each with '
                  'how many items it holds and whether it is the default.', m.SiteListInput, m.SitePageOutput,
                  plan_site_list, errors=[])


# ---------------------------------------------------------------------------- transfers

def plan_transfer_post(inp, ctx, s) -> Plan:
    return sites.prepare(s, ctx, inp, 'post')


def plan_transfer_update(inp, ctx, s) -> Plan:
    return sites.prepare(s, ctx, inp, 'update')


def plan_transfer_void(inp, ctx, s) -> Plan:
    return sites.prepare(s, ctx, inp, 'void')


def plan_transfer_show(inp, ctx, s) -> Plan:
    return Plan(sites.show(s, inp))


def plan_transfer_query(inp, ctx, s) -> Plan:
    return Plan(sites.query(s, inp))


def plan_transfer_history(inp, ctx, s) -> Plan:
    return Plan(sites.history(s, inp))


_TRANSFER_ERRORS = ['E_VALIDATION', 'E_RECORD_NOT_FOUND', 'E_INACTIVE_REFERENCE', 'E_VALUE_RANGE',
                    'E_PERIOD_CLOSED', 'E_DUPLICATE_NUMBER']
_TRANSFER_VERSION = ('inventory-transfer show', 'transfer', 'version')

transfer_post = _write(
    'inventory-transfer post', "Move stock from one inventory site to another, as the anchor's Transfer Inventory "
    'does: from_site, to_site and 1-200 lines (item and quantity in its base unit), with date (default today), '
    'memo and reason. Each line takes the quantity out of the from site and puts it in the to site on that date. '
    'Nothing is posted: no journal entry, no value and no cost change, because an item has one average cost '
    'company-wide. Stock leaves and arrives at once (nothing is held in transit). Taking the from site below '
    'zero is saved with a warning that names where else the item is. A date on or before the closing date is '
    'refused. The result gives the transfer number and each site\'s on hand after it. Moving money between '
    'accounts is `transfer post`. Example: `bookflow inventory-transfer post --from-site "Van 1" --to-site '
    'Warehouse --lines \'[{"item":"Widget","quantity":"5"}]\' --reason "Unloaded 5 widgets from the van"`.',
    m.TransferPostInput, m.TransferWriteOutput, plan_transfer_post, sites.apply, errors=_TRANSFER_ERRORS)
transfer_update = _write(
    'inventory-transfer update', 'Correct a transfer under its expected version: name only what changes (date, '
    'sites, lines, memo). The old placements are reversed and the new ones written in one transaction; nothing '
    'is posted. An update reaching a date on or before the closing date is refused.',
    m.TransferUpdateInput, m.TransferWriteOutput, plan_transfer_update, sites.apply,
    errors=[*_TRANSFER_ERRORS, 'E_VERSION_CONFLICT'], positional=['transfer'], version=_TRANSFER_VERSION)
transfer_void = _write(
    'inventory-transfer void', 'Void a transfer, with a reason: its stock goes back to the sites it came from. A '
    'void is final and kept in history. Refused when the transfer is dated on or before the closing date.',
    m.TransferVoidInput, m.TransferWriteOutput, plan_transfer_void, sites.apply,
    errors=[*_TRANSFER_ERRORS, 'E_VERSION_CONFLICT', 'E_REASON_REQUIRED'], positional=['transfer'],
    version=_TRANSFER_VERSION)
transfer_show = _read('inventory-transfer show', 'Show a transfer: its sites, date, lines and every revision.',
                      m.TransferShowInput, m.TransferOutput, plan_transfer_show, errors=['E_RECORD_NOT_FOUND'],
                      positional=['transfer'])
transfer_query = _read('inventory-transfer query', 'Transfers, newest first, filtered by site (in or out), item and '
                       'dates; voided ones only with include_voided.', m.TransferQueryInput, m.TransferPageOutput,
                       plan_transfer_query, errors=['E_RECORD_NOT_FOUND', 'E_VALIDATION'])
transfer_history = _read('inventory-transfer history', 'Every revision of a transfer: post, updates and void, with '
                         'who, when and why.', m.TransferShowInput, m.TransferOutput, plan_transfer_history,
                         errors=['E_RECORD_NOT_FOUND'], positional=['transfer'])


# ---------------------------------------------------------------------------- reports

def plan_quantity_by_site(inp, ctx, s) -> Plan:
    from bookflow.company.inventory_reports import quantity_by_site
    return Plan(preview=quantity_by_site(inp, s, principal_id=ctx.on_behalf_of))


def _quantity_by_site_models():
    from bookflow.company.inventory_reports import QuantityBySiteInput, QuantityBySiteOutput
    return QuantityBySiteInput, QuantityBySiteOutput


_QBS_IN, _QBS_OUT = _quantity_by_site_models()
quantity_by_site_report = command(
    'report quantity-by-site', scope='company', required_role='member', capability='reports',
    description="Quantity on Hand by Site, as of as_of: each inventory item at each site with what is on hand there, "
                'committed there by open sales-order and work-order lines naming that site, reserved there by pending '
                'builds, available and on order to arrive there, and its share of the item\'s value. Order and '
                'work-order lines naming no site commit on an Unassigned row (they are deducted at no site), so the '
                'sites plus Unassigned are the company figures report stock-status shows. Value is shared by quantity '
                'over the sites holding stock; a site below zero shows no value, and the sites add up to the item. site narrows it to one site. Quantities are read from placements and the report '
                'refuses when they disagree with the movement ledger (the quantity tie) or when the values disagree '
                'with the balance sheet (the asset tie). Rows are paged.',
    input_model=_QBS_IN, output_model=_QBS_OUT, writes=set(),
    error_codes=['E_QUERY_STALE', 'E_VALUE_RANGE', 'E_VALIDATION'])(plan_quantity_by_site)
