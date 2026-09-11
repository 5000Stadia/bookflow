"""Memorizing a transaction, and entering the ones that are due.

**What a template is.** One registered create-or-post command, the canonical input it takes,
and a schedule. Entry does not reconstruct a document: it replays that input through the same
command every other surface calls, so the books cannot learn a second way to post. Everything
the payload does not say stays unsaid, and the command recomputes it at entry -- terms, due
dates, prices, tax and the document number all follow today's records rather than the day the
template was written. The accounting date is the only field entry always supplies, because
the occurrence's slot *is* the accounting date.

**What never goes into the payload.** The original document number, an expected version, an
operation key: each is an identity of the entry that was made once, and replaying one would
either duplicate an identity or pin a template to a version that has moved on. They are
stripped at capture, with a warning, rather than silently kept.

**Idempotence per slot.** An occurrence is created *before* anything is dispatched, carrying
``entry_key`` -- its command idempotency identity, reused by every retry. Three layers stand
between a repeated run and a repeated document, and they answer in this order:

1. the occurrence's own status, which is ``entered`` once it is;
2. the durable receipt: the idempotency row the target command wrote under this exact key, or
   failing that the company audit event stamped with this occurrence's ``source_ref``. Both
   are read **without an actor filter**, so a host restarted under a different login recovers
   the same way the original one would;
3. the target command's own idempotency lookup, which is what the receipt in (2) came from.

A crash between the post and the completion record therefore leaves a *pending* occurrence
whose document already exists, and the next run adopts that document rather than posting a
second one. That is the one window the design allows, and layer (2) is what closes it.

**Failure is a record, not a silence.** A failed entry becomes a blocked occurrence carrying
its code and message, keeping its own slot date, retryable and skippable. The template keeps
running: the schedule has already advanced past that slot, so one bad month does not stop the
next one, and no other template is affected at all.
"""

from __future__ import annotations

import json
import unicodedata
from typing import Any

import sqlalchemy as sa

from bookflow.company import memorized_references as edges, memorized_schedule as schedule, schema as c
from bookflow.core import clock
from bookflow.core.errors import BookflowError
from bookflow.core.ids import new_id
from bookflow.core.registry import Applied, Plan, Touched

# The context reference every memorized entry is stamped with. It puts the occurrence in the
# company audit trail, which is what makes a crashed entry recoverable without a receipt.
SOURCE_MARK = 'memorized-occurrence:'

# Identities of one particular entry. Replaying any of them is either a duplicate identity or
# a pin to a version that has moved on, so capture drops them and says it did.
NEVER_STORED = ('number', 'expected_version', 'operation_key')


# ---------------------------------------------------------------- small shared helpers

def _name_key(value: str, field: str = 'name') -> tuple[str, str]:
    display = unicodedata.normalize('NFC', str(value).strip())
    if not display:
        raise BookflowError('E_VALIDATION', details={'fields': [{'field': field, 'problem': 'must not be empty'}]})
    return display, unicodedata.normalize('NFC', display.casefold())


def _rows(s, table, *where):
    query = sa.select(table)
    for clause in where:
        query = query.where(clause)
    return [dict(row) for row in s.company.conn.execute(query).mappings()]


def _one(s, table, *where):
    found = _rows(s, table, *where)
    return found[0] if found else None


def _version_conflict(row, expected, record_type):
    if row['version'] != expected:
        raise BookflowError('E_VERSION_CONFLICT', details={
            'record_type': record_type, 'record_id': row['id'], 'expected_version': expected,
            'current_version': row['version'], 'updated_by': row['updated_by'],
            'updated_via': row['updated_via']})


def _resolve_template(s, selector, *, include_deleted=False):
    where = [sa.or_(c.memorized_transactions.c.id == selector,
                    c.memorized_transactions.c.name_key == _name_key(selector, 'memorized')[1])]
    if not include_deleted:
        where.append(c.memorized_transactions.c.status != 'deleted')
    row = _one(s, c.memorized_transactions, *where)
    if row is None:
        raise BookflowError('E_RECORD_NOT_FOUND', details={'record_type': 'memorized_transaction', 'selector': selector})
    return row


