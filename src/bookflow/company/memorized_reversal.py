"""One reversible memorized run: native document inverses followed by exact slot restoration."""
from __future__ import annotations
import json
import sqlalchemy as sa
from bookflow import BookflowError
from bookflow.company import batches, schema as c
from bookflow.core.audit import write_event_to
from bookflow.core.registry import Touched

KIND = 'memorized-run'
STATE = 'memorized_run'
SCHEDULE = ('next_date', 'remaining_count', 'status', 'version', 'current_revision_id')


def snapshot(s, ids):
    templates = [dict(r) for r in s.company.conn.execute(sa.select(c.memorized_transactions).where(
        c.memorized_transactions.c.id.in_(ids))).mappings()]
    groups = {r['group_id'] for r in templates if r['group_id']}
    schedules = {}
    for table, rows in ((c.memorized_transactions, templates), (c.memorized_groups,
            s.company.conn.execute(sa.select(c.memorized_groups).where(c.memorized_groups.c.id.in_(groups))).mappings())):
        schedules[table.name] = {r['id']: {k: r[k] for k in SCHEDULE if k in r} for r in rows}
    occurrences = {r['id']: dict(r) for r in s.company.conn.execute(sa.select(c.memorized_occurrences).where(
        c.memorized_occurrences.c.template_id.in_(ids))).mappings()}
    holds = {r['occurrence_id']: dict(r) for r in s.company.conn.execute(sa.select(c.scheduled_holds).where(
        c.scheduled_holds.c.occurrence_id.in_(occurrences))).mappings()}
    return dict(ids=sorted(ids), schedules=schedules, occurrences=occurrences, holds=holds)


def start(s, ctx, data, ids):
    ids = set(ids)
    if not ids:
        return
    group_ids = set(s.company.conn.execute(sa.select(c.memorized_transactions.c.group_id).where(
        c.memorized_transactions.c.id.in_(ids))).scalars()) - {None}
    for batch_row in s.company.conn.execute(sa.select(c.batches).where(
            c.batches.c.kind == KIND, c.batches.c.state.in_(('reversing', 'blocked')))).mappings():
        held = _state(batches.effects(s, batch_row['id']))
        if held and (set(held['prior']['ids']) & ids or
                     set(held['prior']['schedules']['memorized_groups']) & group_ids):
            raise BookflowError('E_VALIDATION', message=f"Finish reversing memorized run {batch_row['id']} before entering this schedule.")
    old = data.get('_run_prior')
    if old:
        extra = snapshot(s, ids - set(old['ids']))
        old['ids'] = sorted(set(old['ids']) | ids)
        for key in ('occurrences', 'holds'):
            old[key].update(extra[key])
        for table, rows in extra['schedules'].items():
            for key, value in rows.items():
                old['schedules'][table].setdefault(key, value)
    else:
        data['_run_prior'] = snapshot(s, ids)
        data['batch_id'] = batches._insert_batch(s, ctx, kind=KIND, ordering='record', scope=KIND,
            label=f"Memorized run {data.get('as_of', '')}", source_type=KIND)


def record(s, data):
    if not data.get('batch_id'):
        return
    batches.record_effect(s, data['batch_id'], record_type=STATE, record_id=data['batch_id'], action='updated',
        version_after=None, before={'prior': data['_run_prior'], 'after': snapshot(s, data['_run_prior']['ids'])},
        event_id=batches.last_event_id(s))


def child_recorder(batch_id):
    def record_child(s, cmd, applied, output):
        from bookflow.company.reversal_registry import DOCUMENTS, transaction_record
        seen = set()
        for touched in applied.touched:
            if touched.db != 'company' or touched.action != 'create':
                continue
            target = transaction_record(s, touched.record_id) if touched.record_type == 'transaction' else (
                (touched.record_type, touched.record_id) if touched.record_type in DOCUMENTS else None)
            if target is None or target in seen:
                continue
            family, key = target
            rec = DOCUMENTS.get(family)
            if rec is None:
                continue
            seen.add(target)
            current = batches._current(s, family, [key])[key]
            batches.record_effect(s, batch_id, record_type=family, record_id=key, action='created',
                version_after=current['version'], event_id=batches.last_event_id(s))
        if not seen and cmd.name != 'batch-invoice post' and any(t.action == 'create' for t in applied.touched):
            key = output.get('id') or output.get('selection_id') or batch_id
            batches.record_effect(s, batch_id, record_type='memorized_child', record_id=key, action='created',
                version_after=output.get('version'), before={'command': cmd.name}, event_id=batches.last_event_id(s))
    return record_child


def _state(effects):
    states = [json.loads(e['before']) for e in effects if e['record_type'] == STATE]
    # Scope may expand after a scheduler hold-only transaction; the final prior includes that expansion.
    return states[-1] if states else None


