"""Reverse one bulk membership enrollment (R188 increment 2) through the shared batch door.

``customer membership enroll`` writes one ``membership-enroll`` batch whose effects are the enrollments it
created. ``batch reverse <id>`` deletes them newest first, each in its own transaction with its own audit
event, under the caller's own ``customer membership set`` authority (promotion.assign and customer write).
Nothing is reversed while any enrollment is refused: one changed since the run (ended or edited: ending it is
a separate, prospective act, not an alleged reversal) or one a saved sale captured (the sale's price came
from it, so erasing it would orphan that fact). Each blocker names the enrollment, the customer and, for a
sale, its type and number.
"""
from __future__ import annotations

import sqlalchemy as sa

from bookflow import BookflowError
from bookflow.company import automation, batches, schema as c
from bookflow.company.memberships import BATCH_KIND, ENROLLMENT
from bookflow.core.audit import write_event_to
from bookflow.core.registry import Touched

AUTHORITY = 'customer membership set'


def _read(s, batch):
    # The caller's own `customer membership show` authority (promotion.read and customer read).
    automation.require_command(s, 'customer membership show')


def _row(s, record_id):
    t = c.customer_memberships
    row = s.company.conn.execute(sa.select(t).where(t.c.id == record_id)).mappings().first()
    return dict(row) if row else None


def _label(s, effect, row):
    customer_id = (row or {}).get('customer_id')
    name = s.company.conn.execute(sa.select(c.customers.c.full_name).where(
        c.customers.c.id == customer_id)).scalar() if customer_id else None
    return f"enrollment {effect['record_id']}" + (f' ({name})' if name else '')


def sales_using(s, enrollment_id: str) -> list[dict]:
    """Saved sales (any revision) whose captured membership is this enrollment."""
    rows = s.company.raw.execute(
        "SELECT DISTINCT t.id, t.type, t.number FROM sales_profiles p JOIN transactions t ON t.id = p.transaction_id "
        "WHERE json_extract(p.profile_snapshot, '$.membership.enrollment_id') = ? ORDER BY t.rowid LIMIT 20",
        (enrollment_id,)).fetchall()
    return [dict(id=r[0], type=r[1], number=r[2]) for r in rows]


def _problems(s, effect, row, label):
    if row is None:
        return [('E_RECORD_NOT_FOUND', f'{label} no longer exists.', {})]
    found = []
    if row['version'] != effect['version_after']:
        found.append(('edited_again', f"{label} changed since this run (now {row['status']}, version {row['version']}); "
                      'end it instead of reversing it.', dict(current_version=row['version'])))
    used = sales_using(s, row['id'])
    if used:
        named = ', '.join(f"{u['type'].replace('_', ' ')} {u['number'] or u['id']}" for u in used)
        found.append(('in_use', f'{label} priced {named}; a sale that used it keeps it. End the enrollment instead.',
                      dict(sales=used)))
    return found


def _plan(s, ctx, batch, effects, done, probe, blockers):
    pending = [e for e in reversed(effects) if (e['record_type'], e['record_id']) not in done]
    steps, work = [], []
    for effect in pending:
        row = _row(s, effect['record_id'])
        label = _label(s, effect, row)
        for code, message, details in _problems(s, effect, row, label):
            blockers.append(dict(seq=effect['seq'], record_type=ENROLLMENT, record_id=effect['record_id'],
                                 code=code, message=message, details=details))
        if probe:
            try:
                automation.require_command(s, AUTHORITY)
            except BookflowError as error:
                blockers.append(dict(seq=effect['seq'], record_type=ENROLLMENT, record_id=effect['record_id'],
                                     code=error.code, message=f'{label}: {error.message}', details=error.details))
        steps.append(dict(seq=effect['seq'], record_type=ENROLLMENT, record_id=effect['record_id'], label=label,
                          action='delete', operation='delete enrollment', authority=AUTHORITY,
                          version=row['version'] if row else None,
                          steps=[{'operation': 'delete enrollment', 'authority': AUTHORITY}]))
        work.append(dict(effect=effect, seq=effect['seq'], label=label))
    return dict(steps=steps, work=work, blockers=blockers, records=len(effects), pending=len(pending))


def _run(s, ctx, batch, reversal_id, item):
    def delete():
        automation.require_command(s, AUTHORITY)
        effect = item['effect']
        row = _row(s, effect['record_id'])
        problems = _problems(s, effect, row, item['label'])
        if problems:
            raise BookflowError('E_VALIDATION', message=problems[0][1], details={'membership': effect['record_id']})
        t = c.customer_memberships
        if s.company.conn.execute(t.delete().where(t.c.id == row['id'], t.c.version == row['version'])).rowcount != 1:
            raise BookflowError('E_VERSION_CONFLICT', details={'record_type': ENROLLMENT, 'record_id': row['id']})
        touched = Touched(ENROLLMENT, row['id'], 'delete', row['version'], None, None, row, db='company')
        event = write_event_to(s.company, ctx, 'batch reverse', f"Reversed {item['label']} from batch {batch['id']}",
                               [touched], actor_id=s.actor.id, actor_kind=s.actor.kind,
                               directive_code=getattr(s, 'directive_code', None))
        batches.record_effect(s, reversal_id, record_type=ENROLLMENT, record_id=row['id'], action='reversed',
                              version_after=None, before={'of_seq': effect['seq'], 'command': 'batch reverse',
                                                          'row': {k: row[k] for k in ('customer_id', 'plan_id',
                                                                  'start_date', 'end_date', 'status')}},
                              event_id=event)
    from bookflow.company.bank_sort import _in_tx
    _in_tx(s, delete)


batches.register(batches.Adapter(BATCH_KIND, planner=_plan, runner=_run, reader=_read, blocks=True,
                                 label='Membership enrollment'))
