"""Add sorting the bank inbox (R108 increment 3) without rewriting any accepted descriptor.

`bank-line match`, `unmatch`, `ignore`, `add`, `transfer` and `undo` are standard writes under the existing capability
`bank-feed`, with `ledger.read` at `member` beside it, as `bank-feed import` is. An add or a posted transfer runs the
account's own command (`check post`, `deposit post`, `card-charge post`, `card-credit post`, `transfer post`) as the
caller, so it asks that command's own `ledger.post`; an undo deletes what its batch made through each family's own
delete grant. No capability, role default or threshold changes, so a root replacing its catalog with this one suspends
no agent and revokes no token.
"""
from dataclasses import asdict, replace
import hashlib
import json

from . import permission_import_documents_catalog as previous, permission_catalog as c
from .permission_snapshot import CatalogBundle

SOURCE_COMMIT = '5de700e0f126c358e455a5b541af89a663e18630'
_OWNER = 'bookflow.commands.bank_feed_cmds._sorter.<locals>.planner|src/bookflow/commands/bank_feed_cmds.py'
_LEDGER = (c.Requirement('ledger.read', 'member'),)
_ADDED = ('bank-line add', 'bank-line ignore', 'bank-line match', 'bank-line transfer', 'bank-line undo',
          'bank-line unmatch')
ADDED_COMMANDS = tuple(
    c.CommandDescriptor(name, 'company', 'bank-feed', 'standard', _LEDGER, True, None, False, False, None, None, None,
                        None)
    for name in _ADDED)
ADDED_ACTIONS = tuple(
    c.CompanyAction(name, tuple(sorted((*_LEDGER, c.Requirement('bank-feed', 'standard')),
                                       key=lambda r: r.capability)), True, (_OWNER,))
    for name in _ADDED)
CATALOG = replace(previous.CATALOG, version=c.BANK_LINE_POLICY_VERSION,
    commands=tuple(sorted((*previous.CATALOG.commands, *ADDED_COMMANDS), key=lambda x: x.name)),
    company_actions=tuple(sorted((*previous.CATALOG.company_actions, *ADDED_ACTIONS), key=lambda x: x.key)))
MANIFEST = c.catalog_manifest(CATALOG, previous.MANIFEST.standalone_names)


def catalog_bundle():
    inventory = dict(manifest=asdict(MANIFEST), sources=[asdict(x) for x in CATALOG.conditional_sources])
    digest = hashlib.sha256(json.dumps(inventory, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return CatalogBundle(SOURCE_COMMIT, CATALOG, MANIFEST.standalone_names, digest)
