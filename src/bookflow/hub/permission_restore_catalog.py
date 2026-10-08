"""Add `journal restore` and `invoice restore` without rewriting any accepted descriptor.

Each restores a deleted document of its family as a new one, posting through the family's own
post writer. Each needs the family's explicit Delete grant -- whoever may remove the document
may bring it back -- and `ledger.post` at `standard`, because it posts. Each carries one company
action naming its planner. No capability, role default, admin action or existing threshold
changes, so a root replacing its catalog with this one suspends no agent and revokes no token.
"""
from dataclasses import asdict, replace
import hashlib
import json

from . import permission_cutover_catalog as previous, permission_catalog as c
from .permission_snapshot import CatalogBundle
from bookflow.core.deletion_families import capability

SOURCE_COMMIT = 'f38f3f927fd92b7ff25732472b3150b95cf40bcb'
_RESTORES = (('journal restore', 'journal_entry', 'journal_cmds.restore_journal|src/bookflow/commands/journal_cmds.py'),
             ('invoice restore', 'invoice', 'sales_cmds.restore_invoice|src/bookflow/commands/sales_cmds.py'))
ADDED_COMMANDS = tuple(c.CommandDescriptor(name, 'company', capability(family), 'standard',
    (c.Requirement('ledger.post', 'standard'),), True, None, False, False, None, None, None, None)
    for name, family, _ in _RESTORES)
ADDED_ACTIONS = tuple(c.CompanyAction(name,
    (c.Requirement(capability(family), 'standard'), c.Requirement('ledger.post', 'standard')), True,
    ('bookflow.commands.' + owner,)) for name, family, owner in _RESTORES)
CATALOG = replace(previous.CATALOG, version=c.RESTORE_POLICY_VERSION,
    commands=tuple(sorted((*previous.CATALOG.commands, *ADDED_COMMANDS), key=lambda x: x.name)),
    company_actions=tuple(sorted((*previous.CATALOG.company_actions, *ADDED_ACTIONS), key=lambda x: x.key)))
MANIFEST = c.catalog_manifest(CATALOG, previous.MANIFEST.standalone_names)


def catalog_bundle():
    inventory = dict(manifest=asdict(MANIFEST), sources=[asdict(x) for x in CATALOG.conditional_sources])
    digest = hashlib.sha256(json.dumps(inventory, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return CatalogBundle(SOURCE_COMMIT, CATALOG, MANIFEST.standalone_names, digest)
