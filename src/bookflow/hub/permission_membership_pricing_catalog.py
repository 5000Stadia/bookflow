"""Membership plans and dated enrollments (R188 increment 1) on the immutable bill-approval-v1 catalog."""
from dataclasses import asdict, replace
import hashlib
import json
from . import permission_bill_approval_catalog as previous, permission_catalog as c
from .permission_snapshot import CatalogBundle

SOURCE_COMMIT = '406b8f5bd87eddd0f1c581d27d126f8b45e3919f'
_OWNER = 'bookflow.commands.membership_cmds.{}|src/bookflow/commands/membership_cmds.py'
# name, planner, capability, additional ordinary resource
_ADDED = (
    ('membership-plan create', 'plan_create', 'promotion.manage', ()),
    ('membership-plan update', 'plan_update', 'promotion.manage', ()),
    ('membership-plan show', 'plan_show', 'promotion.read', ()),
    ('membership-plan list', 'plan_list', 'promotion.read', ()),
    ('customer membership set', 'plan_set', 'promotion.assign', (c.Requirement('customer', 'standard'),)),
    ('customer membership end', 'plan_end', 'promotion.assign', (c.Requirement('customer', 'standard'),)),
    ('customer membership show', 'plan_membership_show', 'promotion.read', (c.Requirement('customer', 'member'),)),
)
ADDED_CAPABILITIES = tuple(c.CapabilitySpec(name, ('standard',), ('standard',))
                           for name in ('promotion.assign', 'promotion.manage', 'promotion.read'))
# Design R188 section 9: read and assign default to standard people; managing plans to admins. An agent
# holds none by default (hub/capabilities.py asks for an explicit grant).
ADDED_DEFAULTS = tuple(c.DefaultEntry(role, c.Requirement(name, 'standard'))
    for role in ('standard', 'admin', 'owner', 'hub_admin')
    for name in ('promotion.read', 'promotion.assign')) + tuple(
    c.DefaultEntry(role, c.Requirement('promotion.manage', 'standard')) for role in ('admin', 'owner', 'hub_admin'))
ADDED_COMMANDS = tuple(c.CommandDescriptor(name, 'company', capability, 'standard', resources, True,
    None, False, False, None, None, None, None) for name, _, capability, resources in _ADDED)
ADDED_ACTIONS = tuple(c.CompanyAction(name, (c.Requirement(capability, 'standard'), *resources), True,
    (_OWNER.format(planner),)) for name, planner, capability, resources in _ADDED)
CATALOG = replace(previous.CATALOG, version=c.MEMBERSHIP_PRICING_POLICY_VERSION,
    commands=tuple(sorted((*previous.CATALOG.commands, *ADDED_COMMANDS), key=lambda x: x.name)),
    capabilities=tuple(sorted((*previous.CATALOG.capabilities, *ADDED_CAPABILITIES), key=lambda x: x.name)),
    defaults=(*previous.CATALOG.defaults, *ADDED_DEFAULTS),
    company_actions=tuple(sorted((*previous.CATALOG.company_actions, *ADDED_ACTIONS), key=lambda x: x.key)))
MANIFEST = c.catalog_manifest(CATALOG, previous.MANIFEST.standalone_names)


def catalog_bundle():
    inventory = dict(manifest=asdict(MANIFEST), sources=[asdict(x) for x in CATALOG.conditional_sources])
    digest = hashlib.sha256(json.dumps(inventory, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return CatalogBundle(SOURCE_COMMIT, CATALOG, MANIFEST.standalone_names, digest)
