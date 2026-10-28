"""Add the item picture (R114 increment 1) without rewriting any accepted descriptor.

`item picture set`, `use` and `clear` are standard writes under the existing capability `item`, with `attachment` at
`standard` beside it, since each links or unlinks the picture's file on the item as `attachment add`, `link` and
`unlink` do; `set` uploads its body as `attachment add` does. Reading a picture is `item show`/`query` (the facts) and
`attachment get` (the bytes), both unchanged. No capability, role default or threshold changes, so a root replacing
its catalog with this one suspends no agent and revokes no token.
"""
from dataclasses import asdict, replace
import hashlib
import json

from . import permission_bank_line_catalog as previous, permission_catalog as c
from .permission_snapshot import CatalogBundle

SOURCE_COMMIT = '2a469f13bba26582047a68274e94eff8efdd572c'
_FILE = 'src/bookflow/commands/item_picture_cmds.py'
_PREPARE = f'bookflow.commands.item_picture_cmds.prepare_set|{_FILE}'
_ATTACHMENT = (c.Requirement('attachment', 'standard'),)
_ADDED = {'item picture clear': 'plan_clear', 'item picture set': 'plan_set', 'item picture use': 'plan_use'}
ADDED_COMMANDS = tuple(
    c.CommandDescriptor(name, 'company', 'item', 'standard', _ATTACHMENT, True, None, False, False, None, None, None,
                        _PREPARE if name == 'item picture set' else None)
    for name in _ADDED)
ADDED_ACTIONS = tuple(
    c.CompanyAction(name, tuple(sorted((*_ATTACHMENT, c.Requirement('item', 'standard')), key=lambda r: r.capability)),
                    True, tuple(sorted({f'bookflow.commands.item_picture_cmds.{plan}|{_FILE}',
                                        *((_PREPARE,) if name == 'item picture set' else ())})))
    for name, plan in _ADDED.items())
CATALOG = replace(previous.CATALOG, version=c.ITEM_PICTURE_POLICY_VERSION,
    commands=tuple(sorted((*previous.CATALOG.commands, *ADDED_COMMANDS), key=lambda x: x.name)),
    company_actions=tuple(sorted((*previous.CATALOG.company_actions, *ADDED_ACTIONS), key=lambda x: x.key)))
MANIFEST = c.catalog_manifest(CATALOG, previous.MANIFEST.standalone_names)


def catalog_bundle():
    inventory = dict(manifest=asdict(MANIFEST), sources=[asdict(x) for x in CATALOG.conditional_sources])
    digest = hashlib.sha256(json.dumps(inventory, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return CatalogBundle(SOURCE_COMMIT, CATALOG, MANIFEST.standalone_names, digest)
