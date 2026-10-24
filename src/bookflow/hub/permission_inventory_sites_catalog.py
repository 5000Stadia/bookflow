"""Add inventory sites and transfers (R104 increment 1) without rewriting any accepted descriptor.

The site list and transfers are `item` (standard to write, member to read), as the item list is:
a transfer moves quantity between sites and posts nothing, so it is stock record-keeping beside the
item list rather than a ledger post. `report quantity-by-site` is `reports` member, as every report
is. Nothing here is reserved to a person; no capability, role default or existing threshold changes,
so a root replacing its catalog with this one suspends no agent and revokes no token.
"""
from dataclasses import asdict, replace
import hashlib
import json

from . import permission_shortfall_po_catalog as previous, permission_catalog as c
from .permission_snapshot import CatalogBundle

SOURCE_COMMIT = '7759da73bb6d955dfe80443370094a984d963de8'
_OWNER = 'bookflow.commands.inventory_site_cmds.{}|src/bookflow/commands/inventory_site_cmds.py'
# (name, capability, threshold, planner)
_ADDED = (
    ('inventory-site activate', 'item', 'standard', 'plan_site_activate'),
    ('inventory-site create', 'item', 'standard', 'plan_site_create'),
    ('inventory-site deactivate', 'item', 'standard', 'plan_site_deactivate'),
    ('inventory-site list', 'item', 'member', 'plan_site_list'),
    ('inventory-site show', 'item', 'member', 'plan_site_show'),
    ('inventory-site update', 'item', 'standard', 'plan_site_update'),
    ('inventory-transfer history', 'item', 'member', 'plan_transfer_history'),
    ('inventory-transfer post', 'item', 'standard', 'plan_transfer_post'),
    ('inventory-transfer query', 'item', 'member', 'plan_transfer_query'),
    ('inventory-transfer show', 'item', 'member', 'plan_transfer_show'),
    ('inventory-transfer update', 'item', 'standard', 'plan_transfer_update'),
    ('inventory-transfer void', 'item', 'standard', 'plan_transfer_void'),
    ('report quantity-by-site', 'reports', 'member', 'plan_quantity_by_site'),
)
ADDED_COMMANDS = tuple(
    c.CommandDescriptor(name, 'company', capability, threshold, (), True, None, False, False, None, None, None, None)
    for name, capability, threshold, _ in _ADDED)
ADDED_ACTIONS = tuple(
    c.CompanyAction(name, (c.Requirement(capability, threshold),), True, (_OWNER.format(planner),))
    for name, capability, threshold, planner in _ADDED)
CATALOG = replace(previous.CATALOG, version=c.INVENTORY_SITES_POLICY_VERSION,
    commands=tuple(sorted((*previous.CATALOG.commands, *ADDED_COMMANDS), key=lambda x: x.name)),
    company_actions=tuple(sorted((*previous.CATALOG.company_actions, *ADDED_ACTIONS), key=lambda x: x.key)))
MANIFEST = c.catalog_manifest(CATALOG, previous.MANIFEST.standalone_names)


def catalog_bundle():
    inventory = dict(manifest=asdict(MANIFEST), sources=[asdict(x) for x in CATALOG.conditional_sources])
    digest = hashlib.sha256(json.dumps(inventory, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return CatalogBundle(SOURCE_COMMIT, CATALOG, MANIFEST.standalone_names, digest)