def _resolve_group(s, selector, *, include_deleted=False):
    where = [sa.or_(c.memorized_groups.c.id == selector,
                    c.memorized_groups.c.name_key == _name_key(selector, 'memorized_group')[1])]
    if not include_deleted:
        where.append(c.memorized_groups.c.status != 'deleted')
    row = _one(s, c.memorized_groups, *where)
    if row is None:
        raise BookflowError('E_RECORD_NOT_FOUND', details={'record_type': 'memorized_group', 'selector': selector})
    return row


def today(s, as_of=None):
    return as_of or schedule.today(s.company_tz)


def _schedule_out(row):
    return {key: row[key] for key in ('frequency', 'anchor_day', 'start_date', 'next_date',
                                      'stop_date', 'remaining_count', 'days_in_advance', 'mode', 'status')}


def _occurrence_out(row, template_name=None):
    out = {key: row[key] for key in (
        'id', 'version', 'template_id', 'revision_id', 'command', 'origin', 'slot_date', 'due_date',
        'entry_date', 'status', 'entry_key', 'transaction_id', 'document_number', 'error_code',
        'error_message', 'attempt_count', 'group_run_id', 'group_ordinal')}
    out['template_name'] = template_name
    return out


def _created(s, ctx, at):
    return {'created_at': at, 'created_by': s.actor.id, 'created_via': ctx.interface.value}


def _audit(s, ctx, name, summary, touched, event):
    """Write the event before the rows that name it: every row here carries its event id."""
    from bookflow.core.audit import write_event_to
    write_event_to(s.company, ctx, name, summary, touched, actor_id=s.actor.id,
                   actor_kind=s.actor.kind, directive_code=s.directive_code, event_id=event)


def _common(s, ctx, at, record_id=None):
    return {'id': record_id or new_id(), 'version': 1, **_created(s, ctx, at),
            'updated_at': at, 'updated_by': s.actor.id, 'updated_via': ctx.interface.value}


# ---------------------------------------------------------------- capture

def _capture(s, command_name, payload, *, warnings):
    """Validate a payload against the command that will replay it, and derive its index."""
    command = edges.resolve_command(command_name)
    if not isinstance(payload, dict):
        raise BookflowError('E_VALIDATION', details={'fields': [{'field': 'payload', 'problem': 'must be a JSON object'}]})
    payload = json.loads(json.dumps(payload))  # a private copy; capture rewrites references in place
    for field in NEVER_STORED:
        if field in payload:
            payload.pop(field)
            warnings.append(f'payload.{field} is an identity of one entry and is never memorized; it was dropped.')
    payload, index, reference_schema, resolution_warnings = edges.capture(s, command, payload)
    warnings.extend(resolution_warnings)
    # Fail now rather than at the first slot: a payload the command cannot accept is a broken
    # template, and finding that out a month later is the failure this catches.
    from bookflow.core.dispatch import validate_input
    validate_input(command, {**payload, 'date': payload.get('date') or schedule.today(s.company_tz)})
    return command, payload, index, reference_schema


def _schedule_fields(inp, previous=None, *, warnings):
    """Build the stored schedule from the input, keeping whatever the input did not name."""
    previous = previous or {}
    given = inp.model_fields_set
    frequency = inp.frequency if 'frequency' in given and inp.frequency else previous.get('frequency', 'never')
    start = inp.start_date if 'start_date' in given else previous.get('start_date')
    stop = inp.stop_date if 'stop_date' in given else previous.get('stop_date')
    count = inp.remaining_count if 'remaining_count' in given else previous.get('remaining_count')
    advance = inp.days_in_advance if 'days_in_advance' in given and inp.days_in_advance is not None else previous.get('days_in_advance', 0)
    mode = inp.mode if 'mode' in given and inp.mode else previous.get('mode', 'on_demand')
    if frequency == 'never':
        return {'frequency': 'never', 'anchor_day': None, 'start_date': start, 'next_date': None,
                'stop_date': stop, 'remaining_count': count, 'days_in_advance': advance, 'mode': mode}
    if not start:
        raise BookflowError('E_VALIDATION', details={'fields': [
            {'field': 'start_date', 'problem': 'a schedule needs a first slot date'}]})
    if stop and stop < start:
        raise BookflowError('E_VALIDATION', details={'fields': [
            {'field': 'stop_date', 'problem': 'is before the first slot date'}]})
    anchor = schedule.anchor_day_for(frequency, start)
    # A schedule that is edited keeps the slot it is standing on. Only a new start date or a
    # new frequency re-anchors it -- otherwise renaming a finished template would put its whole
    # history back on the board.
    reanchored = not previous or 'start_date' in given or previous.get('frequency') != frequency
    next_date = start if reanchored else previous.get('next_date')
    if next_date and stop and next_date > stop:
        warnings.append('The stop date is already past the next slot; this schedule is finished.')
    fields = {'frequency': frequency, 'anchor_day': anchor, 'start_date': start, 'next_date': next_date,
              'stop_date': stop, 'remaining_count': count, 'days_in_advance': advance, 'mode': mode}
    if reanchored and next_date and previous and previous.get('status') == 'finished':
        # Giving a finished template a new start is asking for it to run again.
        fields['status'] = 'active'
    return fields


