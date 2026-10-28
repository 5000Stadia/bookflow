"""Add document delivery's queue and transport seam (R112 increment 2) without rewriting any accepted descriptor.

`delivery approve`, `retry` and `run` are messages, admitted as increment 1's are: `customer` at `standard`, with
`ledger.read` and `customer-work` at `member` so a principal who may not read a document may not move its email.
`approve` gives a go and carries a people-only admin action (R187 converts it to the capability `delivery.send`).
Sending connections say where mail goes and under whose keys, a company preference: `company` at `admin` for every
write and `check`, each people-only (R187: `delivery.connect`), and `company` at `member` to read. No capability, role
default or existing threshold changes, so a root replacing its catalog with this one suspends no agent and revokes no
token.
"""
from dataclasses import asdict, replace
import hashlib
import json

from . import permission_packing_catalog as previous, permission_catalog as c
from . import permission_human_admin_catalog as human_admin
from .permission_snapshot import CatalogBundle

SOURCE_COMMIT = '80f91c26f313b5859647ecfa32ab09931a499065'
_OWNER = 'bookflow.commands.delivery_cmds.{}|src/bookflow/commands/delivery_cmds.py'
_MESSAGES = (c.Requirement(capability='customer-work', threshold='member'),
             c.Requirement(capability='ledger.read', threshold='member'))
# (name, capability, threshold, planner, resources)
_ADDED = (
    ('delivery approve', 'customer', 'standard', 'plan_delivery_approve', _MESSAGES),
    ('delivery retry', 'customer', 'standard', 'plan_delivery_retry', _MESSAGES),
    ('delivery run', 'customer', 'standard', 'plan_delivery_run', _MESSAGES),
    ('delivery-connection add', 'company', 'admin', 'plan_connection_add', ()),
    ('delivery-connection check', 'company', 'admin', 'plan_connection_check', ()),
    ('delivery-connection disable', 'company', 'admin', 'plan_connection_disable', ()),
    ('delivery-connection enable', 'company', 'admin', 'plan_connection_enable', ()),
    ('delivery-connection query', 'company', 'member', 'plan_connection_query', ()),
    ('delivery-connection remove', 'company', 'admin', 'plan_connection_remove', ()),
    ('delivery-connection show', 'company', 'member', 'plan_connection_show', ()),
    ('delivery-connection update', 'company', 'admin', 'plan_connection_update', ()),
)
# R187 converts these to capabilities: approve to delivery.send, the connection writes to delivery.connect.
PEOPLE_ONLY = ('delivery approve', 'delivery-connection add', 'delivery-connection check',
               'delivery-connection disable', 'delivery-connection enable', 'delivery-connection remove',
               'delivery-connection update')
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
CATALOG = replace(previous.CATALOG, version=c.DELIVERY_QUEUE_POLICY_VERSION,
    commands=tuple(sorted((*previous.CATALOG.commands, *ADDED_COMMANDS), key=lambda x: x.name)),
    company_actions=tuple(sorted((*previous.CATALOG.company_actions, *ADDED_ACTIONS), key=lambda x: x.key)),
    admin_actions=tuple(sorted((*previous.CATALOG.admin_actions, *ADDED_ADMIN_ACTIONS), key=lambda x: x.key)))
MANIFEST = c.catalog_manifest(CATALOG, previous.MANIFEST.standalone_names)


def catalog_bundle():
    inventory = dict(manifest=asdict(MANIFEST), sources=[asdict(x) for x in CATALOG.conditional_sources])
    digest = hashlib.sha256(json.dumps(inventory, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return CatalogBundle(SOURCE_COMMIT, CATALOG, MANIFEST.standalone_names, digest)
