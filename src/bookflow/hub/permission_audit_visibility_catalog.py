"""Record the audit trail's capability filter without rewriting any accepted descriptor.

`audit list`, `audit show`, `audit tail` and `activity` now hide every event that touched a record
type the reader may not read (`company/audit_visibility.py`). That module asks the policy in force
once per read capability at `member`, from one new call site, which this delta records. No command,
capability, role default, company action, admin action or threshold changes, so a root replacing
its catalog with this one suspends no agent and revokes no token.
"""
from dataclasses import asdict, replace
import hashlib
import json

from . import permission_report_export_catalog as previous, permission_catalog as c
from .permission_snapshot import CatalogBundle

SOURCE_COMMIT = '9595c9c458f6b0dcdf5cb5deafb14ae7a812427f'
# Every read capability a touched record type can need, at `member`; a record type the filter
# does not know needs all of them.
READ_CAPABILITIES = (
    'account', 'attachment', 'class', 'company', 'custom-field', 'customer', 'customer-message',
    'customer-type', 'customer-work', 'directive', 'employee', 'item', 'item-category', 'job-type',
    'ledger.read', 'note', 'other-name', 'payment-method', 'price-level', 'sales-rep',
    'sales-tax-code', 'ship-method', 'term', 'unit-of-measure', 'vendor', 'vendor-type')
ADDED_SOURCES = (
    c.ResourceSource('bookflow.company.audit_visibility.admitted',
        (('src/bookflow/company/audit_visibility.py', 97),),
        tuple(c.Requirement(name, 'member') for name in READ_CAPABILITIES)),
)
CATALOG = replace(previous.CATALOG, version=c.AUDIT_VISIBILITY_POLICY_VERSION,
    conditional_sources=tuple(sorted((*previous.CATALOG.conditional_sources, *ADDED_SOURCES), key=lambda x: x.owner)))
MANIFEST = c.catalog_manifest(CATALOG, previous.MANIFEST.standalone_names)


def catalog_bundle():
    inventory = dict(manifest=asdict(MANIFEST), sources=[asdict(x) for x in CATALOG.conditional_sources])
    digest = hashlib.sha256(json.dumps(inventory, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return CatalogBundle(SOURCE_COMMIT, CATALOG, MANIFEST.standalone_names, digest)
