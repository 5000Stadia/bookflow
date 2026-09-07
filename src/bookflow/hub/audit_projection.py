"""Private semantic history projection; no registered wire/cursor cutover.

Authenticated reader ownership is mandatory. Stored audit bytes are never changed.
The legacy company adapter owns closed payload decoding, not authority decisions.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Literal
import json
import zlib
import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from bookflow.core import audit
from bookflow.core.errors import BookflowError
from bookflow.core.identity_admin_binding import BoundReader, ReaderIdentity
from . import permission_catalog as c, permission_policy as policy, schema as h


def format_error():
    raise BookflowError('E_VALIDATION', details={'reason':'audit_format'})


class FrozenView(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True, strict=True)


class ProjectedFailure(FrozenView):
    code: Literal['E_EVENT_NOT_FOUND','E_RECORD_NOT_FOUND','E_VALIDATION','E_PERMISSION']
    reason: Literal['audit_format','history_filter','invalid_cursor'] | None = None

    def error(self):
        return BookflowError(self.code,details={} if self.reason is None else {'reason':self.reason})

    @classmethod
    def capture(cls,error):
        if error.code in ('E_EVENT_NOT_FOUND','E_RECORD_NOT_FOUND','E_PERMISSION'):
            return cls(code=error.code)
        if error.code=='E_VALIDATION' and error.details in (
                {'reason':'audit_format'},{'reason':'history_filter'},{'reason':'invalid_cursor'}):
            return cls(code=error.code,reason=error.details['reason'])
        raise error


class MembershipView(FrozenView):
    tag: Literal['membership']='membership'
    id: str
    user_id: str | None
    scope_type: Literal['organization','company']
    scope_id: str
    role: Literal['readonly','standard','admin','owner']
    grants: tuple[str,...]
    denies: tuple[str,...]
    granted_at: str
    granted_by: str | None
    revoked_at: str | None


class UserView(FrozenView):
    tag: Literal['user']='user'
    id: str
    username: str | None = None
    display_name: str | None = None
    kind: Literal['human','agent','system'] | None = None
    active: bool | None = None
    hub_admin: bool | None = None
    owner_user_id: str | None = None
    timezone: str | None = None


class AssignmentView(FrozenView):
    principal_user_id: str
    assigned_by: str | None
    assigned_at: str
    revoked_at: str | None


class AuthorityView(FrozenView):
    tag: Literal['agent_authority']='agent_authority'
    agent_user_id: str
    epoch: int
    suspended_at: str | None
    suspension_reason: str | None
    authorized_at: str | None = None
    authorized_by: str | None = None
    permitted_use_at: str | None = None
    fresh_context_ack_at: str | None = None
    fresh_context_required: bool = False
    assignments: tuple[AssignmentView,...]
    transition_reasons: tuple[str,...]


class TokenView(FrozenView):
    tag: Literal['api_token']='api_token'
    id: str
    user_id: str | None = None
    kind: Literal['bearer','session'] | None = None
    on_behalf_of: str | None = None
    authority_epoch: int | None = None
    revoked_at: str | None = None
    created_at: str | None = None
    created_by: str | None = None
    created_via: str | None = None


class TokenRevocationView(FrozenView):
    tag: Literal['token_revocation']='token_revocation'
    revoked: Literal[True]=True


class CatalogDefaultView(FrozenView):
    role: str
    capability: str
    threshold: str


class CapabilityPolicyView(FrozenView):
    name: str
    company_thresholds: tuple[str,...]
    registered_thresholds: tuple[str,...]


class CompanyActionView(FrozenView):
    key: str
    requirements: tuple[c.Requirement,...]
    available: bool


class CatalogView(FrozenView):
    tag: Literal['permission_state']='permission_state'
    defaults: tuple[CatalogDefaultView,...]
    capabilities: tuple[CapabilityPolicyView,...]
    company_actions: tuple[CompanyActionView,...]
    admin_actions: tuple[c.AdminAction,...]


class RegistryView(FrozenView):
    tag: Literal['organization','company']
    id: str
    display_name: str
    organization_id: str | None = None
    is_demo: bool
    legal_name: str | None = None
    home_currency: str | None = None
    schema_revision: str | None = None
    path: str | None = None
    pending_path: str | None = None
    created_at: str | None = None
    created_by: str | None = None
    created_via: str | None = None
    updated_at: str | None = None
    updated_by: str | None = None
    updated_via: str | None = None


class RegistryCaptured(FrozenView):
    id: str
    version: int
    display_name: str
    name_key: str
    path: str
    pending_path: str | None
    is_demo: bool
    created_at: str
    created_by: str
    created_via: str
    updated_at: str
    updated_by: str
    updated_via: str


class CompanyRegistryCaptured(RegistryCaptured):
    organization_id: str
    legal_name: str
    home_currency: str
    schema_revision: str


class MigrationView(FrozenView):
    tag: Literal['migration']='migration'
    from_revision: str | None
    to_revision: str


class LegacyCommonCaptured(FrozenView):
    id: str
    version: int = Field(ge=1)
    created_at: str
    created_by: str
    created_via: str
    updated_at: str
    updated_by: str
    updated_via: str


class LegacyUserCaptured(LegacyCommonCaptured):
    kind: Literal['human','agent','system']
    username: str
    display_name: str
    owner_user_id: str | None = None
    password_hash: str | None
    hub_admin: bool
    timezone: str | None
    active: bool


class LegacyTokenCaptured(LegacyCommonCaptured):
    user_id: str
    on_behalf_of: str | None = None
    kind: Literal['bearer','session']
    label: str | None
    expires_at: str | None
    last_used_at: str | None
    revoked_at: str | None
    authority_epoch: int | None = None


class LegacyMembershipCaptured(FrozenView):
    id: str
    user_id: str
    scope_type: Literal['organization','company']
    scope_id: str
    role: Literal['readonly','standard','admin','owner']
    grants: str | None
    denies: str | None
    granted_by: str
    granted_at: str
    revoked_at: str | None
    # hub0012 added these columns; absence in old audit bytes remains absence.
    version: int | None = Field(None,ge=1)
    updated_at: str | None = None
    updated_by: str | None = None
    updated_via: str | None = None


@dataclass(frozen=True)
class AuditIdentity:
    company: str | None
    kind: str
    id: str


@dataclass(frozen=True)
class ProjectedEntry:
    id: str
    identity: AuditIdentity
    action: str
    before: FrozenView | None
    after: FrozenView | None
    changed_fields: tuple[str,...]
    version_before: int | None
    version_after: int | None


@dataclass(frozen=True)
class ProjectedEvent:
    id: str
    at: str
    command: str | None
    summary: str
    actor_id: str | None
    actor_name: str | None
    actor_kind: str | None
    principal_id: str | None
    principal_name: str | None
    interface: str
    entries: tuple[ProjectedEntry,...]


class HistorySelection(FrozenView):
    mode: Literal['show','list','tail','activity']
    company: str | None = None
    event: str | None = None
    anchor: str | None = None
    endpoint: str | None = None
    limit: int = 50
    scan_limit: int | None = None
    since: str | None = None
    until: str | None = None
    actor: str | None = None
    principal: str | None = None
    actor_kind: str | None = None
    interface: str | None = None
    command: str | None = None
    record_type: str | None = None
    record_id: str | None = None
    empty_start: bool = False
    entry_anchor: str | None = None
    kinds: tuple[Literal['audit','note','attachment'],...] | None = None

    @model_validator(mode='after')
    def closed_mode(self):
        for key in ('company','event','anchor','endpoint','record_type','record_id','actor','principal'):
            if getattr(self,key) is not None and not getattr(self,key):
                raise ValueError('empty history identity')
        if not 1<=self.limit<=1000 or (self.scan_limit is not None and not 1<=self.scan_limit<=1000):
            raise ValueError('invalid history bounds')
        if self.mode=='show':
            if not self.event or self.model_fields_set-{'mode','company','event'}:
                raise ValueError('show requires only an event and scope')
        elif self.event is not None:
            raise ValueError('event is show-only')
        if self.scan_limit is not None and self.mode!='tail':
            raise ValueError('scan limit is tail-only')
        if self.empty_start and (self.mode!='tail' or self.anchor is not None):
            raise ValueError('empty start is a tail continuation without an anchor')
        if self.mode=='activity' and (self.company is None or self.record_type is None or self.record_id is None):
            raise ValueError('activity requires a typed company record')
        if self.mode=='activity':
            if self.limit>200 or (self.anchor is None)!=(self.entry_anchor is None):
                raise ValueError('activity requires a complete entry anchor and bounded page')
            if self.kinds is not None and len(set(self.kinds))!=len(self.kinds):
                raise ValueError('duplicate activity kind')
        elif self.entry_anchor is not None or self.kinds is not None:
            raise ValueError('activity-only fields')
        return self


class ProjectedActivity(FrozenView):
    kind: Literal['audit','note','attachment']
    at: str
    event_id: str
    entry_id: str
    record_type: str
    record_id: str
    action: str
    command: str | None
    summary: str
    actor_id: str | None
    actor_name: str | None
    principal_id: str | None
    principal_name: str | None
    interface: str
    version_before: int | None
    version_after: int | None
    body: str | None = None
    caption: str | None = None
    attachment_id: str | None = None
    active: bool | None = None
    text_truncated: bool = False


@dataclass(frozen=True)
class ProjectedHistory:
    events: tuple[ProjectedEvent,...]
    next_anchor: str | None
    endpoint: str | None
    has_more: bool
    scanned_count: int
    scanned_more: bool
    # Internal immutable eligible evidence supporting scan and continuation.
    evidence: tuple[str,...]
    # Only decisions used by filtering/count/continuation. Bodies of unreturned
    # events are not disclosed dependencies of the response.
    predicates: tuple[tuple[str,bool],...] = ()
    activity_items: tuple[ProjectedActivity,...] = ()
    activity_dependencies: tuple[tuple[str,str],...] = ()
    next_entry_anchor: str | None = None
    activity: bool = False


class AuditAudience:
    def __init__(self, reader: BoundReader, *, read_capability="audit"):
        if type(reader) is not BoundReader:
            raise BookflowError('E_UNAUTHENTICATED')
        if read_capability not in ('audit','activity'):
            raise BookflowError('E_VALIDATION')
        self.read_capability=read_capability
        observed=reader.observe()
        self.reader=reader
        self.identity=observed.identity
        self.observation=observed.observation
        self.comparison=observed.observation.snapshot.comparison
        self._signatures={}
        self._origin_kinds={}

    def validate(self):
        if self.reader.authenticate()!=self.identity:
            raise BookflowError('E_UNAUTHENTICATED')

    def subjects(self):
        i=self.identity
        return (i.actor,) if i.principal is None else (i.actor,i.principal)

    def signature(self,subject,scope):
        if subject not in self.comparison.subjects or scope not in self.comparison.scopes:
            return None
        if subject not in self._signatures:
            self._signatures[subject]=policy.signature(self.comparison,phase='old',subject=subject)
        return next((x for x in self._signatures[subject].scopes if x.scope==scope),None)

    def visible(self,scope):
        return all((x:=self.signature(subject,scope)) is not None and x.effective_visible for subject in self.subjects())

    def administrator(self,scope):
        return self.visible(scope) and all((x:=self.signature(subject,scope)).membership_role in ('admin','owner') or x.hub_admin for subject in self.subjects())

    def global_admin(self):
        return self.identity.actor_kind=='human' and self.identity.hub_admin and self.identity.principal is None

    def identity_visible(self,identifier):
        return identifier is not None and (self.global_admin() or identifier in self.subjects())

    def require(self,company,requirements):
        scope=c.ScopeKey('company',company)
        if not self.visible(scope):raise BookflowError('E_PERMISSION')
        for capability,threshold in requirements:
            answer=policy.execution(self.comparison,phase='old',actor=self.identity.actor,
                bound_human=self.identity.principal,scope=scope,requirement=c.Requirement(capability,threshold))
            if not answer.intersection_admitted:raise BookflowError('E_PERMISSION')


def make_audience(reader, *, read_capability="audit"):
    return AuditAudience(reader, read_capability=read_capability)


def _decode(raw):
    try:
        if raw is not None and (type(raw) is not bytes or raw[:1] not in (audit.RAW,audit.ZIP)):
            format_error()
        if raw is None:return None
        payload=raw[1:]
        if raw[:1]==audit.ZIP:
            decoder=zlib.decompressobj()
            payload=decoder.decompress(payload)
            if not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:format_error()
        def object_(pairs):
            result={}
            for key,value in pairs:
                if key in result:format_error()
                result[key]=value
            return result
        value=json.loads(payload,object_pairs_hook=object_,parse_constant=lambda _:format_error())
        if value is not None and type(value) is not dict:format_error()
        return value
    except (ValueError,TypeError,UnicodeError,zlib.error):format_error()


def _row(value,kind):
    if value is None:return None
    if 'format' in value:
        extra={'agent_authority':{'assignments','transition_reasons'},
               'permission_state':{'state_key','catalog','defaults'}}.get(kind,set())
        if (set(value)!={'format','kind','row'}|extra or type(value['format']) is not int
                or value['format']!=1 or value['kind']!=kind or type(value['row']) is not dict):
            format_error()
        return value['row']
    return value


def _validate_b2(value,kind,identifier):
    """Reuse the producing owners' complete safe row contracts, before projection."""
    if 'format' not in value:return
    from . import permission_snapshot as snapshot, permission_admin_audit as producer
    from .agent_authority import TokenRow
    row=_row(value,kind)
    try:
        model={'membership':snapshot.MembershipRow,'user':producer.UserAuditRow,
               'api_token':TokenRow,'agent_authority':snapshot.AuthorityRow}.get(kind)
        if model is not None:
            decoded=snapshot._decode(row,model,'audit')
            if getattr(decoded,'agent_user_id',getattr(decoded,'id',None))!=identifier:format_error()
        elif kind=='permission_state':
            names={'id','generation','mode','catalog_version','catalog_sha256','updated_at','updated_by','updated_via'}
            if set(row)!=names or type(value['state_key']) is not int or value['state_key']!=1 or identifier!='1':format_error()
            snapshot._decode(dict(row,catalog_json=None),producer.StateRow,'audit')
            snapshot._decode(value['defaults'],tuple[c.DefaultEntry,...],'audit')
        else:format_error()
        if kind=='agent_authority':
            assignments=snapshot._decode(value['assignments'],tuple[snapshot.AssignmentRow,...],'audit')
            if any(x.agent_user_id!=identifier for x in assignments):format_error()
            snapshot._decode(value['transition_reasons'],tuple[str,...],'audit')
    except (ValueError,TypeError):format_error()


