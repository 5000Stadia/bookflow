"""Private frozen catalog for pure policy calculation; never imports the registry or storage."""
from __future__ import annotations
from dataclasses import dataclass, fields, is_dataclass, asdict, replace
from typing import Literal, get_type_hints, get_origin, get_args, Union
from types import UnionType
from enum import Enum
import hashlib
import json

SubjectKind = Literal['human', 'agent', 'system']
Role = Literal['readonly', 'standard', 'admin', 'owner']
DefaultRole = Literal['readonly', 'standard', 'admin', 'owner', 'hub_admin']
Threshold = Literal['authenticated', 'member', 'standard', 'admin', 'owner', 'hub_admin']
ScopeKind = Literal['hub', 'organization', 'company', 'future_company']

@dataclass(frozen=True, slots=True)
class ScopeKey:
    kind: ScopeKind
    id: str


@dataclass(frozen=True, slots=True)
class Requirement:
    capability: str
    threshold: Threshold


@dataclass(frozen=True, slots=True)
class CommandDescriptor:
    name: str
    routed_scope: Literal['hub','company']
    capability: str
    threshold: Threshold
    resources: tuple[Requirement, ...]
    available: bool
    feature: str | None
    local_only: bool
    bootstrap: bool
    authorization_text: str | None
    conditional_owner: str | None
    permanent_recovery_owner: str | None
    transfer_owner: str | None


@dataclass(frozen=True, slots=True)
class CapabilitySpec:
    name: str
    company_thresholds: tuple[Threshold, ...]
    registered_thresholds: tuple[Threshold, ...]


@dataclass(frozen=True, slots=True)
class DefaultEntry:
    role: DefaultRole
    requirement: Requirement


@dataclass(frozen=True, slots=True)
class CompanyAction:
    key: str
    requirements: tuple[Requirement, ...]
    available: bool
    remaining_graph_owners: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AdminAction:
    key: str
    domain: Literal['hub','organization','company']
    threshold: Threshold
    human_only: bool
    available: bool


@dataclass(frozen=True, slots=True)
class ResourceSource:
    owner: str
    call_sites: tuple[tuple[str,int], ...]
    requirements: tuple[Requirement, ...]


@dataclass(frozen=True, slots=True)
class Catalog:
    version: str
    commands: tuple[CommandDescriptor, ...]
    capabilities: tuple[CapabilitySpec, ...]
    defaults: tuple[DefaultEntry, ...]
    company_actions: tuple[CompanyAction, ...]
    admin_actions: tuple[AdminAction, ...]
    conditional_sources: tuple[ResourceSource, ...]


@dataclass(frozen=True, slots=True)
class CatalogManifest:
    version: str
    command_names: tuple[str, ...]
    standalone_names: tuple[str, ...]
    capability_names: tuple[str, ...]
    company_requirements: tuple[Requirement, ...]
    registered_requirements: tuple[Requirement, ...]
    company_action_keys: tuple[str, ...]
    admin_action_keys: tuple[str, ...]
    descriptor_sha256: str


ROLES = ('readonly', 'standard', 'admin', 'owner')
THRESHOLDS = ('authenticated', 'member', 'standard', 'admin', 'owner', 'hub_admin')
_SCOPE_KINDS = ('hub', 'organization', 'company', 'future_company')
DELETE_NAMES = tuple('transaction.' + family + '.delete' for family in
                     ('journal_entry', 'invoice', 'sales_receipt', 'payment'))


class InputErrorCategory(str, Enum):
    invalid_type = 'invalid_type'
    duplicate_key = 'duplicate_key'
    unknown_capability = 'unknown_capability'
    policy_overlap = 'policy_overlap'
    missing_key = 'missing_key'
    unexpected_key = 'unexpected_key'
    invalid_reference = 'invalid_reference'
    manifest_mismatch = 'manifest_mismatch'
    invalid_catalog = 'invalid_catalog'
    inconsistent_fact = 'inconsistent_fact'


class PolicyInputError(ValueError):
    """Only static schema locations, never supplied values, escape validation."""
    def __init__(self, category: InputErrorCategory, field: str):
        self.category, self.field = category, field
        super().__init__(category.value, field)


def _fail(category, field):
    raise PolicyInputError(InputErrorCategory(category), field)


def _check(value, annotation, field='input'):
    origin, args = get_origin(annotation), get_args(annotation)
    if origin in (Union, UnionType):
        for option in args:
            try:
                _check(value, option, field)
                return
            except PolicyInputError:
                pass
        _fail('invalid_type', field)
    elif origin is Literal:
        if not any(type(value) is type(a) and value == a for a in args):
            _fail('invalid_type', field)
    elif origin is tuple:
        if type(value) is not tuple:
            _fail('invalid_type', field)
        if len(args) == 2 and args[1] is Ellipsis:
            for item in value:
                _check(item, args[0], field)
        else:
            if len(value) != len(args):
                _fail('invalid_type', field)
            for item, kind in zip(value, args):
                _check(item, kind, field)
    elif is_dataclass(annotation):
        if type(value) is not annotation:
            _fail('invalid_type', field)
        for key, kind in get_type_hints(annotation).items():
            _check(getattr(value, key), kind, key)
    elif isinstance(annotation, type) and issubclass(annotation, Enum):
        if type(value) is not annotation:
            _fail('invalid_type', field)
    elif type(value) is not annotation:
        _fail('invalid_type', field)
    elif annotation is str and (not value or '*' in value):
        _fail('invalid_type', field)


def _unique(values, key, field):
    result = {}
    for value in values:
        k = key(value)
        if k in result:
            _fail('duplicate_key', field)
        result[k] = value
    return result


def _same_keys(actual, expected, field):
    expected = set(expected)
    if expected - set(actual):
        _fail('missing_key', field)
    if set(actual) - set(expected):
        _fail('unexpected_key', field)


def _scope_key(s):
    return (_SCOPE_KINDS.index(s.kind), s.id)


def _req_key(r):
    return r.capability, THRESHOLDS.index(r.threshold)


def _requirements(catalog, company=False):
    return tuple(sorted((Requirement(c.name, t) for c in catalog.capabilities
                        for t in (c.company_thresholds if company else c.registered_thresholds)), key=_req_key))


def _normal_catalog(catalog):
    _check(catalog, Catalog, 'catalog')
    specs = _unique(catalog.capabilities, lambda x: x.name, 'capabilities')
    commands = _unique(catalog.commands, lambda x: x.name, 'commands')
    actions = _unique(catalog.company_actions, lambda x: x.key, 'company_actions')
    admins = _unique(catalog.admin_actions, lambda x: x.key, 'admin_actions')
    defaults = _unique(catalog.defaults, lambda x: (x.role, x.requirement), 'defaults')
    sources = _unique(catalog.conditional_sources, lambda x: x.owner, 'conditional_sources')
    for spec in specs.values():
        if not spec.company_thresholds and not spec.registered_thresholds:
            _fail('invalid_catalog', 'capabilities')
        for ts in (spec.company_thresholds, spec.registered_thresholds):
            _unique(ts, lambda x: x, 'thresholds')
        if spec.name in DELETE_NAMES and (spec.company_thresholds != ('standard',) or spec.registered_thresholds):
            _fail('invalid_catalog', 'capabilities')
    company = set(_requirements(catalog, True))
    registered = set(_requirements(catalog))
    if company - registered - {Requirement(name, 'standard') for name in DELETE_NAMES}:
        _fail('invalid_catalog', 'capabilities')
    for cmd in commands.values():
        _unique(cmd.resources, lambda x: x, 'resources')
        if Requirement(cmd.capability, cmd.threshold) not in registered or not set(cmd.resources) <= registered:
            _fail('invalid_reference', 'commands')
        if cmd.routed_scope == 'company' and not {Requirement(cmd.capability, cmd.threshold), *cmd.resources} <= company:
            _fail('invalid_reference', 'commands')
    for action in actions.values():
        _unique(action.requirements, lambda x: x, 'requirements')
        _unique(action.remaining_graph_owners, lambda x: x, 'remaining_graph_owners')
        if not action.requirements or not set(action.requirements) <= company:
            _fail('invalid_reference', 'company_actions')
    for entry in defaults.values():
        if entry.requirement.capability in DELETE_NAMES or entry.requirement not in registered:
            _fail('invalid_catalog', 'defaults')
    for source in sources.values():
        _unique(source.call_sites, lambda x: x, 'call_sites')
        _unique(source.requirements, lambda x: x, 'requirements')
        # Private source owners can use known company requirements before a
        # public command registers them. Source metadata grants no availability.
        if any(line <= 0 for _, line in source.call_sites) or not set(source.requirements) <= company:
            _fail('invalid_catalog', 'conditional_sources')
    return replace(catalog,
        commands=tuple(replace(c, resources=tuple(sorted(c.resources, key=_req_key))) for c in sorted(commands.values(), key=lambda x: x.name)),
        capabilities=tuple(replace(c, company_thresholds=tuple(sorted(c.company_thresholds,key=THRESHOLDS.index)), registered_thresholds=tuple(sorted(c.registered_thresholds,key=THRESHOLDS.index))) for c in sorted(specs.values(),key=lambda x:x.name)),
        defaults=tuple(sorted(defaults.values(),key=lambda x: ((*ROLES,'hub_admin').index(x.role), *_req_key(x.requirement)))),
        company_actions=tuple(replace(a, requirements=tuple(sorted(a.requirements,key=_req_key)),remaining_graph_owners=tuple(sorted(a.remaining_graph_owners))) for a in sorted(actions.values(),key=lambda x:x.key)),
        admin_actions=tuple(sorted(admins.values(),key=lambda x:x.key)),
        conditional_sources=tuple(replace(s,call_sites=tuple(sorted(s.call_sites)),requirements=tuple(sorted(s.requirements,key=_req_key))) for s in sorted(sources.values(),key=lambda x:x.owner)))


