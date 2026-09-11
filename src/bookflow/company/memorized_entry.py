"""Entering what a memorized transaction owes: claim the slot, replay the command, record it.

The order is the whole safety argument, so it is written out rather than implied.

1. **Claim, and commit.** Every occurrence this run will touch is written as a ``pending`` row
   and the schedule is advanced past those slots, in one transaction that commits before
   anything is dispatched. After this point the slot exists, its accounting date is fixed, and
   nothing that happens next can move it.
2. **Replay, one occurrence at a time.** Each entry goes through ``run_in_session`` -- the same
   dispatch every adapter uses -- carrying the occurrence's ``entry_key`` as the command
   idempotency key and ``memorized-occurrence:<id>`` as the source reference. Each entry is its
   own transaction, so one member of a group failing rolls back nothing that already posted.
3. **Record, and commit.** The outcomes are written in a second transaction.

Between (2) and (3) there is a real window: a host killed there has posted a document and not
yet said so. ``_recover`` closes it. Before dispatching anything it looks for durable evidence
that this exact occurrence already entered -- the target command's own idempotency receipt
under this key, then the company audit event stamped with this occurrence's source reference.
Neither lookup filters by actor, so the recovery works under a different login than the one
that crashed. Only when both are silent does anything get dispatched.

The schedule advances at claim time, not at success. That is deliberate: a slot that fails
keeps its own date and becomes a blocked occurrence a person can see and retry, while the next
slot still comes due on time. A failure records itself; it never disables the template and
never moves an accounting date.
"""

from __future__ import annotations

import json
from typing import Any

import sqlalchemy as sa

from bookflow.company import (memorized as service, memorized_references as edges,
                              memorized_schedule as schedule, schema as c)
from bookflow.core import clock
from bookflow.core.audit import write_event_to
from bookflow.core.errors import BookflowError
from bookflow.core.ids import new_id
from bookflow.core.registry import Applied, Plan, Touched

SOURCE_MARK = service.SOURCE_MARK


def _entry_key(occurrence_id: str) -> str:
    return 'memorized-' + occurrence_id


def _advance(row, taken):
    """The schedule after ``taken`` slots have been minted from it."""
    nxt, left = row['next_date'], row['remaining_count']
    for _ in taken:
        if left is not None:
            left = max(0, left - 1)
        nxt = schedule.advance(row['frequency'], nxt, row['anchor_day'])
    status = row['status']
    if schedule.finished(nxt, row['stop_date'], left):
        nxt, status = None, ('finished' if status == 'active' else status)
    return {'next_date': nxt, 'remaining_count': left, 'status': status}


# ---------------------------------------------------------------- planning

def prepare_entry(s, ctx, inp, verb):
    """Work out exactly which occurrences this run will touch, without writing anything."""
    from bookflow.company.memorized_models import MemorizedEntryOutput, OccurrenceWriteOutput
    as_of = service.today(s, getattr(inp, 'as_of', None))
    data: dict[str, Any] = {'verb': verb, 'as_of': as_of, 'warnings': []}
    if verb in ('retry', 'skip'):
        row = service._one(s, c.memorized_occurrences, c.memorized_occurrences.c.id == inp.occurrence)
        if row is None:
            raise BookflowError('E_RECORD_NOT_FOUND', details={'record_type': 'memorized_occurrence', 'selector': inp.occurrence})
        service._version_conflict(row, inp.expected_version, 'memorized_occurrence')
        if row['status'] not in ('pending', 'blocked'):
            raise BookflowError('E_VALIDATION', details={'fields': [{'field': 'occurrence', 'problem':
                f"is {row['status']}; only a pending or blocked occurrence can be retried or skipped"}]})
        data['occurrence'] = row
        template = service._one(s, c.memorized_transactions, c.memorized_transactions.c.id == row['template_id'])
        preview = service._occurrence_out(dict(row, version=row['version'] + 1,
                                               status='skipped' if verb == 'skip' else row['status']),
                                          template['name'])
        return Plan(OccurrenceWriteOutput(occurrence=preview, dry_run=s.dry_run), data)

    plan_rows: list[dict[str, Any]] = []
    if verb == 'enter':
        template = service._resolve_template(s, inp.memorized)
        if template['status'] == 'deleted':
            raise BookflowError('E_VALIDATION', details={'fields': [{'field': 'memorized', 'problem': 'has been deleted'}]})
        slot = inp.date or as_of
        plan_rows.append({'template': template, 'slot': slot, 'origin': 'manual', 'enter': True,
                          'advance': False, 'group_run': None, 'group_ordinal': None})
    elif verb == 'group-enter':
        group = service._resolve_group(s, inp.memorized_group)
        if group['status'] == 'deleted':
            raise BookflowError('E_VALIDATION', details={'fields': [{'field': 'memorized_group', 'problem': 'has been deleted'}]})
        members = service._members(s, group['id'])
        if not members:
            raise BookflowError('E_VALIDATION', details={'fields': [{'field': 'memorized_group', 'problem': 'has no members to enter'}]})
        slot = inp.date or as_of
        data['group'] = group
        for ordinal, member in enumerate(members, start=1):
            plan_rows.append({'template': member, 'slot': slot, 'origin': 'scheduled', 'enter': True,
                              'advance': False, 'group': group, 'group_run': group['id'],
                              'group_ordinal': member['group_ordinal'] or ordinal})
    else:  # process
        plan_rows = _due_work(s, inp, as_of)
    data['work'] = plan_rows
    preview = _entry_preview(s, plan_rows, as_of)
    return Plan(preview, data)


