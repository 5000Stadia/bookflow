"""Private conditional B2 services; no current route, OS producer or activation.

The coordinator supplies a current binding, governed visibility and an existing
transaction (BEGIN IMMEDIATE for apply), owns enclosing rollback and commit, and
must separately implement C's audit projection/publication ordering before use.
"""
from __future__ import annotations
from dataclasses import asdict, dataclass, field, fields, replace
from pathlib import Path
from typing import Literal
import hashlib
import json
import sqlite3
import struct
from . import permission_snapshot as s, permission_catalog as c, permission_policy as a
from . import credentials, schema as h, agent_authority as aa, permission_admin_audit as audit_owner
from .agent_authority import AdministrationError, fail, typed, integer, increment, identity
from .permission_admin_audit import AuditContext
from bookflow.storage.engine import Database
from bookflow.core.ids import new_id


@dataclass(frozen=True, slots=True)
class Absent:
    pass


@dataclass(frozen=True, slots=True)
class Version:
    value: int


@dataclass(frozen=True, slots=True)
class PutMembership:
    user_id: str
    scope: c.ScopeKey
    expected: Absent | Version
    role: c.Role
    grants: tuple[str,...] | None = None
    denies: tuple[str,...] | None = None


@dataclass(frozen=True, slots=True)
class RevokeMembership:
    membership_id: str
    expected_version: int


@dataclass(frozen=True, slots=True)
class SetUserActive:
    user_id: str
    expected_version: int
    active: bool


@dataclass(frozen=True, slots=True)
class SetAssignments:
    agent_id: str
    expected_authority_version: int
    expected_epoch: int
    principals: tuple[str,...]
    permitted_use_confirmed: bool


@dataclass(frozen=True, slots=True)
class AuthorizeAgent:
    agent_id: str
    expected_authority_version: int
    expected_epoch: int
    permitted_use_confirmed: bool
    fresh_context_ack: bool


@dataclass(frozen=True, slots=True)
class ReplaceCatalog:
    expected_catalog_sha256: str
    new_bundle: s.CatalogBundle
    full_defaults: tuple[c.DefaultEntry,...]


Edit = PutMembership | RevokeMembership | SetUserActive | SetAssignments | AuthorizeAgent | ReplaceCatalog


@dataclass(frozen=True, slots=True)
class TokenBinding:
    secret: str = field(repr=False)
    token_id: str
    user_id: str
    kind: Literal['bearer','session']
    principal_id: str | None
    root: Path
    request_id: str


class OSOperation:
    """Lifetime guard only. No identity/config producer or authentication factory.

    Trusted B3/C must establish kernel identity, uninterrupted root serialization,
    post-dequeue pending-config precedence and mapping before supplying OSBinding.
    A savepoint proves this operation still belongs to the original transaction;
    Database identity/in_transaction alone cannot detect COMMIT then BEGIN reuse.
    """
    def __init__(self, tx: Database, *, request_id: str, purpose: Literal['preview','apply']):
        self.tx, self.root, self.request_id, self.purpose = tx, tx.path, request_id, purpose
        self._name='b2_os_'+new_id();self._active=False;self._used=False

    def __enter__(self):
        if self._used or not self.tx.raw.in_transaction or self.purpose not in ('preview','apply'):
            fail('invalid_input','binding')
        self._used=True;self.tx.raw.execute('SAVEPOINT '+self._name);self._active=True
        return self

    def validate(self,tx,request_id,purpose):
        if not self._active or tx is not self.tx or tx.path != self.root or request_id != self.request_id or purpose != self.purpose or not tx.raw.in_transaction:
            fail('invalid_input','binding')
        try:
            tx.raw.execute('RELEASE SAVEPOINT '+self._name)
            tx.raw.execute('SAVEPOINT '+self._name)
        except sqlite3.DatabaseError:
            self._active=False;fail('invalid_input','binding')

    def __exit__(self,*exc):
        try:
            if self._active:
                try:self.tx.raw.execute('RELEASE SAVEPOINT '+self._name)
                except sqlite3.DatabaseError:pass
        finally:self._active=False


