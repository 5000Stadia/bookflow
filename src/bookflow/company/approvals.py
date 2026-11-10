"""One-step bill approval. Approval evidence and native money writes share a writer."""
import json
import sqlalchemy as sa
from bookflow.company import schema as c, purchase_drafts as d, approval_models as m
from bookflow.core import clock
from bookflow.core.ids import new_id
from bookflow.core.errors import BookflowError
from bookflow.core.registry import Plan, Applied, Touched, MatchedRecovery
from bookflow.hub import access, capabilities


def fail(code, message, **details):
    raise BookflowError(code, message=message, details=details)


def principal(s, ctx):
    return capabilities.principal_of(s) or ctx.on_behalf_of if s.actor.kind != 'human' else s.actor.id


def enabled(s):
    return bool(s.company.conn.execute(sa.select(c.company_info.c.bill_approvals_enabled)).scalar())


def require_native_door(s, operation, bill=None):
    if operation not in ('post', 'update', 'restore'):
        return
    captured = False
    if bill:
        from bookflow.company import bills
        identity = bills.resolve(s, bill)['id']
        captured = s.company.conn.execute(sa.select(c.purchase_drafts.c.id).where(
            c.purchase_drafts.c.original_id == identity,
            c.purchase_drafts.c.state.in_(['pending', 'approved']))).first() is not None
    if enabled(s) or captured:
        fail('E_APPROVAL_REQUIRED', 'bill ' + operation + ' requires an approved purchase draft; use purchase-draft create, submit and commit.',
             command='bill ' + operation, next_command='purchase-draft create')


_SEAL = object()


def validate_native_seal(s, plan):
    if plan.data.get('_approval_seal') is _SEAL:
        return
    require_native_door(s, plan.data['operation'], getattr(plan.data.get('input'), 'bill', None))


def native_write(s, ctx, inp, operation, draft_id, inverse_commit_id=None):
    from bookflow.company import bills
    access.require_resource(s, 'ledger.post', 'standard')
    capabilities.require_company(s, 'bill ' + operation)
    draft = d.row(s, draft_id)
    if inverse_commit_id is None:
        if draft['state'] != 'approved' and (draft['state'] != 'draft' or enabled(s)):
            fail('E_APPROVAL_REQUIRED', 'Commit requires the approved draft revision.')
        if draft['state'] != 'approved':
            require_native_door(s, operation, getattr(inp, 'bill', None))
    else:
        commit = get(s, c.approval_commits, inverse_commit_id)
        if commit['draft_id'] != draft_id or commit['operation'] != 'correct' or draft['state'] != 'committed':
            fail('E_APPROVAL_STATE', 'The inverse must match this committed correction.')
        if inp.bill != commit['document_id']:
            fail('E_APPROVAL_STATE', 'The inverse must target its committed native bill.')
        expected = json.loads(commit['before_payload'])
        proposed = inp.model_dump(mode='json', exclude_unset=True)
        for key in ('bill', 'expected_version'):
            expected.pop(key, None)
            proposed.pop(key, None)
        if expected != proposed:
            fail('E_APPROVAL_STALE', 'The inverse must restore the captured prior commercial payload.')
    plan = bills._prepare_native(s, ctx, inp, operation)
    if inverse_commit_id is not None:
        expected_snapshot = json.loads(commit['before_snapshot'])
        # The inverse re-enters exact captured values, but their provenance remains
        # the original policy/default choice, not a new user-entered choice.
        origins = expected_snapshot['profile']['origins']
        plan.data['semantic']['profile']['origins'] = origins
        profile_row = plan.data['pending']['purchase_profiles'][0]
        profile = json.loads(profile_row['profile_snapshot'])
        profile['origins'] = origins
        profile_row['profile_snapshot'] = d.encoded(profile)
        from bookflow.company.bill_facts import BillProfile
        plan.preview = plan.preview.model_copy(update={'revision': plan.preview.revision.model_copy(
            update={'profile': BillProfile.model_validate(profile)})})
        if plan.data.get('semantic') != expected_snapshot:
            fail('E_APPROVAL_STALE', 'The native inverse cannot restore the exact captured prior facts.', changed_fields=bills._changes(expected_snapshot, plan.data.get('semantic')))
    plan.data['_approval_seal'] = _SEAL
    return plan


def get(s, table, identity):
    row = s.company.conn.execute(sa.select(table).where(table.c.id == identity)).mappings().first()
    if row is None:
        fail('E_RECORD_NOT_FOUND', 'Record not found.', record_type=table.name, selector=identity)
    return dict(row)


