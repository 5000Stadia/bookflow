"""Strict private aggregate validation for nonactivating reconciliation storage.

The caller supplies immutable rows and an already authorized source Graph. This
module neither queries a company nor repairs/materializes it. N permits empty
storage without a source graph; nonempty source evidence must be proved in full.
"""
from collections import Counter, defaultdict
from datetime import date
from hashlib import sha256
import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from bookflow.company.reconciliation_models import MovementKey
from bookflow.company.reconciliation_schema import PREFIX


class InvalidStorage(ValueError):
    """Private safe diagnostic: fixed rule name, never document IDs or counts."""


def require(condition, rule):
    if not condition:
        raise InvalidStorage(rule)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return sha256(canonical(value).encode()).hexdigest()


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True)

    @model_validator(mode='after')
    def exact_fields(self):
        for name in type(self).model_fields:
            value=getattr(self,name)
            if value is not None and name in ('date','cutoff','opening_date','statement_date'):
                if date.fromisoformat(value).isoformat()!=value:raise ValueError('date_format')
            if value is not None and name in ('entered_balance','amount_minor_units','signed_gl_total'):
                if type(value) is not int or not -(2**63)<=value<2**63:raise ValueError('integer_range')
        return self


class Display(Strict):
    format: Literal[1]
    number: str
    memo: str | None
    payees: list[str]
    issuer: dict
    custom_fields: dict


class Provenance(Strict):
    format: Literal[1]
    document_line_ids: list[str]
    rows: list[str]


class Population(Strict):
    format: Literal[1]
    account_id: str
    currency: str
    cutoff: str
    version_ids: list[str]
    signed_gl_total: int
    source_fingerprint: str


class Evidence(Strict):
    format: Literal[1]
    statement_reference: str | None
    entered_text: str | None


class Preferences(Strict):
    format: Literal[1]
    columns: list[Literal['date','number','payee','memo','amount','type','status']]
    sort: Literal['date','number','payee','amount','type']
    descending: bool
    hide_after_date: bool
    view: Literal['as_certified','current_discrepancy']


class Header(Strict):
    format: Literal[1]
    opening_date: str | None
    statement_date: str | None
    entered_balance: int | None
    evidence: Evidence
    preferences: Preferences


class ProposalInput(Strict):
    format: Literal[1]
    date: str
    amount_minor_units: int
    currency: str
    offset_account_id: str
    class_id: str | None
    memo: str | None
    number: str | None
    reason: str | None


class AccountFacts(Strict):
    format: Literal[1]
    account_id: str
    version: int = Field(gt=0)
    type: str
    currency: str
    active: bool


class Collection(Strict):
    count: int = Field(ge=0)
    hash: str


class Envelope(Strict):
    """Storage envelope, not a public command input/financial admission model."""
    format: Literal[1]
    # Exact command-owned original JSON is retained, never reinterpreted here.
    document: dict
    collections: dict[Literal['request','effects','targets','generated'], Collection]


class RequestEnvelope(Envelope):
    canonical_intent: dict


class Chunk(Strict):
    format: Literal[1]
    items: list[dict] = Field(max_length=200)


class ChunkReceipt(Strict):
    format: Literal[1]
    chunk_index: int = Field(ge=0)
    first_ordinal: int = Field(ge=0)
    count: int = Field(ge=0, le=200)
    request_hash: str


MODELS = {
    ('effect_versions','movement_snapshot'): MovementKey,
    ('effect_versions','display_snapshot'): Display,
    ('effect_versions','provenance_snapshot'): Provenance,
    ('openings','evidence_snapshot'): Evidence,
    ('openings','authorized_source_snapshot'): Population,
    ('certificates','captured_source_snapshot'): Population,
    ('draft_revisions','header_snapshot'): Header,
    ('proposal_revisions','input_snapshot'): ProposalInput,
    ('proposal_revisions','expected_account_facts'): AccountFacts,
    ('operations','original_request_snapshot'): RequestEnvelope,
    ('operations','original_effect_snapshot'): Envelope,
    ('attempt_chunks','original_chunk'): Chunk,
    ('attempt_chunks','receipt'): ChunkReceipt,
    ('report_presets','parameters_snapshot'): Preferences,
    ('report_preset_revisions','parameters_snapshot'): Preferences,
}