# ---------------------------------------------------------------- template writes

def prepare(s, ctx, inp, verb):
    """Plan for the create/update/delete verbs of both nouns."""
    warnings: list[str] = []
    data: dict[str, Any] = {'verb': verb, 'warnings': warnings}
    if verb == 'create':
        display, key = _name_key(inp.name)
        if _one(s, c.memorized_transactions, c.memorized_transactions.c.name_key == key,
                c.memorized_transactions.c.status != 'deleted'):
            raise BookflowError('E_NAME_TAKEN', details={'name': display})
        command, payload, index, reference_schema = _capture(s, inp.command, inp.payload, warnings=warnings)
        fields = _schedule_fields(inp, warnings=warnings)
        group = _resolve_group(s, inp.group) if inp.group else None
        _check_group_pairing(group, fields, inp.group_ordinal, bool(inp.group))
        data.update(command=command.name, payload=payload, index=index, name=display, name_key=key,
                    reference_schema=reference_schema, fields=fields, group=group,
                    group_ordinal=inp.group_ordinal)
        preview = _template_write_preview(s, ctx, None, display, command.name, fields, index,
                                          reference_schema, group, inp.group_ordinal, warnings)
    elif verb == 'update':
        row = _resolve_template(s, inp.memorized)
        _version_conflict(row, inp.expected_version, 'memorized_transaction')
        revision = _one(s, c.memorized_transaction_revisions, c.memorized_transaction_revisions.c.id == row['current_revision_id'])
        display, key = _name_key(inp.name) if inp.name else (row['name'], row['name_key'])
        if key != row['name_key'] and _one(s, c.memorized_transactions, c.memorized_transactions.c.name_key == key,
                                           c.memorized_transactions.c.status != 'deleted'):
            raise BookflowError('E_NAME_TAKEN', details={'name': display})
        if inp.payload is not None:
            command, payload, index, reference_schema = _capture(s, row['command'], inp.payload, warnings=warnings)
        else:
            command = edges.resolve_command(row['command'])
            payload = json.loads(revision['payload'])
            index = [dict(row) for row in _rows(s, c.memorized_references,
                                                c.memorized_references.c.revision_id == revision['id'])]
            reference_schema = revision['reference_schema']
        fields = _schedule_fields(inp, row, warnings=warnings)
        if inp.status:
            fields['status'] = inp.status
        if 'group' in inp.model_fields_set:
            # `--clear group` names the field with no value, which is how a template leaves
            # its group; naming no field at all leaves it where it is.
            group = _resolve_group(s, inp.group) if inp.group else None
            ordinal = inp.group_ordinal if group is not None else None
            if group is not None and ordinal is None and group['id'] == row['group_id']:
                ordinal = row['group_ordinal']
        else:
            group = _one(s, c.memorized_groups, c.memorized_groups.c.id == row['group_id']) if row['group_id'] else None
            ordinal = inp.group_ordinal if inp.group_ordinal is not None else row['group_ordinal']
        _check_group_pairing(group, fields, ordinal, group is not None)
        data.update(row=row, command=command.name, payload=payload, index=index, name=display, name_key=key,
                    reference_schema=reference_schema, fields=fields, group=group, group_ordinal=ordinal)
        preview = _template_write_preview(s, ctx, row, display, command.name, fields, index,
                                          reference_schema, group, ordinal, warnings)
    else:  # delete
        row = _resolve_template(s, inp.memorized)
        _version_conflict(row, inp.expected_version, 'memorized_transaction')
        data.update(row=row)
        revision = _one(s, c.memorized_transaction_revisions, c.memorized_transaction_revisions.c.id == row['current_revision_id'])
        fields = dict(_schedule_out(row), status='deleted', next_date=None)
        preview = _template_write_preview(s, ctx, row, row['name'], row['command'], fields, [],
                                          revision['reference_schema'], None, None, warnings, deleted=True)
    return Plan(preview, data)