def public_state(state):
    """Scheduling facts are public; nested command receipts may contain separately authorized fields."""
    return {**state, 'occurrences': {key: {k: v for k, v in row.items() if k != 'result'}
                                     for key, row in state['occurrences'].items()}}


def state_changes(state):
    """The precise scheduling edits in the reversal, omitting untouched history and receipt bodies."""
    prior, after = state['prior'], state['after']
    changes = {}
    for kind in ('occurrences', 'holds'):
        changes[kind] = []
        for key in sorted(set(prior[kind]) | set(after[kind])):
            before, current = prior[kind].get(key), after[kind].get(key)
            if before == current:
                continue
            changes[kind].append(dict(id=key, action='remove' if before is None else 'restore',
                values={k: v for k, v in before.items() if k != 'result'} if before else None))
    changes['schedules'] = []
    for table, rows in prior['schedules'].items():
        for key, row in rows.items():
            values = {field: value for field, value in row.items()
                      if value != after['schedules'][table][key][field]}
            if values:
                changes['schedules'].append(dict(record_type=table, id=key, values=values))
    return changes


def reversed_attempt(s, key):
    batch_id = key.rsplit('-', 1)[-1]
    return s.company.conn.execute(sa.select(c.batches.c.state).where(c.batches.c.id == batch_id)).scalar() == 'reversed'


def retired(s, record_id):
    if not record_id:
        return False
    from bookflow.company.reversal_registry import DOCUMENTS
    seen = set()
    for rec in DOCUMENTS.values():
        if rec.table.name in seen:
            continue
        seen.add(rec.table.name)
        if rec.table.name == 'shipments':
            from bookflow.company.reversal_registry import record_current
            row = record_current(s, rec, [record_id]).get(record_id)
            if row and row['status'] == 'voided':
                return True
        if 'status' not in rec.table.c:
            continue
        status = s.company.conn.execute(sa.select(rec.table.c.status).where(rec.table.c.id == record_id)).scalar()
        if status in ('voided', 'deleted'):
            return True
    return False


def _authority(s, state):
    from bookflow.company.automation import require_command
    require_command(s, 'memorized update')
    if state['prior']['schedules']['memorized_groups']:
        require_command(s, 'memorized-group update')


def _read(s, batch):
    batches.require_read(s, 'memorized list')
    batches.require_read(s, 'memorized-group list')
    batches.require_batch_read(s, batch)


def _check(s, batch, state):
    issues = []
    ids = state['prior']['ids']
    groups = set(state['prior']['schedules']['memorized_groups'])
    later = s.company.raw.execute("SELECT id, label FROM batches WHERE kind = ? AND reversal_of IS NULL "
        "AND state != 'reversed' AND rowid > (SELECT rowid FROM batches WHERE id = ?)", (KIND, batch['id'])).fetchall()
    for other, label in later:
        other_state = _state(batches.effects(s, other))
        if other_state and (set(ids) & set(other_state['prior']['ids']) or groups & set(
                other_state['prior']['schedules']['memorized_groups'])):
            issues.append(dict(code='later_batch', message=f'Later memorized run {other} ({label}) must be reversed first.',
                               record_type='batch', record_id=other, seq=None))
    current = snapshot(s, ids)
    if current != state['after']:
        changed = []
        for table, rows in current['schedules'].items():
            for key in set(rows) | set(state['after']['schedules'][table]):
                if rows.get(key) != state['after']['schedules'][table].get(key):
                    changed.append(f'{table} {key}')
        for kind in ('occurrences', 'holds'):
            for key in set(current[kind]) | set(state['after'][kind]):
                if current[kind].get(key) != state['after'][kind].get(key):
                    changed.append(f'{kind} {key}')
        issues.append(dict(code='edited_again', message=f"Changed after run {batch['id']}: " + ', '.join(changed),
                           record_type=STATE, record_id=batch['id'], seq=None))
    return issues


def _document_blockers(s, effects):
    from bookflow.company import reconciliation_changes, deposit_dependencies
    issues = []
    for effect in effects:
        if effect['record_type'] == STATE:
            continue
        key = effect['record_id']
        header = s.company.conn.execute(sa.select(c.transactions).where(c.transactions.c.id == key)).mappings().first()
        label = f"{effect['record_type']} {header['number'] if header else key}"
        def add(code, message):
            issues.append(dict(code=code, message=label + ': ' + message, seq=effect['seq'],
                               record_type=effect['record_type'], record_id=key))
        if header and header['status'] not in ('voided', 'deleted'):
            for claim in reconciliation_changes._held(s, transactions=[key]):
                add('reconciled', f"reconciliation {claim['certificate_id'] or claim['opening_id']} holds this document.")
            claim = deposit_dependencies.active_claim(s, key)
            if claim:
                add('deposited', f"deposit {claim['transaction_id']} holds this document.")
        d, m = c.delivery_message_documents, c.delivery_messages
        for message in s.company.conn.execute(sa.select(m.c.id, m.c.number).join(d, d.c.message_id == m.c.id).where(
                d.c.source_id == key, m.c.status == 'sent')):
            add('sent', f"message {message.number} ({message.id}) was sent; sent mail cannot be recalled.")
    return issues


