"""Strict whole-run reversal of drafts made by document delivery batches."""
from __future__ import annotations

import json
import sqlalchemy as sa

from bookflow import BookflowError
from bookflow.company import batches, schema as c

KIND = 'delivery-batch'
RECORD = 'delivery_batch'
COMMAND = 'delivery batch-cancel'


def shared_id(s, native_id):
    return s.company.conn.execute(sa.select(c.batches.c.id).where(
        c.batches.c.kind == KIND, c.batches.c.source_type == RECORD,
        c.batches.c.source_id == native_id)).scalar()


def record(s, ctx, native, drafted):
    """Record this committed chunk, preserving earlier messages' original versions."""
    from bookflow.company import delivery_batches as db
    batch_id = shared_id(s, native['id'])
    prior = {}
    if batch_id:
        previous = batches.effects(s, batch_id)
        if previous:
            prior = json.loads(previous[-1]['before'])['messages']
    else:
        batch_id = batches._insert_batch(s, ctx, kind=KIND, label=f"Delivery {native['number']}",
                                         scope=f"delivery:{native['id']}", ordering='record',
                                         source_type=RECORD, source_id=native['id'])
    for message in db._messages(s, native['id']):
        if message['number'] in drafted:
            prior[message['id']] = db.stamp(s, message)
    batches.record_effect(s, batch_id, record_type=RECORD, record_id=native['id'],
                          action='created' if not batches.effects(s, batch_id) else 'updated',
                          version_after=native['version'], before={'messages': prior})


def _read(s, batch):
    batches.require_read(s, 'delivery batch-show')


def _check(s, effect):
    from bookflow.company import delivery_batches as db
    native = db.row(s, effect['record_id'])
    problems = []
    if native['version'] != effect['version_after'] or native['state'] == 'cancelled':
        problems.append((RECORD, native['id'], f"Delivery batch {native['number']} changed since its last chunk."))
    original = json.loads(effect['before'])['messages']
    rows = db._messages(s, native['id'])
    for message in rows:
        if message['status'] == 'sent':
            problem = 'sent mail cannot be recalled'
        elif db.d._exported(s, message['id']):
            problem = 'exported files cannot be recalled'
        elif message['status'] != 'draft' or message['released_at']:
            problem = f"it is {message['status']}; only unreleased drafts can be reversed"
        else:
            problem = db.drift(s, message, original.get(message['id']))
        if problem:
            problems.append(('delivery_message', message['id'], f"Message {message['number']}: {problem}."))
    for missing in original.keys() - {m['id'] for m in rows}:
        problems.append(('delivery_message', missing, f"Message {original[missing]['number']} no longer exists."))
    return native, rows, problems


def _guard(s, effect):
    _, _, problems = _check(s, effect)
    if problems:
        raise BookflowError('E_WORK_DEPENDENCY', message='; '.join(p[2] for p in problems),
                            details={'batch': effect['record_id']})


def _plan(s, ctx, batch, effects, done, probe, blockers):
    effect = effects[-1] if effects else None
    if effect is None or (RECORD, effect['record_id']) in done:
        return dict(steps=[], work=[], blockers=blockers, records=int(effect is not None), pending=0)
    native, rows, problems = _check(s, effect)
    for record_type, record_id, message in problems:
        blockers.append(dict(seq=effect['seq'], record_type=record_type, record_id=record_id,
                             code='E_WORK_DEPENDENCY', message=message))
    payload = {'batch': native['id'], 'expected_version': native['version']}
    if probe:
        try:
            batches.run_nested(s, ctx, COMMAND, payload, source_ref=f"reverse:{batch['id']}", dry_run=True)
        except BookflowError as error:
            blockers.append(dict(seq=effect['seq'], record_type=RECORD, record_id=native['id'],
                                 code=error.code, message=error.message, details=error.details))
    step = dict(seq=effect['seq'], record_type=RECORD, record_id=native['id'],
                label=f"Delivery batch {native['number']}", action='cancel', version=native['version'],
                steps=[dict(operation=COMMAND, **payload)],
                messages=[dict(id=m['id'], number=m['number'], action='cancel') for m in rows],
                undrafted=native['planned'] - native['position'])
    return dict(steps=[step], work=[dict(step, effect=effect, payload=payload)], blockers=blockers,
                records=1, pending=1)


def _run(s, ctx, batch, reversal_id, item):
    effect = item['effect']
    def recorded(session, command, applied, output):
        batches.record_effect(session, reversal_id, record_type=RECORD, record_id=effect['record_id'],
                              action='reversed', version_after=output['version'],
                              before={'of_seq': effect['seq'], 'step': 0, 'command': COMMAND},
                              event_id=batches.last_event_id(session))
    batches.run_nested(s, ctx, COMMAND, item['payload'], source_ref=f"reverse:{batch['id']}",
                       recorder=recorded, before=lambda session: _guard(session, effect))


batches.register(batches.Adapter(KIND, planner=_plan, runner=_run, reader=_read,
                                 label='Delivery drafts', blocks=True))
