"""Private complete hub facts for policy calculations; no runtime activation.

The caller supplies a reviewed build bundle, a consistent existing hub transaction
and explicit governed visibility. No registry discovery, path opening or writes.
"""
from __future__ import annotations
from dataclasses import asdict, dataclass, fields, is_dataclass, replace
import hashlib
import json
import re
import sqlite3
from types import UnionType
from typing import Literal, Protocol, Union, get_args, get_origin, get_type_hints
from . import permission_catalog as c, permission_policy as a
from bookflow.storage.engine import Database
from bookflow.storage.migrate import HEADS


class SnapshotError(ValueError):
    """Static categories/locations only; never echo private supplied facts."""
    def __init__(self, code: str, field: str):
        self.code, self.field = code, field
        super().__init__(code, field)


def _fail(code, field):
    raise SnapshotError(code, field)


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def _digest(value):
    return hashlib.sha256(_json(value).encode('utf-8')).hexdigest()


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _fail('invalid_json', 'catalog')
        result[key] = value
    return result


def _parse(raw, field):
    if type(raw) is not str:
        _fail('invalid_json', field)
    try:
        return json.loads(raw, object_pairs_hook=_object,
                          parse_constant=lambda value: _fail('invalid_json', field))
    except (ValueError, RecursionError, UnicodeError):
        _fail('invalid_json', field)


def _decode(value, annotation, field, *, native=False):
    origin, args = get_origin(annotation), get_args(annotation)
    if origin in (Union, UnionType):
        for option in args:
            try:
                return _decode(value, option, field, native=native)
            except SnapshotError:
                pass
        _fail('invalid_type', field)
    if origin is Literal:
        if not any(type(value) is type(x) and value == x for x in args):
            _fail('invalid_type', field)
        return value
    if origin is tuple:
        if type(value) not in ((tuple,) if native else (list, tuple)):
            _fail('invalid_type', field)
        if len(args) == 2 and args[1] is Ellipsis:
            return tuple(_decode(x, args[0], field, native=native) for x in value)
        if len(value) != len(args):
            _fail('invalid_type', field)
        return tuple(_decode(x, kind, field, native=native) for x, kind in zip(value, args))
    if is_dataclass(annotation):
        hints = get_type_hints(annotation)
        if native:
            if type(value) is not annotation:
                _fail('invalid_type', field)
            for key, kind in hints.items():
                _decode(getattr(value, key), kind, field, native=True)
            return value
        if type(value) is not dict or set(value) != set(hints):
            _fail('invalid_fields', field)
        return annotation(**{key: _decode(value[key], kind, field) for key, kind in hints.items()})
    if type(value) is not annotation:
        _fail('invalid_type', field)
    return value


def encode_catalog(catalog: c.Catalog) -> str:
    try:
        return _json(asdict(c._normal_catalog(catalog)))
    except (c.PolicyInputError, UnicodeError, ValueError, TypeError, RecursionError):
        _fail('invalid_catalog', 'catalog')


def decode_catalog(raw: str, *, version: str, sha256: str) -> c.Catalog:
    try:
        catalog = c._normal_catalog(_decode(_parse(raw, 'catalog'), c.Catalog, 'catalog'))
        manifest = c.catalog_manifest(catalog)
        if catalog.version != version or manifest.descriptor_sha256 != sha256:
            _fail('catalog_mismatch', 'catalog')
        return catalog
    except (c.PolicyInputError, UnicodeError, TypeError, RecursionError):
        _fail('invalid_catalog', 'catalog')


@dataclass(frozen=True, slots=True)
class CatalogBundle:
    source_commit: str
    descriptor: c.Catalog
    exclusions: tuple[str, ...]
    source_inventory_digest: str


def _bundle(bundle):
    if type(bundle) is not CatalogBundle or type(bundle.source_commit) is not str or type(bundle.source_inventory_digest) is not str or not re.fullmatch('[0-9a-f]{40}', bundle.source_commit) or not re.fullmatch('[0-9a-f]{64}', bundle.source_inventory_digest):
        _fail('catalog_mismatch', 'bundle')
    try:
        manifest = c.catalog_manifest(bundle.descriptor, bundle.exclusions)
        return _digest(dict(source_commit=bundle.source_commit, manifest=asdict(manifest), inventory=bundle.source_inventory_digest))
    except (c.PolicyInputError, TypeError, UnicodeError):
        _fail('catalog_mismatch', 'bundle')


