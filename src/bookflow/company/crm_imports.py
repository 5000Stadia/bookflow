"""Source-keyed lead imports: one live batch per source, rows that land one by one and resume, and their inverse."""
from __future__ import annotations

import hashlib
import json

import sqlalchemy as sa

from bookflow.company import batches, crm, crm_conversion as conv, schema as c
from bookflow.company import crm_models as m
from bookflow.core import clock
from bookflow.core.errors import BookflowError
from bookflow.core.ids import new_id
from bookflow.core.registry import Touched

KIND = 'crm-lead-import'


def scope(source: str) -> str:
    return f'crm-lead-import:{source}'[:200]


def row_hash(row: m.LeadImportRow) -> str:
    return hashlib.sha256(json.dumps(row.model_dump(mode='json', exclude={'source_key'}, exclude_unset=True),
                                     sort_keys=True).encode()).hexdigest()


def authorize(s):
    crm.require_read(s)
    conv.require(s, 'crm.bulk', 'standard')
    conv.require(s, 'import', 'standard')
    conv.require(s, 'crm.write', 'standard')


def _held(s, source, keys):
    t = c.crm_lead_import_keys
    if not keys:
        return {}
    return {r['source_key']: dict(r) for r in s.company.conn.execute(sa.select(t).where(
        t.c.source == source, t.c.source_key.in_(keys), t.c.released_by.is_(None))).mappings()}


def _unfinished(s, source):
    b = c.batches
    row = s.company.conn.execute(sa.select(b.c.id, b.c.state).where(b.c.kind == KIND, b.c.scope == scope(source),
        b.c.reversal_of.is_(None), b.c.state.in_(('reversing', 'blocked')))).first()
    if row is not None:
        raise BookflowError('E_VALIDATION', message=(
            f'An earlier import from {source} is part way reversed; finish or clear that reversal first.'),
            details={'field': 'source', 'batch': row.id, 'state': row.state, 'problem': 'reversal_unfinished',
                     'next': [f'batch reverse {row.id}']})


def _changed(source, key, lead_id):
    return BookflowError('E_VALIDATION', message=(
        f'Row {key} from {source} was imported before with different values; a source key keeps its row. '
        'Edit the lead itself, or reverse the import first.'),
        details={'field': 'rows.source_key', 'source_key': key, 'lead': lead_id, 'problem': 'source_key_changed'})


def plan(s, ctx, inp):
    """Classify every row before any write: unchanged, changed (refused) or new (validated by a dry run)."""
    authorize(s)
    keys = [row.source_key for row in inp.rows]
    repeated = sorted({k for k in keys if keys.count(k) > 1})
    if repeated:
        raise BookflowError('E_VALIDATION', message='Each source key appears once in an import.',
                            details={'field': 'rows.source_key', 'source_keys': repeated[:20], 'problem': 'duplicate_key'})
    _unfinished(s, inp.source)
    held = _held(s, inp.source, keys)
    results, pending = [], []
    for index, row in enumerate(inp.rows):
        found = held.get(row.source_key)
        if found is not None:
            if found['payload_hash'] != row_hash(row):
                raise _changed(inp.source, row.source_key, found['lead_id'])
            results.append(m.LeadImportRowResult(source_key=row.source_key, outcome='unchanged', lead_id=found['lead_id']))
            continue
        try:
            batches.run_nested(s, ctx, 'lead create', _payload(row), source_ref=_ref(inp.source, row), dry_run=True,
                               recovery=False)
        except BookflowError as error:
            details = dict(error.details or {}, row=index, source_key=row.source_key)
            raise BookflowError(error.code, message=f'Row {row.source_key}: {error.message}', details=details) from None
        results.append(m.LeadImportRowResult(source_key=row.source_key, outcome='would_create', lead_id=None))
        pending.append(row)
    return results, pending


def _payload(row):
    return row.model_dump(mode='python', exclude={'source_key'}, exclude_unset=True) | {'display_name': row.display_name}


def _ref(source, row):
    return f'lead-import:{source}:{row.source_key}'


