"""Commission attribution at the till (R189 increment 2) on the immutable intercompany-setup-v1 catalog.

Selecting who sold a line becomes a `member` act, so a `readonly` POS clerk can hold `commission.attribute` by an
explicit grant: standard, admin, owner and installation administrators hold it at `member` by default, as they
already did at `standard`. The plain clerk gains nothing; the "Clerk with commission attribution" preset grants only
`commission.attribute` beside `pos.sell` and records its marker `preset.pos-clerk-commission` (no command requires a
marker). One command: `pos commission-options` (`pos.sell`, member), the till's recipient lookup, names and IDs
only; its plan asks `commission.attribute` through `bookflow.company.commissions.require_capability`, whose declared
requirements (and the agent gate's) gain the member threshold. `commission.read` and `commission.manage` do not
change.
"""
from dataclasses import asdict, replace
import hashlib
import json

from . import permission_intercompany_setup_catalog as previous, permission_catalog as c
from .permission_snapshot import CatalogBundle

SOURCE_COMMIT = '513641afd9b8273ca49c7bd0ab5638dcbe2911ea'
_OWNER = 'bookflow.commands.pos_sale_cmds.{}|src/bookflow/commands/pos_sale_cmds.py'
ATTRIBUTE = c.Requirement('commission.attribute', 'member')
PRESET_MARKER = 'preset.pos-clerk-commission'
_ROLES = ('standard', 'admin', 'owner', 'hub_admin')

ADDED_COMMANDS = (c.CommandDescriptor('pos commission-options', 'company', 'pos.sell', 'member', (), True,
    None, False, False, None, None, None, None),)
ADDED_ACTIONS = (c.CompanyAction('pos commission-options', (c.Requirement('pos.sell', 'member'),), True,
    (_OWNER.format('plan_commission_options'),)),)
_ATTRIBUTE_SPEC = c.CapabilitySpec('commission.attribute', ('member', 'standard'), ('member', 'standard'))
ADDED_CAPABILITIES = (c.CapabilitySpec(PRESET_MARKER, ('member',), ('member',)),)
ADDED_DEFAULTS = tuple(c.DefaultEntry(role, ATTRIBUTE) for role in _ROLES)
_SOURCE = next(x for x in previous.CATALOG.conditional_sources
               if x.owner == 'bookflow.company.commissions.require_capability')
ATTRIBUTION_SOURCE = replace(_SOURCE, call_sites=(('src/bookflow/company/commissions.py', 13),),
    requirements=tuple(sorted((*_SOURCE.requirements, ATTRIBUTE), key=lambda r: (r.capability, r.threshold))))
_GATE = next(x for x in previous.CATALOG.conditional_sources
             if x.owner == 'bookflow.company.capability_gate.require_held')
GATE_SOURCE = replace(_GATE, requirements=tuple(sorted((*_GATE.requirements, ATTRIBUTE),
    key=lambda r: (r.capability, r.threshold))))
_REPLACED = {ATTRIBUTION_SOURCE.owner, GATE_SOURCE.owner}
CATALOG = replace(previous.CATALOG, version=c.COMMISSION_POS_POLICY_VERSION,
    commands=tuple(sorted((*previous.CATALOG.commands, *ADDED_COMMANDS), key=lambda x: x.name)),
    company_actions=tuple(sorted((*previous.CATALOG.company_actions, *ADDED_ACTIONS), key=lambda x: x.key)),
    capabilities=tuple(sorted((*(x for x in previous.CATALOG.capabilities if x.name != _ATTRIBUTE_SPEC.name),
                               _ATTRIBUTE_SPEC, *ADDED_CAPABILITIES), key=lambda x: x.name)),
    defaults=(*previous.CATALOG.defaults, *ADDED_DEFAULTS),
    conditional_sources=tuple(sorted((*(x for x in previous.CATALOG.conditional_sources
        if x.owner not in _REPLACED), ATTRIBUTION_SOURCE, GATE_SOURCE), key=lambda x: x.owner)))
MANIFEST = c.catalog_manifest(CATALOG, previous.MANIFEST.standalone_names)


def catalog_bundle():
    inventory = dict(manifest=asdict(MANIFEST), sources=[asdict(x) for x in CATALOG.conditional_sources])
    digest = hashlib.sha256(json.dumps(inventory, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return CatalogBundle(SOURCE_COMMIT, CATALOG, MANIFEST.standalone_names, digest)