@dataclass(frozen=True, slots=True)
class UserRow:
    id: str
    kind: c.SubjectKind
    active: bool
    hub_admin: bool
    owner_user_id: str | None
    version: int


@dataclass(frozen=True, slots=True)
class OrganizationRow:
    id: str
    version: int
    pending_path: str | None


@dataclass(frozen=True, slots=True)
class CompanyRow:
    id: str
    organization_id: str
    version: int
    pending_path: str | None


@dataclass(frozen=True, slots=True)
class MembershipRow:
    id: str
    user_id: str
    scope_type: Literal['organization', 'company']
    scope_id: str
    role: c.Role
    grants: str | None
    denies: str | None
    granted_by: str
    granted_at: str
    revoked_at: str | None
    version: int
    updated_at: str | None
    updated_by: str | None
    updated_via: str | None


@dataclass(frozen=True, slots=True)
class AssignmentRow:
    agent_user_id: str
    principal_user_id: str
    assigned_by: str
    assigned_at: str
    revoked_at: str | None


@dataclass(frozen=True, slots=True)
class AuthorityRow:
    agent_user_id: str
    epoch: int
    suspended_at: str | None
    suspension_reason: str | None
    version: int
    updated_at: str | None
    updated_by: str | None
    updated_via: str | None
    authorized_at: str | None
    authorized_by: str | None
    permitted_use_at: str | None
    fresh_context_ack_at: str | None
    fresh_context_required: bool


@dataclass(frozen=True, slots=True)
class SourceKeys:
    users: tuple[str, ...]
    organizations: tuple[str, ...]
    companies: tuple[tuple[str, str], ...]
    memberships: tuple[tuple[str, str, str, str], ...]
    assignments: tuple[tuple[str, str], ...]
    authorities: tuple[str, ...]
    defaults: tuple[tuple[str, str, str], ...]


@dataclass(frozen=True, slots=True)
class ReadStamp:
    hub_revision: str
    generation: int
    mode: Literal['legacy', 'policy_v1']
    catalog_sha256: str | None
    authority_rows_digest: str
    bundle_digest: str


@dataclass(frozen=True, slots=True)
class RootFacts:
    stamp: ReadStamp
    keys: SourceKeys
    users: tuple[UserRow, ...]
    organizations: tuple[OrganizationRow, ...]
    companies: tuple[CompanyRow, ...]
    memberships: tuple[MembershipRow, ...]
    assignments: tuple[AssignmentRow, ...]
    authorities: tuple[AuthorityRow, ...]
    role_defaults: tuple[c.DefaultEntry, ...]
    catalog: c.Catalog


@dataclass(frozen=True, slots=True)
class ProposalRows:
    """Complete replacement/upsert rows; omitted identities remain unchanged."""
    users: tuple[UserRow, ...] = ()
    memberships: tuple[MembershipRow, ...] = ()
    assignments: tuple[AssignmentRow, ...] = ()
    authorities: tuple[AuthorityRow, ...] = ()


@dataclass(frozen=True, slots=True)
class DerivedFacts:
    """Derived keys, not a SQL observation or authenticated database provenance."""
    root: RootFacts
    base_stamp: ReadStamp
    provenance: Literal['derived_from_loaded_old'] = 'derived_from_loaded_old'


@dataclass(frozen=True, slots=True)
class VisibilityFacts:
    policy_revision: str
    rows: tuple[a.Visibility, ...]


class VisibilityProvider(Protocol):
    def facts(self, root: RootFacts, union_scopes: tuple[c.ScopeKey, ...],
              union_subjects: tuple[str, ...]) -> VisibilityFacts: ...


@dataclass(frozen=True, slots=True)
class SnapshotPair:
    old: RootFacts
    proposed: RootFacts
    comparison: a.ValidatedComparison
    visibility_policy_revision: str