def _live_batch(s, ctx, source):
    """Find or open the source's live batch in one transaction, so two imports of one source share it."""
    b = c.batches
    with s.commits.operation(batches.OWNER, s.company):
        s.company.raw.execute('BEGIN IMMEDIATE')
        try:
            _unfinished(s, source)
            found = s.company.conn.execute(sa.select(b.c.id).where(b.c.kind == KIND, b.c.scope == scope(source),
                b.c.reversal_of.is_(None), b.c.state == 'live').order_by(b.c.created_at.desc())).scalar()
            batch = found or batches._insert_batch(s, ctx, kind=KIND, label=f'Lead import from {source}'[:200],
                scope=scope(source), ordering='record', source_type='lead_import')
            s.commits.commit(s.company, batches.OWNER)
        except BaseException:
            if s.company.write_transaction:
                s.company.raw.rollback()
            raise
    return batch


def run(s, ctx, inp, results, pending):
    """Land each new row in its own transaction with its batch effect and source key; a rerun resumes."""
    if not pending:
        return dict(source=inp.source, batch_id=None, created=0, unchanged=len(results), rows=results, next=[])
    batch = _live_batch(s, ctx, inp.source)
    by_key = {r.source_key: i for i, r in enumerate(results)}
    created = 0
    for row in pending:
        digest = row_hash(row)

        def recorder(session, cmd, applied, output, row=row, digest=digest):
            held = _held(session, inp.source, [row.source_key]).get(row.source_key)
            if held is not None:  # a racing import of the same source landed it first
                raise BookflowError('E_VERSION_CONFLICT', message='landed meanwhile',
                                    details={'problem': 'source_key_landed', 'lead': held['lead_id'],
                                             'same': held['payload_hash'] == digest})
            batches.record_effect(session, batch, record_type='lead', record_id=output['id'], action='created',
                                  version_after=output['version'], event_id=batches.last_event_id(session))
            session.company.conn.execute(c.crm_lead_import_keys.insert().values(id=new_id(), source=inp.source,
                source_key=row.source_key, payload_hash=digest, lead_id=output['id'], batch_id=batch,
                created_at=clock.now_iso()))
        try:
            output = batches.run_nested(s, ctx, 'lead create', _payload(row), source_ref=_ref(inp.source, row),
                                        recorder=recorder, recovery=False)
        except BookflowError as error:
            details = error.details or {}
            if details.get('problem') != 'source_key_landed':
                raise
            if not details['same']:
                raise _changed(inp.source, row.source_key, details['lead']) from None
            results[by_key[row.source_key]] = m.LeadImportRowResult(source_key=row.source_key, outcome='unchanged',
                                                                    lead_id=details['lead'])
            continue
        created += 1
        results[by_key[row.source_key]] = m.LeadImportRowResult(source_key=row.source_key, outcome='created',
                                                                lead_id=output['id'])
    return dict(source=inp.source, batch_id=batch, created=created, unchanged=len(results) - created, rows=results,
                next=[f'batch show {batch}'])


# ---------------------------------------------------------------- reversal through `batch reverse`

def _read(s, batch):
    crm.require_read(s)


def _authority(s):
    crm.require_read(s)
    conv.require(s, 'crm.bulk', 'standard')
    conv.require(s, 'crm.write', 'standard')


def problems(s, effect):
    lead = s.company.conn.execute(sa.select(c.crm_leads).where(c.crm_leads.c.id == effect['record_id'])).mappings().first()
    out = []

    def add(code, message, **details):
        out.append(dict(seq=effect['seq'], record_type='lead', record_id=effect['record_id'], code=code,
                        message=message, clears=None, details=details))

    if lead is None:
        add('missing', 'The imported lead no longer exists.')
        return None, out
    name = lead['display_name']
    if lead['version'] != effect['version_after']:
        add('edited_again', f'Lead {name} was edited after the import.', lead=lead['id'],
            expected_version=effect['version_after'], current_version=lead['version'])
    conversion = s.company.conn.execute(sa.select(c.crm_conversions.c.id).where(
        c.crm_conversions.c.lead_id == lead['id'])).first()
    if conversion is not None:
        add('converted', f'Lead {name} was converted after the import; reverse its conversion first.',
            conversion=conversion.id)
    contacts = [r[0] for r in s.company.conn.execute(sa.select(c.crm_lead_contacts.c.id).where(
        c.crm_lead_contacts.c.lead_id == lead['id']))]
    n, k, a = c.notes, c.todos, c.attachment_links

    def on(type_col, id_col):
        return sa.or_(sa.and_(type_col == 'lead', id_col == lead['id']),
                      sa.and_(type_col == 'lead_contact', id_col.in_(contacts)) if contacts else sa.false())
    note = s.company.conn.execute(sa.select(n.c.id).where(on(n.c.record_type, n.c.record_id)).limit(1)).first()
    if note is not None:
        add('later_note', f'A note on lead {name} depends on the import (note {note.id}).', note=note.id)
    task = s.company.conn.execute(sa.select(k.c.id, k.c.title).where(on(k.c.link_type, k.c.link_id)).limit(1)).first()
    if task is not None:
        add('later_task', f'A to-do for lead {name} depends on the import: {task.title} (to-do {task.id}).', todo=task.id)
    link = s.company.conn.execute(sa.select(a.c.id).where(on(a.c.record_type, a.c.record_id)).limit(1)).first()
    if link is not None:
        add('later_attachment', f'A file attached to lead {name} depends on the import.', attachment_link=link.id)
    return dict(lead), out


