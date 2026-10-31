"""POS layout and styles on bank-source-v1; accepted layers remain unchanged.

Reads require pos.sell. Every layout write, including saved styles and group pictures, requires pos.layout.
"""
from dataclasses import asdict, replace
import hashlib
import json
from . import permission_bank_source_catalog as previous, permission_catalog as c
from .permission_snapshot import CatalogBundle

SOURCE_COMMIT = '183f244427c917f2745b87aa49857326f0200ed4'
_FILE = 'src/bookflow/commands/pos_box_cmds.py'
_OWNER = 'bookflow.commands.pos_box_cmds.{}|' + _FILE
_ADDED = (
    ('pos-box create', 'plan_create', 'pos.layout', 'admin', None),
    ('pos-box list', 'plan_list', 'pos.sell', 'member', None),
    ('pos-box move', 'plan_move', 'pos.layout', 'admin', None),
    ('pos-box picture get', 'plan_picture_get', 'pos.sell', 'member', 'prepare_get'),
    ('pos-box picture set', 'plan_picture_set', 'pos.layout', 'admin', 'prepare_set'),
    ('pos-box remove', 'plan_remove', 'pos.layout', 'admin', None),
    ('pos-box seed', 'plan_seed', 'pos.layout', 'admin', None),
    ('pos-box update', 'plan_update', 'pos.layout', 'admin', None),
    ('pos-box view', 'plan_view', 'pos.layout', 'admin', None),
)
ADDED_COMMANDS = tuple(c.CommandDescriptor(name, 'company', capability, role, (), True, None, False, False,
    None, None, None, _OWNER.format(prepare) if prepare else None)
    for name, planner, capability, role, prepare in _ADDED)
ADDED_ACTIONS = tuple(c.CompanyAction(name, (c.Requirement(capability, role),), True,
    tuple(sorted({_OWNER.format(planner), *((_OWNER.format(prepare),) if prepare else ())})))
    for name, planner, capability, role, prepare in _ADDED)
CATALOG = replace(previous.CATALOG, version=c.POS_LAYOUT_POLICY_VERSION,
    commands=tuple(sorted((*previous.CATALOG.commands, *ADDED_COMMANDS), key=lambda x: x.name)),
    company_actions=tuple(sorted((*previous.CATALOG.company_actions, *ADDED_ACTIONS), key=lambda x: x.key)))
MANIFEST = c.catalog_manifest(CATALOG, previous.MANIFEST.standalone_names)


def catalog_bundle():
    inventory = dict(manifest=asdict(MANIFEST), sources=[asdict(x) for x in CATALOG.conditional_sources])
    digest = hashlib.sha256(json.dumps(inventory, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return CatalogBundle(SOURCE_COMMIT, CATALOG, MANIFEST.standalone_names, digest)
