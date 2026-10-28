"""Add list imports' check, decisions and saved mappings (R107 increment 2) without rewriting any accepted descriptor.

A new layer on delivery-batch-v1, under the `import` capability that import-v1 added (its role defaults unchanged):
`import check` and `import-mapping show`, `history`, `query`, `match` are member reads; `import decide` and
`import-mapping save`, `deactivate` are standard writes. A decision's create runs its own command under the caller's
grants when the run reaches it, so the importer grants nothing. The kind's own read permission is asked through
`batches.require_read`, re-declared here: its pairs now include the lists an import's rows refer to beyond the
importable kinds (sales tax codes, units of measure), since `import check` asks whether the caller may read a list
before offering names from it. No capability, role default or existing threshold changes, so a root replacing its
catalog with this one suspends no agent and revokes no token.
"""
from dataclasses import asdict, replace
import hashlib
import json

from . import permission_delivery_batch_catalog as previous, permission_catalog as c
from .permission_snapshot import CatalogBundle

SOURCE_COMMIT = '73d7bebd640414bda46984313ac503ccd66bbcfe'
_OWNER = 'bookflow.commands.import_cmds.{}|src/bookflow/commands/import_cmds.py'
# (name, threshold, planner)
_ADDED = (
    ('import check', 'member', 'plan_import_check'),
    ('import decide', 'standard', 'plan_import_decide'),
    ('import-mapping deactivate', 'standard', 'plan_mapping_deactivate'),
    ('import-mapping history', 'member', 'plan_mapping_history'),
    ('import-mapping match', 'member', 'plan_mapping_match'),
    ('import-mapping query', 'member', 'plan_mapping_query'),
    ('import-mapping save', 'standard', 'plan_mapping_save'),
    ('import-mapping show', 'member', 'plan_mapping_show'),
)
ADDED_COMMANDS = tuple(
    c.CommandDescriptor(name, 'company', 'import', threshold, (), True, None, False, False, None, None, None, None)
    for name, threshold, _ in _ADDED)
ADDED_ACTIONS = tuple(
    c.CompanyAction(name, (c.Requirement('import', threshold),), True, (_OWNER.format(planner),))
    for name, threshold, planner in _ADDED)
_READS = ('account', 'attachment', 'class', 'customer', 'customer-message', 'customer-type', 'employee', 'item',
          'item-category', 'job-type', 'other-name', 'payment-method', 'price-level', 'sales-rep', 'sales-tax-code',
          'ship-method', 'term', 'unit-of-measure', 'vendor', 'vendor-type')
_REQUIRE_READ = 'bookflow.company.batches.require_read'
CURRENT_SOURCES = (
    *(s for s in previous.CATALOG.conditional_sources if s.owner != _REQUIRE_READ),
    c.ResourceSource(_REQUIRE_READ,
        (('src/bookflow/company/batches.py', 366), ('src/bookflow/company/batches.py', 368)),
        tuple(sorted({c.Requirement(capability, 'member') for capability in _READS},
                     key=lambda r: r.capability))))
CATALOG = replace(previous.CATALOG, version=c.IMPORT_MAPPING_POLICY_VERSION,
    commands=tuple(sorted((*previous.CATALOG.commands, *ADDED_COMMANDS), key=lambda x: x.name)),
    company_actions=tuple(sorted((*previous.CATALOG.company_actions, *ADDED_ACTIONS), key=lambda x: x.key)),
    conditional_sources=tuple(sorted(CURRENT_SOURCES, key=lambda x: x.owner)))
MANIFEST = c.catalog_manifest(CATALOG, previous.MANIFEST.standalone_names)


def catalog_bundle():
    inventory = dict(manifest=asdict(MANIFEST), sources=[asdict(x) for x in CATALOG.conditional_sources])
    digest = hashlib.sha256(json.dumps(inventory, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return CatalogBundle(SOURCE_COMMIT, CATALOG, MANIFEST.standalone_names, digest)
