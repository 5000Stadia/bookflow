"""Explicit executable journal-entry Delete delta over the accepted recorded-time descriptor.

The last of the four prepared families to gain its Delete. Its capability and its
unavailable delete contract were declared in the frozen ancestor long ago, so this
delta flips them rather than adding them, and carries the command, its company action
and the admit site the family's own coordinator owns.

Where a new command goes is not a matter of taste. Every version below this one is
`replace(previous.CATALOG, ...)` over a single shared ancestor, and a root stores the
exact descriptor it activated, so a command written into that ancestor rewrites
descriptors installations already hold and locks them out of their own books. Nor is it
only commands: the ancestor records each conditional source by file and LINE NUMBER, so
editing a file that holds one moves the ancestor too, with nothing on screen to suggest
a catalog change is happening at all. A correction to those numbers belongs in a delta
like this one, never in the ancestor.
"""
from dataclasses import asdict, replace
import hashlib
import json
from . import permission_job_time_catalog as previous, permission_catalog as c
from .permission_snapshot import CatalogBundle
from bookflow.core.deletion_families import JOURNAL_FAMILIES, capability

SOURCE_COMMIT = 'd464bb9c6c3d05fbb5a48b6a8c5b1e6a4f4a0dbb'
ADDED_COMMANDS = tuple(c.CommandDescriptor(
    family.replace('_entry','').replace('_','-')+' delete', 'company', capability(family), 'standard',
    (c.Requirement('ledger.read','member'),), True, None, False, False, None, None,
    'bookflow.commands.journal_cmds._delete.<locals>.recover|src/bookflow/commands/journal_cmds.py', None)
    for family in JOURNAL_FAMILIES)
ADDED_ACTIONS = tuple(c.CompanyAction(x.name,
    (c.Requirement(x.capability,'standard'),c.Requirement('ledger.read','member')),True,
    ('bookflow.commands.journal_cmds._delete.<locals>.planner|src/bookflow/commands/journal_cmds.py','bookflow.commands.journal_cmds._delete.<locals>.recover|src/bookflow/commands/journal_cmds.py')) for x in ADDED_COMMANDS)
CURRENT_SOURCES = (*previous.CATALOG.conditional_sources,
    c.ResourceSource('bookflow.company.journal_deletions.admit',
        (('src/bookflow/company/journal_deletions.py',51),),
        (c.Requirement('ledger.read','member'),)))
CATALOG = replace(previous.CATALOG, version=c.JOURNAL_DELETE_POLICY_VERSION,
    commands=tuple(sorted((*previous.CATALOG.commands,*ADDED_COMMANDS),key=lambda x:x.name)),
    capabilities=tuple(replace(x,registered_thresholds=('standard',)) if x.name in tuple(map(capability,JOURNAL_FAMILIES)) else x for x in previous.CATALOG.capabilities),
    company_actions=tuple(sorted((*(replace(x,available=True) if x.key in tuple('contract:delete:'+f for f in JOURNAL_FAMILIES) else x for x in previous.CATALOG.company_actions),*ADDED_ACTIONS),key=lambda x:x.key)),
    conditional_sources=tuple(sorted(CURRENT_SOURCES,key=lambda x:x.owner)))
MANIFEST = c.catalog_manifest(CATALOG, previous.MANIFEST.standalone_names)


def catalog_bundle():
    inventory = dict(manifest=asdict(MANIFEST),sources=[asdict(x) for x in CATALOG.conditional_sources])
    digest = hashlib.sha256(json.dumps(inventory,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    return CatalogBundle(SOURCE_COMMIT,CATALOG,MANIFEST.standalone_names,digest)
