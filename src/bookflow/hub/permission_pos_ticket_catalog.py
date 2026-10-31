"""Held ticket lifecycle and POS void on the immutable pos-layout-v1 tip."""
from dataclasses import asdict, replace
import hashlib
import json
from . import permission_pos_layout_catalog as previous, permission_catalog as c
from .permission_snapshot import CatalogBundle

SOURCE_COMMIT = 'b808143c7252e08d926890bde25b431b2e9317e7'
_OWNER = 'bookflow.commands.pos_ticket_cmds.{}|src/bookflow/commands/pos_ticket_cmds.py'
_HOLD = 'bookflow.company.pos_sales.authorize|src/bookflow/company/pos_sales.py'
_TICKET = 'bookflow.company.pos_tickets.authorize|src/bookflow/company/pos_tickets.py'
_RULE = 'member pos.sell; pos.price-override when a line has unit_price'
_ADDED = (
    ('pos ticket hold', 'plan_hold', _HOLD, _RULE),
    ('pos ticket list', 'plan_list', _TICKET, None),
    ('pos ticket resume', 'plan_resume', _TICKET, None),
    ('pos ticket discard', 'plan_discard', _TICKET, None),
)
ADDED_COMMANDS = tuple(c.CommandDescriptor(name, 'company', 'pos.sell', 'member', (), True, None, False, False,
    rule, owner, None, None) for name, planner, owner, rule in _ADDED) + (
    c.CommandDescriptor('pos sale void', 'company', 'pos.void', 'member', (), True, None, False, False,
                        'member pos.void', 'bookflow.company.pos_void.authorize|src/bookflow/company/pos_void.py', None, None),)
ADDED_ACTIONS = tuple(c.CompanyAction(name, (c.Requirement('pos.sell', 'member'),), True,
    tuple(sorted((_OWNER.format(planner), owner)))) for name, planner, owner, rule in _ADDED) + (
    c.CompanyAction('pos sale void', (c.Requirement('pos.void', 'member'),), True,
        ('bookflow.commands.pos_void_cmds.plan_void|src/bookflow/commands/pos_void_cmds.py',
         'bookflow.company.pos_void.authorize|src/bookflow/company/pos_void.py')),)
CATALOG = replace(previous.CATALOG, version=c.POS_TICKET_POLICY_VERSION,
    commands=tuple(sorted((*previous.CATALOG.commands, *ADDED_COMMANDS), key=lambda x: x.name)),
    company_actions=tuple(sorted((*previous.CATALOG.company_actions, *ADDED_ACTIONS), key=lambda x: x.key)))
MANIFEST = c.catalog_manifest(CATALOG, previous.MANIFEST.standalone_names)


def catalog_bundle():
    inventory = dict(manifest=asdict(MANIFEST), sources=[asdict(x) for x in CATALOG.conditional_sources])
    digest = hashlib.sha256(json.dumps(inventory, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return CatalogBundle(SOURCE_COMMIT, CATALOG, MANIFEST.standalone_names, digest)
