"""Add fixed-asset depreciation and loans without rewriting any accepted descriptor.

R119. `fixed-asset setup` edits a fixed-asset item's depreciation terms (and the item's own accounts), so it is
admitted at `item` `standard`, as `item update` is. `fixed-asset depreciate`, `fixed-asset dispose` and `loan pay`
post journal entries and checks, so they are `ledger.post` `standard`, as `journal post` and `check post` are.
`loan create` and `loan update` keep a loan's terms, list-like setup on the chart's liability account, so they are
`account` `standard`, as budgets are. The schedules and lists read the ledger: `ledger.read` `member`. No capability,
role default, admin action or existing threshold changes, so a root replacing its catalog with this one suspends no
agent and revokes no token.
"""
from dataclasses import asdict, replace
import hashlib
import json

from . import permission_list_merge_catalog as previous, permission_catalog as c
from .permission_snapshot import CatalogBundle

SOURCE_COMMIT = 'a22094546064e52dd5fc31cd501aeccfd7809a73'
_OWNER = 'bookflow.commands.fixed_asset_cmds.{}|src/bookflow/commands/fixed_asset_cmds.py'
_ADDED = (
    ('fixed-asset depreciate', 'ledger.post', 'standard', 'plan_fixed_asset_depreciate'),
    ('fixed-asset dispose', 'ledger.post', 'standard', 'plan_fixed_asset_dispose'),
    ('fixed-asset list', 'ledger.read', 'member', 'plan_fixed_asset_list'),
    ('fixed-asset schedule', 'ledger.read', 'member', 'plan_fixed_asset_schedule'),
    ('fixed-asset setup', 'item', 'standard', 'plan_fixed_asset_setup'),
    ('loan create', 'account', 'standard', 'plan_loan_create'),
    ('loan list', 'ledger.read', 'member', 'plan_loan_list'),
    ('loan pay', 'ledger.post', 'standard', 'plan_loan_pay'),
    ('loan show', 'ledger.read', 'member', 'plan_loan_show'),
    ('loan update', 'account', 'standard', 'plan_loan_update'),
)
ADDED_COMMANDS = tuple(
    c.CommandDescriptor(name, 'company', capability, threshold, (), True, None, False, False, None, None, None, None)
    for name, capability, threshold, _ in _ADDED)
ADDED_ACTIONS = tuple(
    c.CompanyAction(name, (c.Requirement(capability, threshold),), True, (_OWNER.format(planner),))
    for name, capability, threshold, planner in _ADDED)
CATALOG = replace(previous.CATALOG, version=c.FIXED_ASSET_POLICY_VERSION,
    commands=tuple(sorted((*previous.CATALOG.commands, *ADDED_COMMANDS), key=lambda x: x.name)),
    company_actions=tuple(sorted((*previous.CATALOG.company_actions, *ADDED_ACTIONS), key=lambda x: x.key)))
MANIFEST = c.catalog_manifest(CATALOG, previous.MANIFEST.standalone_names)


def catalog_bundle():
    inventory = dict(manifest=asdict(MANIFEST), sources=[asdict(x) for x in CATALOG.conditional_sources])
    digest = hashlib.sha256(json.dumps(inventory, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return CatalogBundle(SOURCE_COMMIT, CATALOG, MANIFEST.standalone_names, digest)
