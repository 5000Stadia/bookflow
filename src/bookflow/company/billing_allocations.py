"""Source basis, bounded interval reads and saved allocation representations."""
from fractions import Fraction
import hashlib
import json

import sqlalchemy as sa

from bookflow.company import schema as c
from bookflow.company.billing_facts import AllocationProof, ExactFraction
from bookflow.company.work_facts import WorkLineFacts
from bookflow.core.errors import BookflowError
from bookflow.core.exact import format_quantity_micro_units


def basis(line, root):
    values = dict(basis_version=1, root_document_id=root[0], root_line_id=root[1],
        line=line.model_dump(mode='json', exclude={'completed_quantity_microunits', 'billable'}))
    encoded = json.dumps(values, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(encoded.encode()).hexdigest()


def captured_line(row):
    snapshot = json.loads(row['facts_snapshot'])
    return WorkLineFacts.model_validate(snapshot['line'])


def make_proof(source, revision, line, root, facts, spans):
    from bookflow.company import billing_math as math
    return AllocationProof(source_document_id=source['id'], source_revision_id=revision['id'],
        source_line_id=line['id'], root_document_id=root[0], root_line_id=root[1],
        source_basis_hash=basis(facts, root), quoted_quantity_microunits=facts.quantity_microunits,
        quoted_base_quantity_microunits=facts.base_quantity_microunits,
        quoted_net_minor_units=facts.net_minor_units,
        denominator=str(math.denominator(facts.quantity_microunits, facts.net_minor_units)),
        spans=[dict(start=str(a), end=str(b)) for a, b in spans])


def stored_proof(proof):
    from bookflow.company import billing_math as math
    if proof is None:
        return dict(allocation_version=1, source_basis_hash=None, denominator_hex=None, spans_json=None)
    return dict(allocation_version=2, source_basis_hash=proof.source_basis_hash,
        denominator_hex=math.coordinate_hex(int(proof.denominator)),
        spans_json=json.dumps([[math.coordinate_hex(a), math.coordinate_hex(b)] for a,b in proof.intervals()],
                             separators=(',', ':')))


def read_proof(row):
    if row.get('allocation_version', 1) == 1:
        return None
    from bookflow.company import billing_math as math
    facts = captured_line(row)
    return AllocationProof(**{k: row[k] for k in ('source_document_id', 'source_revision_id', 'source_line_id',
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


def occupied_spans(s, root, facts, *, excluding=None):
    """SQLite orders canonical hex coordinates; Python retains one row at a time."""
    from bookflow.company import billing_math as math
    denominator = math.denominator(facts.quantity_microunits, facts.net_minor_units)
    expected_basis = basis(facts, root)
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
            if row['allocation_version'] == 2 and (row['source_basis_hash'] != expected_basis
                    or row['denominator_hex'] != params['full']):
                raise BookflowError('E_WORK_DEPENDENCY', details=dict(problem='active billing uses a different source basis',
                    root_document_id=root[0], root_line_id=root[1]))
            yield math.coordinate_int(row['start']), math.coordinate_int(row['end'])


def free_spans(s, root, facts, *, excluding=None):
    from bookflow.company import billing_math as math
    return math.free_spans(occupied_spans(s, root, facts, excluding=excluding),
                          math.denominator(facts.quantity_microunits, facts.net_minor_units))


def remaining(s, root, facts):
    from bookflow.company import billing_math as math
    d = math.denominator(facts.quantity_microunits, facts.net_minor_units)
    length = net = 0
    for a,b in free_spans(s, root, facts):
        length += b-a
        net += math.portion(facts.net_minor_units, a, b, d)
    return length, net


def consumption_fingerprint(s, roots):
    """Hash all current source allocations without materializing their history."""
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
