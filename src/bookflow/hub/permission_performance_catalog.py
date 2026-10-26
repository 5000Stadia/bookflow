"""Add the performance tracking commands (R180) without rewriting any accepted descriptor.

Two hub commands on existing capabilities: `perf report` is `hub` for any signed-in person or
agent (the command itself limits a non-administrator to calls on the companies they can see);
`perf settings` is `company` at `hub_admin`, installation administrators only, as `company attach`
is. `perf generate`, `perf bench` and `perf compare` are standalone local tools and join the
standalone names. No capability, role
default or existing threshold changes, so a root replacing its catalog with this one suspends no
agent and revokes no token.
"""
from dataclasses import asdict, replace
import hashlib
import json

from . import permission_scheduling_catalog as previous, permission_catalog as c
from .permission_snapshot import CatalogBundle

SOURCE_COMMIT = '529a2fdb34a615b5a22bf49bc8d5b742e480ca38'
ADDED_COMMANDS = (
    c.CommandDescriptor('perf report', 'hub', 'hub', 'authenticated', (), True, None, False, False,
                        'any signed-in person or agent; installation administrators see every company, '
                        'others only calls on companies they can see', None, None, None),
    c.CommandDescriptor('perf settings', 'hub', 'company', 'hub_admin', (), True, None, False, False, None, None, None, None),
)
STANDALONE = ('perf bench', 'perf compare', 'perf generate')
CATALOG = replace(previous.CATALOG, version=c.PERFORMANCE_POLICY_VERSION,
    commands=tuple(sorted((*previous.CATALOG.commands, *ADDED_COMMANDS), key=lambda x: x.name)))
MANIFEST = c.catalog_manifest(CATALOG, tuple(sorted((*previous.MANIFEST.standalone_names, *STANDALONE))))


def catalog_bundle():
    inventory = dict(manifest=asdict(MANIFEST), sources=[asdict(x) for x in CATALOG.conditional_sources])
    digest = hashlib.sha256(json.dumps(inventory, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return CatalogBundle(SOURCE_COMMIT, CATALOG, MANIFEST.standalone_names, digest)