def _bool(value):
    if type(value) is bool:return value
    if type(value) is int and value in (0,1):return bool(value)
    format_error()


def _policies(value):
    if value is None:return ()
    if type(value) is str:
        try:value=json.loads(value)
        except ValueError:format_error()
    if type(value) is not list or any(type(x) is not str for x in value):format_error()
    return tuple(value)


def _hub_view(audience,kind,identifier,value):
    if value is None:return None
    _validate_b2(value,kind,identifier)
    row=_row(value,kind)
    if 'format' not in value and kind in ('membership','user','api_token'):
        try:
            if kind=='api_token' and set(row)=={'revoked'}:
                TokenRevocationView.model_validate(row)
            else:
                model={'membership':LegacyMembershipCaptured,'user':LegacyUserCaptured,'api_token':LegacyTokenCaptured}[kind]
                captured=model.model_validate(row)
                if captured.id!=identifier:format_error()
        except (ValueError,TypeError):format_error()
    def identity(key):
        result=row.get(key)
        return result if audience.identity_visible(result) else None
    try:
        if kind=='membership':
            scope=c.ScopeKey(row['scope_type'],row['scope_id'])
            if not audience.visible(scope):return None
            if row['user_id'] not in audience.subjects() and not audience.administrator(scope):return None
            return MembershipView(id=identifier,user_id=row['user_id'],scope_type=row['scope_type'],scope_id=row['scope_id'],
                role=row['role'],grants=_policies(row.get('grants')),denies=_policies(row.get('denies')),
                granted_at=row['granted_at'],granted_by=identity('granted_by'),revoked_at=row.get('revoked_at'))
        if kind=='user':
            if not audience.identity_visible(identifier):return None
            fields=dict(id=identifier,username=row.get('username'),display_name=row.get('display_name'),timezone=row.get('timezone'))
            if audience.global_admin():fields.update(kind=row.get('kind'),active=_bool(row['active']) if 'active' in row else None,
                hub_admin=_bool(row['hub_admin']) if 'hub_admin' in row else None,owner_user_id=identity('owner_user_id'))
            return UserView(**fields)
        if kind=='api_token':
            if set(row)=={'revoked'}:return TokenRevocationView(revoked=_bool(row['revoked']))
            if not audience.global_admin() and row.get('user_id')!=audience.identity.actor:return None
            fields={key:identifier if key=='id' else row.get(key) for key in TokenView.model_fields if key!='tag'}
            for key in ('created_by','user_id','on_behalf_of'):
                fields[key]=identity(key)
            return TokenView(**fields)
        if kind in ('organization','company'):
            if not audience.visible(c.ScopeKey(kind,identifier)):return None
            if 'schema_revision' in row and 'display_name' not in row:
                return MigrationView(from_revision=row.get('from'),to_revision=row['schema_revision'])
            model=CompanyRegistryCaptured if kind=='company' else RegistryCaptured
            captured=model.model_validate(dict(row,is_demo=_bool(row['is_demo'])))
            if captured.id!=identifier:format_error()
            parent=getattr(captured,'organization_id',None)
            if parent is not None and not audience.visible(c.ScopeKey('organization',parent)):parent=None
            full=audience.global_admin()
            return RegistryView(tag=kind,id=identifier,display_name=captured.display_name,
                organization_id=parent,is_demo=captured.is_demo,
                legal_name=getattr(captured,'legal_name',None),home_currency=getattr(captured,'home_currency',None),
                schema_revision=getattr(captured,'schema_revision',None),
                path=captured.path if full else None,pending_path=captured.pending_path if full else None,
                created_at=captured.created_at if full else None,created_by=captured.created_by if full else None,
                created_via=captured.created_via if full else None,updated_at=captured.updated_at if full else None,
                updated_by=captured.updated_by if full else None,updated_via=captured.updated_via if full else None)
        if kind=='hub' and audience.global_admin():
            return MigrationView(from_revision=row.get('from'),to_revision=row['schema_revision'])
        if kind=='agent_authority':
            if not audience.global_admin():return None
            fields={key:row[key] for key in AuthorityView.model_fields if key in row and key not in ('tag','assignments','transition_reasons')}
            if 'fresh_context_required' in fields:fields['fresh_context_required']=_bool(fields['fresh_context_required'])
            return AuthorityView(**fields,assignments=tuple(AssignmentView(**{key:item.get(key) for key in AssignmentView.model_fields}) for item in value['assignments']),transition_reasons=tuple(value['transition_reasons']))
        if kind=='permission_state':
            if not audience.global_admin():return None
            from .permission_snapshot import decode_catalog
            catalog=value['catalog']
            encoded=json.dumps(catalog,sort_keys=True,separators=(',',':'))
            import hashlib
            decoded=decode_catalog(encoded,version=catalog['version'],sha256=hashlib.sha256(encoded.encode()).hexdigest())
            defaults=tuple(CatalogDefaultView(role=x['role'],capability=x['requirement']['capability'],threshold=x['requirement']['threshold']) for x in value['defaults'])
            # Source inventories/digests are internal; policy publication fields
            # will use the explicit catalog-content projection below.
            return CatalogView(defaults=defaults,
                capabilities=tuple(CapabilityPolicyView(name=x.name,company_thresholds=x.company_thresholds,registered_thresholds=x.registered_thresholds) for x in decoded.capabilities),
                company_actions=tuple(CompanyActionView(key=x.key,requirements=x.requirements,available=x.available) for x in decoded.company_actions),
                admin_actions=decoded.admin_actions)
    except (KeyError,TypeError,ValueError,ValidationError):format_error()
    if audience.global_admin():format_error()
    return None


