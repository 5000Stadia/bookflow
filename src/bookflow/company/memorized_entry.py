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
        _refuse_held_for_agents(s, [row['id']], verb)
        template = service._one(s, c.memorized_transactions, c.memorized_transactions.c.id == row['template_id'])
        replays = row['revision_id'] if verb == 'skip' or _recover(s, row) is not None else template['current_revision_id']
        if verb == 'retry' and replays != row['revision_id']:
            _require_preview(s, inp, [row])
        preview = _with_author(s, service._occurrence_out(dict(row, version=row['version'] + 1, revision_id=replays,
                                                               status='skipped' if verb == 'skip' else row['status']),
                                                          template['name']))
        fingerprint = None
        if verb == 'retry':
            fingerprint = _fingerprint({row['template_id']: template['current_revision_id']})
            _check_fingerprint(inp, fingerprint)
            data['revisions'] = {row['template_id']: template['current_revision_id']}
        return Plan(OccurrenceWriteOutput(occurrence=preview, facts_fingerprint=fingerprint, dry_run=s.dry_run), data)

    plan_rows: list[dict[str, Any]] = []
    if verb == 'enter':
        template = service._resolve_template(s, inp.memorized)
        if template['status'] == 'deleted':
            raise BookflowError('E_VALIDATION', details={'fields': [{'field': 'memorized', 'problem': 'has been deleted'}]})
        slot = inp.date or as_of
        if s.actor.kind != 'human':
            # While a slot of this template waits for a person, an agent enters it on no date at all:
            # another date would post the same content and leave the held slot listed.
            held = held_reasons(s)
            waiting_held = [o['id'] for o in _rows(s, c.memorized_occurrences, c.memorized_occurrences.c.template_id ==
                                                    template['id'], c.memorized_occurrences.c.status == 'pending')
                            if o['id'] in held]
            _refuse_held_for_agents(s, waiting_held, verb)
        waiting = _existing(s, template['id'], slot)
        data['slot_state'] = {f"{template['id']}:{slot}": _slot_state(waiting)}
        if waiting is not None and waiting['status'] == 'entered' and not inp.again:
            # The slot for this date already posted: a second entry is a deliberate act, never a slip.
            ref = waiting['document_number'] or waiting['transaction_id'] or waiting['id']
            said = f"The {slot} slot of {template['name']} was already entered as {ref}."
            if s.dry_run or inp.expected_facts_fingerprint is not None:
                data['warnings'].append(said + ' Entering it posts a second entry.')
            else:
                raise BookflowError('E_VALIDATION', message=said + ' Pass again: true to enter it a second time.',
                                    details={'fields': [{'field': 'again', 'problem': f'the {slot} slot was already entered as {ref}'}],
                                             'occurrence': waiting['id']})
        if waiting is not None and waiting['status'] in ('pending', 'blocked'):
            # A scheduled slot already waits for this date: entering it by hand takes that slot, so one
            # slot can never post twice (once by hand, once from the waiting occurrence).
            _refuse_held_for_agents(s, [waiting['id']], verb)
            plan_rows.append({'template': template, 'slot': slot, 'origin': 'scheduled', 'enter': True,
                              'advance': False, 'occurrence': waiting, 'group_run': waiting['group_run_id'],
                              'group_ordinal': waiting['group_ordinal']})
        else:
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
        held = held_reasons(s)
        agent = s.actor.kind != 'human'
        plan_rows = _due_work(s, inp, as_of,
                              keep_waiting=(lambda occurrence, mode: occurrence['id'] not in held) if agent else None)
        if agent and held:
            data['warnings'].append(f'{len(held)} waiting entr{"y is" if len(held) == 1 else "ies are"} held for a person '
                                    'and left out: only a person may enter or skip them.')
    if verb == 'group-enter':
        existing = [_existing(s, item['template']['id'], item['slot']) for item in plan_rows]
        if s.actor.kind != 'human':
            held = held_reasons(s)
            members = {item['template']['id'] for item in plan_rows}
            _refuse_held_for_agents(s, [o['id'] for o in _rows(s, c.memorized_occurrences,
                                                               c.memorized_occurrences.c.status == 'pending')
                                        if o['template_id'] in members and o['id'] in held], verb)
        for item, row in zip(plan_rows, existing):
            if row is not None and row['status'] in ('pending', 'blocked'):
                item['occurrence_seen'] = row
    waiting = [item.get('occurrence') or item.get('occurrence_seen') for item in plan_rows if item['enter']]
    _require_preview(s, inp, [row for row in waiting if row is not None
                              and row['revision_id'] != _current_revision(s, row['template_id'])
                              and _recover(s, row) is None])
    data['work'] = plan_rows
    held = held_reasons(s)
    releasing = [(item['template']['name'], item['slot'], held[row['id']]) for item in plan_rows
                 for row in [item.get('occurrence') or item.get('occurrence_seen')]
                 if item['enter'] and row is not None and row['id'] in held]
    if releasing:
        data['warnings'].append(
            f"{len(releasing)} entr{'y was' if len(releasing) == 1 else 'ies were'} held for a person and "
            f"{'is' if len(releasing) == 1 else 'are'} entered by you: "
            + '; '.join(f'{name} for {slot} ({reason})' for name, slot, reason in releasing))
    revisions = {item['template']['id']: item['template']['current_revision_id'] for item in plan_rows if item['enter']}
    data['revisions'] = revisions
    preview = _entry_preview(s, plan_rows, as_of)
    preview.facts_fingerprint = _fingerprint(dict(revisions, **data.get('slot_state', {})))
    hint = None
    if verb == 'enter' and plan_rows and plan_rows[0]['origin'] == 'manual':
        taken = _existing(s, plan_rows[0]['template']['id'], plan_rows[0]['slot'])
        if taken is not None and taken['status'] == 'entered':
            hint = (f"The {taken['slot_date']} slot of {plan_rows[0]['template']['name']} has been entered since the "
                    "preview (the scheduler or someone else entered it); entering it again would post it twice.")
    _check_fingerprint(inp, preview.facts_fingerprint, hint)
    preview.warnings = list(data['warnings'])
    return Plan(preview, data)