def version(row, expected):
    if row['version'] != expected:
        fail('E_VERSION_CONFLICT', 'This record changed; refresh before acting.', expected_version=expected, current_version=row['version'])


def state(row, allowed):
    if row['state'] not in allowed:
        fail('E_APPROVAL_STATE', 'This act is unavailable in the current state.', state=row['state'], allowed_states=allowed)


def reason(ctx):
    if not ctx.reason or not ctx.reason.strip():
        fail('E_REASON_REQUIRED', 'Give a reason for this approval act.')


def request_for(s, draft):
    row = s.company.conn.execute(sa.select(c.approval_requests).where(
        c.approval_requests.c.draft_id == draft).order_by(c.approval_requests.c.draft_version.desc()).limit(1)).mappings().first()
    return dict(row) if row else None


def request_output(s, row):
    events = [dict(e, data=json.loads(e['data'])) for e in s.company.conn.execute(sa.select(c.approval_events).where(
        c.approval_events.c.draft_id == row['draft_id']).order_by(c.approval_events.c.sequence)).mappings()]
    return m.RequestOutput(**{key: row[key] for key in m.RequestOutput.model_fields if key not in ('steps', 'events')},
                           steps=json.loads(row['steps']), events=events)


def record_transition(s, ctx, draft, new_state, action, data=None):
    data = data or {}
    at = clock.now_iso()
    after = dict(draft, state=new_state, version=draft['version'] + 1,
                 updated_at=at, updated_by=s.actor.id, updated_via=ctx.interface.value)
    if 'original_version' in data:
        after['original_version'] = data['original_version']
    s.company.conn.execute(c.purchase_drafts.update().where(c.purchase_drafts.c.id == draft['id']).values(**after))
    s.company.conn.execute(c.purchase_draft_revisions.insert().values(id=new_id(), draft_id=draft['id'],
        version=after['version'], state=new_state, payload=after['payload'], preview=after['preview'], actor=s.actor.id,
        principal=principal(s, ctx), interface=ctx.interface.value, reason=ctx.reason, created_at=at))
    req = request_for(s, draft['id'])
    if action in ('commit', 'commit_reversed'):
        commit = get(s, c.approval_commits, data['commit_id'])
        req = get(s, c.approval_requests, commit['request_id']) if commit['request_id'] else None
    seq = (s.company.conn.execute(sa.select(sa.func.max(c.approval_events.c.sequence)).where(
        c.approval_events.c.draft_id == draft['id'])).scalar() or 0) + 1
    s.company.conn.execute(c.approval_events.insert().values(id=new_id(), draft_id=draft['id'],
        request_id=req['id'] if req else None, sequence=seq, action=action, actor=s.actor.id,
        principal=principal(s, ctx), interface=ctx.interface.value, reason=ctx.reason, created_at=at, data=d.encoded(data)))
    if action == 'commit_reversed' and req:
        s.company.conn.execute(c.approval_requests.update().where(c.approval_requests.c.id == req['id']).values(
            state='approved', version=req['version'] + 1))
    return after


def active_policy(s):
    row = s.company.conn.execute(sa.select(c.approval_policy_revisions).join(c.approval_policies,
        c.approval_policies.c.id == c.approval_policy_revisions.c.policy_id).where(
        c.approval_policies.c.target == 'bill').order_by(c.approval_policy_revisions.c.publication_sequence.desc()).limit(1)).mappings().first()
    if row is None:
        fail('E_APPROVAL_POLICY', 'Publish a bill policy with eligible reviewers first.')
    return dict(row)


def member_ids(s, members):
    from bookflow.hub import schema as h
    # Inputs are user IDs of members, never names from an unrelated organization.
    scopes = sa.or_(sa.and_(h.memberships.c.scope_type == 'company', h.memberships.c.scope_id == s.company_id),
                    sa.and_(h.memberships.c.scope_type == 'organization', h.memberships.c.scope_id == s.company_row['organization_id']))
    valid = set(s.hub.conn.execute(sa.select(h.memberships.c.user_id).join(h.users, h.users.c.id == h.memberships.c.user_id).where(
        scopes, h.memberships.c.revoked_at.is_(None), h.users.c.active.is_(True))).scalars())
    if not members or any(x not in valid for x in members):
        fail('E_APPROVAL_POLICY', 'Choose active members of this company for the Review step.', field='eligible_members')
    return sorted(set(members))