def _tables(audience,company):
    session=audience.reader.session
    if company is None:return session.hub,h.audit_events,h.audit_entries
    if session.company is None or session.company_row is None or session.company_row['id']!=company:
        raise BookflowError('E_PERMISSION')
    from bookflow.company import schema
    audience.require(company,((audience.read_capability,'member'),))
    return session.company,schema.audit_events,schema.audit_entries


def _hub_candidate(audience,entry):
    kind,identifier=entry['record_type'],entry['record_id']
    if kind in ('organization','company'):
        return audience.visible(c.ScopeKey(kind,identifier))
    if kind=='user':return audience.identity_visible(identifier)
    if kind in ('permission_state','agent_authority','hub'):return audience.global_admin()
    if kind=='membership':
        # Scope is an authenticated historical fact, never today's row.
        for raw in (entry['before'], entry['after']):
            try:
                row=_row(_decode(raw),'membership')
            except BookflowError:
                continue
            if row is None:continue
            scope=c.ScopeKey(row.get('scope_type'),row.get('scope_id'))
            if audience.visible(scope) and (row.get('user_id') in audience.subjects() or audience.administrator(scope)):
                return True
        return False
    if kind=='api_token':
        if audience.global_admin():return True
        if audience.identity.actor_kind!='human':return False
        # Token ownership is immutable historical evidence. Sparse logout rows
        # have no owner; a later physical expiry sweep must not erase their
        # owner's history or make today's token table an admission oracle.
        db=audience.reader.session.hub
        cutoff=db.conn.execute(sa.select(h.audit_events.c.seq).where(h.audit_events.c.id==entry['event_id'])).scalar_one()
        rows=db.conn.execute(sa.select(h.audit_entries.c.before,h.audit_entries.c.after).select_from(
            h.audit_entries.join(h.audit_events,h.audit_entries.c.event_id==h.audit_events.c.id)).where(
            h.audit_entries.c.record_type=='api_token',h.audit_entries.c.record_id==identifier,
            h.audit_events.c.seq<=cutoff)).all()
        owners=set()
        for pair in rows:
            for blob in pair:
                row=None
                try:
                    value=_decode(blob)
                    if value is None:continue
                    row=_row(value,kind)
                    if set(row)=={'revoked'}:continue
                    if 'format' in value:_validate_b2(value,kind,identifier)
                    else:
                        if LegacyTokenCaptured.model_validate(row).id!=identifier:format_error()
                    owners.add(row['user_id'])
                except (BookflowError,ValueError,TypeError):
                    # Unsupported evidence belonging to someone else does not
                    # disclose a format failure through an ordinary audience.
                    if isinstance(row,dict) and row.get('user_id')==audience.identity.actor:format_error()
        if audience.identity.actor not in owners:return False
        if len(owners)!=1:format_error()
        return True
    return audience.global_admin()


