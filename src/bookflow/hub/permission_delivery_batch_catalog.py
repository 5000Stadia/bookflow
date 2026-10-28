"""Add document delivery's flags, queue and batches (R112 increment 3) without rewriting any accepted descriptor.

`delivery flag`, `unflag`, `batch` and `batch-cancel` are message writes, admitted as increment 1's are: `customer` at
`standard`, with `ledger.read` and `customer-work` at `member` so a principal who may not read a document may not flag
or batch it. `delivery queue` and `batch-show` read at `customer` `member` with the same resources. A batch's `send`
releases as `delivery send` does: a person's release is their go and an agent's waits for one (R187: `delivery.send`),
so no people-only action is added. No capability, role default or existing threshold changes, so a root replacing its
catalog with this one suspends no agent and revokes no token.
"""
from dataclasses import asdict, replace
import hashlib
import json

from . import permission_delivery_queue_catalog as previous, permission_catalog as c
from .permission_snapshot import CatalogBundle

SOURCE_COMMIT = '73d7bebd640414bda46984313ac503ccd66bbcfe'
_OWNER = 'bookflow.commands.delivery_cmds.{}|src/bookflow/commands/delivery_cmds.py'
_MESSAGES = (c.Requirement(capability='customer-work', threshold='member'),
             c.Requirement(capability='ledger.read', threshold='member'))
# (name, capability, threshold, planner, resources)
_ADDED = (
    ('delivery batch', 'customer', 'standard', 'plan_delivery_batch', _MESSAGES),
    ('delivery batch-cancel', 'customer', 'standard', 'plan_delivery_batch_cancel', _MESSAGES),
    ('delivery batch-show', 'customer', 'member', 'plan_delivery_batch_show', _MESSAGES),
    ('delivery flag', 'customer', 'standard', 'plan_delivery_flag', _MESSAGES),
    ('delivery queue', 'customer', 'member', 'plan_delivery_queue', _MESSAGES),
    ('delivery unflag', 'customer', 'standard', 'plan_delivery_unflag', _MESSAGES),
)
ADDED_COMMANDS = tuple(
    c.CommandDescriptor(name, 'company', capability, threshold, resources, True, None, False, False, None, None, None,
                        None)
    for name, capability, threshold, _, resources in _ADDED)
ADDED_ACTIONS = tuple(
    c.CompanyAction(name, tuple(sorted((*resources, c.Requirement(capability, threshold)),
                                       key=lambda r: r.capability)), True, (_OWNER.format(planner),))
    for name, capability, threshold, planner, resources in _ADDED)
CATALOG = replace(previous.CATALOG, version=c.DELIVERY_BATCH_POLICY_VERSION,
    commands=tuple(sorted((*previous.CATALOG.commands, *ADDED_COMMANDS), key=lambda x: x.name)),
    company_actions=tuple(sorted((*previous.CATALOG.company_actions, *ADDED_ACTIONS), key=lambda x: x.key)))
MANIFEST = c.catalog_manifest(CATALOG, previous.MANIFEST.standalone_names)


def catalog_bundle():
    inventory = dict(manifest=asdict(MANIFEST), sources=[asdict(x) for x in CATALOG.conditional_sources])
    digest = hashlib.sha256(json.dumps(inventory, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return CatalogBundle(SOURCE_COMMIT, CATALOG, MANIFEST.standalone_names, digest)
