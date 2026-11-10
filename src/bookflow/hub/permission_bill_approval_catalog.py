"""One-step bill approvals on the immutable purchase-draft-v1 catalog."""
from dataclasses import asdict, replace
import hashlib
import json
from . import permission_purchase_draft_catalog as previous, permission_catalog as c
from .permission_snapshot import CatalogBundle

SOURCE_COMMIT = 'b1b435aa1d433b272921de19f232e04d02f510a1'
_OWNER = 'bookflow.commands.approval_cmds.{}|src/bookflow/commands/approval_cmds.py'
_RECOVERY = _OWNER.format('recover')
# name, planner, capability, threshold, write, additional native authority
_ADDED = (
    ('purchase-draft submit', 'plan_submit', 'approval.prepare', 'standard', True, False),
    ('purchase-draft withdraw', 'plan_withdraw', 'approval.prepare', 'standard', True, False),
    ('purchase-draft commit', 'plan_commit', 'approval.prepare', 'standard', True, True),
    ('approval decide', 'plan_decide', 'approval.decide', 'admin', True, False),
    ('approval show', 'plan_show', 'approval.read', 'standard', False, False),
    ('approval history', 'plan_history', 'approval.read', 'standard', False, False),
    ('approval-policy create', 'plan_policy_create', 'approval.manage', 'admin', True, False),
    ('approval-policy update', 'plan_policy_update', 'approval.manage', 'admin', True, False),
    ('approval-policy publish', 'plan_policy_publish', 'approval.manage', 'admin', True, False),
    ('approval-policy show', 'plan_policy_show', 'approval.read', 'standard', False, False),
    ('approval-policy list', 'plan_policy_list', 'approval.read', 'standard', False, False),
)
ADDED_CAPABILITIES = tuple(c.CapabilitySpec(name, ('admin',), ('admin',))
                           for name in ('approval.decide', 'approval.manage'))
ADDED_DEFAULTS = tuple(c.DefaultEntry(role, c.Requirement(name, 'admin'))
    for role in ('admin', 'owner', 'hub_admin')
    for name in ('approval.decide', 'approval.manage'))


def _resources(native):
    return (c.Requirement('ledger.read', 'member'),) + ((c.Requirement('ledger.post', 'standard'),) if native else ())


ADDED_COMMANDS = tuple(c.CommandDescriptor(name, 'company', capability, threshold, _resources(native),
    True, None, False, False, None, None, _RECOVERY if write else None, None)
    for name, planner, capability, threshold, write, native in _ADDED)
ADDED_ACTIONS = tuple(c.CompanyAction(name,
    (c.Requirement(capability, threshold), *_resources(native)), True,
    tuple(sorted((_OWNER.format(planner), _RECOVERY))) if write else (_OWNER.format(planner),))
    for name, planner, capability, threshold, write, native in _ADDED)
CATALOG = replace(previous.CATALOG, version=c.BILL_APPROVAL_POLICY_VERSION,
    commands=tuple(sorted((*previous.CATALOG.commands, *ADDED_COMMANDS), key=lambda x: x.name)),
    capabilities=tuple(sorted((*previous.CATALOG.capabilities, *ADDED_CAPABILITIES), key=lambda x: x.name)),
    defaults=(*previous.CATALOG.defaults, *ADDED_DEFAULTS),
    company_actions=tuple(sorted((*previous.CATALOG.company_actions, *ADDED_ACTIONS), key=lambda x: x.key)))
MANIFEST = c.catalog_manifest(CATALOG, previous.MANIFEST.standalone_names)


def catalog_bundle():
    inventory = dict(manifest=asdict(MANIFEST), sources=[asdict(x) for x in CATALOG.conditional_sources])
    digest = hashlib.sha256(json.dumps(inventory, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return CatalogBundle(SOURCE_COMMIT, CATALOG, MANIFEST.standalone_names, digest)