def held_reasons(s) -> dict[str, str]:
    """Waiting occurrences the scheduler left for a person, and why."""
    return {row[0]: row[1] for row in s.company.conn.execute(
        sa.select(c.scheduled_holds.c.occurrence_id, c.scheduled_holds.c.reason))}


def _refuse_held_for_agents(s, occurrence_ids, verb):
    """A slot held for a person is entered or skipped by a person: never by an agent, on any path."""
    if s.actor.kind == 'human' or not occurrence_ids:
        return
    held = held_reasons(s)
    for occurrence_id in occurrence_ids:
        if occurrence_id in held:
            raise BookflowError('E_PERMISSION', message=(
                f"This memorized entry waits for a person ({held[occurrence_id]}). Only a person may "
                f"{'skip' if verb == 'skip' else 'enter'} it."),
                details={'reason': 'held_for_a_person', 'occurrence': occurrence_id})


def _existing(s, template_id, slot):
    return service._one(s, c.memorized_occurrences, c.memorized_occurrences.c.template_id == template_id,
                        c.memorized_occurrences.c.slot_date == slot, c.memorized_occurrences.c.origin == 'scheduled')


def _current_revision(s, template_id):
    return s.company.conn.execute(sa.select(c.memorized_transactions.c.current_revision_id).where(
        c.memorized_transactions.c.id == template_id)).scalar_one_or_none()


def _require_preview(s, inp, superseded):
    """A slot that would post a newer revision than it was listed with is entered only from a preview of it."""
    if not superseded or s.dry_run or getattr(inp, 'expected_facts_fingerprint', None) is not None:
        return
    raise BookflowError('E_VALIDATION', message=(
        'The memorized transaction changed after this slot was listed, so it would post the newer version. '
        'Preview it first (dry run) and send back its facts_fingerprint as expected_facts_fingerprint.'),
        details={'fields': [{'field': 'expected_facts_fingerprint',
                             'problem': 'required: a template changed after its slot was listed'}],
                 'occurrences': [row['id'] for row in superseded]})


def _fingerprint(revisions):
    """Which revision of each template a run will post: what a preview promised and an apply must keep."""
    import hashlib
    return hashlib.sha256(json.dumps(sorted(revisions.items())).encode()).hexdigest()


def _check_fingerprint(inp, current, hint=None):
    expected = getattr(inp, 'expected_facts_fingerprint', None)
    if expected is not None and expected != current:
        raise BookflowError('E_PREVIEW_STALE', message=hint, details={
            'reason': 'memorized_slot' if hint else 'memorized_revision', 'current_facts_fingerprint': current})


def _rows(s, table, *where):
    return service._rows(s, table, *where)


def _slot_state(row):
    """What a scheduled slot looked like: nothing, or its status and version (part of an enter's fingerprint)."""
    return 'none' if row is None else f"{row['id']}:{row['status']}:{row['version']}"


