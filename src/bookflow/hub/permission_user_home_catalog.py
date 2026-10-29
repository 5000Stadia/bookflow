"""Add each person's Home (R191, design/point-of-sale.md §8) without rewriting any accepted descriptor. Chained on bulk-edit-v1 (R107 increment 6).

`user home set`, `show` and `clear` are hub commands under the existing capability `user`, open to every signed-in
actor (threshold `authenticated`, as `user set-password` is): one's own Home is self-service; another person's needs
`user.manage`, asked in `user_home_cmds.authorize` through `hub.capabilities.require_hub`, the same check for a person
(an installation administrator) and an agent (granted it, acting for one). No capability, role default, company
action or threshold changes, so a root replacing its catalog with this one suspends no agent and revokes no token.
"""
from dataclasses import asdict, replace
import hashlib
import json

from . import permission_bulk_edit_catalog as previous, permission_catalog as c
from .permission_snapshot import CatalogBundle

SOURCE_COMMIT = '426d16db06973a0946261de9b4645f7795925992'
_FILE = 'src/bookflow/commands/user_home_cmds.py'
_OWNER = f'bookflow.commands.user_home_cmds.authorize|{_FILE}'
AUTHORIZATION = ("any signed-in person or agent for their own Home; another person's needs user.manage (an installation "
                 "administrator, or an agent its principal granted user.manage)")
ADDED_COMMANDS = tuple(
    c.CommandDescriptor(name, 'hub', 'user', 'authenticated', (), True, None, False, False, AUTHORIZATION, _OWNER, None,
                        None)
    for name in ('user home clear', 'user home set', 'user home show'))
CATALOG = replace(previous.CATALOG, version=c.USER_HOME_POLICY_VERSION,
    commands=tuple(sorted((*previous.CATALOG.commands, *ADDED_COMMANDS), key=lambda x: x.name)))
MANIFEST = c.catalog_manifest(CATALOG, previous.MANIFEST.standalone_names)


def catalog_bundle():
    inventory = dict(manifest=asdict(MANIFEST), sources=[asdict(x) for x in CATALOG.conditional_sources])
    digest = hashlib.sha256(json.dumps(inventory, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return CatalogBundle(SOURCE_COMMIT, CATALOG, MANIFEST.standalone_names, digest)
