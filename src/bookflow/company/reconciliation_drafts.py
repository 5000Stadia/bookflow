"""Immutable private draft/proposal preparation; never persists or posts."""
import json
from bookflow.company import reconciliation_commands_models as m
from bookflow.company.reconciliation_storage_validation import Header, Evidence, Preferences
from bookflow.company.reconciliation_preparation import (
    require,read_admission,groups,group_fingerprint,whole_selection,account_population,claimed,fingerprint,
)

DEFAULT_PREFERENCES=Preferences(format=1,columns=['date','number','payee','amount','status'],sort='date',descending=False,hide_after_date=True,view='as_certified')

def load(s,identity, *, authority_transactions):
    read_admission(s,authority_transactions)
    require(identity in s.by('drafts'),'E_RECORD_NOT_FOUND')
    h=s.by('drafts')[identity];r=s.by('draft_revisions')[h['current_revision_id']]
    first=min((v for v in s.rows['draft_revisions'] if v['draft_id']==identity),key=lambda v:v['revision_number'])
    event=next(v for v in s.rows['events'] if v['audit_event_id']==first['audit_event_id'])
    original=json.loads(s.by('operations')[event['operation_id']]['original_request_snapshot'])['document']
    refs=m.TypeAdapter(tuple[m.EvidenceRef,...]).validate_json(json.dumps(original.get('original_input',{}).get('references',[])))
    return m.Draft(**{k:h[k] for k in ('id','account_id','kind','version','current_revision_id','state','terminal_operation_id')},
        evidence_references=refs,header=Header.model_validate_json(r['header_snapshot']),
        **{k:r[k] for k in ('base_chain_version','base_opening_id','base_head_id','repair_of_opening_id','repair_of_certificate_id')},
        selections=tuple(m.Selection(key_id=v['key_id'],version_id=v['version_id'],action=v['action']) for v in sorted(s.rows['draft_members'],key=lambda v:v['ordinal']) if v['revision_id']==r['id']),
        proposal_revision_ids=tuple(v['proposal_revision_id'] for v in s.rows['draft_proposals'] if v['draft_revision_id']==r['id']))

def start(s,inp, *, identity,revision_id,opening_draft=None):
    state=next((v for v in s.rows['accounts'] if v['account_id']==inp.account),None)
    opening=isinstance(inp,m.OpeningStart)
    cutoff=inp.opening_date if opening else inp.statement_date
    if opening:
        from bookflow.company.reconciliation_preparation import validate_evidence
        validate_evidence(s,inp.references)
    account_population(s,inp.account,cutoff)
    if opening:require(state is None or state['opening_id'] is None,'E_RECONCILIATION_DEPENDENCY')
    else:
        if inp.opening_id:
            require(state is not None and state['opening_id']==inp.opening_id,'E_RECONCILIATION_CHAIN_STALE')
        else:
            require(opening_draft is not None and opening_draft.id==inp.opening_draft_id and opening_draft.account_id==inp.account and opening_draft.kind=='opening' and opening_draft.state=='open','E_RECONCILIATION_DRAFT_STATE')
    header=Header(format=1,opening_date=cutoff if opening else None,statement_date=None if opening else cutoff,
        entered_balance=inp.entered_balance if opening else inp.ending_balance,
        evidence=inp.evidence if opening else Evidence(format=1,statement_reference=None,entered_text=None),preferences=DEFAULT_PREFERENCES)
    return m.Draft(id=identity,account_id=inp.account,kind='opening' if opening else 'statement',version=1,current_revision_id=revision_id,state='open',header=header,
        evidence_references=inp.references if opening else (),base_chain_version=state['version'] if state else 0,base_opening_id=state['opening_id'] if state else None,base_head_id=state['head_certificate_id'] if state else None)

def editable(s,draft,expected, *, attempt_id=None):
    require(draft.version==expected,'E_VERSION_CONFLICT')
    require(draft.state=='open','E_RECONCILIATION_DRAFT_STATE')
    require(not any(v['draft_id']==draft.id and v['attempt_id']!=attempt_id for v in s.rows['attempt_active']),'E_RECONCILIATION_ATTEMPT_STATE')