def require_settings_admin(s, ctx):
    from bookflow.hub import schema as h
    for identity in {s.actor.id, principal(s, ctx)}:
        user = s.hub.conn.execute(sa.select(h.users).where(h.users.c.id == identity, h.users.c.active.is_(True))).mappings().first()
        scopes = sa.or_(sa.and_(h.memberships.c.scope_type == 'company', h.memberships.c.scope_id == s.company_id),
                        sa.and_(h.memberships.c.scope_type == 'organization', h.memberships.c.scope_id == s.company_row['organization_id']))
        admin = s.hub.conn.execute(sa.select(h.memberships.c.id).where(h.memberships.c.user_id == identity,
            h.memberships.c.revoked_at.is_(None), h.memberships.c.role.in_(['admin','owner']), scopes)).first()
        if not user or (not admin and not (user['kind'] == 'human' and user['hub_admin'])):
            fail('E_PERMISSION', 'Publishing approval settings requires company admin for actor and principal plus approval.manage.')


def policy_output(row):
    return m.PolicyOutput(**{k: row[k] for k in ('id', 'version', 'target', 'state')}, **json.loads(row['definition']))


def command_name(inp):
    return {m.SubmitInput:'purchase-draft submit', m.WithdrawInput:'purchase-draft withdraw',
        m.CommitInput:'purchase-draft commit', m.DecideInput:'approval decide',
        m.PolicyCreateInput:'approval-policy create', m.PolicyUpdateInput:'approval-policy update',
        m.PolicyPublishInput:'approval-policy publish'}[type(inp)]


def recover(inp, ctx, s):
    command = command_name(inp)
    if isinstance(inp, m.CommitInput):
        access.require_resource(s, 'ledger.post', 'standard')
        capabilities.require_company(s, 'bill post', 'ledger.post')
    if isinstance(inp, m.PolicyPublishInput):
        require_settings_admin(s, ctx)
    row = s.company.conn.execute(sa.select(c.approval_operations).where(c.approval_operations.c.command == command,
        c.approval_operations.c.actor == s.actor.id, c.approval_operations.c.operation_key == inp.operation_key)).mappings().first()
    if row is None:
        return None
    # Current assignment/self policy remains a prerequisite to decision replay.
    if isinstance(inp, m.DecideInput):
        decision_authority(s, ctx, get(s, c.approval_requests, inp.request))
    if row['request'] != d.request(inp, ctx):
        fail('E_IDEMPOTENCY_MISMATCH', 'This operation key belongs to different input.')
    from bookflow.core.registry import get as command_get
    return MatchedRecovery(command_get(command).output_model.model_validate_json(row['result']))


def decision_authority(s, ctx, req):
    access.require_resource(s, 'approval.decide', 'standard')
    access.require_resource(s, 'ledger.read', 'member')
    capabilities.require_company(s, 'approval decide', 'approval.decide')
    step = json.loads(req['steps'])[req['current_step']]
    member_ids(s, [s.actor.id])
    if s.actor.id not in step['eligible_members']:
        fail('E_APPROVAL_ASSIGNEE', 'Only an assigned member may decide this Review step.', step_id='review')
    if not step['allow_self'] and ({s.actor.id, principal(s, ctx)} & set(json.loads(req['requesters']))):
        fail('E_APPROVAL_SELF', 'Creator, submitter and their bound principals cannot review their own submission.')


