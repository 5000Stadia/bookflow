"""Source basis, bounded interval reads and saved allocation representations."""
from contextlib import contextmanager
from fractions import Fraction
import hashlib
import json

import sqlalchemy as sa

from bookflow.company import schema as c
from bookflow.company.billing_facts import AllocationProof, TaxAllocationProof, ExactFraction
from bookflow.company.sales_facts import LATER_LINE_PROFILE_FIELDS
from bookflow.company.work_tax_facts import read_line, read_facts, basis_hash, economic_basis
from bookflow.company import tax_policy
from bookflow.core.errors import BookflowError
from bookflow.core.exact import format_quantity_micro_units


def _digest(values):
    encoded = json.dumps(values, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(encoded.encode()).hexdigest()


def _basis_values(line, root, policy):
    if line.schema_version==2:return economic_basis(line,policy), 'economics'
    return dict(basis_version=1, root_document_id=root[0], root_line_id=root[1],
        line=line.model_dump(mode='json', exclude={'completed_quantity_microunits', 'billable'})), 'line'


def basis(line, root, policy=None):
    if line.schema_version==2:return basis_hash(line,policy)
    return _digest(_basis_values(line, root, policy)[0])


def matches_basis(stored, line, root, policy=None):
    """A stored proof hash matches these facts in today's shape or, when the line carries none of
    the later profile fields, in the shape a proof stored before those fields existed hashed.
    Stored hashes are never rewritten; new proofs always carry today's shape."""
    if stored == basis(line, root, policy):
        return True
    if any(getattr(line.profile, field) is not None for field in LATER_LINE_PROFILE_FIELDS):
        return False
    values, key = _basis_values(line, root, policy)
    for field in LATER_LINE_PROFILE_FIELDS:
        values[key]['profile'].pop(field, None)
    return stored == _digest(values)


def captured_line(row):
    snapshot = json.loads(row['facts_snapshot'])
    return read_line(snapshot['line'])


def captured_policy(row):
    return tax_policy.effective(read_facts(json.loads(row['facts_snapshot'])['document']).profile)

def source_policy(s,line):
    from bookflow.company import work
    rev=work.rows(s,c.work_revisions,c.work_revisions.c.id==line['revision_id'])[0]
    return tax_policy.effective(work.facts(rev).profile)


def proof_basis(s, root, facts, policy=None, *, excluding=None):
    """The hash a new proof of this root carries. Every active proof of one root shares one
    hash, so when active proofs carry an accepted earlier shape of these same facts, a new
    proof continues it; otherwise today's shape."""
    return within_reading(s, ('proof_basis',) + _facts_key(root, facts, excluding, policy),
                          lambda: _proof_basis(s, root, facts, policy, excluding=excluding))


def _proof_basis(s, root, facts, policy=None, *, excluding=None):
    current = basis(facts, root, policy)
    a = c.work_billing_allocations
    query = active_query([root], excluding=excluding).with_only_columns(a.c.source_basis_hash).where(
        a.c.allocation_version.in_((2, 3))).limit(1)
    stored = s.company.conn.execute(query).scalar()
    return stored if stored is not None and stored != current and matches_basis(stored, facts, root, policy) else current


def make_proof(s, source, revision, line, root, facts, spans, *, excluding=None):
    from bookflow.company import billing_math as math
    model=TaxAllocationProof if facts.schema_version==2 else AllocationProof
    extra={'basis_version':2} if facts.schema_version==2 else {}
    policy=tax_policy.effective(read_facts(revision['facts_snapshot']).profile)
    return model(**extra,source_document_id=source['id'], source_revision_id=revision['id'],
        source_line_id=line['id'], root_document_id=root[0], root_line_id=root[1],
        source_basis_hash=proof_basis(s, root, facts, policy, excluding=excluding), quoted_quantity_microunits=facts.quantity_microunits,
        quoted_base_quantity_microunits=facts.base_quantity_microunits,
        quoted_net_minor_units=facts.net_minor_units,
        denominator=str(math.denominator(facts.quantity_microunits, facts.net_minor_units)),
        spans=[dict(start=str(a), end=str(b)) for a, b in spans])


def stored_proof(proof):
    from bookflow.company import billing_math as math
    if proof is None:
        return dict(allocation_version=1, source_basis_hash=None, denominator_hex=None, spans_json=None)
    return dict(allocation_version=3 if isinstance(proof,TaxAllocationProof) else 2, source_basis_hash=proof.source_basis_hash,
        denominator_hex=math.coordinate_hex(int(proof.denominator)),
        spans_json=json.dumps([[math.coordinate_hex(a), math.coordinate_hex(b)] for a,b in proof.intervals()],
                             separators=(',', ':')))


def read_proof(row):
    if row.get('allocation_version', 1) == 1:
        return None
    from bookflow.company import billing_math as math
    facts = captured_line(row)
    model=TaxAllocationProof if row['allocation_version']==3 else AllocationProof
    if row['allocation_version'] not in (2,3) or (facts.schema_version==2)!=(row['allocation_version']==3):
        raise BookflowError('E_INTERNAL',message='Stored allocation version and work basis disagree')
    extra={'basis_version':2} if row['allocation_version']==3 else {}
    return model(**extra,**{k: row[k] for k in ('source_document_id', 'source_revision_id', 'source_line_id',
        'root_document_id', 'root_line_id', 'source_basis_hash')},
        quoted_quantity_microunits=facts.quantity_microunits,
        quoted_base_quantity_microunits=facts.base_quantity_microunits,
        quoted_net_minor_units=facts.net_minor_units,
        denominator=str(math.coordinate_int(row['denominator_hex'])),
        spans=[dict(start=str(math.coordinate_int(a)), end=str(math.coordinate_int(b)))
               for a,b in json.loads(row['spans_json'])])


def source_output(row):
    values = {k:v for k,v in row.items() if k not in ('source_basis_hash', 'denominator_hex', 'spans_json')}
    return dict(values, facts_snapshot=json.loads(row['facts_snapshot']), allocation_proof=read_proof(row))


def quantity_output(line):
    """Ordinary outputs keep microunit formatting; allocated quantities stay exact."""
    from bookflow.company.sales_facts import SalesLineProfile
    facts = SalesLineProfile.model_validate_json(line['item_snapshot'])
    if facts.pricing_basis != 'allocated':
        return dict(quantity=format_quantity_micro_units(line['quantity_microunits']),
                    base_quantity=format_quantity_micro_units(line['base_quantity_microunits']))
    from bookflow.company import billing_math as math
    proof = facts.allocation_proof
    return dict(quantity=math.format_fraction(proof.quantity()),
        base_quantity=math.format_fraction(proof.quantity(base=True)),
        quantity_fraction=ExactFraction.of(proof.quantity()),
        base_quantity_fraction=ExactFraction.of(proof.quantity(base=True)),
        quoted_quantity=format_quantity_micro_units(proof.quoted_quantity_microunits))


def active_query(roots, *, excluding=None):
    a, t = c.work_billing_allocations, c.transactions
    query = sa.select(a, t.c.type.label('destination_type')).join(t,
        sa.and_(t.c.id == a.c.transaction_id, t.c.current_revision_id == a.c.revision_id,
                t.c.status == 'posted')).where(sa.tuple_(a.c.root_document_id, a.c.root_line_id).in_(roots))
    if excluding:
        query = query.where(t.c.id != excluding)
    return query


def occupied_roots(s, roots):
    a = c.work_billing_allocations
    query = active_query(roots).with_only_columns(a.c.root_document_id, a.c.root_line_id).distinct()
    return {tuple(row) for row in s.company.conn.execute(query)}


def has_active_for_document(s, document_id):
    a, t, i = c.work_billing_allocations,c.transactions,c.work_line_identities
    query = sa.select(a.c.id).join(t,sa.and_(t.c.id == a.c.transaction_id,
        t.c.current_revision_id == a.c.revision_id,t.c.status == 'posted')).join(i,
        sa.and_(i.c.root_document_id == a.c.root_document_id,i.c.root_line_id == a.c.root_line_id)).where(
            i.c.document_id == document_id).limit(1)
    return s.company.conn.execute(query).first() is not None


@contextmanager
def one_reading(s):
    """Within one billing preparation or billing read, read each root's history once.

    A preparation asks for the same root's occupied scope many times over (selection,
    posting checks, the remaining-work forecast, validation), and each ask used to scan
    the root's whole allocation history again, so one bill cost history x asks. Inside
    this scope a root's occupied and free spans, its proof basis, the consumption
    fingerprint, posting eligibility and the remaining-work forecast are each read once
    (`within_reading`) and reused. The scope writes
    nothing and holds nothing past its own exit: a plan phase's preview is recomputed by
    the apply phase inside its own write transaction, and any write on the company
    connection inside the scope drops everything read so far.
    """
    if getattr(s, '_allocation_reads', None) is not None:
        yield
        return
    s._allocation_reads = {}
    try:
        yield
    finally:
        s._allocation_reads = None


def _reads(s):
    reads = getattr(s, '_allocation_reads', None)
    if reads is None:
        return None
    changes = s.company.raw.total_changes
    if reads.get('changes') != changes:
        reads.clear()
        reads['changes'] = changes
    return reads


def within_reading(s, key, compute):
    """`compute()` once per key inside `one_reading`; every call outside it computes afresh.

    Only for pure reads of the company database whose answer the key fully determines. A
    failure is never kept: it is raised again by computing again."""
    reads = _reads(s)
    if reads is None:
        return compute()
    if key not in reads:
        reads[key] = compute()
    return reads[key]


def _facts_key(root, facts, excluding, policy):
    # The serialized facts fix the denominator and the expected basis hash (and with it
    # the legacy-shape fallback), so equal keys give equal checked answers.
    return (tuple(root), excluding, policy, type(facts).__name__, facts.model_dump_json())


def occupied_spans(s, root, facts, *, excluding=None, policy=None):
    """SQLite orders canonical hex coordinates; Python retains one row at a time.

    Inside `one_reading` a root's checked spans are read once and reused."""
    reads = _reads(s)
    if reads is None:
        yield from _occupied_spans(s, root, facts, excluding, policy)
        return
    key = ('occupied',) + _facts_key(root, facts, excluding, policy)
    if key not in reads:
        reads[key] = tuple(_occupied_spans(s, root, facts, excluding, policy))
    yield from reads[key]


def _occupied_spans(s, root, facts, excluding, policy):
    from bookflow.company import billing_math as math
    denominator = math.denominator(facts.quantity_microunits, facts.net_minor_units)
    expected_basis = basis(facts, root, policy)
    query = sa.text('''
        SELECT a.allocation_version, a.source_basis_hash, a.denominator_hex,
               CASE WHEN a.allocation_version=1 THEN :zero ELSE json_extract(span.value,'$[0]') END AS start,
               CASE WHEN a.allocation_version=1 THEN :full ELSE json_extract(span.value,'$[1]') END AS end
        FROM work_billing_allocations a JOIN transactions t
          ON t.id=a.transaction_id AND t.current_revision_id=a.revision_id AND t.status='posted'
        JOIN json_each(CASE WHEN a.allocation_version=1 THEN '[null]' ELSE a.spans_json END) span
        WHERE a.root_document_id=:document AND a.root_line_id=:line
          AND (:excluded IS NULL OR a.transaction_id<>:excluded)
        ORDER BY start COLLATE BINARY, end COLLATE BINARY
    ''')
    params = dict(zero=math.coordinate_hex(0), full=math.coordinate_hex(denominator),
        document=root[0], line=root[1], excluded=excluding)
    with s.company.conn.execute(query, params) as result:
        for row in result.mappings():
            if row['allocation_version'] in (2,3) and (row['denominator_hex'] != params['full']
                    or (row['source_basis_hash'] != expected_basis and not matches_basis(row['source_basis_hash'], facts, root, policy))):
                raise BookflowError('E_WORK_DEPENDENCY', details=dict(problem='active billing uses a different source basis',
                    root_document_id=root[0], root_line_id=root[1]))
            yield math.coordinate_int(row['start']), math.coordinate_int(row['end'])


def free_spans(s, root, facts, *, excluding=None, policy=None):
    from bookflow.company import billing_math as math
    free = lambda: math.free_spans(occupied_spans(s, root, facts, excluding=excluding, policy=policy),
                                   math.denominator(facts.quantity_microunits, facts.net_minor_units))
    reads = _reads(s)
    if reads is None:
        return free()
    key = ('free',) + _facts_key(root, facts, excluding, policy)
    if key not in reads:
        reads[key] = tuple(free())
    return iter(reads[key])


def remaining(s, root, facts, *, policy=None):
    from bookflow.company import billing_math as math
    d = math.denominator(facts.quantity_microunits, facts.net_minor_units)
    length = net = 0
    for a,b in free_spans(s, root, facts, policy=policy):
        length += b-a
        net += math.portion(facts.net_minor_units, a, b, d)
    return length, net


def consumption_fingerprint(s, roots):
    """Hash all current source allocations without materializing their history."""
    return within_reading(s, ('consumption', tuple(tuple(root) for root in roots)),
                          lambda: _consumption_fingerprint(s, roots))


def _consumption_fingerprint(s, roots):
    a = c.work_billing_allocations
    keys = ('root_document_id', 'root_line_id', 'id', 'transaction_id', 'revision_id',
            'document_line_id', 'allocation_version', 'source_basis_hash', 'denominator_hex', 'spans_json')
    query = active_query(roots).with_only_columns(*(a.c[key] for key in keys)).order_by(
        a.c.root_document_id, a.c.root_line_id, a.c.id)
    digest = hashlib.sha256()
    with s.company.conn.execute(query) as rows:
        for row in rows:
            digest.update(json.dumps(tuple(row), separators=(',', ':')).encode())
            digest.update(b'\n')
    return digest.hexdigest()


def active_totals(s, root):
    a, t = c.work_billing_allocations, c.transactions
    aggregate = active_query([root]).with_only_columns(a.c.transaction_id,t.c.type,a.c.net_minor_units,a.c.tax_minor_units)
    count = net = tax = 0
    one = None
    # Cumulative rounded tax need not fit SQLite SUM's signed64 accumulator.
    # Python integers preserve it without retaining the transaction history.
    with s.company.conn.execute(aggregate) as rows:
        for transaction, kind, own_net, own_tax in rows:
            count += 1
            net += own_net
            tax += own_tax
            one = (transaction,kind)
    # Only a sole current destination can be represented by the legacy singular link.
    one = one if count == 1 else None
    return dict(count=count, net=net, tax=tax, destination_id=one[0] if one else None,
                destination_type=one[1] if one else None)
