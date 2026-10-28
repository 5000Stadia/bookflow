"""Add reversing and moving a bank import (R108 increment 2) without rewriting any accepted descriptor.

`bank-feed reverse` and `bank-feed move` are standard writes under the existing capability `bank-feed`, with
`ledger.read` at `member` beside it, as `bank-feed import` is. Deleting an entry an import created asks that entry's
own family `delete` grant, through the family's own command, so no new capability is needed; a move that goes on past
an account-number mismatch asks `bank-feed.connect`, as an import does. `batch show` of a bank import reads it as
`bank-feed show` does, so `batches.require_read` is re-declared with that command's requirements. No capability, role
default or threshold changes, so a root replacing its catalog with this one suspends no agent and revokes no token.
"""
from dataclasses import asdict, replace
import hashlib
import json

from . import permission_bank_feed_catalog as previous, permission_catalog as c
from .permission_snapshot import CatalogBundle

SOURCE_COMMIT = 'de9326f36bf2a960b301d0c2116b1b7d97c150f3'
_OWNER = 'bookflow.commands.bank_feed_cmds.{}|src/bookflow/commands/bank_feed_cmds.py'
_LEDGER = (c.Requirement('ledger.read', 'member'),)
# (name, threshold, planner)
_ADDED = (
    ('bank-feed move', 'standard', 'plan_bank_feed_move'),
    ('bank-feed reverse', 'standard', 'plan_bank_feed_reverse'),
)
ADDED_COMMANDS = tuple(
    c.CommandDescriptor(name, 'company', 'bank-feed', threshold, _LEDGER, True, None, False, False, None, None, None,
                        None)
    for name, threshold, _ in _ADDED)
ADDED_ACTIONS = tuple(
    c.CompanyAction(name, tuple(sorted((*_LEDGER, c.Requirement('bank-feed', threshold)),
                                       key=lambda r: r.capability)), True, (_OWNER.format(planner),))
    for name, threshold, planner in _ADDED)
_REQUIRE_READ = 'bookflow.company.batches.require_read'
_READ_OLD = next(x for x in previous.CATALOG.conditional_sources if x.owner == _REQUIRE_READ)
CURRENT_SOURCES = (
    *(x for x in previous.CATALOG.conditional_sources if x.owner != _REQUIRE_READ),
    replace(_READ_OLD, requirements=tuple(sorted(
        {*_READ_OLD.requirements, c.Requirement('bank-feed', 'member'), *_LEDGER}, key=lambda r: r.capability))))
CATALOG = replace(previous.CATALOG, version=c.BANK_FEED_REVERSE_POLICY_VERSION,
    commands=tuple(sorted((*previous.CATALOG.commands, *ADDED_COMMANDS), key=lambda x: x.name)),
    company_actions=tuple(sorted((*previous.CATALOG.company_actions, *ADDED_ACTIONS), key=lambda x: x.key)),
    conditional_sources=tuple(sorted(CURRENT_SOURCES, key=lambda x: x.owner)))
MANIFEST = c.catalog_manifest(CATALOG, previous.MANIFEST.standalone_names)


def catalog_bundle():
    inventory = dict(manifest=asdict(MANIFEST), sources=[asdict(x) for x in CATALOG.conditional_sources])
    digest = hashlib.sha256(json.dumps(inventory, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return CatalogBundle(SOURCE_COMMIT, CATALOG, MANIFEST.standalone_names, digest)