def prepare(s, ctx, inp):
    reason(ctx)
    replay = recover(inp, ctx, s)
    if replay:
        return Plan(replay.output, dict(replay=True))
    name = command_name(inp)
    data = dict(input=inp, name=name)
    if name.startswith('approval-policy '):
        before = get(s, c.approval_policies, inp.policy) if not isinstance(inp, m.PolicyCreateInput) or isinstance(inp, m.PolicyUpdateInput) else None
        if before:
            version(before, inp.expected_version)
        at = clock.now_iso()
        if isinstance(inp, m.PolicyPublishInput):
            if not inp.confirm:
                fail('E_APPROVAL_POLICY', 'Preview this exact policy, then publish with confirm=true.')
            require_settings_admin(s, ctx)
            definition = json.loads(before['definition'])
            member_ids(s, definition['eligible_members'])
            after = dict(before, state='published')
        else:
            definition = dict(eligible_members=member_ids(s, inp.eligible_members), allow_self=inp.allow_self, enabled=inp.enabled)
            after = dict(before or {}, target='bill', state='draft', definition=d.encoded(definition))
        if not before:
            after.update(id=new_id(), created_at=at, created_by=s.actor.id, created_via=ctx.interface.value)
        after.update(version=before['version']+1 if before else 1, updated_at=at, updated_by=s.actor.id, updated_via=ctx.interface.value)
        data.update(before=before, after=after)
        return Plan(policy_output(after), data)
    if isinstance(inp, m.DecideInput):
        req = get(s, c.approval_requests, inp.request)
        version(req, inp.expected_version)
        state(req, ['pending'])
        decision_authority(s, ctx, req)
        draft = d.row(s, req['draft_id'])
        state(draft, ['pending'])
        after = dict(req, state='approved' if inp.decision == 'approve' else 'rejected', version=req['version']+1)
        data.update(draft=draft, req=req, after_request=after)
        return Plan(request_output(s, after), data)
    draft = d.row(s, inp.draft)
    version(draft, inp.expected_version)
    data['draft'] = draft
    req = request_for(s, draft['id'])
    data['req'] = req
    if isinstance(inp, m.SubmitInput):
        state(draft, ['draft'])
        if not enabled(s):
            fail('E_APPROVAL_POLICY', 'Bill approvals are off; commit the draft directly.')
        policy = active_policy(s)
        definition = json.loads(policy['definition'])
        member_ids(s, definition['eligible_members'])
        from bookflow.company.approval_bill_payload import calculate
        payload, snapshot, native = calculate(s, ctx, json.loads(draft['payload']), draft['operation'], draft['original_id'], draft['original_version'])
        if draft.get('snapshot') and json.loads(draft['snapshot']) != snapshot:
            fail('E_APPROVAL_STALE', 'The draft calculation changed; update it before submitting.', changed_fields=['snapshot'])
        creator = s.company.conn.execute(sa.select(c.purchase_draft_revisions).where(
            c.purchase_draft_revisions.c.draft_id == draft['id']).order_by(c.purchase_draft_revisions.c.version).limit(1)).mappings().one()
        requesters = sorted({x for x in (draft['created_by'], creator['principal'], s.actor.id, principal(s, ctx)) if x})
        after = dict(id=new_id(), draft_id=draft['id'], draft_version=draft['version'], policy_revision_id=policy['id'],
            steps=d.encoded([dict(id='review', label='Review', eligible_members=definition['eligible_members'], allow_self=definition['allow_self'])]),
            requesters=d.encoded(requesters), state='pending', version=1, current_step=0, created_at=clock.now_iso())
        data.update(after_request=after, snapshot=snapshot)
        return Plan(request_output(s, after), data)
    if isinstance(inp, m.WithdrawInput):
        state(draft, ['pending', 'approved'])
        version(req, inp.expected_request_version)
        if draft['state'] == 'approved' and not inp.confirm:
            fail('E_APPROVAL_STATE', 'Withdrawing approved work requires confirm=true after preview.')
        data['after_request'] = dict(req, state='withdrawn', version=req['version']+1)
        return Plan(d.output(s, dict(draft, state='draft', version=draft['version']+1)), data)
    state(draft, ['draft', 'approved'])
    if req and req['state'] in ('pending', 'approved'):
        version(req, inp.expected_request_version)
        state(req, ['approved'])
    if draft['state'] == 'draft' and enabled(s):
        fail('E_APPROVAL_REQUIRED', 'Submit this draft and obtain approval before committing.')
    if draft['state'] == 'draft':
        req = None
        data['req'] = None
    from bookflow.company.approval_bill_payload import calculate
    payload, snapshot, native = calculate(s, ctx, json.loads(draft['payload']), draft['operation'], draft['original_id'], draft['original_version'])
    if draft.get('snapshot') and json.loads(draft['snapshot']) != snapshot:
        fail('E_APPROVAL_STALE', 'The approved calculation or sources changed; withdraw, update and resubmit.', changed_fields=['snapshot'])
    native = native_write(s, ctx, native.data['input'], native.data['operation'], draft['id'])
    if not native.data.get('changed'):
        fail('E_APPROVAL_STATE', 'This correction has no native effect.')
    output = m.CommitOutput(draft_id=draft['id'], version=draft['version']+1, request_id=req['id'] if req else None,
        document_id=native.preview.id, document_version=native.preview.version, batch_id=new_id(),
        total_minor_units=native.preview.total_minor_units, currency=native.preview.currency)
    data.update(native=native)
    return Plan(output, data)


