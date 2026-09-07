"""Nonposting transformations over explicit, complete R0/N snapshots.

Authority participants must come from the current owning authority call. This
private value is neither an authorization credential nor reusable across fences.
Runtime loading, publication and persistence are deliberately not implemented.
"""
import copy
import json
from dataclasses import dataclass
from collections import defaultdict
from bookflow.company import reconciliation_adapters as adapters
from bookflow.company.reconciliation_proof import prove
from bookflow.company.reconciliation_storage_validation import validate, canonical, digest
from bookflow.company import reconciliation_commands_models as m

# Private domain reasons travel the existing typed pipeline without registering
# public reconciliation codes before activation. Shared codes remain unchanged.
from bookflow.core.errors import BookflowError, ALL_CODES
from contextlib import contextmanager

PRIVATE_REASONS=frozenset({'E_RECONCILIATION_ATTEMPT_STATE','E_RECONCILIATION_CHAIN_STALE','E_RECONCILIATION_DATE','E_RECONCILIATION_DEPENDENCY','E_RECONCILIATION_DIFFERENCE','E_RECONCILIATION_DRAFT_STATE','E_RECONCILIATION_MANIFEST','E_RECONCILIATION_MEMBERSHIP_CONFLICT','E_RECONCILIATION_OPENING_UNPROVEN','E_RECONCILIATION_OPERATION_KEY_REUSED','E_RECONCILIATION_SELECTION_STALE','E_RECONCILIATION_SOURCE_INVALID','E_RECONCILIATION_UNSUPPORTED'})

class ReconciliationError(BookflowError):
    def __init__(self,rule):
        if rule not in ALL_CODES and rule not in PRIVATE_REASONS:raise ValueError('unknown private reconciliation rule')
        self.rule=rule
        if rule in ALL_CODES:
            super().__init__(rule)
        else:
            code='E_INTERNAL' if rule=='E_RECONCILIATION_SOURCE_INVALID' else 'E_VALIDATION'
            super().__init__(code,message=rule,details={'reason':rule})

@contextmanager
def adapter_errors():
    """Convert only the owning adapter's known content/unsupported failures."""
    try:yield
    except adapters.Unsupported as exc:
        raise ReconciliationError('E_RECONCILIATION_UNSUPPORTED') from exc
    except (adapters.Corrupt,KeyError,ValueError,TypeError,IndexError,StopIteration) as exc:
        raise ReconciliationError('E_RECONCILIATION_SOURCE_INVALID') from exc


def require(condition,code):
    if not condition:raise ReconciliationError(code)

def bounded(value):
    require(type(value) is int and -(2**63)<=value<2**63,'E_VALUE_RANGE')
    return value

def authority_required(rows,source,captured_graphs):
    required={r['id'] for r in source.rows['transactions']}
    required.update(r['transaction_id'] for r in rows['operation_transactions'])
    required.update(r['id'] for g in captured_graphs.values() for r in g.rows['transactions'])
    return required


def read_admission(s,authority_transactions, *, extra_transactions=(),extra_accounts=()):
    """Caller supplies a fresh real authority result, not a capability flag.

    Check retained/current identity coverage before all content, filters or page
    diagnostics. A previously constructed Snapshot does not grant a later read.
    """
    extra_transactions=set(extra_transactions)
    require(authority_required(s.rows,s.source,s.captured_graphs)|extra_transactions<=set(authority_transactions),'E_PERMISSION')
    require(extra_transactions<={v['id'] for v in s.source.rows['transactions']},'E_RECONCILIATION_SOURCE_INVALID')
    # Complete snapshot scope, including old/canceled/aborted account owners.
    accounts={v['account_id'] for name in ('accounts','drafts','certificates','openings','effect_versions') for v in s.rows[name]}
    accounts.update(extra_accounts)
    for account in sorted(accounts):account_population(s,account,'9999-12-31')


@dataclass(frozen=True)
class Snapshot:
    rows: dict
    source: adapters.Graph
    captured_graphs: dict
    referenced_rows: dict
    authority_transactions: tuple[str,...]
    def by(self,name):return {r['id']:r for r in self.rows[name]}
    @property
    def versions(self):return self.by('effect_versions')
    @property
    def current(self):
        versions=self.versions
        return {r['key_id']:versions[r['version_id']] for r in self.rows['effect_heads']}

def snapshot(rows, *, source: adapters.Graph, captured_graphs, referenced_rows, authority_transactions):
    """Identity coverage only; caller must first run real current authority.

    Never converts an absent source/authority input to an empty successful world.
    Checks authority coverage before content diagnostics, including history.
    """
    require(authority_required(rows,source,captured_graphs)<=set(authority_transactions),'E_PERMISSION')
    values=copy.deepcopy((rows,source,captured_graphs,referenced_rows))
    with adapter_errors():
        validate(values[0],source=values[1],captured_graphs=values[2],referenced_rows=values[3])
    return Snapshot(*values,tuple(sorted(set(authority_transactions))))

