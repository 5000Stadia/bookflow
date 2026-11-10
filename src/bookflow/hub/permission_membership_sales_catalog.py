"""Membership across sales and jobs (R188 increment 2) on the immutable payroll-post-v1 catalog.

Three commands, each under the capabilities increment 1 added (no new capability, no new default):
`customer membership inherit` (promotion.assign and customer write: whether a job takes its parent's membership),
`customer membership list` (promotion.read and customer read: who is a member on a date, jobs included) and
`customer membership enroll` (promotion.assign and customer write: one reversible batch of enrollments).
`batch reverse` of a `membership-enroll` batch reads and deletes through the caller's own `customer membership show`
and `customer membership set` authority via `bookflow.company.automation.require_command`, so that source's
declared requirements gain `promotion.read` and `promotion.assign` at standard.
"""
from dataclasses import asdict, replace
import hashlib
import json

from . import permission_payroll_post_catalog as previous, permission_catalog as c
from .permission_snapshot import CatalogBundle

SOURCE_COMMIT = '645bba8c7a5693413e1abafac9d485d48c3403c7'
_OWNER = 'bookflow.commands.membership_cmds.{}|src/bookflow/commands/membership_cmds.py'
_WRITE = (c.Requirement('customer', 'standard'),)
_READ = (c.Requirement('customer', 'member'),)
# name, planner, capability, additional ordinary resource
_ADDED = (
    ('customer membership inherit', 'plan_inherit', 'promotion.assign', _WRITE),
    ('customer membership list', 'plan_members', 'promotion.read', _READ),
    ('customer membership enroll', 'plan_enroll', 'promotion.assign', _WRITE),
)
ADDED_COMMANDS = tuple(c.CommandDescriptor(name, 'company', capability, 'standard', resources, True,
    None, False, False, None, None, None, None) for name, _, capability, resources in _ADDED)
ADDED_ACTIONS = tuple(c.CompanyAction(name, (c.Requirement(capability, 'standard'), *resources), True,
    (_OWNER.format(planner),)) for name, planner, capability, resources in _ADDED)
_KIND = next(x for x in previous.CATALOG.conditional_sources
             if x.owner == 'bookflow.company.automation.require_command')
KIND_SOURCE = replace(_KIND, call_sites=(('src/bookflow/company/automation.py', 401),),
    requirements=tuple(sorted({*_KIND.requirements, c.Requirement('promotion.assign', 'standard'),
                               c.Requirement('promotion.read', 'standard')},
                              key=lambda r: (r.capability, r.threshold))))
CATALOG = replace(previous.CATALOG, version=c.MEMBERSHIP_SALES_POLICY_VERSION,
    commands=tuple(sorted((*previous.CATALOG.commands, *ADDED_COMMANDS), key=lambda x: x.name)),
    company_actions=tuple(sorted((*previous.CATALOG.company_actions, *ADDED_ACTIONS), key=lambda x: x.key)),
    conditional_sources=tuple(sorted((*(x for x in previous.CATALOG.conditional_sources
        if x.owner != KIND_SOURCE.owner), KIND_SOURCE), key=lambda x: x.owner)))
MANIFEST = c.catalog_manifest(CATALOG, previous.MANIFEST.standalone_names)


def catalog_bundle():
    inventory = dict(manifest=asdict(MANIFEST), sources=[asdict(x) for x in CATALOG.conditional_sources])
    digest = hashlib.sha256(json.dumps(inventory, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return CatalogBundle(SOURCE_COMMIT, CATALOG, MANIFEST.standalone_names, digest)
