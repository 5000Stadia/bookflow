"""Public deposit detail execution receipt, rechecked by the publication owner.

The sibling of ``publication_audit`` for the two registered deposit reads. A
proof is a process-owned value, never a token, a cursor or an activation switch,
and a fresh reader is mandatory for every hosted release check.

**The proof compares audience-derived facts only, and that is load bearing.**
The private inspection guard's ``read_digest`` is derived from readset relation
anchors, so it moves whenever a reference the reader may not see changes.
Keeping that guard off the wire is necessary but not sufficient: a proof that
captured the private readset, the guard, or the connected closure would refuse
release after a hidden-only change, and an observable refusal is the same
disclosure arriving through the release path instead of the response body. So
the only things compared here are the disclosed document this reader received
and the exact failure it received instead — both already audience-filtered by
the public projector. Admission is not compared; it is re-performed, by running
the same producer again, which denies for a reader who has actually lost access
and stays silent for a change that reader could never observe.

The observation instant travels in the proof for the same reason. Re-execution
pins it, so a release compares the same document rather than refusing because
the clock advanced past a dated cutoff.
"""
from dataclasses import dataclass
import hashlib
import json

from bookflow.company import deposit_public_authority as pa
from bookflow.company import deposit_public_reads as reads
from bookflow.company import deposit_queries as q
from bookflow.core.deposit_request import DepositRequest
from bookflow.core.errors import BookflowError
from bookflow.core.identity_admin_binding import BoundReader, ReaderIdentity

_SEAL = object()

# Closed semantic outcomes of a public deposit read. Anything else - an
# authentication loss, an I/O fault, a busy database - is not a semantic result
# and is raised rather than retained, exactly as the projected history family
# treats its own unmodelled failures.
CAPTURED_CODES = frozenset({'E_RECORD_NOT_FOUND', 'E_PERMISSION', 'E_VALIDATION',
                            'E_QUERY_STALE', 'E_DEPOSIT_SOURCE_INVALID', 'E_COMPANY_NOT_FOUND'})


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _deny():
    raise BookflowError('E_PERMISSION', details={'stage': 'publication',
                        'reason': 'authority_changed', 'outcome': 'unknown'})


@dataclass(frozen=True, slots=True)
class CapturedFailure:
    """One closed rejection, reproducible without the original session."""

    code: str
    details: tuple

    @classmethod
    def capture(cls, error):
        details = error.details or {}
        if (error.code not in CAPTURED_CODES or type(details) is not dict
                or not all(type(key) is str and type(value) in (str, int, bool)
                           for key, value in details.items())):
            raise error
        return cls(error.code, tuple(sorted(details.items())))

    def error(self):
        return BookflowError(self.code, details=dict(self.details))


@dataclass(frozen=True, init=False, repr=False)
class DepositProof:
    """Seal-required receipt of one executed public deposit read.

    Holds the reader identity, the closed request, the selected pin, the
    disclosed document or the exact captured failure, and the observation
    instant. No reader, session, company handle, credential or callback
    survives; every leaf is a frozen typed value or plain JSON.
    """

    identity: ReaderIdentity
    request: DepositRequest
    pin: tuple
    document: dict | None
    failure: CapturedFailure | None
    observed_at: str
    digest: str

    def __init__(self, identity, request, document, *, _seal, failure=None, observed_at=None):
        if _seal is not _SEAL:
            raise TypeError('execution-owned proof required')
        if (document is None) == (failure is None):
            raise TypeError('exactly one semantic outcome required')
        if type(identity) is not ReaderIdentity or type(request) is not DepositRequest:
            raise TypeError('closed deposit request required')
        if failure is not None and type(failure) is not CapturedFailure:
            raise TypeError('closed captured failure required')
        if type(observed_at) is not str or not observed_at:
            raise TypeError('captured observation instant required')
        pin = ()
        if document is not None:
            selected = document['selected']
            selected = selected['pin'] if request.command == 'deposit show' else selected
            pin = (selected['deposit_id'], selected['revision_id'], selected['revision_number'])
        output = document if failure is None else failure.error().to_dict()
        for key, value in dict(identity=identity, request=request, pin=pin, document=document,
                               failure=failure, observed_at=observed_at,
                               digest=_digest(output)).items():
            object.__setattr__(self, key, value)

    def __deepcopy__(self, memo):
        # All leaves are frozen typed values or plain JSON. No reader, host or
        # connection is retained, so the bounded receipt cache copies nothing live.
        return self

    def matches(self, result):
        return self.digest == _digest(result)


def _produce(session, request, audience, at):
    if request.command == 'deposit show':
        return reads.show(session, request.input, audience=audience, at=at)
    return reads.items(session, request.input, audience=audience, at=at)


def execute_detail(reader: BoundReader, request: DepositRequest, binding, *, ctx=None, at=None):
    """Only construction path: authenticated observation, then sole projection.

    ``binding`` is the genuine producer this reader already holds - an OSBinding
    obtained through the private execution bridge, or the HTTP Credential the
    adapter authenticated. It is never synthesized from a ReaderIdentity, and
    the audience refuses one that does not agree with this reader.
    """
    if type(request) is not DepositRequest:
        raise TypeError('closed deposit request required')
    audience = pa.audience(reader, binding)
    at = at or q.now()
    try:
        session = reads.open_selected(reader, audience, request.company, ctx)
        value = _produce(session, request, audience, at)
    except BookflowError as error:
        failure = CapturedFailure.capture(error)
        audience.validate()
        return failure.error().to_dict(), DepositProof(audience.identity, request, None, _seal=_SEAL,
                                                       failure=failure, observed_at=at)
    audience.validate()
    document = value.model_dump(mode='json')
    return document, DepositProof(audience.identity, request, document, _seal=_SEAL, observed_at=at)


def revalidate_proof(reader: BoundReader, proof: DepositProof, binding, *, ctx=None):
    """Re-run the same request under current authority and compare what it discloses."""
    if type(proof) is not DepositProof:
        _deny()
    audience = pa.audience(reader, binding)
    if audience.identity != proof.identity:
        _deny()
    try:
        session = reads.open_selected(reader, audience, proof.request.company, ctx)
        value = _produce(session, proof.request, audience, proof.observed_at)
    except BookflowError as error:
        if proof.failure is None or CapturedFailure.capture(error) != proof.failure:
            _deny()
        audience.validate()
        return
    if proof.failure is not None or not proof.matches(value.model_dump(mode='json')):
        _deny()
    audience.validate()


def check_hosted(host, cred, ctx, proof):
    """Hosted release: a fresh reader, the original admitted credential, no cache."""
    from bookflow.adapters.http.execution import _reader_binding
    from bookflow.core.identity_admin_binding import hosted_reader
    from bookflow.hub.identity_admin import AdministrationError
    try:
        with hosted_reader(host, _reader_binding(host, cred, ctx.request_id),
                           request_id=ctx.request_id) as reader:
            revalidate_proof(reader, proof, cred, ctx=ctx)
    except AdministrationError:
        _deny()