@dataclass(frozen=True, slots=True)
class OSBinding:
    """Conditional trusted producer input, never a from-Context/bare-login proof."""
    operation: OSOperation = field(repr=False)
    admitted_user_id: str
    mapped_user_id: str


TrustedBinding = TokenBinding | OSBinding


@dataclass(frozen=True, slots=True)
class VisibleEdit:
    kind: str
    target_id: str | None
    version: int | None
    changed: bool
    prospective: bool


@dataclass(frozen=True, slots=True)
class Mutation:
    table: str
    key: tuple[tuple[str,object],...]
    expected: tuple[tuple[str,object],...]
    values: tuple[tuple[str,object],...]
    insert: bool = False
    delete: bool = False


@dataclass(frozen=True, slots=True)
class PreparedEdit:
    """Internal safe effects, not executable cached authority or a public output."""
    visible: VisibleEdit
    old: s.RootFacts
    initiating: s.DerivedFacts
    final: s.DerivedFacts
    pair: s.SnapshotPair
    reconciliation: aa.ReconciliationEffects
    old_tokens: tuple[aa.TokenRow,...]
    final_tokens: tuple[aa.TokenRow,...]
    old_state: audit_owner.StateRow
    final_state: audit_owner.StateRow
    old_users: tuple[audit_owner.UserAuditRow,...]
    final_users: tuple[audit_owner.UserAuditRow,...]
    catalog: s.CatalogBundle
    mutations: tuple[Mutation,...]
    audit: audit_owner.PreparedAudit | None


@dataclass(frozen=True, slots=True)
class EditEffects:
    visible: VisibleEdit
    event_id: str | None
    private: PreparedEdit = field(repr=False)


def _require_tx(tx,write=False):
    if not isinstance(tx,Database) or not tx.raw.in_transaction or (write and not tx.write_transaction):
        fail('invalid_input','transaction')
    # Existing credential owner uses unqualified names. Fail closed rather than
    # changing its eligibility rules or allowing TEMP to redefine this hub.
    local=tx.raw.execute("SELECT name FROM temp.sqlite_master WHERE type IN ('table','view')").fetchall()
    if any(name.lower() in h.metadata.tables for (name,) in local):fail('unsupported_schema','transaction')


def _actor(tx,binding,request_id,purpose):
    if type(binding) is TokenBinding:
        if type(binding.secret) is not str or not binding.secret or binding.root != tx.path or binding.request_id != request_id:
            fail('invalid_input','binding')
        current=credentials.resolve_token(tx,binding.secret)
        if (current['id'],current['user_id'],current['kind'],current['on_behalf_of']) != (binding.token_id,binding.user_id,binding.kind,binding.principal_id):
            fail('not_administrator','binding')
        actor_id=current['user_id']
        del current  # Full credential row/hash is never carried into prepared effects.
    elif type(binding) is OSBinding:
        if type(binding.operation) is not OSOperation:fail('invalid_input','binding')
        binding.operation.validate(tx,request_id,purpose)
        if binding.admitted_user_id != binding.mapped_user_id:fail('not_administrator','binding')
        actor_id=binding.mapped_user_id
    else:fail('invalid_input','binding')
    row=tx.raw.execute('SELECT id,kind,active,hub_admin FROM main.users WHERE id=?',(actor_id,)).fetchone()
    if row is None or row[1]!='human' or row[2]!=1:fail('not_administrator','binding')
    return row[0]


