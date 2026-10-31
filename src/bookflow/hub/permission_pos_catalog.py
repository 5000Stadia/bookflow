"""Add the POS layer (R114 increment 4, design/point-of-sale.md §9) on automation-v1, without rewriting any accepted
descriptor.

Five new capabilities. `pos.sell` (ringing up, holding, looking up customers, a drawer's shift, the day summary for
one's own register), `pos.void`, `pos.refund` and `pos.price-override` are held by standard, admin and owner (and
installation administrators) at threshold `member`, so a `readonly` member holds one only by an explicit grant: the
POS clerk preset is `readonly` plus a grant of `pos.sell`. `pos.layout` (the tap boxes and the `pos-register` writes)
is held at `admin`. An agent holds none of them by its role: `hub.capabilities` asks it for an explicit grant, as it
does the capabilities-v1 acts, then its principal's holding.

This layer's commands: `pos-register show` and `list` (`pos.sell`, `member`) and `pos-register create` and `update`
(`pos.layout`, `admin`). One existing descriptor family changes: the shipment, shipment-return and pick-list reads
also require `customer` (member), since those records name customers. No capability, role default or threshold
changes.
"""
from dataclasses import asdict, replace
import hashlib
import json

from . import permission_automation_catalog as previous, permission_catalog as c
from .permission_snapshot import CatalogBundle

SOURCE_COMMIT = '06c29f99e0f2ea31a76b3e83877467cab5022c8b'
_OWNER = 'bookflow.commands.pos_register_cmds.{}|src/bookflow/commands/pos_register_cmds.py'
# capability -> (threshold its commands name, the roles holding it by default)
_SELLER = ('standard', 'admin', 'owner', 'hub_admin')
CAPABILITIES = {
    'pos.sell': ('member', _SELLER),
    'pos.void': ('member', _SELLER),
    'pos.refund': ('member', _SELLER),
    'pos.price-override': ('member', _SELLER),
    'pos.layout': ('admin', ('admin', 'owner', 'hub_admin')),
}
# (name, capability, threshold, planner)
_ADDED = (
    ('pos-register create', 'pos.layout', 'admin', 'plan_create'),
    ('pos-register list', 'pos.sell', 'member', 'plan_list'),
    ('pos-register show', 'pos.sell', 'member', 'plan_show'),
    ('pos-register update', 'pos.layout', 'admin', 'plan_update'),
)
# The presets' markers (hub/pos_presets.py): a grant recording which preset a membership was made from. No command
# requires one and no role holds one, so granting it gives nothing.
PRESET_MARKERS = ('preset.pos-clerk', 'preset.pos-clerk-returns')
ADDED_CAPABILITIES = tuple(sorted((*(c.CapabilitySpec(name, (threshold,), (threshold,))
                                     for name, (threshold, _) in CAPABILITIES.items()),
                                   *(c.CapabilitySpec(name, ('member',), ('member',)) for name in PRESET_MARKERS)),
                                  key=lambda x: x.name))
ADDED_DEFAULTS = tuple(c.DefaultEntry(role, c.Requirement(name, threshold))
                       for name, (threshold, roles) in sorted(CAPABILITIES.items()) for role in roles)
ADDED_COMMANDS = tuple(
    c.CommandDescriptor(name, 'company', capability, threshold, (), True, None, False, False, None, None, None, None)
    for name, capability, threshold, _ in _ADDED)
ADDED_ACTIONS = tuple(
    c.CompanyAction(name, (c.Requirement(capability, threshold),), True, (_OWNER.format(planner),))
    for name, capability, threshold, planner in _ADDED)
# Reads that name customers (shipments, their returns, pick lists) also need `customer` (member), for everyone: a
# reader holding `item` alone, such as a POS clerk, sees items, not who was shipped what (review of increment 4).
CUSTOMER_READ = c.Requirement('customer', 'member')
CUSTOMER_NAMING_READS = ('pick-list query', 'pick-list show', 'shipment history', 'shipment query', 'shipment show',
                         'shipment-return query', 'shipment-return show')


def _with_customer(x):
    if x.name in CUSTOMER_NAMING_READS:
        return replace(x, resources=tuple(sorted((*x.resources, CUSTOMER_READ), key=lambda r: (r.capability, r.threshold))))
    return x


def _action_with_customer(x):
    if x.key in CUSTOMER_NAMING_READS:
        return replace(x, requirements=tuple(sorted((*x.requirements, CUSTOMER_READ), key=lambda r: r.capability)))
    return x


# The agent's half of each POS capability is admitted at the capabilities-v1 gate (hub.capabilities ->
# company.capability_gate), as automation-v1's are.
GATE_SOURCE = replace(previous.GATE_SOURCE, requirements=tuple(sorted(
    {*previous.GATE_SOURCE.requirements, *(c.Requirement(name, threshold) for name, (threshold, _) in CAPABILITIES.items())},
    key=lambda r: (r.capability, r.threshold))))
# An item read shows costs only with ledger.read and vendors only with vendor (company/item_concealment.py).
CONCEALMENT_SOURCE = c.ResourceSource('bookflow.company.item_concealment.holds',
    (('src/bookflow/company/item_concealment.py', 26),),
    (c.Requirement('ledger.read', 'member'), c.Requirement('vendor', 'member')))
CATALOG = replace(previous.CATALOG, version=c.POS_POLICY_VERSION,
    commands=tuple(sorted((*(_with_customer(x) for x in previous.CATALOG.commands), *ADDED_COMMANDS),
                          key=lambda x: x.name)),
    capabilities=tuple(sorted((*previous.CATALOG.capabilities, *ADDED_CAPABILITIES), key=lambda x: x.name)),
    defaults=(*previous.CATALOG.defaults, *ADDED_DEFAULTS),
    company_actions=tuple(sorted((*(_action_with_customer(x) for x in previous.CATALOG.company_actions),
                                  *ADDED_ACTIONS), key=lambda x: x.key)),
    conditional_sources=tuple(sorted(
        (*(x for x in previous.CATALOG.conditional_sources if x.owner != GATE_SOURCE.owner), GATE_SOURCE,
         CONCEALMENT_SOURCE), key=lambda x: x.owner)))
MANIFEST = c.catalog_manifest(CATALOG, previous.MANIFEST.standalone_names)


def catalog_bundle():
    inventory = dict(manifest=asdict(MANIFEST), sources=[asdict(x) for x in CATALOG.conditional_sources])
    digest = hashlib.sha256(json.dumps(inventory, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return CatalogBundle(SOURCE_COMMIT, CATALOG, MANIFEST.standalone_names, digest)