def _due_work(s, inp, as_of):
    """Every slot whose due date has arrived, oldest first: what is waiting, then what is new."""
    work: list[dict[str, Any]] = []
    limit = inp.limit
    only = service._resolve_template(s, inp.memorized)['id'] if getattr(inp, 'memorized', None) else None
    # An occurrence already waiting comes first. Its slot is already minted and its schedule
    # already advanced, so a run interrupted between posting and recording is picked up here
    # rather than being stranded behind a `next_date` that has moved past it.
    templates_by_id = {row['id']: row for row in service._rows(s, c.memorized_transactions)}
    waiting = sorted(service._rows(s, c.memorized_occurrences,
                                   c.memorized_occurrences.c.status == 'pending',
                                   c.memorized_occurrences.c.due_date <= as_of),
                     key=lambda row: (row['slot_date'], row['id']))
    for occurrence in waiting:
        template = templates_by_id.get(occurrence['template_id'])
        if template is None or (only is not None and template['id'] != only):
            continue
        mode = template['mode']
        if template['group_id']:
            group = service._one(s, c.memorized_groups, c.memorized_groups.c.id == template['group_id'])
            if group is not None and group['frequency'] != 'never':
                mode = group['mode']
        work.append({'template': template, 'slot': occurrence['slot_date'], 'occurrence': occurrence,
                     'origin': occurrence['origin'], 'enter': mode == 'enter_automatically',
                     'advance': False, 'group_run': occurrence['group_run_id'],
                     'group_ordinal': occurrence['group_ordinal']})
    # Naming one template asks for that template's own schedule. A group carries its members'
    # schedule as a unit, so running one member of it alone is not a thing this can mean.
    groups = [] if only is not None else [
        row for row in service._rows(s, c.memorized_groups,
                                     c.memorized_groups.c.status == 'active',
                                     c.memorized_groups.c.frequency != 'never')
        if row['mode'] in ('remind', 'enter_automatically')]
    for group in sorted(groups, key=lambda row: (row['next_date'] or '', row['id'])):
        members = service._members(s, group['id'])
        for slot in service._projected_slots(group, as_of, limit):
            for ordinal, member in enumerate(members, start=1):
                work.append({'template': member, 'slot': slot, 'origin': 'scheduled',
                             'enter': group['mode'] == 'enter_automatically', 'advance': True,
                             'group': group, 'group_run': group['id'],
                             'group_ordinal': member['group_ordinal'] or ordinal})
    templates = service._rows(s, c.memorized_transactions, c.memorized_transactions.c.status == 'active',
                              c.memorized_transactions.c.frequency != 'never')
    for template in sorted(templates, key=lambda row: (row['next_date'] or '', row['id'])):
        if only is not None and template['id'] != only:
            continue
        if template['mode'] == 'on_demand':
            continue
        for slot in service._projected_slots(template, as_of, limit):
            work.append({'template': template, 'slot': slot, 'origin': 'scheduled',
                         'enter': template['mode'] == 'enter_automatically', 'advance': True,
                         'group_run': None, 'group_ordinal': None})
    return work[:limit]


