"""Operation-specific purchase approval inverses behind the shared batch door."""
from __future__ import annotations

import json

import sqlalchemy as sa

from bookflow import BookflowError
from bookflow.company import batches, bills, schema as c
from bookflow.company.bill_models import BillUpdateInput, BillVoidInput
from bookflow.hub import access, capabilities

KIND = 'approval-commit'
RECORD = 'approval_commit'


def _read(s, batch):
    batches.require_read(s, 'purchase-draft show')
    access.require_resource(s, 'ledger.read', 'member')


def _commit(s, identity):
    value = s.company.conn.execute(sa.select(c.approval_commits).where(
        c.approval_commits.c.id == identity)).mappings().first()
    if value is None:
        raise BookflowError('E_RECORD_NOT_FOUND', details={'record_type': RECORD, 'selector': identity})
    value = dict(value)
    if value.get('order_id'):
        value['document_id'] = value['order_id']
    return value


def _problems(s, effect, commit):
    """All observable dependencies, before any native inverse is allowed to write."""
    from bookflow.company import delivery, purchase_drafts
    draft = purchase_drafts.row(s, commit['draft_id'])
    from bookflow.company.approvals import domain
    native = domain(draft['kind'])
    document = native.resolve(s, commit['document_id'])
    problems = []

    def add(code, message, **details):
        problems.append(dict(seq=effect['seq'], record_type=RECORD, record_id=commit['id'],
                             code=code, message=message, details=details))

    if draft['state'] != 'committed' or draft['version'] != commit['draft_version']:
        add('edited_again', 'The committed purchase draft changed; reverse its later work first.')
    if document['version'] != commit['document_version'] or document['status'] not in (('open', 'partly_received', 'closed') if draft['kind'] == 'purchase_order' else ('posted',)):
        add('edited_again', 'The committed purchase changed; reverse its later work first.',
            bill=document['id'], expected_version=commit['document_version'], current_version=document['version'])
    uses = batches._document_uses(s, batches.document_record('purchase_order' if draft['kind'] == 'purchase_order' else 'bill'), [document['id']], {document['id']})
    if document['id'] in uses:
        held = uses[document['id']]
        add('in_use', 'The committed purchase is used by ' + ', '.join(held['documents']) + '.',
            bill=document['id'], dependencies=held)
    documents, messages = c.delivery_message_documents, c.delivery_messages
    linked = s.company.conn.execute(sa.select(messages.c.id, messages.c.number, messages.c.status).join(
        documents, documents.c.message_id == messages.c.id).where(documents.c.source_id == document['id'])).mappings()
    for message in linked:
        if message['status'] == 'sent' or delivery._exported(s, message['id']):
            add('external_artifact', 'The committed purchase has a sent or exported delivery artifact.',
                message=message['id'])
    if commit['operation'] == 'restore':
        add('native_restore_unavailable', 'Bill restoration has no native reversible adapter; this commit cannot be reversed.')
    return draft, document, problems


def _native(s, ctx, commit, document):
    """Current native authority, including human rights and explicit agent grants."""
    from bookflow.company import approvals, purchase_drafts
    from bookflow.company.purchase_order_models import PurchaseOrderVoidInput, PurchaseOrderUpdateInput
    draft = purchase_drafts.row(s, commit['draft_id'])
    is_order = draft['kind'] == 'purchase_order'
    native = approvals.domain(draft['kind'])
    operation = 'void' if commit['operation'] == 'create' else 'update'
    access.require_resource(s, 'ledger.read', 'member')
    access.require_resource(s, 'ledger.post', 'standard')
    capabilities.require_company(s, approvals.native_command(draft['kind']) + ' ' + operation)
    if operation == 'void':
        inp = (PurchaseOrderVoidInput(purchase_order=document['id'], expected_version=document['version']) if is_order
               else BillVoidInput(bill=document['id'], expected_version=document['version']))
        return native.prepare(s, ctx, inp, operation)
    if commit['operation'] != 'correct' or not commit['before_payload']:
        raise BookflowError('E_WORK_DEPENDENCY', message='This approval commit has no exact native inverse snapshot.')
    from bookflow.company import approvals
    payload = json.loads(commit['before_payload'])
    payload.update({'purchase_order' if is_order else 'bill': document['id'], 'expected_version': document['version']})
    inp = (PurchaseOrderUpdateInput if is_order else BillUpdateInput).model_validate(payload)
    return approvals.native_write(s, ctx, inp, operation, commit['draft_id'], inverse_commit_id=commit['id'])


