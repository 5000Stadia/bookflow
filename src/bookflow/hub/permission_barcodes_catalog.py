"""Add barcodes (R104 increment 3) without rewriting any accepted descriptor (chained on check-printing-v1).

`barcode lookup` is `item` member and `barcode generate` is `item` standard, as `item show` and
`item update` are: generating writes each item's barcode as an item update. Nothing is reserved to a
person; no capability, role default or existing threshold changes, so a root replacing its catalog
with this one suspends no agent and revokes no token.
"""
from dataclasses import asdict, replace
import hashlib
import json

from . import permission_check_printing_catalog as previous, permission_catalog as c
from .permission_snapshot import CatalogBundle

SOURCE_COMMIT = '747916b1063932ea15b5e82d836b018dae1dafc5'
_OWNER = 'bookflow.commands.barcode_cmds.{}|src/bookflow/commands/barcode_cmds.py'
_ADDED = (
    ('barcode generate', 'item', 'standard', 'plan_generate'),
    ('barcode lookup', 'item', 'member', 'plan_lookup'),
)
ADDED_COMMANDS = tuple(
    c.CommandDescriptor(name, 'company', capability, threshold, (), True, None, False, False, None, None, None, None)
    for name, capability, threshold, _ in _ADDED)
ADDED_ACTIONS = tuple(
    c.CompanyAction(name, (c.Requirement(capability, threshold),), True, (_OWNER.format(planner),))
    for name, capability, threshold, planner in _ADDED)
CATALOG = replace(previous.CATALOG, version=c.BARCODES_POLICY_VERSION,
    commands=tuple(sorted((*previous.CATALOG.commands, *ADDED_COMMANDS), key=lambda x: x.name)),
    company_actions=tuple(sorted((*previous.CATALOG.company_actions, *ADDED_ACTIONS), key=lambda x: x.key)))
MANIFEST = c.catalog_manifest(CATALOG, previous.MANIFEST.standalone_names)


def catalog_bundle():
    inventory = dict(manifest=asdict(MANIFEST), sources=[asdict(x) for x in CATALOG.conditional_sources])
    digest = hashlib.sha256(json.dumps(inventory, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return CatalogBundle(SOURCE_COMMIT, CATALOG, MANIFEST.standalone_names, digest)
