"""Add draft shipments and packing (R181 increment 4) without rewriting any accepted descriptor.

`shipment create`, `pack` and `cancel` are stock records that post nothing, as pick lists, transfers and count entries
are: `item` standard, with `customer-work` read checked too, since a draft reads a customer's open order. Nothing is
people-only. No capability, role default or existing threshold changes, so a root replacing its catalog with this one
suspends no agent and revokes no token.
"""
from dataclasses import asdict, replace
import hashlib
import json

from . import permission_picking_catalog as previous, permission_catalog as c
from .permission_snapshot import CatalogBundle

SOURCE_COMMIT = 'bcb8c303de2050e227f50493f78241b7e73276b8'
_OWNER = 'bookflow.commands.shipment_pack_cmds.{}|src/bookflow/commands/shipment_pack_cmds.py'
_READ_WORK = (c.Requirement(capability='customer-work', threshold='member'),)
# (name, capability, threshold, planner, resources, authorization text)
_ADDED = (
    ('shipment cancel', 'item', 'standard', 'plan_shipment_cancel', _READ_WORK, None),
    ('shipment create', 'item', 'standard', 'plan_shipment_create', _READ_WORK, None),
    ('shipment pack', 'item', 'standard', 'plan_shipment_pack', _READ_WORK, None),
)
ADDED_COMMANDS = tuple(
    c.CommandDescriptor(name, 'company', capability, threshold, resources, True, None, False, False, authorization,
                        None, None, None)
    for name, capability, threshold, _, resources, authorization in _ADDED)
ADDED_ACTIONS = tuple(
    c.CompanyAction(name, tuple(sorted((*resources, c.Requirement(capability, threshold)),
                                       key=lambda r: r.capability)), True, (_OWNER.format(planner),))
    for name, capability, threshold, planner, resources, _ in _ADDED)
CATALOG = replace(previous.CATALOG, version=c.PACKING_POLICY_VERSION,
    commands=tuple(sorted((*previous.CATALOG.commands, *ADDED_COMMANDS), key=lambda x: x.name)),
    company_actions=tuple(sorted((*previous.CATALOG.company_actions, *ADDED_ACTIONS), key=lambda x: x.key)))
MANIFEST = c.catalog_manifest(CATALOG, previous.MANIFEST.standalone_names)


def catalog_bundle():
    inventory = dict(manifest=asdict(MANIFEST), sources=[asdict(x) for x in CATALOG.conditional_sources])
    digest = hashlib.sha256(json.dumps(inventory, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return CatalogBundle(SOURCE_COMMIT, CATALOG, MANIFEST.standalone_names, digest)
