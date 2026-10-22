"""Add the assembly build commands without rewriting any accepted descriptor.

The five writes (`assembly build`, `update`, `finalize`, `void`, `delete`) are admitted at `ledger.post`
at `standard`, as `inventory adjust` and `inventory void` are: a build is an inventory document that
moves stock and posts through the same ledger, so it asks no more of a caller than an adjustment does
and no less. The two reads (`assembly show`, `assembly list`) are at `ledger.read` at `member`, as
`inventory show` is. Each carries one company action naming its planner. No capability, role default,
admin action or existing threshold changes, so a root replacing its catalog with this one suspends no
agent and revokes no token. Building an assembly is bookkeeping, not a person-only act.
"""
from dataclasses import asdict, replace
import hashlib
import json

from . import permission_sales_order_catalog as previous, permission_catalog as c
from .permission_snapshot import CatalogBundle

SOURCE_COMMIT = '5c492ae2f6c1fc166a6f593a447977e6dc17c6ad'
_PATH = 'src/bookflow/commands/assembly_cmds.py'
_WRITES = ('assembly build', 'assembly update', 'assembly finalize', 'assembly void', 'assembly delete')
_READS = ('assembly show', 'assembly list')
ADDED_COMMANDS = tuple(
    c.CommandDescriptor(name, 'company', 'ledger.post', 'standard', (), True, None, False, False, None, None, None, None)
    for name in _WRITES) + tuple(
    c.CommandDescriptor(name, 'company', 'ledger.read', 'member', (), True, None, False, False, None, None, None, None)
    for name in _READS)
ADDED_ACTIONS = tuple(
    c.CompanyAction(name, (c.Requirement('ledger.post', 'standard'),), True,
                    (f'bookflow.commands.assembly_cmds.plan_assembly_{name.split()[1]}|{_PATH}',))
    for name in _WRITES) + tuple(
    c.CompanyAction(name, (c.Requirement('ledger.read', 'member'),), True,
                    (f'bookflow.commands.assembly_cmds.plan_assembly_{name.split()[1]}|{_PATH}',))
    for name in _READS)
CATALOG = replace(previous.CATALOG, version=c.ASSEMBLY_BUILD_POLICY_VERSION,
    commands=tuple(sorted((*previous.CATALOG.commands, *ADDED_COMMANDS), key=lambda x: x.name)),
    company_actions=tuple(sorted((*previous.CATALOG.company_actions, *ADDED_ACTIONS), key=lambda x: x.key)))
MANIFEST = c.catalog_manifest(CATALOG, previous.MANIFEST.standalone_names)


def catalog_bundle():
    inventory = dict(manifest=asdict(MANIFEST), sources=[asdict(x) for x in CATALOG.conditional_sources])
    digest = hashlib.sha256(json.dumps(inventory, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return CatalogBundle(SOURCE_COMMIT, CATALOG, MANIFEST.standalone_names, digest)