def revised(draft,revision_id,**changes):
    require(revision_id!=draft.current_revision_id,'E_RECONCILIATION_MANIFEST')
    return m.Draft.model_validate(dict(draft.model_dump(),version=draft.version+1,current_revision_id=revision_id,**changes))

def update(s,draft,inp, *, revision_id):
    editable(s,draft,inp.expected_version);require(inp.draft==draft.id,'E_RECONCILIATION_MANIFEST')
    fields=inp.model_fields_set-{'operation_key','draft','expected_version'}
    require(not (draft.kind=='opening' and 'statement_date' in fields) and not (draft.kind!='opening' and 'opening_date' in fields),'E_RECONCILIATION_DATE')
    patch={k:getattr(inp,k) for k in fields}
    header=Header.model_validate(dict(draft.header.model_dump(),**patch))
    if header==draft.header:return draft
    # Date/header changes deliberately retain saved versions and marks.
    return revised(draft,revision_id,header=header)

def mark(s,draft,inp, *, revision_id):
    editable(s,draft,inp.expected_version);require(inp.draft==draft.id,'E_RECONCILIATION_MANIFEST')
    current,_=account_population(s,draft.account_id,draft.header.statement_date or draft.header.opening_date)
    available=groups(current);selections={v.key_id:v for v in draft.selections};seen=set()
    for entry in inp.entries:
        key=json.dumps(entry.movement.model_dump(mode='json'),sort_keys=True,separators=(',',':'),ensure_ascii=False)
        group=available.get(key)
        require(group is not None and group_fingerprint(group)==entry.group_fingerprint,'E_RECONCILIATION_SELECTION_STALE')
        require(key not in seen,'E_RECONCILIATION_MANIFEST');seen.add(key)
        require(entry.action in (('covered','outstanding') if draft.kind=='opening' else ('mark','unmark')),'E_RECONCILIATION_MANIFEST')
        if entry.action!='unmark':
            require(all(v['effective_date']<=(draft.header.statement_date or draft.header.opening_date) for v in group),'E_RECONCILIATION_DATE')
            require(not {v['key_id'] for v in group}&set(claimed(s)),'E_RECONCILIATION_MEMBERSHIP_CONFLICT')
        for v in group:
            require(v['key_id'] not in selections or selections[v['key_id']].version_id==v['id'],'E_RECONCILIATION_SELECTION_STALE')
            if entry.action=='unmark':selections.pop(v['key_id'],None)
            else:selections[v['key_id']]=m.Selection(key_id=v['key_id'],version_id=v['id'],action=entry.action)
    return revised(draft,revision_id,selections=tuple(selections[k] for k in sorted(selections)))

def accept_current(s,draft,inp, *, revision_id):
    editable(s,draft,inp.expected_version)
    require(inp.draft==draft.id,'E_RECONCILIATION_MANIFEST')
    saved=groups(whole_selection(s,draft,current=False));by_hash={group_fingerprint(v):v for v in saved.values()}
    choices={v.key_id:v for v in draft.selections};seen=set()
    for entry in inp.entries:
        require(entry.saved_group_fingerprint in by_hash and entry.saved_group_fingerprint not in seen,'E_RECONCILIATION_SELECTION_STALE');seen.add(entry.saved_group_fingerprint)
        old=by_hash[entry.saved_group_fingerprint];keys={v['key_id'] for v in old};action=choices[next(iter(keys))].action
        now=[v for group in groups(v for v in s.current.values() if v['active'] and v['account_id']==draft.account_id).values() if keys & {v['key_id'] for v in group} for v in group]
        actual={group_fingerprint(v):v for v in groups(now).values()}
        require(len(entry.current)==len(actual) and {v.group_fingerprint for v in entry.current}==set(actual),'E_RECONCILIATION_SELECTION_STALE')
        for ref in entry.current:
            require(all(json.loads(v['movement_snapshot'])==ref.movement.model_dump(mode='json') for v in actual[ref.group_fingerprint]),'E_RECONCILIATION_SELECTION_STALE')
        for k in keys:choices.pop(k)
        for v in now:choices[v['key_id']]=m.Selection(key_id=v['key_id'],version_id=v['id'],action=action)
    result=revised(draft,revision_id,selections=tuple(choices[k] for k in sorted(choices)))
    whole_selection(s,result,current=False)
    return result

