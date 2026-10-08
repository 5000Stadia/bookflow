"""Add `reconcile import` without rewriting any accepted permission descriptor.

One company command that reads a bank statement file against an account and may start and tick
a statement reconciliation, admitted exactly as `reconcile start` and `reconcile mark` are -- the
`ledger.post` capability at `standard` -- with one company action carrying the planner that runs
it. No capability, role default, admin action or existing threshold changes, so a root replacing
its catalog with this one suspends no agent and revokes no token, and a root activated at the
previous catalog admits the command through the tip without re-activation.
"""
from dataclasses import asdict, replace
import hashlib
import json

from . import permission_audit_visibility_catalog as previous, permission_catalog as c
from .permission_snapshot import CatalogBundle

SOURCE_COMMIT = '22881382ffeb09d2e438dc984b27747ae28a080e'
ADDED_COMMANDS = (
    c.CommandDescriptor('reconcile import', 'company', 'ledger.post', 'standard', (), True, None, False, False, None, None, None, None),
)
ADDED_ACTIONS = (
    c.CompanyAction('reconcile import', (c.Requirement('ledger.post', 'standard'),), True,
                    ('bookflow.commands.reconcile_import_cmds._planner|src/bookflow/commands/reconcile_import_cmds.py',)),
)
CATALOG = replace(previous.CATALOG, version=c.STATEMENT_IMPORT_POLICY_VERSION,
    commands=tuple(sorted((*previous.CATALOG.commands, *ADDED_COMMANDS), key=lambda x: x.name)),
    company_actions=tuple(sorted((*previous.CATALOG.company_actions, *ADDED_ACTIONS), key=lambda x: x.key)))
MANIFEST = c.catalog_manifest(CATALOG, previous.MANIFEST.standalone_names)


def catalog_bundle():
    inventory = dict(manifest=asdict(MANIFEST), sources=[asdict(x) for x in CATALOG.conditional_sources])
    digest = hashlib.sha256(json.dumps(inventory, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return CatalogBundle(SOURCE_COMMIT, CATALOG, MANIFEST.standalone_names, digest)
