"""Add bins and cycle counts (R104 increment 5) without rewriting any accepted descriptor.

The bin and cycle-class lists are `item` (standard to write, member to read), as sites are; classifying items
for cycle counts sets item fields, so it is `item` standard too, and `cycle-count due` is an `item` read. Items
by Bin is a report, `reports` member. Nothing here is reserved to a person; no capability, role default or
existing threshold changes, so a root replacing its catalog with this one suspends no agent and revokes no token.
"""
from dataclasses import asdict, replace
import hashlib
import json

from . import permission_serial_lot_catalog as previous, permission_catalog as c
from .permission_snapshot import CatalogBundle

SOURCE_COMMIT = '4114c40ac6cd58cf880c84bcaa025cff1cd860bc'
_OWNER = 'bookflow.commands.bin_cmds.{}|src/bookflow/commands/bin_cmds.py'
# (name, capability, threshold, planner)
_ADDED = (
    ('bin activate', 'item', 'standard', 'plan_bin_activate'),
    ('bin create', 'item', 'standard', 'plan_bin_create'),
    ('bin deactivate', 'item', 'standard', 'plan_bin_deactivate'),
    ('bin list', 'item', 'member', 'plan_bin_list'),
    ('bin show', 'item', 'member', 'plan_bin_show'),
    ('bin update', 'item', 'standard', 'plan_bin_update'),
    ('cycle-class activate', 'item', 'standard', 'plan_class_activate'),
    ('cycle-class create', 'item', 'standard', 'plan_class_create'),
    ('cycle-class deactivate', 'item', 'standard', 'plan_class_deactivate'),
    ('cycle-class list', 'item', 'member', 'plan_class_list'),
    ('cycle-class show', 'item', 'member', 'plan_class_show'),
    ('cycle-class update', 'item', 'standard', 'plan_class_update'),
    ('cycle-count classify', 'item', 'standard', 'plan_classify'),
    ('cycle-count due', 'item', 'member', 'plan_due'),
    ('report items-by-bin', 'reports', 'member', 'plan_items_by_bin'),
)
ADDED_COMMANDS = tuple(
    c.CommandDescriptor(name, 'company', capability, threshold, (), True, None, False, False, None, None, None, None)
    for name, capability, threshold, _ in _ADDED)
ADDED_ACTIONS = tuple(
    c.CompanyAction(name, (c.Requirement(capability, threshold),), True, (_OWNER.format(planner),))
    for name, capability, threshold, planner in _ADDED)
CATALOG = replace(previous.CATALOG, version=c.BINS_POLICY_VERSION,
    commands=tuple(sorted((*previous.CATALOG.commands, *ADDED_COMMANDS), key=lambda x: x.name)),
    company_actions=tuple(sorted((*previous.CATALOG.company_actions, *ADDED_ACTIONS), key=lambda x: x.key)))
MANIFEST = c.catalog_manifest(CATALOG, previous.MANIFEST.standalone_names)


def catalog_bundle():
    inventory = dict(manifest=asdict(MANIFEST), sources=[asdict(x) for x in CATALOG.conditional_sources])
    digest = hashlib.sha256(json.dumps(inventory, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return CatalogBundle(SOURCE_COMMIT, CATALOG, MANIFEST.standalone_names, digest)
