"""Establishing an automation is a capability (R187 increment 2) on bank-line-details-v1, without rewriting any accepted
descriptor.

design/principal-directives.md §3: an automation is a rule that writes with nobody asking at the time. Anyone with the
ordinary right to write the rule drafts it; making it live needs `automate.<kind>`; a live rule runs under its
establisher's authority, checked again at every firing. capabilities-v1 registered `automate.delivery` with R112's
acts; this layer registers the other two kinds, `automate.bank-rule` (R108's auto-add and ignore rules) and
`automate.invoice` (recurring or criteria-based invoicing; no consumer yet), each held by a person at standard, as
`automate.delivery` is, and by an agent only when its principal grants it (hub.capabilities is the gate).

It also adds `automation list`, `show` and `pause` under a new capability `automation`, held at member by every role
and at standard by standard, admin and owner, as list reads and writes are: each rule is then checked again against
its own kind's commands (company.automation.require_command), so `automation` grants nothing a kind's own commands
would not. The capability gate's declared requirements grow by the two new capabilities. No existing capability, role
default or threshold changes, so a root replacing its catalog with this one suspends no agent and revokes no token.
"""
from dataclasses import asdict, replace
import hashlib
import json

from . import permission_bank_line_details_catalog as previous, permission_catalog as c
from . import permission_capabilities_catalog as capabilities_v1
from .permission_snapshot import CatalogBundle

SOURCE_COMMIT = '0b3769e84c061467ebb6e11adfdc75cf8ba5d443'
_OWNER = 'bookflow.commands.automation_cmds.{}|src/bookflow/commands/automation_cmds.py'
# capability -> (threshold a person holds it at, the commands it opens): none opens a command of its own; each
# domain's own command makes its rules live, checked against the capability.
CAPABILITIES = {
    'automate.bank-rule': ('standard', ()),
    'automate.invoice': ('standard', ()),
}
# (name, threshold, planner)
_ADDED = (
    ('automation list', 'member', 'plan_list'),
    ('automation pause', 'standard', 'plan_pause'),
    ('automation show', 'member', 'plan_show'),
)
_RANK = ('readonly', 'standard', 'admin', 'owner')
ADDED_CAPABILITIES = tuple(sorted((
    c.CapabilitySpec('automation', ('member', 'standard'), ('member', 'standard')),
    *(c.CapabilitySpec(name, (threshold,), (threshold,)) for name, (threshold, _) in CAPABILITIES.items())),
    key=lambda x: x.name))
ADDED_DEFAULTS = (
    c.DefaultEntry('readonly', c.Requirement('automation', 'member')),
    *(c.DefaultEntry(role, c.Requirement('automation', threshold))
      for role in ('standard', 'admin', 'owner', 'hub_admin') for threshold in ('member', 'standard')),
    *(c.DefaultEntry(role, c.Requirement(name, threshold))
      for name, (threshold, _) in sorted(CAPABILITIES.items())
      for role in (*_RANK[_RANK.index(threshold):], 'hub_admin')),
)
ADDED_COMMANDS = tuple(
    c.CommandDescriptor(name, 'company', 'automation', threshold, (), True, None, False, False, None, None, None, None)
    for name, threshold, _ in _ADDED)
ADDED_ACTIONS = tuple(
    c.CompanyAction(name, (c.Requirement('automation', threshold),), True, (_OWNER.format(planner),))
    for name, threshold, planner in _ADDED)
# The capability gate admits the two new capabilities too: its declared requirements are capabilities-v1's and these.
GATE_SOURCE = replace(capabilities_v1.GATE_SOURCE, requirements=tuple(sorted(
    {*capabilities_v1.GATE_SOURCE.requirements,
     *(c.Requirement(name, threshold) for name, (threshold, _) in CAPABILITIES.items())},
    key=lambda r: (r.capability, r.threshold))))
# Each rule is read or paused only by a caller its own kind's commands admit (delivery schedules: `delivery-schedule
# show` and `update`). A kind added later declares its own commands' requirements in its own delta.
KIND_SOURCE = c.ResourceSource('bookflow.company.automation.require_command',
    (('src/bookflow/company/automation.py', 401),),
    (c.Requirement('customer', 'member'), c.Requirement('customer', 'standard'),
     c.Requirement('customer-work', 'member'), c.Requirement('ledger.read', 'member')))
CATALOG = replace(previous.CATALOG, version=c.AUTOMATION_POLICY_VERSION,
    commands=tuple(sorted((*previous.CATALOG.commands, *ADDED_COMMANDS), key=lambda x: x.name)),
    capabilities=tuple(sorted((*previous.CATALOG.capabilities, *ADDED_CAPABILITIES), key=lambda x: x.name)),
    defaults=(*previous.CATALOG.defaults, *ADDED_DEFAULTS),
    company_actions=tuple(sorted((*previous.CATALOG.company_actions, *ADDED_ACTIONS), key=lambda x: x.key)),
    conditional_sources=tuple(sorted(
        (*(x for x in previous.CATALOG.conditional_sources if x.owner != GATE_SOURCE.owner), GATE_SOURCE, KIND_SOURCE),
        key=lambda x: x.owner)))
MANIFEST = c.catalog_manifest(CATALOG, previous.MANIFEST.standalone_names)


def catalog_bundle():
    inventory = dict(manifest=asdict(MANIFEST), sources=[asdict(x) for x in CATALOG.conditional_sources])
    digest = hashlib.sha256(json.dumps(inventory, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return CatalogBundle(SOURCE_COMMIT, CATALOG, MANIFEST.standalone_names, digest)
