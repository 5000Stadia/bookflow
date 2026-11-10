"""Saved bill drafts use native commercial validation without entering any posting path."""
import json
import sqlalchemy as sa
from bookflow.company import bills, schema as c, purchase_draft_models as m
from bookflow.company.journal_models import checked_sum
from bookflow.core import clock
from bookflow.core.ids import new_id
from bookflow.core.money import Money
from bookflow.core.errors import BookflowError
from bookflow.core.registry import Plan, Applied, Touched, MatchedRecovery


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False)


def named(value):
    if value is None:
        return None
    return dict(id=value.id, name=getattr(value, 'full_name', None) or value.label)


def calculate(s, inp, ctx=None, operation='create', original_id=None, original_version=None):
    from bookflow.company.approval_bill_payload import calculate as native_calculate
    if ctx is None:
        from bookflow.core.context import Context, Interface
        ctx = Context.new(Interface.python, 'Draft preview')
    fields = {k:v for k,v in inp.model_dump(mode='json', exclude_unset=True).items() if k in m.Payload.model_fields}
    normalized, snapshot, native = native_calculate(s, ctx, fields, operation, original_id, original_version)
    # Native correction identity is stored on the draft header, not in its commercial payload.
    normalized = {k:v for k,v in normalized.items() if k in m.Payload.model_fields}
    bill = native.preview
    expenses = [dict(account=named(x.line_snapshot.account), amount=x.amount,
        memo=x.memo, customer=named(x.line_snapshot.customer), class_id=named(x.line_snapshot.class_id),billable=x.billable)
        for x in bill.revision.expenses]
    preview = m.Preview(total=bill.total,expenses=expenses,vendor=dict(id=bill.vendor_id,name=bill.vendor_name),
        ap_account=named(bill.revision.profile.ap_account),due_date=bill.due_date,currency=bill.currency)
    return m.Payload.model_validate(normalized), preview, snapshot


def row(s, draft):
    found = s.company.conn.execute(sa.select(c.purchase_drafts).where(c.purchase_drafts.c.id == draft)).mappings().first()
    if found is None:
        raise BookflowError('E_RECORD_NOT_FOUND', details={'record_type': 'purchase_draft', 'selector': draft})
    return dict(found)


def revision_out(value):
    return m.Revision(**{key: value[key] for key in m.Revision.model_fields if key not in ('payload', 'preview')},
                      payload=json.loads(value['payload']), preview=json.loads(value['preview']))


def output(s, value, added=None):
    history = [revision_out(r) for r in s.company.conn.execute(sa.select(c.purchase_draft_revisions).where(
        c.purchase_draft_revisions.c.draft_id == value['id']).order_by(c.purchase_draft_revisions.c.version)).mappings()]
    if added is not None:
        history.append(revision_out(added))
    from bookflow.company.approvals import request_for
    req = request_for(s, value['id'])
    return m.DraftOutput(snapshot=json.loads(value['snapshot']) if value.get('snapshot') else None, request_id=req['id'] if req else None, request_version=req['version'] if req else None, **{key: value[key] for key in ('id', 'version', 'kind', 'operation', 'state', 'created_at',
        'created_by', 'updated_at', 'updated_by', 'original_id', 'original_version')}, payload=json.loads(value['payload']), preview=json.loads(value['preview']),
        history=history)


def check(s, value, expected):
    if value['version'] != expected:
        # Draft-specific facts: no lookup into another document family's history.
        raise BookflowError('E_VERSION_CONFLICT', message='This draft changed; show it again and use its current version.',
            details=dict(record_type='purchase_draft', record_id=value['id'], expected_version=expected,
                         current_version=value['version'], updated_by=value['updated_by'], updated_at=value['updated_at'],
                         changed_fields=['payload', 'state']))
    if value['state'] not in ('draft', 'rejected'):
        raise BookflowError('E_APPROVAL_STATE', message='A cancelled draft remains readable and cannot be changed.',
                            details={'state': value['state'], 'allowed_actions': ['show', 'list']})


def request(inp, ctx):
    return encoded(dict(input=inp.model_dump(mode='json'), reason=ctx.reason, principal=ctx.on_behalf_of))


