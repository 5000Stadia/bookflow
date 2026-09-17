"""Add retained refund history without rewriting any accepted permission descriptor."""
from dataclasses import asdict, replace
import hashlib
import json

from . import permission_journal_deletion_catalog as previous, permission_catalog as c
from .permission_snapshot import CatalogBundle

SOURCE_COMMIT = '86a7a2fe17c63145447f393dc6d7024c678f17b7'
ADDED_COMMANDS = (
    c.CommandDescriptor('customer-refund history', 'company', 'ledger.read', 'member', (),
                        True, None, False, False, None, None, None, None),
)
ADDED_ACTIONS = (
    c.CompanyAction('customer-refund history', (c.Requirement('ledger.read', 'member'),), True,
        ('bookflow.commands.refund_cmds._read.<locals>.planner|src/bookflow/commands/refund_cmds.py',)),
)
CATALOG = replace(previous.CATALOG, version=c.REFUND_HISTORY_POLICY_VERSION,
    commands=tuple(sorted((*previous.CATALOG.commands, *ADDED_COMMANDS), key=lambda x: x.name)),
    company_actions=tuple(sorted((*previous.CATALOG.company_actions, *ADDED_ACTIONS), key=lambda x: x.key)))
MANIFEST = c.catalog_manifest(CATALOG, previous.MANIFEST.standalone_names)


def catalog_bundle():
    inventory = dict(manifest=asdict(MANIFEST), sources=[asdict(x) for x in CATALOG.conditional_sources])
    digest = hashlib.sha256(json.dumps(inventory, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return CatalogBundle(SOURCE_COMMIT, CATALOG, MANIFEST.standalone_names, digest)
