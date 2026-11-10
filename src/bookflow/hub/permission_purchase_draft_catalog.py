"""Saved non-posting purchase drafts on the immutable notice-reversal-v1 tip."""
from dataclasses import asdict, replace
import hashlib
import json
from . import permission_notice_reversal_catalog as previous, permission_catalog as c
from .permission_snapshot import CatalogBundle

SOURCE_COMMIT = 'bf41ae7ff3422aced7ab29023fcc07fac63f87b0'
_OWNER = 'bookflow.commands.purchase_draft_cmds.{}|src/bookflow/commands/purchase_draft_cmds.py'
_RECOVERY = _OWNER.format('_write.<locals>.<lambda>')
_ADDED = tuple((verb, 'approval.prepare' if verb in ('create', 'update', 'cancel') else 'approval.read')
               for verb in ('create', 'update', 'cancel', 'preview', 'show', 'list'))
ADDED_CAPABILITIES = tuple(c.CapabilitySpec(name, ('standard',), ('standard',))
                           for name in ('approval.prepare', 'approval.read'))
ADDED_DEFAULTS = tuple(c.DefaultEntry(role, c.Requirement(name, 'standard'))
    for role in ('standard', 'admin', 'owner', 'hub_admin')
    for name in ('approval.prepare', 'approval.read'))
ADDED_COMMANDS = tuple(c.CommandDescriptor('purchase-draft ' + verb, 'company', capability,
    'standard', (c.Requirement('ledger.read', 'member'),), True, None, False, False,
    None, None, _RECOVERY if capability == 'approval.prepare' else None, None) for verb, capability in _ADDED)
ADDED_ACTIONS = tuple(c.CompanyAction('purchase-draft ' + verb,
    (c.Requirement(capability, 'standard'), c.Requirement('ledger.read', 'member')), True,
    tuple(sorted((_OWNER.format('plan_' + verb), _RECOVERY))) if capability == 'approval.prepare'
    else (_OWNER.format('plan_' + verb),)) for verb, capability in _ADDED)
CATALOG = replace(previous.CATALOG, version=c.PURCHASE_DRAFT_POLICY_VERSION,
    commands=tuple(sorted((*previous.CATALOG.commands, *ADDED_COMMANDS), key=lambda x: x.name)),
    capabilities=tuple(sorted((*previous.CATALOG.capabilities, *ADDED_CAPABILITIES), key=lambda x: x.name)),
    defaults=(*previous.CATALOG.defaults, *ADDED_DEFAULTS),
    company_actions=tuple(sorted((*previous.CATALOG.company_actions, *ADDED_ACTIONS), key=lambda x: x.key)))
MANIFEST = c.catalog_manifest(CATALOG, previous.MANIFEST.standalone_names)


def catalog_bundle():
    inventory = dict(manifest=asdict(MANIFEST), sources=[asdict(x) for x in CATALOG.conditional_sources])
    digest = hashlib.sha256(json.dumps(inventory, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return CatalogBundle(SOURCE_COMMIT, CATALOG, MANIFEST.standalone_names, digest)
