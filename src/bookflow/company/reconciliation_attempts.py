"""Immutable bounded chunks, complete seal and nonposting attempt transitions."""
from typing import Literal
from pydantic import Field
from bookflow.company import reconciliation_commands_models as m
from bookflow.company.reconciliation_preparation import require,whole_selection
from bookflow.company.reconciliation_storage_validation import digest,ChunkReceipt
from bookflow.company.reconciliation_drafts import editable,revised

class SavedChunk(m.Model):
    index: m.Count
    items: tuple[m.AttemptItem,...]=Field(min_length=1,max_length=200)
    receipt: ChunkReceipt

class Attempt(m.Model):
    id: m.ID
    draft_id: m.ID
    base_revision_id: m.ID
    version: m.Version
    state: Literal['uploading','sealed','applied','aborted','superseded']
    attempt_generation: str=Field(min_length=1)
    declared_count: m.Count
    intent_hash: m.Fingerprint
    chunks: tuple[SavedChunk,...]=()

    @property
    def items(self):return tuple(v for c in self.chunks for v in c.items)

def payload(items):return [v.model_dump(mode='json') for v in items]

def begin(s,draft,inp, *, identity):
    editable(s,draft,inp.expected_version)
    require(inp.draft==draft.id and inp.base_revision_id==draft.current_revision_id,'E_RECONCILIATION_CHAIN_STALE')
    return Attempt(id=identity,draft_id=draft.id,base_revision_id=draft.current_revision_id,version=1,state='uploading',attempt_generation=inp.attempt_generation,declared_count=inp.declared_count,intent_hash=inp.intent_hash)

def upload(attempt,inp):
    require(inp.attempt==attempt.id,'E_RECONCILIATION_ATTEMPT_STATE')
    body=dict(format=1,items=payload(inp.items));hash_=digest(body)
    if inp.chunk_index<len(attempt.chunks):
        old=attempt.chunks[inp.chunk_index]
        require(old.receipt.request_hash==hash_,'E_RECONCILIATION_OPERATION_KEY_REUSED')
        return attempt,old.receipt
    require(attempt.state=='uploading' and inp.chunk_index==len(attempt.chunks),'E_RECONCILIATION_ATTEMPT_STATE')
    require(len(attempt.items)+len(inp.items)<=attempt.declared_count,'E_RECONCILIATION_MANIFEST')
    receipt=ChunkReceipt(format=1,chunk_index=inp.chunk_index,first_ordinal=len(attempt.items),count=len(inp.items),request_hash=hash_)
    chunk=SavedChunk(index=inp.chunk_index,items=inp.items,receipt=receipt)
    return Attempt.model_validate(dict(attempt.model_dump(),version=attempt.version+1,chunks=attempt.chunks+(chunk,))),receipt

def semantic_key(item):
    p=item.payload
    if item.kind=='member':return ('member',p.key_id)
    if item.kind=='proposal':return ('proposal',p.proposal_id)
    if item.kind=='seed':return ('seed',p.account_id,p.kind,p.opening_id,p.certificate_id,p.date)
    return ('certificate',p.account_id,p.certificate_id,p.predecessor_id)

def validate_items(s,draft,items, *, current):
    seen=set();versions=s.versions
    for item in items:
        key=semantic_key(item);require(key not in seen,'E_RECONCILIATION_MANIFEST');seen.add(key)
        p=item.payload
        if item.kind=='member':
            require(p.draft_id==draft.id and p.account_id==draft.account_id,'E_RECONCILIATION_MANIFEST')
            for identity in (p.saved_version_id,p.current_version_id):
                if identity is not None:require(identity in versions and versions[identity]['key_id']==p.key_id and versions[identity]['account_id']==p.account_id,'E_RECONCILIATION_MANIFEST')
            expected=p.current_version_id if p.action=='accept_current' else p.saved_version_id
            if current and p.action not in ('unmark',):
                actual=s.current.get(p.key_id)
                if expected is None:require(actual is None or not actual['active'] or actual['account_id']!=draft.account_id,'E_RECONCILIATION_SELECTION_STALE')
                else:require(actual is not None and actual['id']==expected and actual['active'],'E_RECONCILIATION_SELECTION_STALE')
        elif item.kind=='proposal':
            require(p.draft_id==draft.id,'E_RECONCILIATION_MANIFEST')
            saved=s.by('proposal_revisions').get(p.proposal_revision_id)
            require(saved is not None and saved['proposal_id']==p.proposal_id and saved['draft_id']==p.draft_id,'E_RECONCILIATION_MANIFEST')
            require(not any(v['proposal_id']==p.proposal_id for v in s.rows['proposal_consumptions']),'E_RECONCILIATION_DRAFT_STATE')
        else:
            require(p.account_id in s.source.accounts,'E_RECONCILIATION_MANIFEST')
            for field,table in (('opening_id','openings'),('certificate_id','certificates'),('predecessor_id','certificates'),('replacement_draft_revision_id','draft_revisions')):
                identity=getattr(p,field,None)
                if identity:
                    row=s.by(table).get(identity)
                    require(row is not None and row['account_id']==p.account_id,'E_RECONCILIATION_MANIFEST')

