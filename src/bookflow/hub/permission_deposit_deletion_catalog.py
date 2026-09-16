"""Explicit executable deposit Delete delta over the accepted credit-memo descriptor."""
from dataclasses import asdict, replace
import hashlib
import json
from . import permission_credit_deletion_catalog as previous, permission_catalog as c
from .permission_snapshot import CatalogBundle
from bookflow.core.deletion_families import DEPOSIT_FAMILIES, capability

SOURCE_COMMIT = '3fb08d66917cf87c846efc9154474d6512e82d70'
# The deposit family has no frozen preparation to flip: its capability, its unavailable
# delete contract and its command all arrive together here, which is what makes this one
# delta the whole of what a root has to accept to gain deposit deletion.
ADDED_CAPABILITIES = tuple(c.CapabilitySpec(capability(family), ('standard',), ('standard',))
    for family in DEPOSIT_FAMILIES)
ADDED_CONTRACTS = tuple(c.CompanyAction('contract:delete:'+family,
    (c.Requirement(capability(family),'standard'), c.Requirement('ledger.read','member')), True,
    ('design/transaction-deletion.md', 'design/permission-resolution.md#delete-disclosure'))
    for family in DEPOSIT_FAMILIES)
ADDED_COMMANDS = tuple(c.CommandDescriptor(
    family.replace('_','-')+' delete', 'company', capability(family), 'standard',
    (c.Requirement('ledger.read','member'),), True, None, False, False, None, None,
    'bookflow.commands.deposit_cmds._delete.<locals>.recover|src/bookflow/commands/deposit_cmds.py', None)
    for family in DEPOSIT_FAMILIES)
ADDED_ACTIONS = tuple(c.CompanyAction(x.name,
    (c.Requirement(x.capability,'standard'),c.Requirement('ledger.read','member')),True,
    ('bookflow.commands.deposit_cmds._delete.<locals>.planner|src/bookflow/commands/deposit_cmds.py','bookflow.commands.deposit_cmds._delete.<locals>.recover|src/bookflow/commands/deposit_cmds.py')) for x in ADDED_COMMANDS)
CURRENT_SOURCES = (*previous.CATALOG.conditional_sources,
    c.ResourceSource('bookflow.company.deposit_deletions.admit',
        (('src/bookflow/company/deposit_deletions.py',51),),
        (c.Requirement('ledger.read','member'),)))
CATALOG = replace(previous.CATALOG, version=c.DEPOSIT_DELETE_POLICY_VERSION,
    commands=tuple(sorted((*previous.CATALOG.commands,*ADDED_COMMANDS),key=lambda x:x.name)),
    capabilities=tuple(sorted((*previous.CATALOG.capabilities,*ADDED_CAPABILITIES),key=lambda x:x.name)),
    company_actions=tuple(sorted((*previous.CATALOG.company_actions,*ADDED_CONTRACTS,*ADDED_ACTIONS),key=lambda x:x.key)),
    conditional_sources=tuple(sorted(CURRENT_SOURCES,key=lambda x:x.owner)))
MANIFEST = c.catalog_manifest(CATALOG, previous.MANIFEST.standalone_names)


def catalog_bundle():
    inventory = dict(manifest=asdict(MANIFEST),sources=[asdict(x) for x in CATALOG.conditional_sources])
    digest = hashlib.sha256(json.dumps(inventory,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    return CatalogBundle(SOURCE_COMMIT,CATALOG,MANIFEST.standalone_names,digest)
