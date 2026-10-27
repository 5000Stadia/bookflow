"""Add the R183 setup-trial fixes without rewriting any accepted descriptor.

`owner-link` is a local bootstrap command like `setup`, on the same capability (`init`, authenticated):
it runs in the calling process as the computer's own login and is never routed over HTTP or MCP.
`agent run` is a standalone local tool like `mcp`: it holds no authority of its own and makes the same
`bookflow_run` call an MCP client makes, with the agent's token, so the host checks it as it checks MCP.
`reconcile discard` cancels an open reconciliation draft: `ledger.post` standard, as `reconcile mark` is;
it is not people-only. No capability, role default or existing threshold changes, so a root replacing
its catalog with this one suspends no agent and revokes no token.
"""
from dataclasses import asdict, replace
import hashlib
import json

from . import permission_shipments_catalog as previous, permission_catalog as c
from .permission_snapshot import CatalogBundle

SOURCE_COMMIT = 'c10c31baa61b5c09f6bdaf95ca8bd2d8f770ded0'
_OWNER = 'bookflow.commands.reconcile_cmds.{}|src/bookflow/commands/reconcile_cmds.py'
STANDALONE = ('agent run',)
ADDED_COMMANDS = (
    c.CommandDescriptor('owner-link', 'hub', 'init', 'authenticated', (), True, None, True, True,
                        'the local operating-system login, which must be the installation\'s owner',
                        None, None, None),
    c.CommandDescriptor('reconcile discard', 'company', 'ledger.post', 'standard', (), True, None, False, False,
                        None, None, None, None),
)
ADDED_ACTIONS = (
    c.CompanyAction('reconcile discard', (c.Requirement('ledger.post', 'standard'),), True,
                    (_OWNER.format('_discard_planner'),)),
)
CATALOG = replace(previous.CATALOG, version=c.SETUP_TRIAL_POLICY_VERSION,
    commands=tuple(sorted((*previous.CATALOG.commands, *ADDED_COMMANDS), key=lambda x: x.name)),
    company_actions=tuple(sorted((*previous.CATALOG.company_actions, *ADDED_ACTIONS), key=lambda x: x.key)))
MANIFEST = c.catalog_manifest(CATALOG, tuple(sorted((*previous.MANIFEST.standalone_names, *STANDALONE))))


def catalog_bundle():
    inventory = dict(manifest=asdict(MANIFEST), sources=[asdict(x) for x in CATALOG.conditional_sources])
    digest = hashlib.sha256(json.dumps(inventory, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return CatalogBundle(SOURCE_COMMIT, CATALOG, MANIFEST.standalone_names, digest)
