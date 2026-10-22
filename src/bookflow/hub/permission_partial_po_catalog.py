"""Add closing purchase order lines by hand without rewriting any accepted descriptor.

R153. `purchase-order close-lines` closes or reopens ordered lines, the order's Closed box per
line; it changes a purchase order and posts nothing, so it is `ledger.post` `standard`, as
`purchase-order update` is. Billing part of an order is a new field on `bill post`, which keeps
its own descriptor. No capability, role default, admin action or existing threshold changes, so
a root replacing its catalog with this one suspends no agent and revokes no token.
"""
from dataclasses import asdict, replace
import hashlib
import json

from . import permission_fixed_asset_catalog as previous, permission_catalog as c
from .permission_snapshot import CatalogBundle

SOURCE_COMMIT = '5f885570c07c4d5fb51f7df295bb25a03f66dde3'
_OWNER = 'bookflow.commands.purchase_order_cmds.{}|src/bookflow/commands/purchase_order_cmds.py'
_ADDED = (
    ('purchase-order close-lines', 'ledger.post', 'standard', 'plan_purchase_order_close_lines'),
)
ADDED_COMMANDS = tuple(
    c.CommandDescriptor(name, 'company', capability, threshold, (), True, None, False, False, None, None, None, None)
    for name, capability, threshold, _ in _ADDED)
ADDED_ACTIONS = tuple(
    c.CompanyAction(name, (c.Requirement(capability, threshold),), True, (_OWNER.format(planner),))
    for name, capability, threshold, planner in _ADDED)
CATALOG = replace(previous.CATALOG, version=c.PARTIAL_PO_POLICY_VERSION,
    commands=tuple(sorted((*previous.CATALOG.commands, *ADDED_COMMANDS), key=lambda x: x.name)),
    company_actions=tuple(sorted((*previous.CATALOG.company_actions, *ADDED_ACTIONS), key=lambda x: x.key)))
MANIFEST = c.catalog_manifest(CATALOG, previous.MANIFEST.standalone_names)


def catalog_bundle():
    inventory = dict(manifest=asdict(MANIFEST), sources=[asdict(x) for x in CATALOG.conditional_sources])
    digest = hashlib.sha256(json.dumps(inventory, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return CatalogBundle(SOURCE_COMMIT, CATALOG, MANIFEST.standalone_names, digest)