def _name(audience,identifier):
    if not audience.identity_visible(identifier):return None
    row=audience.reader.session.hub.conn.execute(sa.select(h.users.c.display_name).where(h.users.c.id==identifier)).scalar_one_or_none()
    return row


def _company_view(audience,event,entry,value):
    if value is None:return None
    from . import audit_projection_legacy as legacy
    decoded=legacy.decode_company_snapshot(producer=event['command'],record_type=entry['record_type'],action=entry['action'],snapshot=value)
    return _disclose_company(audience,audience.reader.session.company_row['id'],decoded,cutoff=event['seq'])


def _kind_allowed(audience,company,kind):
    from . import audit_projection_legacy as legacy
    try:
        audience.require(company,legacy.entry_requirement(kind))
        return True
    except BookflowError as exc:
        if exc.code=='E_PERMISSION':return False
        raise


def _disclose_company(audience,company,value,reference_kind=None,*,cutoff=None):
    """One closed typed traversal; no SQL reflection, string rewriting or ACLs."""
    from . import audit_projection_legacy as legacy
    if value is None:return None
    if type(value) is tuple:
        return tuple(_disclose_company(audience,company,item,reference_kind,cutoff=cutoff) for item in value)
    if not isinstance(value,legacy.View):return value
    if isinstance(value,legacy.CounterpartylinkView) and not all(
            _kind_allowed(audience,company,kind) for kind in ('customer','vendor')):
        return None
    updates={};partial=False
    if isinstance(value,legacy.Origin) and value.source_id is not None:
        # Legacy origins name a persisted default source without a kind tag.
        # Resolve only its immutable, already-existing audit owner; never use a
        # current label/definition, a guessed ULID namespace or a later creation.
        from bookflow.company import schema
        if cutoff is None:format_error()
        db=audience.reader.session.company
        cache_key=(company,cutoff,value.source_id)
        if cache_key not in audience._origin_kinds:
            audience._origin_kinds[cache_key]=frozenset(db.conn.execute(sa.select(schema.audit_entries.c.record_type).distinct().select_from(
                schema.audit_entries.join(schema.audit_events,schema.audit_entries.c.event_id==schema.audit_events.c.id)).where(
                schema.audit_entries.c.record_id==value.source_id,schema.audit_events.c.seq<=cutoff,
                schema.audit_entries.c.record_type.in_(('company_info','customer','item','price_level','customer_message','term')))).scalars())
        kinds=audience._origin_kinds[cache_key]
        if len(kinds)!=1:format_error()
        if not _kind_allowed(audience,company,next(iter(kinds))):
            updates['source_id']=None;partial=True
    # Internal normalized keys are never retained by a public proof. Secrets and
    # operational receipts make the record partial even when their value is null.
    for key in value._internal:
        if key in type(value).model_fields:updates[key]=None
    if value._internal & {'tax_id','provider_profile_ref','scan_cursor','warnings','source_basis_hash','conversion_key_hash','request_hash'}:partial=True
    groups=[]
    for base in type(value).__mro__:
        groups.extend(legacy._REFERENCE_GROUPS.get(base,()))
    if reference_kind is not None:
        groups.append((tuple(key for key in ('id','label','version') if key in type(value).model_fields),reference_kind))
    for fields,kind in groups:
        if not _kind_allowed(audience,company,kind):
            updates.update({key:None for key in fields});partial=True
    for base in type(value).__mro__:
        fields=legacy._POLYMORPHIC_PARTIES.get(base)
        if fields is None:continue
        kind=getattr(value,fields[0])
        identifier=getattr(value,fields[1])
        if kind is None and identifier is None:continue
        if kind not in ('customer','vendor','employee','other_name'):format_error()
        if not _kind_allowed(audience,company,kind):
            updates.update({key:None for key in fields});partial=True
    for key in ('created_by','updated_by','author_id','uploaded_by','linked_by','given_by','recorded_by','entered_by','deactivated_by','voided_by','accepted_by'):
        if key in type(value).model_fields:
            identifier=getattr(value,key)
            if identifier is not None and not audience.identity_visible(identifier):
                updates[key]=None;partial=True
    for key,field in type(value).model_fields.items():
        if key in updates or key=='projection_partial':continue
        item=getattr(value,key)
        if key in ('custom_values','values') and isinstance(value,(legacy.AggregateView,legacy.CustomCaptures)):
            if not _kind_allowed(audience,company,'custom_field'):
                updates[key]=None;partial=True;continue
        route=None
        for base in type(value).__mro__:
            route=legacy._OBJECT_REFERENCE_KINDS.get((base,key))
            if route is not None:break
        # Field admission applies to absent and populated captures alike. A
        # hidden reference becoming present must not expose its presence through
        # an empty object, collection size or version-only audit entry.
        required=next((legacy._OBJECT_FIELD_REQUIREMENTS[base,key] for base in type(value).__mro__
                       if (base,key) in legacy._OBJECT_FIELD_REQUIREMENTS),())
        if route is not None:required=(*required,route)
        if any(not _kind_allowed(audience,company,kind) for kind in required):
            updates[key]=None;partial=True;continue
        # Preserve omission in an entitled historical capture. Masked fields
        # above deliberately use one null shape even when originally absent.
        if key not in value.model_fields_set:
            continue
        projected=_disclose_company(audience,company,item,route,cutoff=cutoff)
        updates[key]=projected
        if isinstance(projected,legacy.View) and projected.projection_partial:partial=True
        if type(projected) is tuple and any(isinstance(x,legacy.View) and x.projection_partial for x in projected):partial=True
    if partial:
        updates.update({key:None for key in legacy._PARTIAL_PROVENANCE if key in type(value).model_fields})
    updates['projection_partial']=partial
    return value.model_copy(update=updates)


