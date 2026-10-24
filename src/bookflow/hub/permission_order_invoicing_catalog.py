"""Add invoicing from sales orders (R101 increment 4) without rewriting any accepted descriptor.

`sales-order invoice` at `ledger.post` standard with `customer-work` standard checked too, as
`estimate invoice` is, and `sales-order billing` at `ledger.read` member with `customer-work` member.
No capability, role default or existing threshold changes.
"""
from dataclasses import asdict, replace
import hashlib
import json

from . import permission_pricing_rules_catalog as previous, permission_catalog as c
from .permission_snapshot import CatalogBundle

SOURCE_COMMIT = '2ac04ad24b0512dbbbf95ad30897a5f93bbe4990'
_OWNER = ('bookflow.commands.billing_cmds.register.<locals>.planner|src/bookflow/commands/billing_cmds.py',)
ADDED_COMMANDS = (
    c.CommandDescriptor(name='sales-order billing', routed_scope='company', capability='ledger.read', threshold='member', resources=(c.Requirement(capability='customer-work', threshold='member'),), available=True, feature=None, local_only=False, bootstrap=False, authorization_text='ledger and customer-work membership', conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    c.CommandDescriptor(name='sales-order invoice', routed_scope='company', capability='ledger.post', threshold='standard', resources=(c.Requirement(capability='customer-work', threshold='standard'),), available=True, feature=None, local_only=False, bootstrap=False, authorization_text='ledger.post and customer-work standard role', conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
)
ADDED_ACTIONS = (
    c.CompanyAction(key='sales-order billing', requirements=(c.Requirement(capability='customer-work', threshold='member'), c.Requirement(capability='ledger.read', threshold='member')), available=True, remaining_graph_owners=_OWNER),
    c.CompanyAction(key='sales-order invoice', requirements=(c.Requirement(capability='customer-work', threshold='standard'), c.Requirement(capability='ledger.post', threshold='standard')), available=True, remaining_graph_owners=_OWNER),
)
CATALOG = replace(previous.CATALOG, version=c.ORDER_INVOICING_POLICY_VERSION,
    commands=tuple(sorted((*previous.CATALOG.commands, *ADDED_COMMANDS), key=lambda x: x.name)),
    company_actions=tuple(sorted((*previous.CATALOG.company_actions, *ADDED_ACTIONS), key=lambda x: x.key)))
MANIFEST = c.catalog_manifest(CATALOG, previous.MANIFEST.standalone_names)


def catalog_bundle():
    inventory = dict(manifest=asdict(MANIFEST), sources=[asdict(x) for x in CATALOG.conditional_sources])
    digest = hashlib.sha256(json.dumps(inventory, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return CatalogBundle(SOURCE_COMMIT, CATALOG, MANIFEST.standalone_names, digest)
