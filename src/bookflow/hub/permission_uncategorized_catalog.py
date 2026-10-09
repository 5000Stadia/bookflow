"""Add `account uncategorized` without rewriting any accepted permission descriptor.

One read-only company command: what is still waiting in the Uncategorized (Ask My Accountant)
accounts. It is admitted exactly as `account show` is -- the `account` capability at `member` --
with one company action carrying the planner that runs it. No capability, role default, admin
action or existing threshold changes, so a root replacing its catalog with this one suspends no
agent and revokes no token, and a root activated at the previous catalog admits this command
through the tip without re-activation.
"""
from dataclasses import asdict, replace
import hashlib
import json

from . import permission_cutover_catalog as previous, permission_catalog as c
from .permission_snapshot import CatalogBundle

SOURCE_COMMIT = 'f38f3f927fd92b7ff25732472b3150b95cf40bcb'
ADDED_COMMANDS = (
    c.CommandDescriptor('account uncategorized', 'company', 'account', 'member', (), True, None, False, False, None, None, None, None),
)
ADDED_ACTIONS = (
    c.CompanyAction('account uncategorized', (c.Requirement('account', 'member'),), True,
                    ('bookflow.commands.account_cmds.plan_account_uncategorized|src/bookflow/commands/account_cmds.py',)),
)
CATALOG = replace(previous.CATALOG, version=c.UNCATEGORIZED_POLICY_VERSION,
    commands=tuple(sorted((*previous.CATALOG.commands, *ADDED_COMMANDS), key=lambda x: x.name)),
    company_actions=tuple(sorted((*previous.CATALOG.company_actions, *ADDED_ACTIONS), key=lambda x: x.key)))
MANIFEST = c.catalog_manifest(CATALOG, previous.MANIFEST.standalone_names)


def catalog_bundle():
    inventory = dict(manifest=asdict(MANIFEST), sources=[asdict(x) for x in CATALOG.conditional_sources])
    digest = hashlib.sha256(json.dumps(inventory, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return CatalogBundle(SOURCE_COMMIT, CATALOG, MANIFEST.standalone_names, digest)
