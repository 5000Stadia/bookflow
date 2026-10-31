"""Restore proposals changed by one bank-rule apply call, through the shared batch engine.

The existing bank-line undo grant authorizes this inverse. Every line must remain waiting;
prior proposals and rule ids are restored together, preserving audit and version history.
"""
from __future__ import annotations

import json

import sqlalchemy as sa

from bookflow import BookflowError
from bookflow.company import automation, bank_sort, batches, schema as c
from bookflow.core.audit import write_event_to
from bookflow.core.registry import Touched
from bookflow.core.ids import new_id

KIND = 'bank-rule-apply'
FIELDS = ('proposal', 'rule_id')


def _read(s, batch):
    batches.require_read(s, 'bank-line query')


def _line(s, record_id):
    row = s.company.conn.execute(sa.select(c.bank_lines).where(c.bank_lines.c.id == record_id)).mappings().first()
    return dict(row) if row is not None else None


def _check(s, batch, effect, line):
    label = f"bank line {effect['record_id']}" + (f" ({line['payee']})" if line else '')
    if line is None:
        return 'E_RECORD_NOT_FOUND', f'{label} no longer exists.'
    if not line['live'] or line['state'] != 'waiting':
        return 'line_decided', f"{label} is {line['state']}" + (
            '; its import was reversed.' if not line['live'] else '; undo that decision first.')
    chain = batches._chains(s, batch, [('bank_line', line['id'])]).get(('bank_line', line['id']))
    # A bank decision also appends to its older import's ledger. That provenance is
    # not a later batch: only batches opened after this proposal run can stand in its way.
    later = s.company.raw.execute(
        "SELECT b.id, b.label FROM batch_effects e JOIN batches b ON b.id = e.batch_id "
        "WHERE e.record_type = 'bank_line' AND e.record_id = ? AND b.reversal_of IS NULL "
        "AND b.rowid > (SELECT rowid FROM batches WHERE id = ?) "
        "AND NOT EXISTS (SELECT 1 FROM batch_effects r WHERE r.batch_id = b.reversed_by "
        "AND r.record_type = 'bank_line' AND r.record_id = e.record_id) ORDER BY e.rowid DESC LIMIT 1",
        (line['id'], batch['id'])).fetchone()
    if later:
        return 'later_batch', f"{label} was changed by later batch {later[0]} ({later[1]}); reverse it first."
    before = json.loads(effect['before'])
    if (chain and (chain['broken'] or chain['expected'] != line['version'])) or any(
            line[k] != before['after'][k] for k in FIELDS):
        return 'edited_again', f'{label} changed since this run; its current proposal cannot be overwritten.'
    return None


def _plan(s, ctx, batch, effects, done, probe, blockers):
    pending = [e for e in reversed(effects) if (e['record_type'], e['record_id']) not in done]
    steps, work = [], []
    for effect in pending:
        line = _line(s, effect['record_id'])
        label = f"bank line {effect['record_id']}" + (f" ({line['payee']})" if line else '')
        problem = _check(s, batch, effect, line)
        if problem is not None:
            code, message = problem
            blockers.append(dict(seq=effect['seq'], record_type='bank_line', record_id=effect['record_id'],
                                 code=code, message=message))
        if probe:
            try:
                automation.require_command(s, 'bank-line undo')
            except BookflowError as error:
                blockers.append(dict(seq=effect['seq'], record_type='bank_line', record_id=effect['record_id'],
                                     code=error.code, message=f'{label}: {error.message}', details=error.details))
        before = json.loads(effect['before'])
        steps.append(dict(seq=effect['seq'], record_type='bank_line', record_id=effect['record_id'],
                          label=label, action='restore',
                          operation='restore bank-line proposal', authority='bank-line undo',
                          version=line['version'] if line else None,
                          steps=[{'operation': 'restore bank-line proposal', 'authority': 'bank-line undo',
                                  'fields': {k: before[k] for k in FIELDS}}],
                          fields={k: before[k] for k in FIELDS}))
        work.append(dict(effect=effect, record_type='bank_line', record_id=effect['record_id'],
                         seq=effect['seq'], label=label))
    return dict(steps=steps, work=work, blockers=blockers, records=len(effects), pending=len(pending))


def _run(s, ctx, batch, reversal_id, item):
    def restore():
        automation.require_command(s, 'bank-line undo')
        effect = item['effect']
        line = _line(s, effect['record_id'])
        problem = _check(s, batch, effect, line)
        if problem is not None:
            raise BookflowError('E_VALIDATION', message=problem[1], details={'line': effect['record_id']})
        before = json.loads(effect['before'])
        values = {k: before[k] for k in FIELDS}
        touched = Touched('bank_line', line['id'], 'update', line['version'], line['version'] + 1,
                          values, {k: line[k] for k in FIELDS}, db='company')
        s.company.conn.execute(c.bank_lines.update().where(c.bank_lines.c.id == line['id']).values(
            **values, version=line['version'] + 1))
        event = write_event_to(s.company, ctx, 'bank-line undo',
                              f"Restored prior proposal for {item['label']}", [touched],
                              actor_id=s.actor.id, actor_kind=s.actor.kind,
                              directive_code=getattr(s, 'directive_code', None))
        s.company.conn.execute(c.bank_line_events.insert().values(
            id=new_id(), line_id=line['id'], kind='reversed', import_id=line['import_id'], batch_id=reversal_id,
            details=json.dumps({'restored': values, 'reversal_of': batch['id']}, sort_keys=True),
            created_by=s.actor.id, created_via=bank_sort._via(ctx), created_at=batches.now(), audit_event_id=event))
        batches.record_effect(s, reversal_id, record_type='bank_line', record_id=line['id'], action='reversed',
                              version_after=line['version'] + 1, before={'of_seq': effect['seq'],
                              'command': 'bank-line undo'}, event_id=event)
    bank_sort._in_tx(s, restore)


batches.register(batches.Adapter(KIND, planner=_plan, runner=_run, reader=_read, blocks=True,
                                 label='Bank rule proposals'))
