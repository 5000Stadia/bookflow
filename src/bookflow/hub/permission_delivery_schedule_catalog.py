"""Add document delivery's recurring schedules (R112 increment 4) without rewriting any accepted descriptor.

`delivery-schedule create`, `update`, `pause`, `resume` and `run` are message writes, admitted as increment 1's are:
`customer` at `standard`, with `ledger.read` and `customer-work` at `member` so a principal who may not read a document
may not schedule its email. `show` and `query` read at `customer` `member` with the same resources. `delivery-schedule
live`, making a send schedule live, carries a people-only admin action (R187 converts it to the capability
`automate.delivery`). No capability, role default or existing threshold changes, so a root replacing its catalog with
this one suspends no agent and revokes no token.
"""
from dataclasses import asdict, replace
import hashlib
import json

from . import permission_import_mapping_catalog as previous, permission_catalog as c
from . import permission_human_admin_catalog as human_admin
from .permission_snapshot import CatalogBundle

SOURCE_COMMIT = '44ecfdef80e37bd6b87f660256b12b5f5b8b9bda'
_OWNER = 'bookflow.commands.delivery_cmds.{}|src/bookflow/commands/delivery_cmds.py'
_MESSAGES = (c.Requirement(capability='customer-work', threshold='member'),
             c.Requirement(capability='ledger.read', threshold='member'))
# (name, capability, threshold, planner, resources)
_ADDED = (
    ('delivery-schedule create', 'customer', 'standard', 'plan_schedule_create', _MESSAGES),
    ('delivery-schedule live', 'customer', 'standard', 'plan_schedule_live', _MESSAGES),
    ('delivery-schedule pause', 'customer', 'standard', 'plan_schedule_pause', _MESSAGES),
    ('delivery-schedule query', 'customer', 'member', 'plan_schedule_query', _MESSAGES),
    ('delivery-schedule resume', 'customer', 'standard', 'plan_schedule_resume', _MESSAGES),
    ('delivery-schedule run', 'customer', 'standard', 'plan_schedule_run', _MESSAGES),
    ('delivery-schedule show', 'customer', 'member', 'plan_schedule_show', _MESSAGES),
    ('delivery-schedule update', 'customer', 'standard', 'plan_schedule_update', _MESSAGES),
)
# R187 converts this to the capability automate.delivery.
PEOPLE_ONLY = ('delivery-schedule live',)
ADDED_COMMANDS = tuple(
    c.CommandDescriptor(name, 'company', capability, threshold, resources, True, None, False, False, None, None, None,
                        None)
    for name, capability, threshold, _, resources in _ADDED)
ADDED_ACTIONS = tuple(
    c.CompanyAction(name, tuple(sorted((*resources, c.Requirement(capability, threshold)),
                                       key=lambda r: r.capability)), True, (_OWNER.format(planner),))
    for name, capability, threshold, planner, resources in _ADDED)
ADDED_ADMIN_ACTIONS = tuple(c.AdminAction(human_admin.PEOPLE_ONLY_PREFIX + name, 'company', 'standard', True, True)
                            for name in PEOPLE_ONLY)
CATALOG = replace(previous.CATALOG, version=c.DELIVERY_SCHEDULE_POLICY_VERSION,
    commands=tuple(sorted((*previous.CATALOG.commands, *ADDED_COMMANDS), key=lambda x: x.name)),
    company_actions=tuple(sorted((*previous.CATALOG.company_actions, *ADDED_ACTIONS), key=lambda x: x.key)),
    admin_actions=tuple(sorted((*previous.CATALOG.admin_actions, *ADDED_ADMIN_ACTIONS), key=lambda x: x.key)))
MANIFEST = c.catalog_manifest(CATALOG, previous.MANIFEST.standalone_names)


def catalog_bundle():
    inventory = dict(manifest=asdict(MANIFEST), sources=[asdict(x) for x in CATALOG.conditional_sources])
    digest = hashlib.sha256(json.dumps(inventory, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return CatalogBundle(SOURCE_COMMIT, CATALOG, MANIFEST.standalone_names, digest)
