"""Private semantic execution receipt, rechecked by the actual publication owner.

No public cursor or activation switch. Proofs are process-owned values, not tokens
or RootFacts authentication. A fresh reader is mandatory for every release check.
"""
from dataclasses import dataclass
import hashlib
import json
from pydantic import BaseModel
from bookflow.core.errors import BookflowError
from bookflow.core.identity_admin_binding import BoundReader, ReaderIdentity
from bookflow.hub import audit_projection as projection

_SEAL = object()


def _json(value):
    if isinstance(value, BaseModel):
        return value.model_dump(mode='json',by_alias=True)
    if hasattr(value, '__dataclass_fields__'):
        return {name: _json(getattr(value, name)) for name in value.__dataclass_fields__}
    if type(value) is tuple:
        return [_json(x) for x in value]
    if value is None or type(value) in (str, int, bool):
        return value
    raise TypeError('unsupported projection value')


def document(history):
    """Closed projected values only; private evidence is never serialized."""
    result={name: _json(getattr(history, name)) for name in (
        'events', 'next_anchor', 'endpoint', 'has_more', 'scanned_count', 'scanned_more')}
    if history.activity:
        result.update(items=_json(history.activity_items),count=len(history.activity_items),
                      next_entry_anchor=history.next_entry_anchor)
    return result


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _deny():
    raise BookflowError('E_PERMISSION', details={'stage': 'publication',
                        'reason': 'authority_changed', 'outcome': 'unknown'})


@dataclass(frozen=True, init=False, repr=False)
class ProjectionProof:
    identity: ReaderIdentity
    selection: projection.HistorySelection
    history: projection.ProjectedHistory | None
    failure: projection.ProjectedFailure | None
    evidence: tuple[projection.ProjectedEvent, ...]
    digest: str
    wire: object | None
    request: object | None

    def __init__(self, identity, selection, history, evidence, *, _seal, failure=None, wire=None, request=None):
        if _seal is not _SEAL:
            raise TypeError('execution-owned proof required')
        if (history is None)==(failure is None):
            raise TypeError('exactly one semantic outcome required')
        if request is not None:
            from bookflow.core.history_request import HistoryRequest
            if type(request) is not HistoryRequest:
                raise TypeError('closed history request required')
        if wire is not None:
            from bookflow.core.history_wire import HistoryWire
            if type(wire) is not HistoryWire or failure is not None or wire.history != history:
                raise TypeError('execution-owned wire history required')
        output=wire.document() if wire is not None else document(history) if failure is None else failure.error().to_dict()
        for key, value in dict(identity=identity, selection=selection, history=history,failure=failure,
                               evidence=evidence, digest=_digest(output), wire=wire, request=request).items():
            object.__setattr__(self, key, value)

    def __deepcopy__(self, memo):
        # All leaves are frozen typed values. No reader/host/connection retained.
        return self

    def matches(self, result):
        return self.digest == _digest(result)


def execute_history(reader: BoundReader, selection: projection.HistorySelection, *, ctx=None, request=None):
    """Only construction path: authenticated observation then sole projection."""
    audience = projection.make_audience(reader, read_capability='activity' if selection.mode=='activity' else 'audit')
    if request is not None:
        from bookflow.core.history_request import HistoryRequest
        if type(request) is not HistoryRequest or request.selection != selection:
            raise TypeError('matching closed history request required')
    try:
        if request is not None:
            selection = request.resolve(reader, ctx=ctx)
        elif ctx is not None:
            open_selected(reader,selection,ctx)
        history = projection.project_history(audience, selection)
    except BookflowError as error:
        failure=projection.ProjectedFailure.capture(error)
        audience.validate()
        proof=ProjectionProof(audience.identity,selection,None,(),_seal=_SEAL,failure=failure,request=request)
        return failure.error().to_dict(),proof
    evidence = history.events
    audience.validate()
    proof = ProjectionProof(audience.identity, selection, history, evidence, _seal=_SEAL,request=request)
    return document(history), proof


def revalidate_proof(reader: BoundReader, proof: ProjectionProof, *, ctx=None):
    if type(proof) is not ProjectionProof:
        _deny()
    audience = projection.make_audience(reader, read_capability='activity' if proof.selection.mode=='activity' else 'audit')
    if audience.identity != proof.identity:
        _deny()
    if proof.wire is not None:
        from bookflow.core.history_cursors import authority_digest
        if authority_digest(audience,proof.selection.company)!=proof.wire.authority:_deny()
    if proof.failure is not None:
        try:
            selection = proof.selection
            if proof.request is not None:
                selection = proof.request.resolve(reader, ctx=ctx)
            elif ctx is not None:
                open_selected(reader,selection,ctx)
            projection.project_history(audience,selection)
        except BookflowError as error:
            current=projection.ProjectedFailure.capture(error)
            if current!=proof.failure:_deny()
        else:_deny()
        audience.validate()
        return
    if ctx is not None:open_selected(reader,proof.selection,ctx)
    # Reproject immutable selected evidence, never rerun a live list whose
    # unrelated new endpoint would spuriously stale an already encoded reply.
    selected={event.id:event for event in proof.evidence}
    predicates=dict(proof.history.predicates)
    selection=projection.normalized_selection(audience,proof.selection)
    if selection.mode=='activity':
        projection._activity_target(audience,selection)
        selection=selection.model_copy(update={'record_type':None,'record_id':None})
    activity_items={(x.event_id,x.entry_id):x for x in proof.history.activity_items}
    activity_dependencies={}
    for event_id,entry_id in proof.history.activity_dependencies:
        activity_dependencies.setdefault(event_id,[]).append(entry_id)
    for identifier in proof.history.evidence:
        current = projection.project_event(audience, identifier, company=selection.company)
        if current is None:_deny()
        if identifier in selected and current!=selected[identifier]:_deny()
        if identifier in predicates and projection._matches(current,selection)!=predicates[identifier]:_deny()
        for entry_id in activity_dependencies.get(identifier,()):
            entry=next((x for x in current.entries if x.id==entry_id),None)
            if entry is None:_deny()
            expected=activity_items.get((identifier,entry_id))
            if expected is not None and projection.activity_item(current,entry)!=expected:_deny()
    audience.validate()


def open_selected(reader, selection, ctx):
    """Governed admission precedes selected-company filesystem/record lookup."""
    if selection.company is None:
        return
    from bookflow.core.dispatch import resolve_company, open_company
    audience = projection.make_audience(reader, read_capability='activity' if selection.mode=='activity' else 'audit')
    audience.require(selection.company, ((audience.read_capability, 'member'),))
    session = reader.session
    session.company_row = resolve_company(session, selection.company, 'option')
    open_company(session, ctx, False)
    reader.authenticate()


def check_hosted(host, cred, ctx, proof):
    from bookflow.core.identity_admin_binding import hosted_reader
    from bookflow.adapters.http.execution import _reader_binding
    from bookflow.hub.identity_admin import AdministrationError
    try:
        with hosted_reader(host, _reader_binding(host, cred, ctx.request_id),
                           request_id=ctx.request_id) as reader:
            revalidate_proof(reader, proof,ctx=ctx)
    except AdministrationError:
        _deny()
