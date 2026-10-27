"""Add pick lists (R181 increment 3) without rewriting any accepted descriptor.

`pick-list create`, `enter`, `close` and `cancel` are stock records that post nothing, as transfers and count entries
are: `item` standard, with `customer-work` read checked too, since a list reads customers' open orders. `pick-list show`
and `query` are `item` member reads. Nothing is people-only. No capability, role default or existing threshold changes,
so a root replacing its catalog with this one suspends no agent and revokes no token.
"""
from dataclasses import asdict, replace
import hashlib
import json

from . import permission_shipment_returns_catalog as previous, permission_catalog as c
from .permission_snapshot import CatalogBundle

SOURCE_COMMIT = '94d14424b064d6ff980409690273eae11343a445'
_OWNER = 'bookflow.commands.pick_list_cmds.{}|src/bookflow/commands/pick_list_cmds.py'
_READ_WORK = (c.Requirement(capability='customer-work', threshold='member'),)
# (name, capability, threshold, planner, resources, authorization text)
_ADDED = (
    ('pick-list cancel', 'item', 'standard', 'plan_pick_list_cancel', _READ_WORK, None),
    ('pick-list close', 'item', 'standard', 'plan_pick_list_close', _READ_WORK, None),
    ('pick-list create', 'item', 'standard', 'plan_pick_list_create', _READ_WORK, None),
    ('pick-list enter', 'item', 'standard', 'plan_pick_list_enter', _READ_WORK, None),
    ('pick-list query', 'item', 'member', 'plan_pick_list_query', (), None),
    ('pick-list show', 'item', 'member', 'plan_pick_list_show', (), None),
)
ADDED_COMMANDS = tuple(
    c.CommandDescriptor(name, 'company', capability, threshold, resources, True, None, False, False, authorization,
                        None, None, None)
    for name, capability, threshold, _, resources, authorization in _ADDED)
ADDED_ACTIONS = tuple(
    c.CompanyAction(name, tuple(sorted((*resources, c.Requirement(capability, threshold)),
                                       key=lambda r: r.capability)), True, (_OWNER.format(planner),))
    for name, capability, threshold, planner, resources, _ in _ADDED)
CATALOG = replace(previous.CATALOG, version=c.PICKING_POLICY_VERSION,
    commands=tuple(sorted((*previous.CATALOG.commands, *ADDED_COMMANDS), key=lambda x: x.name)),
    company_actions=tuple(sorted((*previous.CATALOG.company_actions, *ADDED_ACTIONS), key=lambda x: x.key)))
MANIFEST = c.catalog_manifest(CATALOG, previous.MANIFEST.standalone_names)


def catalog_bundle():
    inventory = dict(manifest=asdict(MANIFEST), sources=[asdict(x) for x in CATALOG.conditional_sources])
    digest = hashlib.sha256(json.dumps(inventory, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return CatalogBundle(SOURCE_COMMIT, CATALOG, MANIFEST.standalone_names, digest)