def _entry_preview(s, work, as_of):
    from bookflow.company.memorized_models import MemorizedEntryOutput
    entered, pending = [], []
    for item in work:
        template = item['template']
        waiting = item.get('occurrence') or {}
        row = {
            'id': waiting.get('id') or new_id(), 'version': waiting.get('version', 1),
            'template_id': template['id'],
            'revision_id': waiting.get('revision_id') or template['current_revision_id'],
            'command': template['command'], 'origin': item['origin'], 'slot_date': item['slot'],
            'due_date': waiting.get('due_date') or schedule.due_date(item['slot'], template['days_in_advance']),
            'entry_date': as_of if item['enter'] else None, 'status': 'entered' if item['enter'] else 'pending',
            'entry_key': waiting.get('entry_key', ''), 'transaction_id': None, 'document_number': None,
            'error_code': None, 'error_message': None,
            'attempt_count': waiting.get('attempt_count', 0) + (1 if item['enter'] else 0),
            'group_run_id': waiting.get('group_run_id'), 'group_ordinal': item['group_ordinal'],
        }
        (entered if item['enter'] else pending).append(service._occurrence_out(row, template['name']))
    return MemorizedEntryOutput(as_of=as_of, entered=entered, pending=pending, blocked=[],
                                entered_count=len(entered), pending_count=len(pending),
                                blocked_count=0, dry_run=s.dry_run)


# ---------------------------------------------------------------- applying

def apply_entry(plan, ctx, s):
    from bookflow.core.dispatch import _upsert_principals
    from bookflow.company.memorized_models import OccurrenceWriteOutput
    data = plan.data
    verb, as_of = data['verb'], data['as_of']
    name = data['command_name']
    with s.commits.operation('memorized.enter', s.hub, s.company):
        try:
            _upsert_principals(s, ctx)
            if verb == 'skip':
                row = _write_skip(s, ctx, data['occurrence'], name)
                template = service._one(s, c.memorized_transactions, c.memorized_transactions.c.id == row['template_id'])
                out = OccurrenceWriteOutput(occurrence=service._occurrence_out(row, template['name']))
                _store_key(s, ctx, name, data, out)
                s.commits.commit(s.company, 'memorized.enter')
                return Applied(out, [], f"skipped the {row['slot_date']} entry of {template['name']}",
                               finalized=True, audited=True)
            claimed = _claim(s, ctx, data, name)
            s.commits.commit(s.company, 'memorized.enter')
        except BaseException:
            if s.company.write_transaction:
                s.company.raw.rollback()
            raise

        results = [_enter_one(s, ctx, occurrence) for occurrence in claimed if occurrence['_enter']]

        s.company.raw.execute('BEGIN IMMEDIATE')
        try:
            rows, acted = _record(s, ctx, name, claimed, results)
            out = _entry_output(s, verb, as_of, rows, acted, data)
            _store_key(s, ctx, name, data, out)
            s.commits.commit(s.company, 'memorized.enter')
        except BaseException:
            if s.company.write_transaction:
                s.company.raw.rollback()
            raise
    summary = ('retried one memorized entry' if verb == 'retry' else
               f'entered {out.entered_count}, blocked {out.blocked_count}, pending {out.pending_count}')
    return Applied(out, [], summary, finalized=True, audited=True)


def _store_key(s, ctx, name, data, output=None):
    from bookflow.core import idempotency
    if ctx.idempotency_key and data.get('input_hash'):
        idempotency.store(s.company, s.actor.id, ctx.idempotency_key, name, data['input_hash'],
                          ctx.request_id, output.model_dump(mode='json') if output is not None else None)


