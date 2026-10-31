"""Receipt reads on the immutable open-ticket recovery tip."""
from dataclasses import asdict, replace
import hashlib
import json
from . import permission_pos_ticket_takeover_catalog as previous, permission_catalog as c
from .permission_snapshot import CatalogBundle

SOURCE_COMMIT = '448d43426f12521164b51c46553edc65a9ce5897'
_OWNER = 'bookflow.commands.pos_receipt_cmds.plan_show|src/bookflow/commands/pos_receipt_cmds.py'
_READ = 'bookflow.company.pos_receipts.authorize|src/bookflow/company/pos_receipts.py'
ADDED_COMMANDS = (c.CommandDescriptor('pos receipt show', 'company', 'pos.sell', 'member', (), True,
    None, False, False, None, _READ, None, None),)
ADDED_ACTIONS = (c.CompanyAction('pos receipt show', (c.Requirement('pos.sell', 'member'),), True,
    tuple(sorted((_OWNER, _READ)))),)
CATALOG = replace(previous.CATALOG, version=c.POS_RECEIPT_POLICY_VERSION,
    commands=tuple(sorted((*previous.CATALOG.commands, *ADDED_COMMANDS), key=lambda x: x.name)),
    company_actions=tuple(sorted((*previous.CATALOG.company_actions, *ADDED_ACTIONS), key=lambda x: x.key)))
MANIFEST = c.catalog_manifest(CATALOG, previous.MANIFEST.standalone_names)


def catalog_bundle():
    inventory = dict(manifest=asdict(MANIFEST), sources=[asdict(x) for x in CATALOG.conditional_sources])
    digest = hashlib.sha256(json.dumps(inventory, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return CatalogBundle(SOURCE_COMMIT, CATALOG, MANIFEST.standalone_names, digest)