def lifecycle(s,draft,action, *, expected_version,revision_id,operation_id):
    editable(s,draft,expected_version)
    require(action in ('leave','resume','cancel','consume'),'E_RECONCILIATION_DRAFT_STATE')
    if action in ('leave','resume'):return draft
    if action=='consume':
        from bookflow.company.reconciliation_preparation import certify,opening,statement
        certify(opening(s,draft) if draft.kind=='opening' else statement(s,draft))
    return revised(draft,revision_id,state='canceled' if action=='cancel' else 'consumed',terminal_operation_id=operation_id)

def proposal(s,draft,inp, *, identity,revision_id,previous=None):
    editable(s,draft,inp.expected_version);require(inp.draft==draft.id,'E_RECONCILIATION_MANIFEST')
    if previous:
        require(previous.id==inp.proposal_id and previous.draft_id==draft.id and previous.version==inp.expected_proposal_version,'E_VERSION_CONFLICT')
        require(previous.consumed_journal_id is None,'E_RECONCILIATION_DRAFT_STATE')
        require(previous.role==inp.role,'E_RECONCILIATION_MANIFEST')
    else:require(inp.proposal_id is None,'E_RECONCILIATION_MANIFEST')
    a=s.source.accounts[inp.input.offset_account_id]
    require(a['active'] and a['id']!=draft.account_id and a['currency']==inp.input.currency==s.source.accounts[draft.account_id]['currency'],'E_RECONCILIATION_UNSUPPORTED')
    require(inp.input.date<=(draft.header.statement_date or draft.header.opening_date),'E_RECONCILIATION_DATE')
    return m.Proposal(id=previous.id if previous else identity,draft_id=draft.id,version=previous.version+1 if previous else 1,revision_id=revision_id,role=inp.role,input=inp.input)

def remove_proposal(s,draft,inp,previous, *, revision_id):
    editable(s,draft,inp.expected_version)
    require(inp.draft==draft.id and previous.draft_id==draft.id and inp.proposal_id==previous.id and inp.expected_proposal_version==previous.version,'E_VERSION_CONFLICT')
    require(previous.consumed_journal_id is None,'E_RECONCILIATION_DRAFT_STATE')
    return revised(draft,revision_id,proposal_revision_ids=tuple(v for v in draft.proposal_revision_ids if v!=previous.revision_id))

def journal_input(s,draft,proposal):
    """Return the actual owning input; ordinary planner still owns admission.

    This is not a posting Plan, account/period/permission approval or effect.
    """
    from bookflow.company.journal_models import JournalPostInput
    require(proposal.draft_id==draft.id and proposal.consumed_journal_id is None,'E_RECONCILIATION_DRAFT_STATE')
    p=proposal.input
    require(p.offset_account_id!=draft.account_id and s.source.accounts[p.offset_account_id]['active'] and s.source.accounts[p.offset_account_id]['currency']==p.currency==s.source.accounts[draft.account_id]['currency'],'E_RECONCILIATION_UNSUPPORTED')
    card=s.source.accounts[draft.account_id]['type']=='credit_card'
    signed=(-p.amount_minor_units if proposal.role=='charge' else p.amount_minor_units)
    if proposal.role=='force_adjustment' and card:signed=-signed
    money=dict(minor_units=abs(signed),currency=p.currency)
    return JournalPostInput(date=p.date,memo=p.memo,**({'number':p.number} if p.number else {}),lines=[
        dict(account=draft.account_id,side='debit' if signed>0 else 'credit',amount=money,class_id=p.class_id),
        dict(account=p.offset_account_id,side='credit' if signed>0 else 'debit',amount=money,class_id=p.class_id)])


def set_proposal(s,draft,inp, *, identity,proposal_revision_id,draft_revision_id,previous=None):
    saved=proposal(s,draft,inp,identity=identity,revision_id=proposal_revision_id,previous=previous)
    references=set(draft.proposal_revision_ids)
    if previous:
        require(previous.revision_id in references,'E_RECONCILIATION_DRAFT_STATE')
        references.remove(previous.revision_id)
    references.add(saved.revision_id)
    return revised(draft,draft_revision_id,proposal_revision_ids=tuple(sorted(references))),saved
