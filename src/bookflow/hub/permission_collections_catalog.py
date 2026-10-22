"""Add `customer-credit transfer` and `customer-credit transfer-void` without rewriting any accepted descriptor.

Both write at `ledger.post` at `standard`, as `customer-credit apply`, `invoice post` and `credit-memo
post` do: a transfer is those writes done together, so it asks no more of a caller than they do and
no less. Each carries one company action naming its planner. No capability, role default, admin
action or existing threshold changes, so a root replacing its catalog with this one suspends no
agent and revokes no token. Moving a credit is bookkeeping, not an administrative act, so the
people-only admin actions of the layer below are carried unchanged.
"""
from dataclasses import asdict, replace
import hashlib
import json

from . import permission_list_merge_catalog as previous, permission_catalog as c
from .permission_snapshot import CatalogBundle

SOURCE_COMMIT = '39a05e7ab7ea240d58f3c78820483942032beae5'
_FILE = 'src/bookflow/commands/credit_settlement_cmds.py'
_PLANNERS = {'transfer': 'planner', 'transfer-void': 'void_planner'}
ADDED_COMMANDS = tuple(
    c.CommandDescriptor(f'customer-credit {verb}', 'company', 'ledger.post', 'standard', (), True, None, False, False, None, None, None, None)
    for verb in _PLANNERS)
ADDED_ACTIONS = tuple(
    c.CompanyAction(f'customer-credit {verb}', (c.Requirement('ledger.post', 'standard'),), True,
                    (f'bookflow.commands.credit_settlement_cmds._transfer.<locals>.{planner}|{_FILE}',))
    for verb, planner in _PLANNERS.items())
CATALOG = replace(previous.CATALOG, version=c.COLLECTIONS_POLICY_VERSION,
    commands=tuple(sorted((*previous.CATALOG.commands, *ADDED_COMMANDS), key=lambda x: x.name)),
    company_actions=tuple(sorted((*previous.CATALOG.company_actions, *ADDED_ACTIONS), key=lambda x: x.key)))
MANIFEST = c.catalog_manifest(CATALOG, previous.MANIFEST.standalone_names)


def catalog_bundle():
    inventory = dict(manifest=asdict(MANIFEST), sources=[asdict(x) for x in CATALOG.conditional_sources])
    digest = hashlib.sha256(json.dumps(inventory, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return CatalogBundle(SOURCE_COMMIT, CATALOG, MANIFEST.standalone_names, digest)
