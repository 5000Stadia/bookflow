"""Add the fulfillment worksheet (R181 increment 5) without rewriting any accepted descriptor.

`report order-fulfillment` and `report shipments` are reports: `reports` member, as `report shipped-not-invoiced` is.
Nothing is people-only. No capability, role default or existing threshold changes, so a root replacing its catalog
with this one suspends no agent and revokes no token.
"""
from dataclasses import asdict, replace
import hashlib
import json

from . import permission_import_catalog as previous, permission_catalog as c
from .permission_snapshot import CatalogBundle

SOURCE_COMMIT = '73d7bebd640414bda46984313ac503ccd66bbcfe'
_OWNER = 'bookflow.commands.fulfillment_cmds.{}|src/bookflow/commands/fulfillment_cmds.py'
# (name, capability, threshold, planner, resources, authorization text)
_ADDED = (
    ('report order-fulfillment', 'reports', 'member', 'plan_order_fulfillment', (), None),
    ('report shipments', 'reports', 'member', 'plan_shipments_report', (), None),
)
ADDED_COMMANDS = tuple(
    c.CommandDescriptor(name, 'company', capability, threshold, resources, True, None, False, False, authorization,
                        None, None, None)
    for name, capability, threshold, _, resources, authorization in _ADDED)
ADDED_ACTIONS = tuple(
    c.CompanyAction(name, tuple(sorted((*resources, c.Requirement(capability, threshold)),
                                       key=lambda r: r.capability)), True, (_OWNER.format(planner),))
    for name, capability, threshold, planner, resources, _ in _ADDED)
CATALOG = replace(previous.CATALOG, version=c.WORKSHEET_POLICY_VERSION,
    commands=tuple(sorted((*previous.CATALOG.commands, *ADDED_COMMANDS), key=lambda x: x.name)),
    company_actions=tuple(sorted((*previous.CATALOG.company_actions, *ADDED_ACTIONS), key=lambda x: x.key)))
MANIFEST = c.catalog_manifest(CATALOG, previous.MANIFEST.standalone_names)


def catalog_bundle():
    inventory = dict(manifest=asdict(MANIFEST), sources=[asdict(x) for x in CATALOG.conditional_sources])
    digest = hashlib.sha256(json.dumps(inventory, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return CatalogBundle(SOURCE_COMMIT, CATALOG, MANIFEST.standalone_names, digest)