def _write_skip(s, ctx, row, command_name):
    at, event = clock.now_iso(), new_id()
    after = dict(row, status='skipped', version=row['version'] + 1, updated_at=at,
                 updated_by=s.actor.id, updated_via=ctx.interface.value, error_code=None, error_message=None)
    write_event_to(s.company, ctx, command_name, f"skipped the {row['slot_date']} memorized entry",
                   [Touched('memorized_occurrence', row['id'], 'update', row['version'], after['version'], after, row, db='company')],
                   actor_id=s.actor.id, actor_kind=s.actor.kind, directive_code=s.directive_code, event_id=event)
    s.company.conn.execute(c.memorized_occurrences.update().where(c.memorized_occurrences.c.id == row['id'])
        .values(status='skipped', error_code=None, error_message=None, version=after['version'],
                updated_at=at, updated_by=s.actor.id, updated_via=ctx.interface.value))
    return after


def _claim(s, ctx, data, command_name):
    """Write every occurrence this run will touch and advance the schedules past those slots."""
    at, event = clock.now_iso(), new_id()
    touched, claimed, runs = [], [], {}
    if data['verb'] == 'retry':
        row = service._one(s, c.memorized_occurrences, c.memorized_occurrences.c.id == data['occurrence']['id'])
        if row['status'] not in ('pending', 'blocked'):
            raise BookflowError('E_VALIDATION', details={'fields': [{'field': 'occurrence', 'problem': f"is {row['status']}"}]})
        claimed.append(dict(row, _enter=True))
        write_event_to(s.company, ctx, command_name, f"retrying the {row['slot_date']} memorized entry",
                       [Touched('memorized_occurrence', row['id'], 'update', row['version'], row['version'], row, row, db='company')],
                       actor_id=s.actor.id, actor_kind=s.actor.kind, directive_code=s.directive_code, event_id=event)
        return claimed

    advanced: dict[str, list[str]] = {}
    group_advanced: dict[str, list[str]] = {}
    prepared = []
    for item in data['work']:
        template, slot = item['template'], item['slot']
        group = item.get('group') or (data.get('group') if item.get('group_run') else None)
        run_id = None
        if item.get('group_run') and item.get('occurrence') is None:
            run_id = _group_run(s, ctx, group, slot, at, event, runs, touched)
        existing = item.get('occurrence')
        if existing is None and item['origin'] == 'scheduled':
            existing = service._one(s, c.memorized_occurrences,
                                    c.memorized_occurrences.c.template_id == template['id'],
                                    c.memorized_occurrences.c.slot_date == slot,
                                    c.memorized_occurrences.c.origin == 'scheduled')
        if existing is not None:
            # The slot already exists. Entering again touches only what has not entered,
            # which is exactly what makes a group retry process the failures alone.
            if existing['status'] in ('pending', 'blocked') and item['enter']:
                claimed.append(dict(existing, _enter=True))
            else:
                claimed.append(dict(existing, _enter=False))
        else:
            row = dict(service._common(s, ctx, at), template_id=template['id'],
                       revision_id=template['current_revision_id'], command=template['command'],
                       origin=item['origin'], slot_date=slot,
                       due_date=schedule.due_date(slot, template['days_in_advance']), entry_date=None,
                       status='pending', entry_key='', transaction_id=None, document_number=None,
                       result=None, error_code=None, error_message=None, attempt_count=0,
                       group_run_id=run_id, group_ordinal=item['group_ordinal'] if run_id else None,
                       audit_event_id=event)
            row['entry_key'] = _entry_key(row['id'])
            prepared.append(row)
            touched.append(Touched('memorized_occurrence', row['id'], 'create', None, 1, row, db='company'))
            claimed.append(dict(row, _enter=item['enter']))
        if not item.get('advance'):
            continue
        if group is not None and group['frequency'] != 'never':
            slots = group_advanced.setdefault(group['id'], [])
            if slot not in slots:
                slots.append(slot)
        else:
            advanced.setdefault(template['id'], []).append(slot)

    updates = []
    for template_id, slots in advanced.items():
        row = service._one(s, c.memorized_transactions, c.memorized_transactions.c.id == template_id)
        moved = _advance(row, slots)
        updates.append((c.memorized_transactions, 'memorized_transaction', row, moved))
    for group_id, slots in group_advanced.items():
        row = service._one(s, c.memorized_groups, c.memorized_groups.c.id == group_id)
        moved = _advance(row, slots)
        updates.append((c.memorized_groups, 'memorized_group', row, moved))
    for table, record_type, row, moved in updates:
        after = dict(row, **moved)
        touched.append(Touched(record_type, row['id'], 'update', row['version'], row['version'], after, row, db='company'))

    write_event_to(s.company, ctx, command_name,
                   f"claimed {len(prepared)} memorized {'entry' if len(prepared) == 1 else 'entries'}",
                   touched, actor_id=s.actor.id, actor_kind=s.actor.kind,
                   directive_code=s.directive_code, event_id=event)
    for run in runs.values():
        if run['_new']:
            s.company.conn.execute(c.memorized_group_runs.insert().values(
                **{k: v for k, v in run.items() if not k.startswith('_')}))
            if run['_members']:
                s.company.conn.execute(c.memorized_group_run_members.insert(), run['_members'])
    for row in prepared:
        s.company.conn.execute(c.memorized_occurrences.insert().values(**row))
    for table, _record_type, row, moved in updates:
        s.company.conn.execute(table.update().where(table.c.id == row['id']).values(**moved))
    return claimed


