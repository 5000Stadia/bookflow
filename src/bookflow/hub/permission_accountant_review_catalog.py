"""Add the accountant review commands (R121) without rewriting any accepted descriptor.

Fourteen company commands on existing capabilities. Designating an external accountant, removing
one, cancelling an accountant's copy and importing an accountant's changes are `company` `admin`
and a person's acts (human-only admin actions under `admin:people-only:`): who can change the books
and taking a change file into them are the owner's calls. Exporting a copy, writing a copy's change
file and previewing a change file are `company` `admin`; reading the copy's status and the list of
accountants are `company` `member`. Reclassifying and the accountant's two marks are `ledger.post`
`standard` (the command itself admits only a designated accountant, or work in a copy). The review
checklist and the changes report are `reports` `member`. No capability, role default or existing
threshold changes, so a root replacing its catalog with this one suspends no agent and revokes no token.
"""
from dataclasses import asdict, replace
import hashlib
import json

from . import permission_shortfall_po_catalog as previous, permission_catalog as c
from . import permission_human_admin_catalog as human_admin
from .permission_snapshot import CatalogBundle

SOURCE_COMMIT = '7759da73bb6d955dfe80443370094a984d963de8'
_OWNER = 'bookflow.commands.accountant_cmds.{}|src/bookflow/commands/accountant_cmds.py'
# (name, capability, threshold, planner)
_ADDED = (
    ('accountant add', 'company', 'admin', 'plan_accountant_add'),
    ('accountant list', 'company', 'member', 'plan_accountant_list'),
    ('accountant mark-entry', 'ledger.post', 'standard', 'plan_accountant_mark_entry'),
    ('accountant reclassify', 'ledger.post', 'standard', 'plan_accountant_reclassify'),
    ('accountant remove', 'company', 'admin', 'plan_accountant_remove'),
    ('accountant reviewed-through', 'ledger.post', 'standard', 'plan_accountant_reviewed_through'),
    ('accountant-copy cancel', 'company', 'admin', 'plan_copy_cancel'),
    ('accountant-copy changes', 'company', 'admin', 'plan_copy_changes'),
    ('accountant-copy export', 'company', 'admin', 'plan_copy_export'),
    ('accountant-copy import', 'company', 'admin', 'plan_copy_import'),
    ('accountant-copy preview', 'company', 'admin', 'plan_copy_preview'),
    ('accountant-copy status', 'company', 'member', 'plan_copy_status'),
    ('report accountant-changes', 'reports', 'member', 'plan_accountant_changes'),
    ('report accountant-review', 'reports', 'member', 'plan_review_checklist'),
)
PEOPLE_ONLY_COMMANDS = ('accountant add', 'accountant remove', 'accountant-copy cancel', 'accountant-copy import')

ADDED_COMMANDS = tuple(
    c.CommandDescriptor(name, 'company', capability, threshold, (), True, None, False, False, None, None, None, None)
    for name, capability, threshold, _ in _ADDED)
ADDED_ACTIONS = tuple(
    c.CompanyAction(name, (c.Requirement(capability, threshold),), True, (_OWNER.format(planner),))
    for name, capability, threshold, planner in _ADDED)
ADDED_ADMIN_ACTIONS = tuple(c.AdminAction(human_admin.PEOPLE_ONLY_PREFIX + name, 'company', 'admin', True, True)
                            for name in PEOPLE_ONLY_COMMANDS)
CATALOG = replace(previous.CATALOG, version=c.ACCOUNTANT_REVIEW_POLICY_VERSION,
    commands=tuple(sorted((*previous.CATALOG.commands, *ADDED_COMMANDS), key=lambda x: x.name)),
    company_actions=tuple(sorted((*previous.CATALOG.company_actions, *ADDED_ACTIONS), key=lambda x: x.key)),
    admin_actions=tuple(sorted((*previous.CATALOG.admin_actions, *ADDED_ADMIN_ACTIONS), key=lambda x: x.key)))
MANIFEST = c.catalog_manifest(CATALOG, previous.MANIFEST.standalone_names)


def catalog_bundle():
    inventory = dict(manifest=asdict(MANIFEST), sources=[asdict(x) for x in CATALOG.conditional_sources])
    digest = hashlib.sha256(json.dumps(inventory, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return CatalogBundle(SOURCE_COMMIT, CATALOG, MANIFEST.standalone_names, digest)