def account_population(s,account,cutoff):
    require(authority_required(s.rows,s.source,s.captured_graphs)<=set(s.authority_transactions),'E_PERMISSION')
    with adapter_errors():
        a=s.source.accounts[account]
        home=s.referenced_rows['company_info'][0]['home_currency']
        require(a['type'] in ('bank','credit_card') and a['currency']==home,'E_RECONCILIATION_UNSUPPORTED')
        history,current=adapters.enumerate_graph(s.source)
        total,gl=prove(s.source,history,current,account,cutoff)
        values=tuple(v for v in s.current.values() if v['account_id']==account and v['active'])
        require(sum(v['signed_debit'] for v in values if v['effective_date']<=cutoff)==total,'E_RECONCILIATION_SOURCE_INVALID')
        return values,(-gl if a['type']=='credit_card' else gl)

def statement_amount(v):return -v['signed_debit'] if v['account_type']=='credit_card' else v['signed_debit']

def groups(values):
    result=defaultdict(list)
    for v in values:result[v['movement_snapshot']].append(v)
    return {k:tuple(sorted(v,key=lambda r:r['key_id'])) for k,v in result.items()}

def group_fingerprint(values):
    return digest([dict(key_id=v['key_id'],version_id=v['id'],movement=json.loads(v['movement_snapshot'])) for v in sorted(values,key=lambda r:r['key_id'])])

def whole_selection(s,draft, *, current=True):
    require(all(v.action in (('covered','outstanding') if draft.kind=='opening' else ('mark',)) for v in draft.selections),'E_RECONCILIATION_MANIFEST')
    versions=s.versions;chosen={v.key_id:v for v in draft.selections}
    require(all(v.version_id in versions and versions[v.version_id]['key_id']==v.key_id and versions[v.version_id]['account_id']==draft.account_id for v in chosen.values()),'E_RECONCILIATION_MANIFEST')
    if current:
        require(all(s.current.get(k,{}).get('id')==v.version_id and s.current[k]['active'] for k,v in chosen.items()),'E_RECONCILIATION_SELECTION_STALE')
    selected=[versions[v.version_id] for v in chosen.values()]
    history_groups=groups(v for v in versions.values() if v['active'])
    for group in groups(selected).values():
        complete=history_groups[group[0]['movement_snapshot']]
        require({v['key_id'] for v in group}=={v['key_id'] for v in complete},'E_RECONCILIATION_MANIFEST')
        require(len({chosen[v['key_id']].action for v in group})==1,'E_RECONCILIATION_MANIFEST')
    return tuple(selected)

def totals(beginning,ending,values):
    grouped=groups(values)
    amounts=[sum(statement_amount(v) for v in group) for group in grouped.values()]
    positive=sum(v for v in amounts if v>0);negative=sum(v for v in amounts if v<0)
    selected=positive+negative;cleared=beginning+selected
    money=dict(positive_sum=positive,negative_sum=negative,selected_sum=selected,
        beginning_balance=beginning,ending_balance=ending,cleared_balance=cleared,difference=ending-cleared)
    return m.Totals(positive_count=sum(v>0 for v in amounts),negative_count=sum(v<0 for v in amounts),
        **{k:bounded(v) for k,v in money.items()},decimal_units={k:str(v) for k,v in money.items()})

def opening(s,draft):
    validate_evidence(s,draft.evidence_references)
    require(draft.kind=='opening' and draft.state=='open','E_RECONCILIATION_DRAFT_STATE')
    values,gl=account_population(s,draft.account_id,draft.header.opening_date)
    eligible={v['key_id']:v for v in values if v['effective_date']<=draft.header.opening_date}
    selected=whole_selection(s,draft)
    require({v['key_id'] for v in selected}==set(eligible),'E_RECONCILIATION_OPENING_UNPROVEN')
    require(all(v.action in ('covered','outstanding') for v in draft.selections),'E_RECONCILIATION_MANIFEST')
    covered={v.key_id for v in draft.selections if v.action=='covered'}
    require(sum(statement_amount(eligible[k]) for k in covered)==draft.header.entered_balance,'E_RECONCILIATION_OPENING_UNPROVEN')
    require(sum(statement_amount(v) for v in eligible.values())==gl,'E_RECONCILIATION_OPENING_UNPROVEN')
    return totals(0,draft.header.entered_balance,[eligible[k] for k in covered])

