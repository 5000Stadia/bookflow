"""Open ticket recovery on the immutable pos-ticket-v1 tip."""
from dataclasses import asdict, replace
import hashlib
import json
from . import permission_pos_ticket_catalog as previous, permission_catalog as c
from .permission_snapshot import CatalogBundle

SOURCE_COMMIT = '793053f89e874ffab32bdaf50ee491fc169755b0'
_OWNER = 'bookflow.commands.pos_ticket_cmds.{}|src/bookflow/commands/pos_ticket_cmds.py'
_TICKET = 'bookflow.company.pos_tickets.authorize|src/bookflow/company/pos_tickets.py'
ADDED_COMMANDS = (c.CommandDescriptor('pos ticket take-over', 'company', 'pos.sell', 'member', (), True,
    None, False, False, None, _TICKET, None, None),)
ADDED_ACTIONS = (c.CompanyAction('pos ticket take-over', (c.Requirement('pos.sell', 'member'),), True,
    tuple(sorted((_OWNER.format('plan_take_over'), _TICKET)))),)
CATALOG = replace(previous.CATALOG, version=c.POS_TICKET_TAKEOVER_POLICY_VERSION,
    commands=tuple(sorted((*previous.CATALOG.commands, *ADDED_COMMANDS), key=lambda x: x.name)),
    company_actions=tuple(sorted((*previous.CATALOG.company_actions, *ADDED_ACTIONS), key=lambda x: x.key)))
MANIFEST = c.catalog_manifest(CATALOG, previous.MANIFEST.standalone_names)


def catalog_bundle():
    inventory = dict(manifest=asdict(MANIFEST), sources=[asdict(x) for x in CATALOG.conditional_sources])
    digest = hashlib.sha256(json.dumps(inventory, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return CatalogBundle(SOURCE_COMMIT, CATALOG, MANIFEST.standalone_names, digest)
