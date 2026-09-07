"""Shared document numbering, exact reversal construction and audited persistence."""
import json
import sqlalchemy as sa

from bookflow.company import schema as c, journal_custom_fields as custom
from bookflow.core import audit
from bookflow.core.errors import BookflowError
from bookflow.core.registry import Applied, Touched


def rows(s, table, *where, order=None):
    query = sa.select(table).where(*where)
    if order is not None:
        query = query.order_by(order)
    values = [dict(row) for row in s.company.conn.execute(query).mappings()]
    if table is c.posting_line_sources:
        for row in values:
            if row.get('payment_component_id') is None:
                row.pop('payment_component_id', None)
            if row.get('deposit_component_id') is None:
                row.pop('deposit_component_id', None)
    return values


def allocate(s, document_type, explicit, own=None):
    t = c.transactions
    def occupied(number):
        query = sa.select(t.c.id).where(t.c.type == document_type, t.c.number == number)
        if own:
            query = query.where(t.c.id != own)
        return s.company.conn.execute(query).first() is not None
    if explicit is not None:
        if occupied(explicit):
            raise BookflowError('E_DUPLICATE_NUMBER', details={'number': explicit, 'type': document_type})
        return explicit, None
    sequence = rows(s, c.sequences, c.sequences.c.name == document_type)
    number, prefix = (sequence[0]['next_number'], sequence[0]['prefix']) if sequence else (1, '')
    while occupied(f'{prefix}{number}'):
        number += 1
    if number >= 9223372036854775807:
        raise BookflowError('E_VALUE_RANGE', details={'field': 'next_number'})
    return f'{prefix}{number}', dict(name=document_type, next_number=number + 1, prefix=prefix)


def reverse(s, header, revision, current_batch, event, created, pending):
    inverse = dict(**created(), transaction_id=header['id'], revision_id=revision['id'], kind='reversal',
        effective_date=current_batch['effective_date'], reverses_batch_id=current_batch['id'],
        replaces_batch_id=None, audit_event_id=event)
    pending['posting_batches'].append(inverse)
    for leg in rows(s, c.posting_lines, c.posting_lines.c.batch_id == current_batch['id'], order=c.posting_lines.c.line_no):
        new = dict(leg, **created(), batch_id=inverse['id'], debit_minor_units=leg['credit_minor_units'],
            credit_minor_units=leg['debit_minor_units'], reversed_line_id=leg['id'])
        pending['posting_lines'].append(new)
        for source in rows(s, c.posting_line_sources, c.posting_line_sources.c.posting_line_id == leg['id']):
            # Preserve pre-payment source shape in old document audit/replay
            # facts and in mixed inverse/replacement bulk insert parameters.
            if source.get('payment_component_id') is None:
                source.pop('payment_component_id', None)
            pending['posting_line_sources'].append(dict(source, **created(), posting_line_id=new['id'],
                reversed_source_id=source['id']))
    return inverse


def decoded(row):
    return {key: json.loads(value) if key.endswith('_snapshot') and isinstance(value, str) else value
            for key, value in row.items()}


def persist(plan, ctx, s, *, command_name, table_kinds):
    """Caller validates the full aggregate first; dispatch owns transaction/commit."""
    if not plan.data['changed']:
        return Applied(plan.preview, [], 'no change')
    data = plan.data
    header, old, pending = data['header'], data['before'], data['pending']
    touched = [Touched('transaction', header['id'], 'update' if old else 'create',
        old['version'] if old else None, header['version'], header, old, db='company')]
    for table, kind, key in table_kinds:
        touched.extend(Touched(kind, row[key], 'create', None, 1, decoded(row), db='company') for row in pending[table])
    custom_plan = data.get('custom_plan')
    if custom_plan is not None:
        touched.extend(custom.touches(custom_plan))
    noun = header['type'].replace('_', ' ')
    summary = f"{data['operation']} {noun} {header['number']}"
    audit.write_event_to(s.company, ctx, command_name, summary, touched,
        actor_id=s.actor.id, actor_kind=s.actor.kind, directive_code=getattr(s, 'directive_code', None),
        event_id=data['event'])
    if old:
        s.company.conn.execute(c.transactions.update().where(c.transactions.c.id == header['id']).values(**header))
    else:
        s.company.conn.execute(c.transactions.insert().values(**header))
    for table, _, _ in table_kinds:
        if pending[table]:
            s.company.conn.execute(getattr(c, table).insert(), pending[table])
    if custom_plan is not None:
        custom.apply(s.company, custom_plan)
    if data['sequence']:
        from sqlalchemy.dialects.sqlite import insert
        stmt = insert(c.sequences).values(**data['sequence'])
        s.company.conn.execute(stmt.on_conflict_do_update(index_elements=['name'], set_=data['sequence']))
    return Applied(plan.preview, touched, summary, audited=True)
