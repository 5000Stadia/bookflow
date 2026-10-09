"""Add `customer merge`, `customer unmerge`, `vendor merge` and `vendor unmerge` without rewriting any accepted descriptor.

Each is admitted at its own list's capability (`customer` or `vendor`) at `standard`, as
`customer link-vendor` is, and each command itself refuses an agent: merging two entries is a
person's call. Each command carries one company action naming its planner. No capability,
role default, admin action or existing threshold changes, so a root replacing its catalog with
this one suspends no agent and revokes no token.
"""
from dataclasses import asdict, replace
import hashlib
import json

from . import permission_cutover_catalog as previous, permission_catalog as c
from .permission_snapshot import CatalogBundle

SOURCE_COMMIT = 'f38f3f927fd92b7ff25732472b3150b95cf40bcb'
_MODULE = 'bookflow.commands.merge_cmds'
_FILE = 'src/bookflow/commands/merge_cmds.py'
ADDED_COMMANDS = tuple(
    c.CommandDescriptor(f'{kind} {verb}', 'company', kind, 'standard', (), True, None, False, False, None, None, None, None)
    for kind in ('customer', 'vendor') for verb in ('merge', 'unmerge'))
ADDED_ACTIONS = tuple(
    c.CompanyAction(f'{kind} {verb}', (c.Requirement(kind, 'standard'),), True,
                    (f'{_MODULE}._commands.<locals>.plan_{verb}|{_FILE}',))
    for kind in ('customer', 'vendor') for verb in ('merge', 'unmerge'))
CATALOG = replace(previous.CATALOG, version=c.PARTY_MERGE_POLICY_VERSION,
    commands=tuple(sorted((*previous.CATALOG.commands, *ADDED_COMMANDS), key=lambda x: x.name)),
    company_actions=tuple(sorted((*previous.CATALOG.company_actions, *ADDED_ACTIONS), key=lambda x: x.key)))
MANIFEST = c.catalog_manifest(CATALOG, previous.MANIFEST.standalone_names)


def catalog_bundle():
    inventory = dict(manifest=asdict(MANIFEST), sources=[asdict(x) for x in CATALOG.conditional_sources])
    digest = hashlib.sha256(json.dumps(inventory, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return CatalogBundle(SOURCE_COMMIT, CATALOG, MANIFEST.standalone_names, digest)