@dataclass(frozen=True, slots=True)
class ScopeRows:
    organizations: tuple[OrganizationRow, ...] = ()
    companies: tuple[CompanyRow, ...] = ()
    remove_organizations: tuple[str, ...] = ()
    remove_companies: tuple[str, ...] = ()
    remove_memberships: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ScopeObservation:
    scope: c.ScopeKey
    raw_present: bool
    logical_present: bool
    raw_parent: str | None
    pending_path: str | None
    retirement: Literal['none', 'own', 'parent', 'own_and_parent']


@dataclass(frozen=True, slots=True)
class LogicalObservation:
    source_stamp: ReadStamp
    scopes: tuple[ScopeObservation, ...]
    memberships: tuple[a.MembershipSlot, ...]
    live_organizations: tuple[str, ...]
    live_companies: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class ObservedPair:
    snapshot: SnapshotPair
    old_observation: LogicalObservation
    new_observation: LogicalObservation


def _retired(pending):
    """Platform-independent lexical classification, preserving the raw value."""
    if pending is None:
        return False
    if type(pending) is not str or not pending or '\x00' in pending or re.match(r'^[A-Za-z]:', pending):
        _fail('invalid_facts', 'pending_path')
    parts = re.split(r'[\\/]', pending)
    if any(part in ('', '.', '..') for part in parts) or parts == ['trash']:
        _fail('invalid_facts', 'pending_path')
    return parts[0] == 'trash'


def _retirement_sets(orgs, companies):
    retired_orgs = {x.id for x in orgs if _retired(x.pending_path)}
    retired_companies = {x.id for x in companies if _retired(x.pending_path) or x.organization_id in retired_orgs}
    return retired_orgs, retired_companies


# Independently selected key/parent projections, not extracted from the row query.
_KEY_SQL = (
    ('users', 'SELECT id FROM main.users ORDER BY id'),
    ('organizations', 'SELECT id FROM main.organizations ORDER BY id'),
    ('companies', 'SELECT id,organization_id FROM main.companies ORDER BY id'),
    ('memberships', 'SELECT id,user_id,scope_type,scope_id FROM main.memberships ORDER BY id'),
    ('assignments', 'SELECT agent_user_id,principal_user_id FROM main.agent_principals ORDER BY agent_user_id,principal_user_id'),
    ('authorities', 'SELECT agent_user_id FROM main.agent_authority ORDER BY agent_user_id'),
    ('defaults', 'SELECT role,capability,required_role FROM main.role_capabilities ORDER BY role,capability,required_role'),
)


def _read_rows(raw, table, kind):
    names = tuple(field.name for field in fields(kind))
    records = []
    order = 'agent_user_id,principal_user_id' if kind is AssignmentRow else names[0]
    for values in raw.execute('SELECT '+','.join(names)+' FROM main.'+table+' ORDER BY '+order):
        values = dict(zip(names, values))
        for name, annotation in get_type_hints(kind).items():
            if annotation is bool:
                if type(values[name]) is not int or values[name] not in (0, 1):
                    _fail('invalid_facts', table)
                values[name] = bool(values[name])
        records.append(_decode(values, kind, table))
    return tuple(records)


def _policy(row, catalog):
    try:
        values = []
        for raw in (row.grants, row.denies):
            value = [] if raw is None else _parse(raw, 'memberships')
            if type(value) is not list:
                _fail('legacy_policy_invalid', 'memberships')
            values.append(tuple(value))
        return a.normalize_policy(*values, catalog=catalog)
    except (SnapshotError, c.PolicyInputError):
        _fail('legacy_policy_invalid', 'memberships')


def _keys(rows):
    users, orgs, companies, members, assignments, authorities, defaults = rows
    return SourceKeys(tuple(sorted(x.id for x in users)), tuple(sorted(x.id for x in orgs)),
        tuple(sorted((x.id, x.organization_id) for x in companies)),
        tuple(sorted((x.id,x.user_id,x.scope_type,x.scope_id) for x in members)),
        tuple(sorted((x.agent_user_id,x.principal_user_id) for x in assignments)),
        tuple(sorted(x.agent_user_id for x in authorities)),
        tuple(sorted((x.role,x.requirement.capability,x.requirement.threshold) for x in defaults)))


