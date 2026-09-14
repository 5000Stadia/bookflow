"""Explicit executable sales Delete delta over the accepted purchase descriptor."""
from dataclasses import asdict, replace
import hashlib
import json
from . import permission_deletion_catalog as previous, permission_catalog as c
from .permission_snapshot import CatalogBundle
from bookflow.core.deletion_families import SALES_FAMILIES, capability

SOURCE_COMMIT = 'dc3a773bf139d9a683a08273ab1fe890c082e579'
ADDED_COMMANDS = tuple(c.CommandDescriptor(
    family.replace('_','-')+' delete', 'company', capability(family), 'standard',
    (c.Requirement('ledger.read','member'),), True, None, False, False, None,
    'bookflow.commands.sales_cmds._delete.<locals>.authorize|src/bookflow/commands/sales_cmds.py',
    'bookflow.commands.sales_cmds._delete.<locals>.recover|src/bookflow/commands/sales_cmds.py', None)
    for family in SALES_FAMILIES)
ADDED_ACTIONS = tuple(c.CompanyAction(x.name,
    (c.Requirement(x.capability,'standard'),c.Requirement('ledger.read','member')),True,
    ('bookflow.commands.sales_cmds._delete.<locals>.authorize|src/bookflow/commands/sales_cmds.py','bookflow.commands.sales_cmds._delete.<locals>.planner|src/bookflow/commands/sales_cmds.py','bookflow.commands.sales_cmds._delete.<locals>.recover|src/bookflow/commands/sales_cmds.py')) for x in ADDED_COMMANDS)
CURRENT_SOURCES = (*previous.CATALOG.conditional_sources,
    c.ResourceSource('bookflow.company.sales_deletions.admit',
        (('src/bookflow/company/sales_deletions.py',29),),
        (c.Requirement('ledger.read','member'),)))
CATALOG = replace(previous.CATALOG, version=c.SALES_DELETE_POLICY_VERSION,
    commands=tuple(sorted((*previous.CATALOG.commands,*ADDED_COMMANDS),key=lambda x:x.name)),
    capabilities=tuple(replace(x,registered_thresholds=('standard',)) if x.name in tuple(map(capability,SALES_FAMILIES)) else x for x in previous.CATALOG.capabilities),
    company_actions=tuple(sorted((*(replace(x,available=True) if x.key in tuple('contract:delete:'+f for f in SALES_FAMILIES) else x for x in previous.CATALOG.company_actions),*ADDED_ACTIONS),key=lambda x:x.key)),
    conditional_sources=tuple(sorted(CURRENT_SOURCES,key=lambda x:x.owner)))
MANIFEST = c.catalog_manifest(CATALOG, previous.MANIFEST.standalone_names)


def catalog_bundle():
    inventory = dict(manifest=asdict(MANIFEST),sources=[asdict(x) for x in CATALOG.conditional_sources])
    digest = hashlib.sha256(json.dumps(inventory,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    return CatalogBundle(SOURCE_COMMIT,CATALOG,MANIFEST.standalone_names,digest)