def chain(s,account):
    certs=s.by('certificates');active=[certs[v['certificate_id']] for v in s.rows['active_certificates'] if v['account_id']==account]
    return tuple(sorted(active,key=lambda v:v['statement_date']))

def claimed(s):
    claims=s.by('claims')
    return {v['key_id']:claims[v['claim_id']] for v in s.rows['current_members']}

_HEAD = object()

def _statement(s,draft, *, predecessor=_HEAD, released_keys=frozenset(), opening_draft=None, replacement_opening=None):
    require(draft.kind in ('statement','amendment') and draft.state=='open','E_RECONCILIATION_DRAFT_STATE')
    account_population(s,draft.account_id,draft.header.statement_date)
    state=next((v for v in s.rows['accounts'] if v['account_id']==draft.account_id),None)
    require(draft.base_chain_version==(state['version'] if state else 0),'E_RECONCILIATION_CHAIN_STALE')
    if opening_draft is not None:
        require(state is None and opening_draft.account_id==draft.account_id,'E_RECONCILIATION_MANIFEST')
        opening(s,opening_draft);beginning=opening_draft.header.entered_balance;previous_date=opening_draft.header.opening_date
        excluded={v.key_id for v in opening_draft.selections if v.action=='covered'}
    else:
        require(state is not None and state['opening_id']==draft.base_opening_id,'E_RECONCILIATION_CHAIN_STALE')
        base=s.by('openings')[state['opening_id']]
        old=predecessor if predecessor is not _HEAD else (s.by('certificates')[state['head_certificate_id']] if state['head_certificate_id'] else None)
        if draft.kind=='statement':require(draft.base_head_id==state['head_certificate_id'],'E_RECONCILIATION_CHAIN_STALE')
        beginning=old['ending_balance'] if old else base['balance'];previous_date=old['statement_date'] if old else base['opening_date']
        excluded={v['key_id'] for v in s.rows['opening_members'] if v['opening_id']==base['id'] and v['classification']=='covered'}
    if replacement_opening is not None:
        require(replacement_opening.account_id==draft.account_id,'E_RECONCILIATION_MANIFEST')
        opening(s,replacement_opening)
        excluded={v.key_id for v in replacement_opening.selections if v.action=='covered'}
        if predecessor is None:
            beginning=replacement_opening.header.entered_balance;previous_date=replacement_opening.header.opening_date
    require(previous_date<draft.header.statement_date,'E_RECONCILIATION_DATE')
    selected=whole_selection(s,draft)
    require(all(v.action=='mark' for v in draft.selections),'E_RECONCILIATION_MANIFEST')
    require(all(v['effective_date']<=draft.header.statement_date for v in selected),'E_RECONCILIATION_DATE')
    keys={v['key_id'] for v in selected}
    require(not keys&excluded and not keys&(set(claimed(s))-set(released_keys)),'E_RECONCILIATION_MEMBERSHIP_CONFLICT')
    return totals(beginning,draft.header.entered_balance,selected)

def certify(result):
    require(result.difference==0,'E_RECONCILIATION_DIFFERENCE')
    return result

def fingerprint(s,draft):
    # Relevant owned facts only, not an audit watermark or authority credential.
    values,_=account_population(s,draft.account_id,draft.header.statement_date or draft.header.opening_date)
    return digest(dict(draft=draft.model_dump(mode='json'),versions=[v['id'] for v in sorted(values,key=lambda v:v['key_id'])],
        account={k:s.source.accounts[draft.account_id][k] for k in ('id','version','type','currency','active')},proposals=[v for v in s.rows['proposals'] if v['draft_id']==draft.id],chain=chain(s,draft.account_id),claims=[v for k,v in sorted(claimed(s).items()) if k in {v['key_id'] for v in values}]))


def statement(s,draft, *, opening_draft=None):
    """Ordinary statement preparation cannot supply released claims or totals."""
    return _statement(s,draft,opening_draft=opening_draft)


def validate_evidence(s,references):
    require({v.transaction_id for v in references}<=set(s.authority_transactions),'E_PERMISSION')
    transactions={v['id'] for v in s.referenced_rows['transactions']}
    attachments={v['id'] for v in s.referenced_rows['attachments']}
    links={v['id']:v for v in s.referenced_rows['attachment_links']}
    for v in references:
        require(v.transaction_id in transactions,'E_RECONCILIATION_MANIFEST')
        if v.kind=='transaction_attachment':
            link=links.get(v.attachment_link_id)
            require(v.attachment_id in attachments and link is not None and
                (link['record_type'],link['record_id'],link['attachment_id'])==('transaction',v.transaction_id,v.attachment_id),'E_RECONCILIATION_MANIFEST')
