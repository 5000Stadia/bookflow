"""Posting one paycheck, net pay by check and voids (R116 increment 2) on the immutable crm-convert-v1 catalog.

Three new payroll capabilities at `standard`, held by default by admin, owner and installation administrators only
(as increment 1's are): `payroll.post` posts a prepared draft, `payroll.pay` records the one check that settles its
net pay, `payroll.void` voids either. Each write also needs `payroll.read` and `ledger.post`; agents need explicit
grants for every one, intersected with their principal's rights. Six commands: `payroll post`, `payroll pay-net`,
`payroll void`, `payroll void-payment`, `payroll paycheck` and `payroll paychecks` (reads, `payroll.read`).

One new conditional source, `bookflow.company.payroll_paychecks.require`: the extra rights a paycheck write or the
`payroll` batch adapter asks at run time (`payroll.pay` for a check recorded with `payroll post`; reading and
reversing a paycheck batch). The agent gate learns the three capabilities, and audit visibility now asks
`payroll.read` for every `payroll_` record type (and for no other), so an event that posts or voids a paycheck needs it
to be seen.
"""
from dataclasses import asdict, replace
import hashlib
import json

from . import permission_crm_convert_catalog as previous, permission_catalog as c
from .permission_snapshot import CatalogBundle

SOURCE_COMMIT = 'b078189fea0a7d215f7f1625ff0ff9d4263ab5f7'
_OWNER = 'bookflow.commands.payroll_cmds.{}|src/bookflow/commands/payroll_cmds.py'
CAPABILITIES = {'payroll.post': 'standard', 'payroll.pay': 'standard', 'payroll.void': 'standard'}
READ, POST = c.Requirement('payroll.read', 'member'), c.Requirement('ledger.post', 'standard')
_ADDED = (
    ('payroll post', 'payroll.post', 'plan_post'),
    ('payroll pay-net', 'payroll.pay', 'plan_pay'),
    ('payroll void', 'payroll.void', 'plan_void'),
    ('payroll void-payment', 'payroll.void', 'plan_void_payment'),
    ('payroll paycheck', 'payroll.read', 'plan_paycheck'),
    ('payroll paychecks', 'payroll.read', 'plan_paychecks'),
)


def _sorted(requirements):
    return tuple(sorted(set(requirements), key=lambda r: (r.capability, r.threshold)))


def resources(capability):
    return () if capability == 'payroll.read' else (READ, POST)


def _own(capability):
    return c.Requirement(capability, CAPABILITIES.get(capability, 'member'))


ADDED_CAPABILITIES = tuple(c.CapabilitySpec(name, (threshold,), (threshold,))
                           for name, threshold in sorted(CAPABILITIES.items()))
ADDED_DEFAULTS = tuple(c.DefaultEntry(role, c.Requirement(name, threshold))
                       for name, threshold in sorted(CAPABILITIES.items()) for role in ('admin', 'owner', 'hub_admin'))
ADDED_COMMANDS = tuple(c.CommandDescriptor(name, 'company', capability, _own(capability).threshold,
    _sorted(resources(capability)), True, None, False, False, None, None, None, None)
    for name, capability, _ in _ADDED)
ADDED_ACTIONS = tuple(c.CompanyAction(name, _sorted((_own(capability), *resources(capability))), True,
    (_OWNER.format(planner),)) for name, capability, planner in _ADDED)
_NEW = tuple(c.Requirement(name, threshold) for name, threshold in CAPABILITIES.items())
_GATE = next(x for x in previous.CATALOG.conditional_sources
             if x.owner == 'bookflow.company.capability_gate.require_held')
GATE_SOURCE = replace(_GATE, requirements=_sorted((*_GATE.requirements, *_NEW)))
_AUDIT = next(x for x in previous.CATALOG.conditional_sources if x.owner == 'bookflow.company.audit_visibility.admitted')
AUDIT_SOURCE = replace(_AUDIT, call_sites=(('src/bookflow/company/audit_visibility.py', 109),),
    requirements=_sorted((*_AUDIT.requirements, READ)))
PAYCHECK_SOURCE = c.ResourceSource('bookflow.company.payroll_paychecks.require',
    (('src/bookflow/company/payroll_paychecks.py', 51),),
    _sorted((READ, c.Requirement('ledger.read', 'member'), c.Requirement('payroll.pay', 'standard'),
             c.Requirement('payroll.void', 'standard'), POST)))
_REPLACED = {GATE_SOURCE.owner, AUDIT_SOURCE.owner}
CATALOG = replace(previous.CATALOG, version=c.PAYROLL_POST_POLICY_VERSION,
    commands=tuple(sorted((*previous.CATALOG.commands, *ADDED_COMMANDS), key=lambda x: x.name)),
    company_actions=tuple(sorted((*previous.CATALOG.company_actions, *ADDED_ACTIONS), key=lambda x: x.key)),
    capabilities=tuple(sorted((*previous.CATALOG.capabilities, *ADDED_CAPABILITIES), key=lambda x: x.name)),
    defaults=(*previous.CATALOG.defaults, *ADDED_DEFAULTS),
    conditional_sources=tuple(sorted((*(x for x in previous.CATALOG.conditional_sources if x.owner not in _REPLACED),
                                      GATE_SOURCE, AUDIT_SOURCE, PAYCHECK_SOURCE), key=lambda x: x.owner)))
MANIFEST = c.catalog_manifest(CATALOG, previous.MANIFEST.standalone_names)


def catalog_bundle():
    inventory = dict(manifest=asdict(MANIFEST), sources=[asdict(x) for x in CATALOG.conditional_sources])
    digest = hashlib.sha256(json.dumps(inventory, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return CatalogBundle(SOURCE_COMMIT, CATALOG, MANIFEST.standalone_names, digest)
