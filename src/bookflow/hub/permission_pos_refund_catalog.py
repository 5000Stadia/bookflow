"""POS refunds on the immutable receipt catalog tip."""
from dataclasses import asdict, replace
import hashlib
import json
from . import permission_pos_receipt_catalog as previous, permission_catalog as c
from .permission_snapshot import CatalogBundle

SOURCE_COMMIT = '418917a4264649e8aa6232b16c1abaa9d3449d7a'
_OWNER = 'bookflow.commands.pos_refund_cmds.{}|src/bookflow/commands/pos_refund_cmds.py'
_AUTHORIZE = 'bookflow.company.pos_refunds.authorize|src/bookflow/company/pos_refunds.py'
_ADDED = (('pos refund', 'plan_refund'), ('pos refund lines', 'plan_lines'), ('pos refund show', 'plan_show'),
          ('pos refund receipt', 'receipt'))
ADDED_COMMANDS = tuple(c.CommandDescriptor(name, 'company', 'pos.refund', 'member', (), True,
    None, False, False, None, _AUTHORIZE, None, None) for name, _ in _ADDED)
ADDED_ACTIONS = tuple(c.CompanyAction(name, (c.Requirement('pos.refund', 'member'),), True,
    tuple(sorted((('bookflow.commands.pos_refund_receipt_cmds.plan_show|src/bookflow/commands/pos_refund_receipt_cmds.py'
                  if planner == 'receipt' else _OWNER.format(planner)), _AUTHORIZE)))) for name, planner in _ADDED)
CATALOG = replace(previous.CATALOG, version=c.POS_REFUND_POLICY_VERSION,
    commands=tuple(sorted((*previous.CATALOG.commands, *ADDED_COMMANDS), key=lambda x: x.name)),
    company_actions=tuple(sorted((*previous.CATALOG.company_actions, *ADDED_ACTIONS), key=lambda x: x.key)))
MANIFEST = c.catalog_manifest(CATALOG, previous.MANIFEST.standalone_names)


def catalog_bundle():
    inventory = dict(manifest=asdict(MANIFEST), sources=[asdict(x) for x in CATALOG.conditional_sources])
    digest = hashlib.sha256(json.dumps(inventory, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return CatalogBundle(SOURCE_COMMIT, CATALOG, MANIFEST.standalone_names, digest)
