"""Retained deposit composition graph evidence, without identity admission."""
from dataclasses import dataclass
import sqlalchemy as sa
from bookflow.company import schema as c
from bookflow.core.errors import BookflowError


@dataclass(frozen=True)
class DraftGraph:
    transactions: tuple[str, ...]
    drafts: tuple[str, ...]
    selections: tuple[str, ...]


def collect(db, *, sources=(), draft=None, selection=None):
    targets=set(sources);drafts=set([draft] if draft else []);selections=set([selection] if selection else [])
    visited_drafts=set();visited_selections=set()
    # Full retained graph, including accepted/abandoned children and operation-only targets.
    while drafts-visited_drafts or selections-visited_selections:
        for identity in sorted(drafts-visited_drafts):
            visited_drafts.add(identity)
            header=db.conn.execute(sa.select(c.deposit_drafts).where(c.deposit_drafts.c.id==identity)).mappings().one_or_none()
            if header is None:raise BookflowError('E_RECORD_NOT_FOUND')
            if header['edit_transaction_id']:targets.add(header['edit_transaction_id'])
            if header['copy_transaction_id']:targets.add(header['copy_transaction_id'])
            targets.update(db.conn.execute(sa.select(c.deposit_draft_sources.c.source_transaction_id).where(c.deposit_draft_sources.c.draft_id==identity)).scalars())
            selections.update(db.conn.execute(sa.select(c.deposit_selections.c.id).where(c.deposit_selections.c.target_draft_id==identity)).scalars())
            if header['consumed_operation_id']:
                targets.update(db.conn.execute(sa.select(c.deposit_operation_targets.c.transaction_id).where(c.deposit_operation_targets.c.operation_id==header['consumed_operation_id'])).scalars())
        for identity in sorted(selections-visited_selections):
            visited_selections.add(identity)
            header=db.conn.execute(sa.select(c.deposit_selections).where(c.deposit_selections.c.id==identity)).mappings().one_or_none()
            if header is None:raise BookflowError('E_RECORD_NOT_FOUND')
            drafts.add(header['target_draft_id'])
            targets.update(db.conn.execute(sa.select(c.deposit_selection_sources.c.source_transaction_id).where(c.deposit_selection_sources.c.selection_id==identity)).scalars())
    # Walk complete deposit membership and permanent-operation participants.
    # A removed receipt still connects its historical deposit and sibling sources.
    visited=set()
    while targets-visited:
        frontier=sorted(targets-visited);visited.update(frontier)
        for offset in range(0,len(frontier),200):
            group=frontier[offset:offset+200];members=c.deposit_memberships
            for source,deposit in db.conn.execute(sa.select(members.c.source_transaction_id,members.c.transaction_id).where(
                    sa.or_(members.c.source_transaction_id.in_(group),members.c.transaction_id.in_(group)))):
                targets.update((source,deposit))
            op=c.deposit_operations;ot=c.deposit_operation_targets
            targets.update(db.conn.execute(sa.select(ot.c.transaction_id).join(op,op.c.id==ot.c.operation_id).where(op.c.transaction_id.in_(group))).scalars())
    return DraftGraph(tuple(sorted(targets)), tuple(sorted(visited_drafts)), tuple(sorted(visited_selections)))