def _check_group_pairing(group, fields, ordinal, wanted):
    if wanted and group is None:
        raise BookflowError('E_VALIDATION', details={'fields': [{'field': 'group', 'problem': 'no such group'}]})
    if group is not None and ordinal is None:
        raise BookflowError('E_VALIDATION', details={'fields': [
            {'field': 'group_ordinal', 'problem': 'a member of a group needs its place in the order'}]})
    if group is not None and group['frequency'] != 'never' and fields['frequency'] != 'never':
        raise BookflowError('E_VALIDATION', details={'fields': [{'field': 'frequency', 'problem':
            'this group carries the schedule; a member of it cannot carry one too'}]})


def _template_write_preview(s, ctx, row, name, command, fields, index, reference_schema, group, ordinal, warnings, deleted=False):
    from bookflow.company.memorized_models import MemorizedWriteOutput
    at, via = clock.now_iso(), ctx.interface.value
    identity = row or {'id': new_id(), 'version': 0, 'created_at': at, 'created_by': s.actor.id,
                       'created_via': via, 'updated_at': at, 'updated_by': s.actor.id, 'updated_via': via}
    return MemorizedWriteOutput(
        id=identity['id'], version=identity['version'] + 1, created_at=identity['created_at'],
        created_by=identity['created_by'], created_via=identity['created_via'], updated_at=at,
        updated_by=s.actor.id, updated_via=via, name=name, command=command,
        current_revision_id=(row or {}).get('current_revision_id') or new_id(),
        revision_version=(row or {}).get('version', 0) + 1,
        schedule=dict(fields, status=fields.get('status', (row or {}).get('status', 'active'))),
        group_id=(group or {}).get('id'), group_ordinal=ordinal, reference_schema=reference_schema,
        references=edges.current_state(s, index), deleted=deleted, warnings=list(warnings), dry_run=s.dry_run)


def apply_write(plan, ctx, s):
    from bookflow.company.memorized_models import MemorizedWriteOutput
    verb, data, warnings = plan.data['verb'], plan.data, plan.data['warnings']
    at, event = clock.now_iso(), new_id()
    touched = []
    if verb == 'delete':
        row = data['row']
        after = dict(row, status='deleted', next_date=None, version=row['version'] + 1,
                     updated_at=at, updated_by=s.actor.id, updated_via=ctx.interface.value)
        touched.append(Touched('memorized_transaction', row['id'], 'delete', row['version'], after['version'], after, row, db='company'))
        summary = f"deleted memorized transaction {row['name']}"
        _audit(s, ctx, data['command_name'], summary, touched, event)
        s.company.conn.execute(c.memorized_transactions.update()
            .where(c.memorized_transactions.c.id == row['id'])
            .values(status='deleted', next_date=None, version=after['version'],
                    updated_at=at, updated_by=s.actor.id, updated_via=ctx.interface.value))
        revision = _one(s, c.memorized_transaction_revisions, c.memorized_transaction_revisions.c.id == row['current_revision_id'])
        out = _row_write_output(s, after, revision, [], warnings, deleted=True)
        return Applied(out, touched, summary, audited=True)

    fields, index = data['fields'], data['index']
    revision_id = new_id()
    if verb == 'create':
        header = _common(s, ctx, at)
        row = dict(header, name=data['name'], name_key=data['name_key'],
                   command=data['command'], current_revision_id=revision_id,
                   status=fields.get('status', 'active'), **{k: v for k, v in fields.items() if k != 'status'},
                   group_id=(data['group'] or {}).get('id'), group_ordinal=data['group_ordinal'],
                   audit_event_id=event)
        before = None
        version = 1
    else:
        before = data['row']
        version = before['version'] + 1
        row = dict(before, name=data['name'], name_key=data['name_key'], command=data['command'],
                   current_revision_id=revision_id, version=version, updated_at=at,
                   updated_by=s.actor.id, updated_via=ctx.interface.value,
                   group_id=(data['group'] or {}).get('id'), group_ordinal=data['group_ordinal'],
                   **{k: v for k, v in fields.items() if k in _schedule_out(before) or k == 'status'})
        row.setdefault('status', before['status'])

    revision = dict(
        id=revision_id, template_id=row['id'], version=version,
        previous_revision_id=before['current_revision_id'] if before else None,
        command=row['command'], payload=edges.canonical(data['payload']),
        payload_hash=edges.digest(data['payload']),
        schedule_snapshot=edges.canonical({k: fields[k] for k in sorted(fields)}),
        reference_schema=data['reference_schema'], **_created(s, ctx, at), audit_event_id=event)

    touched.append(Touched('memorized_transaction', row['id'], 'create' if before is None else 'update',
                           before['version'] if before else None, version, row, before, db='company'))
    touched.append(Touched('memorized_transaction_revision', revision_id, 'create', None, 1, revision, db='company'))
    summary = ('memorized ' if before is None else 'updated memorized ') + row['name']
    _audit(s, ctx, data['command_name'], summary, touched, event)
    if before is None:
        s.company.conn.execute(c.memorized_transactions.insert().values(**row))
    else:
        s.company.conn.execute(c.memorized_transactions.update()
            .where(c.memorized_transactions.c.id == row['id'])
            .values(**{k: row[k] for k in row if k not in ('id', 'created_at', 'created_by', 'created_via')}))
    s.company.conn.execute(c.memorized_transaction_revisions.insert().values(**revision))
    for edge in index:
        s.company.conn.execute(c.memorized_references.insert().values(
            id=new_id(), template_id=row['id'], revision_id=revision_id, created_at=at,
            **{k: edge[k] for k in ('field_path', 'ordinal', 'target_noun', 'target_record_type', 'target_id', 'target_active')}))
    out = _row_write_output(s, row, revision, index, warnings)
    return Applied(out, touched, summary, audited=True)