def _scope_admin(old,base_pair,actor,scope,owner=False):
    if scope.kind not in ('organization','company'):fail('invalid_input','scope')
    identity(scope.id,'scope')
    present=scope.id in (old.keys.organizations if scope.kind=='organization' else dict(old.keys.companies))
    visible=next((x.visible for x in base_pair.comparison.old.visibility if x.subject==actor.id and x.scope==scope),False)
    if not present or not visible:fail('unavailable_target','scope')
    if actor.hub_admin:return
    parent=dict(old.keys.companies).get(scope.id) if scope.kind=='company' else None
    roles=[x.role for x in old.memberships if x.user_id==actor.id and x.revoked_at is None and (
        (x.scope_type==scope.kind and x.scope_id==scope.id) or (parent is not None and x.scope_type=='organization' and x.scope_id==parent))]
    rank=max((c.ROLES.index(x) for x in roles),default=-1)
    if rank<c.ROLES.index('owner' if owner else 'admin'):fail('not_administrator','scope')


def _expected(actual,expected,field):
    integer(expected,field)
    if actual!=expected:fail('conflict',field)


def _policy(member,catalog):
    return a.normalize_policy(None if member.grants is None else tuple(json.loads(member.grants)),
                              None if member.denies is None else tuple(json.loads(member.denies)),catalog=catalog)


def _target(old,identifier,*,agent=False):
    identity(identifier,'target')
    row=next((x for x in old.users if x.id==identifier),None)
    if row is None:fail('unavailable_target','target')
    if row.kind=='system':fail('protected_identity','target')
    if agent and (row.kind!='agent' or not row.active):fail('invalid_assignment','agent')
    return row


def _translate_snapshot(exc):
    if exc.code == 'invalid_comparison' and exc.field == 'visibility':
        fail('visibility_unresolved','visibility')
    category={'schema_mismatch':'unsupported_schema','legacy_comparison_unavailable':'activation_required',
              'legacy_policy_invalid':'legacy_policy_invalid','catalog_mismatch':'catalog_mismatch',
              'visibility_unresolved':'visibility_unresolved'}.get(exc.code,'invalid_input')
    fail(category,'facts')


def _pair(old,proposed,old_catalog,new_catalog,visibility):
    try:return s.assemble_pair(old,proposed,old_catalog=old_catalog,new_catalog=new_catalog,visibility=visibility)
    except s.SnapshotError as exc:_translate_snapshot(exc)
    except c.PolicyInputError:fail('invalid_input','facts')
    except Exception:fail('visibility_unresolved','visibility')


def _mutation(table,before,after,key_names):
    b={} if before is None else asdict(before);v=asdict(after)
    key=tuple((x,v[x]) for x in key_names)
    expected=tuple((x,b[x]) for x in ('version','epoch','generation') if x in b)
    if table=='agent_principals' and before is not None:expected=tuple(b.items())
    values=tuple((x,value) for x,value in v.items() if x not in key_names and (before is None or b[x]!=value))
    return Mutation(table,key,expected,tuple(v.items()) if before is None else values,before is None)


