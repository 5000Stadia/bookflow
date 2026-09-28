"""Add agent and user deactivation without rewriting any accepted permission descriptor.

Four hub commands, all installation-administrator only: `agent deactivate`, `agent activate`,
`user deactivate` and `user activate`. No capability, role default, company action, admin action
or existing threshold changes, so a root replacing its catalog with this one suspends no agent and
revokes no token, and a root activated at the previous catalog admits these commands through the
tip without re-activation, as it did the agent commands.
"""
from dataclasses import asdict, replace
import hashlib
import json

from . import permission_everyday_reports_catalog as previous, permission_catalog as c
from .permission_snapshot import CatalogBundle

SOURCE_COMMIT = '169d26f3e835d0a027d7fc98686d942506d96653'
ADMIN_ONLY = 'human installation administrator on an activated installation'
ADDED_COMMANDS = tuple(
    c.CommandDescriptor(name, 'hub', 'user', 'hub_admin', (), True, None, False, False, ADMIN_ONLY, None, None, None)
    for name in ('agent activate', 'agent deactivate', 'user activate', 'user deactivate'))
CATALOG = replace(previous.CATALOG, version=c.DEACTIVATION_POLICY_VERSION,
    commands=tuple(sorted((*previous.CATALOG.commands, *ADDED_COMMANDS), key=lambda x: x.name)))
MANIFEST = c.catalog_manifest(CATALOG, previous.MANIFEST.standalone_names)


def catalog_bundle():
    inventory = dict(manifest=asdict(MANIFEST), sources=[asdict(x) for x in CATALOG.conditional_sources])
    digest = hashlib.sha256(json.dumps(inventory, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return CatalogBundle(SOURCE_COMMIT, CATALOG, MANIFEST.standalone_names, digest)