def _annotation_targets(audience,company,kind,identifier,seen=()):
    """Immutable annotation target columns precede body decoding and disclosure."""
    key=(kind,identifier)
    if key in seen:format_error()
    from bookflow.company import schema as schema
    from .audit_projection_legacy import entry_requirement
    audience.require(company,entry_requirement(kind))
    db=audience.reader.session.company
    if kind=='principal':
        if not audience.identity_visible(identifier):raise BookflowError('E_PERMISSION')
        return
    if kind in ('audit_event','audit_entry'):
        event_id=identifier if kind=='audit_event' else db.conn.execute(sa.select(schema.audit_entries.c.event_id)
            .where(schema.audit_entries.c.id==identifier)).scalar_one_or_none()
        if event_id is None:format_error()
        visible=project_event(audience,event_id,company=company,_seen_annotations=seen+(key,))
        if visible is None or (kind=='audit_entry' and not any(x.id==identifier for x in visible.entries)):
            raise BookflowError('E_PERMISSION')
        return
    if kind=='attachment':
        targets=db.conn.execute(sa.select(schema.attachment_links.c.record_type,schema.attachment_links.c.record_id)
            .where(schema.attachment_links.c.attachment_id==identifier)).all()
    elif kind in ('note','attachment_link','custom_field_value'):
        table={'note':schema.notes,'attachment_link':schema.attachment_links,'custom_field_value':schema.custom_field_values}[kind]
        targets=db.conn.execute(sa.select(table.c.record_type,table.c.record_id).where(table.c.id==identifier)).all()
        if not targets:format_error()
    else:return
    for target_kind,target_id in targets:
        _annotation_targets(audience,company,target_kind,target_id,seen+(key,))


