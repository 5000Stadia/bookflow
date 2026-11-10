"""Private manual payroll drafts (R116 increment 1), on purchase-draft-v1.

Only administrators hold the three capabilities by default. Preparation and setup also
require payroll.read; changing the switch requires company administration. Agents need
explicit grants for every payroll capability they use, with principal intersection.
"""
from dataclasses import asdict, replace
import hashlib
import json
from . import permission_purchase_draft_catalog as previous, permission_catalog as c
from .permission_snapshot import CatalogBundle

SOURCE_COMMIT = '940402dffcc8cd2c591c9a2e5555998f974b787c'
CAPABILITIES = {'payroll.read': 'member', 'payroll.prepare': 'standard', 'payroll.configure': 'admin'}
_OWNER = 'bookflow.commands.payroll_cmds.{}|src/bookflow/commands/payroll_cmds.py'
_ADDED = (
    ('payroll setup', 'payroll.configure', 'plan_setup'),
    ('payroll settings', 'payroll.read', 'plan_settings'),
    ('payroll draft', 'payroll.prepare', 'plan_draft'),
    ('payroll show', 'payroll.read', 'plan_show'),
    ('payroll list', 'payroll.read', 'plan_list'),
    ('payroll prepare', 'payroll.prepare', 'plan_prepare'),
)


def resources(name, capability):
    return ((c.Requirement('company', 'admin'), c.Requirement('payroll.read', 'member')) if name == 'payroll setup'
            else (c.Requirement('payroll.read', 'member'),) if capability != 'payroll.read' else ())


ADDED_CAPABILITIES = tuple(c.CapabilitySpec(name, (threshold,), (threshold,))
                           for name, threshold in sorted(CAPABILITIES.items()))
ADDED_DEFAULTS = tuple(c.DefaultEntry(role, c.Requirement(name, threshold))
                       for name, threshold in sorted(CAPABILITIES.items()) for role in ('admin', 'owner', 'hub_admin'))
ADDED_COMMANDS = tuple(c.CommandDescriptor(name, 'company', capability, CAPABILITIES[capability],
    resources(name, capability), True, None, False, False, None, None, None, None)
    for name, capability, _ in _ADDED)
ADDED_ACTIONS = tuple(c.CompanyAction(name,
    tuple(sorted((c.Requirement(capability, CAPABILITIES[capability]), *resources(name, capability)),
                 key=lambda r: (r.capability, r.threshold))), True, (_OWNER.format(planner),))
    for name, capability, planner in _ADDED)
_GATE = next(x for x in previous.CATALOG.conditional_sources
             if x.owner == 'bookflow.company.capability_gate.require_held')
GATE_SOURCE = replace(_GATE, requirements=tuple(sorted(
    {*_GATE.requirements, *(c.Requirement(name, threshold) for name, threshold in CAPABILITIES.items())},
    key=lambda r: (r.capability, r.threshold))))
ARCHIVE_SOURCE = c.ResourceSource('bookflow.company.payroll_privacy.require_archive_read',
    (('src/bookflow/company/payroll_privacy.py', 15),), (c.Requirement('payroll.read', 'member'),))
CATALOG = replace(previous.CATALOG, version=c.PAYROLL_DRAFT_POLICY_VERSION,
    commands=tuple(sorted((*previous.CATALOG.commands, *ADDED_COMMANDS), key=lambda x: x.name)),
    capabilities=tuple(sorted((*previous.CATALOG.capabilities, *ADDED_CAPABILITIES), key=lambda x: x.name)),
    defaults=(*previous.CATALOG.defaults, *ADDED_DEFAULTS),
    company_actions=tuple(sorted((*previous.CATALOG.company_actions, *ADDED_ACTIONS), key=lambda x: x.key)),
    conditional_sources=tuple(sorted(
        (*(x for x in previous.CATALOG.conditional_sources if x.owner != GATE_SOURCE.owner), GATE_SOURCE, ARCHIVE_SOURCE),
        key=lambda x: x.owner)))
MANIFEST = c.catalog_manifest(CATALOG, previous.MANIFEST.standalone_names)


def catalog_bundle():
    inventory = dict(manifest=asdict(MANIFEST), sources=[asdict(x) for x in CATALOG.conditional_sources])
    digest = hashlib.sha256(json.dumps(inventory, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return CatalogBundle(SOURCE_COMMIT, CATALOG, MANIFEST.standalone_names, digest)
