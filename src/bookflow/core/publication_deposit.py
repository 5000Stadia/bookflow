"""Public deposit detail execution receipt, rechecked by the publication owner.

The sibling of ``publication_audit`` for the registered deposit reads. A
proof is a process-owned value, never a token, a cursor or an activation switch,
and a fresh reader is mandatory for every hosted release check.

**The proof carries audience-derived facts only, and that is load bearing.**
The private inspection guard's ``read_digest`` is derived from readset relation
anchors, so it moves whenever a reference the reader may not see changes. A
proof that captured the private readset, the guard, or the connected closure
would let a hidden-only change produce an observable refusal, which is the same
disclosure arriving through the release path instead of the response body. So a
proof holds only what this reader was actually shown: the disclosed document, or
the exact closed failure it received instead.

**Release re-establishes authority; it does not re-read.** Under the accepted
D11 narrowing, reconstructing and comparing the whole audience-filtered result
at every release lost its justification, and so did treating an ordinary
same-company edit as invalidating an already captured coherent read merely
because a current label moved. What release still proves, every time and never
inferred from an unchanged epoch or from a reused earlier answer, is written out
in :func:`revalidate_proof`. Cross-company leakage stays a blocking boundary;
same-company inference does not. The financial validation itself is undiminished
- it runs in full at execution, which is now the only place this module reads.

The observation instant still travels in the proof, because the disclosed
document quotes it and any re-execution of the same request has to be able to
reproduce that document rather than differ only by the clock.
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
            if document.get('company_id') != request.company:
                raise TypeError('the disclosed document must belong to the requested company')
            # Only the two reads that select one immutable revision carry a pin. A query
            # spans documents and a history walk spans every revision of one, so neither has
            # a single selection to record.
            if request.command in ('deposit show', 'deposit items'):
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


def _scoped(proof):
    """Every released value belongs to the company the request names.

    Checked where the proof is built and again at every release, so a document
    projected in one company can never leave through a request for another.
    """
    if proof.document is not None and proof.document.get('company_id') != proof.request.company:
        _deny()


def _produce(session, request, audience, at):
    if request.command == 'deposit query':
        return reads.query(session, request.input, audience=audience, at=at)
    if request.command == 'deposit show':
        return reads.show(session, request.input, audience=audience, at=at)
    if request.command == 'deposit history':
        return reads.history(session, request.input, audience=audience, at=at)
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
    """Re-establish authority for one already captured result, without re-reading.

    Everything below is evaluated on every call. Nothing is memoized across the
    parts of one response and nothing is inferred from an unchanged epoch: a
    credential can expire between two parts of the same delivery with no commit
    anywhere, so a release that reused an earlier answer would let an expired
    credential finish reading.

    Proved here, every time:

    * the supplied producer is genuinely this reader's own and the reader still
      authenticates, which for a token credential is where revocation, the
      principal authority epoch and **expiry** are decided;
    * the credential itself still revalidates in the session that releases it;
    * the captured result is attributed to the identity now asking for it;
    * that identity currently holds admitted access to the **selected company**,
      through the same actor-and-principal intersection execution used;
    * the captured result belongs to that company, so nothing crosses one;
    * a captured failure is still the failure this company answers with, when
      the company itself is what refuses.

    Deliberately not proved here, under the accepted narrowing: that the whole
    audience-filtered document would come out identical if the read ran again.
    An ordinary same-company edit no longer invalidates a coherent captured
    read. Nothing about the connected closure is re-derived either, because
    deriving it is the read.
    """
    if type(proof) is not DepositProof:
        _deny()
    _scoped(proof)
    audience = pa.audience(reader, binding)
    if audience.identity != proof.identity:
        _deny()
    try:
        session = reads.open_selected(reader, audience, proof.request.company, ctx)
        binding.revalidate(session.hub)
    except BookflowError as error:
        if proof.failure is None or CapturedFailure.capture(error) != proof.failure:
            _deny()
        audience.validate()
        return
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
