"""Add timekeeping (R106) without rewriting any accepted descriptor (chained on partial-po-v1).

Fifteen company commands, all at `customer-work`, as recorded time is: weekly timesheets
(`timesheet show`, `copy-last`, `save`), the time clock (`clock in`, `out`, `cancel`, `status`,
`link`) and the job schedule (`schedule create`, `update`, `cancel`, `show`, `calendar`) are
`standard` to write and `member` to read, the preferences included, as price-rule preferences
are. `clock link`
says which person's login is which employee -- what lets an agent clock a person only on that
person's own instruction -- so it is a person's act, carried as one people-only admin action.
No capability, role default or existing threshold changes, so a root replacing its catalog with
this one suspends no agent and revokes no token.
"""
from dataclasses import asdict, replace
import hashlib
import json

from . import permission_partial_po_catalog as previous, permission_catalog as c
from . import permission_human_admin_catalog as human_admin
from .permission_snapshot import CatalogBundle

SOURCE_COMMIT = '0d031f4f5c4dac42361ab96a76016b21d10fc4a7'
_OWNER = 'bookflow.commands.timekeeping_cmds.{}|src/bookflow/commands/timekeeping_cmds.py'
# (name, threshold, planner)
_ADDED = (
    ('clock cancel', 'standard', 'plan_clock_cancel'),
    ('clock in', 'standard', 'plan_clock_in'),
    ('clock link', 'standard', 'plan_clock_link'),
    ('clock out', 'standard', 'plan_clock_out'),
    ('clock status', 'member', 'plan_clock_status'),
    ('schedule calendar', 'member', 'plan_schedule_calendar'),
    ('schedule cancel', 'standard', 'plan_schedule_cancel'),
    ('schedule create', 'standard', 'plan_schedule_create'),
    ('schedule show', 'member', 'plan_schedule_show'),
    ('schedule update', 'standard', 'plan_schedule_update'),
    ('timesheet copy-last', 'member', 'plan_timesheet_copy_last'),
    ('timesheet preferences', 'member', 'plan_timesheet_preferences'),
    ('timesheet save', 'standard', 'plan_timesheet_save'),
    ('timesheet set-preferences', 'standard', 'plan_timesheet_set_preferences'),
    ('timesheet show', 'member', 'plan_timesheet_show'),
)
ADDED_COMMANDS = tuple(
    c.CommandDescriptor(name, 'company', 'customer-work', threshold, (), True, None, False, False, None, None, None, None)
    for name, threshold, _ in _ADDED)
ADDED_ACTIONS = tuple(
    c.CompanyAction(name, (c.Requirement('customer-work', threshold),), True, (_OWNER.format(planner),))
    for name, threshold, planner in _ADDED)
ADDED_ADMIN_ACTIONS = (c.AdminAction(human_admin.PEOPLE_ONLY_PREFIX + 'clock link', 'company', 'standard', True, True),)
CATALOG = replace(previous.CATALOG, version=c.TIMESHEETS_POLICY_VERSION,
    commands=tuple(sorted((*previous.CATALOG.commands, *ADDED_COMMANDS), key=lambda x: x.name)),
    company_actions=tuple(sorted((*previous.CATALOG.company_actions, *ADDED_ACTIONS), key=lambda x: x.key)),
    admin_actions=tuple(sorted((*previous.CATALOG.admin_actions, *ADDED_ADMIN_ACTIONS), key=lambda x: x.key)))
MANIFEST = c.catalog_manifest(CATALOG, previous.MANIFEST.standalone_names)


def catalog_bundle():
    inventory = dict(manifest=asdict(MANIFEST), sources=[asdict(x) for x in CATALOG.conditional_sources])
    digest = hashlib.sha256(json.dumps(inventory, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return CatalogBundle(SOURCE_COMMIT, CATALOG, MANIFEST.standalone_names, digest)
