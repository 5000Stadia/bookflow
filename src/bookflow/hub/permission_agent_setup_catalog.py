"""Add `setup` (R182) without rewriting any accepted descriptor.

`setup` is a local bootstrap command like `init`, on the same capability (`init`, authenticated): it
runs in the calling process as the computer's own login, is never routed over HTTP or MCP, and does
its work through the existing commands, each checked under its own rule. No capability, role default
or existing threshold changes, so a root replacing its catalog with this one suspends no agent and
revokes no token.
"""
from dataclasses import asdict, replace
import hashlib
import json

from . import permission_verify_catalog as previous, permission_catalog as c
from .permission_snapshot import CatalogBundle

SOURCE_COMMIT = '9047333f4cfcb75f373258600f0387258019e55a'
ADDED_COMMANDS = (
    c.CommandDescriptor('setup', 'hub', 'init', 'authenticated', (), True, None, True, True,
                        'the local operating-system login, which becomes or already is the installation\'s administrator',
                        None, None, None),
)
CATALOG = replace(previous.CATALOG, version=c.AGENT_SETUP_POLICY_VERSION,
    commands=tuple(sorted((*previous.CATALOG.commands, *ADDED_COMMANDS), key=lambda x: x.name)))
MANIFEST = c.catalog_manifest(CATALOG, previous.MANIFEST.standalone_names)


def catalog_bundle():
    inventory = dict(manifest=asdict(MANIFEST), sources=[asdict(x) for x in CATALOG.conditional_sources])
    digest = hashlib.sha256(json.dumps(inventory, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return CatalogBundle(SOURCE_COMMIT, CATALOG, MANIFEST.standalone_names, digest)