def _prepare(tx,*,actor_id,intent,catalog,visibility,context=None):
    preview=context is None
    _require_tx(tx,not preview)
    if type(intent) not in (PutMembership,RevokeMembership,SetUserActive,SetAssignments,AuthorizeAgent,ReplaceCatalog):fail('invalid_input','intent')
    typed(intent,type(intent),'intent')
    try:old=s.load_root(tx,catalog=catalog)
    except s.SnapshotError as exc:_translate_snapshot(exc)
    if old.stamp.mode!='policy_v1':fail('activation_required','mode')
    old_state=audit_owner.read_state(tx);old_tokens=aa.read_tokens(tx);old_users=audit_owner.read_users(tx)
    actor=next(x for x in old.users if x.id==actor_id)
    base_pair=_pair(old,old,catalog,catalog,visibility)
    at='prospective' if preview else context.at
    via='python' if preview else context.interface
    updates=dict(updated_at=at,updated_by=actor_id,updated_via=via)
    users=[];members=[];assignments=[];assignment_agents=();addition_agents=();authorize=None
    permitted=False;ack=False;new_catalog=catalog;defaults=None;extra_revocations=();target_id=None;version=None;semantic=False
    if type(intent) is PutMembership:
        _scope_admin(old,base_pair,actor,intent.scope)
        _target(old,intent.user_id)
        before=next((x for x in old.memberships if x.user_id==intent.user_id and (x.scope_type,x.scope_id)==(intent.scope.kind,intent.scope.id)),None)
        _scope_admin(old,base_pair,actor,intent.scope,owner=intent.role=='owner' or bool(before and before.revoked_at is None and before.role=='owner'))
        if type(intent.expected) is Absent:
            if before is not None:fail('conflict','membership_version')
        else:
            integer(intent.expected.value,'membership_version')
            if before is None:fail('conflict','membership_version')
            _expected(before.version,intent.expected.value,'membership_version')
        try:policy=a.normalize_policy(intent.grants,intent.denies,catalog=old.catalog)
        except c.PolicyInputError:fail('invalid_input','policy')
        semantic=before is None or before.revoked_at is not None or before.role!=intent.role or _policy(before,old.catalog)!=policy
        if before is not None:target_id=before.id;version=before.version
        if semantic:
            if before is None:
                identifier=new_id() if not preview else next('prospective-'+str(i) for i in range(len(old.memberships)+1) if 'prospective-'+str(i) not in {x.id for x in old.memberships})
                after=s.MembershipRow(identifier,intent.user_id,intent.scope.kind,intent.scope.id,intent.role,
                    json.dumps(policy.grants,separators=(',',':')),json.dumps(policy.denies,separators=(',',':')),
                    actor_id,at,None,1,at,actor_id,via)
            else:
                after=replace(before,role=intent.role,grants=json.dumps(policy.grants,separators=(',',':')),
                    denies=json.dumps(policy.denies,separators=(',',':')),revoked_at=None,
                    granted_by=actor_id if before.revoked_at else before.granted_by,
                    granted_at=at if before.revoked_at else before.granted_at,
                    version=increment(before.version,'membership_version'),**updates)
            members=[after];target_id=None if preview and before is None else after.id;version=after.version
    elif type(intent) is RevokeMembership:
        identity(intent.membership_id,'target')
        before=next((x for x in old.memberships if x.id==intent.membership_id),None)
        if before is None:fail('unavailable_target','scope')
        scope=c.ScopeKey(before.scope_type,before.scope_id)
        _scope_admin(old,base_pair,actor,scope,owner=before.revoked_at is None and before.role=='owner')
        _target(old,before.user_id);_expected(before.version,intent.expected_version,'membership_version')
        semantic=before.revoked_at is None;target_id=before.id;version=before.version
        if semantic:
            members=[replace(before,revoked_at=at,version=increment(before.version,'membership_version'),**updates)];version=members[0].version
    else:
        if not actor.hub_admin:fail('not_administrator','hub')
        if type(intent) is SetUserActive:
            before=_target(old,intent.user_id);_expected(before.version,intent.expected_version,'user_version')
            semantic=before.active!=intent.active;target_id=before.id;version=before.version
            if semantic:
                if not intent.active and before.kind=='human' and before.hub_admin and sum(x.kind=='human' and x.active and x.hub_admin for x in old.users)<=1:fail('protected_identity','last_administrator')
                users=[replace(before,active=intent.active,version=increment(before.version,'user_version'))];version=users[0].version
                if not intent.active:extra_revocations=(before.id,)
        elif type(intent) in (SetAssignments,AuthorizeAgent):
            _target(old,intent.agent_id,agent=True);before=next(x for x in old.authorities if x.agent_user_id==intent.agent_id)
            _expected(before.version,intent.expected_authority_version,'authority_version');_expected(before.epoch,intent.expected_epoch,'authority_epoch')
            target_id=before.agent_user_id;version=before.version;permitted=intent.permitted_use_confirmed
            if type(intent) is AuthorizeAgent:authorize=intent.agent_id;ack=intent.fresh_context_ack
            else:
                if len(set(intent.principals))!=len(intent.principals):fail('invalid_input','principals')
                prior={x.principal_user_id:x for x in old.assignments if x.agent_user_id==intent.agent_id}
                current={k for k,x in prior.items() if x.revoked_at is None};desired=set(intent.principals)
                additions=desired-current
                for identifier in intent.principals:
                    person=_target(old,identifier)
                    if person.kind!='human' or (identifier in additions and not person.active):fail('invalid_assignment','principals')
                for identifier in sorted(current-desired):assignments.append(replace(prior[identifier],revoked_at=at))
                for identifier in sorted(additions):assignments.append(s.AssignmentRow(intent.agent_id,identifier,actor_id,at,None))
                semantic=bool(assignments)
                if semantic:assignment_agents=(intent.agent_id,)
                if additions:addition_agents=(intent.agent_id,)
        else:
            if intent.expected_catalog_sha256 != old.stamp.catalog_sha256:fail('conflict','catalog')
            new_catalog=intent.new_bundle;defaults=intent.full_defaults
    patch=s.ProposalRows(tuple(users),tuple(members),tuple(assignments))
    try:initiating=s.derive_proposal(old,old_catalog=catalog,new_catalog=new_catalog,changes=patch,generation=old.stamp.generation,full_defaults=defaults)
    except s.SnapshotError as exc:_translate_snapshot(exc)
    pair=_pair(old,initiating.root,catalog,new_catalog,visibility)
    if pair.visibility_policy_revision!=base_pair.visibility_policy_revision or pair.comparison.old.visibility!=base_pair.comparison.old.visibility:
        fail('visibility_unresolved','visibility')
    reconciliation=aa.reconcile(old,initiating.root,pair=pair,actor_id=actor_id,at=at,via=via,
        assignment_agents=assignment_agents,addition_agents=addition_agents,authorize_agent=authorize,
        permitted_use_confirmed=permitted,fresh_context_ack=ack)
    semantic=semantic or bool(reconciliation.authorities) or initiating.root.catalog!=old.catalog
    if type(intent) is ReplaceCatalog:
        semantic=semantic or initiating.root.stamp.bundle_digest!=old.stamp.bundle_digest
    generation=increment(old.stamp.generation,'generation') if semantic else old.stamp.generation
    try:final=s.derive_proposal(old,old_catalog=catalog,new_catalog=new_catalog,
        changes=replace(patch,authorities=reconciliation.authorities),generation=generation,full_defaults=defaults)
    except s.SnapshotError as exc:_translate_snapshot(exc)
    final_tokens=aa.revoke_tokens(old_tokens,tuple(sorted(set(reconciliation.revoke_agents)|set(extra_revocations))),actor_id=actor_id,at=at,via=via)
    user_changes={x.id:x for x in users}
    final_users=tuple(replace(x,active=user_changes[x.id].active,version=user_changes[x.id].version,**updates) if x.id in user_changes else x for x in old_users)
    final_state=replace(old_state,generation=generation,catalog_version=final.root.catalog.version,
                        catalog_sha256=final.root.stamp.catalog_sha256,catalog_json=s.encode_catalog(final.root.catalog),**updates) if semantic else old_state
    mutations=[]
    for table,before_rows,after_rows,key in (('users',old_users,final_users,('id',)),('memberships',old.memberships,final.root.memberships,('id',)),
        ('agent_principals',old.assignments,final.root.assignments,('agent_user_id','principal_user_id')),
        ('agent_authority',old.authorities,final.root.authorities,('agent_user_id',)),('api_tokens',old_tokens,final_tokens,('id',))):
        before_map={tuple(getattr(x,k) for k in key):x for x in before_rows}
        for after in after_rows:
            before=before_map.get(tuple(getattr(after,k) for k in key))
            if before!=after:mutations.append(_mutation(table,before,after,key))
    old_defaults={(x.role,x.requirement.capability,x.requirement.threshold) for x in old.role_defaults}
    final_defaults={(x.role,x.requirement.capability,x.requirement.threshold) for x in final.root.role_defaults}
    for values in sorted(old_defaults-final_defaults):
        key=tuple(zip(('role','capability','required_role'),values))
        mutations.append(Mutation('role_capabilities',key,(),(),delete=True))
    for values in sorted(final_defaults-old_defaults):
        key=tuple(zip(('role','capability','required_role'),values))
        mutations.append(Mutation('role_capabilities',key,(),key,insert=True))
    if semantic:mutations.append(_mutation('permission_state',old_state,final_state,('id',)))
    if target_id in {x.agent_user_id for x in final.root.authorities}:version=next(x.version for x in final.root.authorities if x.agent_user_id==target_id)
    kind={PutMembership:'membership put',RevokeMembership:'membership revoke',SetUserActive:'user active',SetAssignments:'assignments set',AuthorizeAgent:'agent authorize',ReplaceCatalog:'catalog replace'}[type(intent)]
    prepared_audit=None
    if semantic and not preview:
        prepared_audit=audit_owner.prepare_audit(tx,context=context,actor_id=actor_id,command='permission '+kind,
            old=old,final=final.root,old_tokens=old_tokens,final_tokens=final_tokens,old_users=old_users,final_users=final_users,
            old_state=old_state,final_state=final_state,transitions=reconciliation.comparison.agents)
    return PreparedEdit(VisibleEdit(kind,target_id,version,semantic,preview),old,initiating,final,pair,reconciliation,
                        old_tokens,final_tokens,old_state,final_state,old_users,final_users,new_catalog,tuple(mutations),prepared_audit)


