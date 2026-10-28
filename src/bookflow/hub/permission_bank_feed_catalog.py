"""Add the bank inbox (R108 increment 1) without rewriting any accepted descriptor.

A new capability, `bank-feed` (design/bank-feeds.md §8), held at `member` by every role and at `standard` by standard,
admin and owner, as `import` is: `bank-feed show`, `query` and `bank-line query` are member reads; `bank-feed import`
is a standard write. Each needs `ledger.read` at `member` beside it, since the inbox shows the entries a line matched.
A second capability, `bank-feed.connect` (admin, held by admin and owner), is asked only when an import goes on past an
account-number mismatch (`confirm_account`); live connections arrive with increment 8. No existing capability, role
default or threshold changes, so a root replacing its catalog with this one suspends no agent and revokes no token.
"""
from dataclasses import asdict, replace
import hashlib
import json

from . import permission_delivery_batch_catalog as previous, permission_catalog as c
from .permission_snapshot import CatalogBundle

SOURCE_COMMIT = '73d7bebd640414bda46984313ac503ccd66bbcfe'
_OWNER = 'bookflow.commands.bank_feed_cmds.{}|src/bookflow/commands/bank_feed_cmds.py'
_LEDGER = (c.Requirement('ledger.read', 'member'),)
# (name, threshold, planner)
_ADDED = (
    ('bank-feed import', 'standard', 'plan_bank_feed_import'),
    ('bank-feed query', 'member', 'plan_bank_feed_query'),
    ('bank-feed show', 'member', 'plan_bank_feed_show'),
    ('bank-line query', 'member', 'plan_bank_line_query'),
)
ADDED_CAPABILITIES = (c.CapabilitySpec('bank-feed', ('member', 'standard'), ('member', 'standard')),
                      c.CapabilitySpec('bank-feed.connect', ('admin',), ('admin',)))
ADDED_DEFAULTS = (
    c.DefaultEntry('readonly', c.Requirement('bank-feed', 'member')),
    *(c.DefaultEntry(role, c.Requirement('bank-feed', threshold))
      for role in ('standard', 'admin', 'owner', 'hub_admin') for threshold in ('member', 'standard')),
    *(c.DefaultEntry(role, c.Requirement('bank-feed.connect', 'admin')) for role in ('admin', 'owner', 'hub_admin')),
)
ADDED_COMMANDS = tuple(
    c.CommandDescriptor(name, 'company', 'bank-feed', threshold, _LEDGER, True, None, False, False, None, None, None,
                        None)
    for name, threshold, _ in _ADDED)
ADDED_ACTIONS = tuple(
    c.CompanyAction(name, tuple(sorted((*_LEDGER, c.Requirement('bank-feed', threshold)),
                                       key=lambda r: r.capability)), True, (_OWNER.format(planner),))
    for name, threshold, planner in _ADDED)
CURRENT_SOURCES = (*previous.CATALOG.conditional_sources,
    c.ResourceSource('bookflow.company.bank_feeds._require_connect', (('src/bookflow/company/bank_feeds.py', 43),),
                     (c.Requirement('bank-feed.connect', 'admin'),)))
CATALOG = replace(previous.CATALOG, version=c.BANK_FEED_POLICY_VERSION,
    commands=tuple(sorted((*previous.CATALOG.commands, *ADDED_COMMANDS), key=lambda x: x.name)),
    capabilities=tuple(sorted((*previous.CATALOG.capabilities, *ADDED_CAPABILITIES), key=lambda x: x.name)),
    defaults=(*previous.CATALOG.defaults, *ADDED_DEFAULTS),
    company_actions=tuple(sorted((*previous.CATALOG.company_actions, *ADDED_ACTIONS), key=lambda x: x.key)),
    conditional_sources=tuple(sorted(CURRENT_SOURCES, key=lambda x: x.owner)))
MANIFEST = c.catalog_manifest(CATALOG, previous.MANIFEST.standalone_names)


def catalog_bundle():
    inventory = dict(manifest=asdict(MANIFEST), sources=[asdict(x) for x in CATALOG.conditional_sources])
    digest = hashlib.sha256(json.dumps(inventory, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return CatalogBundle(SOURCE_COMMIT, CATALOG, MANIFEST.standalone_names, digest)