def project_event(audience,event_id,*,company=None,requirements=None,_seen_annotations=()):
    audience.validate()
    db,events,entries=_tables(audience,company)
    event=db.conn.execute(sa.select(events).where(events.c.id==event_id)).mappings().one_or_none()
    if event is None:return None
    if company is not None:
        from bookflow.company import payment_authority
        try:
            actual=requirements if requirements is not None else payment_authority.event_requirements(db,event_id)
            # Keep existing whole-graph owner admission and add governed A rights.
            payment_authority.authorize_event(audience.reader.session,event_id,{event_id:actual})
            audience.require(company,actual)
        except BookflowError as exc:
            if exc.code=='E_PERMISSION':return None
            raise
    raw_entries=list(db.conn.execute(sa.select(entries).where(entries.c.event_id==event_id).order_by(entries.c.id)).mappings())
    projected=[]
    for entry in raw_entries:
        if company is None and not _hub_candidate(audience,entry):continue
        if company is not None:
            from .audit_projection_legacy import entry_requirement
            try:
                audience.require(company,entry_requirement(entry['record_type']))
                _annotation_targets(audience,company,entry['record_type'],entry['record_id'],_seen_annotations)
            except BookflowError as exc:
                if exc.code=='E_PERMISSION':continue
                raise
        before,after=_decode(entry['before']),_decode(entry['after'])
        if before is None and entry['action'] not in ('create','baseline','migrate','delete') and entry['version_before'] is not None:
            previous=list(db.conn.execute(sa.select(entries.c.after).select_from(
                entries.join(events,entries.c.event_id==events.c.id)).where(
                entries.c.record_type==entry['record_type'],entries.c.record_id==entry['record_id'],
                entries.c.version_after==entry['version_before'],events.c.seq<event['seq'])).scalars())
            if len(previous)!=1:format_error()
            before=_decode(previous[0])
        render=(lambda value:_hub_view(audience,entry['record_type'],entry['record_id'],value)) if company is None else (lambda value:_company_view(audience,event,entry,value))
        left,right=render(before),render(after)
        if left is None and right is None:continue
        if left==right and entry['action'] not in ('create','delete','baseline','migrate'):continue
        l={} if left is None else left.model_dump(mode='json',by_alias=True)
        r={} if right is None else right.model_dump(mode='json',by_alias=True)
        if l==r and entry['action'] not in ('create','delete','baseline','migrate'):continue
        differences=tuple(k for k in sorted(set(l)|set(r)) if k!='tag' and l.get(k)!=r.get(k))
        # Hub projections deliberately omit management-sensitive metadata. Whole
        # company graph views retain versions only after their full owner check.
        full=company is not None and not any(getattr(x,'projection_partial',False) for x in (left,right) if x is not None)
        projected.append(ProjectedEntry(entry['id'],AuditIdentity(company,entry['record_type'],entry['record_id']),entry['action'],left,right,differences,
                                        entry['version_before'] if full else None,entry['version_after'] if full else None))
    if not projected:
        if raw_entries:return None
        # Explicit supported hub no-entry initialization is management-domain only.
        if company is None and event['command']=='init' and audience.global_admin():
            command,summary='init','Local login mapping restored.'
        else:
            # Company no-entry adapters must enumerate the owning producer rather
            # than interpreting its unclassified free-text summary.
            if company is not None:
                from . import audit_projection_legacy as legacy
                summary=legacy.no_entry_summary(producer=event['command'])
                if summary is None:return None
                command=event['command']
            else:return None
    else:
        # Derived records alone reveal no original initiating command/free text.
        kinds=tuple(sorted({x.identity.kind for x in projected}))
        command=None
        initiating={'permission membership put':'membership','permission membership revoke':'membership',
                    'permission user active':'user','permission assignments set':'agent_authority',
                    'permission agent authorize':'agent_authority','permission catalog replace':'permission_state',
                    'token issue':'api_token','token revoke':'api_token','user set-password':'user',
                    'login':'api_token','logout':'api_token','session sweep':'api_token',
                    'organization new':'organization','organization rename':'organization',
                    'company new':'company','company rename':'company','company detach':'company'}
        if company is not None:
            verb=event['command'].split(' ',1)[0]
            expected={'journal':'journal_entry','invoice':'invoice','sales-receipt':'sales_receipt',
                      'payment':'payment','deposit':'deposit'}.get(verb)
            if expected is not None and any(e.identity.kind=='transaction' and
                    getattr(e.after or e.before,'type',None)==expected for e in projected):
                command=event['command']
            from .audit_projection_legacy import _LIST_NOUNS
            if any(_LIST_NOUNS.get(e.identity.kind)==verb for e in projected):
                command=event['command']
            if event['command'].startswith('payment selection ') and any(
                    e.identity.kind=='payment_selection' for e in projected):
                command=event['command']
        if initiating.get(event['command']) in kinds:
            command=event['command']
        summary=(command+'.') if command is not None else ', '.join(kinds)+' updated.' 
    actor=event['actor_id'] if audience.identity_visible(event['actor_id']) else None
    principal=event['on_behalf_of'] if audience.identity_visible(event['on_behalf_of']) else None
    from bookflow.core.session import localize
    result=ProjectedEvent(event_id,localize(audience.reader.session,event['at']),command,summary,actor,_name(audience,actor),
        event['actor_kind'] if actor is not None else None,principal,_name(audience,principal),event['interface'],tuple(projected))
    audience.validate()
    return result


