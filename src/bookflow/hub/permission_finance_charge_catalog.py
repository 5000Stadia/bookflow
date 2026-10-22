"""Add `finance-charge assess` without rewriting any accepted descriptor.

It is admitted at `ledger.post` at `standard`, as `statement-charge post` is: every charge it writes is
an ordinary statement charge, and its dry run is the preview. It carries one company action naming its
planner. No capability, role default, admin action or existing threshold changes, so a root replacing
its catalog with this one suspends no agent and revokes no token.
"""
from dataclasses import asdict, replace
import hashlib
import json

from . import permission_item_convert_catalog as previous, permission_catalog as c
from .permission_snapshot import CatalogBundle

SOURCE_COMMIT = '2850baf1c26f1a752ef5d5e34cb2ffdfbb319a86'
ADDED_COMMANDS = (
    c.CommandDescriptor('finance-charge assess', 'company', 'ledger.post', 'standard', (), True, None, False, False, None, None, None, None),
)
ADDED_ACTIONS = (
    c.CompanyAction('finance-charge assess', (c.Requirement('ledger.post', 'standard'),), True,
                    ('bookflow.commands.finance_charge_cmds.plan_assess|src/bookflow/commands/finance_charge_cmds.py',)),
)
CATALOG = replace(previous.CATALOG, version=c.FINANCE_CHARGE_POLICY_VERSION,
    commands=tuple(sorted((*previous.CATALOG.commands, *ADDED_COMMANDS), key=lambda x: x.name)),
    company_actions=tuple(sorted((*previous.CATALOG.company_actions, *ADDED_ACTIONS), key=lambda x: x.key)))
MANIFEST = c.catalog_manifest(CATALOG, previous.MANIFEST.standalone_names)


def catalog_bundle():
    inventory = dict(manifest=asdict(MANIFEST), sources=[asdict(x) for x in CATALOG.conditional_sources])
    digest = hashlib.sha256(json.dumps(inventory, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return CatalogBundle(SOURCE_COMMIT, CATALOG, MANIFEST.standalone_names, digest)
