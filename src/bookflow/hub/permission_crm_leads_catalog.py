"""CRM lead read/write rights on notice-reversal-v1; agent grants remain explicit."""
from dataclasses import asdict, replace
import hashlib
import json
from . import permission_notice_reversal_catalog as previous, permission_catalog as c
from .permission_snapshot import CatalogBundle

SOURCE_COMMIT = 'bf41ae7ff3422aced7ab29023fcc07fac63f87b0'
_OWNER = 'bookflow.commands.crm_cmds.{}|src/bookflow/commands/crm_cmds.py'
_ADDED = tuple(('lead ' + verb, 'crm.read' if verb in ('show', 'list') else 'crm.write',
    'member' if verb in ('show', 'list') else 'standard',
    'plan_' + verb if verb in ('show', 'list') else '_write.<locals>.planner')
    for verb in ('archive', 'close', 'create', 'list', 'reopen', 'show', 'update'))
ADDED_COMMANDS = tuple(c.CommandDescriptor(name, 'company', capability, threshold, (), True,
    None, False, False, None, 'bookflow.commands.crm_cmds.authorize|src/bookflow/commands/crm_cmds.py' if threshold == 'standard' else None,
    None, None) for name, capability, threshold, _ in _ADDED)
ADDED_ACTIONS = tuple(c.CompanyAction(name, (c.Requirement(capability, threshold),), True, (_OWNER.format(planner),) + (('bookflow.commands.crm_cmds.authorize|src/bookflow/commands/crm_cmds.py',) if threshold == 'standard' else ()))
    for name, capability, threshold, planner in _ADDED)
ADDED_CAPABILITIES = (c.CapabilitySpec('crm.read', ('member',), ('member',)),
                      c.CapabilitySpec('crm.write', ('standard',), ('standard',)))
ADDED_DEFAULTS = tuple(c.DefaultEntry(role, c.Requirement('crm.read', 'member'))
    for role in ('readonly', 'standard', 'admin', 'owner', 'hub_admin')) + tuple(
    c.DefaultEntry(role, c.Requirement('crm.write', 'standard')) for role in ('standard', 'admin', 'owner', 'hub_admin'))
SOURCES = (
    replace(next(x for x in previous.CATALOG.conditional_sources if x.owner == 'bookflow.company.audit_visibility.admitted'), requirements=tuple(sorted((*next(x for x in previous.CATALOG.conditional_sources if x.owner == 'bookflow.company.audit_visibility.admitted').requirements, c.Requirement('crm.read', 'member')), key=lambda r: (r.capability, r.threshold)))),
    c.ResourceSource('bookflow.company.crm.require_read', (('src/bookflow/company/crm.py', 15),), (c.Requirement('crm.read', 'member'),)),
    c.ResourceSource('bookflow.company.crm.may', (('src/bookflow/company/crm.py', 20),),
        (c.Requirement('crm.write', 'standard'), c.Requirement('note', 'member'))),
    c.ResourceSource('bookflow.company.todos.admit_link', (('src/bookflow/company/todos.py', 52),), (c.Requirement('crm.read', 'member'),)),
)
_ANNOTATIONS = ('note add', 'note edit', 'note show', 'note list', 'attachment add', 'attachment link',
                'attachment unlink', 'attachment get', 'attachment list', 'attachment text')

def _command(x):
    if x.name in _ANNOTATIONS:
        return replace(x, conditional_owner='bookflow.company.crm_admission.authorize_annotation|src/bookflow/company/crm_admission.py')
    if x.name in ('todo create', 'todo update', 'todo complete'):
        return replace(x, conditional_owner='bookflow.company.todos.authorize_input|src/bookflow/company/todos.py')
    return x

def _action(x):
    owner = None
    if x.key in _ANNOTATIONS:
        owner = 'bookflow.company.crm_admission.authorize_annotation|src/bookflow/company/crm_admission.py'
    elif x.key in ('todo create', 'todo update', 'todo complete'):
        owner = 'bookflow.company.todos.authorize_input|src/bookflow/company/todos.py'
    return replace(x, remaining_graph_owners=tuple(sorted((*x.remaining_graph_owners, owner)))) if owner else x

CATALOG = replace(previous.CATALOG, version=c.CRM_LEADS_POLICY_VERSION,
    commands=tuple(sorted((*(_command(x) for x in previous.CATALOG.commands), *ADDED_COMMANDS), key=lambda x: x.name)),
    capabilities=tuple(sorted((*previous.CATALOG.capabilities, *ADDED_CAPABILITIES), key=lambda x: x.name)),
    defaults=(*previous.CATALOG.defaults, *ADDED_DEFAULTS),
    company_actions=tuple(sorted((*(_action(x) for x in previous.CATALOG.company_actions), *ADDED_ACTIONS), key=lambda x: x.key)),
    conditional_sources=tuple(sorted((*(x for x in previous.CATALOG.conditional_sources if x.owner != 'bookflow.company.audit_visibility.admitted'), *SOURCES), key=lambda x: x.owner)))
MANIFEST = c.catalog_manifest(CATALOG, previous.MANIFEST.standalone_names)


def catalog_bundle():
    inventory = dict(manifest=asdict(MANIFEST), sources=[asdict(x) for x in CATALOG.conditional_sources])
    digest = hashlib.sha256(json.dumps(inventory, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return CatalogBundle(SOURCE_COMMIT, CATALOG, MANIFEST.standalone_names, digest)
