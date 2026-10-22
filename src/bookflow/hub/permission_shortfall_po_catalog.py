"""Add shortfall coverage (R101 increment 5) without rewriting any accepted descriptor.

`sales-order purchase-order` and `work-order purchase-order` at `ledger.post` standard (what
`purchase-order post` requires) with `customer-work` standard checked too. No capability, role
default or existing threshold changes.
"""
from dataclasses import asdict, replace
import hashlib
import json

from . import permission_order_invoicing_catalog as previous, permission_catalog as c
from .permission_snapshot import CatalogBundle

SOURCE_COMMIT = '2a7c513c7007c486dae27e066547d5c6e2f95c88'
_OWNER = ('bookflow.commands.shortfall_cmds._register.<locals>.planner|src/bookflow/commands/shortfall_cmds.py',)
_TEXT = 'ledger.post (what purchase-order post requires) and customer-work standard role'
ADDED_COMMANDS = tuple(
    c.CommandDescriptor(name=name, routed_scope='company', capability='ledger.post', threshold='standard', resources=(c.Requirement(capability='customer-work', threshold='standard'),), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=_TEXT, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None)
    for name in ('sales-order purchase-order', 'work-order purchase-order'))
ADDED_ACTIONS = tuple(
    c.CompanyAction(key=name, requirements=(c.Requirement(capability='customer-work', threshold='standard'), c.Requirement(capability='ledger.post', threshold='standard')), available=True, remaining_graph_owners=_OWNER)
    for name in ('sales-order purchase-order', 'work-order purchase-order'))
CATALOG = replace(previous.CATALOG, version=c.SHORTFALL_PO_POLICY_VERSION,
    commands=tuple(sorted((*previous.CATALOG.commands, *ADDED_COMMANDS), key=lambda x: x.name)),
    company_actions=tuple(sorted((*previous.CATALOG.company_actions, *ADDED_ACTIONS), key=lambda x: x.key)))
MANIFEST = c.catalog_manifest(CATALOG, previous.MANIFEST.standalone_names)


def catalog_bundle():
    inventory = dict(manifest=asdict(MANIFEST), sources=[asdict(x) for x in CATALOG.conditional_sources])
    digest = hashlib.sha256(json.dumps(inventory, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return CatalogBundle(SOURCE_COMMIT, CATALOG, MANIFEST.standalone_names, digest)