def _decode(value):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, 'duplicate_json_key')
            result[key] = value
        return result
    try:
        return json.loads(value, object_pairs_hook=pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(InvalidStorage('nonfinite_json')))
    except (ValueError, TypeError) as exc:
        raise InvalidStorage('invalid_json') from exc


def validate(rows, *, source=None, captured_graphs=None, referenced_rows=None):
    """Validate a complete N-table snapshot; return no partial decoded output.

    Source is an explicit R0 Graph, not a connection. Whole source coverage is
    required only for nonempty effect storage (future A migration/owner caller).
    Original command documents remain opaque evidence in strict envelopes; their
    future public action models/authorization and execution are not implemented.
    """
    from bookflow.company.schema import metadata
    tables = {n.removeprefix(PREFIX):t for n,t in metadata.tables.items() if n.startswith(PREFIX)}
    require(set(rows) == set(tables), 'table_inventory')
    r = {n:[dict(v) for v in values] for n,values in rows.items()}
    for name, values in r.items():
        t = tables[name]
        seen = set()
        for constraint in t.constraints:
            if constraint.__class__.__name__=='UniqueConstraint':
                tuples=[tuple(v[c.name] for c in constraint.columns) for v in values]
                tuples=[v for v in tuples if None not in v]
                require(len(set(tuples))==len(tuples),'duplicate_unique_key')
        for value in values:
            require(set(value) == set(t.c.keys()), 'row_columns')
            pk = tuple(value[c.name] for c in t.primary_key)
            require(pk not in seen, 'duplicate_primary_key'); seen.add(pk)
            for c in t.c:
                v = value[c.name]; kind = c.info['kind']
                if v is None:
                    require(c.nullable, 'null_required'); continue
                if kind in ('int','positive','count','bool'):
                    require(type(v) is int and -(2**63) <= v < 2**63, 'integer_range')
                    require(kind != 'positive' or v > 0, 'positive_integer')
                    require(kind != 'count' or v >= 0, 'nonnegative_integer')
                    require(kind != 'bool' or v in (0,1), 'boolean_integer')
                else:
                    require(type(v) is str, 'text_type')
                    if kind == 'id': require(re.fullmatch('[0-9A-HJKMNP-TV-Z]{26}',v) is not None, 'identity_format')
                    if kind == 'date':
                        try: require(date.fromisoformat(v).isoformat()==v, 'date_format')
                        except ValueError as exc: raise InvalidStorage('date_format') from exc
                    if kind == 'json':
                        decoded = _decode(v); require(type(decoded) is dict, 'json_object')
                        if (name,c.name) in MODELS:
                            if 'format' in MODELS[name,c.name].model_fields:require(type(decoded.get('format')) is int,'snapshot_format')
                            try: MODELS[name,c.name].model_validate(decoded,strict=True)
                            except ValueError as exc: raise InvalidStorage('snapshot_format') from exc
                        value[c.name] = decoded
    def by(name,field='id'): return {v[field]:v for v in r[name]}
    def matching(name,field,key): return [v for v in r[name] if v[field]==key]
    def get(name,key,field='id'):
        found=by(name,field).get(key); require(found is not None,'missing_owner'); return found
    # Prove every new-table FK explicitly even when a migration has FK OFF.
    referenced_rows = referenced_rows or {}
    for name,t in tables.items():
        for constraint in t.foreign_key_constraints:
            remote=constraint.referred_table.name
            for value in r[name]:
                key=tuple(value[e.parent.name] for e in constraint.elements)
                if any(v is None for v in key): continue
                candidates = r[remote.removeprefix(PREFIX)] if remote.startswith(PREFIX) else referenced_rows.get(remote)
                require(candidates is not None,'referenced_rows_required')
                require(any(tuple(v[e.column.name] for e in constraint.elements)==key for v in candidates),'foreign_owner')
    choices={
        ('opening_members','classification'):('covered','outstanding'),
        ('drafts','kind'):('opening','statement','amendment'),
        ('drafts','state'):('open','consumed','canceled'),
        ('draft_members','action'):('mark','covered','outstanding'),
        ('proposals','role'):('charge','earned_credit','force_adjustment'),
        ('operation_items','kind'):('request','effects','targets','generated'),
        ('attempt_members','action'):('mark','unmark','covered','outstanding','accept_current'),
        ('attempt_proposals','action'):('add','remove'),
    }
    for (name,field),allowed in choices.items():
        require(all(v[field] in allowed for v in r[name]),'closed_discriminator')
    for draft in r['drafts']:
        require((draft['state']=='open')==(draft['terminal_operation_id'] is None),'draft_terminal_receipt')
    for v in r['effect_versions']:
        require(v['format_version']==1 and v['active']==int(v['signed_debit']!=0),'effect_format')
        require(v['account_type'] in ('bank','credit_card'),'effect_account_type')
        field='deposit_link_id' if v['producer']=='deposit' else 'commercial_link_id'
        other='commercial_link_id' if v['producer']=='deposit' else 'deposit_link_id'
        require(v[field]==v['id'] and v[other] is None,'effect_subtype_pointer')
    for table,kind in [('commercial_versions',False),('deposit_versions',True)]:
        for v in r[table]:
            require((get('effect_versions',v['id'])['producer']=='deposit')==kind,'extra_effect_subtype')
    for v in r['attempt_items']:
        require(v['kind'] in ('member','seed','certificate','proposal'),'attempt_kind')
        for kind in ('member','seed','certificate','proposal'):
            require(v[kind+'_ordinal']==(v['ordinal'] if kind==v['kind'] else None),'attempt_subtype_pointer')
    for kind in ('member','seed','certificate','proposal'):
        for v in r['attempt_'+kind+'s']:
            require(any(i['attempt_id']==v['attempt_id'] and i['ordinal']==v['ordinal'] and i['kind']==kind for i in r['attempt_items']),'extra_attempt_subtype')
    for c in r['claims']:
        require((c['opening_id'] is None)!=(c['certificate_id'] is None),'claim_owner_shape')
    for key in r['keys']:
        choices={'journal_entry':('entered',),'payment':('cash',),'sales_receipt':('control','net'),'invoice':('net',),'deposit':('main_bank','cash_back','additional')}
        require(key['producer'] in choices and key['role'] in choices[key['producer']],'producer_role')
        require((key['deposit_key_id'] is not None)==(key['producer']=='deposit') and (key['commercial_line_id'] is not None)==(key['producer']!='deposit'),'key_subtype')
    for event in r['events']:
        require(event['schema_version']==1 and event['kind'] in ('draft_change','opening_certify','finish','amend','invalidate','undo','proposal_change','bulk_stage','bulk_terminal','report_preset_change'),'event_format')
        op=get('operations',event['operation_id'])
        require(event['audit_event_id']==op['audit_event_id'],'event_operation_audit')
    if r['accounts'] or r['openings'] or r['certificates']:
        company=referenced_rows.get('company_info',[])
        require(len(company)==1,'company_currency_proof')
        home=company[0]['home_currency']
        for name in ('accounts','openings','certificates'):
            for owner in r[name]:
                account=next((a for a in referenced_rows['accounts'] if a['id']==owner['account_id']),None)
                require(account is not None and account['type'] in ('bank','credit_card') and owner['currency']==account['currency']==home,'statement_currency_unsupported')
                if 'convention' in owner:require(owner['convention']==('card_debt' if account['type']=='credit_card' else 'bank'),'account_convention')
    for evidence in r['opening_evidence']:
        require((evidence['transaction_id'] is not None and evidence['attachment_id'] is None and evidence['attachment_link_id'] is None) or (evidence['transaction_id'] is None and evidence['attachment_id'] is not None and evidence['attachment_link_id'] is not None),'opening_evidence_shape')
        if evidence['attachment_id']:
            require(any(v['id']==evidence['attachment_link_id'] and v['attachment_id']==evidence['attachment_id'] for v in referenced_rows['attachment_links']),'attachment_owner')
    keys=by('keys'); versions=by('effect_versions')
    transitions = {}
    if versions:
        require(source is not None,'source_proof_required')
        from bookflow.company.reconciliation_adapters import enumerate_graph
        from bookflow.company.reconciliation_proof import prove
        history,current=enumerate_graph(source)
        for account in {v.account_id for v in history}: prove(source,history,current,account,'9999-12-31')
        expected={(v.ref.producer,v.ref.transaction_id,v.ref.role,v.ref.component_id,v.version_id):v for v in history}
        actual={}
        for v in versions.values():
            k=keys[v['key_id']]
            ref=(k['producer'],k['transaction_id'],k['role'],k['deposit_key_id'] or k['commercial_line_id'],v['source_version'])
            require(ref not in actual,'duplicate_source_version'); actual[ref]=v
            require(ref in expected,'unknown_source_version'); e=expected[ref]
            for field,attr in [('revision_id','revision_id'),('business_batch_id','business_batch_id'),('transition_batch_id','transition_batch_id'),('source_audit_event_id','audit_event_id'),('account_id','account_id'),('account_type','account_type'),('currency','currency'),('effective_date','effective_date'),('signed_debit','signed_debit'),('active','active')]:
                require(v[field]==getattr(e,attr),'source_facts')
            require(v['movement_snapshot']==e.movement_key.model_dump(mode='json'),'movement_facts')
            display=v['display_snapshot']; rev=source.by_id('transaction_revisions')[e.revision_id]
            require(display==dict(format=1,number=e.number,memo=e.memo,payees=list(e.payees),issuer=_decode(rev['issuer_snapshot']),custom_fields=_decode(rev['custom_fields_snapshot'])),'display_facts')
            require(v['provenance_snapshot']==dict(format=1,document_line_ids=list(e.document_line_ids),rows=list(e.provenance)),'provenance_facts')
            for name,field,expected_ids in [('effect_legs','posting_line_id',e.posting_line_ids),('effect_sources','source_id',e.source_ids)]:
                require(Counter(x[field] for x in matching(name,'version_id',v['id']))==Counter(expected_ids),'physical_coverage')
            if e.ref.producer=='deposit':
                link=get('deposit_versions',v['id']); require((link['bank_key_id'],link['bank_version_id'])==(e.ref.component_id,e.version_id),'deposit_link')
            else: require(get('commercial_versions',v['id'])['line_id']==e.ref.component_id,'commercial_link')
        require(set(actual)==set(expected),'source_history_coverage')
        # R0 enumeration orders each stable key's immutable business versions;
        # never infer predecessor from ULIDs, dates, amounts or insertion order.
        previous = {}
        for effect in history:
            ref=(effect.ref.producer,effect.ref.transaction_id,effect.ref.role,effect.ref.component_id,effect.version_id)
            stored=actual[ref]; key=stored['key_id']; audit=effect.audit_event_id
            transition=(key,previous.get(key),stored['id'],audit)
            require((audit,key) not in transitions,'source_event_cardinality')
            transitions[audit,key]=transition
            previous[key]=stored['id']

        heads={v['key_id']:v['version_id'] for v in r['effect_heads']}
        expected_heads={next(k['id'] for k in keys.values() if (k['producer'],k['transaction_id'],k['role'],k['deposit_key_id'] or k['commercial_line_id'])==(v.ref.producer,v.ref.transaction_id,v.ref.role,v.ref.component_id)): actual[v.ref.producer,v.ref.transaction_id,v.ref.role,v.ref.component_id,v.version_id]['id'] for v in current}
        require(heads==expected_heads,'source_heads')
    else:
        require(not keys and not r['effect_heads'],'empty_source_links')
    def members(name,owner_field,owner):
        values=matching(name,owner_field,owner)
        require(sorted(v['ordinal'] for v in values)==list(range(len(values))),'member_ordinals')
        grouped=defaultdict(set)
        for m in values:
            v=versions[m['version_id']]
            grouped[canonical(v['movement_snapshot'])].add(m.get('classification',m.get('action')))
        require(all(len(v)==1 for v in grouped.values()),'split_movement')
        selected={m['version_id'] for m in values}
        for m in values:
            v=versions[m['version_id']]
            whole={e['id'] for e in versions.values() if e['active'] and e['movement_snapshot']==v['movement_snapshot']}
            require(whole<=selected,'incomplete_movement')
        return values
    captured_graphs = captured_graphs or {}
    expected_captures={v['id'] for name in ('openings','certificates') for v in r[name]}
    require(set(captured_graphs)==expected_captures,'capture_proof_inventory')
    def capture(owner,pop,opening=False):
        from bookflow.company.reconciliation_adapters import enumerate_graph
        from bookflow.company.reconciliation_proof import prove
        graph=captured_graphs[owner['id']]
        history,current=enumerate_graph(graph)
        total,gl=prove(graph,history,current,owner['account_id'],pop['cutoff'])
        values=[v for v in current if v.account_id==owner['account_id'] and (not opening or v.active and v.effective_date<=pop['cutoff'])]
        identity={ (keys[v['key_id']]['producer'],keys[v['key_id']]['transaction_id'],keys[v['key_id']]['role'],keys[v['key_id']]['deposit_key_id'] or keys[v['key_id']]['commercial_line_id'],v['source_version']):v['id'] for v in versions.values() }
        expected=[identity.get((v.ref.producer,v.ref.transaction_id,v.ref.role,v.ref.component_id,v.version_id)) for v in values]
        require(None not in expected and Counter(expected)==Counter(pop['version_ids']),'capture_population_completeness')
        require(pop['signed_gl_total']==total==gl,'capture_gl')
        require(pop['source_fingerprint']==digest(sorted((v.model_dump(mode='json') for v in values),key=canonical)),'capture_fingerprint')
    def amount(m):
        v=versions[m['version_id']]; return -v['signed_debit'] if v['account_type']=='credit_card' else v['signed_debit']
    for o in r['openings']:
        ms=members('opening_members','opening_id',o['id']); pop=o['authorized_source_snapshot']
        capture(o,pop,opening=True)
        require(get('drafts',get('draft_revisions',o['origin_draft_revision_id'])['draft_id'])['kind']=='opening','opening_draft_kind')
        require(pop['account_id']==o['account_id'] and pop['currency']==o['currency'] and pop['cutoff']==o['opening_date'],'opening_snapshot_owner')
        require(Counter(pop['version_ids'])==Counter(m['version_id'] for m in ms),'opening_population')
        require(all(versions[m['version_id']]['active'] and versions[m['version_id']]['effective_date']<=o['opening_date'] for m in ms),'opening_eligibility')
        require(sum(amount(m) for m in ms if m['classification']=='covered')==o['balance'],'opening_balance')
        require(sum(versions[m['version_id']]['signed_debit'] for m in ms)==pop['signed_gl_total'],'opening_gl')
    for c in r['certificates']:
        ms=members('certificate_members','certificate_id',c['id']); pop=c['captured_source_snapshot']
        capture(c,pop)
        require(get('drafts',get('draft_revisions',c['origin_draft_revision_id'])['draft_id'])['kind'] in ('statement','amendment'),'certificate_draft_kind')
        require(pop['account_id']==c['account_id'] and pop['currency']==c['currency'] and pop['cutoff']==c['statement_date'],'certificate_snapshot_owner')
        require(Counter(pop['version_ids'])==Counter(m['version_id'] for m in ms),'certificate_population')
        covered={m['key_id'] for m in matching('opening_members','opening_id',c['opening_id']) if m['classification']=='covered'}
        prior=set(); previous=c['previous_certificate_id']; visited=set()
        while previous:
            require(previous not in visited,'chain_cycle');visited.add(previous)
            prior.update(m['key_id'] for m in matching('certificate_members','certificate_id',previous) if m['classification']=='selected')
            previous=get('certificates',previous)['previous_certificate_id']
        for m in ms:
            expected='opening_covered' if m['key_id'] in covered else 'prior_cleared' if m['key_id'] in prior else None
            require(m['classification']==expected if expected else m['classification'] in ('selected','outstanding'),'certificate_classification')
        selected=[m for m in ms if m['classification']=='selected']
        for m in ms:
            v=versions[m['version_id']]; eligible=v['active'] and v['effective_date']<=c['statement_date']
            require(m['eligible_at_cutoff']==eligible,'certificate_eligibility')
            require(m['classification']!='selected' or eligible,'selected_future')
        total=sum(amount(m) for m in selected)
        require(total==c['selected_sum'] and c['beginning_balance']+total==c['ending_balance'] and c['final_difference']==0,'certificate_balance')
        for side,pred in [('positive',lambda a:a>0),('negative',lambda a:a<0)]:
            values=[amount(m) for m in selected if pred(amount(m))]
            require(len({canonical(versions[m['version_id']]['movement_snapshot']) for m in selected if pred(amount(m))})==c[side+'_count'] and sum(values)==c[side+'_sum'],'certificate_sides')
        previous=get('certificates',c['previous_certificate_id']) if c['previous_certificate_id'] else get('openings',c['opening_id'])
        require(c['beginning_balance']==previous.get('ending_balance',previous.get('balance')),'chain_adjacency')
        require(c['statement_date']>previous.get('statement_date',previous.get('opening_date')),'chain_dates')
        require(c['currency']==previous['currency'],'chain_currency')
    for name,parent in [('draft_revisions','draft_id'),('proposal_revisions','proposal_id')]:
        for v in r[name]:
            previous=v['previous_revision_id']
            require((previous is None)==(v['revision_number']==1),'revision_origin')
            if previous: require(get(name,previous)['revision_number']+1==v['revision_number'],'revision_sequence')
    for d in r['draft_revisions']:
        members('draft_members','revision_id',d['id'])
        header=d['header_snapshot']; kind=get('drafts',d['draft_id'])['kind']
        require((kind=='opening')==(header['opening_date'] is not None),'draft_header_kind')
        require(kind=='opening' or header['statement_date'] is not None,'draft_statement_date')
    for p in r['proposal_revisions']:
        values=p['input_snapshot']; require(all(p[k]==v for k,v in values.items() if k!='format'),'proposal_input')
        require(p['expected_account_facts']['account_id']==p['offset_account_id'],'proposal_account_capture')
        require(get('proposals',p['proposal_id'])['role']=='force_adjustment' or p['amount_minor_units']>0,'proposal_positive')
        if get('proposals',p['proposal_id'])['role']=='force_adjustment': require(bool(p['reason']),'force_reason')
    claims=by('claims'); released={v['claim_id'] for v in r['releases']}
    for c in claims.values():
        table,field,key,classification=('opening_members','opening_id',c['opening_id'],'covered') if c['opening_id'] else ('certificate_members','certificate_id',c['certificate_id'],'selected')
        require(any(m['key_id']==c['key_id'] and m['version_id']==c['version_id'] and m['classification']==classification for m in matching(table,field,key)),'claim_member')
    current=[v for v in claims.values() if v['id'] not in released]
    require(len({v['key_id'] for v in current})==len(current),'overlapping_claims')
    require({(v['key_id'],v['claim_id']) for v in r['current_members']}=={(v['key_id'],v['id']) for v in current},'current_claims')
    active=set()
    for a in r['accounts']:
        if a['head_certificate_id']: require(a['opening_id'] is not None,'head_without_opening')
        head=a['head_certificate_id']; visited=set()
        while head:
            require(head not in visited,'chain_cycle'); visited.add(head)
            c=get('certificates',head); require(c['opening_id']==a['opening_id'],'chain_opening')
            active.add((a['account_id'],c['statement_date'],head)); head=c['previous_certificate_id']
        # Multiaccount events are selected by both keys.
        event=next((e for e in r['event_accounts'] if e['event_id']==a['last_event_id'] and e['account_id']==a['account_id']),None)
        require(event is not None and (event['after_chain_version'],event['after_opening_id'],event['after_head_id'])==(a['version'],a['opening_id'],a['head_certificate_id']),'chain_event_head')
    require(active=={(v['account_id'],v['statement_date'],v['certificate_id']) for v in r['active_certificates']},'active_chain_projection')
    for c in current:
        require(any(a['opening_id']==c['opening_id'] and a['account_id']==c['account_id'] for a in r['accounts']) if c['opening_id'] else any(v[2]==c['certificate_id'] for v in active),'claim_active_owner')
    for e in r['event_accounts']:
        require(e['after_chain_version']==e['before_chain_version']+1,'event_version')
        if e['before_chain_version']:
            require(any(p['account_id']==e['account_id'] and p['after_chain_version']==e['before_chain_version'] and p['after_opening_id']==e['before_opening_id'] and p['after_head_id']==e['before_head_id'] for p in r['event_accounts']),'event_history_gap')
    for consumption in r['proposal_consumptions']:
        proposal=get('proposals',consumption['proposal_id']); saved=get('proposal_revisions',consumption['proposal_revision_id'])
        account=get('drafts',proposal['draft_id'])['account_id']
        effects=[v for v in versions.values() if v['transaction_id']==consumption['journal_transaction_id'] and v['revision_id']==consumption['journal_revision_id'] and v['active'] and v['account_id']==account]
        require(bool(effects),'generated_journal_effect')
        total=sum(-v['signed_debit'] if v['account_type']=='credit_card' else v['signed_debit'] for v in effects)
        convention=effects[0]['account_type']
        sign= -1 if proposal['role']=='charge' else 1
        if convention=='credit_card' and proposal['role']!='force_adjustment':sign=-sign
        require(total==sign*saved['amount_minor_units'],'generated_journal_amount')
        require(all(v['effective_date']==saved['date'] and v['currency']==saved['currency'] for v in effects),'generated_journal_date')
    for name,rev,parent,number in [('drafts','draft_revisions','draft_id','revision_number'),('proposals','proposal_revisions','proposal_id','revision_number'),('report_presets','report_preset_revisions','preset_id','version')]:
        for h in r[name]:
            choices=matching(rev,parent,h['id']); require(bool(choices),'projection_revision')
            latest=max(choices,key=lambda v:v[number]); require(h['version']==latest[number],'projection_version')
            if 'current_revision_id' in h: require(h['current_revision_id']==latest['id'],'projection_head')
            else: require(h['parameters_snapshot']==latest['parameters_snapshot'],'preset_snapshot')
    _receipts(r)
    require(Counter(e['operation_id'] for e in r['events'])==Counter({o['id']:1 for o in r['operations']}),'operation_event_coverage')
    for event in r['events']:
        audit=event['audit_event_id']
        expected={v for (owner,_),v in transitions.items() if owner==audit}
        actual=set()
        targets={v['transaction_id'] for v in r['operation_transactions'] if v['operation_id']==event['operation_id']}
        for link in matching('event_effects','event_id',event['id']):
            new=versions[link['new_version_id']]
            require(link['source_audit_event_id']==new['source_audit_event_id']==audit,'event_causality')
            require(new['transaction_id'] in targets,'event_source_target')
            value=(link['key_id'],link['old_version_id'],link['new_version_id'],link['source_audit_event_id'])
            require(value==transitions.get((audit,link['key_id'])),'event_predecessor')
            actual.add(value)
        require(actual==expected,'event_transition_coverage')


