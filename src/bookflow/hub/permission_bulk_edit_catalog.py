"""Add grid edits (R107 increment 6) without rewriting any accepted descriptor.

A new capability, `bulk-edit` (design/bulk-import.md §8), held at `member` by every role and at `standard` by
standard, admin and owner, as list writes are: `bulk-edit columns`, `grid`, `show` and `query` are member reads;
`bulk-edit preview`, `apply` and `reverse` are standard writes, each record (and each restoring step of a reversal)
checked again against its own `update` command's capability, so the grid grants nothing. No approval capability and
no size threshold (Kabe, 2026-10-28: sensitivity is impact, not quantity). No existing capability, role default or
threshold changes, so a root replacing its catalog with this one suspends no agent and revokes no token.
"""
from dataclasses import asdict, replace
import hashlib
import json

from . import permission_bank_line_catalog as previous, permission_catalog as c
from .permission_snapshot import CatalogBundle

SOURCE_COMMIT = '034b64050e284452d9c597f9edf5b849c773c63b'
_OWNER = 'bookflow.commands.bulk_edit_cmds.{}|src/bookflow/commands/bulk_edit_cmds.py'
# (name, threshold, planner)
_ADDED = (
    ('bulk-edit apply', 'standard', 'plan_apply'),
    ('bulk-edit columns', 'member', 'plan_columns'),
    ('bulk-edit grid', 'member', 'plan_grid'),
    ('bulk-edit preview', 'standard', 'plan_preview'),
    ('bulk-edit query', 'member', 'plan_query'),
    ('bulk-edit reverse', 'standard', 'plan_reverse'),
    ('bulk-edit show', 'member', 'plan_show'),
)
ADDED_CAPABILITIES = (c.CapabilitySpec('bulk-edit', ('member', 'standard'), ('member', 'standard')),)
ADDED_DEFAULTS = (
    c.DefaultEntry('readonly', c.Requirement('bulk-edit', 'member')),
    *(c.DefaultEntry(role, c.Requirement('bulk-edit', threshold))
      for role in ('standard', 'admin', 'owner', 'hub_admin') for threshold in ('member', 'standard')),
)
ADDED_COMMANDS = tuple(
    c.CommandDescriptor(name, 'company', 'bulk-edit', threshold, (), True, None, False, False, None, None, None, None)
    for name, threshold, _ in _ADDED)
ADDED_ACTIONS = tuple(
    c.CompanyAction(name, (c.Requirement('bulk-edit', threshold),), True, (_OWNER.format(planner),))
    for name, threshold, planner in _ADDED)
CATALOG = replace(previous.CATALOG, version=c.BULK_EDIT_POLICY_VERSION,
    commands=tuple(sorted((*previous.CATALOG.commands, *ADDED_COMMANDS), key=lambda x: x.name)),
    capabilities=tuple(sorted((*previous.CATALOG.capabilities, *ADDED_CAPABILITIES), key=lambda x: x.name)),
    defaults=(*previous.CATALOG.defaults, *ADDED_DEFAULTS),
    company_actions=tuple(sorted((*previous.CATALOG.company_actions, *ADDED_ACTIONS), key=lambda x: x.key)))
MANIFEST = c.catalog_manifest(CATALOG, previous.MANIFEST.standalone_names)


def catalog_bundle():
    inventory = dict(manifest=asdict(MANIFEST), sources=[asdict(x) for x in CATALOG.conditional_sources])
    digest = hashlib.sha256(json.dumps(inventory, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return CatalogBundle(SOURCE_COMMIT, CATALOG, MANIFEST.standalone_names, digest)