def _plan(s, ctx, batch, effects, done, probe, blockers):
    steps, work = [], []
    for effect in effects:
        if (RECORD, effect['record_id']) in done:
            continue
        commit = _commit(s, effect['record_id'])
        draft, document, problems = _problems(s, effect, commit)
        blockers.extend(problems)
        if probe and commit['operation'] != 'restore':
            try:
                _native(s, ctx, commit, document)
            except BookflowError as error:
                blockers.append(dict(seq=effect['seq'], record_type=RECORD, record_id=commit['id'],
                    code=error.code, message=error.message, details=error.details))
        operation = {'create': draft['kind'].replace('_', '-') + ' void', 'correct': 'restore prior purchase revision',
                     'restore': 'bill delete'}[commit['operation']]
        step = dict(seq=effect['seq'], record_type=RECORD, record_id=commit['id'],
            label=f"Approval commit for {draft['kind']} {document['number']}", version=document['version'],
            steps=[dict(operation=operation, **{'purchase_order' if draft['kind'] == 'purchase_order' else 'bill': document['id']}, expected_version=document['version'],
                        draft=draft['id'], draft_version=draft['version'])])
        steps.append(step)
        work.append(dict(step, effect=effect))
    return dict(steps=steps, work=work, blockers=blockers, records=len(effects), pending=len(work))


def _run(s, ctx, batch, reversal_id, item):
    from bookflow.company import approvals, purchase_drafts
    effect = item['effect']
    with s.commits.operation(batches.OWNER, s.company):
        s.company.raw.execute('BEGIN IMMEDIATE')
        try:
            # A resumed or racing runner must never perform the financial inverse twice.
            if (RECORD, effect['record_id']) in batches._done_steps(s, batches._batch(s, batch['id'])):
                s.commits.commit(s.company, batches.OWNER)
                return
            commit = _commit(s, effect['record_id'])
            draft, document, problems = _problems(s, effect, commit)
            if problems:
                raise BookflowError('E_BATCH_BLOCKED', message=problems[0]['message'], details={'blockers': problems})
            native = _native(s, ctx, commit, document)
            owner = approvals.domain(draft['kind'])
            owner.persist_prepared(native, ctx, s, command_name='batch reverse')
            inverse_document = owner.resolve(s, document['id'])
            data = dict(commit_id=commit['id'], batch_id=batch['id'], reversal_batch_id=reversal_id,
                        document_id=document['id'], document_version=inverse_document['version'])
            if commit['operation'] == 'correct':
                data['original_version_before'] = draft['original_version']
                data['original_version_after'] = inverse_document['version']
                s.company.conn.execute(c.purchase_drafts.update().where(c.purchase_drafts.c.id == draft['id']).values(
                    original_version=inverse_document['version']))
                draft = purchase_drafts.row(s, draft['id'])
            approvals.record_transition(s, ctx, draft, 'approved' if commit['request_id'] else 'draft',
                                        'commit_reversed', data)
            after = purchase_drafts.row(s, draft['id'])
            batches.record_effect(s, reversal_id, record_type=RECORD, record_id=commit['id'], action='reversed',
                version_after=after['version'], before=dict(of_seq=effect['seq'], step=0, command='batch reverse',
                    document_version=inverse_document['version']), event_id=batches.last_event_id(s))
            s.commits.commit(s.company, batches.OWNER)
        except BaseException:
            if s.company.write_transaction:
                s.company.raw.rollback()
            raise


batches.register(batches.Adapter(KIND, planner=_plan, runner=_run, reader=_read,
    release_document_numbers=False, blocks=True, label='Approved purchase commitments'))