def _slot_unchanged(s, data):
    """In the writing transaction: a slot the plan saw has not been entered or taken since (by the scheduler)."""
    for key, seen in (data.get('slot_state') or {}).items():
        template_id, slot = key.split(':', 1)
        now = _existing(s, template_id, slot)
        if _slot_state(now) != seen:
            name = s.company.conn.execute(sa.select(c.memorized_transactions.c.name).where(
                c.memorized_transactions.c.id == template_id)).scalar_one_or_none()
            raise BookflowError('E_PREVIEW_STALE', message=(
                f"The {slot} slot of {name} changed since the preview: "
                f"{'the scheduler entered it' if now is not None and now['status'] == 'entered' else 'it was listed or taken'}"
                ". Look again before entering it."), details={'reason': 'memorized_slot', 'current_facts_fingerprint': None})


def _still_current(s, revisions):
    """At apply: a template edited since the plan was made would post something nobody previewed."""
    for template_id, revision_id in (revisions or {}).items():
        now = service._one(s, c.memorized_transactions, c.memorized_transactions.c.id == template_id)
        if now is None or now['current_revision_id'] != revision_id:
            raise BookflowError('E_PREVIEW_STALE', details={'reason': 'memorized_revision',
                                                            'current_facts_fingerprint': None})


def _with_author(s, out):
    """Name who saved the revision an entry posts, so a preview says whose content it is."""
    author = s.company.conn.execute(
        sa.select(c.memorized_transaction_revisions.c.created_by, c.principals.c.display_name)
        .select_from(c.memorized_transaction_revisions)
        .outerjoin(c.principals, c.principals.c.user_id == c.memorized_transaction_revisions.c.created_by)
        .where(c.memorized_transaction_revisions.c.id == out['revision_id'])).first()
    if author is not None:
        out['replays_author_id'], out['replays_author'] = author[0], author[1]
    return out


def _due_work(s, inp, as_of, keep_waiting=None):
    """Every slot whose due date has arrived, oldest first: what is waiting, then what is new.

    ``keep_waiting(occurrence, mode)`` narrows the waiting occurrences, so a caller that will not
    act on some of them (the scheduler, on reminder slots) never lets them fill the limit. The
    limit never splits one group slot: its members are taken together or not at all, because the
    claim advances the group past every slot it takes.
    """
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
        if template is None or template['status'] == 'deleted' or (only is not None and template['id'] != only):
            continue
        mode = template['mode']
        if template['group_id']:
            group = service._one(s, c.memorized_groups, c.memorized_groups.c.id == template['group_id'])
            if group is not None and group['frequency'] != 'never':
                mode = group['mode']
        if keep_waiting is not None and not keep_waiting(occurrence, mode):
            continue
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
    taken, units = [], {}
    for item in work:
        key = (item['group_run'], item['slot']) if item.get('group_run') and item.get('advance') else id(item)
        units.setdefault(key, []).append(item)
    for unit in units.values():
        if taken and len(taken) + len(unit) > limit:
            break
        taken.extend(unit)
    return taken


def _entry_preview(s, work, as_of):
    from bookflow.company.memorized_models import MemorizedEntryOutput
    entered, pending, held = [], [], held_reasons(s)
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
        if item['enter'] and not (waiting and _recover(s, waiting) is not None):
            row['revision_id'] = template['current_revision_id']   # what will post: the template as it is now
        out = _with_author(s, service._occurrence_out(row, template['name']))
        out['waiting_reason'] = held.get(row['id'])
        (entered if item['enter'] else pending).append(out)
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
            _still_current(s, data.get('revisions'))
            _slot_unchanged(s, data)
            claimed = _claim(s, ctx, data, name)
            rebase(s, ctx, claimed, name)
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
    release_holds(s, ctx, command_name, [row['id']], 'skipped')
    return after


def release_holds(s, ctx, command_name, occurrence_ids, how):
    """A person entered or skipped slots the scheduler held for one: the holds go, on the record, naming them."""
    holds = [dict(r) for r in s.company.conn.execute(sa.select(c.scheduled_holds).where(
        c.scheduled_holds.c.occurrence_id.in_(list(occurrence_ids)))).mappings()] if occurrence_ids else []
    if not holds:
        return
    write_event_to(s.company, ctx, command_name,
                   f"{s.actor.display_name or s.actor.username} {how} {len(holds)} entr{'y' if len(holds) == 1 else 'ies'} "
                   'held for a person', [Touched('scheduled_hold', h['occurrence_id'], 'delete', 1, None, None, h, db='company')
                                         for h in holds],
                   actor_id=s.actor.id, actor_kind=s.actor.kind, directive_code=s.directive_code, event_id=new_id())
    s.company.conn.execute(c.scheduled_holds.delete().where(
        c.scheduled_holds.c.occurrence_id.in_([h['occurrence_id'] for h in holds])))


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


