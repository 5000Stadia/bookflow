"""Add document delivery's messages and outbox (R112 increment 1) without rewriting any accepted descriptor.

Emails to customers are customer correspondence, admitted as overdue notices are: `customer` `member` to read,
`customer` `standard` to draft, edit, release, export (it holds a stale message and writes events) or cancel. A message carries a customer's documents, so every
`delivery` command also needs `ledger.read` and `customer-work` at `member` (a principal who may not read an invoice
or an estimate may not see it in the outbox); drafting and previewing read the document as the principal, so the
document's own read is checked too. Templates are `customer` `member` and `standard`. Marking a message sent says
what happened in the owner's mail program, so it carries a people-only admin action, as `check print-confirm` does.
No capability, role default or existing threshold changes, so a root replacing its catalog with this one suspends no
agent and revokes no token.
"""
from dataclasses import asdict, replace
import hashlib
import json

from . import permission_picking_catalog as previous, permission_catalog as c
from . import permission_human_admin_catalog as human_admin
from .permission_snapshot import CatalogBundle

SOURCE_COMMIT = 'bcb8c303de2050e227f50493f78241b7e73276b8'
_OWNER = 'bookflow.commands.delivery_cmds.{}|src/bookflow/commands/delivery_cmds.py'
_MESSAGES = (c.Requirement(capability='customer-work', threshold='member'),
             c.Requirement(capability='ledger.read', threshold='member'))
# (name, threshold, planner, resources)
_ADDED = (
    ('delivery cancel', 'standard', 'plan_delivery_cancel', _MESSAGES),
    ('delivery draft', 'standard', 'plan_delivery_draft', _MESSAGES),
    ('delivery export', 'standard', 'plan_delivery_export', _MESSAGES),
    ('delivery history', 'member', 'plan_delivery_history', _MESSAGES),
    ('delivery mark-sent', 'standard', 'plan_delivery_mark_sent', _MESSAGES),
    ('delivery query', 'member', 'plan_delivery_query', _MESSAGES),
    ('delivery send', 'standard', 'plan_delivery_send', _MESSAGES),
    ('delivery show', 'member', 'plan_delivery_show', _MESSAGES),
    ('delivery update', 'standard', 'plan_delivery_update', _MESSAGES),
    ('delivery-template create', 'standard', 'plan_template_create', ()),
    ('delivery-template deactivate', 'standard', 'plan_template_deactivate', ()),
    ('delivery-template fields', 'member', 'plan_template_fields', ()),
    ('delivery-template preview', 'member', 'plan_template_preview', ()),
    ('delivery-template query', 'member', 'plan_template_query', ()),
    ('delivery-template show', 'member', 'plan_template_show', ()),
    ('delivery-template update', 'standard', 'plan_template_update', ()),
)
PEOPLE_ONLY = ('delivery mark-sent',)
ADDED_COMMANDS = tuple(
    c.CommandDescriptor(name, 'company', 'customer', threshold, resources, True, None, False, False, None, None, None,
                        None)
    for name, threshold, _, resources in _ADDED)
ADDED_ACTIONS = tuple(
    c.CompanyAction(name, tuple(sorted((*resources, c.Requirement('customer', threshold)),
                                       key=lambda r: r.capability)), True, (_OWNER.format(planner),))
    for name, threshold, planner, resources in _ADDED)
ADDED_ADMIN_ACTIONS = tuple(c.AdminAction(human_admin.PEOPLE_ONLY_PREFIX + name, 'company', 'standard', True, True)
                            for name in PEOPLE_ONLY)
CATALOG = replace(previous.CATALOG, version=c.DELIVERY_POLICY_VERSION,
    commands=tuple(sorted((*previous.CATALOG.commands, *ADDED_COMMANDS), key=lambda x: x.name)),
    company_actions=tuple(sorted((*previous.CATALOG.company_actions, *ADDED_ACTIONS), key=lambda x: x.key)),
    admin_actions=tuple(sorted((*previous.CATALOG.admin_actions, *ADDED_ADMIN_ACTIONS), key=lambda x: x.key)))
MANIFEST = c.catalog_manifest(CATALOG, previous.MANIFEST.standalone_names)


def catalog_bundle():
    inventory = dict(manifest=asdict(MANIFEST), sources=[asdict(x) for x in CATALOG.conditional_sources])
    digest = hashlib.sha256(json.dumps(inventory, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return CatalogBundle(SOURCE_COMMIT, CATALOG, MANIFEST.standalone_names, digest)
