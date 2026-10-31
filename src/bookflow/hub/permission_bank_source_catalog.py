"""Live feed connection seam on bank-reconcile-v1; merged descriptors stay frozen."""
from dataclasses import asdict, replace
import hashlib
import json
from . import permission_bank_reconcile_catalog as previous, permission_catalog as c
from .permission_snapshot import CatalogBundle

SOURCE_COMMIT = '89622d32040590d2f54760a800312d656a390b99'
_OWNER = 'bookflow.commands.bank_connection_cmds.{}|src/bookflow/commands/bank_connection_cmds.py'
_READ = (c.Requirement('ledger.read', 'member'),)
_WRITE = (c.Requirement('bank-feed', 'member'), *_READ)
_ADDED = (
    ('bank-feed-connection add', 'bank-feed.connect', 'admin', 'plan_add', _WRITE),
    ('bank-feed-connection enable', 'bank-feed.connect', 'admin', 'plan_enable', _WRITE),
    ('bank-feed-connection disable', 'bank-feed', 'standard', 'plan_disable', ()),
    ('bank-feed-connection check', 'bank-feed.connect', 'admin', 'plan_check', _WRITE),
    ('bank-feed fetch', 'bank-feed.connect', 'admin', 'plan_fetch', _WRITE),
    ('bank-feed-connection query', 'bank-feed', 'member', 'plan_query', _READ),
)
CAPABILITIES = {'bank-feed.connect': ('admin', tuple(name for name, cap, *_ in _ADDED if cap == 'bank-feed.connect'))}
ADDED_COMMANDS = tuple(c.CommandDescriptor(name, 'company', capability, threshold, resources, True, None, False, False, None, None, None, None)
    for name, capability, threshold, _, resources in _ADDED)
ADDED_ACTIONS = tuple(c.CompanyAction(name, tuple(sorted((*resources, c.Requirement(capability, threshold)), key=lambda r: r.capability)), True, (_OWNER.format(planner),))
    for name, capability, threshold, planner, resources in _ADDED)
SOURCES = tuple(replace(source, requirements=tuple(sorted((*source.requirements, c.Requirement('bank-feed.connect', 'admin')), key=lambda r:r.capability)))
    if source.owner == 'bookflow.company.capability_gate.require_held' else source for source in previous.CATALOG.conditional_sources)
CATALOG = replace(previous.CATALOG, version=c.BANK_SOURCE_POLICY_VERSION,
    commands=tuple(sorted((*previous.CATALOG.commands, *ADDED_COMMANDS), key=lambda x:x.name)),
    company_actions=tuple(sorted((*previous.CATALOG.company_actions, *ADDED_ACTIONS), key=lambda x:x.key)),
    conditional_sources=SOURCES)
MANIFEST = c.catalog_manifest(CATALOG, previous.MANIFEST.standalone_names)


def catalog_bundle():
    inventory = dict(manifest=asdict(MANIFEST), sources=[asdict(x) for x in CATALOG.conditional_sources])
    digest = hashlib.sha256(json.dumps(inventory, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return CatalogBundle(SOURCE_COMMIT, CATALOG, MANIFEST.standalone_names, digest)