def rebase(s, ctx, claimed, command_name):
    """Point every slot about to be entered at its template as it is now.

    A slot that has never posted enters the memorized transaction as it stands -- the anchor's
    behaviour -- whoever enters it and however (the scheduler, `memorized process`, `memorized
    retry`), so the content and the authority of an entry always come from the same, current
    revision and a superseded payload can never be posted in a later saver's name. A slot that
    did post before its outcome was recorded (a host killed in between) keeps the revision that
    produced its document: recovery is consulted first, and such a slot is never re-pointed.
    The frozen membership of a group run is history of what the run captured, and is left as it is.
    """
    from bookflow.core.registry import Touched
    touched, at = [], clock.now_iso()
    for occurrence in claimed:
        if not occurrence.get('_enter') or occurrence['status'] not in ('pending', 'blocked'):
            continue
        template = service._one(s, c.memorized_transactions, c.memorized_transactions.c.id == occurrence['template_id'])
        if template is None or template['current_revision_id'] == occurrence['revision_id']:
            continue
        if _recover(s, occurrence) is not None:
            continue
        values = dict(revision_id=template['current_revision_id'], command=template['command'],
                      version=occurrence['version'] + 1, updated_at=at, updated_by=s.actor.id,
                      updated_via=ctx.interface.value)
        before = {k: v for k, v in occurrence.items() if not k.startswith('_')}
        touched.append(Touched('memorized_occurrence', occurrence['id'], 'update', occurrence['version'],
                               values['version'], dict(before, **values), before, db='company'))
        s.company.conn.execute(c.memorized_occurrences.update().where(c.memorized_occurrences.c.id == occurrence['id'])
                               .values(**values))
        occurrence.update(values)
    if touched:
        write_event_to(s.company, ctx, command_name,
                       f'{len(touched)} entr{"y" if len(touched) == 1 else "ies"} will replay the memorized transaction as it is now',
                       touched, actor_id=s.actor.id, actor_kind=s.actor.kind, directive_code=s.directive_code,
                       event_id=new_id())
    return touched


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


def _enter_one(s, ctx, occurrence, reason=None):
    """Replay one occurrence through the ordinary command, or recover the entry it already made."""
    from bookflow.core import registry
    from bookflow.core.dispatch import run_in_session
    recovered = _recover(s, occurrence)
    if recovered is not None:
        return {'occurrence': occurrence, 'output': recovered['output'], 'recovered': recovered['source']}
    command = registry.get(occurrence['command'])
    if not edges.enterable(command):
        # What may be memorized can change between the save and the slot (a command made a
        # person's act since): checked again at the moment of entry, never assumed.
        return {'occurrence': occurrence, 'error': BookflowError('E_VALIDATION', message=(
            f"`{occurrence['command']}` can no longer be entered from a memorized transaction."),
            details={'fields': [{'field': 'command', 'problem': 'not a memorizable create or post command'}]})}
    revision = service._one(s, c.memorized_transaction_revisions,
                            c.memorized_transaction_revisions.c.id == occurrence['revision_id'])
    payload = dict(json.loads(revision['payload']), date=occurrence['slot_date'])
    reason = reason or ctx.reason or f"Memorized entry for {occurrence['slot_date']}"
    entry_ctx = ctx.model_copy(update={
        'idempotency_key': occurrence['entry_key'],
        'source_ref': (SOURCE_MARK + occurrence['id'])[:512],
        'reason': reason[:140],
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
    release_holds(s, ctx, command_name, [record_id for record_id, values in updates if values['status'] == 'entered'],
                  'entered')
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
    projected = [_with_author(s, service._occurrence_out(row, names.get(row['template_id']))) for row in rows]
    if verb == 'retry':
        return OccurrenceWriteOutput(occurrence=projected[0])
    entered = [row for row in projected if row['status'] == 'entered' and row['id'] in acted]
    blocked = [row for row in projected if row['status'] == 'blocked']
    pending = [row for row in projected if row['status'] == 'pending']
    return MemorizedEntryOutput(as_of=as_of, entered=entered, blocked=blocked, pending=pending,
                                entered_count=len(entered), blocked_count=len(blocked),
                                pending_count=len(pending), warnings=list(data['warnings']))
