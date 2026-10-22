"""Add the `prepayment` commands and `report customer-prepayments` without rewriting any accepted descriptor.

A prepayment write is ordinary documents written together (a sales receipt; a credit memo with its
`customer-credit apply` or `customer-credit unapply`; a customer refund), each at `ledger.post` at
`standard`, so the commands ask no more of a caller than those documents do and no less. `attach`
moves money between a customer's work documents, so it also needs `customer-work` at `standard`,
as billing work does. The reads are `ledger.read` member and the report `reports` member. Each
command carries one company action naming its planner. No capability, role default, admin action
or existing threshold changes, so a root replacing its catalog with this one suspends no agent and
revokes no token. Nothing here is a person's act: an agent with the standard role may do all of it.
"""
from dataclasses import asdict, replace
import hashlib
import json

from . import permission_budget_catalog as previous, permission_catalog as c
from .permission_snapshot import CatalogBundle

SOURCE_COMMIT = 'f3fcc426513273f3d34db1aac4740f670d3ff60e'
_SOURCE = '|src/bookflow/commands/prepayment_cmds.py'
_PLANNERS = {
    'prepayment receive': 'plan_receive', 'prepayment attach': 'plan_attach', 'prepayment apply': 'plan_apply',
    'prepayment unapply': 'plan_unapply', 'prepayment refund': 'plan_refund', 'prepayment show': 'prepayment_show',
    'prepayment query': 'prepayment_query', 'report customer-prepayments': 'report_customer_prepayments',
}
_WORK = (c.Requirement('customer-work', 'standard'),)


def _descriptor(name):
    if name == 'prepayment attach':
        return c.CommandDescriptor(name, 'company', 'ledger.post', 'standard', _WORK, True, None, False, False,
                                   'ledger.post and customer-work standard role', None, None, None)
    capability, threshold = (('reports', 'member') if name.startswith('report') else
                             ('ledger.read', 'member') if name in ('prepayment show', 'prepayment query') else
                             ('ledger.post', 'standard'))
    return c.CommandDescriptor(name, 'company', capability, threshold, (), True, None, False, False, None, None, None, None)


def _action(name):
    descriptor = _descriptor(name)
    requirements = tuple(sorted((*descriptor.resources, c.Requirement(descriptor.capability, descriptor.threshold)),
                                key=lambda r: r.capability))
    return c.CompanyAction(name, requirements, True, (f'bookflow.commands.prepayment_cmds.{_PLANNERS[name]}{_SOURCE}',))


ADDED_COMMANDS = tuple(_descriptor(name) for name in sorted(_PLANNERS))
ADDED_ACTIONS = tuple(_action(name) for name in sorted(_PLANNERS))
CATALOG = replace(previous.CATALOG, version=c.CUSTOMER_PREPAYMENT_POLICY_VERSION,
    commands=tuple(sorted((*previous.CATALOG.commands, *ADDED_COMMANDS), key=lambda x: x.name)),
    company_actions=tuple(sorted((*previous.CATALOG.company_actions, *ADDED_ACTIONS), key=lambda x: x.key)))
MANIFEST = c.catalog_manifest(CATALOG, previous.MANIFEST.standalone_names)


def catalog_bundle():
    inventory = dict(manifest=asdict(MANIFEST), sources=[asdict(x) for x in CATALOG.conditional_sources])
    digest = hashlib.sha256(json.dumps(inventory, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return CatalogBundle(SOURCE_COMMIT, CATALOG, MANIFEST.standalone_names, digest)
