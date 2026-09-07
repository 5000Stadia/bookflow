"""Prepared private B2 audit rows; no generic writer, routing or publication."""
from __future__ import annotations
from dataclasses import asdict, dataclass, fields
from typing import Literal
import sqlalchemy as sa
from bookflow.core.audit import encode_snapshot, next_seq
from bookflow.core.ids import new_id
from bookflow.core import clock
from . import schema as h, permission_snapshot as s
from .agent_authority import TokenRow, typed, integer, fail, MAX_INTEGER


@dataclass(frozen=True, slots=True)
class AuditContext:
    at: str
    interface: Literal['cli','http','mcp','gui','python','system']
    client_name: str
    client_version: str
    client_host: str
    session_id: str
    request_id: str
    reason: str | None = None
    idempotency_key: str | None = None
    directive_id: str | None = None
    directive_code: str | None = None
    source_ref: str | None = None

    def validate(self):
        typed(self,AuditContext,'audit')
        try:
            parsed=clock.parse_iso(self.at)
            if parsed.tzinfo is None:fail('invalid_input','audit')
        except (ValueError,TypeError):fail('invalid_input','audit')
        limits={'at':32,'client_name':64,'client_version':32,'client_host':255,
                'session_id':26,'request_id':26,'reason':140,'idempotency_key':128,
                'directive_id':26,'directive_code':16,'source_ref':512}
        for key,limit in limits.items():
            value=getattr(self,key)
            if value is not None and (len(value)>limit or (key in ('at','request_id','session_id') and not value)):
                fail('invalid_input','audit')


@dataclass(frozen=True, slots=True)
class StateRow:
    id: int
    generation: int
    mode: Literal['legacy','policy_v1']
    catalog_version: str | None
    catalog_sha256: str | None
    catalog_json: str | None
    updated_at: str | None
    updated_by: str | None
    updated_via: str | None


def read_state(tx):
    rows=tx.raw.execute('SELECT '+','.join(x.name for x in fields(StateRow))+' FROM main.permission_state').fetchall()
    if len(rows)!=1:fail('unsupported_schema','state')
    value=StateRow(*rows[0]);typed(value,StateRow,'state');integer(value.generation,'generation')
    if value.id!=1:fail('unsupported_schema','state')
    return value


@dataclass(frozen=True, slots=True)
class UserAuditRow:
    id: str
    kind: Literal['human','agent','system']
    active: bool
    hub_admin: bool
    owner_user_id: str | None
    version: int
    created_at: str
    created_by: str
    created_via: str
    updated_at: str
    updated_by: str
    updated_via: str


def read_users(tx):
    rows=[]
    names=tuple(x.name for x in fields(UserAuditRow))
    for values in tx.raw.execute('SELECT '+','.join(names)+' FROM main.users ORDER BY id'):
        values=list(values)
        for i in (2,3):
            if type(values[i]) is not int or values[i] not in (0,1):fail('invalid_input','users')
            values[i]=bool(values[i])
        row=UserAuditRow(*values);typed(row,UserAuditRow,'users');integer(row.version,'user_version');rows.append(row)
    return tuple(rows)


@dataclass(frozen=True, slots=True)
class EventRow:
    id: str
    seq: int
    at: str
    command: str
    actor_id: str
    actor_kind: str
    on_behalf_of: None
    interface: str
    client_name: str
    client_version: str
    client_host: str
    session_id: str
    request_id: str
    idempotency_key: str | None
    reason: str | None
    directive_id: str | None
    directive_code: str | None
    source_ref: str | None
    summary: str


@dataclass(frozen=True, slots=True)
class EntryRow:
    id: str
    event_id: str
    record_type: str
    record_id: str
    action: str
    version_before: int | None
    version_after: int | None
    after: bytes | None
    before: bytes | None


@dataclass(frozen=True, slots=True)
class PreparedAudit:
    event: EventRow
    entries: tuple[EntryRow, ...]


