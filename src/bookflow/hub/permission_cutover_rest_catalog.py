"""Add `vendor 1099-opening` without rewriting any accepted descriptor.

It is admitted at the `vendor` capability at `standard`, as `vendor update` is: what a 1099 vendor
was paid before the books began here is a vendor's figure a bookkeeper keeps, and `cutover apply`
sets it from the old books' 1099 Summary for whoever runs the move-in. It carries one company action
naming its planner. No capability, role default, admin action or existing threshold changes, so a
root replacing its catalog with this one suspends no agent and revokes no token.
"""
from dataclasses import asdict, replace
import hashlib
import json

from . import permission_bounced_check_catalog as previous, permission_catalog as c
from .permission_snapshot import CatalogBundle

SOURCE_COMMIT = '7d1e7d2d083a9f3ee1c31d56cf7e947f88641e37'
ADDED_COMMANDS = (
    c.CommandDescriptor('vendor 1099-opening', 'company', 'vendor', 'standard', (), True, None, False, False, None, None, None, None),
)
ADDED_ACTIONS = (
    c.CompanyAction('vendor 1099-opening', (c.Requirement('vendor', 'standard'),), True,
                    ('bookflow.commands.vendor_1099_cmds.vendor_1099_opening|src/bookflow/commands/vendor_1099_cmds.py',)),
)
CATALOG = replace(previous.CATALOG, version=c.CUTOVER_REST_POLICY_VERSION,
    commands=tuple(sorted((*previous.CATALOG.commands, *ADDED_COMMANDS), key=lambda x: x.name)),
    company_actions=tuple(sorted((*previous.CATALOG.company_actions, *ADDED_ACTIONS), key=lambda x: x.key)))
MANIFEST = c.catalog_manifest(CATALOG, previous.MANIFEST.standalone_names)


def catalog_bundle():
    inventory = dict(manifest=asdict(MANIFEST), sources=[asdict(x) for x in CATALOG.conditional_sources])
    digest = hashlib.sha256(json.dumps(inventory, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return CatalogBundle(SOURCE_COMMIT, CATALOG, MANIFEST.standalone_names, digest)
