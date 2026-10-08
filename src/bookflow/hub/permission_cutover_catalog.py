"""Add `cutover plan`, `cutover apply` and `cutover tie-out` without rewriting any accepted descriptor.

`cutover plan` reads at `ledger.read` and `cutover tie-out` at `reports`, both at `member`;
`cutover apply` posts at `ledger.post` at `standard`, as `memorized enter` does, and each write it
makes runs through its own command, which admits it again on its own capability. Each command
carries one company action naming its planner. No capability, role default, admin action or
existing threshold changes, so a root replacing its catalog with this one suspends no agent and
revokes no token.
"""
from dataclasses import asdict, replace
import hashlib
import json

from . import permission_audit_visibility_catalog as previous, permission_catalog as c
from .permission_snapshot import CatalogBundle

SOURCE_COMMIT = '22881382ffeb09d2e438dc984b27747ae28a080e'
ADDED_COMMANDS = (
    c.CommandDescriptor('cutover apply', 'company', 'ledger.post', 'standard', (), True, None, False, False, None, None, None, None),
    c.CommandDescriptor('cutover plan', 'company', 'ledger.read', 'member', (), True, None, False, False, None, None, None, None),
    c.CommandDescriptor('cutover tie-out', 'company', 'reports', 'member', (), True, None, False, False, None, None, None, None),
)
ADDED_ACTIONS = (
    c.CompanyAction('cutover apply', (c.Requirement('ledger.post', 'standard'),), True,
                    ('bookflow.commands.cutover_cmds._apply.<locals>.planner|src/bookflow/commands/cutover_cmds.py',)),
    c.CompanyAction('cutover plan', (c.Requirement('ledger.read', 'member'),), True,
                    ('bookflow.commands.cutover_cmds.plan_cutover|src/bookflow/commands/cutover_cmds.py',)),
    c.CompanyAction('cutover tie-out', (c.Requirement('reports', 'member'),), True,
                    ('bookflow.commands.cutover_cmds.tie_out_cutover|src/bookflow/commands/cutover_cmds.py',)),
)
CATALOG = replace(previous.CATALOG, version=c.CUTOVER_POLICY_VERSION,
    commands=tuple(sorted((*previous.CATALOG.commands, *ADDED_COMMANDS), key=lambda x: x.name)),
    company_actions=tuple(sorted((*previous.CATALOG.company_actions, *ADDED_ACTIONS), key=lambda x: x.key)))
MANIFEST = c.catalog_manifest(CATALOG, previous.MANIFEST.standalone_names)


def catalog_bundle():
    inventory = dict(manifest=asdict(MANIFEST), sources=[asdict(x) for x in CATALOG.conditional_sources])
    digest = hashlib.sha256(json.dumps(inventory, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return CatalogBundle(SOURCE_COMMIT, CATALOG, MANIFEST.standalone_names, digest)