def _validate(rows, keys, catalog):
    if _keys(rows) != keys:
        _fail('source_incomplete', 'root')
    for key in fields(keys):
        values = getattr(keys, key.name)
        if len(set(values)) != len(values):
            _fail('invalid_facts', 'keys')
    users, orgs, companies, members, assignments, authorities, defaults = rows
    uu = {x.id:x for x in users}; oo = {x.id for x in orgs}; cc = {x.id for x in companies}
    if len(cc) != len(companies) or any(x.organization_id not in oo for x in companies):
        _fail('invalid_facts', 'companies')
    for collection in rows[:6]:
        for row in collection:
            for name in ('id','agent_user_id','principal_user_id','user_id','scope_id'):
                if hasattr(row,name) and (type(getattr(row,name)) is not str or not getattr(row,name)):
                    _fail('invalid_facts','identity')
            for name in ('version','epoch'):
                if hasattr(row,name) and not (type(getattr(row,name)) is int and 1 <= getattr(row,name) <= 9223372036854775807):
                    _fail('invalid_facts','version')
    for row in (*orgs, *companies):
        _retired(row.pending_path)
    member_keys = set()
    for row in members:
        key = (row.user_id,row.scope_type,row.scope_id)
        if key in member_keys:
            _fail('invalid_facts','memberships')
        member_keys.add(key)
        if row.revoked_at is None and (row.user_id not in uu or row.scope_id not in (oo if row.scope_type=='organization' else cc)):
            _fail('invalid_facts','memberships')
        _policy(row, catalog)  # Full preflight includes revoked policies too.
    for row in assignments:
        if row.agent_user_id not in uu or uu[row.agent_user_id].kind != 'agent' or row.principal_user_id not in uu or uu[row.principal_user_id].kind != 'human':
            _fail('invalid_facts','assignments')
    agents = {x.id for x in users if x.kind=='agent'}
    if set(keys.authorities) != agents:
        _fail('invalid_facts','authorities')


def _rows_digest(rows):
    return _digest([asdict(x) for group in rows for x in group])


def load_root(tx: Database, *, catalog: CatalogBundle) -> RootFacts:
    try:
        return _load_root(tx, catalog=catalog)
    except (sqlite3.DatabaseError, UnicodeError, RecursionError):
        _fail('invalid_facts', 'root')


def _load_root(tx: Database, *, catalog: CatalogBundle) -> RootFacts:
    """Read all facts from a caller-owned, already open SQLite transaction."""
    bundle_digest = _bundle(catalog)
    if not isinstance(tx, Database) or not tx.raw.in_transaction:
        _fail('snapshot_required', 'transaction')
    raw = tx.raw
    revision = raw.execute('SELECT version_num FROM main.alembic_version').fetchall()
    if revision != [(HEADS['hub'],)]:
        _fail('schema_mismatch', 'revision')
    state = raw.execute('SELECT id,generation,mode,catalog_version,catalog_sha256,catalog_json FROM main.permission_state').fetchall()
    if len(state)!=1 or state[0][0]!=1 or type(state[0][1]) is not int or not 1 <= state[0][1] <= 9223372036854775807:
        _fail('invalid_facts','state')
    _, generation, mode, version, sha, encoded = state[0]
    if mode not in ('legacy','policy_v1'):
        _fail('invalid_facts','state')
    key_rows = {name: tuple(raw.execute(sql).fetchall()) for name,sql in _KEY_SQL}
    keys = SourceKeys(**{name: tuple(x[0] for x in values) if name in ('users','organizations','authorities') else values for name,values in key_rows.items()})
    rows = tuple(_read_rows(raw, table, kind) for table,kind in (
        ('users',UserRow), ('organizations',OrganizationRow), ('companies',CompanyRow),
        ('memberships',MembershipRow), ('agent_principals',AssignmentRow), ('agent_authority',AuthorityRow)))
    # Separate materialization from the independently captured default keys.
    defaults = tuple(c.DefaultEntry(role,c.Requirement(capability,threshold)) for role,capability,threshold in raw.execute('SELECT role,capability,required_role FROM main.role_capabilities'))
    try:
        actual = c._normal_catalog(replace(catalog.descriptor, defaults=defaults))
    except c.PolicyInputError:
        _fail('legacy_policy_invalid','defaults')
    rows = (*rows, actual.defaults)
    _validate(rows,keys,actual)
    if mode=='legacy':
        if (version,sha,encoded)!=(None,None,None):
            _fail('catalog_mismatch','state')
    else:
        stored = decode_catalog(encoded, version=version, sha256=sha)
        if stored != actual:
            _fail('catalog_mismatch','state')
    facts_digest = _rows_digest(rows)
    return RootFacts(ReadStamp(HEADS['hub'],generation,mode,sha,facts_digest,bundle_digest), keys, *rows, actual)


