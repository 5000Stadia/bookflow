"""Add the item picture (R114 increment 1) without rewriting any accepted descriptor.

`item picture set`, `use` and `clear` are standard writes under the existing capability `item`, with `attachment` at
`standard` beside it, since each links or unlinks the picture's file on the item as `attachment add`, `link` and
`unlink` do; `set` uploads its body as `attachment add` does. `item picture get` reads a picture's bytes at `item`
`member` alone, as `item show` reads its facts: only a file some item shows as its picture or thumbnail, so a POS
clerk sees pictures without the `attachment` capability, and no other file (2026-10-28). No capability, role default
or threshold changes, so a root replacing its catalog with this one suspends no agent and revokes no token.
Re-chained onto capabilities-v1 (R187) at the merge of main.
"""
from dataclasses import asdict, replace
import hashlib
import json

from . import permission_capabilities_catalog as previous, permission_catalog as c
from .permission_snapshot import CatalogBundle

SOURCE_COMMIT = '4c36eb4f67d5baa091cd45060afcebd8c779acaf'
_FILE = 'src/bookflow/commands/item_picture_cmds.py'
_PREPARE = f'bookflow.commands.item_picture_cmds.prepare_set|{_FILE}'
_GET = f'bookflow.commands.item_picture_cmds.prepare_get|{_FILE}'
_ATTACHMENT = (c.Requirement('attachment', 'standard'),)
_ADDED = {'item picture clear': 'plan_clear', 'item picture set': 'plan_set', 'item picture use': 'plan_use'}
_PREPARES = {'item picture set': _PREPARE, 'item picture get': _GET}
ADDED_COMMANDS = tuple(sorted((*(
    c.CommandDescriptor(name, 'company', 'item', 'standard', _ATTACHMENT, True, None, False, False, None, None, None,
                        _PREPARES.get(name))
    for name in _ADDED), c.CommandDescriptor('item picture get', 'company', 'item', 'member', (), True, None, False,
                                             False, None, None, None, _GET)), key=lambda x: x.name))
ADDED_ACTIONS = tuple(sorted((*(
    c.CompanyAction(name, tuple(sorted((*_ATTACHMENT, c.Requirement('item', 'standard')), key=lambda r: r.capability)),
                    True, tuple(sorted({f'bookflow.commands.item_picture_cmds.{plan}|{_FILE}',
                                        *((_PREPARES[name],) if name in _PREPARES else ())})))
    for name, plan in _ADDED.items()), c.CompanyAction('item picture get', (c.Requirement('item', 'member'),), True,
    tuple(sorted({f'bookflow.commands.item_picture_cmds.plan_get|{_FILE}', _GET})))), key=lambda x: x.key))
CATALOG = replace(previous.CATALOG, version=c.ITEM_PICTURE_POLICY_VERSION,
    commands=tuple(sorted((*previous.CATALOG.commands, *ADDED_COMMANDS), key=lambda x: x.name)),
    company_actions=tuple(sorted((*previous.CATALOG.company_actions, *ADDED_ACTIONS), key=lambda x: x.key)))
MANIFEST = c.catalog_manifest(CATALOG, previous.MANIFEST.standalone_names)


def catalog_bundle():
    inventory = dict(manifest=asdict(MANIFEST), sources=[asdict(x) for x in CATALOG.conditional_sources])
    digest = hashlib.sha256(json.dumps(inventory, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return CatalogBundle(SOURCE_COMMIT, CATALOG, MANIFEST.standalone_names, digest)