def _row_write_output(s, row, revision, index, warnings, deleted=False):
    from bookflow.company.memorized_models import MemorizedWriteOutput
    return MemorizedWriteOutput(
        id=row['id'], version=row['version'], created_at=row['created_at'], created_by=row['created_by'],
        created_via=row['created_via'], updated_at=row['updated_at'], updated_by=row['updated_by'],
        updated_via=row['updated_via'], name=row['name'], command=row['command'],
        current_revision_id=row['current_revision_id'], revision_version=revision['version'],
        schedule=_schedule_out(row), group_id=row.get('group_id'), group_ordinal=row.get('group_ordinal'),
        reference_schema=revision['reference_schema'], references=edges.current_state(s, index),
        deleted=deleted, warnings=list(warnings))


# ---------------------------------------------------------------- group writes

def prepare_group(s, ctx, inp, verb):
    warnings: list[str] = []
    data: dict[str, Any] = {'verb': verb, 'warnings': warnings}
    if verb == 'create':
        display, key = _name_key(inp.name)
        if _one(s, c.memorized_groups, c.memorized_groups.c.name_key == key, c.memorized_groups.c.status != 'deleted'):
            raise BookflowError('E_NAME_TAKEN', details={'name': display})
        fields = _schedule_fields(inp, warnings=warnings)
        data.update(name=display, name_key=key, fields=fields)
        preview = _group_write_preview(s, ctx, None, display, fields, 0, warnings)
    elif verb == 'update':
        row = _resolve_group(s, inp.memorized_group)
        _version_conflict(row, inp.expected_version, 'memorized_group')
        display, key = _name_key(inp.name) if inp.name else (row['name'], row['name_key'])
        if key != row['name_key'] and _one(s, c.memorized_groups, c.memorized_groups.c.name_key == key,
                                           c.memorized_groups.c.status != 'deleted'):
            raise BookflowError('E_NAME_TAKEN', details={'name': display})
        fields = _schedule_fields(inp, row, warnings=warnings)
        if inp.status:
            fields['status'] = inp.status
        members = _members(s, row['id'])
        if fields['frequency'] != 'never' and any(m['frequency'] != 'never' for m in members):
            raise BookflowError('E_VALIDATION', details={'fields': [{'field': 'frequency', 'problem':
                'a member of this group already carries its own schedule'}]})
        data.update(row=row, name=display, name_key=key, fields=fields)
        preview = _group_write_preview(s, ctx, row, display, fields, len(members), warnings)
    else:  # delete
        row = _resolve_group(s, inp.memorized_group)
        _version_conflict(row, inp.expected_version, 'memorized_group')
        members = _members(s, row['id'])
        if members:
            raise BookflowError('E_ACTIVE_DEPENDENTS', details={
                'record_type': 'memorized_group', 'record_id': row['id'],
                'dependents': [{'record_type': 'memorized_transaction', 'record_id': m['id'], 'name': m['name']} for m in members]})
        data.update(row=row)
        preview = _group_write_preview(s, ctx, row, row['name'], dict(_schedule_out(row), status='deleted', next_date=None), 0, warnings, deleted=True)
    return Plan(preview, data)