def _group_run(s, ctx, group, slot, at, event, runs, touched):
    """Freeze the membership and order of one group slot, or reuse the frozen one."""
    key = (group['id'], slot)
    if key in runs:
        return runs[key]['id']
    existing = service._one(s, c.memorized_group_runs, c.memorized_group_runs.c.group_id == group['id'],
                            c.memorized_group_runs.c.slot_date == slot)
    if existing is not None:
        runs[key] = dict(existing, _new=False, _members=[])
        return existing['id']
    run = {'id': new_id(), 'group_id': group['id'], 'slot_date': slot,
           'entry_date': service.today(s), **service._created(s, ctx, at), 'audit_event_id': event}
    members = [{'run_id': run['id'], 'ordinal': member['group_ordinal'] or ordinal,
                'template_id': member['id'], 'revision_id': member['current_revision_id'], 'created_at': at}
               for ordinal, member in enumerate(service._members(s, group['id']), start=1)]
    runs[key] = dict(run, _new=True, _members=members)
    touched.append(Touched('memorized_group_run', run['id'], 'create', None, 1, run, db='company'))
    return run['id']


# ---------------------------------------------------------------- one entry

def _recover(s, occurrence):
    """Durable evidence that this occurrence already entered, found without an actor filter."""
    receipt = s.company.conn.execute(
        sa.select(c.idempotency_keys).where(c.idempotency_keys.c.key == occurrence['entry_key'],
                                            c.idempotency_keys.c.command == occurrence['command'],
                                            c.idempotency_keys.c.state == 'done')).mappings().first()
    if receipt is not None and receipt['output']:
        try:
            return {'output': json.loads(receipt['output']), 'source': 'idempotency receipt'}
        except ValueError:
            pass
    event = s.company.conn.execute(
        sa.select(c.audit_events.c.id).where(c.audit_events.c.source_ref == SOURCE_MARK + occurrence['id'],
                                             c.audit_events.c.command == occurrence['command'])
        .order_by(c.audit_events.c.seq).limit(1)).scalar_one_or_none()
    if event is None:
        return None
    entry = s.company.conn.execute(
        sa.select(c.audit_entries.c.record_id).where(c.audit_entries.c.event_id == event,
                                                     c.audit_entries.c.record_type == 'transaction')
        .limit(1)).scalar_one_or_none()
    return {'output': {'id': entry, 'recovered_from': 'audit'}, 'source': 'company audit trail'}


def _enter_one(s, ctx, occurrence):
    """Replay one occurrence through the ordinary command, or recover the entry it already made."""
    from bookflow.core import registry
    from bookflow.core.dispatch import run_in_session
    recovered = _recover(s, occurrence)
    if recovered is not None:
        return {'occurrence': occurrence, 'output': recovered['output'], 'recovered': recovered['source']}
    command = registry.get(occurrence['command'])
    revision = service._one(s, c.memorized_transaction_revisions,
                            c.memorized_transaction_revisions.c.id == occurrence['revision_id'])
    payload = dict(json.loads(revision['payload']), date=occurrence['slot_date'])
    entry_ctx = ctx.model_copy(update={
        'idempotency_key': occurrence['entry_key'],
        'source_ref': (SOURCE_MARK + occurrence['id'])[:512],
        'reason': (ctx.reason or f"Memorized entry for {occurrence['slot_date']}")[:140],
    })
    saved = (s.company_touched, s.hub_touched, list(s.warnings), s.dry_run)
    try:
        inp = command.input_model.model_validate(payload)
        output = run_in_session(command, inp, entry_ctx, s)
        return {'occurrence': occurrence, 'output': output, 'recovered': None}
    except BookflowError as error:
        return {'occurrence': occurrence, 'error': error}
    except Exception as error:  # a defect is still a visible blocked occurrence, never a silence
        return {'occurrence': occurrence,
                'error': BookflowError('E_INTERNAL', message=str(error)[:500] or type(error).__name__)}
    finally:
        s.company_touched, s.hub_touched, s.warnings, s.dry_run = saved[0], saved[1], saved[2], saved[3]
        if s.company is not None and s.company.write_transaction:
            s.company.raw.rollback()


