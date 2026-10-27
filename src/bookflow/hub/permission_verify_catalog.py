"""Add `company verify` (R180 stage 2) without rewriting any accepted descriptor.

`company verify` proves the whole of a company's statement reconciliation storage and reads only, so it is
`ledger.read` (member): anyone who may read the ledger, person or agent. No capability, role default or
existing threshold changes, so a root replacing its catalog with this one suspends no agent and revokes no
token.
"""
from dataclasses import asdict, replace
import hashlib
import json

from . import permission_fifo_catalog as previous, permission_catalog as c
from .permission_snapshot import CatalogBundle

SOURCE_COMMIT = '2a7f5d8db171ab2129b6994f5956b302515f3779'
_OWNER = 'bookflow.commands.verify_cmds.{}|src/bookflow/commands/verify_cmds.py'
# (name, capability, threshold, planner)
_ADDED = (
    ('company verify', 'ledger.read', 'member', 'plan_company_verify'),
)
ADDED_COMMANDS = tuple(
    c.CommandDescriptor(name, 'company', capability, threshold, (), True, None, False, False, None, None, None, None)
    for name, capability, threshold, _ in _ADDED)
ADDED_ACTIONS = tuple(
    c.CompanyAction(name, (c.Requirement(capability, threshold),), True, (_OWNER.format(planner),))
    for name, capability, threshold, planner in _ADDED)
CATALOG = replace(previous.CATALOG, version=c.VERIFY_POLICY_VERSION,
    commands=tuple(sorted((*previous.CATALOG.commands, *ADDED_COMMANDS), key=lambda x: x.name)),
    company_actions=tuple(sorted((*previous.CATALOG.company_actions, *ADDED_ACTIONS), key=lambda x: x.key)))
MANIFEST = c.catalog_manifest(CATALOG, previous.MANIFEST.standalone_names)


def catalog_bundle():
    inventory = dict(manifest=asdict(MANIFEST), sources=[asdict(x) for x in CATALOG.conditional_sources])
    digest = hashlib.sha256(json.dumps(inventory, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return CatalogBundle(SOURCE_COMMIT, CATALOG, MANIFEST.standalone_names, digest)
