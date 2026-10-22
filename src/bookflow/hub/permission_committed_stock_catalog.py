"""Add `report open-sales-orders` (R102, committed stock) without rewriting any accepted descriptor.

One company command at `reports` member, as the other reports are. No capability, role default or
existing threshold changes, so a root replacing its catalog with this one suspends no agent and
revokes no token.
"""
from dataclasses import asdict, replace
import hashlib
import json

from . import permission_assembly_build_catalog as previous, permission_catalog as c
from .permission_snapshot import CatalogBundle

SOURCE_COMMIT = '5c492ae2f6c1fc166a6f593a447977e6dc17c6ad'
ADDED_COMMANDS = (
    c.CommandDescriptor('report open-sales-orders', 'company', 'reports', 'member', (), True, None, False, False,
                        None, None, None, None),
)
ADDED_ACTIONS = (
    c.CompanyAction('report open-sales-orders', (c.Requirement('reports', 'member'),), True,
                    ('bookflow.commands.report_cmds.plan_open_sales_orders|src/bookflow/commands/report_cmds.py',)),
)
CATALOG = replace(previous.CATALOG, version=c.COMMITTED_STOCK_POLICY_VERSION,
    commands=tuple(sorted((*previous.CATALOG.commands, *ADDED_COMMANDS), key=lambda x: x.name)),
    company_actions=tuple(sorted((*previous.CATALOG.company_actions, *ADDED_ACTIONS), key=lambda x: x.key)))
MANIFEST = c.catalog_manifest(CATALOG, previous.MANIFEST.standalone_names)


def catalog_bundle():
    inventory = dict(manifest=asdict(MANIFEST), sources=[asdict(x) for x in CATALOG.conditional_sources])
    digest = hashlib.sha256(json.dumps(inventory, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return CatalogBundle(SOURCE_COMMIT, CATALOG, MANIFEST.standalone_names, digest)