def _record(s, ctx, command_name, claimed, results):
    """Write every outcome of this run in one transaction, with one audit event."""
    at, event = clock.now_iso(), new_id()
    by_id = {result['occurrence']['id']: result for result in results}
    touched, rows, updates, acted = [], [], [], set()
    for occurrence in claimed:
        result = by_id.get(occurrence['id'])
        before = {k: v for k, v in occurrence.items() if not k.startswith('_')}
        if result is None:
            rows.append(before)
            continue
        error = result.get('error')
        acted.add(before['id'])
        if error is None:
            output = result['output'] if isinstance(result['output'], dict) else {}
            values = {'status': 'entered', 'entry_date': service.today(s),
                      'transaction_id': _identifier(output), 'document_number': _number(output),
                      'result': edges.canonical(output), 'error_code': None, 'error_message': None}
        else:
            values = {'status': 'blocked', 'error_code': error.code,
                      'error_message': (error.message or error.code)[:512],
                      'result': None, 'entry_date': None, 'transaction_id': None, 'document_number': None}
        values['attempt_count'] = before['attempt_count'] + 1
        values['version'] = before['version'] + 1
        values['updated_at'], values['updated_by'], values['updated_via'] = at, s.actor.id, ctx.interface.value
        after = dict(before, **values)
        touched.append(Touched('memorized_occurrence', before['id'], 'update', before['version'],
                               after['version'], after, before, db='company'))
        updates.append((before['id'], values))
        rows.append(after)
    if touched:
        entered = sum(1 for _, values in updates if values['status'] == 'entered')
        write_event_to(s.company, ctx, command_name,
                       f"entered {entered} of {len(updates)} memorized entries", touched,
                       actor_id=s.actor.id, actor_kind=s.actor.kind, directive_code=s.directive_code,
                       event_id=event)
    for record_id, values in updates:
        s.company.conn.execute(c.memorized_occurrences.update()
            .where(c.memorized_occurrences.c.id == record_id).values(**values))
    return rows, acted


def _identifier(output):
    value = output.get('id') or output.get('transaction_id')
    return value if isinstance(value, str) and len(value) == 26 else None


def _number(output):
    for key in ('number', 'document_number'):
        if isinstance(output.get(key), str):
            return output[key][:64]
    revision = output.get('revision')
    if isinstance(revision, dict) and isinstance(revision.get('number'), str):
        return revision['number'][:64]
    return None


def _entry_output(s, verb, as_of, rows, acted, data):
    """What this run did, never what the template holds: a rerun that entered nothing says so."""
    from bookflow.company.memorized_models import MemorizedEntryOutput, OccurrenceWriteOutput
    names = {row['id']: row['name'] for row in service._rows(s, c.memorized_transactions)}
    projected = [service._occurrence_out(row, names.get(row['template_id'])) for row in rows]
    if verb == 'retry':
        return OccurrenceWriteOutput(occurrence=projected[0])
    entered = [row for row in projected if row['status'] == 'entered' and row['id'] in acted]
    blocked = [row for row in projected if row['status'] == 'blocked']
    pending = [row for row in projected if row['status'] == 'pending']
    return MemorizedEntryOutput(as_of=as_of, entered=entered, blocked=blocked, pending=pending,
                                entered_count=len(entered), blocked_count=len(blocked),
                                pending_count=len(pending), warnings=list(data['warnings']))
