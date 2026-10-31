"""Reconciliation from the inbox (R108 increment 6), on bank-rule-v1.

The existing reconciliation import keeps its authority for file input. Reading the inbox
also requires bank-feed at member, only when from_inbox is requested. No commands,
capabilities, defaults or merged descriptors change.
"""
from dataclasses import asdict, replace
import hashlib
import json

from . import permission_bank_rule_catalog as previous, permission_catalog as c
from .permission_snapshot import CatalogBundle

SOURCE_COMMIT = '89622d32040590d2f54760a800312d656a390b99'
INBOX_SOURCE = c.ResourceSource('bookflow.company.statement_inbox.require_inbox',
    (('src/bookflow/company/statement_inbox.py', 20),), (c.Requirement('bank-feed', 'member'),))
CATALOG = replace(previous.CATALOG, version=c.BANK_RECONCILE_POLICY_VERSION,
    conditional_sources=tuple(sorted((*previous.CATALOG.conditional_sources, INBOX_SOURCE), key=lambda x: x.owner)))
MANIFEST = c.catalog_manifest(CATALOG, previous.MANIFEST.standalone_names)


def catalog_bundle():
    inventory = dict(manifest=asdict(MANIFEST), sources=[asdict(x) for x in CATALOG.conditional_sources])
    digest = hashlib.sha256(json.dumps(inventory, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return CatalogBundle(SOURCE_COMMIT, CATALOG, MANIFEST.standalone_names, digest)
