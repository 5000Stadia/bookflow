"""Add the costing method (R104 increment 7) without rewriting any accepted descriptor.

`costing-method set` and `withdraw` change how every stocked item is costed from a date, so they are the
company's own setting: `company` (admin), and a person's act -- an agent is refused whatever its role (the
people-only list). `costing-method show` is `ledger.read` (member) and `report inventory-layers` `reports`
(member). No capability, role default or existing threshold changes, so a root replacing its catalog with this
one suspends no agent and revokes no token.
"""
from dataclasses import asdict, replace
import hashlib
import json

from . import permission_landed_cost_catalog as previous, permission_catalog as c
from . import permission_human_admin_catalog as human_admin
from .permission_snapshot import CatalogBundle

SOURCE_COMMIT = '3b4d7943e3325f732a819c1eb1c44a9ef1c90961'
_OWNER = 'bookflow.commands.costing_method_cmds.{}|src/bookflow/commands/costing_method_cmds.py'
# (name, capability, threshold, planner)
_ADDED = (
    ('costing-method set', 'company', 'admin', 'plan_costing_method_set'),
    ('costing-method show', 'ledger.read', 'member', 'plan_costing_method_show'),
    ('costing-method withdraw', 'company', 'admin', 'plan_costing_method_withdraw'),
    ('report inventory-layers', 'reports', 'member', 'plan_inventory_layers'),
)
PEOPLE_ONLY_COMMANDS = ('costing-method set', 'costing-method withdraw')
ADDED_COMMANDS = tuple(
    c.CommandDescriptor(name, 'company', capability, threshold, (), True, None, False, False, None, None, None, None)
    for name, capability, threshold, _ in _ADDED)
ADDED_ACTIONS = tuple(
    c.CompanyAction(name, (c.Requirement(capability, threshold),), True, (_OWNER.format(planner),))
    for name, capability, threshold, planner in _ADDED)
ADDED_ADMIN_ACTIONS = tuple(c.AdminAction(human_admin.PEOPLE_ONLY_PREFIX + name, 'company', 'admin', True, True)
                            for name in PEOPLE_ONLY_COMMANDS)
CATALOG = replace(previous.CATALOG, version=c.FIFO_POLICY_VERSION,
    commands=tuple(sorted((*previous.CATALOG.commands, *ADDED_COMMANDS), key=lambda x: x.name)),
    company_actions=tuple(sorted((*previous.CATALOG.company_actions, *ADDED_ACTIONS), key=lambda x: x.key)),
    admin_actions=tuple(sorted((*previous.CATALOG.admin_actions, *ADDED_ADMIN_ACTIONS), key=lambda x: x.key)))
MANIFEST = c.catalog_manifest(CATALOG, previous.MANIFEST.standalone_names)


def catalog_bundle():
    inventory = dict(manifest=asdict(MANIFEST), sources=[asdict(x) for x in CATALOG.conditional_sources])
    digest = hashlib.sha256(json.dumps(inventory, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return CatalogBundle(SOURCE_COMMIT, CATALOG, MANIFEST.standalone_names, digest)
