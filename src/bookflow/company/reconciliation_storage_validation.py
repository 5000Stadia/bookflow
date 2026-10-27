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

from bookflow.company.reconciliation_models import (
    MovementKey, PRODUCER_ROLES, COMMERCIAL_PRODUCERS, DEPOSIT_PRODUCERS, FUNDING_PRODUCERS)
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


FINGERPRINT_FORMAT = 'reconciliation.population/1'


def population_fingerprint(values):
    """What a captured population fingerprints: the stored rows, never the present ledger.

    A capture says what was true at a cutoff, so the number that later proves it has to come
    from the rows the capture stored -- the ledger underneath is expected to move, and asking
    it again would only ever re-answer a different question. One definition, imported by the
    writer that stores a capture and by the validation that reads one back, because two
    spellings of a fingerprint are two fingerprints.

    The format is named inside the hash rather than left implicit. This is not the adapter-value
    digest an earlier draft of the storage used, and a fingerprint that does not say which
    definition produced it cannot be told apart from one that disagrees; naming it is what lets
    a later format be added without any stored capture becoming unreadable. Inactive and
    future-dated members are included -- they are part of the population even though they are
    not part of the general-ledger total.
    """
    # A snapshot column is text in the database and a decoded dict once `validate` has read it,
    # and this is called from both sides, so the one shape it works in is the decoded one.
    def movement(v):
        found=v['movement_snapshot']
        return found if type(found) is dict else json.loads(found)
    return digest(dict(format=FINGERPRINT_FORMAT,values=sorted((dict(key_id=v['key_id'],version_id=v['id'],source_version=v['source_version'],
                               account_id=v['account_id'],effective_date=v['effective_date'],
                               signed_debit=v['signed_debit'],active=v['active'],
                               movement=movement(v)) for v in values),key=canonical)))


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
    # The schema version of this blob, and the only field here a person is never asked for: a
    # form that renders it makes a bookkeeper choose "1" from a dropdown to describe a statement.
    # Stored snapshots still carry it explicitly, which `snapshot_format` checks on the way in.
    format: Literal[1] = 1
    statement_reference: str | None = None
    entered_text: str | None = None


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


def _frozen(value):
    """A hashable stand-in for a decoded JSON value: equal exactly when the values are `==`."""
    if type(value) is dict:
        return ('dict', frozenset((k, _frozen(v)) for k, v in value.items()))
    if type(value) is list:
        return ('list', tuple(_frozen(v) for v in value))
    return value


def enumerated(memo, graph):
    """`enumerate_graph(graph)`, once per graph per memo. A Graph's rows are frozen, so its
    enumeration cannot change while the memo holds it (and holding it keeps its id unique)."""
    from bookflow.company.reconciliation_adapters import enumerate_graph
    found = memo.get(('enumerate', id(graph)))
    if found is None or found[0] is not graph:
        found = memo['enumerate', id(graph)] = (graph, enumerate_graph(graph))
    return found[1]


def proved(memo, graph, account_id, cutoff):
    """`prove` over the graph's own enumeration, once per (graph, account, cutoff) per memo.
    Only a result is kept: a proof that raises is attempted again, and raises again."""
    from bookflow.company.reconciliation_proof import prove
    key = ('prove', id(graph), account_id, cutoff)
    found = memo.get(key)
    if found is None or found[0] is not graph:
        history, current = enumerated(memo, graph)
        found = memo[key] = (graph, prove(graph, history, current, account_id, cutoff))
    return found[1]


class Suffix:
    """What a scoped validation re-proves on top of a verified watermark (`reconciliation_watermark`).

    `rows`: for each insert-only table, the primary keys of its rows written after the watermark.
    A scoped validation is given a scoped source graph (`Graph.scope`) with it and re-proves the
    documents in that scope, every row in `rows`, every row of a mutable table, and every older
    closure those touch (a capture or draft revision whose movement group gained a version, an
    event whose transitions name a document in scope). Everything else stands as the watermark
    proved it. Its refusals are only ever a reason to run the full validation instead
    (`reconciliation_loading.load`), so a scoped refusal never reaches a caller.
    """
    def __init__(self, rows):
        self.rows = {name: frozenset(keys) for name, keys in rows.items()}