def _matches(event,selection):
    pairs=((selection.actor,event.actor_id),(selection.principal,event.principal_id),
           (selection.actor_kind,event.actor_kind),(selection.interface,event.interface),(selection.command,event.command))
    if any(wanted is not None and wanted!=actual for wanted,actual in pairs):return False
    from bookflow.core.clock import parse_iso
    if selection.since is not None and parse_iso(event.at)<parse_iso(selection.since):return False
    if selection.until is not None and parse_iso(event.at)>=parse_iso(selection.until):return False
    if selection.record_type is not None or selection.record_id is not None:
        return any((selection.record_type is None or e.identity.kind==selection.record_type) and
                   (selection.record_id is None or e.identity.id==selection.record_id) for e in event.entries)
    return True


def _visible_events(audience,company,*,descending,lower=None,upper=None):
    """Bounded SQL blocks; stop decoding once the semantic page is complete."""
    db,events,_=_tables(audience,company)
    last=None
    while True:
        q=sa.select(events.c.id,events.c.seq)
        if lower is not None:q=q.where(events.c.seq>lower)
        if upper is not None:q=q.where(events.c.seq<=upper)
        if last is not None:q=q.where(events.c.seq<last if descending else events.c.seq>last)
        rows=list(db.conn.execute(q.order_by(events.c.seq.desc() if descending else events.c.seq).limit(200)))
        if not rows:return
        cohort=None
        if company is not None:
            from bookflow.company.payment_authority import _EventCohort
            cohort=_EventCohort(db,[row.id for row in rows])
        for row in rows:
            value=project_event(audience,row.id,company=company,
                requirements=None if cohort is None else cohort.requirements(row.id))
            if value is not None:yield row.seq,value
        last=rows[-1].seq


def _visible_anchor(audience,selection,identifier):
    value=project_event(audience,identifier,company=selection.company)
    if value is None:raise BookflowError('E_VALIDATION',details={'reason':'invalid_cursor'})
    db,events,_=_tables(audience,selection.company)
    sequence=db.conn.execute(sa.select(events.c.seq).where(events.c.id==identifier)).scalar_one()
    return sequence,value


def normalized_selection(audience,selection):
    if type(selection) is not HistorySelection:
        raise BookflowError('E_VALIDATION')
    if selection.mode!='show':
        from bookflow.core.dispatch import parse_when
        session=audience.reader.session
        zone=session.actor.timezone or session.company_tz
        try:
            since=parse_when(selection.since,zone) if selection.since is not None else None
            until=parse_when(selection.until,zone,end=True) if selection.until is not None else None
        except BookflowError:
            raise BookflowError('E_VALIDATION',details={'reason':'history_filter'}) from None
        if since is not None and until is not None and since>=until:
            raise BookflowError('E_VALIDATION',details={'reason':'history_filter'})
        selection=selection.model_copy(update={'since':since,'until':until})
    return selection


def project_history(audience,selection):
    audience.validate()
    selection=normalized_selection(audience,selection)
    if selection.mode=='activity':return _project_activity(audience,selection)
    if selection.mode=='show':
        event=project_event(audience,selection.event,company=selection.company)
        if event is None:raise BookflowError('E_EVENT_NOT_FOUND')
        return ProjectedHistory((event,),None,event.id,False,0,False,(event.id,))
    evidence=[]
    if selection.endpoint is not None:
        endseq,endpoint=_visible_anchor(audience,selection,selection.endpoint)
    else:
        newest=next(_visible_events(audience,selection.company,descending=True),None)
        endseq,endpoint=(None,None) if newest is None else newest
    if endpoint is not None:evidence.append(endpoint.id)
    anchorseq=None
    if selection.anchor is not None:
        anchorseq,anchor=_visible_anchor(audience,selection,selection.anchor)
        if endseq is None or anchorseq>endseq:
            raise BookflowError('E_VALIDATION',details={'reason':'invalid_cursor'})
        evidence.append(anchor.id)
    if selection.mode=='tail' and selection.anchor is None and not selection.empty_start:
        key=endpoint.id if endpoint is not None else None
        return ProjectedHistory((),key,key,False,0,False,tuple(evidence))
    if endpoint is None:
        return ProjectedHistory((),None,None,False,0,False,())
    descending=selection.mode=='list'
    upper=min(endseq,anchorseq-1) if descending and anchorseq is not None else endseq
    lower=anchorseq if not descending else None
    matched=[];scanned=[];more=False;predicates=[]
    budget=min(selection.limit,selection.scan_limit) if selection.mode=='tail' and selection.scan_limit is not None else None
    for _,event in _visible_events(audience,selection.company,descending=descending,lower=lower,upper=upper):
        evidence.append(event.id)
        if budget is not None:
            if len(scanned)==budget:more=True;break
            scanned.append(event)
            matches=_matches(event,selection);predicates.append((event.id,matches))
            if matches:matched.append(event)
        else:
            matches=_matches(event,selection);predicates.append((event.id,matches))
            if matches:
                if len(matched)==selection.limit:more=True;break
                matched.append(event)
    if budget is not None:
        next_anchor=scanned[-1].id if scanned else selection.anchor
    else:
        next_anchor=matched[-1].id if matched and (more or selection.mode=='tail') else None
    audience.validate()
    return ProjectedHistory(tuple(matched),next_anchor,endpoint.id,more,
        len(scanned),more if budget is not None else False,tuple(dict.fromkeys(evidence)),tuple(predicates))


