"""Shared one-step bill approval commands for every adapter."""
import sqlalchemy as sa
from bookflow.company import approvals as a, approval_models as m, purchase_draft_models as dm, schema as c
from bookflow.core.registry import Plan, command


def recover(inp,ctx,s):
    return a.recover(inp,ctx,s)


def plan_submit(inp,ctx,s): return a.prepare(s,ctx,inp)
def plan_withdraw(inp,ctx,s): return a.prepare(s,ctx,inp)
def plan_commit(inp,ctx,s): return a.prepare(s,ctx,inp)
def plan_decide(inp,ctx,s): return a.prepare(s,ctx,inp)
def plan_policy_create(inp,ctx,s): return a.prepare(s,ctx,inp)
def plan_policy_update(inp,ctx,s): return a.prepare(s,ctx,inp)
def plan_policy_publish(inp,ctx,s): return a.prepare(s,ctx,inp)
def plan_show(inp,ctx,s): return Plan(a.request_output(s,a.get(s,c.approval_requests,inp.request)))
def plan_history(inp,ctx,s): return plan_show(inp,ctx,s)
def plan_policy_show(inp,ctx,s): return Plan(a.policy_output(a.get(s,c.approval_policies,inp.policy)))
def plan_policy_list(inp,ctx,s):
    query = sa.select(c.approval_policies).order_by(c.approval_policies.c.id)
    if inp.cursor:
        query = query.where(c.approval_policies.c.id > inp.cursor)
    rows = list(s.company.conn.execute(query.limit(inp.limit+1)).mappings())
    return Plan(m.PolicyListOutput(items=[a.policy_output(r) for r in rows[:inp.limit]],
        next_cursor=rows[inp.limit-1]['id'] if len(rows)>inp.limit else None))


def register(name, model, output, planner, capability, role, description, write=False):
    cmd = command(name,scope='company',description=description,input_model=model,output_model=output,
        writes={'company'} if write else set(),required_role=role,capability=capability,
        accepts_idempotency_key=write,
        error_codes=['E_APPROVAL_REQUIRED','E_APPROVAL_STALE','E_APPROVAL_STATE','E_APPROVAL_SELF',
                     'E_APPROVAL_ASSIGNEE','E_APPROVAL_POLICY','E_VERSION_CONFLICT','E_REASON_REQUIRED',
                     'E_IDEMPOTENCY_MISMATCH','E_RECORD_NOT_FOUND','E_PERIOD_CLOSED','E_HAS_APPLICATIONS'])(planner)
    cmd.resource_requirements = (('ledger.read','member'),) + ((('ledger.post','standard'),) if name=='purchase-draft commit' else ())
    if write:
        cmd.permanent_recovery = recover
        cmd.applier(a.apply)
    return cmd


approval_submit=register('purchase-draft submit',m.SubmitInput,m.RequestOutput,plan_submit,'approval.prepare','standard',
    'Submit the exact saved bill revision to the published one-step Review policy. Freezes commercial and source facts; posts nothing. Use operation_key and reason.',True)
approval_withdraw=register('purchase-draft withdraw',m.WithdrawInput,dm.DraftOutput,plan_withdraw,'approval.prepare','standard',
    'Withdraw pending or approved work using exact draft and request versions. Retains decisions and returns to draft. Approved withdrawal requires confirm=true after preview.',True)
approval_commit=register('purchase-draft commit',m.CommitInput,m.CommitOutput,plan_commit,'approval.prepare','standard',
    'Post an approved bill or commit a saved draft with approvals off. Revalidates exact source and commercial evidence and current native posting rights; creates one reversible batch. Supply request version when approved.',True)
approval_decide=register('approval decide',m.DecideInput,m.RequestOutput,plan_decide,'approval.decide','standard',
    'Approve or return the assigned Review step at its exact request version. Reason required. Self-review is refused by actor and principal unless captured policy allows it. Approval posts nothing.',True)
approval_show=register('approval show',m.RequestInput,m.RequestOutput,plan_show,'approval.read','standard','Show captured approval steps and attributed history.')
approval_history=register('approval history',m.RequestInput,m.RequestOutput,plan_history,'approval.read','standard','Read immutable submission, decision, withdrawal, commit and reversal evidence.')
approval_policy_create=register('approval-policy create',m.PolicyCreateInput,m.PolicyOutput,plan_policy_create,'approval.manage','standard',
    'Save a simple bill Review policy with explicit eligible company member user IDs. Does not enable approvals until published. allow_self defaults false.',True)
approval_policy_update=register('approval-policy update',m.PolicyUpdateInput,m.PolicyOutput,plan_policy_update,'approval.manage','standard',
    'Save a replacement simple policy at its exact version. Published evidence and pending requests retain their captured policy.',True)
approval_policy_publish=register('approval-policy publish',m.PolicyPublishInput,m.PolicyOutput,plan_policy_publish,'approval.manage','standard',
    'Publish the exact policy revision and its bill approval switch. Requires company admin plus approval.manage and confirm=true after preview. Disabling affects future submissions only.',True)
approval_policy_show=register('approval-policy show',m.PolicyInput,m.PolicyOutput,plan_policy_show,'approval.read','standard','Show a simple bill policy and its current saved revision.')
approval_policy_list=register('approval-policy list',m.PolicyListInput,m.PolicyListOutput,plan_policy_list,'approval.read','standard','Page bill policies in stable ID order.')
