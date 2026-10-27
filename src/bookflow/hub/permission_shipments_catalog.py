"""Add shipments (R181 increment 1) without rewriting any accepted descriptor.

`shipment post`, `update` and `void` post to the ledger and fill a customer's order: `ledger.post` standard with
`customer-work` standard checked too, as `sales-order invoice` is; `shipment invoice` is that command for one
shipment. `shipment show`, `query` and `history` are `item` member reads, and `report shipped-not-invoiced`
`reports` member. Nothing is people-only. No capability, role default or existing threshold changes, so a root
replacing its catalog with this one suspends no agent and revokes no token.
"""
from dataclasses import asdict, replace
import hashlib
import json

from . import permission_fifo_catalog as previous, permission_catalog as c
from .permission_snapshot import CatalogBundle

SOURCE_COMMIT = '361123cb91c3ce333c7a44f6ca5c0eb358f329f7'
_OWNER = 'bookflow.commands.shipment_cmds.{}|src/bookflow/commands/shipment_cmds.py'
_WORK = (c.Requirement(capability='customer-work', threshold='standard'),)
_AUTH = 'ledger.post and customer-work standard role'
# (name, capability, threshold, planner, resources, authorization text)
_ADDED = (
    ('report shipped-not-invoiced', 'reports', 'member', 'plan_shipped_not_invoiced', (), None),
    ('shipment history', 'item', 'member', 'plan_shipment_history', (), None),
    ('shipment invoice', 'ledger.post', 'standard', 'plan_shipment_invoice', _WORK, _AUTH),
    ('shipment post', 'ledger.post', 'standard', 'plan_shipment_post', _WORK, None),
    ('shipment query', 'item', 'member', 'plan_shipment_query', (), None),
    ('shipment show', 'item', 'member', 'plan_shipment_show', (), None),
    ('shipment update', 'ledger.post', 'standard', 'plan_shipment_update', _WORK, None),
    ('shipment void', 'ledger.post', 'standard', 'plan_shipment_void', _WORK, None),
)
ADDED_COMMANDS = tuple(
    c.CommandDescriptor(name, 'company', capability, threshold, resources, True, None, False, False, authorization,
                        None, None, None)
    for name, capability, threshold, _, resources, authorization in _ADDED)
ADDED_ACTIONS = tuple(
    c.CompanyAction(name, tuple(sorted((*resources, c.Requirement(capability, threshold)),
                                       key=lambda r: r.capability)), True, (_OWNER.format(planner),))
    for name, capability, threshold, planner, resources, _ in _ADDED)
CATALOG = replace(previous.CATALOG, version=c.SHIPMENTS_POLICY_VERSION,
    commands=tuple(sorted((*previous.CATALOG.commands, *ADDED_COMMANDS), key=lambda x: x.name)),
    company_actions=tuple(sorted((*previous.CATALOG.company_actions, *ADDED_ACTIONS), key=lambda x: x.key)))
MANIFEST = c.catalog_manifest(CATALOG, previous.MANIFEST.standalone_names)


def catalog_bundle():
    inventory = dict(manifest=asdict(MANIFEST), sources=[asdict(x) for x in CATALOG.conditional_sources])
    digest = hashlib.sha256(json.dumps(inventory, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return CatalogBundle(SOURCE_COMMIT, CATALOG, MANIFEST.standalone_names, digest)
