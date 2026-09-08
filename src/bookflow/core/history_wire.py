"""Execution-owned immutable response rendering for the coordinated history cutover."""
from dataclasses import dataclass
from bookflow.core import publication_audit as publication, history_cursors as cursors
from bookflow.hub import audit_projection as projection


@dataclass(frozen=True)
class HistoryWire:
    mode: str
    history: projection.ProjectedHistory
    next_cursor: str | None
    event_cursors: tuple[tuple[str,str],...]
    authority: str

    def document(self):
        h=self.history
        if self.mode=='activity':
            return dict(projection_version=2,items=publication._json(h.activity_items),
                        count=len(h.activity_items),has_more=h.has_more,next_cursor=self.next_cursor)
        items=[]
        bookmarks=dict(self.event_cursors)
        for event in h.events:
            value=publication._json(event)
            value['entry_count']=len(event.entries)
            if self.mode!='show':value['entries']=None
            if self.mode=='tail':value['resume_after']=bookmarks[event.id]
            items.append(value)
        if self.mode=='show':
            return dict(projection_version=2,**items[0])
        result=dict(projection_version=2,items=items,count=len(items))
        if self.mode=='list':result['next_before']=self.next_cursor
        else:result.update(next_after=self.next_cursor,scanned_count=h.scanned_count,scan_more=h.scanned_more)
        return result


def bind(reader, proof):
    """No caller-supplied response bytes; every value derives from the sealed proof."""
    publication.revalidate_proof(reader,proof)
    if proof.failure is not None:
        return proof.failure.error().to_dict(),proof
    audience=projection.make_audience(reader,read_capability='activity' if proof.selection.mode=='activity' else 'audit')
    wire=HistoryWire(proof.selection.mode,proof.history,cursors.issue(reader,proof),
        cursors.issue_events(reader,proof) if proof.selection.mode=='tail' else (),
        cursors.authority_digest(audience,proof.selection.company))
    bound=publication.ProjectionProof(proof.identity,proof.selection,proof.history,proof.evidence,
                                     _seal=publication._SEAL,wire=wire,request=proof.request)
    audience.validate()
    return wire.document(),bound
