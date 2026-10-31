"""One physical count is a reversible aggregate, including its entries and posted differences."""
from __future__ import annotations

import json
import sqlalchemy as sa

from bookflow import BookflowError
from bookflow.company import batches, schema as c

KIND = 'inventory-count'


def linked(s, count_id):
    return s.company.conn.execute(sa.select(c.batches.c.id).where(
        c.batches.c.kind == KIND, c.batches.c.source_id == count_id)).scalar()


def record(s, ctx, count, *, created=False, event_id=None):
    from bookflow.company import inventory
    batch_id = linked(s, count['id'])
    if batch_id is None:
        batch_id = batches._insert_batch(s, ctx, kind=KIND, label=f"Inventory count {count['number']}",
            scope=f"inventory-count:{count['id']}", ordering='record',
            source_type='inventory_count', source_id=count['id'])
    batches.record_effect(s, batch_id, record_type='inventory_count', record_id=count['id'],
        action='created' if created else 'updated', version_after=count['version'],
        before={'stock_watermark': inventory.next_sequence(s), 'status': count['status']},
        event_id=event_id or batches.last_event_id(s))
    return batch_id


def _read(s, batch):
    batches.require_read(s, 'inventory-count show')


def _check(s, batch, effect, count):
    from bookflow.company import inventory_counts as counts
    problems = []
    def add(code, message, **details):
        problems.append(dict(seq=effect['seq'], record_type='inventory_count', record_id=count['id'],
                             code=code, message=message, details=details))
    if count['status'] not in ('open', 'recorded'):
        add('count_ended', f"Count {count['number']} is already {count['status']}.")
        return problems
    if count['version'] != effect['version_after']:
        add('edited_again', f"Count {count['number']} changed since its batch was recorded.")
    if count['status'] == 'open':
        return problems
    lines = counts.count_lines(s, count['id'])
    items = {line['item_id'] for line in lines if line['counted_microunits'] is not None}
    k, ln = c.inventory_counts, c.inventory_count_lines
    a, e = c.audit_entries, c.audit_events
    posted_seq = sa.select(e.c.seq).where(e.c.id == effect['event_id']).scalar_subquery()
    later = s.company.conn.execute(sa.select(k.c.id, k.c.number).join(ln, ln.c.count_id == k.c.id)
        .join(a, sa.and_(a.c.record_type == 'inventory_count', a.c.record_id == k.c.id))
        .join(e, e.c.id == a.c.event_id).where(
            k.c.id != count['id'], k.c.status.in_(('open', 'recorded')), ln.c.item_id.in_(items),
            e.c.command.in_(('inventory-count create', 'inventory-count post')), e.c.seq > posted_seq)
        .distinct()).all()
    for ident, number in later:
        add('later_count', f"Count {count['number']} is used by later count {number} ({ident}); reverse it first.",
            count=ident)
    mark = json.loads(effect['before'])['stock_watermark']
    own = [row['transaction_id'] for row in counts.postings(s, count['id'])]
    m = c.inventory_movements
    retired = sa.select(m.c.reverses_movement_id).where(m.c.reverses_movement_id.is_not(None))
    moves = s.company.conn.execute(sa.select(m.c.transaction_id, m.c.item_id).where(
        m.c.item_id.in_(items), m.c.sequence >= mark, m.c.kind != 'reversal',
        m.c.id.not_in(retired), m.c.transaction_id.not_in(own)).distinct()).all()
    for ident, item in moves:
        doc = s.company.conn.execute(sa.select(c.transactions.c.type, c.transactions.c.number).where(
            c.transactions.c.id == ident)).first()
        label = f'{doc[0]} {doc[1]}' if doc else ident
        add('later_stock', f"Count {count['number']}: later stock document {label} ({ident}) uses counted item "
            f"{counts.inventory.item_name(s, item)}; reverse it first.", transaction=ident, item=item)
    p = c.inventory_placements
    retired_places = sa.select(p.c.reverses_placement_id).where(p.c.reverses_placement_id.is_not(None))
    changed_places = s.company.conn.execute(sa.select(p).where(
        p.c.item_id.in_(items), p.c.sequence >= mark, p.c.source.in_(('transfer', 'assignment')),
        p.c.reverses_placement_id.is_(None), p.c.id.not_in(retired_places))).mappings()
    for place in changed_places:
        ident = place['transfer_revision_id'] or place['source_line_id']
        add('later_placement', f"Count {count['number']}: later {place['source']} {ident} changed counted item "
            f"{counts.inventory.item_name(s, place['item_id'])}; reverse it first.", placement=place['id'])
    return problems


def _plan(s, ctx, batch, effects, done, probe, blockers):
    from bookflow.company import inventory_counts as counts
    effect = effects[-1]
    count = counts.resolve(s, effect['record_id'])
    if ('inventory_count', count['id']) in done:
        return dict(steps=[], work=[], blockers=blockers, records=1, pending=0)
    blockers.extend(_check(s, batch, effect, count))
    operation = 'inventory-count ' + ('cancel' if count['status'] == 'open' else 'void')
    payload = dict(count=count['id'], expected_version=count['version'])
    if probe:
        try:
            batches.run_nested(s, ctx, operation, payload, source_ref=f"reverse:{batch['id']}", dry_run=True)
        except BookflowError as error:
            blockers.append(dict(seq=effect['seq'], record_type='inventory_count', record_id=count['id'],
                code=error.code, message=f"Count {count['number']}: {error.message}", details=error.details))
    entries = counts.count_entries(s, count['id'])
    entered = counts.counted_by_line(entries)
    detail = dict(seq=effect['seq'], record_type='inventory_count', record_id=count['id'],
        label=f"Count {count['number']}", operation=operation, version=count['version'],
        steps=[dict(operation=operation, **payload)],
        documents=[dict(transaction_id=d['transaction_id'], number=d['number'], action='void differences')
                   for d in counts.postings(s, count['id']) if d['status'] != 'voided'],
        items=[dict(item_id=line['item_id'], counted=entered.get(line['id']))
               for line in counts.count_lines(s, count['id'])])
    detail['steps'][0].update(documents=detail['documents'], items=detail['items'],
                              entries=entries, postings=counts.own_movements(s, count))
    return dict(steps=[detail], work=[dict(detail, effect=effect, payload=payload)],
                blockers=blockers, records=1, pending=1)


def _run(s, ctx, batch, reversal_id, item):
    from bookflow.company import inventory_counts as counts
    def check(session):
        problems = _check(session, batch, item['effect'], counts.resolve(session, item['record_id']))
        if problems:
            raise BookflowError('E_BATCH_BLOCKED', message=problems[0]['message'], details={'blockers': problems})
    batches.run_nested(s, ctx, item['operation'], item['payload'], source_ref=f"reverse:{batch['id']}",
        before=check, recorder=batches._reversal_recorder(reversal_id, item['effect'], 0, None))


batches.register(batches.Adapter(KIND, planner=_plan, runner=_run, reader=_read, blocks=True,
                                 label='Inventory counts'))
