"""Explicit c235 source delta for private activation; never adopted by runtime imports.

Legacy runtime/catalog_bundle stays frozen. Only explicitly activated roots select
this catalog; no public activation route is exposed here.
The inventory test compares every executable descriptor and conditional owner.
"""
from dataclasses import asdict, replace
import hashlib
import json
from .permission_catalog import (FROZEN_CATALOG, FROZEN_MANIFEST, CommandDescriptor,
    CompanyAction, CapabilitySpec, Requirement, ResourceSource, DefaultEntry, catalog_manifest, PURCHASE_DELETE_FAMILIES, SCOPED_POLICY_VERSION)
from .permission_snapshot import CatalogBundle

SOURCE_COMMIT = 'c235e9a27f317cc5ae2e21316153ed6fb012d64c'
ADDED_COMMANDS = (
    CommandDescriptor(name='item-receipt history', routed_scope='company', capability='ledger.read', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='item-receipt post', routed_scope='company', capability='ledger.post', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='item-receipt query', routed_scope='company', capability='ledger.read', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='item-receipt show', routed_scope='company', capability='ledger.read', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='item-receipt update', routed_scope='company', capability='ledger.post', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='item-receipt void', routed_scope='company', capability='ledger.post', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
)
ADDED_ACTIONS = (
    CompanyAction(key='item-receipt history', requirements=(Requirement(capability='ledger.read', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.receiving_cmds._read.<locals>.planner|src/bookflow/commands/receiving_cmds.py',)),
    CompanyAction(key='item-receipt post', requirements=(Requirement(capability='ledger.post', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.receiving_cmds._write.<locals>.planner|src/bookflow/commands/receiving_cmds.py',)),
    CompanyAction(key='item-receipt query', requirements=(Requirement(capability='ledger.read', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.receiving_cmds._read.<locals>.planner|src/bookflow/commands/receiving_cmds.py',)),
    CompanyAction(key='item-receipt show', requirements=(Requirement(capability='ledger.read', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.receiving_cmds._read.<locals>.planner|src/bookflow/commands/receiving_cmds.py',)),
    CompanyAction(key='item-receipt update', requirements=(Requirement(capability='ledger.post', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.receiving_cmds._write.<locals>.planner|src/bookflow/commands/receiving_cmds.py',)),
    CompanyAction(key='item-receipt void', requirements=(Requirement(capability='ledger.post', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.receiving_cmds._write.<locals>.planner|src/bookflow/commands/receiving_cmds.py',)),
)
CURRENT_SOURCES = (
    ResourceSource(owner='bookflow.company.billing_edits.carry_allocations', call_sites=(('src/bookflow/company/billing_edits.py', 110),), requirements=(Requirement(capability='customer-work', threshold='standard'),)),
    ResourceSource(owner='bookflow.company.billing_edits.protect_sale', call_sites=(('src/bookflow/company/billing_edits.py', 71),), requirements=(Requirement(capability='customer-work', threshold='standard'),)),
    ResourceSource(owner='bookflow.company.billing_queries.authorize_sale', call_sites=(('src/bookflow/company/billing_queries.py', 124),), requirements=(Requirement(capability='customer-work', threshold='member'), Requirement(capability='customer-work', threshold='standard'))),
    ResourceSource(owner='bookflow.company.billing_queries.sale_source_links', call_sites=(('src/bookflow/company/billing_queries.py', 112),), requirements=(Requirement(capability='customer-work', threshold='member'),)),
    ResourceSource(owner='bookflow.company.billing_queries.sale_source_output', call_sites=(('src/bookflow/company/billing_queries.py', 100),), requirements=(Requirement(capability='customer-work', threshold='member'),)),
    ResourceSource(owner='bookflow.company.deposit_resolution.resolve', call_sites=(('src/bookflow/company/deposit_resolution.py', 16),), requirements=(Requirement(capability='ledger.post', threshold='standard'),)),
    ResourceSource(owner='bookflow.company.payment_authority.authorize', call_sites=(('src/bookflow/company/payment_authority.py', 61),), requirements=(Requirement(capability='customer-work', threshold='member'), Requirement(capability='customer-work', threshold='standard'), Requirement(capability='ledger.post', threshold='standard'), Requirement(capability='ledger.read', threshold='member'))),
    ResourceSource(owner='bookflow.company.payment_authority.authorize_event', call_sites=(('src/bookflow/company/payment_authority.py', 415),), requirements=(Requirement(capability='customer-work', threshold='member'), Requirement(capability='ledger.read', threshold='member'))),
    ResourceSource(owner='bookflow.company.payment_authority.authorize_events', call_sites=(('src/bookflow/company/payment_authority.py', 430),), requirements=(Requirement(capability='customer-work', threshold='member'), Requirement(capability='ledger.read', threshold='member'))),
    ResourceSource(owner='bookflow.company.payment_authority.authorize_publication_selections', call_sites=(('src/bookflow/company/payment_authority.py', 675),), requirements=(Requirement(capability='customer-work', threshold='member'), Requirement(capability='customer-work', threshold='standard'), Requirement(capability='ledger.post', threshold='standard'), Requirement(capability='ledger.read', threshold='member'))),
    ResourceSource(owner='bookflow.company.payment_authority.authorize_publication_transactions', call_sites=(('src/bookflow/company/payment_authority.py', 525), ('src/bookflow/company/payment_authority.py', 527)), requirements=(Requirement(capability='customer-work', threshold='member'), Requirement(capability='customer-work', threshold='standard'), Requirement(capability='ledger.post', threshold='standard'), Requirement(capability='ledger.read', threshold='member'))),
    ResourceSource(owner='bookflow.company.payment_authority.authorize_query', call_sites=(('src/bookflow/company/payment_authority.py', 72), ('src/bookflow/company/payment_authority.py', 74)), requirements=(Requirement(capability='customer-work', threshold='member'), Requirement(capability='customer-work', threshold='standard'), Requirement(capability='ledger.post', threshold='standard'), Requirement(capability='ledger.read', threshold='member'))),
    ResourceSource(owner='bookflow.company.payment_authority.denied_events', call_sites=(('src/bookflow/company/payment_authority.py', 448),), requirements=(Requirement(capability='customer-work', threshold='member'), Requirement(capability='ledger.read', threshold='member'))),
    ResourceSource(owner='bookflow.company.payment_authority.readable_predicate', call_sites=(('src/bookflow/company/payment_authority.py', 26),), requirements=(Requirement(capability='customer-work', threshold='member'),)),
    ResourceSource(owner='bookflow.company.payment_preparation.payment_page', call_sites=(('src/bookflow/company/payment_preparation.py', 278),), requirements=(Requirement(capability='customer-work', threshold='member'),)),
    ResourceSource(owner='bookflow.company.payment_recovery.readable_selection', call_sites=(('src/bookflow/company/payment_recovery.py', 595),), requirements=(Requirement(capability='customer-work', threshold='member'),)),
    ResourceSource(owner='bookflow.company.reconciliation_adapters.authority', call_sites=(('src/bookflow/company/reconciliation_adapters.py', 108),), requirements=(Requirement(capability='ledger.read', threshold='member'),)),
    ResourceSource(owner='bookflow.company.reconciliation_adapters.population', call_sites=(('src/bookflow/company/reconciliation_adapters.py', 356),), requirements=(Requirement(capability='ledger.read', threshold='member'),)),
    ResourceSource(owner='bookflow.company.reconciliation_adapters.prepare_prospective', call_sites=(('src/bookflow/company/reconciliation_adapters.py', 488),), requirements=(Requirement(capability='customer-work', threshold='member'),)),
    ResourceSource(owner='bookflow.company.reconciliation_proposals.preview', call_sites=(('src/bookflow/company/reconciliation_proposals.py', 37),), requirements=(Requirement(capability='ledger.post', threshold='standard'),)),
    ResourceSource(owner='bookflow.company.transaction_deletion_facts.admit', call_sites=(('src/bookflow/company/transaction_deletion_facts.py', 100), ('src/bookflow/company/transaction_deletion_facts.py', 101)), requirements=(Requirement(capability='transaction.journal_entry.delete', threshold='standard'), Requirement(capability='transaction.invoice.delete', threshold='standard'), Requirement(capability='transaction.sales_receipt.delete', threshold='standard'), Requirement(capability='transaction.payment.delete', threshold='standard'), Requirement(capability='ledger.read', threshold='member'))),
    ResourceSource(owner='bookflow.company.transaction_deletion_facts.load', call_sites=(('src/bookflow/company/transaction_deletion_facts.py', 152),), requirements=(Requirement(capability='customer-work', threshold='standard'),)),
)
CATALOG = replace(FROZEN_CATALOG, version=SCOPED_POLICY_VERSION,
    commands=tuple(sorted((*FROZEN_CATALOG.commands, *ADDED_COMMANDS), key=lambda x:x.name)),
    capabilities=tuple(sorted((*FROZEN_CATALOG.capabilities,
        *(CapabilitySpec('transaction.'+family+'.delete', ('standard',), ()) for family in PURCHASE_DELETE_FAMILIES)), key=lambda x:x.name)),
    company_actions=tuple(sorted((*FROZEN_CATALOG.company_actions, *ADDED_ACTIONS,
        *(CompanyAction('contract:delete:'+family,
            (Requirement('transaction.'+family+'.delete','standard'), Requirement('ledger.read','member')),
            False, ('design/transaction-deletion.md', 'design/permission-resolution.md#delete-disclosure')) for family in PURCHASE_DELETE_FAMILIES)), key=lambda x:x.key)),
    defaults=tuple(sorted((*FROZEN_CATALOG.defaults,
        *(DefaultEntry(role, Requirement('membership','authenticated')) for role in ('admin','hub_admin','owner','readonly','standard')),
        DefaultEntry('hub_admin', Requirement('user','hub_admin'))), key=lambda x:(x.role,x.requirement.capability,x.requirement.threshold))),
    conditional_sources=CURRENT_SOURCES)
MANIFEST = catalog_manifest(CATALOG, FROZEN_MANIFEST.standalone_names)

def catalog_bundle():
    inventory=dict(manifest=asdict(MANIFEST), sources=[asdict(x) for x in CURRENT_SOURCES])
    digest=hashlib.sha256(json.dumps(inventory, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return CatalogBundle(SOURCE_COMMIT, CATALOG, MANIFEST.standalone_names, digest)