def _receipts(r):
    from bookflow.company.reconciliation_schema import COMMANDS
    for op in r['operations']:
        require(op['command'] in COMMANDS and op['request_schema_version']==op['effect_schema_version']==1,'operation_format')
        require(1<=len(op['operation_key'])<=128,'operation_key')
        for field in ('original_request_snapshot','original_effect_snapshot'):
            envelope=op[field]; require(set(envelope['collections'])=={'request','effects','targets','generated'},'receipt_manifest')
            for kind,manifest in envelope['collections'].items():
                items=sorted((v for v in r['operation_items'] if v['operation_id']==op['id'] and v['kind']==kind),key=lambda v:v['ordinal'])
                require([v['ordinal'] for v in items]==list(range(manifest['count'])),'receipt_tail')
                require(digest([v['facts_snapshot'] for v in items])==manifest['hash'],'receipt_hash')
        # Intent hash covers the canonical stored original intent document; the
        # future command owner supplies presence/spelling/context captures.
        require(digest(op['original_request_snapshot']['canonical_intent'])==op['canonical_intent_hash'],'intent_hash')
        target_items=[v['facts_snapshot'] for v in r['operation_items'] if v['operation_id']==op['id'] and v['kind']=='targets']
        require(all(set(v)=={'kind','id'} for v in target_items),'target_shape')
        targets={(v['kind'],v['id']) for v in target_items}
        actual={(kind,v[field]) for kind,field in [('transactions','transaction_id'),('accounts','account_id'),('drafts','draft_id'),('openings','opening_id'),('certificates','certificate_id')] for v in r['operation_'+kind] if v['operation_id']==op['id']}
        require(len(targets)==len(target_items) and targets==actual,'operation_target_coverage')
    for a in r['attempts']:
        require(a['state'] in ('uploading','sealed','applied','aborted','superseded'),'attempt_state')
        require(a['state'] not in ('uploading','sealed') or any(d['id']==a['draft_id'] and d['state']=='open' for d in r['drafts']),'attempt_open_draft')
        require(any(e['audit_event_id']==a['audit_event_id'] and e['kind']==('bulk_stage' if a['state'] in ('uploading','sealed') else 'bulk_terminal') and any(d['operation_id']==e['operation_id'] and d['draft_id']==a['draft_id'] for d in r['operation_drafts']) for e in r['events']),'attempt_event')
        items=sorted((v for v in r['attempt_items'] if v['attempt_id']==a['id']),key=lambda v:v['ordinal'])
        require([v['ordinal'] for v in items]==list(range(len(items))) and len(items)<=a['declared_count'],'attempt_ordinals')
        seen=set()
        for item in items:
            subtype=next((v for v in r['attempt_'+item['kind']+'s'] if v['attempt_id']==a['id'] and v['ordinal']==item['ordinal']),None)
            require(subtype is not None,'attempt_subtype')
            payload={k:v for k,v in subtype.items() if k not in ('attempt_id','ordinal')}
            require(item['payload']==payload,'attempt_payload')
            fields={'member':('key_id',),'seed':('account_id','kind','opening_id','certificate_id','date'),'certificate':('account_id','certificate_id','predecessor_id'),'proposal':('proposal_id',)}[item['kind']]
            key=(item['kind'],*(subtype[f] for f in fields)); require(key not in seen,'attempt_duplicate_target'); seen.add(key)
            if item['kind'] in ('member','proposal'): require(subtype['draft_id']==a['draft_id'],'attempt_draft_owner')
            if item['kind']=='seed':
                require((subtype['kind']=='opening' and subtype['opening_id'] is not None and subtype['certificate_id'] is None) or (subtype['kind']=='statement' and subtype['certificate_id'] is not None and subtype['opening_id'] is None) or (subtype['kind']=='insert' and subtype['opening_id'] is None and subtype['certificate_id'] is None and subtype['date'] is not None),'seed_shape')
            if item['kind']=='certificate':
                require((subtype['mode']=='invalidate' and subtype['certificate_id'] is not None and subtype['replacement_draft_revision_id'] is None) or (subtype['mode']=='replace' and subtype['certificate_id'] is not None and subtype['replacement_draft_revision_id'] is not None) or (subtype['mode']=='insert' and subtype['certificate_id'] is None and subtype['replacement_draft_revision_id'] is not None),'replacement_shape')
        if a['state'] in ('sealed','applied'):
            require(len(items)==a['declared_count'],'attempt_tail')
            require(digest([{'kind':v['kind'],'payload':v['payload']} for v in items])==a['intent_hash'],'attempt_hash')
        chunks=sorted((v for v in r['attempt_chunks'] if v['attempt_id']==a['id']),key=lambda v:v['chunk_index'])
        require([v['chunk_index'] for v in chunks]==list(range(len(chunks))),'chunk_sequence')
        received=[]
        for chunk in chunks:
            receipt=chunk['receipt']; values=chunk['original_chunk']['items']
            require(digest(chunk['original_chunk'])==chunk['request_hash']==receipt['request_hash'],'chunk_hash')
            require(receipt['chunk_index']==chunk['chunk_index'] and receipt['first_ordinal']==len(received) and receipt['count']==len(values),'chunk_receipt')
            received.extend(values)
        require(received==[{'kind':v['kind'],'payload':v['payload']} for v in items],'chunk_item_coverage')
    require({(v['draft_id'],v['attempt_id']) for v in r['attempt_active']}=={(v['draft_id'],v['id']) for v in r['attempts'] if v['state'] in ('uploading','sealed')},'attempt_barriers')