def validate(rows, *, source=None, captured_graphs=None, referenced_rows=None, memo=None, suffix=None):
    """Validate a complete N-table snapshot; return no partial decoded output.

    `memo` lets a caller keep the source enumeration and proofs made here for the snapshot it
    builds (`reconciliation_preparation`), so a command proves each graph once.

    Source is an explicit R0 Graph, not a connection. Whole source coverage is
    required only for nonempty effect storage (future A migration/owner caller).
    Original command documents remain opaque evidence in strict envelopes; their
    future public action models/authorization and execution are not implemented.
    """
    from bookflow.company.schema import metadata
    from bookflow.company.reconciliation_schema import MUTABLE
    tables = {n.removeprefix(PREFIX):t for n,t in metadata.tables.items() if n.startswith(PREFIX)}
    require(set(rows) == set(tables), 'table_inventory')
    r = {n:[dict(v) for v in values] for n,values in rows.items()}
    scope = None
    if suffix is not None:
        require(source is not None and source.scope is not None, 'source_proof_required')
        scope = source.scope
    mutable = {n.removeprefix(PREFIX) for n in MUTABLE}
    def fresh(name, value):
        """Whether this row is re-proved: always without a suffix; with one, a mutable table's
        rows and the rows written after the watermark."""
        if suffix is None or name in mutable: return True
        written = suffix.rows.get(name)
        return bool(written) and tuple(value[c.name] for c in tables[name].primary_key) in written
    renewed = {}
    for name, values in r.items():
        t = tables[name]
        seen = set()
        for constraint in t.constraints:
            if constraint.__class__.__name__=='UniqueConstraint':
                tuples=[tuple(v[c.name] for c in constraint.columns) for v in values]
                tuples=[v for v in tuples if None not in v]
                require(len(set(tuples))==len(tuples),'duplicate_unique_key')
        # Each table's shape read once, not once per row.
        names = set(t.c.keys()); key = [c.name for c in t.primary_key]
        shape = [(c.name, c.info['kind'], c.nullable) for c in t.c]
        decoded_only = [c for c, kind, _ in shape if kind == 'json']
        written = None if suffix is None or name in mutable else suffix.rows.get(name, frozenset())
        renewed[name] = again = []
        for value in values:
            require(set(value) == names, 'row_columns')
            pk = tuple(value[c] for c in key)
            require(pk not in seen, 'duplicate_primary_key'); seen.add(pk)
            if written is not None and pk not in written:
                # Proved when the watermark was written and unchanged since: only decoded.
                for c in decoded_only:
                    if value[c] is not None: value[c] = json.loads(value[c])
                continue
            again.append(value)
            for column, kind, nullable in shape:
                v = value[column]
                if v is None:
                    require(nullable, 'null_required'); continue
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
                        if (name,column) in MODELS:
                            if 'format' in MODELS[name,column].model_fields:require(type(decoded.get('format')) is int,'snapshot_format')
                            try: MODELS[name,column].model_validate(decoded,strict=True)
                            except ValueError as exc: raise InvalidStorage('snapshot_format') from exc
                        value[column] = decoded
    # The rows are final from here on, so every lookup below is an index built once from them
    # (lazily, on first use) rather than a scan per question. `by` keeps the last row of a
    # repeated key and `matching` keeps table order, as the scans they replace did.
    indexes={}
    def by(name,field='id'):
        found=indexes.get(('by',name,field))
        if found is None: found=indexes['by',name,field]={v[field]:v for v in r[name]}
        return found
    def grouped_by(name,field):
        found=indexes.get(('matching',name,field))
        if found is None:
            found=indexes['matching',name,field]={}
            for v in r[name]: found.setdefault(v[field],[]).append(v)
        return found
    def matching(name,field,key): return list(grouped_by(name,field).get(key,()))
    def get(name,key,field='id'):
        found=by(name,field).get(key); require(found is not None,'missing_owner'); return found
    # Prove every new-table FK explicitly even when a migration has FK OFF. Each target's key
    # tuples become one set, built the first time a non-null key asks for it.
    referenced_rows = referenced_rows or {}
    targets={}
    from bookflow.company.reconciliation_loading import KEYED_ONLY
    for name,t in tables.items():
        for constraint in t.foreign_key_constraints:
            remote=constraint.referred_table.name
            columns=tuple(e.column.name for e in constraint.elements)
            # A key into an insert-only table cannot be broken after it was proved: the target
            # can be neither deleted nor changed. So a scoped validation proves those keys for
            # the rows it re-proves, and every key into a table whose rows can change.
            settled = suffix is not None and (remote in KEYED_ONLY or (remote.startswith(PREFIX) and remote.removeprefix(PREFIX) not in mutable))
            for value in (renewed[name] if settled else r[name]):
                key=tuple(value[e.parent.name] for e in constraint.elements)
                if any(v is None for v in key): continue
                candidates = r[remote.removeprefix(PREFIX)] if remote.startswith(PREFIX) else referenced_rows.get(remote)
                require(candidates is not None,'referenced_rows_required')
                held=targets.get((remote,columns))
                if held is None: held=targets[remote,columns]={tuple(v[c] for c in columns) for v in candidates}
                require(key in held,'foreign_owner')
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
        # Three shapes, one declaration: a deposit points at its bank subtype row, a funding
        # document has no subtype row because its component is the document itself, and every
        # other producer points at the commercial line the movement was entered on.
        if v['producer'] in DEPOSIT_PRODUCERS:
            require(v['deposit_link_id']==v['id'] and v['commercial_link_id'] is None,'effect_subtype_pointer')
        elif v['producer'] in FUNDING_PRODUCERS:
            require(v['deposit_link_id'] is None and v['commercial_link_id'] is None,'effect_subtype_pointer')
        else:
            require(v['commercial_link_id']==v['id'] and v['deposit_link_id'] is None,'effect_subtype_pointer')
    for table,allowed in [('commercial_versions',COMMERCIAL_PRODUCERS),('deposit_versions',DEPOSIT_PRODUCERS)]:
        for v in r[table]:
            require(get('effect_versions',v['id'])['producer'] in allowed,'extra_effect_subtype')
    for v in r['attempt_items']:
        require(v['kind'] in ('member','seed','certificate','proposal'),'attempt_kind')
        for kind in ('member','seed','certificate','proposal'):
            require(v[kind+'_ordinal']==(v['ordinal'] if kind==v['kind'] else None),'attempt_subtype_pointer')
    attempt_keys={(i['attempt_id'],i['ordinal'],i['kind']) for i in r['attempt_items']}
    for kind in ('member','seed','certificate','proposal'):
        for v in r['attempt_'+kind+'s']:
            require((v['attempt_id'],v['ordinal'],kind) in attempt_keys,'extra_attempt_subtype')
    for c in r['claims']:
        require((c['opening_id'] is None)!=(c['certificate_id'] is None),'claim_owner_shape')
    for key in r['keys']:
        require(key['producer'] in PRODUCER_ROLES and key['role'] in PRODUCER_ROLES[key['producer']],'producer_role')
        require((key['deposit_key_id'] is not None)==(key['producer'] in DEPOSIT_PRODUCERS),'key_subtype')
        require((key['commercial_line_id'] is not None)==(key['producer'] in COMMERCIAL_PRODUCERS),'key_subtype')
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
        require(evidence['transaction_id'] is not None and (
            (evidence['kind']=='transaction' and evidence['attachment_id'] is None and evidence['attachment_link_id'] is None)
            or (evidence['kind']=='transaction_attachment' and evidence['attachment_id'] is not None and evidence['attachment_link_id'] is not None)), 'opening_evidence_shape')
        if evidence['kind']=='transaction_attachment':
            require(any(v['id']==evidence['attachment_link_id']
                        and v['attachment_id']==evidence['attachment_id']
                        and v['record_type']=='transaction'
                        and v['record_id']==evidence['transaction_id']
                        for v in referenced_rows['attachment_links']), 'attachment_owner')
    keys=by('keys'); versions=by('effect_versions')
    def component(key):
        """What a stored key names: a bank effect key, the document itself, or a line."""
        if key['producer'] in DEPOSIT_PRODUCERS: return key['deposit_key_id']
        if key['producer'] in FUNDING_PRODUCERS: return key['transaction_id']
        return key['commercial_line_id']
    transitions = {}
    memo = {} if memo is None else memo
    if versions:
        require(source is not None,'source_proof_required')
        history,current=enumerated(memo,source)
        for account in {v.account_id for v in history}: proved(memo,source,account,'9999-12-31')
        expected={(v.ref.producer,v.ref.transaction_id,v.ref.role,v.ref.component_id,v.version_id):v for v in history}
        actual={}
        def derived(v):
            """Whether this stored version is re-derived: always, or with a suffix when its
            document (or its key's) is in the scope. The rest stand as the watermark proved them."""
            return scope is None or v['transaction_id'] in scope or keys[v['key_id']]['transaction_id'] in scope
        for v in versions.values():
            if not derived(v): continue
            k=keys[v['key_id']]
            ref=(k['producer'],k['transaction_id'],k['role'],component(k),v['source_version'])
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
            if e.ref.producer in DEPOSIT_PRODUCERS:
                link=get('deposit_versions',v['id']); require((link['bank_key_id'],link['bank_version_id'])==(e.ref.component_id,e.version_id),'deposit_link')
            elif e.ref.producer in FUNDING_PRODUCERS:
                # The component is the document, already carried by the key and the version.
                require(k['transaction_id']==e.ref.component_id,'funding_link')
                require(not matching('commercial_versions','id',v['id']) and not matching('deposit_versions','id',v['id']),'funding_link')
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

        heads={v['key_id']:v['version_id'] for v in r['effect_heads'] if scope is None or keys[v['key_id']]['transaction_id'] in scope}
        first_key={}
        for k in keys.values(): first_key.setdefault((k['producer'],k['transaction_id'],k['role'],component(k)),k['id'])
        expected_heads={}
        for v in current:
            head=first_key.get((v.ref.producer,v.ref.transaction_id,v.ref.role,v.ref.component_id))
            if head is None: raise StopIteration  # what the scan's `next()` raised
            expected_heads[head]=actual[v.ref.producer,v.ref.transaction_id,v.ref.role,v.ref.component_id,v.version_id]['id']
        require(heads==expected_heads,'source_heads')
    else:
        require(not keys and not r['effect_heads'],'empty_source_links')
    whole_movements={}
    for e in versions.values():
        if e['active']: whole_movements.setdefault(_frozen(e['movement_snapshot']),set()).add(e['key_id'])
    # With a suffix, the owners re-proved below: every capture and draft revision written after
    # the watermark or gaining a member since, and every older one holding a version whose
    # movement group gained a version since (which is how an append reaches an old capture's
    # `incomplete_movement`). The rest hold only rows that cannot change and stand proved.
    reproved=None
    if suffix is not None:
        grown={_frozen(v['movement_snapshot']) for v in versions.values() if v['active'] and fresh('effect_versions',v)}
        reached={i for i,v in versions.items() if _frozen(v['movement_snapshot']) in grown} if grown else set()
        reproved=set()
        for name,owner,field in (('openings','opening_members','opening_id'),('certificates','certificate_members','certificate_id'),('draft_revisions','draft_members','revision_id')):
            reproved.update((name,v['id']) for v in renewed[name])
            reproved.update((name,m[field]) for m in renewed[owner])
            if reached: reproved.update((name,m[field]) for m in r[owner] if m['version_id'] in reached)
    def owed(name,owner):
        return reproved is None or (name,owner) in reproved
    def members(name,owner_field,owner):
        values=matching(name,owner_field,owner)
        require(sorted(v['ordinal'] for v in values)==list(range(len(values))),'member_ordinals')
        grouped=defaultdict(set)
        for m in values:
            v=versions[m['version_id']]
            grouped[canonical(v['movement_snapshot'])].add(m.get('classification',m.get('action')))
        require(all(len(v)==1 for v in grouped.values()),'split_movement')
        # A movement is whole when every key it is made of is represented, as
        # `reconciliation_preparation.whole_selection` decides it. Comparing version ids would
        # also demand a key's superseded versions: a void that keeps its revision (a voided sales
        # receipt) shares the movement of the active version it replaced, and a certificate,
        # whose members are the heads, holds only the void.
        selected={versions[m['version_id']]['key_id'] for m in values}
        for m in values:
            v=versions[m['version_id']]
            whole=whole_movements.get(_frozen(v['movement_snapshot']),set())
            require(whole<=selected,'incomplete_movement')
        return values
    # A capture is proven against the live graph where it is written and validated against the
    # rows it stored where it is read. Re-deriving it later does not test what it says -- "at
    # this cutoff these were the heads and the signed total was X" -- it asks whether they are
    # still the heads, which is expected to answer no after any ordinary correction and is the
    # very state `reconciliation_reports.project` exists to report. So a graph is optional here
    # and every capture whose graph is supplied is proven; the stored-row ties below run either
    # way.
    #
    # Those ties are consistency, never completeness. A capture that simply left a movement out,
    # with its members, population, total and fingerprint all agreeing about the smaller world,
    # satisfies every one of them -- so head coverage has to be derived independently, from the
    # graph, at the moment the capture is written, which is what `capture_population_completeness`
    # below does and why a writer must supply the graph.
    captured_graphs = captured_graphs or {}
    expected_captures={v['id'] for name in ('openings','certificates') for v in r[name]}
    require(set(captured_graphs)<=expected_captures,'capture_proof_inventory')
    if reproved is not None:
        reproved.update((name,v['id']) for name in ('openings','certificates') for v in r[name] if v['id'] in captured_graphs)
    def capture(owner,pop,opening=False):
        """The write-time proof. Absent a graph the stored-row ties below stand alone."""
        if owner['id'] not in captured_graphs:return
        graph=captured_graphs[owner['id']]
        history,current=enumerated(memo,graph)
        total,gl=proved(memo,graph,owner['account_id'],pop['cutoff'])
        values=[v for v in current if v.account_id==owner['account_id'] and (not opening or v.active and v.effective_date<=pop['cutoff'])]
        identity={ (keys[v['key_id']]['producer'],keys[v['key_id']]['transaction_id'],keys[v['key_id']]['role'],component(keys[v['key_id']]),v['source_version']):v['id'] for v in versions.values() }
        expected=[identity.get((v.ref.producer,v.ref.transaction_id,v.ref.role,v.ref.component_id,v.version_id)) for v in values]
        if getattr(graph, 'verified', None) is not None:
            # A scoped graph: the documents outside its scope are current as the watermark proved.
            expected.extend(i for i,account,active,date,_ in graph.verified.current
                            if account==owner['account_id'] and (not opening or active and date<=pop['cutoff']))
        require(None not in expected and Counter(expected)==Counter(pop['version_ids']),'capture_population_completeness')
        require(pop['signed_gl_total']==total==gl,'capture_gl')
    def amount(m):
        v=versions[m['version_id']]; return -v['signed_debit'] if v['account_type']=='credit_card' else v['signed_debit']
    for o in r['openings']:
        if not owed('openings',o['id']):
            require(get('drafts',get('draft_revisions',o['origin_draft_revision_id'])['draft_id'])['kind']=='opening','opening_draft_kind')
            continue
        ms=members('opening_members','opening_id',o['id']); pop=o['authorized_source_snapshot']
        capture(o,pop,opening=True)
        require(get('drafts',get('draft_revisions',o['origin_draft_revision_id'])['draft_id'])['kind']=='opening','opening_draft_kind')
        require(pop['account_id']==o['account_id'] and pop['currency']==o['currency'] and pop['cutoff']==o['opening_date'],'opening_snapshot_owner')
        require(Counter(pop['version_ids'])==Counter(m['version_id'] for m in ms),'opening_population')
        require(all(versions[m['version_id']]['active'] and versions[m['version_id']]['effective_date']<=o['opening_date'] for m in ms),'opening_eligibility')
        require(sum(amount(m) for m in ms if m['classification']=='covered')==o['balance'],'opening_balance')
        require(sum(versions[m['version_id']]['signed_debit'] for m in ms)==pop['signed_gl_total'],'opening_gl')
        require(pop['source_fingerprint']==population_fingerprint(versions[m['version_id']] for m in ms),'capture_fingerprint')
    selected_keys={}
    def cleared(certificate):
        found=selected_keys.get(certificate)
        if found is None: found=selected_keys[certificate]={m['key_id'] for m in matching('certificate_members','certificate_id',certificate) if m['classification']=='selected'}
        return found
    for c in r['certificates']:
        if not owed('certificates',c['id']):
            require(get('drafts',get('draft_revisions',c['origin_draft_revision_id'])['draft_id'])['kind'] in ('statement','amendment'),'certificate_draft_kind')
            continue
        ms=members('certificate_members','certificate_id',c['id']); pop=c['captured_source_snapshot']
        capture(c,pop)
        require(get('drafts',get('draft_revisions',c['origin_draft_revision_id'])['draft_id'])['kind'] in ('statement','amendment'),'certificate_draft_kind')
        require(pop['account_id']==c['account_id'] and pop['currency']==c['currency'] and pop['cutoff']==c['statement_date'],'certificate_snapshot_owner')
        require(Counter(pop['version_ids'])==Counter(m['version_id'] for m in ms),'certificate_population')
        covered={m['key_id'] for m in matching('opening_members','opening_id',c['opening_id']) if m['classification']=='covered'}
        prior=set(); previous=c['previous_certificate_id']; visited=set()
        while previous:
            require(previous not in visited,'chain_cycle');visited.add(previous)
            prior.update(cleared(previous))
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
        # The certificate counterpart of `opening_gl`. Without it, dropping the graph re-proof
        # would leave a certificate with no tie at all between its captured total and the rows
        # it captured -- `certificate_balance` only ties the selected subset to the statement.
        require(sum(versions[m['version_id']]['signed_debit'] for m in ms if m['eligible_at_cutoff'])==pop['signed_gl_total'],'certificate_gl')
        require(pop['source_fingerprint']==population_fingerprint(versions[m['version_id']] for m in ms),'capture_fingerprint')
    for name,parent in [('draft_revisions','draft_id'),('proposal_revisions','proposal_id')]:
        for v in r[name]:
            previous=v['previous_revision_id']
            require((previous is None)==(v['revision_number']==1),'revision_origin')
            if previous: require(get(name,previous)['revision_number']+1==v['revision_number'],'revision_sequence')
    for d in r['draft_revisions']:
        if owed('draft_revisions',d['id']): members('draft_members','revision_id',d['id'])
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
        if not fresh('claims',c): continue
        table,field,key,classification=('opening_members','opening_id',c['opening_id'],'covered') if c['opening_id'] else ('certificate_members','certificate_id',c['certificate_id'],'selected')
        found=indexes.get(('claimable',table,field,key))
        if found is None: found=indexes['claimable',table,field,key]={(m['key_id'],m['version_id'],m['classification']) for m in matching(table,field,key)}
        require((c['key_id'],c['version_id'],classification) in found,'claim_member')
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
    owners={(a['opening_id'],a['account_id']) for a in r['accounts']}; chain_heads={v[2] for v in active}
    for c in current:
        require((c['opening_id'],c['account_id']) in owners if c['opening_id'] else c['certificate_id'] in chain_heads,'claim_active_owner')
    afters={(p['account_id'],p['after_chain_version'],p['after_opening_id'],p['after_head_id']) for p in r['event_accounts']}
    for e in r['event_accounts']:
        require(e['after_chain_version']==e['before_chain_version']+1,'event_version')
        if e['before_chain_version']:
            require((e['account_id'],e['before_chain_version'],e['before_opening_id'],e['before_head_id']) in afters,'event_history_gap')
    for consumption in r['proposal_consumptions']:
        if not fresh('proposal_consumptions',consumption) and consumption['journal_transaction_id'] not in scope: continue
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
    _receipts(r,grouped_by)
    require(Counter(e['operation_id'] for e in r['events'])==Counter({o['id']:1 for o in r['operations']}),'operation_event_coverage')
    by_audit=defaultdict(set)
    for (owner,_),v in transitions.items(): by_audit[owner].add(v)
    for event in r['events']:
        audit=event['audit_event_id']
        expected=by_audit.get(audit,set())
        actual=set()
        targets={v['transaction_id'] for v in matching('operation_transactions','operation_id',event['operation_id'])}
        links=matching('event_effects','event_id',event['id'])
        if scope is not None and not fresh('events',event):
            # An older event: only its transitions of documents in scope are derived again, and
            # its links to the rest stand as the watermark proved them.
            links=[v for v in links if versions[v['new_version_id']]['transaction_id'] in scope]
        for link in links:
            new=versions[link['new_version_id']]
            require(link['source_audit_event_id']==new['source_audit_event_id']==audit,'event_causality')
            require(new['transaction_id'] in targets,'event_source_target')
            value=(link['key_id'],link['old_version_id'],link['new_version_id'],link['source_audit_event_id'])
            require(value==transitions.get((audit,link['key_id'])),'event_predecessor')
            actual.add(value)
        require(actual==expected,'event_transition_coverage')


