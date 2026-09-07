"""Actual current binding and complete historical aggregate admission."""
from dataclasses import dataclass
import sqlalchemy as sa
from bookflow.company import schema as c, deposit_dependency_history as h
from bookflow.company.deposit_draft_validation import admit as draft_admit
from bookflow.core.errors import BookflowError

@dataclass(frozen=True)
class ReadEvidence:
    company_id: str
    roots: tuple[str,...]
    events: tuple[str,...]
    drafts: tuple[str,...]
    selected_pins: tuple[tuple[str,str],...] = ()
    consumed_links: tuple[tuple[str,str,str],...] = ()
    current_references: tuple = ()
    relation_recipe: str = 'complete stored deposit aggregate; selection applied by private query owner'
    # Internal proof provenance, never a caller policy or public token.
    observable_fields: tuple[str,...] = ()


def select(s,table,column,identities):
    values=sorted(set(identities));result=[]
    for n in range(0,len(values),200):
        result.extend(dict(r) for r in s.company.conn.execute(sa.select(table).where(column.in_(values[n:n+200]))).mappings())
    return result


def authenticate(s,binding):
    # Also enforces actual applicable membership with no installation-admin bypass.
    draft_admit(s,binding=binding,write=False)


def admit(s,identities,*,binding):
    authenticate(s,binding)
    roots=set(identities);events=set();drafts=set();seen=set()
    while roots-seen:
        group=roots-seen;seen.update(group)
        h._authorize_binding_graph(s,binding,tuple(sorted(group)),write=False)
        memberships=select(s,c.deposit_memberships,c.deposit_memberships.c.transaction_id,group)
        memberships+=select(s,c.deposit_memberships,c.deposit_memberships.c.source_transaction_id,group)
        for r in memberships:roots.update((r['transaction_id'],r['source_transaction_id']));events.add(r['audit_event_id'])
        operations=select(s,c.deposit_operations,c.deposit_operations.c.transaction_id,group)
        targets=select(s,c.deposit_operation_targets,c.deposit_operation_targets.c.transaction_id,group)
        operations+=select(s,c.deposit_operations,c.deposit_operations.c.id,[r['operation_id'] for r in targets])
        connected=select(s,c.deposit_drafts,c.deposit_drafts.c.edit_transaction_id,group)
        connected+=select(s,c.deposit_drafts,c.deposit_drafts.c.copy_transaction_id,group)
        for draft in connected:
            draft_admit(s,binding=binding,draft=draft['id'],write=False)
            drafts.add(draft['id']);events.add(draft['audit_event_id'])
            for row in select(s,c.deposit_draft_sources,c.deposit_draft_sources.c.draft_id,[draft['id']]):roots.add(row['source_transaction_id'])
        opids={r['id'] for r in operations}
        for r in operations:roots.add(r['transaction_id']);events.add(r['audit_event_id'])
        for r in select(s,c.deposit_operation_targets,c.deposit_operation_targets.c.operation_id,opids):roots.add(r['transaction_id'])
        for r in select(s,c.deposit_draft_consumptions,c.deposit_draft_consumptions.c.operation_id,opids):drafts.add(r['draft_id']);events.add(r['audit_event_id'])
    h._authorize_binding_graph(s,binding,tuple(sorted(roots)),tuple(sorted(events)),write=False)
    for identity in sorted(drafts):draft_admit(s,binding=binding,draft=identity,write=False)
    return ReadEvidence(s.company_row['id'],tuple(sorted(roots)),tuple(sorted(events)),tuple(sorted(drafts)))


def selected(s,identity,*,binding):
    try:return admit(s,[identity],binding=binding)
    except BookflowError as e:
        if e.code in ('E_PERMISSION','E_COMPANY_NOT_FOUND','E_RECORD_NOT_FOUND'):
            raise BookflowError('E_RECORD_NOT_FOUND') from None
        raise
