"""Strict, atomic native unmerge inverses for merge batches."""
from __future__ import annotations

import json

from bookflow import BookflowError
from bookflow.company import batches, list_merges, party_merges

TABLES = {'customer': 'customers', 'vendor': 'vendors', 'account': 'accounts', 'item': 'items'}


def _source(kind):
    return ('party_merges', 'party_kind', 'party_merge') if kind in party_merges.KINDS else (
        'list_merges', 'list_kind', 'list_merge')


def _documents(db, kind, merged):
    if kind in party_merges.KINDS:
        ids = db.raw.execute(party_merges._document_ids_sql(), {'kind': kind, 'party': merged}).fetchall()
    elif kind == 'account':
        ids = db.raw.execute('SELECT DISTINCT transaction_id FROM posting_lines WHERE account_id=?', (merged,)).fetchall()
    else:
        ids = db.raw.execute(' UNION '.join(
            f'SELECT d.transaction_id FROM {t} p JOIN document_lines d ON d.id=p.document_line_id '
            'JOIN transactions tx ON tx.current_revision_id=d.revision_id WHERE p.item_id=?'
            for t in list_merges._ITEM_LINE_TABLES), (merged,) * len(list_merges._ITEM_LINE_TABLES)).fetchall()
    documents = {('transactions', i) for (i,) in ids}
    sources = []
    if kind == 'customer':
        sources.append(('work_documents', 'work_revisions', 'customer_id'))
    if kind == 'vendor':
        sources.append(('purchase_orders', 'purchase_order_revisions', 'vendor_id'))
    if kind == 'item':
        sources.append(('work_documents', 'work_lines', 'item_id'))
    if kind in ('item', 'account', 'customer'):
        sources.append(('purchase_orders', 'purchase_order_lines', f'{kind}_id'))
    for table, refs, column in sources:
        join = 'id' if refs.endswith('revisions') else 'revision_id'
        documents.update((table, i) for (i,) in db.raw.execute(
            f'SELECT DISTINCT d.id FROM {table} d JOIN {refs} r ON r.{join}=d.current_revision_id '
            f'WHERE r.{column}=?', (merged,)))
    return [{'table': table, 'id': i, 'version': db.raw.execute(
        f'SELECT version FROM {table} WHERE id=?', (i,)).fetchone()[0]} for table, i in sorted(documents)]


def record(s, ctx, kind, merge_id, planned):
    """Called inside the merge's own transaction, after its references and audit exist."""
    table, _, record_type = _source(kind)
    batch_id = batches._insert_batch(s, ctx, kind=f'{kind}-merge',
        label=f"Merge {kind} {planned.merged['name']} into {planned.survivor['name']}",
        scope=f"{kind}-merge:{planned.survivor['id']}", ordering='record',
        source_type=record_type, source_id=merge_id)
    event = s.company.raw.execute(f'SELECT audit_event_id FROM {table} WHERE id=?', (merge_id,)).fetchone()[0]
    batches.record_effect(s, batch_id, record_type=record_type, record_id=merge_id, action='created',
        version_after=1, before={'kind': kind, 'documents': _documents(s.company, kind, planned.merged['id'])}, event_id=event)
    return batch_id


def existing(db, kind, merge_id):
    return db.raw.execute('SELECT id FROM batches WHERE kind=? AND source_id=? ORDER BY rowid DESC LIMIT 1',
                          (f'{kind}-merge', merge_id)).fetchone()


def output_link(db, kind, merge):
    found = existing(db, kind, merge['id']) if merge else None
    batch_id = found[0] if found else None
    return {'batch_id': batch_id, 'next': f'batch reverse {batch_id} --dry-run' if batch_id else None}