def _envelope(kind,row,**extra):
    # Every row passed here is one of this module's explicit safe projections or
    # B1's typed safe rows, never a generic users/token SQL mapping.
    return None if row is None else dict(format=1,kind=kind,row=asdict(row),**extra)


def _state_envelope(state,root):
    row={key:getattr(state,key) for key in ('id','generation','mode','catalog_version','catalog_sha256','updated_at','updated_by','updated_via')}
    return dict(format=1,kind='permission_state',state_key=1,row=row,
                catalog=asdict(root.catalog),defaults=[asdict(x) for x in root.role_defaults])


def prepare_audit(tx, *, context: AuditContext, actor_id: str, command: str,
                  old: s.RootFacts, final: s.RootFacts, old_tokens: tuple[TokenRow,...],
                  final_tokens: tuple[TokenRow,...], old_users: tuple[UserAuditRow,...],
                  final_users: tuple[UserAuditRow,...], old_state: StateRow, final_state: StateRow,
                  transitions) -> PreparedAudit:
    """Fix all IDs/time/sequence/encoded bytes before service DML."""
    context.validate()
    # Explicit main table avoids a local naming ambiguity in sequence discovery.
    table=h.audit_events.to_metadata(sa.MetaData(),schema='main')
    sequence=next_seq(tx,table)
    if type(sequence) is not int or sequence<1:fail('invalid_input','audit_sequence')
    if sequence>MAX_INTEGER:fail('integer_overflow','audit_sequence')
    event=EventRow(new_id(),sequence,context.at,command,actor_id,'human',None,
        context.interface,context.client_name,context.client_version,context.client_host,
        context.session_id,context.request_id,context.idempotency_key,context.reason,
        context.directive_id,context.directive_code,context.source_ref,'Permission administration updated.')
    changes=[]
    for kind,before_rows,after_rows in (('membership',old.memberships,final.memberships),
                                      ('user',old_users,final_users),('api_token',old_tokens,final_tokens)):
        before_map={x.id:x for x in before_rows}
        for after in after_rows:
            before=before_map.get(after.id)
            if before != after:
                changes.append((kind,after.id,'create' if before is None else 'update',
                    before.version if before else None,after.version,
                    _envelope(kind,before),_envelope(kind,after)))
    previous={x.agent_user_id:x for x in old.authorities};reason_map={x.agent:tuple(y.value for y in x.reasons) for x in transitions}
    for after in final.authorities:
        before=previous[after.agent_user_id]
        if before!=after:
            def envelope(value,root,reasons):
                return _envelope('agent_authority',value,
                    assignments=[asdict(x) for x in root.assignments if x.agent_user_id==value.agent_user_id],
                    transition_reasons=list(reasons))
            changes.append(('agent_authority',after.agent_user_id,'update',before.version,after.version,
                envelope(before,old,()),envelope(after,final,reason_map[after.agent_user_id])))
    changes.append(('permission_state','1','update',old_state.generation,final_state.generation,
                    _state_envelope(old_state,old),_state_envelope(final_state,final)))
    entries=tuple(EntryRow(new_id(),event.id,kind,identifier,action,before_version,after_version,
                          encode_snapshot(after),encode_snapshot(before))
                  for kind,identifier,action,before_version,after_version,before,after in sorted(changes,key=lambda x:(x[0],x[1])))
    return PreparedAudit(event,entries)


def insert_audit(tx, prepared: PreparedAudit):
    """Bound exact prepared rows, no clock/ID generation and no commit."""
    for table,rows in (('audit_events',(prepared.event,)),('audit_entries',prepared.entries)):
        for row in rows:
            data=asdict(row);names=tuple(data)
            tx.raw.execute('INSERT INTO main.'+table+' ('+','.join('"'+x+'"' for x in names)+') VALUES ('+','.join('?' for _ in names)+')',tuple(data.values()))