def activity_item(event,entry):
    """An excerpt of the sole projected entry, never a second raw decoder."""
    import json
    from bookflow.commands.activity_cmds import PAGE_BYTES
    from . import audit_projection_legacy as legacy
    kind='note' if entry.identity.kind=='note' else 'attachment' if entry.identity.kind in ('attachment','attachment_link') else 'audit'
    fields=dict(kind=kind,at=event.at,event_id=event.id,entry_id=entry.id,
        record_type=entry.identity.kind,record_id=entry.identity.id,action=entry.action,
        command=event.command,summary=event.summary,actor_id=event.actor_id,actor_name=event.actor_name,
        principal_id=event.principal_id,principal_name=event.principal_name,interface=event.interface,
        version_before=entry.version_before,version_after=entry.version_after)
    capture=entry.after if entry.after is not None else entry.before
    if kind=='note':
        if type(capture) is not legacy.NoteView:format_error()
        fields['body']=capture.body
    elif kind=='attachment':
        if type(capture) is legacy.AttachmentLinkView:
            fields.update(caption=capture.caption,attachment_id=capture.attachment_id,active=capture.active)
        elif type(capture) is not legacy.AttachmentView:format_error()
    item=ProjectedActivity(**fields)
    while len(json.dumps(item.model_dump()).encode())>PAGE_BYTES-8192:
        key='body' if item.body is not None else 'caption'
        value=getattr(item,key)
        if not value:format_error()
        item=item.model_copy(update={key:value[:len(value)//2],'text_truncated':True})
    return item


def _activity_target(audience,selection):
    from bookflow.company import records,payment_authority
    from .audit_projection_legacy import entry_requirement
    audience.require(selection.company,entry_requirement(selection.record_type))
    key=records.resolve(audience.reader.session,selection.record_type,selection.record_id)
    _annotation_targets(audience,selection.company,selection.record_type,key)
    roots=payment_authority.record_transactions(audience.reader.session.company,selection.record_type,key)
    if roots:audience.require(selection.company,payment_authority.requirements(audience.reader.session.company,roots))
    return key


def _activity_rows(audience,selection,key,*,newest=False,upper=None,after=None):
    from bookflow.commands.activity_cmds import _candidates
    db,events,entries=_tables(audience,selection.company)
    last=None
    while True:
        q=sa.select(events.c.id.label('event_id'),entries.c.id.label('entry_id'),events.c.at,events.c.seq).select_from(
            entries.join(events,entries.c.event_id==events.c.id)).where(entries.c.id.in_(_candidates(selection,key)))
        if upper is not None:q=q.where(events.c.seq<=upper)
        columns=(events.c.seq,entries.c.id) if newest else (events.c.at,events.c.id,entries.c.id)
        position=last if last is not None else after
        if position is not None:
            q=q.where(sa.tuple_(*columns)<sa.tuple_(*position) if newest else sa.tuple_(*columns)>sa.tuple_(*position))
        rows=list(db.conn.execute(q.order_by(*(x.desc() if newest else x for x in columns)).limit(200)).mappings())
        if not rows:return
        from bookflow.company.payment_authority import _EventCohort
        cohort=_EventCohort(db,tuple(dict.fromkeys(x['event_id'] for x in rows)))
        projected={}
        for row in rows:
            if row['event_id'] not in projected:
                projected[row['event_id']]=project_event(audience,row['event_id'],company=selection.company,
                    requirements=cohort.requirements(row['event_id']))
            event=projected[row['event_id']]
            if event is None:continue
            entry=next((x for x in event.entries if x.id==row['entry_id']),None)
            if entry is not None:yield row,event,entry
        row=rows[-1]
        last=(row['seq'],row['entry_id']) if newest else (row['at'],row['event_id'],row['entry_id'])


def _activity_anchor(audience,selection,key,event_id,entry_id=None):
    from bookflow.commands.activity_cmds import _candidates
    db,events,entries=_tables(audience,selection.company)
    event=project_event(audience,event_id,company=selection.company)
    if event is not None:
        candidate_ids=set(db.conn.execute(sa.select(entries.c.id).where(
            entries.c.event_id==event_id,entries.c.id.in_(_candidates(selection,key)))).scalars())
        eligible=next((x for x in event.entries if x.id in candidate_ids and (entry_id is None or x.id==entry_id)),None)
        if eligible is not None:
            row=db.conn.execute(sa.select(events.c.seq,events.c.at).where(events.c.id==event_id)).one()
            return row.seq,(row.at,event_id,eligible.id),eligible.id
    raise BookflowError('E_VALIDATION',details={'reason':'invalid_cursor'})


def _project_activity(audience,selection):
    import json
    from bookflow.commands.activity_cmds import PAGE_BYTES
    key=_activity_target(audience,selection)
    dependencies=[];evidence=[];predicates=[]
    if selection.endpoint is not None:
        upper,_,entry_id=_activity_anchor(audience,selection,key,selection.endpoint)
        endpoint=selection.endpoint
    else:
        newest=next(_activity_rows(audience,selection,key,newest=True),None)
        if newest is None:
            if selection.anchor is not None:raise BookflowError('E_VALIDATION',details={'reason':'invalid_cursor'})
            return ProjectedHistory((),None,None,False,0,False,(),activity=True)
        row,event,entry=newest;upper=row['seq'];endpoint=event.id;entry_id=entry.id
    dependencies.append((endpoint,entry_id));evidence.append(endpoint)
    after=None
    if selection.anchor is not None:
        anchor_seq,after,anchor_entry=_activity_anchor(audience,selection,key,selection.anchor,selection.entry_anchor)
        if anchor_seq>upper:raise BookflowError('E_VALIDATION',details={'reason':'invalid_cursor'})
        dependencies.append((selection.anchor,anchor_entry));evidence.append(selection.anchor)
    # Target selection is performed by the immutable annotation owner above.
    # Event filters run only after the entry has survived the sole projection.
    filters=selection.model_copy(update={'record_type':None,'record_id':None})
    items=[];size=0;more=False
    for _,event,entry in _activity_rows(audience,selection,key,upper=upper,after=after):
        dependencies.append((event.id,entry.id));evidence.append(event.id)
        matches=_matches(event,filters);predicates.append((event.id,matches))
        if not matches:continue
        item=activity_item(event,entry)
        item_size=len(json.dumps(item.model_dump()).encode())+2
        if len(items)==selection.limit or size+item_size>PAGE_BYTES-4096:
            more=True;break
        items.append(item);size+=item_size
    audience.validate()
    return ProjectedHistory((),items[-1].event_id if more and items else None,endpoint,more,0,False,
        tuple(dict.fromkeys(evidence)),tuple(dict.fromkeys(predicates)),tuple(items),
        tuple(dict.fromkeys(dependencies)),items[-1].entry_id if more and items else None,True)
