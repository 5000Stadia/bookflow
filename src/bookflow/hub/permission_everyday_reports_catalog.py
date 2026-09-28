"""Add the everyday reports without rewriting any accepted permission descriptor.

Read-only company reports the anchor has and Bookflow lacked: customer and vendor balance
summary and detail, open purchase orders, purchases by vendor and by item, deposit detail,
the transaction list by date, and the 1099 vendor summary. Each is admitted exactly as every
other report is -- the `reports` capability at `member` -- with one company action per
command carrying the planner that runs it. No capability, role default, admin action or
existing threshold changes, so a root replacing its catalog with this one suspends no agent
and revokes no token, and a root activated at the previous catalog admits these commands
through the tip without re-activation, as it did the agent commands.
"""
from dataclasses import asdict, replace
import hashlib
import json

from . import permission_agent_catalog as previous, permission_catalog as c
from .permission_snapshot import CatalogBundle

SOURCE_COMMIT = '3686f4b55eda5b22ad2c11f70235d5f76ea3131a'
# Report verb -> the planner in report_cmds that runs it.
REPORTS = {
    'customer-balance-detail': 'plan_customer_balance_detail',
    'customer-balance-summary': 'plan_customer_balance_summary',
    'deposit-detail': 'plan_deposit_detail',
    'open-purchase-orders': 'plan_open_purchase_orders',
    'purchases-by-item': 'plan_purchases_by_item',
    'purchases-by-vendor': 'plan_purchases_by_vendor',
    'transaction-list-by-date': 'plan_transaction_list_by_date',
    'vendor-balance-detail': 'plan_vendor_balance_detail',
    'vendor-balance-summary': 'plan_vendor_balance_summary',
    'vendor-1099-summary': 'plan_vendor_1099_summary',
}
ADDED_COMMANDS = tuple(
    c.CommandDescriptor('report ' + verb, 'company', 'reports', 'member', (), True, None, False, False, None, None, None, None)
    for verb in sorted(REPORTS))
ADDED_ACTIONS = tuple(
    c.CompanyAction('report ' + verb, (c.Requirement('reports', 'member'),), True,
                    (f'bookflow.commands.report_cmds.{planner}|src/bookflow/commands/report_cmds.py',))
    for verb, planner in sorted(REPORTS.items()))
CATALOG = replace(previous.CATALOG, version=c.EVERYDAY_REPORTS_POLICY_VERSION,
    commands=tuple(sorted((*previous.CATALOG.commands, *ADDED_COMMANDS), key=lambda x: x.name)),
    company_actions=tuple(sorted((*previous.CATALOG.company_actions, *ADDED_ACTIONS), key=lambda x: x.key)))
MANIFEST = c.catalog_manifest(CATALOG, previous.MANIFEST.standalone_names)


def catalog_bundle():
    inventory = dict(manifest=asdict(MANIFEST), sources=[asdict(x) for x in CATALOG.conditional_sources])
    digest = hashlib.sha256(json.dumps(inventory, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return CatalogBundle(SOURCE_COMMIT, CATALOG, MANIFEST.standalone_names, digest)