def _members(s, group_id):
    return sorted(_rows(s, c.memorized_transactions, c.memorized_transactions.c.group_id == group_id,
                        c.memorized_transactions.c.status != 'deleted'),
                  key=lambda row: (row['group_ordinal'] or 0, row['id']))


def _group_write_preview(s, ctx, row, name, fields, member_count, warnings, deleted=False):
    from bookflow.company.memorized_models import MemorizedGroupWriteOutput
    at, via = clock.now_iso(), ctx.interface.value
    identity = row or {'id': new_id(), 'version': 0, 'created_at': at, 'created_by': s.actor.id,
                       'created_via': via, 'updated_at': at, 'updated_by': s.actor.id, 'updated_via': via}
    return MemorizedGroupWriteOutput(
        id=identity['id'], version=identity['version'] + 1, created_at=identity['created_at'],
        created_by=identity['created_by'], created_via=identity['created_via'], updated_at=at,
        updated_by=s.actor.id, updated_via=via, name=name,
        schedule=dict(fields, status=fields.get('status', (row or {}).get('status', 'active'))),
        member_count=member_count, deleted=deleted, warnings=list(warnings), dry_run=s.dry_run)


def apply_group_write(plan, ctx, s):
    from bookflow.company.memorized_models import MemorizedGroupWriteOutput
    verb, data, warnings = plan.data['verb'], plan.data, plan.data['warnings']
    at, event = clock.now_iso(), new_id()
    if verb == 'delete':
        row = data['row']
        after = dict(row, status='deleted', next_date=None, version=row['version'] + 1,
                     updated_at=at, updated_by=s.actor.id, updated_via=ctx.interface.value)
        touched = [Touched('memorized_group', row['id'], 'delete', row['version'], after['version'], after, row, db='company')]
        summary = f"deleted memorized group {row['name']}"
        _audit(s, ctx, data['command_name'], summary, touched, event)
        s.company.conn.execute(c.memorized_groups.update().where(c.memorized_groups.c.id == row['id'])
            .values(status='deleted', next_date=None, version=after['version'], updated_at=at,
                    updated_by=s.actor.id, updated_via=ctx.interface.value))
        out = MemorizedGroupWriteOutput(**_group_out_fields(after), schedule=_schedule_out(after),
                                        member_count=0, deleted=True, warnings=list(warnings))
        return Applied(out, touched, summary, audited=True)
    fields = data['fields']
    before = None if verb == 'create' else data['row']
    if before is None:
        row = dict(_common(s, ctx, at), name=data['name'], name_key=data['name_key'],
                   status=fields.get('status', 'active'), **{k: v for k, v in fields.items() if k != 'status'},
                   audit_event_id=event)
        member_count = 0
    else:
        row = dict(before, name=data['name'], name_key=data['name_key'], version=before['version'] + 1,
                   updated_at=at, updated_by=s.actor.id, updated_via=ctx.interface.value,
                   **{k: v for k, v in fields.items()})
        member_count = len(_members(s, row['id']))
    touched = [Touched('memorized_group', row['id'], 'create' if before is None else 'update',
                       before['version'] if before else None, row['version'], row, before, db='company')]
    summary = ('memorized group ' if before is None else 'updated memorized group ') + row['name']
    _audit(s, ctx, data['command_name'], summary, touched, event)
    if before is None:
        s.company.conn.execute(c.memorized_groups.insert().values(**row))
    else:
        s.company.conn.execute(c.memorized_groups.update().where(c.memorized_groups.c.id == row['id'])
            .values(**{k: row[k] for k in row if k not in ('id', 'created_at', 'created_by', 'created_via')}))
    out = MemorizedGroupWriteOutput(**_group_out_fields(row), schedule=_schedule_out(row),
                                    member_count=member_count, warnings=list(warnings))
    return Applied(out, touched, summary, audited=True)


