"""Complete private mandatory/seed suffix derivation and exact manifest checks."""
from pydantic import model_validator
from typing import Literal
from bookflow.company import reconciliation_commands_models as m
from bookflow.company.reconciliation_preparation import require,chain,claimed,_statement,certify,opening
from bookflow.company import reconciliation_adapters as adapters
from bookflow.company.reconciliation_proof import prove

class OpeningAction(m.Model):
    account_id: m.ID
    opening_id: m.ID
    mode: Literal['replace','invalidate']
    replacement_draft_revision_id: m.ID|None=None
    @model_validator(mode='after')
    def replacement(self):
        if (self.mode=='replace')!=(self.replacement_draft_revision_id is not None):raise ValueError('opening replacement shape')
        return self

class Closure(m.Model):
    opening_ids: tuple[m.ID,...]
    certificate_ids: tuple[m.ID,...]
    account_versions: dict[m.ID,m.Count]


def derive(s,seeds, *, before_source,authority_transactions):
    require({r['id'] for r in before_source.rows['transactions']}<=set(authority_transactions),'E_PERMISSION')
    old_history,old=adapters.enumerate_graph(before_source);new_history,new=adapters.enumerate_graph(s.source)
    for account in {v.account_id for v in old+new}:
        prove(before_source,old_history,old,account,'9999-12-31')
        prove(s.source,new_history,new,account,'9999-12-31')
    old={v.ref:v for v in old};new={v.ref:v for v in new}
    changed={ref for ref in old.keys()|new.keys() if old.get(ref)!=new.get(ref)}
    keys={adapters.StatementEffectRef(producer=v['producer'],transaction_id=v['transaction_id'],component_id=v['deposit_key_id'] or v['commercial_line_id'],role=v['role']):v['id'] for v in s.rows['keys']}
    affected={keys[ref] for ref in changed if ref in keys};claims=claimed(s);starts={};openings=set()
    accounts={v['account_id']:v for v in s.rows['accounts']}
    def add(account,certificate=None,opening_id=None,date=None):
        require(account in accounts,'E_RECONCILIATION_MANIFEST')
        existing=chain(s,account)
        if opening_id:
            require(accounts[account]['opening_id']==opening_id,'E_RECONCILIATION_CHAIN_STALE');openings.add(opening_id);index=0
        elif certificate:
            require(any(v['id']==certificate for v in existing),'E_RECONCILIATION_CHAIN_STALE')
            index=next(i for i,v in enumerate(existing) if v['id']==certificate)
        else:index=next((i for i,v in enumerate(existing) if v['statement_date']>=date),len(existing))
        starts[account]=min(starts.get(account,len(existing)),index)
    for key in affected&claims.keys():
        claim=claims[key];add(claim['account_id'],certificate=claim['certificate_id'],opening_id=claim['opening_id'])
    # New/current effects also guard destination histories, including effects
    # without an old claim. Date moves do not resurrect an earlier version.
    for ref in changed:
        v=new.get(ref)
        if v and v.active and v.account_id in accounts:
            a=accounts[v.account_id];o=s.by('openings').get(a['opening_id'])
            if o and v.effective_date<=o['opening_date']:add(v.account_id,opening_id=o['id'])
            else:add(v.account_id,date=v.effective_date)
    seen=set()
    for seed in seeds:
        signature=seed.model_dump_json();require(signature not in seen,'E_RECONCILIATION_MANIFEST');seen.add(signature)
        add(seed.account_id,certificate=seed.certificate_id,opening_id=seed.opening_id,date=seed.date)
    certs=tuple(v['id'] for a in sorted(starts) for v in chain(s,a)[starts[a]:])
    return Closure(opening_ids=tuple(sorted(openings)),certificate_ids=certs,account_versions={a:accounts[a]['version'] for a in sorted(starts)})


