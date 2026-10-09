"""Add Adjust Sales Tax Due without rewriting any accepted permission descriptor.

`sales-tax adjust` and `sales-tax adjustment void` post at `ledger.post` at `standard`, as
`sales-tax pay` and `sales-tax payment void` do; `sales-tax adjustment show` and `query` read at
`ledger.read` at `member`, as the remittance reads do. Each command carries one company action
naming its planner. No capability, role default, admin action or existing threshold changes, so
a root replacing its catalog with this one suspends no agent and revokes no token. The people-only
admin actions of the layer below are carried unchanged.
"""
from dataclasses import asdict, replace
import hashlib
import json

from . import permission_human_admin_catalog as previous, permission_catalog as c
from .permission_snapshot import CatalogBundle

SOURCE_COMMIT = '23e5ec538cc24e46fe5f6ab54ca3dd61463f4cd8'
_MODULE = 'src/bookflow/commands/sales_tax_cmds.py'
_WRITE = 'bookflow.commands.sales_tax_cmds._adjustment_write.<locals>.planner|' + _MODULE
_READ = 'bookflow.commands.sales_tax_cmds._adjustment_read.<locals>.planner|' + _MODULE
ADDED_COMMANDS = (
    c.CommandDescriptor('sales-tax adjust', 'company', 'ledger.post', 'standard', (), True, None, False, False, None, None, None, None),
    c.CommandDescriptor('sales-tax adjustment query', 'company', 'ledger.read', 'member', (), True, None, False, False, None, None, None, None),
    c.CommandDescriptor('sales-tax adjustment show', 'company', 'ledger.read', 'member', (), True, None, False, False, None, None, None, None),
    c.CommandDescriptor('sales-tax adjustment void', 'company', 'ledger.post', 'standard', (), True, None, False, False, None, None, None, None),
)
ADDED_ACTIONS = (
    c.CompanyAction('sales-tax adjust', (c.Requirement('ledger.post', 'standard'),), True, (_WRITE,)),
    c.CompanyAction('sales-tax adjustment query', (c.Requirement('ledger.read', 'member'),), True, (_READ,)),
    c.CompanyAction('sales-tax adjustment show', (c.Requirement('ledger.read', 'member'),), True, (_READ,)),
    c.CompanyAction('sales-tax adjustment void', (c.Requirement('ledger.post', 'standard'),), True, (_WRITE,)),
)
CATALOG = replace(previous.CATALOG, version=c.SALES_TAX_ADJUSTMENT_POLICY_VERSION,
    commands=tuple(sorted((*previous.CATALOG.commands, *ADDED_COMMANDS), key=lambda x: x.name)),
    company_actions=tuple(sorted((*previous.CATALOG.company_actions, *ADDED_ACTIONS), key=lambda x: x.key)))
MANIFEST = c.catalog_manifest(CATALOG, previous.MANIFEST.standalone_names)


def catalog_bundle():
    inventory = dict(manifest=asdict(MANIFEST), sources=[asdict(x) for x in CATALOG.conditional_sources])
    digest = hashlib.sha256(json.dumps(inventory, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return CatalogBundle(SOURCE_COMMIT, CATALOG, MANIFEST.standalone_names, digest)
