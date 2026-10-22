"""Add the sales order (R101) without rewriting any accepted descriptor.

Eight company commands: `sales-order create`, `update`, `copy` and `void` and `estimate
sales-order` at `customer-work` standard, as the other customer-work writes are, and
`sales-order show`, `query` and `history` at `customer-work` member. An agent with the standard
role can do all of it; nothing here is reserved to a person. No capability, role default or
existing threshold changes, so a root replacing its catalog with this one suspends no agent
and revokes no token.
"""
from dataclasses import asdict, replace
import hashlib
import json

from . import permission_list_merge_catalog as previous, permission_catalog as c
from .permission_snapshot import CatalogBundle

SOURCE_COMMIT = '273b99cbf9bc4d8d3b9f9f7fd3bd9861cccc3ce3'
_OWNER = ('bookflow.commands.work_cmds._register.<locals>.planner|src/bookflow/commands/work_cmds.py',)
_WRITES = ('estimate sales-order', 'sales-order copy', 'sales-order create', 'sales-order update', 'sales-order void')
_READS = ('sales-order history', 'sales-order query', 'sales-order show')


def _threshold(name):
    return 'standard' if name in _WRITES else 'member'


ADDED_COMMANDS = tuple(
    c.CommandDescriptor(name, 'company', 'customer-work', _threshold(name), (), True, None, False, False,
                        None, None, None, None)
    for name in sorted(_WRITES + _READS))
ADDED_ACTIONS = tuple(
    c.CompanyAction(name, (c.Requirement('customer-work', _threshold(name)),), True, _OWNER)
    for name in sorted(_WRITES + _READS))
CATALOG = replace(previous.CATALOG, version=c.SALES_ORDER_POLICY_VERSION,
    commands=tuple(sorted((*previous.CATALOG.commands, *ADDED_COMMANDS), key=lambda x: x.name)),
    company_actions=tuple(sorted((*previous.CATALOG.company_actions, *ADDED_ACTIONS), key=lambda x: x.key)))
MANIFEST = c.catalog_manifest(CATALOG, previous.MANIFEST.standalone_names)


def catalog_bundle():
    inventory = dict(manifest=asdict(MANIFEST), sources=[asdict(x) for x in CATALOG.conditional_sources])
    digest = hashlib.sha256(json.dumps(inventory, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return CatalogBundle(SOURCE_COMMIT, CATALOG, MANIFEST.standalone_names, digest)
