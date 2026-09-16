"""Explicit executable recorded-time delta over the accepted deposit descriptor.

The nine `time-activity` commands arrived after every version below this one was
accepted, so this is where a root accepts them; they first landed in the frozen
ancestor, which rewrote descriptors roots had already stored.

This delta also carries the five moved conditional-source call sites. The ancestor
records each `require_resource` site by file and line number, so editing
`billing_edits.py` or `billing_queries.py` at all moves the ancestor's descriptor --
a landmine independent of commands, and the reason those line numbers cannot be
corrected in place. The correction belongs here, where the current source tree is
what a new root accepts and every older version keeps the numbers it stored.
"""
from dataclasses import asdict, replace
import hashlib
import json
from . import permission_deposit_deletion_catalog as previous, permission_catalog as c
from .permission_snapshot import CatalogBundle

SOURCE_COMMIT = '3e6937a7f4f83b0b3bb3ab60da5fcb37e44f70e8'
ADDED_COMMANDS = (
    c.CommandDescriptor(name='time-activity billing', routed_scope='company', capability='ledger.read', threshold='member', resources=(c.Requirement(capability='customer-work', threshold='member'),), available=True, feature=None, local_only=False, bootstrap=False, authorization_text='ledger and customer-work membership', conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    c.CommandDescriptor(name='time-activity create', routed_scope='company', capability='customer-work', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    c.CommandDescriptor(name='time-activity history', routed_scope='company', capability='customer-work', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    c.CommandDescriptor(name='time-activity invoice', routed_scope='company', capability='ledger.post', threshold='standard', resources=(c.Requirement(capability='customer-work', threshold='standard'),), available=True, feature=None, local_only=False, bootstrap=False, authorization_text='ledger.post and customer-work standard role', conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    c.CommandDescriptor(name='time-activity query', routed_scope='company', capability='customer-work', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    c.CommandDescriptor(name='time-activity sales-receipt', routed_scope='company', capability='ledger.post', threshold='standard', resources=(c.Requirement(capability='customer-work', threshold='standard'),), available=True, feature=None, local_only=False, bootstrap=False, authorization_text='ledger.post and customer-work standard role', conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    c.CommandDescriptor(name='time-activity show', routed_scope='company', capability='customer-work', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    c.CommandDescriptor(name='time-activity update', routed_scope='company', capability='customer-work', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    c.CommandDescriptor(name='time-activity void', routed_scope='company', capability='customer-work', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
)
ADDED_ACTIONS = (
    c.CompanyAction(key='time-activity billing', requirements=(c.Requirement(capability='customer-work', threshold='member'), c.Requirement(capability='ledger.read', threshold='member')), available=True, remaining_graph_owners=('bookflow.commands.billing_cmds.register.<locals>.planner|src/bookflow/commands/billing_cmds.py',)),
    c.CompanyAction(key='time-activity create', requirements=(c.Requirement(capability='customer-work', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.time_cmds._register.<locals>.planner|src/bookflow/commands/time_cmds.py',)),
    c.CompanyAction(key='time-activity history', requirements=(c.Requirement(capability='customer-work', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.time_cmds._register.<locals>.planner|src/bookflow/commands/time_cmds.py',)),
    c.CompanyAction(key='time-activity invoice', requirements=(c.Requirement(capability='customer-work', threshold='standard'), c.Requirement(capability='ledger.post', threshold='standard')), available=True, remaining_graph_owners=('bookflow.commands.billing_cmds.register.<locals>.planner|src/bookflow/commands/billing_cmds.py',)),
    c.CompanyAction(key='time-activity query', requirements=(c.Requirement(capability='customer-work', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.time_cmds._register.<locals>.planner|src/bookflow/commands/time_cmds.py',)),
    c.CompanyAction(key='time-activity sales-receipt', requirements=(c.Requirement(capability='customer-work', threshold='standard'), c.Requirement(capability='ledger.post', threshold='standard')), available=True, remaining_graph_owners=('bookflow.commands.billing_cmds.register.<locals>.planner|src/bookflow/commands/billing_cmds.py',)),
    c.CompanyAction(key='time-activity show', requirements=(c.Requirement(capability='customer-work', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.time_cmds._register.<locals>.planner|src/bookflow/commands/time_cmds.py',)),
    c.CompanyAction(key='time-activity update', requirements=(c.Requirement(capability='customer-work', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.time_cmds._register.<locals>.planner|src/bookflow/commands/time_cmds.py',)),
    c.CompanyAction(key='time-activity void', requirements=(c.Requirement(capability='customer-work', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.time_cmds._register.<locals>.planner|src/bookflow/commands/time_cmds.py',)),
)
# Where the five sites are now. `billing_edits` and `billing_queries` both gained and
# lost lines when recorded time was threaded through them; nothing about the
# requirements changed, only where the calls sit.
MOVED_CALL_SITES = {
    'bookflow.company.billing_edits.carry_allocations': (('src/bookflow/company/billing_edits.py', 110),),
    'bookflow.company.billing_edits.protect_sale': (('src/bookflow/company/billing_edits.py', 71),),
    'bookflow.company.billing_queries.authorize_sale': (('src/bookflow/company/billing_queries.py', 124),),
    'bookflow.company.billing_queries.sale_source_links': (('src/bookflow/company/billing_queries.py', 112),),
    'bookflow.company.billing_queries.sale_source_output': (('src/bookflow/company/billing_queries.py', 100),),
}
CURRENT_SOURCES = tuple(replace(x, call_sites=MOVED_CALL_SITES[x.owner]) if x.owner in MOVED_CALL_SITES else x
                        for x in previous.CATALOG.conditional_sources)
CATALOG = replace(previous.CATALOG, version=c.JOB_TIME_POLICY_VERSION,
    commands=tuple(sorted((*previous.CATALOG.commands,*ADDED_COMMANDS),key=lambda x:x.name)),
    company_actions=tuple(sorted((*previous.CATALOG.company_actions,*ADDED_ACTIONS),key=lambda x:x.key)),
    conditional_sources=tuple(sorted(CURRENT_SOURCES,key=lambda x:x.owner)))
MANIFEST = c.catalog_manifest(CATALOG, previous.MANIFEST.standalone_names)


def catalog_bundle():
    inventory = dict(manifest=asdict(MANIFEST),sources=[asdict(x) for x in CATALOG.conditional_sources])
    digest = hashlib.sha256(json.dumps(inventory,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    return CatalogBundle(SOURCE_COMMIT,CATALOG,MANIFEST.standalone_names,digest)
