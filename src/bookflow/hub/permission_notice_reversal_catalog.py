"""Notice reversal on the accepted batch reversal layer."""
from dataclasses import asdict, replace
import hashlib
import json
from . import permission_reversible_batch_catalog as previous, permission_catalog as c
from .permission_snapshot import CatalogBundle

SOURCE_COMMIT = 'bf41ae7ff3422aced7ab29023fcc07fac63f87b0'
ADDED_COMMANDS = (c.CommandDescriptor('notice reverse', 'company', 'customer', 'standard', (), True,
                                      None, False, False, None, None, None, None),)
ADDED_ACTIONS = (c.CompanyAction('notice reverse', (c.Requirement('customer', 'standard'),), True,
    ('bookflow.commands.scheduling_cmds._notice_write.<locals>.planner|src/bookflow/commands/scheduling_cmds.py',)),)
CATALOG = replace(previous.CATALOG, version=c.NOTICE_REVERSAL_POLICY_VERSION,
    commands=tuple(sorted((*previous.CATALOG.commands, *ADDED_COMMANDS), key=lambda x: x.name)),
    company_actions=tuple(sorted((*previous.CATALOG.company_actions, *ADDED_ACTIONS), key=lambda x: x.key)))
MANIFEST = c.catalog_manifest(CATALOG, previous.MANIFEST.standalone_names)


def catalog_bundle():
    inventory = dict(manifest=asdict(MANIFEST), sources=[asdict(x) for x in CATALOG.conditional_sources])
    digest = hashlib.sha256(json.dumps(inventory, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return CatalogBundle(SOURCE_COMMIT, CATALOG, MANIFEST.standalone_names, digest)
