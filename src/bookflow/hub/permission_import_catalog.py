"""Add list imports and the batch ledger (R107 increments 0 and 1) without rewriting any accepted descriptor.

A new capability, `import` (design/bulk-import.md §8), held at `member` by every role and at `standard` by standard,
admin and owner, as list writes are: `import kinds`, `template`, `show`, `query`, `rows`, `errors` and `batch show`,
`query` are member reads; `import preview`, `run`, `cancel` and `reverse` are standard writes, each row (and each
inverse step of a reversal) checked again against its own command's capability, so the importer grants nothing.
Increment 2 adds to the same layer: `import check` and `import-mapping show`, `history`, `query`, `match` are member
reads; `import decide` and `import-mapping save`, `deactivate` standard writes (a decision's create runs its own command
under the caller's grants when the run reaches it). `import.approve` (R187) arrives with document imports. No existing capability, role default or threshold changes,
so a root replacing its catalog with this one suspends no agent and revokes no token.
"""
from dataclasses import asdict, replace
import hashlib
import json

from . import permission_delivery_queue_catalog as previous, permission_catalog as c
from .permission_snapshot import CatalogBundle

SOURCE_COMMIT = '73d7bebd640414bda46984313ac503ccd66bbcfe'
_OWNER = 'bookflow.commands.import_cmds.{}|src/bookflow/commands/import_cmds.py'
# (name, threshold, planner)
_ADDED = (
    ('batch query', 'member', 'plan_batch_query'),
    ('batch show', 'member', 'plan_batch_show'),
    ('import cancel', 'standard', 'plan_import_cancel'),
    ('import check', 'member', 'plan_import_check'),
    ('import decide', 'standard', 'plan_import_decide'),
    ('import errors', 'member', 'plan_import_errors'),
    ('import kinds', 'member', 'plan_import_kinds'),
    ('import preview', 'standard', 'plan_import_preview'),
    ('import query', 'member', 'plan_import_query'),
    ('import reverse', 'standard', 'plan_import_reverse'),
    ('import rows', 'member', 'plan_import_rows'),
    ('import run', 'standard', 'plan_import_run'),
    ('import show', 'member', 'plan_import_show'),
    ('import template', 'member', 'plan_import_template'),
    ('import-mapping deactivate', 'standard', 'plan_mapping_deactivate'),
    ('import-mapping history', 'member', 'plan_mapping_history'),
    ('import-mapping match', 'member', 'plan_mapping_match'),
    ('import-mapping query', 'member', 'plan_mapping_query'),
    ('import-mapping save', 'standard', 'plan_mapping_save'),
    ('import-mapping show', 'member', 'plan_mapping_show'),
)
ADDED_CAPABILITIES = (c.CapabilitySpec('import', ('member', 'standard'), ('member', 'standard')),)
ADDED_DEFAULTS = (
    c.DefaultEntry('readonly', c.Requirement('import', 'member')),
    *(c.DefaultEntry(role, c.Requirement('import', threshold))
      for role in ('standard', 'admin', 'owner', 'hub_admin') for threshold in ('member', 'standard')),
)
ADDED_COMMANDS = tuple(
    c.CommandDescriptor(name, 'company', 'import', threshold, (), True, None, False, False, None, None, None, None)
    for name, threshold, _ in _ADDED)
ADDED_ACTIONS = tuple(
    c.CompanyAction(name, (c.Requirement('import', threshold),), True, (_OWNER.format(planner),))
    for name, threshold, planner in _ADDED)
CATALOG = replace(previous.CATALOG, version=c.IMPORT_POLICY_VERSION,
    commands=tuple(sorted((*previous.CATALOG.commands, *ADDED_COMMANDS), key=lambda x: x.name)),
    capabilities=tuple(sorted((*previous.CATALOG.capabilities, *ADDED_CAPABILITIES), key=lambda x: x.name)),
    defaults=(*previous.CATALOG.defaults, *ADDED_DEFAULTS),
    company_actions=tuple(sorted((*previous.CATALOG.company_actions, *ADDED_ACTIONS), key=lambda x: x.key)))
MANIFEST = c.catalog_manifest(CATALOG, previous.MANIFEST.standalone_names)


def catalog_bundle():
    inventory = dict(manifest=asdict(MANIFEST), sources=[asdict(x) for x in CATALOG.conditional_sources])
    digest = hashlib.sha256(json.dumps(inventory, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return CatalogBundle(SOURCE_COMMIT, CATALOG, MANIFEST.standalone_names, digest)
