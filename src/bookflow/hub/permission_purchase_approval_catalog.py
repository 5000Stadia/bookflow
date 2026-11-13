"""Purchase release approvals reuse existing capabilities on membership-sales-v1.

Both native targets use ledger.read; commit and inverse use current ledger.post.
No additional commands, default grants or reviewer authority are introduced.
"""
from dataclasses import asdict, replace
import hashlib
import json
from . import permission_membership_sales_catalog as previous, permission_catalog as c
from .permission_snapshot import CatalogBundle

SOURCE_COMMIT = '0e66abf794ba77f8c7cb04c241ec511686e95636'
_SITES = {'bookflow.company.approval_commit_reversal._native': (('src/bookflow/company/approval_commit_reversal.py',
                                                        76),
                                                       ('src/bookflow/company/approval_commit_reversal.py',
                                                        77)),
 'bookflow.company.approvals.decision_authority': (('src/bookflow/company/approvals.py', 243),
                                                   ('src/bookflow/company/approvals.py', 244)),
 'bookflow.company.approvals.native_write': (('src/bookflow/company/approvals.py', 67),),
 'bookflow.company.approvals.recover': (('src/bookflow/company/approvals.py', 224),),
 'bookflow.company.batches.require_read': (('src/bookflow/company/batches.py', 445),
                                           ('src/bookflow/company/batches.py', 447)),
 'bookflow.company.bill_deletions.admit': (('src/bookflow/company/bill_deletions.py', 37),),
 'bookflow.company.billing_edits.carry_allocations': (('src/bookflow/company/billing_edits.py', 115),),
 'bookflow.company.billing_edits.protect_sale': (('src/bookflow/company/billing_edits.py', 75),),
 'bookflow.company.credit_deletions.admit': (('src/bookflow/company/credit_deletions.py', 39),),
 'bookflow.company.credits.facts': (('src/bookflow/company/credits.py', 1190),),
 'bookflow.company.deposit_deletions.admit': (('src/bookflow/company/deposit_deletions.py', 52),),
 'bookflow.company.journal_deletions.admit': (('src/bookflow/company/journal_deletions.py', 52),),
 'bookflow.company.payment_authority.authorize': (('src/bookflow/company/payment_authority.py', 94),),
 'bookflow.company.payment_authority.authorize_event': (('src/bookflow/company/payment_authority.py', 448),),
 'bookflow.company.payment_authority.authorize_events': (('src/bookflow/company/payment_authority.py', 463),),
 'bookflow.company.payment_authority.authorize_publication_selections': (('src/bookflow/company/payment_authority.py',
                                                                          722),),
 'bookflow.company.payment_authority.authorize_publication_transactions': (('src/bookflow/company/payment_authority.py',
                                                                            566),
                                                                           ('src/bookflow/company/payment_authority.py',
                                                                            569)),
 'bookflow.company.payment_authority.authorize_query': (('src/bookflow/company/payment_authority.py', 105),
                                                        ('src/bookflow/company/payment_authority.py', 107)),
 'bookflow.company.payment_authority.denied_events': (('src/bookflow/company/payment_authority.py', 481),),
 'bookflow.company.payment_authority.pos_scope': (('src/bookflow/company/payment_authority.py', 31),),
 'bookflow.company.payment_authority.readable_predicate': (('src/bookflow/company/payment_authority.py',
                                                            54),),
 'bookflow.company.payment_deletions.admit': (('src/bookflow/company/payment_deletions.py', 41),),
 'bookflow.company.payment_preparation.payment_page': (('src/bookflow/company/payment_preparation.py', 332),),
 'bookflow.company.pos_sales.require_price_override': (('src/bookflow/company/pos_sales.py', 179),),
 'bookflow.company.reconciliation_adapters.authority': (('src/bookflow/company/reconciliation_adapters.py',
                                                         176),),
 'bookflow.company.reconciliation_adapters.population': (('src/bookflow/company/reconciliation_adapters.py',
                                                          452),),
 'bookflow.company.reconciliation_adapters.prepare_prospective': (('src/bookflow/company/reconciliation_adapters.py',
                                                                   584),),
 'bookflow.company.statement_inbox.require_inbox': (('src/bookflow/company/statement_inbox.py', 13),),
 'bookflow.company.transaction_deletion_facts.admit': (('src/bookflow/company/transaction_deletion_facts.py',
                                                        106),
                                                       ('src/bookflow/company/transaction_deletion_facts.py',
                                                        107)),
 'bookflow.company.transaction_deletion_facts.load': (('src/bookflow/company/transaction_deletion_facts.py',
                                                       158),),
 'bookflow.company.transaction_find._withheld': (('src/bookflow/company/transaction_find.py', 332),)}
CATALOG = replace(previous.CATALOG, version=c.PURCHASE_APPROVAL_POLICY_VERSION,
    conditional_sources=tuple(replace(source, call_sites=_SITES[source.owner]) if source.owner in _SITES else source
                              for source in previous.CATALOG.conditional_sources))
MANIFEST = c.catalog_manifest(CATALOG, previous.MANIFEST.standalone_names)


def catalog_bundle():
    inventory = dict(manifest=asdict(MANIFEST), sources=[asdict(x) for x in CATALOG.conditional_sources])
    digest = hashlib.sha256(json.dumps(inventory, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return CatalogBundle(SOURCE_COMMIT, CATALOG, MANIFEST.standalone_names, digest)
