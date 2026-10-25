"""Add physical counts (R104 increment 2) without rewriting any accepted descriptor.

Creating a count, entering what was counted and cancelling an open count are `item` (standard), as
sites and transfers are: they are stock record-keeping that posts nothing. Recording a count and
voiding one post its derived differences to the ledger, so they are `ledger.post` (standard), as
`inventory adjust` is. Reading counts and their sheets is `item` member; the two reports are `reports`
member, as every report is. Nothing here is reserved to a person; no capability, role default or
existing threshold changes, so a root replacing its catalog with this one suspends no agent and
revokes no token.
"""
from dataclasses import asdict, replace
import hashlib
import json

from . import permission_check_printing_catalog as previous, permission_catalog as c
from .permission_snapshot import CatalogBundle

SOURCE_COMMIT = '6985a28ce5ab0ced0e8e52ad7e9678351c38237f'
_OWNER = 'bookflow.commands.inventory_count_cmds.{}|src/bookflow/commands/inventory_count_cmds.py'
# (name, capability, threshold, planner)
_ADDED = (
    ('inventory-count cancel', 'item', 'standard', 'plan_count_cancel'),
    ('inventory-count create', 'item', 'standard', 'plan_count_create'),
    ('inventory-count enter', 'item', 'standard', 'plan_count_enter'),
    ('inventory-count post', 'ledger.post', 'standard', 'plan_count_post'),
    ('inventory-count query', 'item', 'member', 'plan_count_query'),
    ('inventory-count sheet', 'item', 'member', 'plan_count_sheet'),
    ('inventory-count show', 'item', 'member', 'plan_count_show'),
    ('inventory-count void', 'ledger.post', 'standard', 'plan_count_void'),
    ('report inventory-valuation-detail', 'reports', 'member', 'plan_inventory_valuation_detail'),
    ('report physical-inventory-worksheet', 'reports', 'member', 'plan_physical_inventory_worksheet'),
)
ADDED_COMMANDS = tuple(
    c.CommandDescriptor(name, 'company', capability, threshold, (), True, None, False, False, None, None, None, None)
    for name, capability, threshold, _ in _ADDED)
ADDED_ACTIONS = tuple(
    c.CompanyAction(name, (c.Requirement(capability, threshold),), True, (_OWNER.format(planner),))
    for name, capability, threshold, planner in _ADDED)
CATALOG = replace(previous.CATALOG, version=c.INVENTORY_COUNTS_POLICY_VERSION,
    commands=tuple(sorted((*previous.CATALOG.commands, *ADDED_COMMANDS), key=lambda x: x.name)),
    company_actions=tuple(sorted((*previous.CATALOG.company_actions, *ADDED_ACTIONS), key=lambda x: x.key)))
MANIFEST = c.catalog_manifest(CATALOG, previous.MANIFEST.standalone_names)


def catalog_bundle():
    inventory = dict(manifest=asdict(MANIFEST), sources=[asdict(x) for x in CATALOG.conditional_sources])
    digest = hashlib.sha256(json.dumps(inventory, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return CatalogBundle(SOURCE_COMMIT, CATALOG, MANIFEST.standalone_names, digest)
