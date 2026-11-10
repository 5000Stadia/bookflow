"""Lead conversion, one lead/customer history and source-keyed lead imports (R115 increment 2) on the immutable
commission-pos-v1 catalog.

Two capabilities: `crm.convert` (convert a lead, and reverse a conversion through `batch reverse`) and `crm.bulk`
(import leads, and reverse such an import), each at `standard`, held by default by the standard, admin, owner and
installation-administrator roles. Agents receive no grant automatically. Three commands: `lead convert`
(`crm.convert`, standard), `lead import` (`crm.bulk`, standard; each row also runs the native `lead create` under
`crm.write`, and the import needs `import` at standard as other imports do) and `crm timeline` (`crm.read`, member).
Conversion additionally asks the native customer right through
`bookflow.company.crm_conversion.require`: `customer` at standard to create a customer or copy contact fields, at
member to link an existing customer only. The history leaves out notes and to-dos without `note`, files without
`attachment` and the customer side without `customer`. No existing capability, default or threshold changes.
"""
from dataclasses import asdict, replace
import hashlib
import json

from . import permission_commission_pos_catalog as previous, permission_catalog as c
from .permission_snapshot import CatalogBundle

SOURCE_COMMIT = '12056be35944174594bd4b45a4e174286516ecc7'
_OWNER = 'bookflow.commands.crm_cmds.{}|src/bookflow/commands/crm_cmds.py'
_AUTHORIZE = _OWNER.format('authorize')
_ROLES = ('standard', 'admin', 'owner', 'hub_admin')
_ADDED = (('crm timeline', 'crm.read', 'member', 'plan_timeline', False),
          ('lead convert', 'crm.convert', 'standard', 'plan_convert', True),
          ('lead import', 'crm.bulk', 'standard', 'plan_import', True))

ADDED_COMMANDS = tuple(c.CommandDescriptor(name, 'company', capability, threshold, (), True, None, False, False, None,
    _AUTHORIZE if gated else None, None, None) for name, capability, threshold, _, gated in _ADDED)
ADDED_ACTIONS = tuple(c.CompanyAction(name, (c.Requirement(capability, threshold),), True,
    tuple(sorted((_OWNER.format(planner), *((_AUTHORIZE,) if gated else ())))))
    for name, capability, threshold, planner, gated in _ADDED)
ADDED_CAPABILITIES = (c.CapabilitySpec('crm.bulk', ('standard',), ('standard',)),
                      c.CapabilitySpec('crm.convert', ('standard',), ('standard',)))
ADDED_DEFAULTS = tuple(c.DefaultEntry(role, c.Requirement(name, 'standard'))
                       for name in ('crm.convert', 'crm.bulk') for role in _ROLES)
SOURCES = (c.ResourceSource('bookflow.company.crm_conversion.require', (('src/bookflow/company/crm_conversion.py', 23),),
    tuple(sorted((c.Requirement('attachment', 'member'), c.Requirement('crm.bulk', 'standard'),
                  c.Requirement('crm.convert', 'standard'), c.Requirement('crm.write', 'standard'),
                  c.Requirement('customer', 'member'), c.Requirement('customer', 'standard'),
                  c.Requirement('import', 'standard'), c.Requirement('note', 'member')),
                 key=lambda r: (r.capability, r.threshold)))),)
CATALOG = replace(previous.CATALOG, version=c.CRM_CONVERT_POLICY_VERSION,
    commands=tuple(sorted((*previous.CATALOG.commands, *ADDED_COMMANDS), key=lambda x: x.name)),
    company_actions=tuple(sorted((*previous.CATALOG.company_actions, *ADDED_ACTIONS), key=lambda x: x.key)),
    capabilities=tuple(sorted((*previous.CATALOG.capabilities, *ADDED_CAPABILITIES), key=lambda x: x.name)),
    defaults=(*previous.CATALOG.defaults, *ADDED_DEFAULTS),
    conditional_sources=tuple(sorted((*previous.CATALOG.conditional_sources, *SOURCES), key=lambda x: x.owner)))
MANIFEST = c.catalog_manifest(CATALOG, previous.MANIFEST.standalone_names)


def catalog_bundle():
    inventory = dict(manifest=asdict(MANIFEST), sources=[asdict(x) for x in CATALOG.conditional_sources])
    digest = hashlib.sha256(json.dumps(inventory, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return CatalogBundle(SOURCE_COMMIT, CATALOG, MANIFEST.standalone_names, digest)