def seal(s,draft,attempt, *, expected_version):
    require(attempt.version==expected_version,'E_VERSION_CONFLICT')
    require(attempt.state=='uploading' and attempt.draft_id==draft.id and attempt.base_revision_id==draft.current_revision_id and draft.state=='open','E_RECONCILIATION_ATTEMPT_STATE')
    require([c.index for c in attempt.chunks]==list(range(len(attempt.chunks))),'E_RECONCILIATION_MANIFEST')
    offset=0
    for c in attempt.chunks:
        require(c.receipt.first_ordinal==offset and c.receipt.count==len(c.items) and c.receipt.chunk_index==c.index and c.receipt.request_hash==digest(dict(format=1,items=payload(c.items))),'E_RECONCILIATION_MANIFEST');offset+=len(c.items)
    require(len(attempt.items)==attempt.declared_count and digest(payload(attempt.items))==attempt.intent_hash,'E_RECONCILIATION_MANIFEST')
    validate_items(s,draft,attempt.items,current=False)
    return Attempt.model_validate(dict(attempt.model_dump(),version=attempt.version+1,state='sealed'))

def apply(s,draft,attempt, *, expected_version,expected_draft_version,revision_id):
    require(attempt.version==expected_version,'E_VERSION_CONFLICT')
    editable(s,draft,expected_draft_version,attempt_id=attempt.id)
    require(attempt.state=='sealed' and attempt.base_revision_id==draft.current_revision_id and attempt.draft_id==draft.id,'E_RECONCILIATION_ATTEMPT_STATE')
    validate_items(s,draft,attempt.items,current=True)
    members={v.key_id:v for v in draft.selections};proposals=set(draft.proposal_revision_ids)
    for item in attempt.items:
        p=item.payload
        if item.kind=='member':
            if p.action=='unmark' or (p.action=='accept_current' and p.current_version_id is None):members.pop(p.key_id,None)
            elif p.action=='accept_current':
                require(p.key_id in members and members[p.key_id].version_id==p.saved_version_id,'E_RECONCILIATION_SELECTION_STALE')
                members[p.key_id]=m.Selection(key_id=p.key_id,version_id=p.current_version_id,action=members[p.key_id].action)
            else:members[p.key_id]=m.Selection(key_id=p.key_id,version_id=p.saved_version_id,action=p.action)
        elif item.kind=='proposal':proposals.add(p.proposal_revision_id)
    result=revised(draft,revision_id,selections=tuple(members[k] for k in sorted(members)),proposal_revision_ids=tuple(sorted(proposals)))
    whole_selection(s,result)
    # Seed/certificate leaves remain the exact immutable manifest, validated by
    # amendment preparation before financial apply; this transition posts nothing.
    return result,Attempt.model_validate(dict(attempt.model_dump(),state='applied',version=attempt.version+1))

def abort(attempt, *, expected_version,superseded=False):
    require(attempt.version==expected_version,'E_VERSION_CONFLICT')
    require(attempt.state in ('uploading','sealed'),'E_RECONCILIATION_ATTEMPT_STATE')
    return Attempt.model_validate(dict(attempt.model_dump(),state='superseded' if superseded else 'aborted',version=attempt.version+1))


def items(attempt, *, limit=50,offset=0,expected_fingerprint=None):
    require(type(limit) is int and 1<=limit<=200 and type(offset) is int and 0<=offset<=len(attempt.items),'E_QUERY_STALE')
    token=digest(dict(attempt=attempt.id,version=attempt.version,state=attempt.state,items=payload(attempt.items)))
    require(expected_fingerprint in (None,token),'E_QUERY_STALE')
    return m.AttemptPage(items=attempt.items[offset:offset+limit],count=len(attempt.items),next_offset=offset+limit if offset+limit<len(attempt.items) else None,fingerprint=token)
