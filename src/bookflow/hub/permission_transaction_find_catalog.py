"""Add `transaction find` without rewriting any accepted descriptor.

It is admitted at `ledger.read` at `member`, as every document's own `show` is: it lists documents a
member can already open one by one, and leaves out (and names) any family whose own read the caller may
not run. It carries one company action naming its planner. No capability, role default, admin action or
existing threshold changes, so a root replacing its catalog with this one suspends no agent and revokes
no token.
"""
from dataclasses import asdict, replace
import hashlib
import json

from . import permission_attachment_text_catalog as previous, permission_catalog as c
from .permission_snapshot import CatalogBundle

SOURCE_COMMIT = 'a583f7414cba98ae31f7525b4dd1bf65cbd820a0'
ADDED_COMMANDS = (
    c.CommandDescriptor('transaction find', 'company', 'ledger.read', 'member', (), True, None, False, False, None, None, None, None),
)
ADDED_ACTIONS = (
    c.CompanyAction('transaction find', (c.Requirement('ledger.read', 'member'),), True,
                    ('bookflow.commands.transaction_find_cmds.transaction_find|src/bookflow/commands/transaction_find_cmds.py',)),
)
CATALOG = replace(previous.CATALOG, version=c.TRANSACTION_FIND_POLICY_VERSION,
    commands=tuple(sorted((*previous.CATALOG.commands, *ADDED_COMMANDS), key=lambda x: x.name)),
    company_actions=tuple(sorted((*previous.CATALOG.company_actions, *ADDED_ACTIONS), key=lambda x: x.key)))
MANIFEST = c.catalog_manifest(CATALOG, previous.MANIFEST.standalone_names)


def catalog_bundle():
    inventory = dict(manifest=asdict(MANIFEST), sources=[asdict(x) for x in CATALOG.conditional_sources])
    digest = hashlib.sha256(json.dumps(inventory, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return CatalogBundle(SOURCE_COMMIT, CATALOG, MANIFEST.standalone_names, digest)
