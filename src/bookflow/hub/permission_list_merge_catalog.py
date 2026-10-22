"""Add `account merge`, `account unmerge`, `item merge` and `item unmerge` without rewriting any accepted descriptor.

Each is admitted at its own list's capability (`account` or `item`) at `standard`, as the customer
and vendor merges are at theirs, and each command itself refuses an agent: merging two entries is a
person's call. Each command carries one company action naming its planner. No capability, role
default, admin action or existing threshold changes, so a root replacing its catalog with this one
suspends no agent and revokes no token.
"""
from dataclasses import asdict, replace
import hashlib
import json

from . import permission_customer_prepayment_catalog as previous, permission_catalog as c
from . import permission_human_admin_catalog as human_admin
from .permission_snapshot import CatalogBundle

SOURCE_COMMIT = '19d97a4e977a64f007311e86040022ba0d5b63da'
_MODULE = 'bookflow.commands.merge_cmds'
_FILE = 'src/bookflow/commands/merge_cmds.py'
ADDED_COMMANDS = tuple(
    c.CommandDescriptor(f'{kind} {verb}', 'company', kind, 'standard', (), True, None, False, False, None, None, None, None)
    for kind in ('account', 'item') for verb in ('merge', 'unmerge'))
ADDED_ACTIONS = tuple(
    c.CompanyAction(f'{kind} {verb}', (c.Requirement(kind, 'standard'),), True,
                    (f'{_MODULE}._list_commands.<locals>.plan_{verb}|{_FILE}',))
    for kind in ('account', 'item') for verb in ('merge', 'unmerge'))
ADDED_ADMIN_ACTIONS = tuple(c.AdminAction(human_admin.PEOPLE_ONLY_PREFIX + f'{kind} {verb}', 'company', 'standard', True, True)
                            for kind in ('account', 'item') for verb in ('merge', 'unmerge'))
CATALOG = replace(previous.CATALOG, version=c.LIST_MERGE_POLICY_VERSION,
    commands=tuple(sorted((*previous.CATALOG.commands, *ADDED_COMMANDS), key=lambda x: x.name)),
    company_actions=tuple(sorted((*previous.CATALOG.company_actions, *ADDED_ACTIONS), key=lambda x: x.key)),
    admin_actions=tuple(sorted((*previous.CATALOG.admin_actions, *ADDED_ADMIN_ACTIONS), key=lambda x: x.key)))
MANIFEST = c.catalog_manifest(CATALOG, previous.MANIFEST.standalone_names)


def catalog_bundle():
    inventory = dict(manifest=asdict(MANIFEST), sources=[asdict(x) for x in CATALOG.conditional_sources])
    digest = hashlib.sha256(json.dumps(inventory, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return CatalogBundle(SOURCE_COMMIT, CATALOG, MANIFEST.standalone_names, digest)