def recover(inp, ctx, s, verb):
    saved = s.company.conn.execute(sa.select(c.purchase_draft_operations).where(
        c.purchase_draft_operations.c.command == 'purchase-draft ' + verb,
        c.purchase_draft_operations.c.actor == s.actor.id,
        c.purchase_draft_operations.c.operation_key == inp.operation_key)).mappings().first()
    if saved is None:
        return None
    if saved['request'] != request(inp, ctx):
        raise BookflowError('E_IDEMPOTENCY_MISMATCH', message='This operation key already belongs to a different draft request.')
    return MatchedRecovery(m.DraftOutput.model_validate_json(saved['result']))


def prepare(s, ctx, inp, verb):
    replay = recover(inp, ctx, s, verb)
    if replay:
        return Plan(replay.output, {'replay': True})
    if not ctx.reason or not ctx.reason.strip():
        raise BookflowError('E_REASON_REQUIRED', message='Give a reason for saving, editing or cancelling this draft.')
    before = row(s, inp.draft) if verb != 'create' else None
    if before:
        check(s, before, inp.expected_version)
    at = clock.now_iso()
    if verb == 'cancel':
        after = dict(before, state='cancelled')
    else:
        entry = inp
        if before and 'operation' not in inp.model_fields_set:
            entry = inp.model_copy(update={'operation': before['operation'],
                'original_id': inp.original_id if 'original_id' in inp.model_fields_set else before['original_id'],
                'original_version': inp.original_version if 'original_version' in inp.model_fields_set else before['original_version']})
        payload, preview, snapshot = calculate(s, entry, ctx, entry.operation, entry.original_id, entry.original_version)
        after = dict(before or {}, kind=entry.kind, operation=entry.operation, state='draft',
                     payload=payload.model_dump_json(exclude_unset=True), preview=preview.model_dump_json(), currency=preview.currency,
                     snapshot=encoded(snapshot), original_id=bills.resolve(s, entry.original_id)['id'] if entry.original_id else None, original_version=entry.original_version)
    if before is None:
        after.update(id=new_id(), created_at=at, created_by=s.actor.id, created_via=ctx.interface.value)
    after.update(version=before['version'] + 1 if before else 1, updated_at=at,
                 updated_by=s.actor.id, updated_via=ctx.interface.value)
    revision = dict(id=new_id(), draft_id=after['id'], version=after['version'], state=after['state'],
                    payload=after['payload'], preview=after['preview'], actor=s.actor.id, principal=ctx.on_behalf_of,
                    interface=ctx.interface.value, reason=ctx.reason, created_at=at)
    return Plan(output(s, after, revision), dict(before=before, after=after, revision=revision,
                                               request=request(inp, ctx), key=inp.operation_key, verb=verb))


def apply(plan, ctx, s):
    if plan.data.get('replay'):
        return Applied(plan.preview, [], 'Recovered the saved draft operation')
    data = plan.data
    before, after = data['before'], data['after']
    if before:
        check(s, row(s, before['id']), before['version'])
        s.company.conn.execute(c.purchase_drafts.update().where(c.purchase_drafts.c.id == after['id'],
            c.purchase_drafts.c.version == before['version']).values(**after))
    else:
        s.company.conn.execute(c.purchase_drafts.insert().values(**after))
    s.company.conn.execute(c.purchase_draft_revisions.insert().values(**data['revision']))
    s.company.conn.execute(c.purchase_draft_operations.insert().values(id=new_id(), draft_id=after['id'],
        command='purchase-draft ' + data['verb'], actor=s.actor.id, operation_key=data['key'], request=data['request'],
        result=plan.preview.model_dump_json()))
    touched = Touched('purchase_draft', after['id'], 'update' if before else 'create',
                      before['version'] if before else None, after['version'], after, before, db='company')
    return Applied(plan.preview, [touched], 'Saved purchase draft; no accounting effect')


def listing(s, inp):
    query = sa.select(c.purchase_drafts).order_by(c.purchase_drafts.c.id)
    if inp.state:
        query = query.where(c.purchase_drafts.c.state == inp.state)
    if inp.cursor:
        try:
            state, last = inp.cursor.split(':', 1)
            if state != (inp.state or '*') or len(last) != 26:
                raise ValueError()
        except ValueError:
            raise BookflowError('E_VALIDATION', message='Cursor must belong to this draft state filter.') from None
        query = query.where(c.purchase_drafts.c.id > last)
    rows = list(s.company.conn.execute(query.limit(inp.limit + 1)).mappings())
    more = len(rows) > inp.limit
    rows = rows[:inp.limit]
    return m.ListOutput(items=[output(s, r) for r in rows],
                        next_cursor=(inp.state or '*') + ':' + rows[-1]['id'] if more else None)
