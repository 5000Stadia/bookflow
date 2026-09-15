"""Explicit executable customer-payment Delete delta over the accepted sales descriptor."""
from dataclasses import asdict, replace
import hashlib
import json
from . import permission_sales_deletion_catalog as previous, permission_catalog as c
from .permission_snapshot import CatalogBundle
from bookflow.core.deletion_families import PAYMENT_FAMILIES, capability

SOURCE_COMMIT = '3fbec9c5ad065284d31ad1c2f2ed636217d8b406'
ADDED_COMMANDS = tuple(c.CommandDescriptor(
    family.replace('_','-')+' delete', 'company', capability(family), 'standard',
    (c.Requirement('ledger.read','member'),), True, None, False, False, None, None,
    'bookflow.commands.payment_cmds._delete.<locals>.recover|src/bookflow/commands/payment_cmds.py', None)
    for family in PAYMENT_FAMILIES)
ADDED_ACTIONS = tuple(c.CompanyAction(x.name,
    (c.Requirement(x.capability,'standard'),c.Requirement('ledger.read','member')),True,
    ('bookflow.commands.payment_cmds._delete.<locals>.planner|src/bookflow/commands/payment_cmds.py','bookflow.commands.payment_cmds._delete.<locals>.recover|src/bookflow/commands/payment_cmds.py')) for x in ADDED_COMMANDS)
CURRENT_SOURCES = (*previous.CATALOG.conditional_sources,
    c.ResourceSource('bookflow.company.payment_deletions.admit',
        (('src/bookflow/company/payment_deletions.py',40),),
        (c.Requirement('ledger.read','member'),)))
CATALOG = replace(previous.CATALOG, version=c.PAYMENT_DELETE_POLICY_VERSION,
    commands=tuple(sorted((*previous.CATALOG.commands,*ADDED_COMMANDS),key=lambda x:x.name)),
    capabilities=tuple(replace(x,registered_thresholds=('standard',)) if x.name in tuple(map(capability,PAYMENT_FAMILIES)) else x for x in previous.CATALOG.capabilities),
    company_actions=tuple(sorted((*(replace(x,available=True) if x.key in tuple('contract:delete:'+f for f in PAYMENT_FAMILIES) else x for x in previous.CATALOG.company_actions),*ADDED_ACTIONS),key=lambda x:x.key)),
    conditional_sources=tuple(sorted(CURRENT_SOURCES,key=lambda x:x.owner)))
MANIFEST = c.catalog_manifest(CATALOG, previous.MANIFEST.standalone_names)


def catalog_bundle():
    inventory = dict(manifest=asdict(MANIFEST),sources=[asdict(x) for x in CATALOG.conditional_sources])
    digest = hashlib.sha256(json.dumps(inventory,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    return CatalogBundle(SOURCE_COMMIT,CATALOG,MANIFEST.standalone_names,digest)
