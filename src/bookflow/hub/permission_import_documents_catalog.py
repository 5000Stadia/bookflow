"""Add document imports (R107 increment 4) without rewriting any accepted descriptor.

A new layer on bank-feed-reverse-v1 adding no command, capability, role default or threshold: invoices, bills, payments and
the other document kinds import through the existing `import` commands, and each document runs through its own
command under the caller's grants (`ledger.post`, `customer-work`), so the importer grants nothing. A reversal retires
each document with its family's delete under the caller's own explicit delete grant (a payment is unapplied first),
else its void. Only `batches.require_read` is re-declared: its pairs now include the `show` of every document kind
(`ledger.read` and `customer-work` at member), asked before a file of that kind is checked, previewed or read back.
No existing default changes, so a root replacing its catalog with this one suspends no agent and revokes no token.
"""
from dataclasses import asdict, replace
import hashlib
import json

from . import permission_bank_feed_reverse_catalog as previous, permission_catalog as c
from .permission_snapshot import CatalogBundle

SOURCE_COMMIT = '5de700e0f126c358e455a5b541af89a663e18630'
_REQUIRE_READ = 'bookflow.company.batches.require_read'
_PRIOR = next(s for s in previous.CATALOG.conditional_sources if s.owner == _REQUIRE_READ)
CURRENT_SOURCES = (
    *(s for s in previous.CATALOG.conditional_sources if s.owner != _REQUIRE_READ),
    c.ResourceSource(_REQUIRE_READ,
        (('src/bookflow/company/batches.py', 369), ('src/bookflow/company/batches.py', 371)),
        tuple(sorted({*_PRIOR.requirements, c.Requirement('ledger.read', 'member'),
                      c.Requirement('customer-work', 'member')}, key=lambda r: r.capability))))
CATALOG = replace(previous.CATALOG, version=c.IMPORT_DOCUMENTS_POLICY_VERSION,
    conditional_sources=tuple(sorted(CURRENT_SOURCES, key=lambda x: x.owner)))
MANIFEST = c.catalog_manifest(CATALOG, previous.MANIFEST.standalone_names)


def catalog_bundle():
    inventory = dict(manifest=asdict(MANIFEST), sources=[asdict(x) for x in CATALOG.conditional_sources])
    digest = hashlib.sha256(json.dumps(inventory, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return CatalogBundle(SOURCE_COMMIT, CATALOG, MANIFEST.standalone_names, digest)
