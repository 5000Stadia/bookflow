"""Add ringing up at the POS (R114 increment 5, design/point-of-sale.md §3, §4, §9) on bank-rule-v1, without rewriting any
accepted descriptor.

Four commands, each under `pos.sell` at threshold `member` (held by standard, admin and owner, and by a `readonly`
clerk only through an explicit grant): `pos sale preview` and `pos customer search` (reads), `pos sale complete` (posts
the sales receipt `sales-receipt post` would, with the facts the register fixes, so `pos.sell` reaches no other
account, date or document) and `pos customer add`. A line with a typed price also needs `pos.price-override` (the
conditional source below). No capability, role default or threshold changes.
"""
from dataclasses import asdict, replace
import hashlib
import json

from . import permission_bank_rule_catalog as previous, permission_catalog as c
from .permission_snapshot import CatalogBundle

SOURCE_COMMIT = '89622d32040590d2f54760a800312d656a390b99'
_OWNER = 'bookflow.commands.pos_sale_cmds.{}|src/bookflow/commands/pos_sale_cmds.py'
_CONDITIONAL = 'bookflow.company.pos_sales.authorize|src/bookflow/company/pos_sales.py'
_SELL = c.Requirement('pos.sell', 'member')
# (name, planner, conditional owner)
_ADDED = (
    ('pos customer add', 'plan_customer_add', None),
    ('pos customer search', 'plan_customer_search', None),
    ('pos sale complete', 'plan_complete', _CONDITIONAL),
    ('pos sale preview', 'plan_preview', _CONDITIONAL),
)
ADDED_COMMANDS = tuple(
    c.CommandDescriptor(name, 'company', 'pos.sell', 'member', (), True, None, False, False,
                        'member pos.sell; pos.price-override when a line has unit_price' if owner else None,
                        owner, None, None)
    for name, _, owner in _ADDED)
ADDED_ACTIONS = tuple(c.CompanyAction(name, (_SELL,), True, tuple(sorted((_OWNER.format(planner), *((owner,) if owner else ())))))
                      for name, planner, owner in _ADDED)
# A typed price on a POS line (company/pos_sales.py require_price_override).
OVERRIDE_SOURCE = c.ResourceSource('bookflow.company.pos_sales.require_price_override',
    (('src/bookflow/company/pos_sales.py', 143),), (c.Requirement('pos.price-override', 'member'),))
CATALOG = replace(previous.CATALOG, version=c.POS_SALE_POLICY_VERSION,
    commands=tuple(sorted((*previous.CATALOG.commands, *ADDED_COMMANDS), key=lambda x: x.name)),
    company_actions=tuple(sorted((*previous.CATALOG.company_actions, *ADDED_ACTIONS), key=lambda x: x.key)),
    conditional_sources=tuple(sorted((*previous.CATALOG.conditional_sources, OVERRIDE_SOURCE),
                                     key=lambda x: x.owner)))
MANIFEST = c.catalog_manifest(CATALOG, previous.MANIFEST.standalone_names)


def catalog_bundle():
    inventory = dict(manifest=asdict(MANIFEST), sources=[asdict(x) for x in CATALOG.conditional_sources])
    digest = hashlib.sha256(json.dumps(inventory, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return CatalogBundle(SOURCE_COMMIT, CATALOG, MANIFEST.standalone_names, digest)