def catalog_manifest(catalog: Catalog, standalone_names: tuple[str, ...] = ()) -> CatalogManifest:
    """Build a descriptor, not attest that this catalog represents a real root."""
    c = _normal_catalog(catalog)
    _check(standalone_names, tuple[str,...], 'standalone_names')
    _unique(standalone_names, lambda x:x, 'standalone_names')
    if set(standalone_names) & {x.name for x in c.commands}:
        _fail('invalid_catalog', 'standalone_names')
    raw = json.dumps(asdict(c), sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()
    return CatalogManifest(c.version, tuple(x.name for x in c.commands), tuple(sorted(standalone_names)),
        tuple(x.name for x in c.capabilities), _requirements(c, True), _requirements(c),
        tuple(x.key for x in c.company_actions), tuple(x.key for x in c.admin_actions), hashlib.sha256(raw).hexdigest())


FROZEN_COMMANDS = (
    CommandDescriptor(name='account activate', routed_scope='company', capability='account', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='account create', routed_scope='company', capability='account', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='account deactivate', routed_scope='company', capability='account', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='account list', routed_scope='company', capability='account', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='account query', routed_scope='company', capability='account', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='account query options', routed_scope='company', capability='account', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='account show', routed_scope='company', capability='account', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='account update', routed_scope='company', capability='account', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='activity', routed_scope='company', capability='activity', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='application history', routed_scope='company', capability='ledger.read', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='application show', routed_scope='company', capability='ledger.read', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='attachment add', routed_scope='company', capability='attachment', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner='bookflow.commands.attachment_cmds.prepare_add|src/bookflow/commands/attachment_cmds.py'),
    CommandDescriptor(name='attachment get', routed_scope='company', capability='attachment', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner='bookflow.commands.attachment_cmds.prepare_get|src/bookflow/commands/attachment_cmds.py'),
    CommandDescriptor(name='attachment link', routed_scope='company', capability='attachment', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='attachment list', routed_scope='company', capability='attachment', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='attachment unlink', routed_scope='company', capability='attachment', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='audit list', routed_scope='company', capability='audit', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='audit show', routed_scope='company', capability='audit', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='audit tail', routed_scope='company', capability='audit', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='chart apply', routed_scope='company', capability='chart', threshold='admin', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='chart list', routed_scope='hub', capability='chart', threshold='authenticated', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='chart show', routed_scope='hub', capability='chart', threshold='authenticated', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='class activate', routed_scope='company', capability='class', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='class create', routed_scope='company', capability='class', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='class deactivate', routed_scope='company', capability='class', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='class list', routed_scope='company', capability='class', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='class query', routed_scope='company', capability='class', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='class query options', routed_scope='company', capability='class', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='class show', routed_scope='company', capability='class', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='class update', routed_scope='company', capability='class', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='company attach', routed_scope='hub', capability='company', threshold='hub_admin', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='company compact', routed_scope='company', capability='attachment', threshold='admin', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='company detach', routed_scope='hub', capability='company', threshold='hub_admin', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='company list', routed_scope='hub', capability='company', threshold='authenticated', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='company new', routed_scope='hub', capability='company', threshold='authenticated', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text='administrator of the selected organization; hub administrators qualify', conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='company rename', routed_scope='company', capability='company', threshold='admin', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='company show', routed_scope='company', capability='company', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='company update', routed_scope='company', capability='company', threshold='admin', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='company use', routed_scope='hub', capability='company', threshold='authenticated', resources=(), available=True, feature=None, local_only=True, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='custom-field activate', routed_scope='company', capability='custom-field', threshold='admin', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='custom-field create', routed_scope='company', capability='custom-field', threshold='admin', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='custom-field deactivate', routed_scope='company', capability='custom-field', threshold='admin', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='custom-field list', routed_scope='company', capability='custom-field', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='custom-field query', routed_scope='company', capability='custom-field', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='custom-field query children', routed_scope='company', capability='custom-field', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='custom-field query options', routed_scope='company', capability='custom-field', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='custom-field show', routed_scope='company', capability='custom-field', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='custom-field update', routed_scope='company', capability='custom-field', threshold='admin', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='customer activate', routed_scope='company', capability='customer', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='customer create', routed_scope='company', capability='customer', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='customer deactivate', routed_scope='company', capability='customer', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='customer link-vendor', routed_scope='company', capability='customer', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='customer list', routed_scope='company', capability='customer', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='customer query', routed_scope='company', capability='customer', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='customer query options', routed_scope='company', capability='customer', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='customer show', routed_scope='company', capability='customer', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='customer unlink-vendor', routed_scope='company', capability='customer', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='customer update', routed_scope='company', capability='customer', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='customer-message activate', routed_scope='company', capability='customer-message', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='customer-message create', routed_scope='company', capability='customer-message', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='customer-message deactivate', routed_scope='company', capability='customer-message', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='customer-message list', routed_scope='company', capability='customer-message', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='customer-message query', routed_scope='company', capability='customer-message', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='customer-message query options', routed_scope='company', capability='customer-message', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='customer-message show', routed_scope='company', capability='customer-message', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='customer-message update', routed_scope='company', capability='customer-message', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='customer-type activate', routed_scope='company', capability='customer-type', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='customer-type create', routed_scope='company', capability='customer-type', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='customer-type deactivate', routed_scope='company', capability='customer-type', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='customer-type list', routed_scope='company', capability='customer-type', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='customer-type query', routed_scope='company', capability='customer-type', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='customer-type query options', routed_scope='company', capability='customer-type', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='customer-type show', routed_scope='company', capability='customer-type', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='customer-type update', routed_scope='company', capability='customer-type', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='demo reset', routed_scope='hub', capability='demo', threshold='hub_admin', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='directive add', routed_scope='company', capability='directive', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='directive deactivate', routed_scope='company', capability='directive', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='directive list', routed_scope='company', capability='directive', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='directive show', routed_scope='company', capability='directive', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='employee activate', routed_scope='company', capability='employee', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='employee create', routed_scope='company', capability='employee', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='employee deactivate', routed_scope='company', capability='employee', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='employee list', routed_scope='company', capability='employee', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='employee query', routed_scope='company', capability='employee', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='employee query options', routed_scope='company', capability='employee', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='employee show', routed_scope='company', capability='employee', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='employee update', routed_scope='company', capability='employee', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='estimate billing', routed_scope='company', capability='ledger.read', threshold='member', resources=(Requirement(capability='customer-work', threshold='member'),), available=True, feature=None, local_only=False, bootstrap=False, authorization_text='ledger and customer-work membership', conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='estimate copy', routed_scope='company', capability='customer-work', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='estimate create', routed_scope='company', capability='customer-work', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='estimate history', routed_scope='company', capability='customer-work', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='estimate invoice', routed_scope='company', capability='ledger.post', threshold='standard', resources=(Requirement(capability='customer-work', threshold='standard'),), available=True, feature=None, local_only=False, bootstrap=False, authorization_text='ledger.post and customer-work standard role', conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='estimate query', routed_scope='company', capability='customer-work', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='estimate sales-receipt', routed_scope='company', capability='ledger.post', threshold='standard', resources=(Requirement(capability='customer-work', threshold='standard'),), available=True, feature=None, local_only=False, bootstrap=False, authorization_text='ledger.post and customer-work standard role', conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='estimate show', routed_scope='company', capability='customer-work', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='estimate update', routed_scope='company', capability='customer-work', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='estimate work-order', routed_scope='company', capability='customer-work', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='hub audit list', routed_scope='hub', capability='hub', threshold='authenticated', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='hub audit show', routed_scope='hub', capability='hub', threshold='authenticated', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='hub audit tail', routed_scope='hub', capability='hub', threshold='authenticated', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='init', routed_scope='hub', capability='init', threshold='authenticated', resources=(), available=True, feature=None, local_only=True, bootstrap=True, authorization_text='none; the local operating-system login becomes or maps to the first hub administrator', conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='invoice history', routed_scope='company', capability='ledger.read', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text='member ledger.read; customer-work member before linked source details', conditional_owner='bookflow.commands.sales_cmds._read.<locals>.<lambda>|src/bookflow/commands/sales_cmds.py', permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='invoice post', routed_scope='company', capability='ledger.post', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='invoice query', routed_scope='company', capability='ledger.read', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='invoice settlement', routed_scope='company', capability='ledger.read', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='invoice show', routed_scope='company', capability='ledger.read', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text='member ledger.read; customer-work member before linked source details', conditional_owner='bookflow.commands.sales_cmds._read.<locals>.<lambda>|src/bookflow/commands/sales_cmds.py', permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='invoice update', routed_scope='company', capability='ledger.post', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text='standard ledger.post; customer-work standard when linked work is consumed', conditional_owner='bookflow.commands.sales_cmds._write.<locals>.<lambda>|src/bookflow/commands/sales_cmds.py', permanent_recovery_owner='bookflow.commands.sales_cmds._write.<locals>.<lambda>|src/bookflow/commands/sales_cmds.py', transfer_owner=None),
    CommandDescriptor(name='invoice void', routed_scope='company', capability='ledger.post', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text='standard ledger.post; customer-work standard when linked work is consumed', conditional_owner='bookflow.commands.sales_cmds._write.<locals>.<lambda>|src/bookflow/commands/sales_cmds.py', permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='item activate', routed_scope='company', capability='item', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='item create', routed_scope='company', capability='item', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='item deactivate', routed_scope='company', capability='item', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='item list', routed_scope='company', capability='item', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='item query', routed_scope='company', capability='item', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='item query children', routed_scope='company', capability='item', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='item query options', routed_scope='company', capability='item', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='item show', routed_scope='company', capability='item', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='item update', routed_scope='company', capability='item', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='item-category activate', routed_scope='company', capability='item-category', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='item-category create', routed_scope='company', capability='item-category', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='item-category deactivate', routed_scope='company', capability='item-category', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='item-category list', routed_scope='company', capability='item-category', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='item-category query', routed_scope='company', capability='item-category', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='item-category query options', routed_scope='company', capability='item-category', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='item-category show', routed_scope='company', capability='item-category', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='item-category update', routed_scope='company', capability='item-category', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='job-type activate', routed_scope='company', capability='job-type', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='job-type create', routed_scope='company', capability='job-type', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='job-type deactivate', routed_scope='company', capability='job-type', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='job-type list', routed_scope='company', capability='job-type', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='job-type query', routed_scope='company', capability='job-type', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='job-type query options', routed_scope='company', capability='job-type', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='job-type show', routed_scope='company', capability='job-type', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='job-type update', routed_scope='company', capability='job-type', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='journal history', routed_scope='company', capability='ledger.read', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='journal post', routed_scope='company', capability='ledger.post', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='journal query', routed_scope='company', capability='ledger.read', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='journal show', routed_scope='company', capability='ledger.read', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='journal update', routed_scope='company', capability='ledger.post', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='journal void', routed_scope='company', capability='ledger.post', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='note add', routed_scope='company', capability='note', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='note edit', routed_scope='company', capability='note', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='note list', routed_scope='company', capability='note', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='note show', routed_scope='company', capability='note', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='organization list', routed_scope='hub', capability='organization', threshold='authenticated', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='organization new', routed_scope='hub', capability='organization', threshold='hub_admin', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='organization rename', routed_scope='hub', capability='organization', threshold='hub_admin', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='organization show', routed_scope='hub', capability='organization', threshold='authenticated', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='other-name activate', routed_scope='company', capability='other-name', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='other-name convert', routed_scope='company', capability='other-name', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='other-name create', routed_scope='company', capability='other-name', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='other-name deactivate', routed_scope='company', capability='other-name', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='other-name list', routed_scope='company', capability='other-name', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='other-name query', routed_scope='company', capability='other-name', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='other-name query options', routed_scope='company', capability='other-name', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='other-name show', routed_scope='company', capability='other-name', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='other-name update', routed_scope='company', capability='other-name', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='payment apply', routed_scope='company', capability='ledger.post', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner='bookflow.commands.payment_cmds._financial.<locals>.<lambda>|src/bookflow/commands/payment_cmds.py', transfer_owner=None),
    CommandDescriptor(name='payment calculate', routed_scope='company', capability='ledger.read', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='payment history', routed_scope='company', capability='ledger.read', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='payment invoices', routed_scope='company', capability='ledger.read', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='payment operation items', routed_scope='company', capability='ledger.read', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='payment operation show', routed_scope='company', capability='ledger.read', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='payment preview items', routed_scope='company', capability='ledger.post', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='payment query', routed_scope='company', capability='ledger.read', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='payment receive', routed_scope='company', capability='ledger.post', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner='bookflow.commands.payment_cmds._financial.<locals>.<lambda>|src/bookflow/commands/payment_cmds.py', transfer_owner=None),
    CommandDescriptor(name='payment recovery abort', routed_scope='company', capability='ledger.post', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner='bookflow.company.payment_recovery.authorize_input|src/bookflow/company/payment_recovery.py', permanent_recovery_owner='bookflow.commands.payment_recovery_cmds.write.<locals>.<lambda>|src/bookflow/commands/payment_recovery_cmds.py', transfer_owner=None),
    CommandDescriptor(name='payment recovery apply', routed_scope='company', capability='ledger.post', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner='bookflow.company.payment_recovery.authorize_input|src/bookflow/company/payment_recovery.py', permanent_recovery_owner='bookflow.commands.payment_recovery_cmds.write.<locals>.<lambda>|src/bookflow/commands/payment_recovery_cmds.py', transfer_owner=None),
    CommandDescriptor(name='payment recovery begin', routed_scope='company', capability='ledger.post', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner='bookflow.company.payment_recovery.authorize_input|src/bookflow/company/payment_recovery.py', permanent_recovery_owner='bookflow.commands.payment_recovery_cmds.write.<locals>.<lambda>|src/bookflow/commands/payment_recovery_cmds.py', transfer_owner=None),
    CommandDescriptor(name='payment recovery compare', routed_scope='company', capability='ledger.read', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='payment recovery compare-items', routed_scope='company', capability='ledger.read', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='payment recovery items', routed_scope='company', capability='ledger.read', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='payment recovery query', routed_scope='company', capability='ledger.read', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='payment recovery replace', routed_scope='company', capability='ledger.post', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner='bookflow.company.payment_recovery.authorize_input|src/bookflow/company/payment_recovery.py', permanent_recovery_owner='bookflow.commands.payment_recovery_cmds.write.<locals>.<lambda>|src/bookflow/commands/payment_recovery_cmds.py', transfer_owner=None),
    CommandDescriptor(name='payment recovery seal', routed_scope='company', capability='ledger.post', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner='bookflow.company.payment_recovery.authorize_input|src/bookflow/company/payment_recovery.py', permanent_recovery_owner='bookflow.commands.payment_recovery_cmds.write.<locals>.<lambda>|src/bookflow/commands/payment_recovery_cmds.py', transfer_owner=None),
    CommandDescriptor(name='payment recovery show', routed_scope='company', capability='ledger.read', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='payment recovery upload', routed_scope='company', capability='ledger.post', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner='bookflow.company.payment_recovery.authorize_input|src/bookflow/company/payment_recovery.py', permanent_recovery_owner='bookflow.commands.payment_recovery_cmds.write.<locals>.<lambda>|src/bookflow/commands/payment_recovery_cmds.py', transfer_owner=None),
    CommandDescriptor(name='payment selection clear', routed_scope='company', capability='ledger.post', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner='bookflow.company.payment_selection.authorize_input|src/bookflow/company/payment_selection.py', permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='payment selection create', routed_scope='company', capability='ledger.post', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner='bookflow.company.payment_selection.authorize_input|src/bookflow/company/payment_selection.py', permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='payment selection items', routed_scope='company', capability='ledger.read', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='payment selection query', routed_scope='company', capability='ledger.read', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='payment selection show', routed_scope='company', capability='ledger.read', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='payment selection update', routed_scope='company', capability='ledger.post', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner='bookflow.company.payment_selection.authorize_input|src/bookflow/company/payment_selection.py', permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='payment settlement', routed_scope='company', capability='ledger.read', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='payment settlement changes', routed_scope='company', capability='ledger.read', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='payment show', routed_scope='company', capability='ledger.read', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='payment suggest', routed_scope='company', capability='ledger.read', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='payment unapply', routed_scope='company', capability='ledger.post', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner='bookflow.commands.payment_cmds._financial.<locals>.<lambda>|src/bookflow/commands/payment_cmds.py', transfer_owner=None),
    CommandDescriptor(name='payment update', routed_scope='company', capability='ledger.post', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner='bookflow.commands.payment_cmds._financial.<locals>.<lambda>|src/bookflow/commands/payment_cmds.py', transfer_owner=None),
    CommandDescriptor(name='payment void', routed_scope='company', capability='ledger.post', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner='bookflow.commands.payment_cmds._financial.<locals>.<lambda>|src/bookflow/commands/payment_cmds.py', transfer_owner=None),
    CommandDescriptor(name='payment-method activate', routed_scope='company', capability='payment-method', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='payment-method create', routed_scope='company', capability='payment-method', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='payment-method deactivate', routed_scope='company', capability='payment-method', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='payment-method list', routed_scope='company', capability='payment-method', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='payment-method query', routed_scope='company', capability='payment-method', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='payment-method query options', routed_scope='company', capability='payment-method', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='payment-method show', routed_scope='company', capability='payment-method', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='payment-method update', routed_scope='company', capability='payment-method', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='presence clear', routed_scope='company', capability='presence', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='presence set', routed_scope='company', capability='presence', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='price-level activate', routed_scope='company', capability='price-level', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='price-level create', routed_scope='company', capability='price-level', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='price-level deactivate', routed_scope='company', capability='price-level', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='price-level list', routed_scope='company', capability='price-level', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='price-level query', routed_scope='company', capability='price-level', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='price-level query children', routed_scope='company', capability='price-level', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='price-level query options', routed_scope='company', capability='price-level', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='price-level show', routed_scope='company', capability='price-level', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='price-level update', routed_scope='company', capability='price-level', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='profile apply', routed_scope='company', capability='profile', threshold='admin', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='profile list', routed_scope='hub', capability='profile', threshold='authenticated', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='profile show', routed_scope='hub', capability='profile', threshold='authenticated', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='proposal copy', routed_scope='company', capability='customer-work', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='proposal create', routed_scope='company', capability='customer-work', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='proposal estimate', routed_scope='company', capability='customer-work', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='proposal history', routed_scope='company', capability='customer-work', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='proposal query', routed_scope='company', capability='customer-work', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='proposal show', routed_scope='company', capability='customer-work', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='proposal update', routed_scope='company', capability='customer-work', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='rate query', routed_scope='company', capability='ledger.read', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='rate set', routed_scope='company', capability='ledger.post', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='rate show', routed_scope='company', capability='ledger.read', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='register calculate', routed_scope='company', capability='ledger.read', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='register post', routed_scope='company', capability='ledger.post', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='register query', routed_scope='company', capability='ledger.read', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='register update', routed_scope='company', capability='ledger.post', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='report balance-sheet', routed_scope='company', capability='reports', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='report general-ledger', routed_scope='company', capability='reports', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='report profit-and-loss', routed_scope='company', capability='reports', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='report trial-balance', routed_scope='company', capability='reports', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='sales-receipt history', routed_scope='company', capability='ledger.read', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text='member ledger.read; customer-work member before linked source details', conditional_owner='bookflow.commands.sales_cmds._read.<locals>.<lambda>|src/bookflow/commands/sales_cmds.py', permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='sales-receipt post', routed_scope='company', capability='ledger.post', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='sales-receipt query', routed_scope='company', capability='ledger.read', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='sales-receipt show', routed_scope='company', capability='ledger.read', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text='member ledger.read; customer-work member before linked source details', conditional_owner='bookflow.commands.sales_cmds._read.<locals>.<lambda>|src/bookflow/commands/sales_cmds.py', permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='sales-receipt update', routed_scope='company', capability='ledger.post', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text='standard ledger.post; customer-work standard when linked work is consumed', conditional_owner='bookflow.commands.sales_cmds._write.<locals>.<lambda>|src/bookflow/commands/sales_cmds.py', permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='sales-receipt void', routed_scope='company', capability='ledger.post', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text='standard ledger.post; customer-work standard when linked work is consumed', conditional_owner='bookflow.commands.sales_cmds._write.<locals>.<lambda>|src/bookflow/commands/sales_cmds.py', permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='sales-rep activate', routed_scope='company', capability='sales-rep', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='sales-rep create', routed_scope='company', capability='sales-rep', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='sales-rep deactivate', routed_scope='company', capability='sales-rep', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='sales-rep list', routed_scope='company', capability='sales-rep', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='sales-rep query', routed_scope='company', capability='sales-rep', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='sales-rep query options', routed_scope='company', capability='sales-rep', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='sales-rep show', routed_scope='company', capability='sales-rep', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='sales-rep update', routed_scope='company', capability='sales-rep', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='sales-tax-code activate', routed_scope='company', capability='sales-tax-code', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='sales-tax-code create', routed_scope='company', capability='sales-tax-code', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='sales-tax-code deactivate', routed_scope='company', capability='sales-tax-code', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='sales-tax-code list', routed_scope='company', capability='sales-tax-code', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='sales-tax-code query', routed_scope='company', capability='sales-tax-code', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='sales-tax-code query options', routed_scope='company', capability='sales-tax-code', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='sales-tax-code show', routed_scope='company', capability='sales-tax-code', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='sales-tax-code update', routed_scope='company', capability='sales-tax-code', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='serve', routed_scope='hub', capability='serve', threshold='authenticated', resources=(), available=True, feature=None, local_only=True, bootstrap=True, authorization_text='human hub administrator', conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='ship-method activate', routed_scope='company', capability='ship-method', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='ship-method create', routed_scope='company', capability='ship-method', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='ship-method deactivate', routed_scope='company', capability='ship-method', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='ship-method list', routed_scope='company', capability='ship-method', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='ship-method query', routed_scope='company', capability='ship-method', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='ship-method query options', routed_scope='company', capability='ship-method', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='ship-method show', routed_scope='company', capability='ship-method', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='ship-method update', routed_scope='company', capability='ship-method', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='term activate', routed_scope='company', capability='term', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='term create', routed_scope='company', capability='term', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='term deactivate', routed_scope='company', capability='term', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='term list', routed_scope='company', capability='term', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='term query', routed_scope='company', capability='term', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='term query options', routed_scope='company', capability='term', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='term show', routed_scope='company', capability='term', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='term update', routed_scope='company', capability='term', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='token issue', routed_scope='hub', capability='token', threshold='authenticated', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text='human self-service; a human hub administrator may issue for another user', conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='token list', routed_scope='hub', capability='token', threshold='authenticated', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text="own tokens; a hub administrator may list another user's tokens", conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='token revoke', routed_scope='hub', capability='token', threshold='authenticated', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text="own tokens; a hub administrator may revoke another user's token", conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='undo', routed_scope='company', capability='undo', threshold='admin', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text="admin and the original event's current role threshold", conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='unit-of-measure activate', routed_scope='company', capability='unit-of-measure', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='unit-of-measure create', routed_scope='company', capability='unit-of-measure', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='unit-of-measure deactivate', routed_scope='company', capability='unit-of-measure', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='unit-of-measure list', routed_scope='company', capability='unit-of-measure', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='unit-of-measure query', routed_scope='company', capability='unit-of-measure', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='unit-of-measure query children', routed_scope='company', capability='unit-of-measure', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='unit-of-measure query options', routed_scope='company', capability='unit-of-measure', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='unit-of-measure show', routed_scope='company', capability='unit-of-measure', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='unit-of-measure update', routed_scope='company', capability='unit-of-measure', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='upgrade', routed_scope='hub', capability='upgrade', threshold='authenticated', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='user set-password', routed_scope='hub', capability='user', threshold='authenticated', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text='human self-service; a human hub administrator may reset another human', conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='vendor activate', routed_scope='company', capability='vendor', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='vendor create', routed_scope='company', capability='vendor', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='vendor deactivate', routed_scope='company', capability='vendor', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='vendor list', routed_scope='company', capability='vendor', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='vendor query', routed_scope='company', capability='vendor', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='vendor query children', routed_scope='company', capability='vendor', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='vendor query options', routed_scope='company', capability='vendor', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='vendor show', routed_scope='company', capability='vendor', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='vendor update', routed_scope='company', capability='vendor', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='vendor-type activate', routed_scope='company', capability='vendor-type', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='vendor-type create', routed_scope='company', capability='vendor-type', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='vendor-type deactivate', routed_scope='company', capability='vendor-type', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='vendor-type list', routed_scope='company', capability='vendor-type', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='vendor-type query', routed_scope='company', capability='vendor-type', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='vendor-type query options', routed_scope='company', capability='vendor-type', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='vendor-type show', routed_scope='company', capability='vendor-type', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='vendor-type update', routed_scope='company', capability='vendor-type', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='work-order billing', routed_scope='company', capability='ledger.read', threshold='member', resources=(Requirement(capability='customer-work', threshold='member'),), available=True, feature=None, local_only=False, bootstrap=False, authorization_text='ledger and customer-work membership', conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='work-order complete', routed_scope='company', capability='customer-work', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='work-order copy', routed_scope='company', capability='customer-work', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='work-order create', routed_scope='company', capability='customer-work', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='work-order history', routed_scope='company', capability='customer-work', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='work-order invoice', routed_scope='company', capability='ledger.post', threshold='standard', resources=(Requirement(capability='customer-work', threshold='standard'),), available=True, feature=None, local_only=False, bootstrap=False, authorization_text='ledger.post and customer-work standard role', conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='work-order query', routed_scope='company', capability='customer-work', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='work-order sales-receipt', routed_scope='company', capability='ledger.post', threshold='standard', resources=(Requirement(capability='customer-work', threshold='standard'),), available=True, feature=None, local_only=False, bootstrap=False, authorization_text='ledger.post and customer-work standard role', conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='work-order show', routed_scope='company', capability='customer-work', threshold='member', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
    CommandDescriptor(name='work-order update', routed_scope='company', capability='customer-work', threshold='standard', resources=(), available=True, feature=None, local_only=False, bootstrap=False, authorization_text=None, conditional_owner=None, permanent_recovery_owner=None, transfer_owner=None),
)


FROZEN_CAPABILITIES = (
    CapabilitySpec(name='account', company_thresholds=('member', 'standard'), registered_thresholds=('member', 'standard')),
    CapabilitySpec(name='activity', company_thresholds=('member',), registered_thresholds=('member',)),
    CapabilitySpec(name='attachment', company_thresholds=('member', 'standard', 'admin'), registered_thresholds=('member', 'standard', 'admin')),
    CapabilitySpec(name='audit', company_thresholds=('member',), registered_thresholds=('member',)),
    CapabilitySpec(name='chart', company_thresholds=('admin',), registered_thresholds=('authenticated', 'admin')),
    CapabilitySpec(name='class', company_thresholds=('member', 'standard'), registered_thresholds=('member', 'standard')),
    CapabilitySpec(name='company', company_thresholds=('member', 'admin'), registered_thresholds=('authenticated', 'member', 'admin', 'hub_admin')),
    CapabilitySpec(name='custom-field', company_thresholds=('member', 'admin'), registered_thresholds=('member', 'admin')),
    CapabilitySpec(name='customer', company_thresholds=('member', 'standard'), registered_thresholds=('member', 'standard')),
    CapabilitySpec(name='customer-message', company_thresholds=('member', 'standard'), registered_thresholds=('member', 'standard')),
    CapabilitySpec(name='customer-type', company_thresholds=('member', 'standard'), registered_thresholds=('member', 'standard')),
    CapabilitySpec(name='customer-work', company_thresholds=('member', 'standard'), registered_thresholds=('member', 'standard')),
    CapabilitySpec(name='demo', company_thresholds=(), registered_thresholds=('hub_admin',)),
    CapabilitySpec(name='directive', company_thresholds=('member', 'standard'), registered_thresholds=('member', 'standard')),
    CapabilitySpec(name='employee', company_thresholds=('member', 'standard'), registered_thresholds=('member', 'standard')),
    CapabilitySpec(name='hub', company_thresholds=(), registered_thresholds=('authenticated',)),
    CapabilitySpec(name='init', company_thresholds=(), registered_thresholds=('authenticated',)),
    CapabilitySpec(name='item', company_thresholds=('member', 'standard'), registered_thresholds=('member', 'standard')),
    CapabilitySpec(name='item-category', company_thresholds=('member', 'standard'), registered_thresholds=('member', 'standard')),
    CapabilitySpec(name='job-type', company_thresholds=('member', 'standard'), registered_thresholds=('member', 'standard')),
    CapabilitySpec(name='ledger.post', company_thresholds=('standard',), registered_thresholds=('standard',)),
    CapabilitySpec(name='ledger.read', company_thresholds=('member',), registered_thresholds=('member',)),
    CapabilitySpec(name='note', company_thresholds=('member', 'standard'), registered_thresholds=('member', 'standard')),
    CapabilitySpec(name='organization', company_thresholds=(), registered_thresholds=('authenticated', 'hub_admin')),
    CapabilitySpec(name='other-name', company_thresholds=('member', 'standard'), registered_thresholds=('member', 'standard')),
    CapabilitySpec(name='payment-method', company_thresholds=('member', 'standard'), registered_thresholds=('member', 'standard')),
    CapabilitySpec(name='presence', company_thresholds=('standard',), registered_thresholds=('standard',)),
    CapabilitySpec(name='price-level', company_thresholds=('member', 'standard'), registered_thresholds=('member', 'standard')),
    CapabilitySpec(name='profile', company_thresholds=('admin',), registered_thresholds=('authenticated', 'admin')),
    CapabilitySpec(name='reports', company_thresholds=('member',), registered_thresholds=('member',)),
    CapabilitySpec(name='sales-rep', company_thresholds=('member', 'standard'), registered_thresholds=('member', 'standard')),
    CapabilitySpec(name='sales-tax-code', company_thresholds=('member', 'standard'), registered_thresholds=('member', 'standard')),
    CapabilitySpec(name='serve', company_thresholds=(), registered_thresholds=('authenticated',)),
    CapabilitySpec(name='ship-method', company_thresholds=('member', 'standard'), registered_thresholds=('member', 'standard')),
    CapabilitySpec(name='term', company_thresholds=('member', 'standard'), registered_thresholds=('member', 'standard')),
    CapabilitySpec(name='token', company_thresholds=(), registered_thresholds=('authenticated',)),
    CapabilitySpec(name='transaction.invoice.delete', company_thresholds=('standard',), registered_thresholds=()),
    CapabilitySpec(name='transaction.journal_entry.delete', company_thresholds=('standard',), registered_thresholds=()),
    CapabilitySpec(name='transaction.payment.delete', company_thresholds=('standard',), registered_thresholds=()),
    CapabilitySpec(name='transaction.sales_receipt.delete', company_thresholds=('standard',), registered_thresholds=()),
    CapabilitySpec(name='undo', company_thresholds=('admin',), registered_thresholds=('admin',)),
    CapabilitySpec(name='unit-of-measure', company_thresholds=('member', 'standard'), registered_thresholds=('member', 'standard')),
    CapabilitySpec(name='upgrade', company_thresholds=(), registered_thresholds=('authenticated',)),
    CapabilitySpec(name='user', company_thresholds=(), registered_thresholds=('authenticated',)),
    CapabilitySpec(name='vendor', company_thresholds=('member', 'standard'), registered_thresholds=('member', 'standard')),
    CapabilitySpec(name='vendor-type', company_thresholds=('member', 'standard'), registered_thresholds=('member', 'standard')),
)


FROZEN_DEFAULTS = (
    DefaultEntry(role='readonly', requirement=Requirement(capability='account', threshold='member')),
    DefaultEntry(role='readonly', requirement=Requirement(capability='activity', threshold='member')),
    DefaultEntry(role='readonly', requirement=Requirement(capability='attachment', threshold='member')),
    DefaultEntry(role='readonly', requirement=Requirement(capability='audit', threshold='member')),
    DefaultEntry(role='readonly', requirement=Requirement(capability='chart', threshold='authenticated')),
    DefaultEntry(role='readonly', requirement=Requirement(capability='class', threshold='member')),
    DefaultEntry(role='readonly', requirement=Requirement(capability='company', threshold='authenticated')),
    DefaultEntry(role='readonly', requirement=Requirement(capability='company', threshold='member')),
    DefaultEntry(role='readonly', requirement=Requirement(capability='custom-field', threshold='member')),
    DefaultEntry(role='readonly', requirement=Requirement(capability='customer', threshold='member')),
    DefaultEntry(role='readonly', requirement=Requirement(capability='customer-message', threshold='member')),
    DefaultEntry(role='readonly', requirement=Requirement(capability='customer-type', threshold='member')),
    DefaultEntry(role='readonly', requirement=Requirement(capability='customer-work', threshold='member')),
    DefaultEntry(role='readonly', requirement=Requirement(capability='directive', threshold='member')),
    DefaultEntry(role='readonly', requirement=Requirement(capability='employee', threshold='member')),
    DefaultEntry(role='readonly', requirement=Requirement(capability='hub', threshold='authenticated')),
    DefaultEntry(role='readonly', requirement=Requirement(capability='init', threshold='authenticated')),
    DefaultEntry(role='readonly', requirement=Requirement(capability='item', threshold='member')),
    DefaultEntry(role='readonly', requirement=Requirement(capability='item-category', threshold='member')),
    DefaultEntry(role='readonly', requirement=Requirement(capability='job-type', threshold='member')),
    DefaultEntry(role='readonly', requirement=Requirement(capability='ledger.read', threshold='member')),
    DefaultEntry(role='readonly', requirement=Requirement(capability='note', threshold='member')),
    DefaultEntry(role='readonly', requirement=Requirement(capability='organization', threshold='authenticated')),
    DefaultEntry(role='readonly', requirement=Requirement(capability='other-name', threshold='member')),
    DefaultEntry(role='readonly', requirement=Requirement(capability='payment-method', threshold='member')),
    DefaultEntry(role='readonly', requirement=Requirement(capability='price-level', threshold='member')),
    DefaultEntry(role='readonly', requirement=Requirement(capability='profile', threshold='authenticated')),
    DefaultEntry(role='readonly', requirement=Requirement(capability='reports', threshold='member')),
    DefaultEntry(role='readonly', requirement=Requirement(capability='sales-rep', threshold='member')),
    DefaultEntry(role='readonly', requirement=Requirement(capability='sales-tax-code', threshold='member')),
    DefaultEntry(role='readonly', requirement=Requirement(capability='serve', threshold='authenticated')),
    DefaultEntry(role='readonly', requirement=Requirement(capability='ship-method', threshold='member')),
    DefaultEntry(role='readonly', requirement=Requirement(capability='term', threshold='member')),
    DefaultEntry(role='readonly', requirement=Requirement(capability='token', threshold='authenticated')),
    DefaultEntry(role='readonly', requirement=Requirement(capability='unit-of-measure', threshold='member')),
    DefaultEntry(role='readonly', requirement=Requirement(capability='upgrade', threshold='authenticated')),
    DefaultEntry(role='readonly', requirement=Requirement(capability='user', threshold='authenticated')),
    DefaultEntry(role='readonly', requirement=Requirement(capability='vendor', threshold='member')),
    DefaultEntry(role='readonly', requirement=Requirement(capability='vendor-type', threshold='member')),
    DefaultEntry(role='standard', requirement=Requirement(capability='account', threshold='member')),
    DefaultEntry(role='standard', requirement=Requirement(capability='account', threshold='standard')),
    DefaultEntry(role='standard', requirement=Requirement(capability='activity', threshold='member')),
    DefaultEntry(role='standard', requirement=Requirement(capability='attachment', threshold='member')),
    DefaultEntry(role='standard', requirement=Requirement(capability='attachment', threshold='standard')),
    DefaultEntry(role='standard', requirement=Requirement(capability='audit', threshold='member')),
    DefaultEntry(role='standard', requirement=Requirement(capability='chart', threshold='authenticated')),
    DefaultEntry(role='standard', requirement=Requirement(capability='class', threshold='member')),
    DefaultEntry(role='standard', requirement=Requirement(capability='class', threshold='standard')),
    DefaultEntry(role='standard', requirement=Requirement(capability='company', threshold='authenticated')),
    DefaultEntry(role='standard', requirement=Requirement(capability='company', threshold='member')),
    DefaultEntry(role='standard', requirement=Requirement(capability='custom-field', threshold='member')),
    DefaultEntry(role='standard', requirement=Requirement(capability='customer', threshold='member')),
    DefaultEntry(role='standard', requirement=Requirement(capability='customer', threshold='standard')),
    DefaultEntry(role='standard', requirement=Requirement(capability='customer-message', threshold='member')),
    DefaultEntry(role='standard', requirement=Requirement(capability='customer-message', threshold='standard')),
    DefaultEntry(role='standard', requirement=Requirement(capability='customer-type', threshold='member')),
    DefaultEntry(role='standard', requirement=Requirement(capability='customer-type', threshold='standard')),
    DefaultEntry(role='standard', requirement=Requirement(capability='customer-work', threshold='member')),
    DefaultEntry(role='standard', requirement=Requirement(capability='customer-work', threshold='standard')),
    DefaultEntry(role='standard', requirement=Requirement(capability='directive', threshold='member')),
    DefaultEntry(role='standard', requirement=Requirement(capability='directive', threshold='standard')),
    DefaultEntry(role='standard', requirement=Requirement(capability='employee', threshold='member')),
    DefaultEntry(role='standard', requirement=Requirement(capability='employee', threshold='standard')),
    DefaultEntry(role='standard', requirement=Requirement(capability='hub', threshold='authenticated')),
    DefaultEntry(role='standard', requirement=Requirement(capability='init', threshold='authenticated')),
    DefaultEntry(role='standard', requirement=Requirement(capability='item', threshold='member')),
    DefaultEntry(role='standard', requirement=Requirement(capability='item', threshold='standard')),
    DefaultEntry(role='standard', requirement=Requirement(capability='item-category', threshold='member')),
    DefaultEntry(role='standard', requirement=Requirement(capability='item-category', threshold='standard')),
    DefaultEntry(role='standard', requirement=Requirement(capability='job-type', threshold='member')),
    DefaultEntry(role='standard', requirement=Requirement(capability='job-type', threshold='standard')),
    DefaultEntry(role='standard', requirement=Requirement(capability='ledger.post', threshold='standard')),
    DefaultEntry(role='standard', requirement=Requirement(capability='ledger.read', threshold='member')),
    DefaultEntry(role='standard', requirement=Requirement(capability='note', threshold='member')),
    DefaultEntry(role='standard', requirement=Requirement(capability='note', threshold='standard')),
    DefaultEntry(role='standard', requirement=Requirement(capability='organization', threshold='authenticated')),
    DefaultEntry(role='standard', requirement=Requirement(capability='other-name', threshold='member')),
    DefaultEntry(role='standard', requirement=Requirement(capability='other-name', threshold='standard')),
    DefaultEntry(role='standard', requirement=Requirement(capability='payment-method', threshold='member')),
    DefaultEntry(role='standard', requirement=Requirement(capability='payment-method', threshold='standard')),
    DefaultEntry(role='standard', requirement=Requirement(capability='presence', threshold='standard')),
    DefaultEntry(role='standard', requirement=Requirement(capability='price-level', threshold='member')),
    DefaultEntry(role='standard', requirement=Requirement(capability='price-level', threshold='standard')),
    DefaultEntry(role='standard', requirement=Requirement(capability='profile', threshold='authenticated')),
    DefaultEntry(role='standard', requirement=Requirement(capability='reports', threshold='member')),
    DefaultEntry(role='standard', requirement=Requirement(capability='sales-rep', threshold='member')),
    DefaultEntry(role='standard', requirement=Requirement(capability='sales-rep', threshold='standard')),
    DefaultEntry(role='standard', requirement=Requirement(capability='sales-tax-code', threshold='member')),
    DefaultEntry(role='standard', requirement=Requirement(capability='sales-tax-code', threshold='standard')),
    DefaultEntry(role='standard', requirement=Requirement(capability='serve', threshold='authenticated')),
    DefaultEntry(role='standard', requirement=Requirement(capability='ship-method', threshold='member')),
    DefaultEntry(role='standard', requirement=Requirement(capability='ship-method', threshold='standard')),
    DefaultEntry(role='standard', requirement=Requirement(capability='term', threshold='member')),
    DefaultEntry(role='standard', requirement=Requirement(capability='term', threshold='standard')),
    DefaultEntry(role='standard', requirement=Requirement(capability='token', threshold='authenticated')),
    DefaultEntry(role='standard', requirement=Requirement(capability='unit-of-measure', threshold='member')),
    DefaultEntry(role='standard', requirement=Requirement(capability='unit-of-measure', threshold='standard')),
    DefaultEntry(role='standard', requirement=Requirement(capability='upgrade', threshold='authenticated')),
    DefaultEntry(role='standard', requirement=Requirement(capability='user', threshold='authenticated')),
    DefaultEntry(role='standard', requirement=Requirement(capability='vendor', threshold='member')),
    DefaultEntry(role='standard', requirement=Requirement(capability='vendor', threshold='standard')),
    DefaultEntry(role='standard', requirement=Requirement(capability='vendor-type', threshold='member')),
    DefaultEntry(role='standard', requirement=Requirement(capability='vendor-type', threshold='standard')),
    DefaultEntry(role='admin', requirement=Requirement(capability='account', threshold='member')),
    DefaultEntry(role='admin', requirement=Requirement(capability='account', threshold='standard')),
    DefaultEntry(role='admin', requirement=Requirement(capability='activity', threshold='member')),
    DefaultEntry(role='admin', requirement=Requirement(capability='attachment', threshold='member')),
    DefaultEntry(role='admin', requirement=Requirement(capability='attachment', threshold='standard')),
    DefaultEntry(role='admin', requirement=Requirement(capability='attachment', threshold='admin')),
    DefaultEntry(role='admin', requirement=Requirement(capability='audit', threshold='member')),
    DefaultEntry(role='admin', requirement=Requirement(capability='chart', threshold='authenticated')),
    DefaultEntry(role='admin', requirement=Requirement(capability='chart', threshold='admin')),
    DefaultEntry(role='admin', requirement=Requirement(capability='class', threshold='member')),
    DefaultEntry(role='admin', requirement=Requirement(capability='class', threshold='standard')),
    DefaultEntry(role='admin', requirement=Requirement(capability='company', threshold='authenticated')),
    DefaultEntry(role='admin', requirement=Requirement(capability='company', threshold='member')),
    DefaultEntry(role='admin', requirement=Requirement(capability='company', threshold='admin')),
    DefaultEntry(role='admin', requirement=Requirement(capability='custom-field', threshold='member')),
    DefaultEntry(role='admin', requirement=Requirement(capability='custom-field', threshold='admin')),
    DefaultEntry(role='admin', requirement=Requirement(capability='customer', threshold='member')),
    DefaultEntry(role='admin', requirement=Requirement(capability='customer', threshold='standard')),
    DefaultEntry(role='admin', requirement=Requirement(capability='customer-message', threshold='member')),
    DefaultEntry(role='admin', requirement=Requirement(capability='customer-message', threshold='standard')),
    DefaultEntry(role='admin', requirement=Requirement(capability='customer-type', threshold='member')),
    DefaultEntry(role='admin', requirement=Requirement(capability='customer-type', threshold='standard')),
    DefaultEntry(role='admin', requirement=Requirement(capability='customer-work', threshold='member')),
    DefaultEntry(role='admin', requirement=Requirement(capability='customer-work', threshold='standard')),
    DefaultEntry(role='admin', requirement=Requirement(capability='directive', threshold='member')),
    DefaultEntry(role='admin', requirement=Requirement(capability='directive', threshold='standard')),
    DefaultEntry(role='admin', requirement=Requirement(capability='employee', threshold='member')),
    DefaultEntry(role='admin', requirement=Requirement(capability='employee', threshold='standard')),
    DefaultEntry(role='admin', requirement=Requirement(capability='hub', threshold='authenticated')),
    DefaultEntry(role='admin', requirement=Requirement(capability='init', threshold='authenticated')),
    DefaultEntry(role='admin', requirement=Requirement(capability='item', threshold='member')),
    DefaultEntry(role='admin', requirement=Requirement(capability='item', threshold='standard')),
    DefaultEntry(role='admin', requirement=Requirement(capability='item-category', threshold='member')),
    DefaultEntry(role='admin', requirement=Requirement(capability='item-category', threshold='standard')),
    DefaultEntry(role='admin', requirement=Requirement(capability='job-type', threshold='member')),
    DefaultEntry(role='admin', requirement=Requirement(capability='job-type', threshold='standard')),
    DefaultEntry(role='admin', requirement=Requirement(capability='ledger.post', threshold='standard')),
    DefaultEntry(role='admin', requirement=Requirement(capability='ledger.read', threshold='member')),
    DefaultEntry(role='admin', requirement=Requirement(capability='note', threshold='member')),
    DefaultEntry(role='admin', requirement=Requirement(capability='note', threshold='standard')),
    DefaultEntry(role='admin', requirement=Requirement(capability='organization', threshold='authenticated')),
    DefaultEntry(role='admin', requirement=Requirement(capability='other-name', threshold='member')),
    DefaultEntry(role='admin', requirement=Requirement(capability='other-name', threshold='standard')),
    DefaultEntry(role='admin', requirement=Requirement(capability='payment-method', threshold='member')),
    DefaultEntry(role='admin', requirement=Requirement(capability='payment-method', threshold='standard')),
    DefaultEntry(role='admin', requirement=Requirement(capability='presence', threshold='standard')),
    DefaultEntry(role='admin', requirement=Requirement(capability='price-level', threshold='member')),
    DefaultEntry(role='admin', requirement=Requirement(capability='price-level', threshold='standard')),
    DefaultEntry(role='admin', requirement=Requirement(capability='profile', threshold='authenticated')),
    DefaultEntry(role='admin', requirement=Requirement(capability='profile', threshold='admin')),
    DefaultEntry(role='admin', requirement=Requirement(capability='reports', threshold='member')),
    DefaultEntry(role='admin', requirement=Requirement(capability='sales-rep', threshold='member')),
    DefaultEntry(role='admin', requirement=Requirement(capability='sales-rep', threshold='standard')),
    DefaultEntry(role='admin', requirement=Requirement(capability='sales-tax-code', threshold='member')),
    DefaultEntry(role='admin', requirement=Requirement(capability='sales-tax-code', threshold='standard')),
    DefaultEntry(role='admin', requirement=Requirement(capability='serve', threshold='authenticated')),
    DefaultEntry(role='admin', requirement=Requirement(capability='ship-method', threshold='member')),
    DefaultEntry(role='admin', requirement=Requirement(capability='ship-method', threshold='standard')),
    DefaultEntry(role='admin', requirement=Requirement(capability='term', threshold='member')),
    DefaultEntry(role='admin', requirement=Requirement(capability='term', threshold='standard')),
    DefaultEntry(role='admin', requirement=Requirement(capability='token', threshold='authenticated')),
    DefaultEntry(role='admin', requirement=Requirement(capability='undo', threshold='admin')),
    DefaultEntry(role='admin', requirement=Requirement(capability='unit-of-measure', threshold='member')),
    DefaultEntry(role='admin', requirement=Requirement(capability='unit-of-measure', threshold='standard')),
    DefaultEntry(role='admin', requirement=Requirement(capability='upgrade', threshold='authenticated')),
    DefaultEntry(role='admin', requirement=Requirement(capability='user', threshold='authenticated')),
    DefaultEntry(role='admin', requirement=Requirement(capability='vendor', threshold='member')),
    DefaultEntry(role='admin', requirement=Requirement(capability='vendor', threshold='standard')),
    DefaultEntry(role='admin', requirement=Requirement(capability='vendor-type', threshold='member')),
    DefaultEntry(role='admin', requirement=Requirement(capability='vendor-type', threshold='standard')),
    DefaultEntry(role='owner', requirement=Requirement(capability='account', threshold='member')),
    DefaultEntry(role='owner', requirement=Requirement(capability='account', threshold='standard')),
    DefaultEntry(role='owner', requirement=Requirement(capability='activity', threshold='member')),
    DefaultEntry(role='owner', requirement=Requirement(capability='attachment', threshold='member')),
    DefaultEntry(role='owner', requirement=Requirement(capability='attachment', threshold='standard')),
    DefaultEntry(role='owner', requirement=Requirement(capability='attachment', threshold='admin')),
    DefaultEntry(role='owner', requirement=Requirement(capability='audit', threshold='member')),
    DefaultEntry(role='owner', requirement=Requirement(capability='chart', threshold='authenticated')),
    DefaultEntry(role='owner', requirement=Requirement(capability='chart', threshold='admin')),
    DefaultEntry(role='owner', requirement=Requirement(capability='class', threshold='member')),
    DefaultEntry(role='owner', requirement=Requirement(capability='class', threshold='standard')),
    DefaultEntry(role='owner', requirement=Requirement(capability='company', threshold='authenticated')),
    DefaultEntry(role='owner', requirement=Requirement(capability='company', threshold='member')),
    DefaultEntry(role='owner', requirement=Requirement(capability='company', threshold='admin')),
    DefaultEntry(role='owner', requirement=Requirement(capability='custom-field', threshold='member')),
    DefaultEntry(role='owner', requirement=Requirement(capability='custom-field', threshold='admin')),
    DefaultEntry(role='owner', requirement=Requirement(capability='customer', threshold='member')),
    DefaultEntry(role='owner', requirement=Requirement(capability='customer', threshold='standard')),
    DefaultEntry(role='owner', requirement=Requirement(capability='customer-message', threshold='member')),
    DefaultEntry(role='owner', requirement=Requirement(capability='customer-message', threshold='standard')),
    DefaultEntry(role='owner', requirement=Requirement(capability='customer-type', threshold='member')),
    DefaultEntry(role='owner', requirement=Requirement(capability='customer-type', threshold='standard')),
    DefaultEntry(role='owner', requirement=Requirement(capability='customer-work', threshold='member')),
    DefaultEntry(role='owner', requirement=Requirement(capability='customer-work', threshold='standard')),
    DefaultEntry(role='owner', requirement=Requirement(capability='directive', threshold='member')),
    DefaultEntry(role='owner', requirement=Requirement(capability='directive', threshold='standard')),
    DefaultEntry(role='owner', requirement=Requirement(capability='employee', threshold='member')),
    DefaultEntry(role='owner', requirement=Requirement(capability='employee', threshold='standard')),
    DefaultEntry(role='owner', requirement=Requirement(capability='hub', threshold='authenticated')),
    DefaultEntry(role='owner', requirement=Requirement(capability='init', threshold='authenticated')),
    DefaultEntry(role='owner', requirement=Requirement(capability='item', threshold='member')),
    DefaultEntry(role='owner', requirement=Requirement(capability='item', threshold='standard')),
    DefaultEntry(role='owner', requirement=Requirement(capability='item-category', threshold='member')),
    DefaultEntry(role='owner', requirement=Requirement(capability='item-category', threshold='standard')),
    DefaultEntry(role='owner', requirement=Requirement(capability='job-type', threshold='member')),
    DefaultEntry(role='owner', requirement=Requirement(capability='job-type', threshold='standard')),
    DefaultEntry(role='owner', requirement=Requirement(capability='ledger.post', threshold='standard')),
    DefaultEntry(role='owner', requirement=Requirement(capability='ledger.read', threshold='member')),
    DefaultEntry(role='owner', requirement=Requirement(capability='note', threshold='member')),
    DefaultEntry(role='owner', requirement=Requirement(capability='note', threshold='standard')),
    DefaultEntry(role='owner', requirement=Requirement(capability='organization', threshold='authenticated')),
    DefaultEntry(role='owner', requirement=Requirement(capability='other-name', threshold='member')),
    DefaultEntry(role='owner', requirement=Requirement(capability='other-name', threshold='standard')),
    DefaultEntry(role='owner', requirement=Requirement(capability='payment-method', threshold='member')),
    DefaultEntry(role='owner', requirement=Requirement(capability='payment-method', threshold='standard')),
    DefaultEntry(role='owner', requirement=Requirement(capability='presence', threshold='standard')),
    DefaultEntry(role='owner', requirement=Requirement(capability='price-level', threshold='member')),
    DefaultEntry(role='owner', requirement=Requirement(capability='price-level', threshold='standard')),
    DefaultEntry(role='owner', requirement=Requirement(capability='profile', threshold='authenticated')),
    DefaultEntry(role='owner', requirement=Requirement(capability='profile', threshold='admin')),
    DefaultEntry(role='owner', requirement=Requirement(capability='reports', threshold='member')),
    DefaultEntry(role='owner', requirement=Requirement(capability='sales-rep', threshold='member')),
    DefaultEntry(role='owner', requirement=Requirement(capability='sales-rep', threshold='standard')),
    DefaultEntry(role='owner', requirement=Requirement(capability='sales-tax-code', threshold='member')),
    DefaultEntry(role='owner', requirement=Requirement(capability='sales-tax-code', threshold='standard')),
    DefaultEntry(role='owner', requirement=Requirement(capability='serve', threshold='authenticated')),
    DefaultEntry(role='owner', requirement=Requirement(capability='ship-method', threshold='member')),
    DefaultEntry(role='owner', requirement=Requirement(capability='ship-method', threshold='standard')),
    DefaultEntry(role='owner', requirement=Requirement(capability='term', threshold='member')),
    DefaultEntry(role='owner', requirement=Requirement(capability='term', threshold='standard')),
    DefaultEntry(role='owner', requirement=Requirement(capability='token', threshold='authenticated')),
    DefaultEntry(role='owner', requirement=Requirement(capability='undo', threshold='admin')),
    DefaultEntry(role='owner', requirement=Requirement(capability='unit-of-measure', threshold='member')),
    DefaultEntry(role='owner', requirement=Requirement(capability='unit-of-measure', threshold='standard')),
    DefaultEntry(role='owner', requirement=Requirement(capability='upgrade', threshold='authenticated')),
    DefaultEntry(role='owner', requirement=Requirement(capability='user', threshold='authenticated')),
    DefaultEntry(role='owner', requirement=Requirement(capability='vendor', threshold='member')),
    DefaultEntry(role='owner', requirement=Requirement(capability='vendor', threshold='standard')),
    DefaultEntry(role='owner', requirement=Requirement(capability='vendor-type', threshold='member')),
    DefaultEntry(role='owner', requirement=Requirement(capability='vendor-type', threshold='standard')),
    DefaultEntry(role='hub_admin', requirement=Requirement(capability='account', threshold='member')),
    DefaultEntry(role='hub_admin', requirement=Requirement(capability='account', threshold='standard')),
    DefaultEntry(role='hub_admin', requirement=Requirement(capability='activity', threshold='member')),
    DefaultEntry(role='hub_admin', requirement=Requirement(capability='attachment', threshold='member')),
    DefaultEntry(role='hub_admin', requirement=Requirement(capability='attachment', threshold='standard')),
    DefaultEntry(role='hub_admin', requirement=Requirement(capability='attachment', threshold='admin')),
    DefaultEntry(role='hub_admin', requirement=Requirement(capability='audit', threshold='member')),
    DefaultEntry(role='hub_admin', requirement=Requirement(capability='chart', threshold='authenticated')),
    DefaultEntry(role='hub_admin', requirement=Requirement(capability='chart', threshold='admin')),
    DefaultEntry(role='hub_admin', requirement=Requirement(capability='class', threshold='member')),
    DefaultEntry(role='hub_admin', requirement=Requirement(capability='class', threshold='standard')),
    DefaultEntry(role='hub_admin', requirement=Requirement(capability='company', threshold='authenticated')),
    DefaultEntry(role='hub_admin', requirement=Requirement(capability='company', threshold='member')),
    DefaultEntry(role='hub_admin', requirement=Requirement(capability='company', threshold='admin')),
    DefaultEntry(role='hub_admin', requirement=Requirement(capability='company', threshold='hub_admin')),
    DefaultEntry(role='hub_admin', requirement=Requirement(capability='custom-field', threshold='member')),
    DefaultEntry(role='hub_admin', requirement=Requirement(capability='custom-field', threshold='admin')),
    DefaultEntry(role='hub_admin', requirement=Requirement(capability='customer', threshold='member')),
    DefaultEntry(role='hub_admin', requirement=Requirement(capability='customer', threshold='standard')),
    DefaultEntry(role='hub_admin', requirement=Requirement(capability='customer-message', threshold='member')),
    DefaultEntry(role='hub_admin', requirement=Requirement(capability='customer-message', threshold='standard')),
    DefaultEntry(role='hub_admin', requirement=Requirement(capability='customer-type', threshold='member')),
    DefaultEntry(role='hub_admin', requirement=Requirement(capability='customer-type', threshold='standard')),
    DefaultEntry(role='hub_admin', requirement=Requirement(capability='customer-work', threshold='member')),
    DefaultEntry(role='hub_admin', requirement=Requirement(capability='customer-work', threshold='standard')),
    DefaultEntry(role='hub_admin', requirement=Requirement(capability='demo', threshold='hub_admin')),
    DefaultEntry(role='hub_admin', requirement=Requirement(capability='directive', threshold='member')),
    DefaultEntry(role='hub_admin', requirement=Requirement(capability='directive', threshold='standard')),
    DefaultEntry(role='hub_admin', requirement=Requirement(capability='employee', threshold='member')),
    DefaultEntry(role='hub_admin', requirement=Requirement(capability='employee', threshold='standard')),
    DefaultEntry(role='hub_admin', requirement=Requirement(capability='hub', threshold='authenticated')),
    DefaultEntry(role='hub_admin', requirement=Requirement(capability='init', threshold='authenticated')),
    DefaultEntry(role='hub_admin', requirement=Requirement(capability='item', threshold='member')),
    DefaultEntry(role='hub_admin', requirement=Requirement(capability='item', threshold='standard')),
    DefaultEntry(role='hub_admin', requirement=Requirement(capability='item-category', threshold='member')),
    DefaultEntry(role='hub_admin', requirement=Requirement(capability='item-category', threshold='standard')),
    DefaultEntry(role='hub_admin', requirement=Requirement(capability='job-type', threshold='member')),
    DefaultEntry(role='hub_admin', requirement=Requirement(capability='job-type', threshold='standard')),
    DefaultEntry(role='hub_admin', requirement=Requirement(capability='ledger.post', threshold='standard')),
    DefaultEntry(role='hub_admin', requirement=Requirement(capability='ledger.read', threshold='member')),
    DefaultEntry(role='hub_admin', requirement=Requirement(capability='note', threshold='member')),
    DefaultEntry(role='hub_admin', requirement=Requirement(capability='note', threshold='standard')),
    DefaultEntry(role='hub_admin', requirement=Requirement(capability='organization', threshold='authenticated')),
    DefaultEntry(role='hub_admin', requirement=Requirement(capability='organization', threshold='hub_admin')),
    DefaultEntry(role='hub_admin', requirement=Requirement(capability='other-name', threshold='member')),
    DefaultEntry(role='hub_admin', requirement=Requirement(capability='other-name', threshold='standard')),
    DefaultEntry(role='hub_admin', requirement=Requirement(capability='payment-method', threshold='member')),
    DefaultEntry(role='hub_admin', requirement=Requirement(capability='payment-method', threshold='standard')),
    DefaultEntry(role='hub_admin', requirement=Requirement(capability='presence', threshold='standard')),
    DefaultEntry(role='hub_admin', requirement=Requirement(capability='price-level', threshold='member')),
    DefaultEntry(role='hub_admin', requirement=Requirement(capability='price-level', threshold='standard')),
    DefaultEntry(role='hub_admin', requirement=Requirement(capability='profile', threshold='authenticated')),
    DefaultEntry(role='hub_admin', requirement=Requirement(capability='profile', threshold='admin')),
    DefaultEntry(role='hub_admin', requirement=Requirement(capability='reports', threshold='member')),
    DefaultEntry(role='hub_admin', requirement=Requirement(capability='sales-rep', threshold='member')),
    DefaultEntry(role='hub_admin', requirement=Requirement(capability='sales-rep', threshold='standard')),
    DefaultEntry(role='hub_admin', requirement=Requirement(capability='sales-tax-code', threshold='member')),
    DefaultEntry(role='hub_admin', requirement=Requirement(capability='sales-tax-code', threshold='standard')),
    DefaultEntry(role='hub_admin', requirement=Requirement(capability='serve', threshold='authenticated')),
    DefaultEntry(role='hub_admin', requirement=Requirement(capability='ship-method', threshold='member')),
    DefaultEntry(role='hub_admin', requirement=Requirement(capability='ship-method', threshold='standard')),
    DefaultEntry(role='hub_admin', requirement=Requirement(capability='term', threshold='member')),
    DefaultEntry(role='hub_admin', requirement=Requirement(capability='term', threshold='standard')),
    DefaultEntry(role='hub_admin', requirement=Requirement(capability='token', threshold='authenticated')),
    DefaultEntry(role='hub_admin', requirement=Requirement(capability='undo', threshold='admin')),
    DefaultEntry(role='hub_admin', requirement=Requirement(capability='unit-of-measure', threshold='member')),
    DefaultEntry(role='hub_admin', requirement=Requirement(capability='unit-of-measure', threshold='standard')),
    DefaultEntry(role='hub_admin', requirement=Requirement(capability='upgrade', threshold='authenticated')),
    DefaultEntry(role='hub_admin', requirement=Requirement(capability='user', threshold='authenticated')),
    DefaultEntry(role='hub_admin', requirement=Requirement(capability='vendor', threshold='member')),
    DefaultEntry(role='hub_admin', requirement=Requirement(capability='vendor', threshold='standard')),
    DefaultEntry(role='hub_admin', requirement=Requirement(capability='vendor-type', threshold='member')),
    DefaultEntry(role='hub_admin', requirement=Requirement(capability='vendor-type', threshold='standard')),
)


FROZEN_COMPANY_ACTIONS = (
    CompanyAction(key='account activate', requirements=(Requirement(capability='account', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.account_cmds._plan_active.<locals>.planner|src/bookflow/commands/account_cmds.py',)),
    CompanyAction(key='account create', requirements=(Requirement(capability='account', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.account_cmds.plan_account_create|src/bookflow/commands/account_cmds.py',)),
    CompanyAction(key='account deactivate', requirements=(Requirement(capability='account', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.account_cmds._plan_active.<locals>.planner|src/bookflow/commands/account_cmds.py',)),
    CompanyAction(key='account list', requirements=(Requirement(capability='account', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.account_cmds.plan_account_list|src/bookflow/commands/account_cmds.py',)),
    CompanyAction(key='account query', requirements=(Requirement(capability='account', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.query_cmds._register.<locals>.plan|src/bookflow/commands/query_cmds.py',)),
    CompanyAction(key='account query options', requirements=(Requirement(capability='account', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.query_cmds._register.<locals>.discover|src/bookflow/commands/query_cmds.py',)),
    CompanyAction(key='account show', requirements=(Requirement(capability='account', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.account_cmds.plan_account_show|src/bookflow/commands/account_cmds.py',)),
    CompanyAction(key='account update', requirements=(Requirement(capability='account', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.account_cmds.plan_account_update|src/bookflow/commands/account_cmds.py',)),
    CompanyAction(key='activity', requirements=(Requirement(capability='activity', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.activity_cmds.plan_activity|src/bookflow/commands/activity_cmds.py',)),
    CompanyAction(key='application history', requirements=(Requirement(capability='ledger.read', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.payment_cmds.application_history|src/bookflow/commands/payment_cmds.py',)),
    CompanyAction(key='application show', requirements=(Requirement(capability='ledger.read', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.payment_cmds.application_show|src/bookflow/commands/payment_cmds.py',)),
    CompanyAction(key='attachment add', requirements=(Requirement(capability='attachment', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.attachment_cmds.plan_add|src/bookflow/commands/attachment_cmds.py', 'bookflow.commands.attachment_cmds.prepare_add|src/bookflow/commands/attachment_cmds.py')),
    CompanyAction(key='attachment get', requirements=(Requirement(capability='attachment', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.attachment_cmds.plan_get|src/bookflow/commands/attachment_cmds.py', 'bookflow.commands.attachment_cmds.prepare_get|src/bookflow/commands/attachment_cmds.py')),
    CompanyAction(key='attachment link', requirements=(Requirement(capability='attachment', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.attachment_cmds.plan_link|src/bookflow/commands/attachment_cmds.py',)),
    CompanyAction(key='attachment list', requirements=(Requirement(capability='attachment', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.attachment_cmds.plan_list|src/bookflow/commands/attachment_cmds.py',)),
    CompanyAction(key='attachment unlink', requirements=(Requirement(capability='attachment', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.attachment_cmds.plan_unlink|src/bookflow/commands/attachment_cmds.py',)),
    CompanyAction(key='audit list', requirements=(Requirement(capability='audit', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.audit_cmds._mk.<locals>.plan_list|src/bookflow/commands/audit_cmds.py',)),
    CompanyAction(key='audit show', requirements=(Requirement(capability='audit', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.audit_cmds._mk.<locals>.plan_show|src/bookflow/commands/audit_cmds.py',)),
    CompanyAction(key='audit tail', requirements=(Requirement(capability='audit', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.audit_cmds._mk.<locals>.plan_tail|src/bookflow/commands/audit_cmds.py',)),
    CompanyAction(key='chart apply', requirements=(Requirement(capability='chart', threshold='admin'),), available=True, remaining_graph_owners=('bookflow.commands.chart_cmds.plan_chart_apply|src/bookflow/commands/chart_cmds.py',)),
    CompanyAction(key='class activate', requirements=(Requirement(capability='class', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.profile_cmds._register_supporting_noun.<locals>.active_plan.<locals>.planner|src/bookflow/commands/profile_cmds.py',)),
    CompanyAction(key='class create', requirements=(Requirement(capability='class', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.profile_cmds._register_supporting_noun.<locals>.create_plan|src/bookflow/commands/profile_cmds.py',)),
    CompanyAction(key='class deactivate', requirements=(Requirement(capability='class', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.profile_cmds._register_supporting_noun.<locals>.active_plan.<locals>.planner|src/bookflow/commands/profile_cmds.py',)),
    CompanyAction(key='class list', requirements=(Requirement(capability='class', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.profile_cmds._register_supporting_noun.<locals>.list_plan|src/bookflow/commands/profile_cmds.py',)),
    CompanyAction(key='class query', requirements=(Requirement(capability='class', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.query_cmds._register.<locals>.plan|src/bookflow/commands/query_cmds.py',)),
    CompanyAction(key='class query options', requirements=(Requirement(capability='class', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.query_cmds._register.<locals>.discover|src/bookflow/commands/query_cmds.py',)),
    CompanyAction(key='class show', requirements=(Requirement(capability='class', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.profile_cmds._register_supporting_noun.<locals>.show_plan|src/bookflow/commands/profile_cmds.py',)),
    CompanyAction(key='class update', requirements=(Requirement(capability='class', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.profile_cmds._register_supporting_noun.<locals>.update_plan|src/bookflow/commands/profile_cmds.py',)),
    CompanyAction(key='company compact', requirements=(Requirement(capability='attachment', threshold='admin'),), available=True, remaining_graph_owners=('bookflow.commands.compact_cmds.plan_company_compact|src/bookflow/commands/compact_cmds.py',)),
    CompanyAction(key='company rename', requirements=(Requirement(capability='company', threshold='admin'),), available=True, remaining_graph_owners=('bookflow.commands.company_cmds.plan_company_rename|src/bookflow/commands/company_cmds.py',)),
    CompanyAction(key='company show', requirements=(Requirement(capability='company', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.company_cmds.plan_company_show|src/bookflow/commands/company_cmds.py',)),
    CompanyAction(key='company update', requirements=(Requirement(capability='company', threshold='admin'),), available=True, remaining_graph_owners=('bookflow.commands.company_cmds.plan_company_update|src/bookflow/commands/company_cmds.py',)),
    CompanyAction(key='contract:delete:invoice', requirements=(Requirement(capability='ledger.read', threshold='member'), Requirement(capability='transaction.invoice.delete', threshold='standard')), available=False, remaining_graph_owners=('design/permission-resolution.md#delete-disclosure', 'design/transaction-deletion.md')),
    CompanyAction(key='contract:delete:journal_entry', requirements=(Requirement(capability='ledger.read', threshold='member'), Requirement(capability='transaction.journal_entry.delete', threshold='standard')), available=False, remaining_graph_owners=('design/permission-resolution.md#delete-disclosure', 'design/transaction-deletion.md')),
    CompanyAction(key='contract:delete:payment', requirements=(Requirement(capability='ledger.read', threshold='member'), Requirement(capability='transaction.payment.delete', threshold='standard')), available=False, remaining_graph_owners=('design/permission-resolution.md#delete-disclosure', 'design/transaction-deletion.md')),
    CompanyAction(key='contract:delete:sales_receipt', requirements=(Requirement(capability='ledger.read', threshold='member'), Requirement(capability='transaction.sales_receipt.delete', threshold='standard')), available=False, remaining_graph_owners=('design/permission-resolution.md#delete-disclosure', 'design/transaction-deletion.md')),
    CompanyAction(key='custom-field activate', requirements=(Requirement(capability='custom-field', threshold='admin'),), available=True, remaining_graph_owners=('bookflow.commands.custom_field_cmds._active_planner.<locals>.planner|src/bookflow/commands/custom_field_cmds.py',)),
    CompanyAction(key='custom-field create', requirements=(Requirement(capability='custom-field', threshold='admin'),), available=True, remaining_graph_owners=('bookflow.commands.custom_field_cmds.plan_create|src/bookflow/commands/custom_field_cmds.py',)),
    CompanyAction(key='custom-field deactivate', requirements=(Requirement(capability='custom-field', threshold='admin'),), available=True, remaining_graph_owners=('bookflow.commands.custom_field_cmds._active_planner.<locals>.planner|src/bookflow/commands/custom_field_cmds.py',)),
    CompanyAction(key='custom-field list', requirements=(Requirement(capability='custom-field', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.custom_field_cmds.plan_list|src/bookflow/commands/custom_field_cmds.py',)),
    CompanyAction(key='custom-field query', requirements=(Requirement(capability='custom-field', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.query_cmds._register.<locals>.plan|src/bookflow/commands/query_cmds.py',)),
    CompanyAction(key='custom-field query children', requirements=(Requirement(capability='custom-field', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.query_cmds._register.<locals>.child_page|src/bookflow/commands/query_cmds.py',)),
    CompanyAction(key='custom-field query options', requirements=(Requirement(capability='custom-field', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.query_cmds._register.<locals>.discover|src/bookflow/commands/query_cmds.py',)),
    CompanyAction(key='custom-field show', requirements=(Requirement(capability='custom-field', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.custom_field_cmds.plan_show|src/bookflow/commands/custom_field_cmds.py',)),
    CompanyAction(key='custom-field update', requirements=(Requirement(capability='custom-field', threshold='admin'),), available=True, remaining_graph_owners=('bookflow.commands.custom_field_cmds.plan_update|src/bookflow/commands/custom_field_cmds.py',)),
    CompanyAction(key='customer activate', requirements=(Requirement(capability='customer', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.party_cmds._register_party.<locals>.active_plan.<locals>.planner|src/bookflow/commands/party_cmds.py',)),
    CompanyAction(key='customer create', requirements=(Requirement(capability='customer', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.party_cmds._register_party.<locals>.create_plan|src/bookflow/commands/party_cmds.py',)),
    CompanyAction(key='customer deactivate', requirements=(Requirement(capability='customer', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.party_cmds._register_party.<locals>.active_plan.<locals>.planner|src/bookflow/commands/party_cmds.py',)),
    CompanyAction(key='customer link-vendor', requirements=(Requirement(capability='customer', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.party_cmds.plan_customer_link_vendor|src/bookflow/commands/party_cmds.py',)),
    CompanyAction(key='customer list', requirements=(Requirement(capability='customer', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.party_cmds._register_party.<locals>.list_plan|src/bookflow/commands/party_cmds.py',)),
    CompanyAction(key='customer query', requirements=(Requirement(capability='customer', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.query_cmds._register.<locals>.plan|src/bookflow/commands/query_cmds.py',)),
    CompanyAction(key='customer query options', requirements=(Requirement(capability='customer', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.query_cmds._register.<locals>.discover|src/bookflow/commands/query_cmds.py',)),
    CompanyAction(key='customer show', requirements=(Requirement(capability='customer', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.party_cmds._register_party.<locals>.show_plan|src/bookflow/commands/party_cmds.py',)),
    CompanyAction(key='customer unlink-vendor', requirements=(Requirement(capability='customer', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.party_cmds.plan_customer_unlink_vendor|src/bookflow/commands/party_cmds.py',)),
    CompanyAction(key='customer update', requirements=(Requirement(capability='customer', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.party_cmds._register_party.<locals>.update_plan|src/bookflow/commands/party_cmds.py',)),
    CompanyAction(key='customer-message activate', requirements=(Requirement(capability='customer-message', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.profile_cmds._register_supporting_noun.<locals>.active_plan.<locals>.planner|src/bookflow/commands/profile_cmds.py',)),
    CompanyAction(key='customer-message create', requirements=(Requirement(capability='customer-message', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.profile_cmds._register_supporting_noun.<locals>.create_plan|src/bookflow/commands/profile_cmds.py',)),
    CompanyAction(key='customer-message deactivate', requirements=(Requirement(capability='customer-message', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.profile_cmds._register_supporting_noun.<locals>.active_plan.<locals>.planner|src/bookflow/commands/profile_cmds.py',)),
    CompanyAction(key='customer-message list', requirements=(Requirement(capability='customer-message', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.profile_cmds._register_supporting_noun.<locals>.list_plan|src/bookflow/commands/profile_cmds.py',)),
    CompanyAction(key='customer-message query', requirements=(Requirement(capability='customer-message', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.query_cmds._register.<locals>.plan|src/bookflow/commands/query_cmds.py',)),
    CompanyAction(key='customer-message query options', requirements=(Requirement(capability='customer-message', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.query_cmds._register.<locals>.discover|src/bookflow/commands/query_cmds.py',)),
    CompanyAction(key='customer-message show', requirements=(Requirement(capability='customer-message', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.profile_cmds._register_supporting_noun.<locals>.show_plan|src/bookflow/commands/profile_cmds.py',)),
    CompanyAction(key='customer-message update', requirements=(Requirement(capability='customer-message', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.profile_cmds._register_supporting_noun.<locals>.update_plan|src/bookflow/commands/profile_cmds.py',)),
    CompanyAction(key='customer-type activate', requirements=(Requirement(capability='customer-type', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.profile_cmds._register_supporting_noun.<locals>.active_plan.<locals>.planner|src/bookflow/commands/profile_cmds.py',)),
    CompanyAction(key='customer-type create', requirements=(Requirement(capability='customer-type', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.profile_cmds._register_supporting_noun.<locals>.create_plan|src/bookflow/commands/profile_cmds.py',)),
    CompanyAction(key='customer-type deactivate', requirements=(Requirement(capability='customer-type', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.profile_cmds._register_supporting_noun.<locals>.active_plan.<locals>.planner|src/bookflow/commands/profile_cmds.py',)),
    CompanyAction(key='customer-type list', requirements=(Requirement(capability='customer-type', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.profile_cmds._register_supporting_noun.<locals>.list_plan|src/bookflow/commands/profile_cmds.py',)),
    CompanyAction(key='customer-type query', requirements=(Requirement(capability='customer-type', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.query_cmds._register.<locals>.plan|src/bookflow/commands/query_cmds.py',)),
    CompanyAction(key='customer-type query options', requirements=(Requirement(capability='customer-type', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.query_cmds._register.<locals>.discover|src/bookflow/commands/query_cmds.py',)),
    CompanyAction(key='customer-type show', requirements=(Requirement(capability='customer-type', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.profile_cmds._register_supporting_noun.<locals>.show_plan|src/bookflow/commands/profile_cmds.py',)),
    CompanyAction(key='customer-type update', requirements=(Requirement(capability='customer-type', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.profile_cmds._register_supporting_noun.<locals>.update_plan|src/bookflow/commands/profile_cmds.py',)),
    CompanyAction(key='directive add', requirements=(Requirement(capability='directive', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.company_cmds.plan_directive_add|src/bookflow/commands/company_cmds.py',)),
    CompanyAction(key='directive deactivate', requirements=(Requirement(capability='directive', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.company_cmds.plan_directive_deactivate|src/bookflow/commands/company_cmds.py',)),
    CompanyAction(key='directive list', requirements=(Requirement(capability='directive', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.company_cmds.plan_directive_list|src/bookflow/commands/company_cmds.py',)),
    CompanyAction(key='directive show', requirements=(Requirement(capability='directive', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.company_cmds.plan_directive_show|src/bookflow/commands/company_cmds.py',)),
    CompanyAction(key='employee activate', requirements=(Requirement(capability='employee', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.party_cmds._register_party.<locals>.active_plan.<locals>.planner|src/bookflow/commands/party_cmds.py',)),
    CompanyAction(key='employee create', requirements=(Requirement(capability='employee', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.party_cmds._register_party.<locals>.create_plan|src/bookflow/commands/party_cmds.py',)),
    CompanyAction(key='employee deactivate', requirements=(Requirement(capability='employee', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.party_cmds._register_party.<locals>.active_plan.<locals>.planner|src/bookflow/commands/party_cmds.py',)),
    CompanyAction(key='employee list', requirements=(Requirement(capability='employee', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.party_cmds._register_party.<locals>.list_plan|src/bookflow/commands/party_cmds.py',)),
    CompanyAction(key='employee query', requirements=(Requirement(capability='employee', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.query_cmds._register.<locals>.plan|src/bookflow/commands/query_cmds.py',)),
    CompanyAction(key='employee query options', requirements=(Requirement(capability='employee', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.query_cmds._register.<locals>.discover|src/bookflow/commands/query_cmds.py',)),
    CompanyAction(key='employee show', requirements=(Requirement(capability='employee', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.party_cmds._register_party.<locals>.show_plan|src/bookflow/commands/party_cmds.py',)),
    CompanyAction(key='employee update', requirements=(Requirement(capability='employee', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.party_cmds._register_party.<locals>.update_plan|src/bookflow/commands/party_cmds.py',)),
    CompanyAction(key='estimate billing', requirements=(Requirement(capability='customer-work', threshold='member'), Requirement(capability='ledger.read', threshold='member')), available=True, remaining_graph_owners=('bookflow.commands.billing_cmds.register.<locals>.planner|src/bookflow/commands/billing_cmds.py',)),
    CompanyAction(key='estimate copy', requirements=(Requirement(capability='customer-work', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.work_cmds._register.<locals>.planner|src/bookflow/commands/work_cmds.py',)),
    CompanyAction(key='estimate create', requirements=(Requirement(capability='customer-work', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.work_cmds._register.<locals>.planner|src/bookflow/commands/work_cmds.py',)),
    CompanyAction(key='estimate history', requirements=(Requirement(capability='customer-work', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.work_cmds._register.<locals>.planner|src/bookflow/commands/work_cmds.py',)),
    CompanyAction(key='estimate invoice', requirements=(Requirement(capability='customer-work', threshold='standard'), Requirement(capability='ledger.post', threshold='standard')), available=True, remaining_graph_owners=('bookflow.commands.billing_cmds.register.<locals>.planner|src/bookflow/commands/billing_cmds.py',)),
    CompanyAction(key='estimate query', requirements=(Requirement(capability='customer-work', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.work_cmds._register.<locals>.planner|src/bookflow/commands/work_cmds.py',)),
    CompanyAction(key='estimate sales-receipt', requirements=(Requirement(capability='customer-work', threshold='standard'), Requirement(capability='ledger.post', threshold='standard')), available=True, remaining_graph_owners=('bookflow.commands.billing_cmds.register.<locals>.planner|src/bookflow/commands/billing_cmds.py',)),
    CompanyAction(key='estimate show', requirements=(Requirement(capability='customer-work', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.work_cmds._register.<locals>.planner|src/bookflow/commands/work_cmds.py',)),
    CompanyAction(key='estimate update', requirements=(Requirement(capability='customer-work', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.work_cmds._register.<locals>.planner|src/bookflow/commands/work_cmds.py',)),
    CompanyAction(key='estimate work-order', requirements=(Requirement(capability='customer-work', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.work_cmds._register.<locals>.planner|src/bookflow/commands/work_cmds.py',)),
    CompanyAction(key='invoice history', requirements=(Requirement(capability='ledger.read', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.sales_cmds._read.<locals>.<lambda>|src/bookflow/commands/sales_cmds.py', 'bookflow.commands.sales_cmds._read.<locals>.planner|src/bookflow/commands/sales_cmds.py')),
    CompanyAction(key='invoice post', requirements=(Requirement(capability='ledger.post', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.sales_cmds._write.<locals>.planner|src/bookflow/commands/sales_cmds.py',)),
    CompanyAction(key='invoice query', requirements=(Requirement(capability='ledger.read', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.sales_cmds._read.<locals>.planner|src/bookflow/commands/sales_cmds.py',)),
    CompanyAction(key='invoice settlement', requirements=(Requirement(capability='ledger.read', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.payment_cmds.invoice_settlement|src/bookflow/commands/payment_cmds.py',)),
    CompanyAction(key='invoice show', requirements=(Requirement(capability='ledger.read', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.sales_cmds._read.<locals>.<lambda>|src/bookflow/commands/sales_cmds.py', 'bookflow.commands.sales_cmds._read.<locals>.planner|src/bookflow/commands/sales_cmds.py')),
    CompanyAction(key='invoice update', requirements=(Requirement(capability='ledger.post', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.sales_cmds._write.<locals>.<lambda>|src/bookflow/commands/sales_cmds.py', 'bookflow.commands.sales_cmds._write.<locals>.planner|src/bookflow/commands/sales_cmds.py')),
    CompanyAction(key='invoice void', requirements=(Requirement(capability='ledger.post', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.sales_cmds._write.<locals>.<lambda>|src/bookflow/commands/sales_cmds.py', 'bookflow.commands.sales_cmds._write.<locals>.planner|src/bookflow/commands/sales_cmds.py')),
    CompanyAction(key='item activate', requirements=(Requirement(capability='item', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.item_cmds._active_plan.<locals>.planner|src/bookflow/commands/item_cmds.py',)),
    CompanyAction(key='item create', requirements=(Requirement(capability='item', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.item_cmds.plan_item_create|src/bookflow/commands/item_cmds.py',)),
    CompanyAction(key='item deactivate', requirements=(Requirement(capability='item', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.item_cmds._active_plan.<locals>.planner|src/bookflow/commands/item_cmds.py',)),
    CompanyAction(key='item list', requirements=(Requirement(capability='item', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.item_cmds.plan_item_list|src/bookflow/commands/item_cmds.py',)),
    CompanyAction(key='item query', requirements=(Requirement(capability='item', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.query_cmds._register.<locals>.plan|src/bookflow/commands/query_cmds.py',)),
    CompanyAction(key='item query children', requirements=(Requirement(capability='item', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.query_cmds._register.<locals>.child_page|src/bookflow/commands/query_cmds.py',)),
    CompanyAction(key='item query options', requirements=(Requirement(capability='item', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.query_cmds._register.<locals>.discover|src/bookflow/commands/query_cmds.py',)),
    CompanyAction(key='item show', requirements=(Requirement(capability='item', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.item_cmds.plan_item_show|src/bookflow/commands/item_cmds.py',)),
    CompanyAction(key='item update', requirements=(Requirement(capability='item', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.item_cmds.plan_item_update|src/bookflow/commands/item_cmds.py',)),
    CompanyAction(key='item-category activate', requirements=(Requirement(capability='item-category', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.profile_cmds._register_supporting_noun.<locals>.active_plan.<locals>.planner|src/bookflow/commands/profile_cmds.py',)),
    CompanyAction(key='item-category create', requirements=(Requirement(capability='item-category', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.profile_cmds._register_supporting_noun.<locals>.create_plan|src/bookflow/commands/profile_cmds.py',)),
    CompanyAction(key='item-category deactivate', requirements=(Requirement(capability='item-category', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.profile_cmds._register_supporting_noun.<locals>.active_plan.<locals>.planner|src/bookflow/commands/profile_cmds.py',)),
    CompanyAction(key='item-category list', requirements=(Requirement(capability='item-category', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.profile_cmds._register_supporting_noun.<locals>.list_plan|src/bookflow/commands/profile_cmds.py',)),
    CompanyAction(key='item-category query', requirements=(Requirement(capability='item-category', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.query_cmds._register.<locals>.plan|src/bookflow/commands/query_cmds.py',)),
    CompanyAction(key='item-category query options', requirements=(Requirement(capability='item-category', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.query_cmds._register.<locals>.discover|src/bookflow/commands/query_cmds.py',)),
    CompanyAction(key='item-category show', requirements=(Requirement(capability='item-category', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.profile_cmds._register_supporting_noun.<locals>.show_plan|src/bookflow/commands/profile_cmds.py',)),
    CompanyAction(key='item-category update', requirements=(Requirement(capability='item-category', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.profile_cmds._register_supporting_noun.<locals>.update_plan|src/bookflow/commands/profile_cmds.py',)),
    CompanyAction(key='job-type activate', requirements=(Requirement(capability='job-type', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.profile_cmds._register_supporting_noun.<locals>.active_plan.<locals>.planner|src/bookflow/commands/profile_cmds.py',)),
    CompanyAction(key='job-type create', requirements=(Requirement(capability='job-type', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.profile_cmds._register_supporting_noun.<locals>.create_plan|src/bookflow/commands/profile_cmds.py',)),
    CompanyAction(key='job-type deactivate', requirements=(Requirement(capability='job-type', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.profile_cmds._register_supporting_noun.<locals>.active_plan.<locals>.planner|src/bookflow/commands/profile_cmds.py',)),
    CompanyAction(key='job-type list', requirements=(Requirement(capability='job-type', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.profile_cmds._register_supporting_noun.<locals>.list_plan|src/bookflow/commands/profile_cmds.py',)),
    CompanyAction(key='job-type query', requirements=(Requirement(capability='job-type', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.query_cmds._register.<locals>.plan|src/bookflow/commands/query_cmds.py',)),
    CompanyAction(key='job-type query options', requirements=(Requirement(capability='job-type', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.query_cmds._register.<locals>.discover|src/bookflow/commands/query_cmds.py',)),
    CompanyAction(key='job-type show', requirements=(Requirement(capability='job-type', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.profile_cmds._register_supporting_noun.<locals>.show_plan|src/bookflow/commands/profile_cmds.py',)),
    CompanyAction(key='job-type update', requirements=(Requirement(capability='job-type', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.profile_cmds._register_supporting_noun.<locals>.update_plan|src/bookflow/commands/profile_cmds.py',)),
    CompanyAction(key='journal history', requirements=(Requirement(capability='ledger.read', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.journal_cmds.journal_history|src/bookflow/commands/journal_cmds.py',)),
    CompanyAction(key='journal post', requirements=(Requirement(capability='ledger.post', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.journal_cmds._write.<locals>.planner|src/bookflow/commands/journal_cmds.py',)),
    CompanyAction(key='journal query', requirements=(Requirement(capability='ledger.read', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.journal_cmds.journal_query|src/bookflow/commands/journal_cmds.py',)),
    CompanyAction(key='journal show', requirements=(Requirement(capability='ledger.read', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.journal_cmds.journal_show|src/bookflow/commands/journal_cmds.py',)),
    CompanyAction(key='journal update', requirements=(Requirement(capability='ledger.post', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.journal_cmds._write.<locals>.planner|src/bookflow/commands/journal_cmds.py',)),
    CompanyAction(key='journal void', requirements=(Requirement(capability='ledger.post', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.journal_cmds._write.<locals>.planner|src/bookflow/commands/journal_cmds.py',)),
    CompanyAction(key='note add', requirements=(Requirement(capability='note', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.note_cmds.plan_add|src/bookflow/commands/note_cmds.py',)),
    CompanyAction(key='note edit', requirements=(Requirement(capability='note', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.note_cmds.plan_edit|src/bookflow/commands/note_cmds.py',)),
    CompanyAction(key='note list', requirements=(Requirement(capability='note', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.note_cmds.plan_list|src/bookflow/commands/note_cmds.py',)),
    CompanyAction(key='note show', requirements=(Requirement(capability='note', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.note_cmds.plan_show|src/bookflow/commands/note_cmds.py',)),
    CompanyAction(key='other-name activate', requirements=(Requirement(capability='other-name', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.party_cmds._register_party.<locals>.active_plan.<locals>.planner|src/bookflow/commands/party_cmds.py',)),
    CompanyAction(key='other-name convert', requirements=(Requirement(capability='other-name', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.party_cmds.plan_other_name_convert|src/bookflow/commands/party_cmds.py',)),
    CompanyAction(key='other-name create', requirements=(Requirement(capability='other-name', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.party_cmds._register_party.<locals>.create_plan|src/bookflow/commands/party_cmds.py',)),
    CompanyAction(key='other-name deactivate', requirements=(Requirement(capability='other-name', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.party_cmds._register_party.<locals>.active_plan.<locals>.planner|src/bookflow/commands/party_cmds.py',)),
    CompanyAction(key='other-name list', requirements=(Requirement(capability='other-name', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.party_cmds._register_party.<locals>.list_plan|src/bookflow/commands/party_cmds.py',)),
    CompanyAction(key='other-name query', requirements=(Requirement(capability='other-name', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.query_cmds._register.<locals>.plan|src/bookflow/commands/query_cmds.py',)),
    CompanyAction(key='other-name query options', requirements=(Requirement(capability='other-name', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.query_cmds._register.<locals>.discover|src/bookflow/commands/query_cmds.py',)),
    CompanyAction(key='other-name show', requirements=(Requirement(capability='other-name', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.party_cmds._register_party.<locals>.show_plan|src/bookflow/commands/party_cmds.py',)),
    CompanyAction(key='other-name update', requirements=(Requirement(capability='other-name', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.party_cmds._register_party.<locals>.update_plan|src/bookflow/commands/party_cmds.py',)),
    CompanyAction(key='payment apply', requirements=(Requirement(capability='ledger.post', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.payment_cmds._financial.<locals>.<lambda>|src/bookflow/commands/payment_cmds.py', 'bookflow.commands.payment_cmds._financial.<locals>.planner|src/bookflow/commands/payment_cmds.py')),
    CompanyAction(key='payment calculate', requirements=(Requirement(capability='ledger.read', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.payment_cmds._preparation.<locals>.plan|src/bookflow/commands/payment_cmds.py',)),
    CompanyAction(key='payment history', requirements=(Requirement(capability='ledger.read', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.payment_cmds.payment_history_read|src/bookflow/commands/payment_cmds.py',)),
    CompanyAction(key='payment invoices', requirements=(Requirement(capability='ledger.read', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.payment_cmds._preparation.<locals>.plan|src/bookflow/commands/payment_cmds.py',)),
    CompanyAction(key='payment operation items', requirements=(Requirement(capability='ledger.read', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.payment_cmds.payment_operation_items|src/bookflow/commands/payment_cmds.py',)),
    CompanyAction(key='payment operation show', requirements=(Requirement(capability='ledger.read', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.payment_cmds.payment_operation_show|src/bookflow/commands/payment_cmds.py',)),
    CompanyAction(key='payment preview items', requirements=(Requirement(capability='ledger.post', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.payment_cmds.payment_preview_items|src/bookflow/commands/payment_cmds.py',)),
    CompanyAction(key='payment query', requirements=(Requirement(capability='ledger.read', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.payment_cmds._preparation.<locals>.plan|src/bookflow/commands/payment_cmds.py',)),
    CompanyAction(key='payment receive', requirements=(Requirement(capability='ledger.post', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.payment_cmds._financial.<locals>.<lambda>|src/bookflow/commands/payment_cmds.py', 'bookflow.commands.payment_cmds._financial.<locals>.planner|src/bookflow/commands/payment_cmds.py')),
    CompanyAction(key='payment recovery abort', requirements=(Requirement(capability='ledger.post', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.payment_recovery_cmds.write.<locals>.<lambda>|src/bookflow/commands/payment_recovery_cmds.py', 'bookflow.commands.payment_recovery_cmds.write.<locals>.planner|src/bookflow/commands/payment_recovery_cmds.py', 'bookflow.company.payment_recovery.authorize_input|src/bookflow/company/payment_recovery.py')),
    CompanyAction(key='payment recovery apply', requirements=(Requirement(capability='ledger.post', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.payment_recovery_cmds.write.<locals>.<lambda>|src/bookflow/commands/payment_recovery_cmds.py', 'bookflow.commands.payment_recovery_cmds.write.<locals>.planner|src/bookflow/commands/payment_recovery_cmds.py', 'bookflow.company.payment_recovery.authorize_input|src/bookflow/company/payment_recovery.py')),
    CompanyAction(key='payment recovery begin', requirements=(Requirement(capability='ledger.post', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.payment_recovery_cmds.write.<locals>.<lambda>|src/bookflow/commands/payment_recovery_cmds.py', 'bookflow.commands.payment_recovery_cmds.write.<locals>.planner|src/bookflow/commands/payment_recovery_cmds.py', 'bookflow.company.payment_recovery.authorize_input|src/bookflow/company/payment_recovery.py')),
    CompanyAction(key='payment recovery compare', requirements=(Requirement(capability='ledger.read', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.payment_recovery_cmds.compare|src/bookflow/commands/payment_recovery_cmds.py',)),
    CompanyAction(key='payment recovery compare-items', requirements=(Requirement(capability='ledger.read', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.payment_recovery_cmds.compare_items|src/bookflow/commands/payment_recovery_cmds.py',)),
    CompanyAction(key='payment recovery items', requirements=(Requirement(capability='ledger.read', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.payment_recovery_cmds.items|src/bookflow/commands/payment_recovery_cmds.py',)),
    CompanyAction(key='payment recovery query', requirements=(Requirement(capability='ledger.read', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.payment_recovery_cmds.query|src/bookflow/commands/payment_recovery_cmds.py',)),
    CompanyAction(key='payment recovery replace', requirements=(Requirement(capability='ledger.post', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.payment_recovery_cmds.write.<locals>.<lambda>|src/bookflow/commands/payment_recovery_cmds.py', 'bookflow.commands.payment_recovery_cmds.write.<locals>.planner|src/bookflow/commands/payment_recovery_cmds.py', 'bookflow.company.payment_recovery.authorize_input|src/bookflow/company/payment_recovery.py')),
    CompanyAction(key='payment recovery seal', requirements=(Requirement(capability='ledger.post', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.payment_recovery_cmds.write.<locals>.<lambda>|src/bookflow/commands/payment_recovery_cmds.py', 'bookflow.commands.payment_recovery_cmds.write.<locals>.planner|src/bookflow/commands/payment_recovery_cmds.py', 'bookflow.company.payment_recovery.authorize_input|src/bookflow/company/payment_recovery.py')),
    CompanyAction(key='payment recovery show', requirements=(Requirement(capability='ledger.read', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.payment_recovery_cmds.show|src/bookflow/commands/payment_recovery_cmds.py',)),
    CompanyAction(key='payment recovery upload', requirements=(Requirement(capability='ledger.post', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.payment_recovery_cmds.write.<locals>.<lambda>|src/bookflow/commands/payment_recovery_cmds.py', 'bookflow.commands.payment_recovery_cmds.write.<locals>.planner|src/bookflow/commands/payment_recovery_cmds.py', 'bookflow.company.payment_recovery.authorize_input|src/bookflow/company/payment_recovery.py')),
    CompanyAction(key='payment selection clear', requirements=(Requirement(capability='ledger.post', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.payment_cmds._write.<locals>.plan|src/bookflow/commands/payment_cmds.py', 'bookflow.company.payment_selection.authorize_input|src/bookflow/company/payment_selection.py')),
    CompanyAction(key='payment selection create', requirements=(Requirement(capability='ledger.post', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.payment_cmds._write.<locals>.plan|src/bookflow/commands/payment_cmds.py', 'bookflow.company.payment_selection.authorize_input|src/bookflow/company/payment_selection.py')),
    CompanyAction(key='payment selection items', requirements=(Requirement(capability='ledger.read', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.payment_cmds.selection_items|src/bookflow/commands/payment_cmds.py',)),
    CompanyAction(key='payment selection query', requirements=(Requirement(capability='ledger.read', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.payment_cmds.selection_query|src/bookflow/commands/payment_cmds.py',)),
    CompanyAction(key='payment selection show', requirements=(Requirement(capability='ledger.read', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.payment_cmds.selection_show|src/bookflow/commands/payment_cmds.py',)),
    CompanyAction(key='payment selection update', requirements=(Requirement(capability='ledger.post', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.payment_cmds._write.<locals>.plan|src/bookflow/commands/payment_cmds.py', 'bookflow.company.payment_selection.authorize_input|src/bookflow/company/payment_selection.py')),
    CompanyAction(key='payment settlement', requirements=(Requirement(capability='ledger.read', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.payment_cmds.payment_settlement|src/bookflow/commands/payment_cmds.py',)),
    CompanyAction(key='payment settlement changes', requirements=(Requirement(capability='ledger.read', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.payment_cmds.settlement_changes|src/bookflow/commands/payment_cmds.py',)),
    CompanyAction(key='payment show', requirements=(Requirement(capability='ledger.read', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.payment_cmds.payment_show|src/bookflow/commands/payment_cmds.py',)),
    CompanyAction(key='payment suggest', requirements=(Requirement(capability='ledger.read', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.payment_cmds._preparation.<locals>.plan|src/bookflow/commands/payment_cmds.py',)),
    CompanyAction(key='payment unapply', requirements=(Requirement(capability='ledger.post', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.payment_cmds._financial.<locals>.<lambda>|src/bookflow/commands/payment_cmds.py', 'bookflow.commands.payment_cmds._financial.<locals>.planner|src/bookflow/commands/payment_cmds.py')),
    CompanyAction(key='payment update', requirements=(Requirement(capability='ledger.post', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.payment_cmds._financial.<locals>.<lambda>|src/bookflow/commands/payment_cmds.py', 'bookflow.commands.payment_cmds._financial.<locals>.planner|src/bookflow/commands/payment_cmds.py')),
    CompanyAction(key='payment void', requirements=(Requirement(capability='ledger.post', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.payment_cmds._financial.<locals>.<lambda>|src/bookflow/commands/payment_cmds.py', 'bookflow.commands.payment_cmds._financial.<locals>.planner|src/bookflow/commands/payment_cmds.py')),
    CompanyAction(key='payment-method activate', requirements=(Requirement(capability='payment-method', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.profile_cmds._register_supporting_noun.<locals>.active_plan.<locals>.planner|src/bookflow/commands/profile_cmds.py',)),
    CompanyAction(key='payment-method create', requirements=(Requirement(capability='payment-method', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.profile_cmds._register_supporting_noun.<locals>.create_plan|src/bookflow/commands/profile_cmds.py',)),
    CompanyAction(key='payment-method deactivate', requirements=(Requirement(capability='payment-method', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.profile_cmds._register_supporting_noun.<locals>.active_plan.<locals>.planner|src/bookflow/commands/profile_cmds.py',)),
    CompanyAction(key='payment-method list', requirements=(Requirement(capability='payment-method', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.profile_cmds._register_supporting_noun.<locals>.list_plan|src/bookflow/commands/profile_cmds.py',)),
    CompanyAction(key='payment-method query', requirements=(Requirement(capability='payment-method', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.query_cmds._register.<locals>.plan|src/bookflow/commands/query_cmds.py',)),
    CompanyAction(key='payment-method query options', requirements=(Requirement(capability='payment-method', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.query_cmds._register.<locals>.discover|src/bookflow/commands/query_cmds.py',)),
    CompanyAction(key='payment-method show', requirements=(Requirement(capability='payment-method', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.profile_cmds._register_supporting_noun.<locals>.show_plan|src/bookflow/commands/profile_cmds.py',)),
    CompanyAction(key='payment-method update', requirements=(Requirement(capability='payment-method', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.profile_cmds._register_supporting_noun.<locals>.update_plan|src/bookflow/commands/profile_cmds.py',)),
    CompanyAction(key='presence clear', requirements=(Requirement(capability='presence', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.company_cmds.plan_presence_clear|src/bookflow/commands/company_cmds.py',)),
    CompanyAction(key='presence set', requirements=(Requirement(capability='presence', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.company_cmds.plan_presence_set|src/bookflow/commands/company_cmds.py',)),
    CompanyAction(key='price-level activate', requirements=(Requirement(capability='price-level', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.unit_pricing_cmds._register_price_levels.<locals>.active_plan.<locals>.planner|src/bookflow/commands/unit_pricing_cmds.py',)),
    CompanyAction(key='price-level create', requirements=(Requirement(capability='price-level', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.unit_pricing_cmds._register_price_levels.<locals>.create_plan|src/bookflow/commands/unit_pricing_cmds.py',)),
    CompanyAction(key='price-level deactivate', requirements=(Requirement(capability='price-level', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.unit_pricing_cmds._register_price_levels.<locals>.active_plan.<locals>.planner|src/bookflow/commands/unit_pricing_cmds.py',)),
    CompanyAction(key='price-level list', requirements=(Requirement(capability='price-level', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.unit_pricing_cmds._register_price_levels.<locals>.list_plan|src/bookflow/commands/unit_pricing_cmds.py',)),
    CompanyAction(key='price-level query', requirements=(Requirement(capability='price-level', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.query_cmds._register.<locals>.plan|src/bookflow/commands/query_cmds.py',)),
    CompanyAction(key='price-level query children', requirements=(Requirement(capability='price-level', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.query_cmds._register.<locals>.child_page|src/bookflow/commands/query_cmds.py',)),
    CompanyAction(key='price-level query options', requirements=(Requirement(capability='price-level', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.query_cmds._register.<locals>.discover|src/bookflow/commands/query_cmds.py',)),
    CompanyAction(key='price-level show', requirements=(Requirement(capability='price-level', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.unit_pricing_cmds._register_price_levels.<locals>.show_plan|src/bookflow/commands/unit_pricing_cmds.py',)),
    CompanyAction(key='price-level update', requirements=(Requirement(capability='price-level', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.unit_pricing_cmds._register_price_levels.<locals>.update_plan|src/bookflow/commands/unit_pricing_cmds.py',)),
    CompanyAction(key='profile apply', requirements=(Requirement(capability='profile', threshold='admin'),), available=True, remaining_graph_owners=('bookflow.commands.profile_cmds.plan_profile_apply|src/bookflow/commands/profile_cmds.py',)),
    CompanyAction(key='proposal copy', requirements=(Requirement(capability='customer-work', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.work_cmds._register.<locals>.planner|src/bookflow/commands/work_cmds.py',)),
    CompanyAction(key='proposal create', requirements=(Requirement(capability='customer-work', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.work_cmds._register.<locals>.planner|src/bookflow/commands/work_cmds.py',)),
    CompanyAction(key='proposal estimate', requirements=(Requirement(capability='customer-work', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.work_cmds._register.<locals>.planner|src/bookflow/commands/work_cmds.py',)),
    CompanyAction(key='proposal history', requirements=(Requirement(capability='customer-work', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.work_cmds._register.<locals>.planner|src/bookflow/commands/work_cmds.py',)),
    CompanyAction(key='proposal query', requirements=(Requirement(capability='customer-work', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.work_cmds._register.<locals>.planner|src/bookflow/commands/work_cmds.py',)),
    CompanyAction(key='proposal show', requirements=(Requirement(capability='customer-work', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.work_cmds._register.<locals>.planner|src/bookflow/commands/work_cmds.py',)),
    CompanyAction(key='proposal update', requirements=(Requirement(capability='customer-work', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.work_cmds._register.<locals>.planner|src/bookflow/commands/work_cmds.py',)),
    CompanyAction(key='rate query', requirements=(Requirement(capability='ledger.read', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.rate_cmds.rate_query|src/bookflow/commands/rate_cmds.py',)),
    CompanyAction(key='rate set', requirements=(Requirement(capability='ledger.post', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.rate_cmds.rate_set|src/bookflow/commands/rate_cmds.py',)),
    CompanyAction(key='rate show', requirements=(Requirement(capability='ledger.read', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.rate_cmds.rate_show|src/bookflow/commands/rate_cmds.py',)),
    CompanyAction(key='register calculate', requirements=(Requirement(capability='ledger.read', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.register_cmds.register_calculate|src/bookflow/commands/register_cmds.py',)),
    CompanyAction(key='register post', requirements=(Requirement(capability='ledger.post', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.register_cmds._write.<locals>.planner|src/bookflow/commands/register_cmds.py',)),
    CompanyAction(key='register query', requirements=(Requirement(capability='ledger.read', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.register_cmds.register_query|src/bookflow/commands/register_cmds.py',)),
    CompanyAction(key='register update', requirements=(Requirement(capability='ledger.post', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.register_cmds._write.<locals>.planner|src/bookflow/commands/register_cmds.py',)),
    CompanyAction(key='report balance-sheet', requirements=(Requirement(capability='reports', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.report_cmds.plan_balance_sheet|src/bookflow/commands/report_cmds.py',)),
    CompanyAction(key='report general-ledger', requirements=(Requirement(capability='reports', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.report_cmds.plan_general_ledger|src/bookflow/commands/report_cmds.py',)),
    CompanyAction(key='report profit-and-loss', requirements=(Requirement(capability='reports', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.report_cmds.plan_profit_and_loss|src/bookflow/commands/report_cmds.py',)),
    CompanyAction(key='report trial-balance', requirements=(Requirement(capability='reports', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.report_cmds.plan_trial_balance|src/bookflow/commands/report_cmds.py',)),
    CompanyAction(key='sales-receipt history', requirements=(Requirement(capability='ledger.read', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.sales_cmds._read.<locals>.<lambda>|src/bookflow/commands/sales_cmds.py', 'bookflow.commands.sales_cmds._read.<locals>.planner|src/bookflow/commands/sales_cmds.py')),
    CompanyAction(key='sales-receipt post', requirements=(Requirement(capability='ledger.post', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.sales_cmds._write.<locals>.planner|src/bookflow/commands/sales_cmds.py',)),
    CompanyAction(key='sales-receipt query', requirements=(Requirement(capability='ledger.read', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.sales_cmds._read.<locals>.planner|src/bookflow/commands/sales_cmds.py',)),
    CompanyAction(key='sales-receipt show', requirements=(Requirement(capability='ledger.read', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.sales_cmds._read.<locals>.<lambda>|src/bookflow/commands/sales_cmds.py', 'bookflow.commands.sales_cmds._read.<locals>.planner|src/bookflow/commands/sales_cmds.py')),
    CompanyAction(key='sales-receipt update', requirements=(Requirement(capability='ledger.post', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.sales_cmds._write.<locals>.<lambda>|src/bookflow/commands/sales_cmds.py', 'bookflow.commands.sales_cmds._write.<locals>.planner|src/bookflow/commands/sales_cmds.py')),
    CompanyAction(key='sales-receipt void', requirements=(Requirement(capability='ledger.post', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.sales_cmds._write.<locals>.<lambda>|src/bookflow/commands/sales_cmds.py', 'bookflow.commands.sales_cmds._write.<locals>.planner|src/bookflow/commands/sales_cmds.py')),
    CompanyAction(key='sales-rep activate', requirements=(Requirement(capability='sales-rep', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.profile_cmds._register_supporting_noun.<locals>.active_plan.<locals>.planner|src/bookflow/commands/profile_cmds.py',)),
    CompanyAction(key='sales-rep create', requirements=(Requirement(capability='sales-rep', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.profile_cmds._register_supporting_noun.<locals>.create_plan|src/bookflow/commands/profile_cmds.py',)),
    CompanyAction(key='sales-rep deactivate', requirements=(Requirement(capability='sales-rep', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.profile_cmds._register_supporting_noun.<locals>.active_plan.<locals>.planner|src/bookflow/commands/profile_cmds.py',)),
    CompanyAction(key='sales-rep list', requirements=(Requirement(capability='sales-rep', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.profile_cmds._register_supporting_noun.<locals>.list_plan|src/bookflow/commands/profile_cmds.py',)),
    CompanyAction(key='sales-rep query', requirements=(Requirement(capability='sales-rep', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.query_cmds._register.<locals>.plan|src/bookflow/commands/query_cmds.py',)),
    CompanyAction(key='sales-rep query options', requirements=(Requirement(capability='sales-rep', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.query_cmds._register.<locals>.discover|src/bookflow/commands/query_cmds.py',)),
    CompanyAction(key='sales-rep show', requirements=(Requirement(capability='sales-rep', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.profile_cmds._register_supporting_noun.<locals>.show_plan|src/bookflow/commands/profile_cmds.py',)),
    CompanyAction(key='sales-rep update', requirements=(Requirement(capability='sales-rep', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.profile_cmds._register_supporting_noun.<locals>.update_plan|src/bookflow/commands/profile_cmds.py',)),
    CompanyAction(key='sales-tax-code activate', requirements=(Requirement(capability='sales-tax-code', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.profile_cmds._register_supporting_noun.<locals>.active_plan.<locals>.planner|src/bookflow/commands/profile_cmds.py',)),
    CompanyAction(key='sales-tax-code create', requirements=(Requirement(capability='sales-tax-code', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.profile_cmds._register_supporting_noun.<locals>.create_plan|src/bookflow/commands/profile_cmds.py',)),
    CompanyAction(key='sales-tax-code deactivate', requirements=(Requirement(capability='sales-tax-code', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.profile_cmds._register_supporting_noun.<locals>.active_plan.<locals>.planner|src/bookflow/commands/profile_cmds.py',)),
    CompanyAction(key='sales-tax-code list', requirements=(Requirement(capability='sales-tax-code', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.profile_cmds._register_supporting_noun.<locals>.list_plan|src/bookflow/commands/profile_cmds.py',)),
    CompanyAction(key='sales-tax-code query', requirements=(Requirement(capability='sales-tax-code', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.query_cmds._register.<locals>.plan|src/bookflow/commands/query_cmds.py',)),
    CompanyAction(key='sales-tax-code query options', requirements=(Requirement(capability='sales-tax-code', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.query_cmds._register.<locals>.discover|src/bookflow/commands/query_cmds.py',)),
    CompanyAction(key='sales-tax-code show', requirements=(Requirement(capability='sales-tax-code', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.profile_cmds._register_supporting_noun.<locals>.show_plan|src/bookflow/commands/profile_cmds.py',)),
    CompanyAction(key='sales-tax-code update', requirements=(Requirement(capability='sales-tax-code', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.profile_cmds._register_supporting_noun.<locals>.update_plan|src/bookflow/commands/profile_cmds.py',)),
    CompanyAction(key='ship-method activate', requirements=(Requirement(capability='ship-method', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.profile_cmds._register_supporting_noun.<locals>.active_plan.<locals>.planner|src/bookflow/commands/profile_cmds.py',)),
    CompanyAction(key='ship-method create', requirements=(Requirement(capability='ship-method', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.profile_cmds._register_supporting_noun.<locals>.create_plan|src/bookflow/commands/profile_cmds.py',)),
    CompanyAction(key='ship-method deactivate', requirements=(Requirement(capability='ship-method', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.profile_cmds._register_supporting_noun.<locals>.active_plan.<locals>.planner|src/bookflow/commands/profile_cmds.py',)),
    CompanyAction(key='ship-method list', requirements=(Requirement(capability='ship-method', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.profile_cmds._register_supporting_noun.<locals>.list_plan|src/bookflow/commands/profile_cmds.py',)),
    CompanyAction(key='ship-method query', requirements=(Requirement(capability='ship-method', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.query_cmds._register.<locals>.plan|src/bookflow/commands/query_cmds.py',)),
    CompanyAction(key='ship-method query options', requirements=(Requirement(capability='ship-method', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.query_cmds._register.<locals>.discover|src/bookflow/commands/query_cmds.py',)),
    CompanyAction(key='ship-method show', requirements=(Requirement(capability='ship-method', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.profile_cmds._register_supporting_noun.<locals>.show_plan|src/bookflow/commands/profile_cmds.py',)),
    CompanyAction(key='ship-method update', requirements=(Requirement(capability='ship-method', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.profile_cmds._register_supporting_noun.<locals>.update_plan|src/bookflow/commands/profile_cmds.py',)),
    CompanyAction(key='term activate', requirements=(Requirement(capability='term', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.profile_cmds._register_supporting_noun.<locals>.active_plan.<locals>.planner|src/bookflow/commands/profile_cmds.py',)),
    CompanyAction(key='term create', requirements=(Requirement(capability='term', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.profile_cmds._register_supporting_noun.<locals>.create_plan|src/bookflow/commands/profile_cmds.py',)),
    CompanyAction(key='term deactivate', requirements=(Requirement(capability='term', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.profile_cmds._register_supporting_noun.<locals>.active_plan.<locals>.planner|src/bookflow/commands/profile_cmds.py',)),
    CompanyAction(key='term list', requirements=(Requirement(capability='term', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.profile_cmds._register_supporting_noun.<locals>.list_plan|src/bookflow/commands/profile_cmds.py',)),
    CompanyAction(key='term query', requirements=(Requirement(capability='term', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.query_cmds._register.<locals>.plan|src/bookflow/commands/query_cmds.py',)),
    CompanyAction(key='term query options', requirements=(Requirement(capability='term', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.query_cmds._register.<locals>.discover|src/bookflow/commands/query_cmds.py',)),
    CompanyAction(key='term show', requirements=(Requirement(capability='term', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.profile_cmds._register_supporting_noun.<locals>.show_plan|src/bookflow/commands/profile_cmds.py',)),
    CompanyAction(key='term update', requirements=(Requirement(capability='term', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.profile_cmds._register_supporting_noun.<locals>.update_plan|src/bookflow/commands/profile_cmds.py',)),
    CompanyAction(key='undo', requirements=(Requirement(capability='undo', threshold='admin'),), available=True, remaining_graph_owners=('bookflow.commands.undo_cmds.plan_undo|src/bookflow/commands/undo_cmds.py',)),
    CompanyAction(key='unit-of-measure activate', requirements=(Requirement(capability='unit-of-measure', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.unit_pricing_cmds._register_units.<locals>.active_plan.<locals>.planner|src/bookflow/commands/unit_pricing_cmds.py',)),
    CompanyAction(key='unit-of-measure create', requirements=(Requirement(capability='unit-of-measure', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.unit_pricing_cmds._register_units.<locals>.create_plan|src/bookflow/commands/unit_pricing_cmds.py',)),
    CompanyAction(key='unit-of-measure deactivate', requirements=(Requirement(capability='unit-of-measure', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.unit_pricing_cmds._register_units.<locals>.active_plan.<locals>.planner|src/bookflow/commands/unit_pricing_cmds.py',)),
    CompanyAction(key='unit-of-measure list', requirements=(Requirement(capability='unit-of-measure', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.unit_pricing_cmds._register_units.<locals>.list_plan|src/bookflow/commands/unit_pricing_cmds.py',)),
    CompanyAction(key='unit-of-measure query', requirements=(Requirement(capability='unit-of-measure', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.query_cmds._register.<locals>.plan|src/bookflow/commands/query_cmds.py',)),
    CompanyAction(key='unit-of-measure query children', requirements=(Requirement(capability='unit-of-measure', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.query_cmds._register.<locals>.child_page|src/bookflow/commands/query_cmds.py',)),
    CompanyAction(key='unit-of-measure query options', requirements=(Requirement(capability='unit-of-measure', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.query_cmds._register.<locals>.discover|src/bookflow/commands/query_cmds.py',)),
    CompanyAction(key='unit-of-measure show', requirements=(Requirement(capability='unit-of-measure', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.unit_pricing_cmds._register_units.<locals>.show_plan|src/bookflow/commands/unit_pricing_cmds.py',)),
    CompanyAction(key='unit-of-measure update', requirements=(Requirement(capability='unit-of-measure', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.unit_pricing_cmds._register_units.<locals>.update_plan|src/bookflow/commands/unit_pricing_cmds.py',)),
    CompanyAction(key='vendor activate', requirements=(Requirement(capability='vendor', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.party_cmds._register_party.<locals>.active_plan.<locals>.planner|src/bookflow/commands/party_cmds.py',)),
    CompanyAction(key='vendor create', requirements=(Requirement(capability='vendor', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.party_cmds._register_party.<locals>.create_plan|src/bookflow/commands/party_cmds.py',)),
    CompanyAction(key='vendor deactivate', requirements=(Requirement(capability='vendor', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.party_cmds._register_party.<locals>.active_plan.<locals>.planner|src/bookflow/commands/party_cmds.py',)),
    CompanyAction(key='vendor list', requirements=(Requirement(capability='vendor', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.party_cmds._register_party.<locals>.list_plan|src/bookflow/commands/party_cmds.py',)),
    CompanyAction(key='vendor query', requirements=(Requirement(capability='vendor', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.query_cmds._register.<locals>.plan|src/bookflow/commands/query_cmds.py',)),
    CompanyAction(key='vendor query children', requirements=(Requirement(capability='vendor', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.query_cmds._register.<locals>.child_page|src/bookflow/commands/query_cmds.py',)),
    CompanyAction(key='vendor query options', requirements=(Requirement(capability='vendor', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.query_cmds._register.<locals>.discover|src/bookflow/commands/query_cmds.py',)),
    CompanyAction(key='vendor show', requirements=(Requirement(capability='vendor', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.party_cmds._register_party.<locals>.show_plan|src/bookflow/commands/party_cmds.py',)),
    CompanyAction(key='vendor update', requirements=(Requirement(capability='vendor', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.party_cmds._register_party.<locals>.update_plan|src/bookflow/commands/party_cmds.py',)),
    CompanyAction(key='vendor-type activate', requirements=(Requirement(capability='vendor-type', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.profile_cmds._register_supporting_noun.<locals>.active_plan.<locals>.planner|src/bookflow/commands/profile_cmds.py',)),
    CompanyAction(key='vendor-type create', requirements=(Requirement(capability='vendor-type', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.profile_cmds._register_supporting_noun.<locals>.create_plan|src/bookflow/commands/profile_cmds.py',)),
    CompanyAction(key='vendor-type deactivate', requirements=(Requirement(capability='vendor-type', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.profile_cmds._register_supporting_noun.<locals>.active_plan.<locals>.planner|src/bookflow/commands/profile_cmds.py',)),
    CompanyAction(key='vendor-type list', requirements=(Requirement(capability='vendor-type', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.profile_cmds._register_supporting_noun.<locals>.list_plan|src/bookflow/commands/profile_cmds.py',)),
    CompanyAction(key='vendor-type query', requirements=(Requirement(capability='vendor-type', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.query_cmds._register.<locals>.plan|src/bookflow/commands/query_cmds.py',)),
    CompanyAction(key='vendor-type query options', requirements=(Requirement(capability='vendor-type', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.query_cmds._register.<locals>.discover|src/bookflow/commands/query_cmds.py',)),
    CompanyAction(key='vendor-type show', requirements=(Requirement(capability='vendor-type', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.profile_cmds._register_supporting_noun.<locals>.show_plan|src/bookflow/commands/profile_cmds.py',)),
    CompanyAction(key='vendor-type update', requirements=(Requirement(capability='vendor-type', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.profile_cmds._register_supporting_noun.<locals>.update_plan|src/bookflow/commands/profile_cmds.py',)),
    CompanyAction(key='work-order billing', requirements=(Requirement(capability='customer-work', threshold='member'), Requirement(capability='ledger.read', threshold='member')), available=True, remaining_graph_owners=('bookflow.commands.billing_cmds.register.<locals>.planner|src/bookflow/commands/billing_cmds.py',)),
    CompanyAction(key='work-order complete', requirements=(Requirement(capability='customer-work', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.work_cmds._register.<locals>.planner|src/bookflow/commands/work_cmds.py',)),
    CompanyAction(key='work-order copy', requirements=(Requirement(capability='customer-work', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.work_cmds._register.<locals>.planner|src/bookflow/commands/work_cmds.py',)),
    CompanyAction(key='work-order create', requirements=(Requirement(capability='customer-work', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.work_cmds._register.<locals>.planner|src/bookflow/commands/work_cmds.py',)),
    CompanyAction(key='work-order history', requirements=(Requirement(capability='customer-work', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.work_cmds._register.<locals>.planner|src/bookflow/commands/work_cmds.py',)),
    CompanyAction(key='work-order invoice', requirements=(Requirement(capability='customer-work', threshold='standard'), Requirement(capability='ledger.post', threshold='standard')), available=True, remaining_graph_owners=('bookflow.commands.billing_cmds.register.<locals>.planner|src/bookflow/commands/billing_cmds.py',)),
    CompanyAction(key='work-order query', requirements=(Requirement(capability='customer-work', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.work_cmds._register.<locals>.planner|src/bookflow/commands/work_cmds.py',)),
    CompanyAction(key='work-order sales-receipt', requirements=(Requirement(capability='customer-work', threshold='standard'), Requirement(capability='ledger.post', threshold='standard')), available=True, remaining_graph_owners=('bookflow.commands.billing_cmds.register.<locals>.planner|src/bookflow/commands/billing_cmds.py',)),
    CompanyAction(key='work-order show', requirements=(Requirement(capability='customer-work', threshold='member'),), available=True, remaining_graph_owners=('bookflow.commands.work_cmds._register.<locals>.planner|src/bookflow/commands/work_cmds.py',)),
    CompanyAction(key='work-order update', requirements=(Requirement(capability='customer-work', threshold='standard'),), available=True, remaining_graph_owners=('bookflow.commands.work_cmds._register.<locals>.planner|src/bookflow/commands/work_cmds.py',)),
)


FROZEN_ADMIN_ACTIONS = (
    AdminAction(key='admin:agents', domain='hub', threshold='hub_admin', human_only=True, available=False),
    AdminAction(key='admin:company:attach', domain='hub', threshold='hub_admin', human_only=False, available=True),
    AdminAction(key='admin:company:detach', domain='hub', threshold='hub_admin', human_only=False, available=True),
    AdminAction(key='admin:company:new', domain='organization', threshold='admin', human_only=False, available=True),
    AdminAction(key='admin:demo:reset', domain='hub', threshold='hub_admin', human_only=False, available=True),
    AdminAction(key='admin:members:admin:company', domain='company', threshold='admin', human_only=True, available=False),
    AdminAction(key='admin:members:admin:organization', domain='organization', threshold='admin', human_only=True, available=False),
    AdminAction(key='admin:members:owner:company', domain='company', threshold='owner', human_only=True, available=False),
    AdminAction(key='admin:members:owner:organization', domain='organization', threshold='owner', human_only=True, available=False),
    AdminAction(key='admin:members:readonly:company', domain='company', threshold='admin', human_only=True, available=False),
    AdminAction(key='admin:members:readonly:organization', domain='organization', threshold='admin', human_only=True, available=False),
    AdminAction(key='admin:members:standard:company', domain='company', threshold='admin', human_only=True, available=False),
    AdminAction(key='admin:members:standard:organization', domain='organization', threshold='admin', human_only=True, available=False),
    AdminAction(key='admin:organization:new', domain='hub', threshold='hub_admin', human_only=False, available=True),
    AdminAction(key='admin:organization:rename', domain='hub', threshold='hub_admin', human_only=False, available=True),
    AdminAction(key='admin:users', domain='hub', threshold='hub_admin', human_only=True, available=False),
)


CONDITIONAL_RESOURCE_SOURCES = (
    ResourceSource(owner='bookflow.company.billing_edits.carry_allocations', call_sites=(('src/bookflow/company/billing_edits.py', 103),), requirements=(Requirement(capability='customer-work', threshold='standard'),)),
    ResourceSource(owner='bookflow.company.billing_edits.protect_sale', call_sites=(('src/bookflow/company/billing_edits.py', 64),), requirements=(Requirement(capability='customer-work', threshold='standard'),)),
    ResourceSource(owner='bookflow.company.billing_queries.authorize_sale', call_sites=(('src/bookflow/company/billing_queries.py', 62),), requirements=(Requirement(capability='customer-work', threshold='member'), Requirement(capability='customer-work', threshold='standard'))),
    ResourceSource(owner='bookflow.company.billing_queries.sale_source_links', call_sites=(('src/bookflow/company/billing_queries.py', 50),), requirements=(Requirement(capability='customer-work', threshold='member'),)),
    ResourceSource(owner='bookflow.company.billing_queries.sale_source_output', call_sites=(('src/bookflow/company/billing_queries.py', 38),), requirements=(Requirement(capability='customer-work', threshold='member'),)),
    ResourceSource(owner='bookflow.company.deposit_resolution.resolve', call_sites=(('src/bookflow/company/deposit_resolution.py', 16),), requirements=(Requirement(capability='ledger.post', threshold='standard'),)),
    ResourceSource(owner='bookflow.company.payment_authority.authorize', call_sites=(('src/bookflow/company/payment_authority.py', 61),), requirements=(Requirement(capability='customer-work', threshold='member'), Requirement(capability='customer-work', threshold='standard'), Requirement(capability='ledger.post', threshold='standard'), Requirement(capability='ledger.read', threshold='member'))),
    ResourceSource(owner='bookflow.company.payment_authority.authorize_event', call_sites=(('src/bookflow/company/payment_authority.py', 391),), requirements=(Requirement(capability='customer-work', threshold='member'), Requirement(capability='ledger.read', threshold='member'))),
    ResourceSource(owner='bookflow.company.payment_authority.authorize_events', call_sites=(('src/bookflow/company/payment_authority.py', 406),), requirements=(Requirement(capability='customer-work', threshold='member'), Requirement(capability='ledger.read', threshold='member'))),
    ResourceSource(owner='bookflow.company.payment_authority.authorize_publication_selections', call_sites=(('src/bookflow/company/payment_authority.py', 651),), requirements=(Requirement(capability='customer-work', threshold='member'), Requirement(capability='customer-work', threshold='standard'), Requirement(capability='ledger.post', threshold='standard'), Requirement(capability='ledger.read', threshold='member'))),
    ResourceSource(owner='bookflow.company.payment_authority.authorize_publication_transactions', call_sites=(('src/bookflow/company/payment_authority.py', 501), ('src/bookflow/company/payment_authority.py', 503)), requirements=(Requirement(capability='customer-work', threshold='member'), Requirement(capability='customer-work', threshold='standard'), Requirement(capability='ledger.post', threshold='standard'), Requirement(capability='ledger.read', threshold='member'))),
    ResourceSource(owner='bookflow.company.payment_authority.authorize_query', call_sites=(('src/bookflow/company/payment_authority.py', 72), ('src/bookflow/company/payment_authority.py', 74)), requirements=(Requirement(capability='customer-work', threshold='member'), Requirement(capability='customer-work', threshold='standard'), Requirement(capability='ledger.post', threshold='standard'), Requirement(capability='ledger.read', threshold='member'))),
    ResourceSource(owner='bookflow.company.payment_authority.denied_events', call_sites=(('src/bookflow/company/payment_authority.py', 424),), requirements=(Requirement(capability='customer-work', threshold='member'), Requirement(capability='ledger.read', threshold='member'))),
    ResourceSource(owner='bookflow.company.payment_authority.readable_predicate', call_sites=(('src/bookflow/company/payment_authority.py', 26),), requirements=(Requirement(capability='customer-work', threshold='member'),)),
    ResourceSource(owner='bookflow.company.payment_preparation.payment_page', call_sites=(('src/bookflow/company/payment_preparation.py', 275),), requirements=(Requirement(capability='customer-work', threshold='member'),)),
    ResourceSource(owner='bookflow.company.payment_recovery.readable_selection', call_sites=(('src/bookflow/company/payment_recovery.py', 589),), requirements=(Requirement(capability='customer-work', threshold='member'),)),
)

FROZEN_CATALOG = Catalog('deposit-lifecycle-co0021-v1', FROZEN_COMMANDS, FROZEN_CAPABILITIES, FROZEN_DEFAULTS, FROZEN_COMPANY_ACTIONS, FROZEN_ADMIN_ACTIONS, CONDITIONAL_RESOURCE_SOURCES)

FROZEN_MANIFEST = CatalogManifest(version='deposit-lifecycle-co0021-v1', command_names=('account activate', 'account create', 'account deactivate', 'account list', 'account query', 'account query options', 'account show', 'account update', 'activity', 'application history', 'application show', 'attachment add', 'attachment get', 'attachment link', 'attachment list', 'attachment unlink', 'audit list', 'audit show', 'audit tail', 'chart apply', 'chart list', 'chart show', 'class activate', 'class create', 'class deactivate', 'class list', 'class query', 'class query options', 'class show', 'class update', 'company attach', 'company compact', 'company detach', 'company list', 'company new', 'company rename', 'company show', 'company update', 'company use', 'custom-field activate', 'custom-field create', 'custom-field deactivate', 'custom-field list', 'custom-field query', 'custom-field query children', 'custom-field query options', 'custom-field show', 'custom-field update', 'customer activate', 'customer create', 'customer deactivate', 'customer link-vendor', 'customer list', 'customer query', 'customer query options', 'customer show', 'customer unlink-vendor', 'customer update', 'customer-message activate', 'customer-message create', 'customer-message deactivate', 'customer-message list', 'customer-message query', 'customer-message query options', 'customer-message show', 'customer-message update', 'customer-type activate', 'customer-type create', 'customer-type deactivate', 'customer-type list', 'customer-type query', 'customer-type query options', 'customer-type show', 'customer-type update', 'demo reset', 'directive add', 'directive deactivate', 'directive list', 'directive show', 'employee activate', 'employee create', 'employee deactivate', 'employee list', 'employee query', 'employee query options', 'employee show', 'employee update', 'estimate billing', 'estimate copy', 'estimate create', 'estimate history', 'estimate invoice', 'estimate query', 'estimate sales-receipt', 'estimate show', 'estimate update', 'estimate work-order', 'hub audit list', 'hub audit show', 'hub audit tail', 'init', 'invoice history', 'invoice post', 'invoice query', 'invoice settlement', 'invoice show', 'invoice update', 'invoice void', 'item activate', 'item create', 'item deactivate', 'item list', 'item query', 'item query children', 'item query options', 'item show', 'item update', 'item-category activate', 'item-category create', 'item-category deactivate', 'item-category list', 'item-category query', 'item-category query options', 'item-category show', 'item-category update', 'job-type activate', 'job-type create', 'job-type deactivate', 'job-type list', 'job-type query', 'job-type query options', 'job-type show', 'job-type update', 'journal history', 'journal post', 'journal query', 'journal show', 'journal update', 'journal void', 'note add', 'note edit', 'note list', 'note show', 'organization list', 'organization new', 'organization rename', 'organization show', 'other-name activate', 'other-name convert', 'other-name create', 'other-name deactivate', 'other-name list', 'other-name query', 'other-name query options', 'other-name show', 'other-name update', 'payment apply', 'payment calculate', 'payment history', 'payment invoices', 'payment operation items', 'payment operation show', 'payment preview items', 'payment query', 'payment receive', 'payment recovery abort', 'payment recovery apply', 'payment recovery begin', 'payment recovery compare', 'payment recovery compare-items', 'payment recovery items', 'payment recovery query', 'payment recovery replace', 'payment recovery seal', 'payment recovery show', 'payment recovery upload', 'payment selection clear', 'payment selection create', 'payment selection items', 'payment selection query', 'payment selection show', 'payment selection update', 'payment settlement', 'payment settlement changes', 'payment show', 'payment suggest', 'payment unapply', 'payment update', 'payment void', 'payment-method activate', 'payment-method create', 'payment-method deactivate', 'payment-method list', 'payment-method query', 'payment-method query options', 'payment-method show', 'payment-method update', 'presence clear', 'presence set', 'price-level activate', 'price-level create', 'price-level deactivate', 'price-level list', 'price-level query', 'price-level query children', 'price-level query options', 'price-level show', 'price-level update', 'profile apply', 'profile list', 'profile show', 'proposal copy', 'proposal create', 'proposal estimate', 'proposal history', 'proposal query', 'proposal show', 'proposal update', 'rate query', 'rate set', 'rate show', 'register calculate', 'register post', 'register query', 'register update', 'report balance-sheet', 'report general-ledger', 'report profit-and-loss', 'report trial-balance', 'sales-receipt history', 'sales-receipt post', 'sales-receipt query', 'sales-receipt show', 'sales-receipt update', 'sales-receipt void', 'sales-rep activate', 'sales-rep create', 'sales-rep deactivate', 'sales-rep list', 'sales-rep query', 'sales-rep query options', 'sales-rep show', 'sales-rep update', 'sales-tax-code activate', 'sales-tax-code create', 'sales-tax-code deactivate', 'sales-tax-code list', 'sales-tax-code query', 'sales-tax-code query options', 'sales-tax-code show', 'sales-tax-code update', 'serve', 'ship-method activate', 'ship-method create', 'ship-method deactivate', 'ship-method list', 'ship-method query', 'ship-method query options', 'ship-method show', 'ship-method update', 'term activate', 'term create', 'term deactivate', 'term list', 'term query', 'term query options', 'term show', 'term update', 'token issue', 'token list', 'token revoke', 'undo', 'unit-of-measure activate', 'unit-of-measure create', 'unit-of-measure deactivate', 'unit-of-measure list', 'unit-of-measure query', 'unit-of-measure query children', 'unit-of-measure query options', 'unit-of-measure show', 'unit-of-measure update', 'upgrade', 'user set-password', 'vendor activate', 'vendor create', 'vendor deactivate', 'vendor list', 'vendor query', 'vendor query children', 'vendor query options', 'vendor show', 'vendor update', 'vendor-type activate', 'vendor-type create', 'vendor-type deactivate', 'vendor-type list', 'vendor-type query', 'vendor-type query options', 'vendor-type show', 'vendor-type update', 'work-order billing', 'work-order complete', 'work-order copy', 'work-order create', 'work-order history', 'work-order invoice', 'work-order query', 'work-order sales-receipt', 'work-order show', 'work-order update'), standalone_names=('docs generate', 'mcp'), capability_names=('account', 'activity', 'attachment', 'audit', 'chart', 'class', 'company', 'custom-field', 'customer', 'customer-message', 'customer-type', 'customer-work', 'demo', 'directive', 'employee', 'hub', 'init', 'item', 'item-category', 'job-type', 'ledger.post', 'ledger.read', 'note', 'organization', 'other-name', 'payment-method', 'presence', 'price-level', 'profile', 'reports', 'sales-rep', 'sales-tax-code', 'serve', 'ship-method', 'term', 'token', 'transaction.invoice.delete', 'transaction.journal_entry.delete', 'transaction.payment.delete', 'transaction.sales_receipt.delete', 'undo', 'unit-of-measure', 'upgrade', 'user', 'vendor', 'vendor-type'), company_requirements=(Requirement(capability='account', threshold='member'), Requirement(capability='account', threshold='standard'), Requirement(capability='activity', threshold='member'), Requirement(capability='attachment', threshold='member'), Requirement(capability='attachment', threshold='standard'), Requirement(capability='attachment', threshold='admin'), Requirement(capability='audit', threshold='member'), Requirement(capability='chart', threshold='admin'), Requirement(capability='class', threshold='member'), Requirement(capability='class', threshold='standard'), Requirement(capability='company', threshold='member'), Requirement(capability='company', threshold='admin'), Requirement(capability='custom-field', threshold='member'), Requirement(capability='custom-field', threshold='admin'), Requirement(capability='customer', threshold='member'), Requirement(capability='customer', threshold='standard'), Requirement(capability='customer-message', threshold='member'), Requirement(capability='customer-message', threshold='standard'), Requirement(capability='customer-type', threshold='member'), Requirement(capability='customer-type', threshold='standard'), Requirement(capability='customer-work', threshold='member'), Requirement(capability='customer-work', threshold='standard'), Requirement(capability='directive', threshold='member'), Requirement(capability='directive', threshold='standard'), Requirement(capability='employee', threshold='member'), Requirement(capability='employee', threshold='standard'), Requirement(capability='item', threshold='member'), Requirement(capability='item', threshold='standard'), Requirement(capability='item-category', threshold='member'), Requirement(capability='item-category', threshold='standard'), Requirement(capability='job-type', threshold='member'), Requirement(capability='job-type', threshold='standard'), Requirement(capability='ledger.post', threshold='standard'), Requirement(capability='ledger.read', threshold='member'), Requirement(capability='note', threshold='member'), Requirement(capability='note', threshold='standard'), Requirement(capability='other-name', threshold='member'), Requirement(capability='other-name', threshold='standard'), Requirement(capability='payment-method', threshold='member'), Requirement(capability='payment-method', threshold='standard'), Requirement(capability='presence', threshold='standard'), Requirement(capability='price-level', threshold='member'), Requirement(capability='price-level', threshold='standard'), Requirement(capability='profile', threshold='admin'), Requirement(capability='reports', threshold='member'), Requirement(capability='sales-rep', threshold='member'), Requirement(capability='sales-rep', threshold='standard'), Requirement(capability='sales-tax-code', threshold='member'), Requirement(capability='sales-tax-code', threshold='standard'), Requirement(capability='ship-method', threshold='member'), Requirement(capability='ship-method', threshold='standard'), Requirement(capability='term', threshold='member'), Requirement(capability='term', threshold='standard'), Requirement(capability='transaction.invoice.delete', threshold='standard'), Requirement(capability='transaction.journal_entry.delete', threshold='standard'), Requirement(capability='transaction.payment.delete', threshold='standard'), Requirement(capability='transaction.sales_receipt.delete', threshold='standard'), Requirement(capability='undo', threshold='admin'), Requirement(capability='unit-of-measure', threshold='member'), Requirement(capability='unit-of-measure', threshold='standard'), Requirement(capability='vendor', threshold='member'), Requirement(capability='vendor', threshold='standard'), Requirement(capability='vendor-type', threshold='member'), Requirement(capability='vendor-type', threshold='standard')), registered_requirements=(Requirement(capability='account', threshold='member'), Requirement(capability='account', threshold='standard'), Requirement(capability='activity', threshold='member'), Requirement(capability='attachment', threshold='member'), Requirement(capability='attachment', threshold='standard'), Requirement(capability='attachment', threshold='admin'), Requirement(capability='audit', threshold='member'), Requirement(capability='chart', threshold='authenticated'), Requirement(capability='chart', threshold='admin'), Requirement(capability='class', threshold='member'), Requirement(capability='class', threshold='standard'), Requirement(capability='company', threshold='authenticated'), Requirement(capability='company', threshold='member'), Requirement(capability='company', threshold='admin'), Requirement(capability='company', threshold='hub_admin'), Requirement(capability='custom-field', threshold='member'), Requirement(capability='custom-field', threshold='admin'), Requirement(capability='customer', threshold='member'), Requirement(capability='customer', threshold='standard'), Requirement(capability='customer-message', threshold='member'), Requirement(capability='customer-message', threshold='standard'), Requirement(capability='customer-type', threshold='member'), Requirement(capability='customer-type', threshold='standard'), Requirement(capability='customer-work', threshold='member'), Requirement(capability='customer-work', threshold='standard'), Requirement(capability='demo', threshold='hub_admin'), Requirement(capability='directive', threshold='member'), Requirement(capability='directive', threshold='standard'), Requirement(capability='employee', threshold='member'), Requirement(capability='employee', threshold='standard'), Requirement(capability='hub', threshold='authenticated'), Requirement(capability='init', threshold='authenticated'), Requirement(capability='item', threshold='member'), Requirement(capability='item', threshold='standard'), Requirement(capability='item-category', threshold='member'), Requirement(capability='item-category', threshold='standard'), Requirement(capability='job-type', threshold='member'), Requirement(capability='job-type', threshold='standard'), Requirement(capability='ledger.post', threshold='standard'), Requirement(capability='ledger.read', threshold='member'), Requirement(capability='note', threshold='member'), Requirement(capability='note', threshold='standard'), Requirement(capability='organization', threshold='authenticated'), Requirement(capability='organization', threshold='hub_admin'), Requirement(capability='other-name', threshold='member'), Requirement(capability='other-name', threshold='standard'), Requirement(capability='payment-method', threshold='member'), Requirement(capability='payment-method', threshold='standard'), Requirement(capability='presence', threshold='standard'), Requirement(capability='price-level', threshold='member'), Requirement(capability='price-level', threshold='standard'), Requirement(capability='profile', threshold='authenticated'), Requirement(capability='profile', threshold='admin'), Requirement(capability='reports', threshold='member'), Requirement(capability='sales-rep', threshold='member'), Requirement(capability='sales-rep', threshold='standard'), Requirement(capability='sales-tax-code', threshold='member'), Requirement(capability='sales-tax-code', threshold='standard'), Requirement(capability='serve', threshold='authenticated'), Requirement(capability='ship-method', threshold='member'), Requirement(capability='ship-method', threshold='standard'), Requirement(capability='term', threshold='member'), Requirement(capability='term', threshold='standard'), Requirement(capability='token', threshold='authenticated'), Requirement(capability='undo', threshold='admin'), Requirement(capability='unit-of-measure', threshold='member'), Requirement(capability='unit-of-measure', threshold='standard'), Requirement(capability='upgrade', threshold='authenticated'), Requirement(capability='user', threshold='authenticated'), Requirement(capability='vendor', threshold='member'), Requirement(capability='vendor', threshold='standard'), Requirement(capability='vendor-type', threshold='member'), Requirement(capability='vendor-type', threshold='standard')), company_action_keys=('account activate', 'account create', 'account deactivate', 'account list', 'account query', 'account query options', 'account show', 'account update', 'activity', 'application history', 'application show', 'attachment add', 'attachment get', 'attachment link', 'attachment list', 'attachment unlink', 'audit list', 'audit show', 'audit tail', 'chart apply', 'class activate', 'class create', 'class deactivate', 'class list', 'class query', 'class query options', 'class show', 'class update', 'company compact', 'company rename', 'company show', 'company update', 'contract:delete:invoice', 'contract:delete:journal_entry', 'contract:delete:payment', 'contract:delete:sales_receipt', 'custom-field activate', 'custom-field create', 'custom-field deactivate', 'custom-field list', 'custom-field query', 'custom-field query children', 'custom-field query options', 'custom-field show', 'custom-field update', 'customer activate', 'customer create', 'customer deactivate', 'customer link-vendor', 'customer list', 'customer query', 'customer query options', 'customer show', 'customer unlink-vendor', 'customer update', 'customer-message activate', 'customer-message create', 'customer-message deactivate', 'customer-message list', 'customer-message query', 'customer-message query options', 'customer-message show', 'customer-message update', 'customer-type activate', 'customer-type create', 'customer-type deactivate', 'customer-type list', 'customer-type query', 'customer-type query options', 'customer-type show', 'customer-type update', 'directive add', 'directive deactivate', 'directive list', 'directive show', 'employee activate', 'employee create', 'employee deactivate', 'employee list', 'employee query', 'employee query options', 'employee show', 'employee update', 'estimate billing', 'estimate copy', 'estimate create', 'estimate history', 'estimate invoice', 'estimate query', 'estimate sales-receipt', 'estimate show', 'estimate update', 'estimate work-order', 'invoice history', 'invoice post', 'invoice query', 'invoice settlement', 'invoice show', 'invoice update', 'invoice void', 'item activate', 'item create', 'item deactivate', 'item list', 'item query', 'item query children', 'item query options', 'item show', 'item update', 'item-category activate', 'item-category create', 'item-category deactivate', 'item-category list', 'item-category query', 'item-category query options', 'item-category show', 'item-category update', 'job-type activate', 'job-type create', 'job-type deactivate', 'job-type list', 'job-type query', 'job-type query options', 'job-type show', 'job-type update', 'journal history', 'journal post', 'journal query', 'journal show', 'journal update', 'journal void', 'note add', 'note edit', 'note list', 'note show', 'other-name activate', 'other-name convert', 'other-name create', 'other-name deactivate', 'other-name list', 'other-name query', 'other-name query options', 'other-name show', 'other-name update', 'payment apply', 'payment calculate', 'payment history', 'payment invoices', 'payment operation items', 'payment operation show', 'payment preview items', 'payment query', 'payment receive', 'payment recovery abort', 'payment recovery apply', 'payment recovery begin', 'payment recovery compare', 'payment recovery compare-items', 'payment recovery items', 'payment recovery query', 'payment recovery replace', 'payment recovery seal', 'payment recovery show', 'payment recovery upload', 'payment selection clear', 'payment selection create', 'payment selection items', 'payment selection query', 'payment selection show', 'payment selection update', 'payment settlement', 'payment settlement changes', 'payment show', 'payment suggest', 'payment unapply', 'payment update', 'payment void', 'payment-method activate', 'payment-method create', 'payment-method deactivate', 'payment-method list', 'payment-method query', 'payment-method query options', 'payment-method show', 'payment-method update', 'presence clear', 'presence set', 'price-level activate', 'price-level create', 'price-level deactivate', 'price-level list', 'price-level query', 'price-level query children', 'price-level query options', 'price-level show', 'price-level update', 'profile apply', 'proposal copy', 'proposal create', 'proposal estimate', 'proposal history', 'proposal query', 'proposal show', 'proposal update', 'rate query', 'rate set', 'rate show', 'register calculate', 'register post', 'register query', 'register update', 'report balance-sheet', 'report general-ledger', 'report profit-and-loss', 'report trial-balance', 'sales-receipt history', 'sales-receipt post', 'sales-receipt query', 'sales-receipt show', 'sales-receipt update', 'sales-receipt void', 'sales-rep activate', 'sales-rep create', 'sales-rep deactivate', 'sales-rep list', 'sales-rep query', 'sales-rep query options', 'sales-rep show', 'sales-rep update', 'sales-tax-code activate', 'sales-tax-code create', 'sales-tax-code deactivate', 'sales-tax-code list', 'sales-tax-code query', 'sales-tax-code query options', 'sales-tax-code show', 'sales-tax-code update', 'ship-method activate', 'ship-method create', 'ship-method deactivate', 'ship-method list', 'ship-method query', 'ship-method query options', 'ship-method show', 'ship-method update', 'term activate', 'term create', 'term deactivate', 'term list', 'term query', 'term query options', 'term show', 'term update', 'undo', 'unit-of-measure activate', 'unit-of-measure create', 'unit-of-measure deactivate', 'unit-of-measure list', 'unit-of-measure query', 'unit-of-measure query children', 'unit-of-measure query options', 'unit-of-measure show', 'unit-of-measure update', 'vendor activate', 'vendor create', 'vendor deactivate', 'vendor list', 'vendor query', 'vendor query children', 'vendor query options', 'vendor show', 'vendor update', 'vendor-type activate', 'vendor-type create', 'vendor-type deactivate', 'vendor-type list', 'vendor-type query', 'vendor-type query options', 'vendor-type show', 'vendor-type update', 'work-order billing', 'work-order complete', 'work-order copy', 'work-order create', 'work-order history', 'work-order invoice', 'work-order query', 'work-order sales-receipt', 'work-order show', 'work-order update'), admin_action_keys=('admin:agents', 'admin:company:attach', 'admin:company:detach', 'admin:company:new', 'admin:demo:reset', 'admin:members:admin:company', 'admin:members:admin:organization', 'admin:members:owner:company', 'admin:members:owner:organization', 'admin:members:readonly:company', 'admin:members:readonly:organization', 'admin:members:standard:company', 'admin:members:standard:organization', 'admin:organization:new', 'admin:organization:rename', 'admin:users'), descriptor_sha256='7e213af6aca70c7836e11c59728ea33542d4528f001d6e1106c9472b414d3bd8')
