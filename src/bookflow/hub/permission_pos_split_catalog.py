"""Scoped split-sale payment composition on the immutable POS refund tip.

No commands, capabilities or defaults change. Only the internal payment scope's
conditional resource requirements enter this layer.
"""
from dataclasses import asdict, replace
import hashlib
import json

from . import permission_pos_refund_catalog as previous, permission_catalog as c
from .permission_snapshot import CatalogBundle

SOURCE_COMMIT = 'baf80032257e8bd60b9b8e4bcb2db10b2205396d'
PAYMENT_SOURCE = c.ResourceSource('bookflow.company.payment_authority.pos_scope',
    (('src/bookflow/company/payment_authority.py', 29),),
    (c.Requirement('pos.sell', 'member'), c.Requirement('pos.void', 'member')))
CATALOG = replace(previous.CATALOG, version=c.POS_SPLIT_POLICY_VERSION,
    conditional_sources=tuple(sorted((*previous.CATALOG.conditional_sources, PAYMENT_SOURCE), key=lambda x: x.owner)))
MANIFEST = c.catalog_manifest(CATALOG, previous.MANIFEST.standalone_names)


def catalog_bundle():
    inventory = dict(manifest=asdict(MANIFEST), sources=[asdict(x) for x in CATALOG.conditional_sources])
    digest = hashlib.sha256(json.dumps(inventory, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return CatalogBundle(SOURCE_COMMIT, CATALOG, MANIFEST.standalone_names, digest)