def _group_out_fields(row):
    return {key: row[key] for key in ('id', 'version', 'created_at', 'created_by', 'created_via',
                                      'updated_at', 'updated_by', 'updated_via')} | {'name': row['name']}


# ---------------------------------------------------------------- reads

def _due_counts(s, template_id):
    rows = _rows(s, c.memorized_occurrences, c.memorized_occurrences.c.template_id == template_id)
    blocked = sum(1 for row in rows if row['status'] == 'blocked')
    pending = sum(1 for row in rows if row['status'] == 'pending')
    entered = [row['entry_date'] for row in rows if row['status'] == 'entered' and row['entry_date']]
    return pending, blocked, max(entered) if entered else None


def _projected_slots(row, as_of, limit=12):
    return schedule.slots_due(row['frequency'], row['next_date'], row['anchor_day'], row['stop_date'],
                              row['remaining_count'], row['days_in_advance'], as_of, limit)


def page(s, inp):
    from bookflow.company.memorized_models import MemorizedListOutput, MemorizedSummary
    as_of = today(s, inp.as_of)
    t = c.memorized_transactions
    query = sa.select(t).where(t.c.status != 'deleted') if inp.status != 'deleted' else sa.select(t)
    if inp.status:
        query = query.where(t.c.status == inp.status)
    if inp.mode:
        query = query.where(t.c.mode == inp.mode)
    if inp.query:
        needle = '%' + unicodedata.normalize('NFC', inp.query.strip().casefold()) + '%'
        query = query.where(t.c.name_key.like(needle))
    if inp.cursor:
        query = query.where(sa.tuple_(t.c.name_key, t.c.id) > sa.tuple_(*_decode_cursor(inp.cursor)))
    query = query.order_by(t.c.name_key, t.c.id).limit(inp.limit + 1)
    found = [dict(row) for row in s.company.conn.execute(query).mappings()]
    more = len(found) > inp.limit
    found = found[:inp.limit]
    groups = {row['id']: row['name'] for row in _rows(s, c.memorized_groups)}
    items, due_total, blocked_total = [], 0, 0
    for row in found:
        pending, blocked, last = _due_counts(s, row['id'])
        due = pending + (len(_projected_slots(row, as_of)) if row['status'] == 'active' else 0)
        due_total += due
        blocked_total += blocked
        if inp.due_only and not due and not blocked:
            continue
        items.append(MemorizedSummary(
            id=row['id'], name=row['name'], command=row['command'], status=row['status'], mode=row['mode'],
            frequency=row['frequency'], next_date=row['next_date'], days_in_advance=row['days_in_advance'],
            remaining_count=row['remaining_count'], group_name=groups.get(row['group_id']),
            due_count=due, blocked_count=blocked, last_entered_on=last, version=row['version']))
    cursor = _encode_cursor(found[-1]['name_key'], found[-1]['id']) if more and found else None
    return MemorizedListOutput(items=items, next_cursor=cursor, as_of=as_of,
                               due_total=due_total, blocked_total=blocked_total)


def _encode_cursor(name_key, record_id):
    import base64
    return base64.urlsafe_b64encode(json.dumps([name_key, record_id]).encode()).decode().rstrip('=')


def _decode_cursor(value):
    import base64
    try:
        padded = value + '=' * (-len(value) % 4)
        name_key, record_id = json.loads(base64.urlsafe_b64decode(padded.encode()).decode())
        return str(name_key), str(record_id)
    except Exception:
        raise BookflowError('E_VALIDATION', details={'fields': [{'field': 'cursor', 'problem': 'is not a cursor this list issued'}]}) from None


