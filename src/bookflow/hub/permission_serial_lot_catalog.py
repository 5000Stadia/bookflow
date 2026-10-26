"""Add serial and lot numbers (R104 increment 4) without rewriting any accepted descriptor.

`inventory assign` numbers units and posts nothing, and `lot update` and `serial-number update` change a
list row's expiry or memo, so all three are `item` (standard), as sites, transfers and counting are.
Showing and listing numbers is `item` member; the three reports are `reports` member, as every report is.
Nothing here is reserved to a person; no capability, role default or existing threshold changes, so a root
replacing its catalog with this one suspends no agent and revokes no token.
"""
from dataclasses import asdict, replace
import hashlib
import json

from . import permission_inventory_counts_catalog as previous, permission_catalog as c
from .permission_snapshot import CatalogBundle

SOURCE_COMMIT = 'dcfe53c4460178de66ab86f61f95c512328109a5'
_OWNER = 'bookflow.commands.serial_lot_cmds.{}|src/bookflow/commands/serial_lot_cmds.py'
# (name, capability, threshold, planner)
_ADDED = (
    ('inventory assign', 'item', 'standard', 'plan_inventory_assign'),
    ('lot list', 'item', 'member', 'plan_lot_list'),
    ('lot show', 'item', 'member', 'plan_lot_show'),
    ('lot update', 'item', 'standard', 'plan_lot_update'),
    ('report lot-trace', 'reports', 'member', 'plan_lot_trace'),
    ('report serial-lot-history', 'reports', 'member', 'plan_serial_lot_history'),
    ('report serial-lot-stock', 'reports', 'member', 'plan_serial_lot_stock'),
    ('serial-number list', 'item', 'member', 'plan_serial_list'),
    ('serial-number show', 'item', 'member', 'plan_serial_show'),
    ('serial-number update', 'item', 'standard', 'plan_serial_update'),
)
ADDED_COMMANDS = tuple(
    c.CommandDescriptor(name, 'company', capability, threshold, (), True, None, False, False, None, None, None, None)
    for name, capability, threshold, _ in _ADDED)
ADDED_ACTIONS = tuple(
    c.CompanyAction(name, (c.Requirement(capability, threshold),), True, (_OWNER.format(planner),))
    for name, capability, threshold, planner in _ADDED)
CATALOG = replace(previous.CATALOG, version=c.SERIAL_LOT_POLICY_VERSION,
    commands=tuple(sorted((*previous.CATALOG.commands, *ADDED_COMMANDS), key=lambda x: x.name)),
    company_actions=tuple(sorted((*previous.CATALOG.company_actions, *ADDED_ACTIONS), key=lambda x: x.key)))
MANIFEST = c.catalog_manifest(CATALOG, previous.MANIFEST.standalone_names)


def catalog_bundle():
    inventory = dict(manifest=asdict(MANIFEST), sources=[asdict(x) for x in CATALOG.conditional_sources])
    digest = hashlib.sha256(json.dumps(inventory, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return CatalogBundle(SOURCE_COMMIT, CATALOG, MANIFEST.standalone_names, digest)
