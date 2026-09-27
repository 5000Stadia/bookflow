"""Add agent administration without rewriting any accepted permission descriptor.

Six hub commands, all installation-administrator only, and `admin:agents` made available.
No capability, role default, company action or existing threshold changes. Availability of
an administrative action is not an input to authority signatures, so a root replacing its
catalog with this one suspends no agent and revokes no token.
"""
from dataclasses import asdict, replace
import hashlib
import json

from . import permission_refund_history_catalog as previous, permission_catalog as c
from .permission_snapshot import CatalogBundle

SOURCE_COMMIT = 'b5605a167044ce93f63dc5465fb2a66ff2bb81ab'
ADMIN_ONLY = 'human installation administrator on an activated installation'
ADDED_COMMANDS = tuple(
    c.CommandDescriptor('agent ' + verb, 'hub', 'user', 'hub_admin', (), True, None, False, False, ADMIN_ONLY, None, None, None)
    for verb in ('assign', 'authorize', 'create', 'list', 'show', 'unassign'))
CHANGED_AUTHORIZATION = {
    'token issue': ('human self-service; a human hub administrator may issue for another user; '
                    'an agent token needs an activated installation and an authorized agent'),
}
CATALOG = replace(previous.CATALOG, version=c.AGENT_ADMIN_POLICY_VERSION,
    commands=tuple(sorted((*(replace(x, authorization_text=CHANGED_AUTHORIZATION[x.name]) if x.name in CHANGED_AUTHORIZATION else x
                             for x in previous.CATALOG.commands), *ADDED_COMMANDS), key=lambda x: x.name)),
    admin_actions=tuple(replace(x, available=True) if x.key == 'admin:agents' else x for x in previous.CATALOG.admin_actions))
MANIFEST = c.catalog_manifest(CATALOG, previous.MANIFEST.standalone_names)


def catalog_bundle():
    inventory = dict(manifest=asdict(MANIFEST), sources=[asdict(x) for x in CATALOG.conditional_sources])
    digest = hashlib.sha256(json.dumps(inventory, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return CatalogBundle(SOURCE_COMMIT, CATALOG, MANIFEST.standalone_names, digest)