def apply(plan, ctx, s):
    if plan.data.get('replay'):
        return Applied(plan.preview, [], 'Recovered approval operation')
    fresh = prepare(s, ctx, plan.data['input'])
    if fresh.data.get('replay'):
        return Applied(fresh.preview, [], 'Recovered approval operation')
    data, inp = fresh.data, fresh.data['input']
    name = data['name']
    touched = []
    if name.startswith('approval-policy '):
        after, before = data['after'], data['before']
        if before:
            s.company.conn.execute(c.approval_policies.update().where(c.approval_policies.c.id == after['id']).values(**after))
        else:
            s.company.conn.execute(c.approval_policies.insert().values(**after))
        if isinstance(inp, m.PolicyPublishInput):
            publication_sequence = (s.company.conn.execute(sa.select(sa.func.max(c.approval_policy_revisions.c.publication_sequence))).scalar() or 0) + 1
            s.company.conn.execute(c.approval_policy_revisions.insert().values(id=new_id(), publication_sequence=publication_sequence, policy_id=after['id'],
                version=after['version'], definition=after['definition'], created_at=clock.now_iso()))
            s.company.conn.execute(c.company_info.update().values(bill_approvals_enabled=json.loads(after['definition'])['enabled']))
        touched.append(Touched('approval_policy',after['id'],'update' if before else 'create', before['version'] if before else None,after['version'],after,before,db='company'))
        result = fresh.preview
    else:
        draft, req = data['draft'], data['req']
        if isinstance(inp, m.SubmitInput):
            s.company.conn.execute(c.approval_requests.insert().values(**data['after_request']))
            draft = dict(draft, snapshot=d.encoded(data['snapshot']))
            after = record_transition(s,ctx,draft,'pending','submit',dict(request_id=data['after_request']['id'], snapshot=data['snapshot']))
            result = request_output(s,data['after_request'])
        elif isinstance(inp, (m.DecideInput, m.WithdrawInput)):
            after_req = data['after_request']
            s.company.conn.execute(c.approval_requests.update().where(c.approval_requests.c.id==req['id']).values(**after_req))
            new_state = after_req['state'] if isinstance(inp,m.DecideInput) else 'draft'
            after = record_transition(s,ctx,draft,new_state,inp.decision if isinstance(inp,m.DecideInput) else 'withdraw',
                dict(comment=inp.comment) if isinstance(inp,m.DecideInput) else {})
            result = request_output(s,after_req) if isinstance(inp,m.DecideInput) else d.output(s,after)
        else:
            from bookflow.company import bills, batches
            native = data['native']
            before_payload = None
            if draft['operation'] == 'correct':
                from bookflow.company.approval_bill_payload import prior_payload
                before_payload = d.encoded(prior_payload(s, native.data['before']))
            native_applied = bills.persist_prepared(native,ctx,s,command_name=name)
            touched.extend(native_applied.touched)
            batch = batches._insert_batch(s,ctx,kind='approval-commit',label='Approved bill commit',scope=draft['id'],ordering='record',source_type='purchase_draft',source_id=draft['id'])
            commit_id = new_id()
            s.company.conn.execute(c.approval_commits.insert().values(id=commit_id,request_id=req['id'] if req else None,
                draft_id=draft['id'],draft_version=draft['version']+1,document_id=native.preview.id,document_version=native.preview.version,
                operation=draft['operation'],batch_id=batch,before_payload=before_payload,before_snapshot=d.encoded(bills._saved_semantic(s,native.data['old_revision'])) if draft['operation']=='correct' else None,created_at=clock.now_iso()))
            batches.record_effect(s,batch,record_type='approval_commit',record_id=commit_id,action='created',version_after=1,event_id=native.data['event'])
            if req:
                s.company.conn.execute(c.approval_requests.update().where(c.approval_requests.c.id==req['id']).values(state='committed',version=req['version']+1))
            after = record_transition(s,ctx,draft,'committed','commit',dict(commit_id=commit_id,batch_id=batch,document_id=native.preview.id))
            result = fresh.preview.model_copy(update={'batch_id':batch})
        touched.append(Touched('purchase_draft',draft['id'],'update',draft['version'],after['version'],after,draft,db='company'))
    s.company.conn.execute(c.approval_operations.insert().values(id=new_id(),command=name,actor=s.actor.id,
        operation_key=inp.operation_key,request=d.request(inp,ctx),result=result.model_dump_json()))
    return Applied(result,touched,'Recorded '+name)
