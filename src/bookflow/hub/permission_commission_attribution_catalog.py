"""Invoice line attribution on the immutable payroll-draft-v1 catalog."""
from dataclasses import asdict, replace
import hashlib
import json

from . import permission_payroll_draft_catalog as previous, permission_catalog as c
from .permission_snapshot import CatalogBundle

SOURCE_COMMIT = '087f61ff75dcc48fcf62b049337d7bb0295f9f19'
CAPABILITIES = {
    'commission.attribute': ('standard', ('standard', 'admin', 'owner', 'hub_admin')),
    'commission.read': ('standard', ('standard', 'admin', 'owner', 'hub_admin')),
    'commission.manage': ('admin', ('admin', 'owner', 'hub_admin')),
}
_OWNER = 'bookflow.commands.commission_cmds.{}|src/bookflow/commands/commission_cmds.py'
_ADDED = (
    ('commission recipient-options', 'commission.attribute', 'plan_recipient_options', ()),
    ('report commission-attribution', 'commission.read', 'plan_attribution', (c.Requirement('customer-work', 'member'),)),
)
ADDED_COMMANDS = tuple(c.CommandDescriptor(name, 'company', cap, 'standard', resources, True,
    None, False, False, None, None, None, None) for name, cap, _, resources in _ADDED)
ADDED_ACTIONS = tuple(c.CompanyAction(name, tuple(sorted((c.Requirement(cap, 'standard'), *resources),
    key=lambda r: (r.capability, r.threshold))), True, (_OWNER.format(planner),))
    for name, cap, planner, resources in _ADDED)
ADDED_CAPABILITIES = tuple(c.CapabilitySpec(name, (threshold,), (threshold,))
    for name, (threshold, _) in sorted(CAPABILITIES.items()))
ADDED_DEFAULTS = tuple(c.DefaultEntry(role, c.Requirement(name, threshold))
    for name, (threshold, roles) in sorted(CAPABILITIES.items()) for role in roles)
_REQUIREMENTS = tuple(c.Requirement(name, threshold) for name, (threshold, _) in sorted(CAPABILITIES.items()))
_GATE = next(x for x in previous.CATALOG.conditional_sources
             if x.owner == 'bookflow.company.capability_gate.require_held')
GATE_SOURCE = replace(_GATE, requirements=tuple(sorted((*_GATE.requirements, *_REQUIREMENTS),
    key=lambda r: (r.capability, r.threshold))))
ATTRIBUTION_SOURCE = c.ResourceSource('bookflow.company.commissions.require_capability',
    (('src/bookflow/company/commissions.py', 13),), _REQUIREMENTS)
CATALOG = replace(previous.CATALOG, version=c.COMMISSION_ATTRIBUTION_POLICY_VERSION,
    commands=tuple(sorted((*previous.CATALOG.commands, *ADDED_COMMANDS), key=lambda x: x.name)),
    company_actions=tuple(sorted((*previous.CATALOG.company_actions, *ADDED_ACTIONS), key=lambda x: x.key)),
    capabilities=tuple(sorted((*previous.CATALOG.capabilities, *ADDED_CAPABILITIES), key=lambda x: x.name)),
    defaults=(*previous.CATALOG.defaults, *ADDED_DEFAULTS),
    conditional_sources=tuple(sorted((*(x for x in previous.CATALOG.conditional_sources
        if x.owner != GATE_SOURCE.owner), GATE_SOURCE, ATTRIBUTION_SOURCE), key=lambda x: x.owner)))
MANIFEST = c.catalog_manifest(CATALOG, previous.MANIFEST.standalone_names)


def catalog_bundle():
    inventory = dict(manifest=asdict(MANIFEST), sources=[asdict(x) for x in CATALOG.conditional_sources])
    digest = hashlib.sha256(json.dumps(inventory, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return CatalogBundle(SOURCE_COMMIT, CATALOG, MANIFEST.standalone_names, digest)
