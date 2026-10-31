"""Day close and deposit hand-off on the immutable refunds tip."""
from dataclasses import asdict, replace
import hashlib
import json
from . import permission_pos_refund_catalog as previous, permission_catalog as c
from .permission_snapshot import CatalogBundle

SOURCE_COMMIT = '2304bbea56d3a13e3d0cec04d65f63bc59fda366'
_ADDED = (
    ('pos shift open', 'pos.sell', 'member', 'pos_day_cmds.shift_open'),
    ('pos shift close', 'pos.sell', 'member', 'pos_day_cmds.shift_close'),
    ('report pos-day', 'pos.sell', 'member', 'pos_day_cmds.pos_day'),
    ('pos deposit prepare', 'ledger.post', 'standard', 'pos_deposit_cmds.plan_prepare'),
)
ADDED_COMMANDS = tuple(c.CommandDescriptor(name, 'company', cap, role, (), True,
    None, False, False, None, None, None, None) for name, cap, role, planner in _ADDED)
ADDED_ACTIONS = tuple(c.CompanyAction(name, (c.Requirement(cap, role),), True,
    (f'bookflow.commands.{planner}|src/bookflow/commands/{planner.split(".")[0]}.py',))
    for name, cap, role, planner in _ADDED)
CATALOG = replace(previous.CATALOG, version=c.POS_DAY_POLICY_VERSION,
    commands=tuple(sorted((*previous.CATALOG.commands, *ADDED_COMMANDS), key=lambda x: x.name)),
    company_actions=tuple(sorted((*previous.CATALOG.company_actions, *ADDED_ACTIONS), key=lambda x: x.key)))
MANIFEST = c.catalog_manifest(CATALOG, previous.MANIFEST.standalone_names)


def catalog_bundle():
    inventory = dict(manifest=asdict(MANIFEST), sources=[asdict(x) for x in CATALOG.conditional_sources])
    digest = hashlib.sha256(json.dumps(inventory, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return CatalogBundle(SOURCE_COMMIT, CATALOG, MANIFEST.standalone_names, digest)