def validate_manifest(s,closure,manifest,opening_actions, *, replacement_drafts):
    require(manifest.account_versions==closure.account_versions,'E_RECONCILIATION_CHAIN_STALE')
    for v in manifest.certificates:
        require(v.account_id in closure.account_versions,'E_RECONCILIATION_MANIFEST')
        if v.certificate_id:require(s.by('certificates')[v.certificate_id]['account_id']==v.account_id,'E_RECONCILIATION_MANIFEST')
    actual=[v.certificate_id for v in manifest.certificates if v.mode!='insert']
    require(len(actual)==len(set(actual)) and set(actual)==set(closure.certificate_ids),'E_RECONCILIATION_MANIFEST')
    require(len(opening_actions)==len(closure.opening_ids) and {v.opening_id for v in opening_actions}==set(closure.opening_ids),'E_RECONCILIATION_MANIFEST')
    # Every insert is explicitly seeded; not an extra unrequested account/date.
    insert_dates={(v.account_id,v.date) for v in manifest.seeds if v.kind=='insert'}
    actual_inserts=[];new_openings={};invalidated=set()
    for action in opening_actions:
        old=s.by('openings')[action.opening_id];require(old['account_id']==action.account_id,'E_RECONCILIATION_MANIFEST')
        if action.mode=='invalidate':invalidated.add(action.account_id)
        else:
            draft=replacement_drafts[action.replacement_draft_revision_id]
            require(draft.account_id==action.account_id and draft.repair_of_opening_id==action.opening_id,'E_RECONCILIATION_MANIFEST')
            opening(s,draft);new_openings[action.account_id]=draft
    releases={k for k,v in claimed(s).items() if v['certificate_id'] in closure.certificate_ids or v['opening_id'] in closure.opening_ids}
    selected=set();results=[]
    for account in closure.account_versions:
        old_chain=chain(s,account);targets=[v for v in manifest.certificates if v.account_id==account]
        def date_of(v):return replacement_drafts[v.replacement_draft_revision_id].header.statement_date if v.replacement_draft_revision_id else s.by('certificates')[v.certificate_id]['statement_date']
        targets.sort(key=date_of)
        first_date=min((date_of(v) for v in targets),default='9999-12-31')
        predecessor=next((v for v in reversed(old_chain) if v['statement_date']<first_date and v['id'] not in closure.certificate_ids),None)
        for target in targets:
            if target.mode=='invalidate':invalidated.add(account);continue
            require(account not in invalidated,'E_RECONCILIATION_MANIFEST')
            draft=replacement_drafts[target.replacement_draft_revision_id]
            require(draft.account_id==account and draft.current_revision_id==target.replacement_draft_revision_id,'E_RECONCILIATION_MANIFEST')
            if target.mode=='insert':actual_inserts.append((account,draft.header.statement_date))
            else:require(draft.repair_of_certificate_id==target.certificate_id,'E_RECONCILIATION_MANIFEST')
            # The FK names the retained predecessor used as an admission
            # guard. Newly generated certificate IDs do not exist at staging.
            # Rebuilt-chain adjacency below follows the derived dated order.
            retained=(s.by('certificates')[target.certificate_id]['previous_certificate_id'] if target.certificate_id else
                next((v['id'] for v in reversed(old_chain) if v['statement_date']<draft.header.statement_date),None))
            require(target.predecessor_id==retained,'E_RECONCILIATION_MANIFEST')
            keys={v.key_id for v in draft.selections};require(not keys&selected,'E_RECONCILIATION_MEMBERSHIP_CONFLICT');selected.update(keys)
            result=certify(_statement(s,draft,predecessor=predecessor,released_keys=releases,replacement_opening=new_openings.get(account)))
            results.append(result)
            predecessor=dict(id=target.certificate_id,ending_balance=result.ending_balance,statement_date=draft.header.statement_date)
    require(len(actual_inserts)==len(set(actual_inserts)) and set(actual_inserts)==insert_dates,'E_RECONCILIATION_MANIFEST')
    return tuple(results)


def undo_manifest(s,account,head,expected_version):
    a=next(v for v in s.rows['accounts'] if v['account_id']==account)
    require(a['version']==expected_version,'E_RECONCILIATION_CHAIN_STALE')
    require(a['head_certificate_id']==head,'E_RECONCILIATION_DEPENDENCY')
    c=s.by('certificates')[head]
    return m.Manifest(seeds=(m.SeedTarget(account_id=account,kind='statement',certificate_id=head),),certificates=(m.CertificateTarget(account_id=account,certificate_id=head,predecessor_id=c['previous_certificate_id'],mode='invalidate'),),account_versions={account:expected_version})