def show(s, inp):
    from bookflow.company.memorized_models import MemorizedOutput
    row = _resolve_template(s, inp.memorized, include_deleted=True)
    revision = _one(s, c.memorized_transaction_revisions, c.memorized_transaction_revisions.c.id == row['current_revision_id'])
    index = _rows(s, c.memorized_references, c.memorized_references.c.revision_id == revision['id'])
    payload = json.loads(revision['payload'])
    from bookflow.core import registry
    command = registry.get(row['command'])
    occurrences = sorted(_rows(s, c.memorized_occurrences, c.memorized_occurrences.c.template_id == row['id']),
                         key=lambda o: (o['slot_date'], o['id']), reverse=True)[:inp.occurrence_limit]
    group = _one(s, c.memorized_groups, c.memorized_groups.c.id == row['group_id']) if row['group_id'] else None
    as_of = today(s)
    return MemorizedOutput(
        **{key: row[key] for key in ('id', 'version', 'created_at', 'created_by', 'created_via',
                                     'updated_at', 'updated_by', 'updated_via')},
        name=row['name'], command=row['command'], current_revision_id=revision['id'],
        revision_version=revision['version'], payload=payload, schedule=_schedule_out(row), group_id=row['group_id'], group_name=(group or {}).get('name'),
        group_ordinal=row['group_ordinal'], reference_schema=revision['reference_schema'],
        references=edges.current_state(s, index),
        fixed_fields=sorted(payload),
        recomputed_fields=sorted(set(command.input_model.model_fields) - set(payload)) if command else [],
        due_slots=_projected_slots(row, as_of) if row['status'] == 'active' else [],
        occurrences=[_occurrence_out(o, row['name']) for o in occurrences])


def group_page(s, inp):
    from bookflow.company.memorized_models import MemorizedGroupListOutput, MemorizedGroupSummary
    g = c.memorized_groups
    query = sa.select(g).where(g.c.status != 'deleted') if inp.status != 'deleted' else sa.select(g)
    if inp.status:
        query = query.where(g.c.status == inp.status)
    if inp.query:
        query = query.where(g.c.name_key.like('%' + unicodedata.normalize('NFC', inp.query.strip().casefold()) + '%'))
    if inp.cursor:
        query = query.where(sa.tuple_(g.c.name_key, g.c.id) > sa.tuple_(*_decode_cursor(inp.cursor)))
    found = [dict(row) for row in s.company.conn.execute(query.order_by(g.c.name_key, g.c.id).limit(inp.limit + 1)).mappings()]
    more = len(found) > inp.limit
    found = found[:inp.limit]
    items = [MemorizedGroupSummary(
        id=row['id'], name=row['name'], status=row['status'], mode=row['mode'], frequency=row['frequency'],
        next_date=row['next_date'], days_in_advance=row['days_in_advance'],
        remaining_count=row['remaining_count'], member_count=len(_members(s, row['id'])), version=row['version'])
        for row in found]
    cursor = _encode_cursor(found[-1]['name_key'], found[-1]['id']) if more and found else None
    return MemorizedGroupListOutput(items=items, next_cursor=cursor)


def group_show(s, inp):
    from bookflow.company.memorized_models import (GroupRunOutput, MemorizedGroupOutput, MemorizedSummary)
    row = _resolve_group(s, inp.memorized_group, include_deleted=True)
    members = _members(s, row['id'])
    summaries = []
    for member in members:
        pending, blocked, last = _due_counts(s, member['id'])
        summaries.append(MemorizedSummary(
            id=member['id'], name=member['name'], command=member['command'], status=member['status'],
            mode=member['mode'], frequency=member['frequency'], next_date=member['next_date'],
            days_in_advance=member['days_in_advance'], remaining_count=member['remaining_count'],
            group_name=row['name'], due_count=pending, blocked_count=blocked, last_entered_on=last,
            version=member['version']))
    runs = sorted(_rows(s, c.memorized_group_runs, c.memorized_group_runs.c.group_id == row['id']),
                  key=lambda r: r['slot_date'], reverse=True)[:inp.occurrence_limit]
    # Names for a frozen run come from every template that ever existed, not only from
    # today's members: a run that included a template since deleted still names it.
    names = {row['id']: row['name'] for row in _rows(s, c.memorized_transactions)}
    run_out = []
    for run in runs:
        occurrences = sorted(_rows(s, c.memorized_occurrences, c.memorized_occurrences.c.group_run_id == run['id']),
                             key=lambda o: o['group_ordinal'] or 0)
        run_out.append(GroupRunOutput(id=run['id'], slot_date=run['slot_date'], entry_date=run['entry_date'],
                                      members=[_occurrence_out(o, names.get(o['template_id'])) for o in occurrences]))
    return MemorizedGroupOutput(
        **{key: row[key] for key in ('id', 'version', 'created_at', 'created_by', 'created_via',
                                     'updated_at', 'updated_by', 'updated_via')},
        name=row['name'], schedule=_schedule_out(row), members=summaries,
        due_slots=_projected_slots(row, today(s)) if row['status'] == 'active' else [], runs=run_out)