def _check(s, effect):
    db = s.company
    saved = json.loads(effect['before'])
    kind = saved['kind']
    table, column, _ = _source(kind)
    row = db.raw.execute(f'SELECT * FROM {table} WHERE id=?', (effect['record_id'],))
    names = [d[0] for d in row.description]
    value = row.fetchone()
    merge = dict(zip(names, value)) if value else None
    blockers, references = [], []

    def block(code, message, **details):
        blockers.append(dict(seq=effect['seq'], record_type=effect['record_type'], record_id=effect['record_id'],
                             code=code, message=message, details=details))

    if merge is None or merge['undone_at'] is not None:
        block('merge_changed', f"{kind} merge {effect['record_id']} is no longer standing.")
        return blockers, references, merge
    for later, merged_id in db.raw.execute(
            f'SELECT id, merged_id FROM {table} WHERE {column}=? AND survivor_id=? AND undone_at IS NULL '
            f'AND rowid > (SELECT rowid FROM {table} WHERE id=?) ORDER BY rowid',
            (kind, merge['survivor_id'], merge['id'])):
        block('later_merge', f'{kind} merge {later} of {merged_id} into survivor {merge["survivor_id"]} must be undone first.', merge_id=later)
    t = TABLES[kind]
    key = 'name_key' if kind == 'vendor' else 'full_name_key'
    entry = db.raw.execute(f'SELECT {key}, active FROM {t} WHERE id=?', (merge['merged_id'],)).fetchone()
    if entry and merge['deactivated_merged']:
        for other, name in db.raw.execute(f'SELECT id, name FROM {t} WHERE {key}=? AND active=1 AND id<>?',
                                         (entry[0], merge['merged_id'])):
            block('name_collision', f'{kind} {name} ({other}) holds the name needed to reactivate {merge["merged_id"]}.', holder_id=other)
        references.append(dict(table=t, record_id=merge['merged_id'], field='active', value=True))
    for doc in saved.get('documents', []):
        current = db.raw.execute(f"SELECT version FROM {doc['table']} WHERE id=?", (doc['id'],)).fetchone()
        if current is None or current[0] != doc['version']:
            block('reference_changed', f"{doc['table']} {doc['id']} changed or was deleted since the merge.", **doc)
    if kind in party_merges.KINDS and merge['deactivated_merged']:
        try:
            party_merges.parties.plan_party_active_change(db, kind, merge['merged_id'], True,
                actor_id=s.actor.id, via='', unmerging=True)
        except BookflowError as error:
            block(error.code, error.message, **error.details)
    if kind in list_merges.KINDS and merge['deactivated_merged']:
        from bookflow.company import list_service, schema
        try:
            table = schema.metadata.tables[TABLES[kind]]
            list_service.plan_activation(db, table, list_merges._resolve(db, kind, merge['merged_id']))
        except BookflowError as error:
            block(error.code, error.message, **error.details)
    if kind == 'item' and merge['deactivated_merged']:
        from bookflow.company import items
        item = items.resolve_item(db, merge['merged_id'])
        if item.get('barcode'):
            try:
                items.assert_barcode_available(db, item['barcode'], exclude_id=item['id'],
                                               reactivating=item['full_name'])
            except BookflowError as error:
                block(error.code, error.message, **error.details)
    module = party_merges if kind in party_merges.KINDS else list_merges
    try:
        plan = module.plan_unmerge(db, kind, merge['merged_id'], **(
            {'check_barcode': False} if module is list_merges else {}))
        for ref in getattr(plan, 'kept', ()):
            block('reference_changed', f"{ref['table_name']} {ref['record_id']}.{ref['column_name']} changed or was deleted; unmerge would keep it.", reference=ref)
        for ref in getattr(plan, 'restores', ()):
            references.append(dict(table=ref['table_name'], record_id=ref['record_id'],
                field=ref['column_name'] if ref['action'] == 'repoint' else 'active',
                value=list_merges._typed(ref['column_name'], ref['before_value']) if ref['action'] == 'repoint' else True))
    except BookflowError as error:
        block(error.code, error.message, **error.details)
    return blockers, references, merge


def _plan(s, ctx, batch, effects, done, probe, blockers):
    pending = [e for e in effects if (e['record_type'], e['record_id']) not in done]
    steps, work = [], []
    for effect in pending:
        kind = json.loads(effect['before'])['kind']
        problems, references, merge = _check(s, effect)
        blockers.extend(problems)
        if merge is None:
            continue
        command = f'{kind} unmerge'
        payload = {'merged': merge['merged_id']}
        if probe:
            try:
                batches.run_nested(s, ctx, command, payload, source_ref=f"reverse:{batch['id']}", dry_run=True)
            except BookflowError as error:
                # Native item unmerge wraps barcode validation in E_MERGE_REFUSED. It is
                # the same collision already enumerated alongside the kept references.
                duplicate = any((b['code'] == error.code and b.get('details') == error.details) or (
                    error.details.get('item_id') and b.get('details', {}).get('item_id') == error.details['item_id'])
                    for b in problems)
                if not duplicate:
                    blockers.append(dict(seq=effect['seq'], record_type=effect['record_type'], record_id=effect['record_id'],
                                         code=error.code, message=error.message, details=error.details))
        step = dict(seq=effect['seq'], record_type=effect['record_type'], record_id=effect['record_id'],
                    label=f"{kind} merge {effect['record_id']}", operation=command, action='unmerge', version=1,
                    steps=[dict(command=command, payload=payload)], references=references,
                    alias=dict(merged_id=merge['merged_id'], survivor_id=merge['survivor_id'], remove=True))
        steps.append(step)
        work.append(dict(step, effect=effect, command=command, payload=payload))
    return dict(steps=steps, work=work, blockers=blockers, records=len(effects), pending=len(pending))


def _run(s, ctx, batch, reversal_id, item):
    def check_locked(session):
        blockers, _, _ = _check(session, item['effect'])
        if blockers:
            raise BookflowError('E_MERGE_REFUSED', message='Merge cannot be completely restored.', details={'blockers': blockers})
    def recorded(session, command, applied, output):
        if not output['changed'] or output.get('kept', 0):
            raise BookflowError('E_MERGE_REFUSED', message='A partial unmerge is not a batch reversal.')
        batches.record_effect(session, reversal_id, record_type=item['record_type'], record_id=item['record_id'],
            action='reversed', version_after=2, before={'of_seq': item['seq'], 'command': item['command']},
            event_id=batches.last_event_id(session))
    batches.run_nested(s, ctx, item['command'], item['payload'], source_ref=f"reverse:{batch['id']}",
                       before=check_locked, recorder=recorded)


for _kind in TABLES:
    batches.register(batches.Adapter(f'{_kind}-merge', planner=_plan, runner=_run, blocks=True))