def _plan(s, ctx, batch, effects, done, probe, blockers):
    blockers.extend(_document_blockers(s, effects))
    unknown = [e for e in effects if e['record_type'] == 'memorized_child']
    for effect in unknown:
        command = json.loads(effect['before'])['command']
        blockers.append(dict(code='no_inverse', message=f"{command} child {effect['record_id']} has no complete inverse.",
                             record_type=effect['record_type'], record_id=effect['record_id'], seq=effect['seq']))
    state = _state(effects)
    state_done = (STATE, batch['id']) in done
    if state and not state_done:
        blockers.extend(_check(s, batch, state))
        if probe:
            try:
                _authority(s, state)
            except BookflowError as error:
                blockers.append(dict(code=error.code, message=error.message, details=error.details,
                                     record_type=STATE, record_id=batch['id'], seq=None))
    found = batches.plan_records(s, ctx, batch, [e for e in effects if e['record_type'] not in (STATE, 'memorized_child')], done, probe, blockers)
    found['work'] = [dict(document=item, record_id=item[0]['record_id'], record_type=item[0]['record_type'],
                          seq=item[0]['seq']) for item in found['work']]
    if state:
        found['records'] += 1
    if state and not state_done:
        step = dict(seq=0, record_type=STATE, record_id=batch['id'], action='restore', version=None,
                    steps=['restore occurrences, holds and schedules'], changes=state_changes(state))
        found['steps'].append(step)
        found['work'].append(dict(state=state, **step))
        found['pending'] += 1
    found.pop('fingerprint', None)
    return found


def _run(s, ctx, batch, reversal_id, item):
    if 'document' in item:
        g, rec, row, steps, offset = item['document']
        version = row['version']
        for i, (name, payload) in enumerate(steps):
            body = batches._payload(s, rec, batch, row, name, payload, version)
            output = batches.run_nested(s, ctx, name, body, source_ref=f"reverse:{batch['id']}", recovery=False,
                recorder=batches._reversal_recorder(reversal_id, g, offset + i, None))
            version = output.get('version')
        return
    _authority(s, item['state'])
    with s.commits.operation(batches.OWNER, s.company):
        s.company.raw.execute('BEGIN IMMEDIATE')
        try:
            state = item['state']
            problems = _check(s, batch, state)
            if problems:
                raise BookflowError('E_BATCH_BLOCKED', message=problems[0]['message'])
            prior, after = state['prior'], state['after']
            # Occurrence history remains in audit and the run ledger. Group membership history is immutable.
            ids = [key for key in set(prior['occurrences']) | set(after['occurrences'])
                   if prior['occurrences'].get(key) != after['occurrences'].get(key)]
            hold_ids = set(ids) | {key for key in set(prior['holds']) | set(after['holds'])
                                   if prior['holds'].get(key) != after['holds'].get(key)}
            s.company.conn.execute(c.scheduled_holds.delete().where(c.scheduled_holds.c.occurrence_id.in_(hold_ids)))
            s.company.conn.execute(c.memorized_occurrences.delete().where(c.memorized_occurrences.c.id.in_(ids)))
            rows = [prior['occurrences'][key] for key in ids if key in prior['occurrences']]
            if rows:
                s.company.conn.execute(c.memorized_occurrences.insert(), rows)
            rows = [prior['holds'][key] for key in hold_ids if key in prior['holds']]
            if rows:
                s.company.conn.execute(c.scheduled_holds.insert(), rows)
            for table_name, rows in prior['schedules'].items():
                table = c.metadata.tables[table_name]
                for key, values in rows.items():
                    s.company.conn.execute(table.update().where(table.c.id == key).values(**values))
            event = write_event_to(s.company, ctx, 'batch reverse', 'Restored memorized occurrences, holds and schedules',
                [Touched(STATE, batch['id'], 'update', None, None, prior, after, db='company')],
                actor_id=s.actor.id, actor_kind=s.actor.kind, directive_code=s.directive_code)
            batches.record_effect(s, reversal_id, record_type=STATE, record_id=batch['id'], action='reversed',
                                  version_after=None, event_id=event)
            s.commits.commit(s.company, batches.OWNER)
        except BaseException:
            if s.company.write_transaction:
                s.company.raw.rollback()
            raise


batches.register(batches.Adapter(KIND, planner=_plan, runner=_run, reader=_read,
                                 release_document_numbers=False, blocks=True))
