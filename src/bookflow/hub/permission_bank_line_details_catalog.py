"""Add more details from a bank line (R108 increment 5) on bulk-edit-v1, without rewriting any accepted descriptor.

`bank-line deposit` and `bank-line pay-bills` are standard writes under the existing capability `bank-feed`, with
`ledger.read` at `member` beside it, as the other `bank-line` acts are. Each runs the typed command (`deposit post`,
`bill pay`) as the caller, so it asks that command's own `ledger.post`; taking it back goes through each family's own
delete or void. No capability, role default or threshold changes, so a root replacing its catalog with this one suspends
no agent and revokes no token.
"""
from dataclasses import asdict, replace
import hashlib
import json

from . import permission_bulk_edit_catalog as previous, permission_catalog as c
from .permission_snapshot import CatalogBundle

SOURCE_COMMIT = '6547b59f532a82c79d745e6946aeb28613f1fc3c'
_OWNER = 'bookflow.commands.bank_feed_cmds._sorter.<locals>.planner|src/bookflow/commands/bank_feed_cmds.py'
_LEDGER = (c.Requirement('ledger.read', 'member'),)
_ADDED = ('bank-line deposit', 'bank-line pay-bills')
ADDED_COMMANDS = tuple(
    c.CommandDescriptor(name, 'company', 'bank-feed', 'standard', _LEDGER, True, None, False, False, None, None, None,
                        None)
    for name in _ADDED)
ADDED_ACTIONS = tuple(
    c.CompanyAction(name, tuple(sorted((*_LEDGER, c.Requirement('bank-feed', 'standard')),
                                       key=lambda r: r.capability)), True, (_OWNER,))
    for name in _ADDED)
CATALOG = replace(previous.CATALOG, version=c.BANK_LINE_DETAILS_POLICY_VERSION,
    commands=tuple(sorted((*previous.CATALOG.commands, *ADDED_COMMANDS), key=lambda x: x.name)),
    company_actions=tuple(sorted((*previous.CATALOG.company_actions, *ADDED_ACTIONS), key=lambda x: x.key)))
MANIFEST = c.catalog_manifest(CATALOG, previous.MANIFEST.standalone_names)


def catalog_bundle():
    inventory = dict(manifest=asdict(MANIFEST), sources=[asdict(x) for x in CATALOG.conditional_sources])
    digest = hashlib.sha256(json.dumps(inventory, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return CatalogBundle(SOURCE_COMMIT, CATALOG, MANIFEST.standalone_names, digest)