def _quoted(name):
    return '"'+name.replace('"','""')+'"'


def _cell(value):
    # Fingerprint exact SQLite storage class/value, including float bits. The
    # storage guard never retains a token/password hash as a prepared row value.
    if type(value) is bool:value=int(value)
    if value is None:tag,data='null',b''
    elif type(value) is int:tag,data='integer',str(value).encode()
    elif type(value) is float:tag,data='real',struct.pack('>d',value)
    elif type(value) is str:tag,data='text',value.encode('utf-8')
    elif type(value) is bytes:tag,data='blob',value
    else:fail('unsupported_schema','storage')
    return tag,hashlib.sha256(data).digest()


@dataclass
class _StorageImage:
    ddl: tuple
    columns: dict
    tables: dict


def _observe(tx):
    ddl=tuple(tx.raw.execute('SELECT type,name,tbl_name,sql FROM main.sqlite_master ORDER BY type,name'))
    columns={};tables={}
    for kind,name,_,_ in ddl:
        if kind!='table':continue
        info=tuple(tx.raw.execute('PRAGMA main.table_xinfo('+_quoted(name)+')'))
        if any(x[6] for x in info):fail('unsupported_schema','storage')
        columns[name]=info
        try:
            rows=tx.raw.execute('SELECT rowid,'+','.join(_quoted(x[1]) for x in info)+' FROM main.'+_quoted(name)+' ORDER BY rowid')
            tables[name]={row[0]:tuple(_cell(x) for x in row[1:]) for row in rows}
        except sqlite3.DatabaseError:fail('unsupported_schema','storage')
    return _StorageImage(ddl,columns,tables)


