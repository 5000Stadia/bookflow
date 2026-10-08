"""Add scheduled backups, backup verification and the restore rehearsal without rewriting any accepted descriptor.

`backup schedule` changes a company's backup schedule, which is host configuration, and is
admitted as `company restore` is: installation administrators only (and the command itself
refuses an agent). `backup list`, `backup verify` and `backup rehearse` read the company's
backups and audit trail and are admitted as `company backup` is: the `company` capability at
`admin`, each with one company action carrying its planner. No capability, role default, admin
action or existing threshold changes, so a root replacing its catalog with this one suspends no
agent and revokes no token, and a root activated at the previous catalog admits these commands
through the tip without re-activation.
"""
from dataclasses import asdict, replace
import hashlib
import json

from . import permission_audit_visibility_catalog as previous, permission_catalog as c
from .permission_snapshot import CatalogBundle

SOURCE_COMMIT = 'ac3de3f1318b96dd4ea4a35bfaf6795201954550'
_MODULE = 'bookflow.commands.backup_schedule_cmds'
_FILE = 'src/bookflow/commands/backup_schedule_cmds.py'
ADDED_COMMANDS = (
    c.CommandDescriptor('backup list', 'company', 'company', 'admin', (), True, None, False, False, None, None, None, None),
    c.CommandDescriptor('backup rehearse', 'company', 'company', 'admin', (), True, None, False, False, None, None, None, None),
    c.CommandDescriptor('backup schedule', 'hub', 'company', 'hub_admin', (), True, None, False, False, None, None, None, None),
    c.CommandDescriptor('backup verify', 'company', 'company', 'admin', (), True, None, False, False, None, None, None, None),
)
ADDED_ACTIONS = tuple(
    c.CompanyAction(f'backup {verb}', (c.Requirement('company', 'admin'),), True,
                    (f'{_MODULE}.plan_backup_{verb}|{_FILE}',))
    for verb in ('list', 'rehearse', 'verify'))
CATALOG = replace(previous.CATALOG, version=c.BACKUP_SCHEDULE_POLICY_VERSION,
    commands=tuple(sorted((*previous.CATALOG.commands, *ADDED_COMMANDS), key=lambda x: x.name)),
    company_actions=tuple(sorted((*previous.CATALOG.company_actions, *ADDED_ACTIONS), key=lambda x: x.key)))
MANIFEST = c.catalog_manifest(CATALOG, previous.MANIFEST.standalone_names)


def catalog_bundle():
    inventory = dict(manifest=asdict(MANIFEST), sources=[asdict(x) for x in CATALOG.conditional_sources])
    digest = hashlib.sha256(json.dumps(inventory, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return CatalogBundle(SOURCE_COMMIT, CATALOG, MANIFEST.standalone_names, digest)
