"""Add to-dos, overdue notices and the Reminders list (R111) without rewriting any accepted descriptor.

To-dos are notes with a due date and an assignee, so they are admitted as notes are: `note` `member` to read,
`note` `standard` to create, change or complete. Overdue notices are customer correspondence: `customer`
`member` to read, `customer` `standard` to generate, mark sent or dismiss. `reminders` reads the ledger:
`ledger.read` `member`. The scheduler's own entries are not a command and need no descriptor: each is
authorized as its owner's ordinary command. No capability, role default, admin action or existing threshold
changes, so a root replacing its catalog with this one suspends no agent and revokes no token.
"""
from dataclasses import asdict, replace
import hashlib
import json

from . import permission_inventory_counts_catalog as previous, permission_catalog as c
from .permission_snapshot import CatalogBundle

SOURCE_COMMIT = 'dcfe53c4460178de66ab86f61f95c512328109a5'
_OWNER = 'bookflow.commands.scheduling_cmds.{}|src/bookflow/commands/scheduling_cmds.py'
_ADDED = (
    ('notice dismiss', 'customer', 'standard', '_notice_write.<locals>.planner'),
    ('notice generate', 'customer', 'standard', 'plan_notice_generate'),
    ('notice list', 'customer', 'member', '_read.<locals>.planner'),
    ('notice mark-sent', 'customer', 'standard', '_notice_write.<locals>.planner'),
    ('notice show', 'customer', 'member', '_read.<locals>.planner'),
    ('reminders', 'ledger.read', 'member', '_read.<locals>.planner'),
    ('todo complete', 'note', 'standard', '_todo_write.<locals>.planner'),
    ('todo create', 'note', 'standard', '_todo_write.<locals>.planner'),
    ('todo list', 'note', 'member', '_read.<locals>.planner'),
    ('todo show', 'note', 'member', '_read.<locals>.planner'),
    ('todo update', 'note', 'standard', '_todo_write.<locals>.planner'),
)
ADDED_COMMANDS = tuple(
    c.CommandDescriptor(name, 'company', capability, threshold, (), True, None, False, False, None, None, None, None)
    for name, capability, threshold, _ in _ADDED)
ADDED_ACTIONS = tuple(
    c.CompanyAction(name, (c.Requirement(capability, threshold),), True, (_OWNER.format(planner),))
    for name, capability, threshold, planner in _ADDED)
# reminders asks, per heading, whether the reader's own list command would admit them, and hides
# the heading if not. That probe is a require_resource site of this delta, so it is declared here and
# never in the frozen ancestor (see tests/test_permission_catalog.py on why).
CURRENT_SOURCES = (*previous.CATALOG.conditional_sources,
    c.ResourceSource('bookflow.company.reminders._may',
        (('src/bookflow/company/reminders.py', 103),),
        tuple(c.Requirement(capability, 'member') for capability in ('customer', 'ledger.read', 'note', 'reports'))))
CATALOG = replace(previous.CATALOG, version=c.SCHEDULING_POLICY_VERSION,
    commands=tuple(sorted((*previous.CATALOG.commands, *ADDED_COMMANDS), key=lambda x: x.name)),
    company_actions=tuple(sorted((*previous.CATALOG.company_actions, *ADDED_ACTIONS), key=lambda x: x.key)),
    conditional_sources=tuple(sorted(CURRENT_SOURCES, key=lambda x: x.owner)))
MANIFEST = c.catalog_manifest(CATALOG, previous.MANIFEST.standalone_names)


def catalog_bundle():
    inventory = dict(manifest=asdict(MANIFEST), sources=[asdict(x) for x in CATALOG.conditional_sources])
    digest = hashlib.sha256(json.dumps(inventory, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return CatalogBundle(SOURCE_COMMIT, CATALOG, MANIFEST.standalone_names, digest)