def _expected_storage(tx,before,prepared):
    image=_StorageImage(before.ddl,before.columns,{name:dict(rows) for name,rows in before.tables.items()})
    def insert(table,values):
        info=image.columns[table];record=[]
        for col in info:
            name=col[1]
            if name in values:value=values[name]
            elif col[4] is not None or col[3]:fail('unsupported_schema','insert_defaults')
            else:value=None
            record.append(_cell(value))
        rowid=max(image.tables[table],default=0)+1
        if rowid>aa.MAX_INTEGER:fail('integer_overflow','rowid')
        image.tables[table][rowid]=tuple(record)
    for mutation in prepared.mutations:
        if mutation.insert:
            insert(mutation.table,dict(mutation.values));continue
        where=' AND '.join(_quoted(key)+' IS ?' for key,_ in mutation.key)
        rows=tx.raw.execute('SELECT rowid FROM main.'+mutation.table+' WHERE '+where,tuple(v for _,v in mutation.key)).fetchall()
        if len(rows)!=1:fail('conflict','write')
        rowid=rows[0][0]
        if mutation.delete:del image.tables[mutation.table][rowid];continue
        values=list(image.tables[mutation.table][rowid]);names=[x[1] for x in image.columns[mutation.table]]
        for name,value in mutation.values:values[names.index(name)]=_cell(value)
        image.tables[mutation.table][rowid]=tuple(values)
    if prepared.audit is not None:
        insert('audit_events',asdict(prepared.audit.event))
        for entry in prepared.audit.entries:insert('audit_entries',asdict(entry))
    return image