def _validated_root(root, bundle):
    _decode(root, RootFacts, 'root', native=True)
    if root.stamp.hub_revision != HEADS['hub']:
        _fail('schema_mismatch', 'revision')
    if not 1 <= root.stamp.generation <= 9223372036854775807:
        _fail('invalid_facts', 'state')
    if type(root) is not RootFacts or root.stamp.bundle_digest != _bundle(bundle):
        _fail('catalog_mismatch','bundle')
    if root.stamp.mode!='policy_v1':
        _fail('legacy_comparison_unavailable','mode')
    rows = (root.users,root.organizations,root.companies,root.memberships,root.assignments,root.authorities,root.role_defaults)
    _validate(rows,root.keys,root.catalog)
    if _rows_digest(rows) != root.stamp.authority_rows_digest or root.catalog != c._normal_catalog(replace(bundle.descriptor,defaults=root.role_defaults)) or c.catalog_manifest(root.catalog).descriptor_sha256 != root.stamp.catalog_sha256:
        _fail('source_incomplete','root')

def _patch_rows(original, changes, *, field, key, immutable=(), insert=False):
    records = {key(row): row for row in original}
    seen = set()
    for row in changes:
        identity = key(row)
        if identity in seen:
            _fail('invalid_facts', field)
        seen.add(identity)
        previous = records.get(identity)
        if previous is None:
            if not insert:
                _fail('invalid_facts', field)
        elif any(getattr(previous, name) != getattr(row, name) for name in immutable):
            _fail('invalid_facts', field)
        records[identity] = row
    return tuple(records[identity] for identity in sorted(records))


def _scope_rows(old, scopes, changes):
    if scopes is None:
        return old.organizations, old.companies, old.memberships, old.keys
    _decode(scopes, ScopeRows, 'scopes', native=True)
    removals = (scopes.remove_organizations, scopes.remove_companies, scopes.remove_memberships)
    originals = (old.keys.organizations, tuple(x[0] for x in old.keys.companies), tuple(x[0] for x in old.keys.memberships))
    patches = (scopes.organizations, scopes.companies, changes.memberships)
    for removed, original, patch in zip(removals, originals, patches):
        if len(set(removed)) != len(removed) or not set(removed) <= set(original) or set(removed) & {x.id for x in patch}:
            _fail('invalid_facts', 'scope_removals')
    removed_orgs, removed_companies, removed_members = map(set, removals)
    if any(x.organization_id in removed_orgs and x.id not in removed_companies for x in old.companies):
        _fail('invalid_facts', 'scope_removals')
    expected_members = {x.id for x in old.memberships if x.scope_id in (removed_orgs if x.scope_type == 'organization' else removed_companies)}
    if removed_members != expected_members:
        _fail('invalid_facts', 'scope_removals')
    orgs = _patch_rows(tuple(x for x in old.organizations if x.id not in removed_orgs), scopes.organizations,
                       field='organizations', key=lambda x: x.id, insert=True)
    companies = _patch_rows(tuple(x for x in old.companies if x.id not in removed_companies), scopes.companies,
                            field='companies', key=lambda x: x.id, immutable=('organization_id',), insert=True)
    retired_orgs, retired_companies = _retirement_sets(old.organizations, old.companies)
    new_retired_orgs, new_retired_companies = _retirement_sets(orgs, companies)
    for original, patch, retired, new_retired in (
        (old.organizations, scopes.organizations, retired_orgs, new_retired_orgs),
        (old.companies, scopes.companies, retired_companies, new_retired_companies)):
        prior = {x.id: x for x in original}
        for row in patch:
            previous = prior.get(row.id)
            if previous is None and row.id in new_retired:
                _fail('invalid_facts', 'retirement')
            if previous is not None and row.id in retired and row.pending_path != previous.pending_path:
                _fail('invalid_facts', 'retirement')
    # Transform OLD independently selected keys separately from row materialization.
    org_keys = (set(old.keys.organizations) - removed_orgs) | {x.id for x in scopes.organizations}
    company_keys = {i: parent for i, parent in old.keys.companies if i not in removed_companies}
    company_keys.update((x.id, x.organization_id) for x in scopes.companies)
    keys = replace(old.keys, organizations=tuple(sorted(org_keys)), companies=tuple(sorted(company_keys.items())),
                   memberships=tuple(x for x in old.keys.memberships if x[0] not in removed_members))
    return orgs, companies, tuple(x for x in old.memberships if x.id not in removed_members), keys


