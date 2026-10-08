"""Add the owner's review reports and `review mark` without rewriting any accepted descriptor.

`report entries-to-review` and `report prior-balances` are read-only company reports, admitted
exactly as every report is -- the `reports` capability at `member`. `review mark` records that the
owner looked at a listed entry; it writes only an audit event and is admitted as the closing date
is, the `company` capability at `admin` (the command also refuses an agent). No capability, role
default, admin action or existing threshold changes, so a root replacing its catalog with this
one suspends no agent and revokes no token.
"""
from dataclasses import asdict, replace
import hashlib
import json

from . import permission_backup_schedule_catalog as previous, permission_catalog as c
from .permission_snapshot import CatalogBundle

SOURCE_COMMIT = '0750a0f8680cacc4c7bd065494fef196a3b5bcc4'
REPORTS = {
    'entries-to-review': 'plan_entries_to_review',
    'prior-balances': 'plan_prior_balances',
}
ADDED_COMMANDS = (
    *(c.CommandDescriptor('report ' + verb, 'company', 'reports', 'member', (), True, None, False, False, None, None, None, None)
      for verb in sorted(REPORTS)),
    c.CommandDescriptor('review mark', 'company', 'company', 'admin', (), True, None, False, False, None, None, None, None),
)
ADDED_ACTIONS = (
    *(c.CompanyAction('report ' + verb, (c.Requirement('reports', 'member'),), True,
                      (f'bookflow.commands.report_cmds.{planner}|src/bookflow/commands/report_cmds.py',))
      for verb, planner in sorted(REPORTS.items())),
    c.CompanyAction('review mark', (c.Requirement('company', 'admin'),), True,
                    ('bookflow.commands.review_cmds.plan_review_mark|src/bookflow/commands/review_cmds.py',)),
)
CATALOG = replace(previous.CATALOG, version=c.ENTRY_REVIEW_POLICY_VERSION,
    commands=tuple(sorted((*previous.CATALOG.commands, *ADDED_COMMANDS), key=lambda x: x.name)),
    company_actions=tuple(sorted((*previous.CATALOG.company_actions, *ADDED_ACTIONS), key=lambda x: x.key)))
MANIFEST = c.catalog_manifest(CATALOG, previous.MANIFEST.standalone_names)


def catalog_bundle():
    inventory = dict(manifest=asdict(MANIFEST), sources=[asdict(x) for x in CATALOG.conditional_sources])
    digest = hashlib.sha256(json.dumps(inventory, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return CatalogBundle(SOURCE_COMMIT, CATALOG, MANIFEST.standalone_names, digest)
