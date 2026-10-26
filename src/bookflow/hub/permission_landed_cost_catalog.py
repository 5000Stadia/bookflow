"""Add landed cost (R104 increment 6) without rewriting any accepted descriptor.

`landed-cost post` and `void` post journal entries and recost stock, so they are `ledger.post` (standard), as
`inventory adjust` is; `landed-cost setup` makes the clearing account and is `ledger.post` (standard) too. The
charges list, show and query are `ledger.read` (member). Nothing here is reserved to a person; no capability,
role default or existing threshold changes, so a root replacing its catalog with this one suspends no agent and
revokes no token.
"""
from dataclasses import asdict, replace
import hashlib
import json

from . import permission_serial_lot_catalog as previous, permission_catalog as c
from .permission_snapshot import CatalogBundle

SOURCE_COMMIT = 'b88908a62f98c2ad20cb5d8d2703f2c2f421721b'
_OWNER = 'bookflow.commands.landed_cost_cmds.{}|src/bookflow/commands/landed_cost_cmds.py'
# (name, capability, threshold, planner)
_ADDED = (
    ('landed-cost charges', 'ledger.read', 'member', 'plan_landed_cost_charges'),
    ('landed-cost post', 'ledger.post', 'standard', 'plan_landed_cost_post'),
    ('landed-cost query', 'ledger.read', 'member', 'plan_landed_cost_query'),
    ('landed-cost setup', 'ledger.post', 'standard', 'plan_landed_cost_setup'),
    ('landed-cost show', 'ledger.read', 'member', 'plan_landed_cost_show'),
    ('landed-cost void', 'ledger.post', 'standard', 'plan_landed_cost_void'),
)
ADDED_COMMANDS = tuple(
    c.CommandDescriptor(name, 'company', capability, threshold, (), True, None, False, False, None, None, None, None)
    for name, capability, threshold, _ in _ADDED)
ADDED_ACTIONS = tuple(
    c.CompanyAction(name, (c.Requirement(capability, threshold),), True, (_OWNER.format(planner),))
    for name, capability, threshold, planner in _ADDED)
CATALOG = replace(previous.CATALOG, version=c.LANDED_COST_POLICY_VERSION,
    commands=tuple(sorted((*previous.CATALOG.commands, *ADDED_COMMANDS), key=lambda x: x.name)),
    company_actions=tuple(sorted((*previous.CATALOG.company_actions, *ADDED_ACTIONS), key=lambda x: x.key)))
MANIFEST = c.catalog_manifest(CATALOG, previous.MANIFEST.standalone_names)


def catalog_bundle():
    inventory = dict(manifest=asdict(MANIFEST), sources=[asdict(x) for x in CATALOG.conditional_sources])
    digest = hashlib.sha256(json.dumps(inventory, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return CatalogBundle(SOURCE_COMMIT, CATALOG, MANIFEST.standalone_names, digest)
