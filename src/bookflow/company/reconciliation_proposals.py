"""Exact owning journal Plans in nonposting reconciliation previews.

Plans are retained for the future aggregate persister, never dispatched/applied
here. No plan factory, generic callback, independent money writer or bypass.
"""
from dataclasses import dataclass
from typing import Literal
from bookflow.core.registry import Plan
from bookflow.hub import access
from bookflow.company import reconciliation_adapters as adapters
from bookflow.company import reconciliation_commands_models as m
from bookflow.company.reconciliation_drafts import journal_input
from bookflow.company.reconciliation_preparation import statement,require,bounded,adapter_errors

class Preview(m.Model):
    contract: Literal['reconciliation.private.v1']='reconciliation.private.v1'
    original_difference: m.Units
    final: m.Totals
    generated_transaction_ids: tuple[m.ID,...]

@dataclass(frozen=True)
class Prepared:
    output: Preview
    # Exact already prepared and independently validated ordinary source Plans.
    plans: tuple[Plan,...]


def adjustment(draft,result,inp, *, identity,revision_id,currency):
    require(inp.date<=draft.header.statement_date,'E_RECONCILIATION_DATE')
    if result.difference==0:return None
    return m.Proposal(id=identity,draft_id=draft.id,version=1,revision_id=revision_id,role='force_adjustment',
        input=m.ProposalInput(format=1,date=inp.date,amount_minor_units=result.difference,currency=currency,offset_account_id=inp.offset_account_id,class_id=inp.class_id,memo=None,number=None,reason=inp.reason))


def preview(session,ctx,s,draft,proposals,plans, *, opening_draft=None):
    """Current source admission precedes disclosure and exact Plan comparison."""
    access.require_resource(session,'ledger.post','standard')
    adapters.authority(session,s.authority_transactions)
    with adapter_errors():
        current=adapters.graph(session,[v['id'] for v in s.source.rows['transactions']])
    require(current.rows==s.source.rows,'E_PREVIEW_STALE')
    revisions=[v.revision_id for v in proposals]
    require(len(revisions)==len(set(revisions)) and len(draft.proposal_revision_ids)==len(set(draft.proposal_revision_ids)) and set(revisions)==set(draft.proposal_revision_ids),'E_RECONCILIATION_MANIFEST')
    require(all(v.draft_id==draft.id for v in proposals),'E_RECONCILIATION_MANIFEST')
    require(len(proposals)==len(plans) and len({v.id for v in proposals})==len(proposals),'E_RECONCILIATION_MANIFEST')
    require(len([v for v in proposals if v.role=='force_adjustment'])<=1,'E_RECONCILIATION_MANIFEST')
    for account in {draft.account_id}|{v.input.offset_account_id for v in proposals}:
        require(current.accounts[account]==s.source.accounts[account],'E_PREVIEW_STALE')
    from bookflow.company import journals
    journals.open_dates(session,[v.input.date for v in proposals])
    base=statement(s,draft,opening_draft=opening_draft)
    positive=base.positive_sum;negative=base.negative_sum;pc=base.positive_count;nc=base.negative_count
    identities=[];adjustment_amount=None
    for proposal,plan in zip(proposals,plans):
        require(type(plan) is Plan and plan.data.get('before') is None,'E_RECONCILIATION_MANIFEST')
        expected=journal_input(s,draft,proposal)
        require(type(plan.data.get('input')) is type(expected) and plan.data['input']==expected,'E_RECONCILIATION_MANIFEST')
        with adapter_errors():
            projected=adapters.prepare_prospective(session,ctx,plan)
        require(not isinstance(projected.changes,adapters.UnsupportedPopulation),'E_RECONCILIATION_UNSUPPORTED')
        require(projected.aggregate is plan,'E_RECONCILIATION_MANIFEST')
        identity=plan.data['header']['id'];require(identity not in identities,'E_RECONCILIATION_MANIFEST');identities.append(identity)
        effects=[v for v in projected.changes.after if v.active and v.account_id==draft.account_id]
        require(bool(effects) and all(v.effective_date<=draft.header.statement_date for v in effects),'E_RECONCILIATION_DATE')
        amount=sum(v.statement_amount for v in effects)
        if proposal.role=='force_adjustment':adjustment_amount=amount
        if amount>0:positive+=amount;pc+=len({v.movement_key for v in effects})
        else:negative+=amount;nc+=len({v.movement_key for v in effects})
    if adjustment_amount is not None:
        require(adjustment_amount==base.ending_balance-(base.beginning_balance+positive+negative-adjustment_amount),'E_RECONCILIATION_DIFFERENCE')
    selected=positive+negative;cleared=base.beginning_balance+selected
    amounts=dict(positive_sum=positive,negative_sum=negative,selected_sum=selected,beginning_balance=base.beginning_balance,ending_balance=base.ending_balance,cleared_balance=cleared,difference=base.ending_balance-cleared)
    result=m.Totals(positive_count=pc,negative_count=nc,**{k:bounded(v) for k,v in amounts.items()},decimal_units={k:str(v) for k,v in amounts.items()})
    return Prepared(Preview(original_difference=base.difference,final=result,generated_transaction_ids=tuple(identities)),tuple(plans))