def derive_proposal(old: RootFacts, *, old_catalog: CatalogBundle,
                    new_catalog: CatalogBundle, changes: ProposalRows,
                    generation: int,
                    full_defaults: tuple[c.DefaultEntry, ...] | None = None) -> DerivedFacts:
    """Pure ordinary proposal; scope rosters and pending paths remain unchanged."""
    return _derive(old, old_catalog, new_catalog, changes, generation, full_defaults, None)


def derive_scope_proposal(old: RootFacts, *, old_catalog: CatalogBundle,
                          new_catalog: CatalogBundle, scopes: ScopeRows,
                          changes: ProposalRows, generation: int,
                          full_defaults: tuple[c.DefaultEntry, ...] | None = None) -> DerivedFacts:
    """Pure structural projection, never operation admission or SQL observation.

    Initiating and final effects must both use the same trusted loaded old root.
    No public dataclass or matching digest authenticates that origin/freshness.
    """
    if type(scopes) is not ScopeRows:
        _fail('invalid_type', 'scopes')
    return _derive(old, old_catalog, new_catalog, changes, generation, full_defaults, scopes)


def _derive(old, old_catalog, new_catalog, changes, generation, full_defaults, scopes):
    try:
        _validated_root(old, old_catalog)
        _decode(changes, ProposalRows, 'changes', native=True)
        _decode(generation, int, 'generation', native=True)
        if not 1 <= generation <= 9223372036854775807 or generation not in (old.stamp.generation, old.stamp.generation + 1):
            _fail('invalid_facts', 'generation')
        bundle_digest = _bundle(new_catalog)
        defaults = old.role_defaults if full_defaults is None else _decode(full_defaults, tuple[c.DefaultEntry, ...], 'defaults', native=True)
        actual = c._normal_catalog(replace(new_catalog.descriptor, defaults=defaults))
        orgs, companies, original_members, original_keys = _scope_rows(old, scopes, changes)
        users = _patch_rows(old.users, changes.users, field='users', key=lambda x: x.id,
                            immutable=('kind', 'hub_admin', 'owner_user_id'))
        members = _patch_rows(original_members, changes.memberships, field='memberships', key=lambda x: x.id,
                              immutable=('user_id', 'scope_type', 'scope_id'), insert=True)
        assignments = _patch_rows(old.assignments, changes.assignments, field='assignments',
                                 key=lambda x: (x.agent_user_id, x.principal_user_id), insert=True)
        authorities = _patch_rows(old.authorities, changes.authorities, field='authorities', key=lambda x: x.agent_user_id)
        # Expected proposal keys start from independently read OLD keys. Only
        # explicit allowed insertions/default replacement can change this set.
        member_keys = set(original_keys.memberships)
        member_ids = {x[0] for x in member_keys}
        for row in changes.memberships:
            if row.id not in member_ids:
                if row.user_id not in old.keys.users or row.scope_id not in (original_keys.organizations if row.scope_type == 'organization' else dict(original_keys.companies)):
                    _fail('invalid_facts', 'memberships')
                member_keys.add((row.id, row.user_id, row.scope_type, row.scope_id))
        keys = replace(original_keys, memberships=tuple(sorted(member_keys)),
                       assignments=tuple(sorted(set(old.keys.assignments) | {(x.agent_user_id, x.principal_user_id) for x in changes.assignments})),
                       defaults=tuple(sorted((x.role, x.requirement.capability, x.requirement.threshold) for x in defaults)))
        rows = (users, orgs, companies, members, assignments, authorities, actual.defaults)
        _validate(rows, keys, actual)
        if scopes is not None:
            retired_orgs, retired_companies = _retirement_sets(orgs, companies)
            prior = {x.id: x for x in old.memberships}
            for row in changes.memberships:
                previous = prior.get(row.id)
                if previous is None or (previous.revoked_at is not None and row.revoked_at is None):
                    if row.scope_id in (retired_orgs if row.scope_type == 'organization' else retired_companies):
                        _fail('invalid_facts', 'memberships')
        stamp = ReadStamp(old.stamp.hub_revision, generation, 'policy_v1',
                          c.catalog_manifest(actual).descriptor_sha256, _rows_digest(rows), bundle_digest)
        return DerivedFacts(RootFacts(stamp, keys, *rows, actual), old.stamp)
    except (c.PolicyInputError, ValueError, TypeError, AttributeError, RecursionError) as exc:
        if isinstance(exc, SnapshotError):
            raise
        _fail('invalid_facts', 'proposal')