def _write_mutation(tx,mutation):
    if mutation.insert:
        names=tuple(x for x,_ in mutation.values)
        sql='INSERT INTO main.'+mutation.table+' ('+','.join(_quoted(x) for x in names)+') VALUES ('+','.join('?' for _ in names)+')'
        values=tuple(v for _,v in mutation.values)
    else:
        guards=(*mutation.key,*mutation.expected)
        where=' AND '.join(_quoted(key)+' IS ?' for key,_ in guards)
        if mutation.delete:
            sql='DELETE FROM main.'+mutation.table+' WHERE '+where;values=tuple(v for _,v in guards)
        else:
            sql='UPDATE main.'+mutation.table+' SET '+','.join(_quoted(key)+'=?' for key,_ in mutation.values)+' WHERE '+where
            values=tuple(v for _,v in (*mutation.values,*guards))
    if tx.raw.execute(sql,values).rowcount!=1:fail('conflict','write')


def _verify_final(tx,prepared,expected_storage):
    try:observed=s.load_root(tx,catalog=prepared.catalog)
    except s.SnapshotError as exc:_translate_snapshot(exc)
    if observed!=prepared.final.root or aa.read_tokens(tx)!=prepared.final_tokens or audit_owner.read_users(tx)!=prepared.final_users or audit_owner.read_state(tx)!=prepared.final_state:
        fail('conflict','final_state')
    if _observe(tx)!=expected_storage:fail('conflict','allowed_write_set')


def preview_edit(tx: Database, *, binding: TrustedBinding, intent: Edit,
                 catalog: s.CatalogBundle, visibility: s.VisibilityProvider,
                 request_id: str) -> PreparedEdit:
    """Fresh conditional preview; no durable identity/time allocation or writes."""
    _require_tx(tx)
    actor_id=_actor(tx,binding,request_id,'preview')
    return _prepare(tx,actor_id=actor_id,intent=intent,catalog=catalog,visibility=visibility)


def apply_edit(tx: Database, *, binding: TrustedBinding, intent: Edit,
               catalog: s.CatalogBundle, visibility: s.VisibilityProvider,
               audit: AuditContext) -> EditEffects:
    """Prepare every effect before DML, write once, check all facts, never commit.

    Caller guarantees BEGIN IMMEDIATE (write_transaction is not a lock-mode
    certificate). Binding refresh occurs in that transaction immediately before
    the service savepoint; rotating an OS lifetime guard cannot release it.
    """
    _require_tx(tx,True);audit.validate()
    actor_id=_actor(tx,binding,audit.request_id,'apply')
    savepoint='b2_edit_'+new_id()
    tx.raw.execute('SAVEPOINT '+savepoint)
    try:
        before=_observe(tx)
        prepared=_prepare(tx,actor_id=actor_id,intent=intent,catalog=catalog,visibility=visibility,context=audit)
        expected=_expected_storage(tx,before,prepared)
        for mutation in prepared.mutations:_write_mutation(tx,mutation)
        if prepared.audit is not None:audit_owner.insert_audit(tx,prepared.audit)
        _verify_final(tx,prepared,expected)
        tx.raw.execute('RELEASE SAVEPOINT '+savepoint)
    except BaseException:
        tx.raw.execute('ROLLBACK TO SAVEPOINT '+savepoint)
        tx.raw.execute('RELEASE SAVEPOINT '+savepoint)
        raise
    return EditEffects(prepared.visible,prepared.audit.event.id if prepared.audit else None,prepared)
