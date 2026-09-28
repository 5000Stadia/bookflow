"""Add company backup and restore without rewriting any accepted permission descriptor.

`company backup` writes a verified archive of the selected company and is admitted as the
company's other administrative writes are: the `company` capability at `admin`, with one company
action carrying the planner that runs it. `company restore` makes a new company from an archive
read off the host's disk, and is admitted as `company attach` is: installation administrators
only. No capability, role default, admin action or existing threshold changes, so a root
replacing its catalog with this one suspends no agent and revokes no token, and a root activated
at the previous catalog admits these commands through the tip without re-activation.
"""
from dataclasses import asdict, replace
import hashlib
import json

from . import permission_card_credit_catalog as previous, permission_catalog as c
from .permission_snapshot import CatalogBundle

SOURCE_COMMIT = '7859a628ed0f1ff481feaace670fb639508a8840'
ADDED_COMMANDS = (
    c.CommandDescriptor('company backup', 'company', 'company', 'admin', (), True, None, False, False, None, None, None, None),
    c.CommandDescriptor('company restore', 'hub', 'company', 'hub_admin', (), True, None, False, False, None, None, None, None),
)
ADDED_ACTIONS = (
    c.CompanyAction('company backup', (c.Requirement('company', 'admin'),), True,
                    ('bookflow.commands.backup_cmds.plan_company_backup|src/bookflow/commands/backup_cmds.py',)),
)
CATALOG = replace(previous.CATALOG, version=c.BACKUP_POLICY_VERSION,
    commands=tuple(sorted((*previous.CATALOG.commands, *ADDED_COMMANDS), key=lambda x: x.name)),
    company_actions=tuple(sorted((*previous.CATALOG.company_actions, *ADDED_ACTIONS), key=lambda x: x.key)))
MANIFEST = c.catalog_manifest(CATALOG, previous.MANIFEST.standalone_names)


def catalog_bundle():
    inventory = dict(manifest=asdict(MANIFEST), sources=[asdict(x) for x in CATALOG.conditional_sources])
    digest = hashlib.sha256(json.dumps(inventory, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return CatalogBundle(SOURCE_COMMIT, CATALOG, MANIFEST.standalone_names, digest)
