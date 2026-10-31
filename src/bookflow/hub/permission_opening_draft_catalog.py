"""Add guided opening balances (R107 increment 7) on automation-v1, without rewriting any accepted descriptor.

`opening-draft show` and `query` are member reads and `create`, `update`, `discard`, `plan`, `apply`, `tie-out` and
`reverse` standard writes, under the existing capability `import` (design/bulk-import.md §8); every posting is the
cutover engine's, each through its own command and capability, so the draft grants nothing. One new capability,
`opening.accept-differences` (standard, held by standard, admin and owner as every other standard act is), asked only
when `apply` posts an opening whose given totals do not tie: a person keeps the right to do that, and an agent holds it
only by its principal's explicit grant (R187, design/principal-directives.md §1; it takes the place §4 gave
`import.approve`, whose other acts were dropped on 2026-10-28). No existing capability, role default or threshold
changes, so a root replacing its catalog with this one suspends no agent and revokes no token.
"""
from dataclasses import asdict, replace
import hashlib
import json

from . import permission_automation_catalog as previous, permission_catalog as c
from .permission_snapshot import CatalogBundle

SOURCE_COMMIT = '06c29f99e0f2ea31a76b3e83877467cab5022c8b'
ACCEPT_CAPABILITY = 'opening.accept-differences'
_OWNER = 'bookflow.commands.opening_draft_cmds.{}|src/bookflow/commands/opening_draft_cmds.py'
# (name, threshold, planner)
_ADDED = (
    ('opening-draft apply', 'standard', 'plan_apply'),
    ('opening-draft create', 'standard', 'plan_create'),
    ('opening-draft discard', 'standard', 'plan_discard'),
    ('opening-draft plan', 'standard', 'plan_plan'),
    ('opening-draft query', 'member', 'plan_query'),
    ('opening-draft reverse', 'standard', 'plan_reverse'),
    ('opening-draft show', 'member', 'plan_show'),
    ('opening-draft tie-out', 'standard', 'plan_tie_out'),
    ('opening-draft update', 'standard', 'plan_update'),
)
ADDED_CAPABILITIES = (c.CapabilitySpec(ACCEPT_CAPABILITY, ('standard',), ('standard',)),)
ADDED_DEFAULTS = tuple(c.DefaultEntry(role, c.Requirement(ACCEPT_CAPABILITY, 'standard'))
                       for role in ('standard', 'admin', 'owner', 'hub_admin'))
ADDED_COMMANDS = tuple(
    c.CommandDescriptor(name, 'company', 'import', threshold, (), True, None, False, False, None, None, None, None)
    for name, threshold, _ in _ADDED)
ADDED_ACTIONS = tuple(
    c.CompanyAction(name, (c.Requirement('import', threshold),), True, (_OWNER.format(planner),))
    for name, threshold, planner in _ADDED)
# `apply` with accept_differences asks for the capability at one declared site.
ACCEPT_SOURCE = c.ResourceSource('bookflow.company.opening_drafts.require_accept',
                                 (('src/bookflow/company/opening_drafts.py', 303),),
                                 (c.Requirement(ACCEPT_CAPABILITY, 'standard'),))
CATALOG = replace(previous.CATALOG, version=c.OPENING_DRAFT_POLICY_VERSION,
    commands=tuple(sorted((*previous.CATALOG.commands, *ADDED_COMMANDS), key=lambda x: x.name)),
    capabilities=tuple(sorted((*previous.CATALOG.capabilities, *ADDED_CAPABILITIES), key=lambda x: x.name)),
    defaults=(*previous.CATALOG.defaults, *ADDED_DEFAULTS),
    company_actions=tuple(sorted((*previous.CATALOG.company_actions, *ADDED_ACTIONS), key=lambda x: x.key)),
    conditional_sources=tuple(sorted((*previous.CATALOG.conditional_sources, ACCEPT_SOURCE), key=lambda x: x.owner)))
MANIFEST = c.catalog_manifest(CATALOG, previous.MANIFEST.standalone_names)


def catalog_bundle():
    inventory = dict(manifest=asdict(MANIFEST), sources=[asdict(x) for x in CATALOG.conditional_sources])
    digest = hashlib.sha256(json.dumps(inventory, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return CatalogBundle(SOURCE_COMMIT, CATALOG, MANIFEST.standalone_names, digest)
