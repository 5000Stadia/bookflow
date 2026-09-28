"""Add `report export` without rewriting any accepted permission descriptor.

One read-only company command, admitted exactly as every report is -- the `reports` capability
at `member` -- with one company action carrying the planner that runs it. No capability, role
default, admin action or existing threshold changes, so a root replacing its catalog with this
one suspends no agent and revokes no token, and a root activated at the previous catalog admits
the command through the tip without re-activation.
"""
from dataclasses import asdict, replace
import hashlib
import json

from . import permission_deactivation_catalog as previous, permission_catalog as c
from .permission_snapshot import CatalogBundle

SOURCE_COMMIT = '5ce7f18db0ad181cc31e52b938af6d8998a70f21'
ADDED_COMMANDS = (
    c.CommandDescriptor('report export', 'company', 'reports', 'member', (), True, None, False, False, None, None, None, None),
)
ADDED_ACTIONS = (
    c.CompanyAction('report export', (c.Requirement('reports', 'member'),), True,
                    ('bookflow.commands.report_export_cmds.plan_report_export|src/bookflow/commands/report_export_cmds.py',)),
)
CATALOG = replace(previous.CATALOG, version=c.REPORT_EXPORT_POLICY_VERSION,
    commands=tuple(sorted((*previous.CATALOG.commands, *ADDED_COMMANDS), key=lambda x: x.name)),
    company_actions=tuple(sorted((*previous.CATALOG.company_actions, *ADDED_ACTIONS), key=lambda x: x.key)))
MANIFEST = c.catalog_manifest(CATALOG, previous.MANIFEST.standalone_names)


def catalog_bundle():
    inventory = dict(manifest=asdict(MANIFEST), sources=[asdict(x) for x in CATALOG.conditional_sources])
    digest = hashlib.sha256(json.dumps(inventory, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return CatalogBundle(SOURCE_COMMIT, CATALOG, MANIFEST.standalone_names, digest)
