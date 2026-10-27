"""Add shipment returns (R181 increment 2) without rewriting any accepted descriptor.

`shipment return` and `shipment-return void` post to the ledger and change a customer's order (a return may cancel
its units on the order): `ledger.post` standard with `customer-work` standard checked too, as `shipment post` is.
`shipment-return show` and `query` are `item` member reads. Nothing is people-only. No capability, role default or
existing threshold changes, so a root replacing its catalog with this one suspends no agent and revokes no token.
"""
from dataclasses import asdict, replace
import hashlib
import json

from . import permission_setup_trial_catalog as previous, permission_catalog as c
from .permission_snapshot import CatalogBundle

SOURCE_COMMIT = '0ed5e625ea653e6c1e7e3197ec47b85ed40c26dd'
_OWNER = 'bookflow.commands.shipment_cmds.{}|src/bookflow/commands/shipment_cmds.py'
_WORK = (c.Requirement(capability='customer-work', threshold='standard'),)
# (name, capability, threshold, planner, resources, authorization text)
_ADDED = (
    ('shipment return', 'ledger.post', 'standard', 'plan_shipment_return', _WORK, None),
    ('shipment-return query', 'item', 'member', 'plan_shipment_return_query', (), None),
    ('shipment-return show', 'item', 'member', 'plan_shipment_return_show', (), None),
    ('shipment-return void', 'ledger.post', 'standard', 'plan_shipment_return_void', _WORK, None),
)
ADDED_COMMANDS = tuple(
    c.CommandDescriptor(name, 'company', capability, threshold, resources, True, None, False, False, authorization,
                        None, None, None)
    for name, capability, threshold, _, resources, authorization in _ADDED)
ADDED_ACTIONS = tuple(
    c.CompanyAction(name, tuple(sorted((*resources, c.Requirement(capability, threshold)),
                                       key=lambda r: r.capability)), True, (_OWNER.format(planner),))
    for name, capability, threshold, planner, resources, _ in _ADDED)
CATALOG = replace(previous.CATALOG, version=c.SHIPMENT_RETURNS_POLICY_VERSION,
    commands=tuple(sorted((*previous.CATALOG.commands, *ADDED_COMMANDS), key=lambda x: x.name)),
    company_actions=tuple(sorted((*previous.CATALOG.company_actions, *ADDED_ACTIONS), key=lambda x: x.key)))
MANIFEST = c.catalog_manifest(CATALOG, previous.MANIFEST.standalone_names)


def catalog_bundle():
    inventory = dict(manifest=asdict(MANIFEST), sources=[asdict(x) for x in CATALOG.conditional_sources])
    digest = hashlib.sha256(json.dumps(inventory, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return CatalogBundle(SOURCE_COMMIT, CATALOG, MANIFEST.standalone_names, digest)
