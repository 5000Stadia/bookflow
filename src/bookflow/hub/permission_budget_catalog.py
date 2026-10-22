"""Add budgets, the budget and comparison reports, and memorized reports without rewriting any accepted descriptor.

R109. Budget writes are admitted at `account` `standard`, as editing the chart's own lists is: a budget is a
list-like plan per account that never posts. Budget reads and the four new reports are `reports` `member`, as
every report is. Memorized reports are `reports` `member` for reading and writing, as the anchor lets anyone who
can run a report memorize it; running one runs the report command itself, under that report's own permission.
No capability, role default, admin action or existing threshold changes, so a root replacing its catalog with
this one suspends no agent and revokes no token.
"""
from dataclasses import asdict, replace
import hashlib
import json

from . import permission_finance_charge_catalog as previous, permission_catalog as c
from .permission_snapshot import CatalogBundle

SOURCE_COMMIT = '027571231ddc873f057783dd52af773867ad5277'
_OWNER = 'bookflow.commands.budget_cmds.{}|src/bookflow/commands/budget_cmds.py'
_ADDED = (
    ('budget clear', 'account', 'standard', 'plan_budget_clear'),
    ('budget create', 'account', 'standard', 'plan_budget_create'),
    ('budget delete', 'account', 'standard', 'plan_budget_delete'),
    ('budget fill', 'account', 'standard', 'plan_budget_fill'),
    ('budget list', 'reports', 'member', 'plan_budget_list'),
    ('budget rename', 'account', 'standard', 'plan_budget_rename'),
    ('budget set', 'account', 'standard', 'plan_budget_set'),
    ('budget show', 'reports', 'member', 'plan_budget_show'),
    ('memorized-report create', 'reports', 'member', 'plan_memorized_create'),
    ('memorized-report delete', 'reports', 'member', 'plan_memorized_delete'),
    ('memorized-report list', 'reports', 'member', 'plan_memorized_list'),
    ('memorized-report run', 'reports', 'member', 'plan_memorized_run'),
    ('memorized-report show', 'reports', 'member', 'plan_memorized_show'),
    ('memorized-report update', 'reports', 'member', 'plan_memorized_update'),
    ('report balance-sheet-comparison', 'reports', 'member', 'plan_balance_sheet_comparison'),
    ('report budget-overview', 'reports', 'member', 'plan_budget_overview'),
    ('report budget-vs-actual', 'reports', 'member', 'plan_budget_vs_actual'),
    ('report profit-and-loss-comparison', 'reports', 'member', 'plan_profit_and_loss_comparison'),
)
ADDED_COMMANDS = tuple(
    c.CommandDescriptor(name, 'company', capability, threshold, (), True, None, False, False, None, None, None, None)
    for name, capability, threshold, _ in _ADDED)
ADDED_ACTIONS = tuple(
    c.CompanyAction(name, (c.Requirement(capability, threshold),), True, (_OWNER.format(planner),))
    for name, capability, threshold, planner in _ADDED)
CATALOG = replace(previous.CATALOG, version=c.BUDGET_POLICY_VERSION,
    commands=tuple(sorted((*previous.CATALOG.commands, *ADDED_COMMANDS), key=lambda x: x.name)),
    company_actions=tuple(sorted((*previous.CATALOG.company_actions, *ADDED_ACTIONS), key=lambda x: x.key)))
MANIFEST = c.catalog_manifest(CATALOG, previous.MANIFEST.standalone_names)


def catalog_bundle():
    inventory = dict(manifest=asdict(MANIFEST), sources=[asdict(x) for x in CATALOG.conditional_sources])
    digest = hashlib.sha256(json.dumps(inventory, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return CatalogBundle(SOURCE_COMMIT, CATALOG, MANIFEST.standalone_names, digest)