def _plan(s, ctx, batch, effects, done, probe, blockers):
    steps, work = [], []
    if probe:
        try:
            _authority(s)
        except BookflowError as error:
            blockers.append(dict(seq=None, record_type='batch', record_id=batch['id'], code=error.code,
                                 message=error.message, details=error.details))
    for effect in reversed(effects):
        if ('lead', effect['record_id']) in done:
            continue
        lead, found = problems(s, effect)
        blockers.extend(found)
        label = f"lead {lead['display_name'] if lead else effect['record_id']}"
        step = dict(seq=effect['seq'], record_type='lead', record_id=effect['record_id'], label=label,
                    version=lead['version'] if lead else None,
                    steps=[dict(operation='close and archive the imported lead, releasing its source key')])
        steps.append(step)
        work.append(dict(step, effect=effect))
    return dict(steps=steps, work=work, blockers=blockers, records=len(effects), pending=len(work))


def _run(s, ctx, batch, reversal_id, item):
    from bookflow.core.audit import write_event_to
    effect = item['effect']
    with s.commits.operation(batches.OWNER, s.company):
        s.company.raw.execute('BEGIN IMMEDIATE')
        try:
            if ('lead', effect['record_id']) in batches._done_steps(s, batches._batch(s, batch['id'])):
                s.commits.commit(s.company, batches.OWNER)
                return
            lead, found = problems(s, effect)
            if found:
                raise BookflowError('E_BATCH_BLOCKED', message=found[0]['message'], details={'blockers': found})
            _authority(s)
            at, via, actor = clock.now_iso(), ctx.interface.value, s.actor.id
            contacts = crm.contacts(s, lead['id'])
            row = dict(lead, status='closed', close_reason=f"Import reversed: {batch['label']}"[:1000], archived=True,
                       version=lead['version'] + 1, updated_at=at, updated_by=actor, updated_via=via)
            s.company.conn.execute(c.crm_leads.update().where(c.crm_leads.c.id == lead['id']).values(**row))
            s.company.conn.execute(c.crm_lead_import_keys.update().where(
                c.crm_lead_import_keys.c.lead_id == lead['id'], c.crm_lead_import_keys.c.batch_id == batch['id'],
                c.crm_lead_import_keys.c.released_by.is_(None)).values(released_by=reversal_id, released_at=at))
            touched = [Touched('lead', lead['id'], 'update', lead['version'], row['version'], dict(row, contacts=contacts),
                               dict(lead, contacts=contacts), db='company')]
            event = write_event_to(s.company, ctx, 'batch reverse', f"Reversed import of {item['label']}", touched,
                                   actor_id=actor, actor_kind=s.actor.kind, directive_code=getattr(s, 'directive_code', None))
            batches.record_effect(s, reversal_id, record_type='lead', record_id=lead['id'], action='reversed',
                version_after=row['version'], before=dict(of_seq=effect['seq'], command='batch reverse'), event_id=event)
            s.commits.commit(s.company, batches.OWNER)
        except BaseException:
            if s.company.write_transaction:
                s.company.raw.rollback()
            raise


batches.register(batches.Adapter(KIND, planner=_plan, runner=_run, reader=_read, release_document_numbers=False,
                                 blocks=True, label='Lead imports'))
