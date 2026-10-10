"""Add `payment bounce` without rewriting any accepted permission descriptor.

`payment bounce` writes at `ledger.post` at `standard`, as `payment unapply` and `customer-refund post`
do: it is those writes (and a register entry and an invoice) done together, so it asks no more of a
caller than they do and no less. It carries one company action naming its planner. No capability,
role default, admin action or existing threshold changes, so a root replacing its catalog with this
one suspends no agent and revokes no token. The people-only admin actions of the layer below are
carried unchanged: recording a returned check is bookkeeping, not an administrative act.
"""
from dataclasses import asdict, replace
import hashlib
import json

from . import permission_party_merge_catalog as previous, permission_catalog as c
from .permission_snapshot import CatalogBundle

SOURCE_COMMIT = 'cfbf6d05e3f4cd53a597bfe2300115ec6de2fddc'
_PLANNER = 'bookflow.commands.payment_cmds._bounce.<locals>.planner|src/bookflow/commands/payment_cmds.py'
ADDED_COMMANDS = (
    c.CommandDescriptor('payment bounce', 'company', 'ledger.post', 'standard', (), True, None, False, False, None, None, None, None),
)
ADDED_ACTIONS = (
    c.CompanyAction('payment bounce', (c.Requirement('ledger.post', 'standard'),), True, (_PLANNER,)),
)
CATALOG = replace(previous.CATALOG, version=c.BOUNCED_CHECK_POLICY_VERSION,
    commands=tuple(sorted((*previous.CATALOG.commands, *ADDED_COMMANDS), key=lambda x: x.name)),
    company_actions=tuple(sorted((*previous.CATALOG.company_actions, *ADDED_ACTIONS), key=lambda x: x.key)))
MANIFEST = c.catalog_manifest(CATALOG, previous.MANIFEST.standalone_names)


def catalog_bundle():
    inventory = dict(manifest=asdict(MANIFEST), sources=[asdict(x) for x in CATALOG.conditional_sources])
    digest = hashlib.sha256(json.dumps(inventory, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return CatalogBundle(SOURCE_COMMIT, CATALOG, MANIFEST.standalone_names, digest)
