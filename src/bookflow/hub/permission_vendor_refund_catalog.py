"""Add `vendor-credit refund` without rewriting any accepted permission descriptor.

`vendor-credit refund` writes at `ledger.post` at `standard`, as `deposit post` and
`vendor-credit apply` do: it is those two writes done together -- a vendor's refund check banked
as a deposit drawn from Accounts Payable, and the credit applied to it (R149) -- so it asks no more
of a caller than they do and no less. It carries one company action naming its planner. No
capability, role default, admin action or existing threshold changes, so a root replacing its
catalog with this one suspends no agent and revokes no token. The people-only admin actions of the
layer below are carried unchanged: recording a vendor's refund is bookkeeping.
"""
from dataclasses import asdict, replace
import hashlib
import json

from . import permission_attachment_text_catalog as previous, permission_catalog as c
from .permission_snapshot import CatalogBundle

SOURCE_COMMIT = 'a583f7414cba98ae31f7525b4dd1bf65cbd820a0'
_PLANNER = 'bookflow.commands.vendor_credit_cmds._refund.<locals>.planner|src/bookflow/commands/vendor_credit_cmds.py'
ADDED_COMMANDS = (
    c.CommandDescriptor('vendor-credit refund', 'company', 'ledger.post', 'standard', (), True, None, False, False, None, None, None, None),
)
ADDED_ACTIONS = (
    c.CompanyAction('vendor-credit refund', (c.Requirement('ledger.post', 'standard'),), True, (_PLANNER,)),
)
CATALOG = replace(previous.CATALOG, version=c.VENDOR_REFUND_POLICY_VERSION,
    commands=tuple(sorted((*previous.CATALOG.commands, *ADDED_COMMANDS), key=lambda x: x.name)),
    company_actions=tuple(sorted((*previous.CATALOG.company_actions, *ADDED_ACTIONS), key=lambda x: x.key)))
MANIFEST = c.catalog_manifest(CATALOG, previous.MANIFEST.standalone_names)


def catalog_bundle():
    inventory = dict(manifest=asdict(MANIFEST), sources=[asdict(x) for x in CATALOG.conditional_sources])
    digest = hashlib.sha256(json.dumps(inventory, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return CatalogBundle(SOURCE_COMMIT, CATALOG, MANIFEST.standalone_names, digest)