def _observe(root, scopes, subjects):
    orgs = {x.id: x for x in root.organizations}
    companies = {x.id: x for x in root.companies}
    retired_orgs, retired_companies = _retirement_sets(root.organizations, root.companies)
    observed = []
    for scope in scopes:
        if scope.kind == 'hub':
            observed.append(ScopeObservation(scope, True, True, None, None, 'none'))
            continue
        row = (companies if scope.kind == 'company' else orgs).get(scope.id)
        own = bool(row and _retired(row.pending_path))
        parent = bool(row and scope.kind == 'company' and row.organization_id in retired_orgs)
        reason = 'own_and_parent' if own and parent else 'own' if own else 'parent' if parent else 'none'
        observed.append(ScopeObservation(scope, row is not None, row is not None and not (own or parent),
                        row.organization_id if row and scope.kind == 'company' else None,
                        row.pending_path if row else None, reason))
    live = {x.scope for x in observed if x.logical_present}
    members = {(x.user_id, c.ScopeKey(x.scope_type, x.scope_id)): x for x in root.memberships if x.revoked_at is None}
    slots = tuple(a.MembershipSlot(who, scope,
        a.Membership(members[who, scope].role, _policy(members[who, scope], root.catalog))
        if who in root.keys.users and scope in live and (who, scope) in members else None)
        for who in subjects for scope in scopes if scope.kind in ('organization', 'company'))
    live_orgs = tuple(sorted(set(orgs) - retired_orgs))
    live_companies = tuple(sorted((x.id, x.organization_id) for x in root.companies if x.id not in retired_companies))
    if {x.scope.id for x in observed if x.scope.kind == 'organization' and x.logical_present} != set(live_orgs) or {
            (x.scope.id, x.raw_parent) for x in observed if x.scope.kind == 'company' and x.logical_present} != set(live_companies):
        _fail('source_incomplete', 'observation')
    return LogicalObservation(root.stamp, tuple(observed), slots, live_orgs, live_companies)


