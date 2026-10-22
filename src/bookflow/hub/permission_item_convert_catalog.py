"""Add `item convert` without rewriting any accepted descriptor.

It is admitted at the `item` capability at `standard`, as `item update` is, and the command itself
refuses an agent: converting an item to an inventory part cannot be undone, so it is a person's
call, as a merge is. It carries one company action naming its planner and one people-only admin
action. No capability, role default or existing threshold changes, so a root replacing its catalog
with this one suspends no agent and revokes no token.
"""
from dataclasses import asdict, replace
import hashlib
import json

from . import permission_attachment_text_catalog as previous, permission_catalog as c
from . import permission_human_admin_catalog as human_admin
from .permission_snapshot import CatalogBundle

SOURCE_COMMIT = 'a583f7414cba98ae31f7525b4dd1bf65cbd820a0'
ADDED_COMMANDS = (
    c.CommandDescriptor('item convert', 'company', 'item', 'standard', (), True, None, False, False, None, None, None, None),
)
ADDED_ACTIONS = (
    c.CompanyAction('item convert', (c.Requirement('item', 'standard'),), True,
                    ('bookflow.commands.item_convert_cmds.plan_convert|src/bookflow/commands/item_convert_cmds.py',)),
)
ADDED_ADMIN_ACTIONS = (c.AdminAction(human_admin.PEOPLE_ONLY_PREFIX + 'item convert', 'company', 'standard', True, True),)
CATALOG = replace(previous.CATALOG, version=c.ITEM_CONVERT_POLICY_VERSION,
    commands=tuple(sorted((*previous.CATALOG.commands, *ADDED_COMMANDS), key=lambda x: x.name)),
    company_actions=tuple(sorted((*previous.CATALOG.company_actions, *ADDED_ACTIONS), key=lambda x: x.key)),
    admin_actions=tuple(sorted((*previous.CATALOG.admin_actions, *ADDED_ADMIN_ACTIONS), key=lambda x: x.key)))
MANIFEST = c.catalog_manifest(CATALOG, previous.MANIFEST.standalone_names)


def catalog_bundle():
    inventory = dict(manifest=asdict(MANIFEST), sources=[asdict(x) for x in CATALOG.conditional_sources])
    digest = hashlib.sha256(json.dumps(inventory, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return CatalogBundle(SOURCE_COMMIT, CATALOG, MANIFEST.standalone_names, digest)