def _receipts(r,grouped_by=None):
    from bookflow.company.reconciliation_schema import COMMANDS
    if grouped_by is None:
        # Called on its own (tests): the same table-order groups `validate` builds.
        groups={}
        def grouped_by(name,field):
            found=groups.get((name,field))
            if found is None:
                found=groups[name,field]={}
                for v in r[name]: found.setdefault(v[field],[]).append(v)
            return found
    def owned(name,field,key): return grouped_by(name,field).get(key,())
    first_subtype={}
    for kind in ('member','seed','certificate','proposal'):
        for v in r['attempt_'+kind+'s']: first_subtype.setdefault((kind,v['attempt_id'],v['ordinal']),v)
    first_revision={}
    for v in r['draft_revisions']: first_revision.setdefault(v['id'],v)
    for op in r['operations']:
        require(op['command'] in COMMANDS and op['request_schema_version']==op['effect_schema_version']==1,'operation_format')
        require(1<=len(op['operation_key'])<=128,'operation_key')
        for field in ('original_request_snapshot','original_effect_snapshot'):
            envelope=op[field]; require(set(envelope['collections'])=={'request','effects','targets','generated'},'receipt_manifest')
            for kind,manifest in envelope['collections'].items():
                items=sorted((v for v in owned('operation_items','operation_id',op['id']) if v['kind']==kind),key=lambda v:v['ordinal'])
                require([v['ordinal'] for v in items]==list(range(manifest['count'])),'receipt_tail')
                require(digest([v['facts_snapshot'] for v in items])==manifest['hash'],'receipt_hash')
        # Intent hash covers the canonical stored original intent document; the
        # future command owner supplies presence/spelling/context captures.
        require(digest(op['original_request_snapshot']['canonical_intent'])==op['canonical_intent_hash'],'intent_hash')
        target_items=[v['facts_snapshot'] for v in owned('operation_items','operation_id',op['id']) if v['kind']=='targets']
        require(all(set(v)=={'kind','id'} for v in target_items),'target_shape')
        targets={(v['kind'],v['id']) for v in target_items}
        actual={(kind,v[field]) for kind,field in [('transactions','transaction_id'),('accounts','account_id'),('drafts','draft_id'),('openings','opening_id'),('certificates','certificate_id')] for v in owned('operation_'+kind,'operation_id',op['id'])}
        require(len(targets)==len(target_items) and targets==actual,'operation_target_coverage')
    for a in r['attempts']:
        require(a['state'] in ('uploading','sealed','applied','aborted','superseded'),'attempt_state')
        require(a['state'] not in ('uploading','sealed') or any(d['id']==a['draft_id'] and d['state']=='open' for d in r['drafts']),'attempt_open_draft')
        require(any(e['audit_event_id']==a['audit_event_id'] and e['kind']==('bulk_stage' if a['state'] in ('uploading','sealed') else 'bulk_terminal') and any(d['operation_id']==e['operation_id'] and d['draft_id']==a['draft_id'] for d in r['operation_drafts']) for e in r['events']),'attempt_event')
        items=sorted(owned('attempt_items','attempt_id',a['id']),key=lambda v:v['ordinal'])
        require([v['ordinal'] for v in items]==list(range(len(items))) and len(items)<=a['declared_count'],'attempt_ordinals')
        seen=set()
        for item in items:
            subtype=first_subtype.get((item['kind'],a['id'],item['ordinal'])) if item['kind'] in ('member','seed','certificate','proposal') else next((v for v in r['attempt_'+item['kind']+'s'] if v['attempt_id']==a['id'] and v['ordinal']==item['ordinal']),None)
            require(subtype is not None,'attempt_subtype')
            payload={k:v for k,v in subtype.items() if k not in ('attempt_id','ordinal')}
            require(item['payload']==payload,'attempt_payload')
            # Joined semantic identity is aggregate-enforced, not a SQL UNIQUE.
            # Retained predecessor is a separate topology observation.
            if item['kind']=='certificate':
                if subtype['certificate_id'] is not None:
                    key=('certificate',subtype['certificate_id'])
                else:
                    revision=first_revision.get(subtype['replacement_draft_revision_id'])
                    require(revision is not None and revision['account_id']==subtype['account_id'],'attempt_revision_owner')
                    header=Header.model_validate(revision['header_snapshot'])
                    require(header.statement_date is not None,'attempt_statement_date')
                    key=('insertion',subtype['account_id'],header.statement_date)
            else:
                fields={'member':('key_id',),'seed':('account_id','kind','opening_id','certificate_id','date'),'proposal':('proposal_id',)}[item['kind']]
                key=(item['kind'],*(subtype[f] for f in fields))
            require(key not in seen,'attempt_duplicate_target'); seen.add(key)
            if item['kind'] in ('member','proposal'): require(subtype['draft_id']==a['draft_id'],'attempt_draft_owner')
            if item['kind']=='seed':
                require((subtype['kind']=='opening' and subtype['opening_id'] is not None and subtype['certificate_id'] is None) or (subtype['kind']=='statement' and subtype['certificate_id'] is not None and subtype['opening_id'] is None) or (subtype['kind']=='insert' and subtype['opening_id'] is None and subtype['certificate_id'] is None and subtype['date'] is not None),'seed_shape')
            if item['kind']=='certificate':
                require((subtype['mode']=='invalidate' and subtype['certificate_id'] is not None and subtype['replacement_draft_revision_id'] is None) or (subtype['mode']=='replace' and subtype['certificate_id'] is not None and subtype['replacement_draft_revision_id'] is not None) or (subtype['mode']=='insert' and subtype['certificate_id'] is None and subtype['replacement_draft_revision_id'] is not None),'replacement_shape')
        if a['state'] in ('sealed','applied'):
            require(len(items)==a['declared_count'],'attempt_tail')
            require(digest([{'kind':v['kind'],'payload':v['payload']} for v in items])==a['intent_hash'],'attempt_hash')
        chunks=sorted(owned('attempt_chunks','attempt_id',a['id']),key=lambda v:v['chunk_index'])
        require([v['chunk_index'] for v in chunks]==list(range(len(chunks))),'chunk_sequence')
        received=[]
        for chunk in chunks:
            receipt=chunk['receipt']; values=chunk['original_chunk']['items']
            require(digest(chunk['original_chunk'])==chunk['request_hash']==receipt['request_hash'],'chunk_hash')
            require(receipt['chunk_index']==chunk['chunk_index'] and receipt['first_ordinal']==len(received) and receipt['count']==len(values),'chunk_receipt')
            received.extend(values)
        require(received==[{'kind':v['kind'],'payload':v['payload']} for v in items],'chunk_item_coverage')
    require({(v['draft_id'],v['attempt_id']) for v in r['attempt_active']}=={(v['draft_id'],v['id']) for v in r['attempts'] if v['state'] in ('uploading','sealed')},'attempt_barriers')