def observe_pair(old: RootFacts, proposed: RootFacts, *, old_catalog: CatalogBundle,
                 new_catalog: CatalogBundle, visibility: VisibilityProvider | None) -> ObservedPair:
    """Complete raw-union observations and the supported live-union A comparison."""
    for root, bundle in ((old, old_catalog), (proposed, new_catalog)):
        _validated_root(root, bundle)
    if visibility is None or not callable(getattr(visibility, 'facts', None)):
        _fail('visibility_unresolved', 'visibility')
    raw_orgs = set(old.keys.organizations) | set(proposed.keys.organizations)
    raw_companies = {x[0] for root in (old, proposed) for x in root.keys.companies}
    subjects = tuple(sorted(set(old.keys.users) | set(proposed.keys.users)))
    agents = sorted({x.id for root in (old, proposed) for x in root.users if x.kind == 'agent'})
    raw_scopes = tuple(sorted((c.ScopeKey('hub', 'root'),
        *(c.ScopeKey('organization', x) for x in raw_orgs),
        *(c.ScopeKey('future_company', x) for x in raw_orgs),
        *(c.ScopeKey('company', x) for x in raw_companies)), key=c._scope_key))
    observations = tuple(_observe(root, raw_scopes, subjects) for root in (old, proposed))
    orgs = sorted({x for obs in observations for x in obs.live_organizations})
    companies = sorted({x[0] for obs in observations for x in obs.live_companies})
    scopes = {c.ScopeKey('hub', 'root'), *(c.ScopeKey('organization', x) for x in orgs),
              *(c.ScopeKey('future_company', x) for x in orgs), *(c.ScopeKey('company', x) for x in companies)}
    manifests = []; phases = []; revisions = []
    for root, bundle, obs in zip((old, proposed), (old_catalog, new_catalog), observations):
        supplied = visibility.facts(root, raw_scopes, subjects)
        if (type(supplied) is not VisibilityFacts or type(supplied.policy_revision) is not str
                or not supplied.policy_revision or type(supplied.rows) is not tuple):
            _fail('visibility_unresolved', 'visibility')
        _decode(supplied, VisibilityFacts, 'visibility', native=True)
        values = {(x.subject, x.scope): x for x in supplied.rows}
        present = {x.scope for x in obs.scopes if x.logical_present}
        if len(values) != len(supplied.rows) or set(values) != {(who, scope) for who in subjects for scope in raw_scopes}:
            _fail('visibility_unresolved', 'visibility')
        if any(x.visible and (x.subject not in root.keys.users or x.scope not in present) for x in supplied.rows):
            _fail('visibility_unresolved', 'visibility')
        revisions.append(supplied.policy_revision)
        manifests.append(a.PhaseManifest(obs.live_organizations, obs.live_companies,
            tuple((x.id, x.kind) for x in root.users), c.catalog_manifest(root.catalog, bundle.exclusions)))
        uu = {x.id: x for x in root.users}; cc = dict(obs.live_companies); aa = {x.agent_user_id: x for x in root.authorities}
        phases.append(a.Phase(root.catalog,
            tuple(a.OrganizationSlot(x, x in obs.live_organizations) for x in orgs),
            tuple(a.CompanySlot(x, x in cc, cc.get(x)) for x in companies),
            tuple(a.SubjectSlot(x, a.Subject(uu[x].kind, uu[x].active, uu[x].hub_admin) if x in uu else None) for x in subjects),
            tuple(x for x in obs.memberships if x.scope in scopes),
            tuple(x for x in supplied.rows if x.scope in scopes),
            tuple(a.AgentSlot(x, a.AgentState(aa[x].suspended_at is not None,
                tuple(sorted(y.principal_user_id for y in root.assignments if y.agent_user_id == x and y.revoked_at is None and uu[y.principal_user_id].active))) if x in aa else None) for x in agents)))
    if revisions[0] != revisions[1]:
        _fail('visibility_unresolved', 'visibility')
    try:
        comparison = a.validate_comparison(a.ComparisonInput(a.Expected(*manifests), *phases))
    except c.PolicyInputError as exc:
        _fail('invalid_comparison', exc.field)
    return ObservedPair(SnapshotPair(old, proposed, comparison, revisions[0]), *observations)


def assemble_pair(old: RootFacts, proposed: RootFacts, *, old_catalog: CatalogBundle,
                  new_catalog: CatalogBundle, visibility: VisibilityProvider | None) -> SnapshotPair:
    """Strict policy comparison; legacy activation and live governance remain external."""
    return observe_pair(old, proposed, old_catalog=old_catalog, new_catalog=new_catalog,
                        visibility=visibility).snapshot
