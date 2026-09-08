"""Reconciliation activation metadata; owned successor must supply its resolver."""
from bookflow.storage.migrate import FeatureRevision, feature_admission
from bookflow.core.errors import BookflowError


class ProvenDepositDenial(BookflowError):
    """Private raise-path provenance, never a retained/compared authority proof.

    Only an evaluated role/resource denial may construct this marker. Its wire
    representation remains the ordinary sanitized E_PERMISSION failure.
    """

    def __init__(self):
        super().__init__('E_PERMISSION')

RECONCILIATION = FeatureRevision('company', None)


def reconciliation_status(db):
    resolver = feature_admission(db, RECONCILIATION, resolver=None)
    if resolver is not None:
        from bookflow.core.errors import BookflowError
        raise BookflowError('E_INTERNAL')
    return 'absent'


DEPOSIT = FeatureRevision('company', 'co0020')


def _active_claim(s, source):
    import sqlalchemy as sa
    from bookflow.company import schema as c
    return s.company.conn.execute(sa.select(c.deposit_current_memberships).where(
        c.deposit_current_memberships.c.source_transaction_id == source)).mappings().first()


def active_claim(s, source):
    resolver = feature_admission(s.company, DEPOSIT, resolver=_active_claim)
    return resolver(s, source) if resolver is not None else None


def active_claims(s, sources):
    """One schema admission for a complete bounded-read source collection."""
    import sqlalchemy as sa
    from bookflow.company import schema as c
    resolver=feature_admission(s.company,DEPOSIT,resolver=_active_claim)
    if resolver is None:return {}
    identities=sorted(set(sources));result={}
    for offset in range(0,len(identities),200):
        rows=s.company.conn.execute(sa.select(c.deposit_current_memberships).where(
            c.deposit_current_memberships.c.source_transaction_id.in_(identities[offset:offset+200]))).mappings()
        result.update((row['source_transaction_id'],dict(row)) for row in rows)
    return result


def historical_sources(s, deposit):
    import sqlalchemy as sa
    from bookflow.company import schema as c
    return set(s.company.conn.execute(sa.select(c.deposit_memberships.c.source_transaction_id).where(
        c.deposit_memberships.c.transaction_id == deposit)).scalars())


def authorize(s, deposit=None, sources=(), *, write=False):
    from bookflow.company.payment_authority import authorize_query
    from bookflow.company import schema as c
    import sqlalchemy as sa
    identifiers = set(sources)
    if deposit:
        identifiers.update(historical_sources(s, deposit))
    ordered = sorted(identifiers)
    for offset in range(0, max(1, len(ordered)), 200):
        selected = sa.select(c.transactions.c.id.label('transaction_id')).where(c.transactions.c.id.in_(ordered[offset:offset+200]))
        authorize_query(s, selected, write=write)
    return tuple(sorted(identifiers))


def require_unclaimed(s, source):
    """Ordinary source command admission; pure producer composition stays separate."""
    from bookflow.core.errors import BookflowError
    claim = active_claim(s, source)
    if claim is None:
        return
    _authorize_claim(s, source, claim)
    raise BookflowError('E_DEPOSIT_DEPENDENCY', details={
        'source': source, 'deposit': claim['transaction_id'],
        'next': 'deposit coordinate',
        'reason': 'A claimed receipt requires one atomic source and deposit correction.'})


def claim_details(s, source, claim):
    """Only after complete owner authorization; expose the actual claim event."""
    from bookflow.company import schema as c, document_effects
    _authorize_claim(s, source, claim)
    membership=document_effects.rows(s,c.deposit_memberships,c.deposit_memberships.c.id==claim['membership_id'])[0]
    return {'source':source,'deposit':claim['transaction_id'],'claim_event_id':membership['audit_event_id']}


def _authorize_claim(s, source, claim):
    from bookflow.core.errors import BookflowError
    try:
        authorize(s, claim['transaction_id'], (source,), write=True)
    except BookflowError as error:
        if error.code in ('E_PERMISSION','E_RECORD_NOT_FOUND'):
            raise BookflowError('E_DEPOSIT_DEPENDENCY') from None
        raise
