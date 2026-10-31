"""Generic batch reversal on the accepted POS split-tender layer; inverse grants remain independent."""
from dataclasses import asdict, replace
import hashlib
import json
from . import permission_pos_split_catalog as previous, permission_catalog as c
from .permission_snapshot import CatalogBundle

SOURCE_COMMIT = 'ee8d6caf1d3de5b8b175dacd4a26a397029f7da2'
ADDED_COMMANDS = (c.CommandDescriptor('batch reverse', 'company', 'import', 'member', (), True,
                                      None, False, False, None, None, None, None),)
ADDED_ACTIONS = (c.CompanyAction('batch reverse', (c.Requirement('import', 'member'),), True,
    ('bookflow.commands.import_cmds.plan_batch_reverse|src/bookflow/commands/import_cmds.py',)),)
CATALOG = replace(previous.CATALOG, version=c.REVERSIBLE_BATCH_POLICY_VERSION,
    commands=tuple(sorted((*previous.CATALOG.commands, *ADDED_COMMANDS), key=lambda x: x.name)),
    company_actions=tuple(sorted((*previous.CATALOG.company_actions, *ADDED_ACTIONS), key=lambda x: x.key)))
MANIFEST = c.catalog_manifest(CATALOG, previous.MANIFEST.standalone_names)


def catalog_bundle():
    inventory = dict(manifest=asdict(MANIFEST), sources=[asdict(x) for x in CATALOG.conditional_sources])
    digest = hashlib.sha256(json.dumps(inventory, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return CatalogBundle(SOURCE_COMMIT, CATALOG, MANIFEST.standalone_names, digest)
