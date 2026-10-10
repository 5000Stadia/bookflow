"""Add `attachment text` without rewriting any accepted descriptor.

It is admitted at the `attachment` capability at `member`, as `attachment get` and `attachment list`
are: it reads a small text attachment's content into the result, which is what `attachment get`
already hands any member as bytes. Over MCP an agent with no output directory had no other way to read
a statement's summary box it had attached (R170 blind July). It carries one company action naming its
planner. No capability, role default, admin action or existing threshold changes, so a root replacing
its catalog with this one suspends no agent and revokes no token.
"""
from dataclasses import asdict, replace
import hashlib
import json

from . import permission_cutover_rest_catalog as previous, permission_catalog as c
from .permission_snapshot import CatalogBundle

SOURCE_COMMIT = '684f14bdda6450b16a2284ca99e91b59d5bec339'
ADDED_COMMANDS = (
    c.CommandDescriptor('attachment text', 'company', 'attachment', 'member', (), True, None, False, False, None, None, None, None),
)
ADDED_ACTIONS = (
    c.CompanyAction('attachment text', (c.Requirement('attachment', 'member'),), True,
                    ('bookflow.commands.attachment_cmds.plan_text|src/bookflow/commands/attachment_cmds.py',)),
)
CATALOG = replace(previous.CATALOG, version=c.ATTACHMENT_TEXT_POLICY_VERSION,
    commands=tuple(sorted((*previous.CATALOG.commands, *ADDED_COMMANDS), key=lambda x: x.name)),
    company_actions=tuple(sorted((*previous.CATALOG.company_actions, *ADDED_ACTIONS), key=lambda x: x.key)))
MANIFEST = c.catalog_manifest(CATALOG, previous.MANIFEST.standalone_names)


def catalog_bundle():
    inventory = dict(manifest=asdict(MANIFEST), sources=[asdict(x) for x in CATALOG.conditional_sources])
    digest = hashlib.sha256(json.dumps(inventory, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return CatalogBundle(SOURCE_COMMIT, CATALOG, MANIFEST.standalone_names, digest)
