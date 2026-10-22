"""Add mileage (R118) without rewriting any accepted descriptor.

Eighteen company commands. The Vehicle List, mileage rates and trips are `customer-work` (standard to
write, member to read), as recorded time is. Billing a trip (`trip invoice`, `trip sales-receipt`) is
`ledger.post` standard with `customer-work` standard, and `trip billing` is `ledger.read` member with
`customer-work` member, exactly as `time-activity invoice` and `time-activity billing` are. `trip
reimburse` writes a check, so it is `ledger.post` standard as `check post` is. The two mileage reports
are `reports` member, as every report is. Nothing here is reserved to a person; no capability, role
default or existing threshold changes, so a root replacing its catalog with this one suspends no agent
and revokes no token.
"""
from dataclasses import asdict, replace
import hashlib
import json

from . import permission_sales_order_catalog as previous, permission_catalog as c
from .permission_snapshot import CatalogBundle

SOURCE_COMMIT = '5c492ae2f6c1fc166a6f593a447977e6dc17c6ad'
_OWNER = 'bookflow.commands.mileage_cmds.{}|src/bookflow/commands/mileage_cmds.py'
# (name, capability, threshold, resource requirement or None, authorization text or None, planner)
_ADDED = (
    ('mileage-rate delete', 'customer-work', 'standard', None, None, 'plan_rate_delete'),
    ('mileage-rate list', 'customer-work', 'member', None, None, 'plan_rate_list'),
    ('mileage-rate set', 'customer-work', 'standard', None, None, 'plan_rate_set'),
    ('report mileage-by-job', 'reports', 'member', None, None, 'plan_mileage_by_job'),
    ('report mileage-by-vehicle', 'reports', 'member', None, None, 'plan_mileage_by_vehicle'),
    ('trip billing', 'ledger.read', 'member', 'member', 'ledger and customer-work membership', 'plan_trip_billing'),
    ('trip create', 'customer-work', 'standard', None, None, 'plan_trip_create'),
    ('trip invoice', 'ledger.post', 'standard', 'standard', 'ledger.post and customer-work standard role', 'plan_trip_invoice'),
    ('trip query', 'customer-work', 'member', None, None, 'plan_trip_query'),
    ('trip reimburse', 'ledger.post', 'standard', None, None, 'plan_trip_reimburse'),
    ('trip sales-receipt', 'ledger.post', 'standard', 'standard', 'ledger.post and customer-work standard role', 'plan_trip_sales_receipt'),
    ('trip show', 'customer-work', 'member', None, None, 'plan_trip_show'),
    ('trip update', 'customer-work', 'standard', None, None, 'plan_trip_update'),
    ('trip void', 'customer-work', 'standard', None, None, 'plan_trip_void'),
    ('vehicle create', 'customer-work', 'standard', None, None, 'plan_vehicle_create'),
    ('vehicle list', 'customer-work', 'member', None, None, 'plan_vehicle_list'),
    ('vehicle show', 'customer-work', 'member', None, None, 'plan_vehicle_show'),
    ('vehicle update', 'customer-work', 'standard', None, None, 'plan_vehicle_update'),
)


def _resources(resource):
    return (c.Requirement('customer-work', resource),) if resource else ()


ADDED_COMMANDS = tuple(
    c.CommandDescriptor(name, 'company', capability, threshold, _resources(resource), True, None, False, False,
                        authorization, None, None, None)
    for name, capability, threshold, resource, authorization, _ in _ADDED)
ADDED_ACTIONS = tuple(
    c.CompanyAction(name, tuple(sorted((*_resources(resource), c.Requirement(capability, threshold)),
                                       key=lambda r: r.capability)), True, (_OWNER.format(planner),))
    for name, capability, threshold, resource, _, planner in _ADDED)
CATALOG = replace(previous.CATALOG, version=c.MILEAGE_POLICY_VERSION,
    commands=tuple(sorted((*previous.CATALOG.commands, *ADDED_COMMANDS), key=lambda x: x.name)),
    company_actions=tuple(sorted((*previous.CATALOG.company_actions, *ADDED_ACTIONS), key=lambda x: x.key)))
MANIFEST = c.catalog_manifest(CATALOG, previous.MANIFEST.standalone_names)


def catalog_bundle():
    inventory = dict(manifest=asdict(MANIFEST), sources=[asdict(x) for x in CATALOG.conditional_sources])
    digest = hashlib.sha256(json.dumps(inventory, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return CatalogBundle(SOURCE_COMMIT, CATALOG, MANIFEST.standalone_names, digest)
