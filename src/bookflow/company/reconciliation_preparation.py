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
from bookflow.company.reconciliation_storage_validation import validate, canonical, digest, InvalidStorage
from bookflow.company import reconciliation_commands_models as m

# Private domain reasons travel the existing typed pipeline without registering
# public reconciliation codes before activation. Shared codes remain unchanged.
from bookflow.core.errors import BookflowError, ALL_CODES
from contextlib import contextmanager

PRIVATE_REASONS=frozenset({'E_RECONCILIATION_ATTEMPT_STATE','E_RECONCILIATION_CHAIN_STALE','E_RECONCILIATION_DATE','E_RECONCILIATION_DEPENDENCY','E_RECONCILIATION_DIFFERENCE','E_RECONCILIATION_DRAFT_STATE','E_RECONCILIATION_MANIFEST','E_RECONCILIATION_MEMBERSHIP_CONFLICT','E_RECONCILIATION_OPENING_UNPROVEN','E_RECONCILIATION_OPERATION_KEY_REUSED','E_RECONCILIATION_SELECTION_STALE','E_RECONCILIATION_SOURCE_INVALID','E_RECONCILIATION_UNSUPPORTED'})

class ReconciliationError(BookflowError):
    def __init__(self,rule,details=None):
        if rule not in ALL_CODES and rule not in PRIVATE_REASONS:raise ValueError('unknown private reconciliation rule')
        self.rule=rule
        if rule in ALL_CODES:
            super().__init__(rule,details=details)
        else:
            code='E_INTERNAL' if rule=='E_RECONCILIATION_SOURCE_INVALID' else 'E_VALIDATION'
            super().__init__(code,message=rule,details={'reason':rule,**(details or {})})

@contextmanager
def adapter_errors():
    """Convert only the owning adapter's known content/unsupported failures."""
    try:yield
    except adapters.Unsupported as exc:
        raise ReconciliationError('E_RECONCILIATION_UNSUPPORTED') from exc
    except InvalidStorage as exc:
        # A storage rule name is fixed text that names no record, so it is safe to hand back;
        # an adapter's own message is not, so those say only which kind of check refused.
        raise ReconciliationError('E_RECONCILIATION_SOURCE_INVALID',{'check':str(exc)}) from exc
    except adapters.Corrupt as exc:
        raise ReconciliationError('E_RECONCILIATION_SOURCE_INVALID',{'check':'source_corrupt'}) from exc
    except (KeyError,ValueError,TypeError,IndexError,StopIteration) as exc:
        raise ReconciliationError('E_RECONCILIATION_SOURCE_INVALID',{'check':'source_unreadable'}) from exc


def require(condition,code,details=None):
    """Refuse with ``code``; ``details`` may be a callable so a passing check builds nothing."""
    if not condition:raise ReconciliationError(code,details() if callable(details) else details)

# How an account's first reconciliation goes, for the refusals that meet someone on the wrong
# step. The opening is never certified alone: `reconcile finish` writes it together with the
# first statement's certificate (design/architecture.md, the reconcile writes).
FIRST_RECONCILIATION=('An opening is certified together with the first statement, not by itself: run '
    '`reconcile start` with opening_draft_id set to the opening draft and a statement_date after the '
    'opening date, tick the statement with `reconcile mark`, then `reconcile finish` that statement '
    'draft. With no earlier statement, open the opening before the account\'s first movement with '
    'entered_balance 0.00 and tick every movement on the statement, as a first reconciliation '
    'starts from zero.')

# What to do when a statement will not tie (R163). An agent under pressure to finish will find a
# way past a check that only asks for a zero difference -- by posting an entry that makes it tie,
# or by ticking one that does not belong -- unless stopping is a designed, named way out. This is
# that way out, said in the refusal, in the preview and in the help.
STOP_RULE=('If it will not tie, stop: leave this draft open, add a note to it saying what you '
    'checked (`note add reconciliation_draft <draft id> "..."`), and tell the company owner. Never '
    'post, change or tick an entry just to make the difference zero; the owner\'s entries-to-review '
    'list shows entries that do. The open draft and its difference stay on the Overview until '
    'someone finishes it. Only a person, never an agent, may instead finish it with a labelled '
    'adjustment: `reconcile finish` with `adjustment` and a reason posts the exact difference to '
    'Reconciliation Discrepancies.')

def statement_only(draft):
    """Details for a statement step handed a draft it does not take."""
    if draft.state!='open':
        return {'draft_kind':draft.kind,'draft_state':draft.state,'accepts':['statement','amendment'],
                'next':'This draft is '+draft.state+'; start a new one with `reconcile start`.'}
    if draft.kind=='opening':
        return {'draft_kind':'opening','accepts':['statement','amendment'],
                'opening_date':draft.header.opening_date,'next_command':'reconcile start',
                'next_input':{'opening_draft_id':draft.id,'statement_date':'a date after '+draft.header.opening_date},
                'next':FIRST_RECONCILIATION}
    return {'draft_kind':draft.kind,'accepts':['statement','amendment']}

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
        stored=sum(v['signed_debit'] for v in values if v['effective_date']<=cutoff)
        require(stored==total,'E_RECONCILIATION_SOURCE_INVALID',lambda:effects_mismatch(stored,total,cutoff))
        return values,(-gl if a['type']=='credit_card' else gl)

def effects_mismatch(effects,ledger,cutoff):
    """What a caller is told when an account's stored effects and its general ledger disagree."""
    return {'check':'effects_total','as_of':cutoff,'effects_total':effects,'ledger_total':ledger}

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

def _decimal(units,places):
    sign='-' if units<0 else '';units=abs(units)
    return sign+(str(units) if not places else f'{units//10**places}.{units%10**places:0{places}d}')

def opening_unproven(s,draft,check,eligible,covered,gl):
    """What a caller is told when an opening draft does not prove: the books' balance at the opening
    date, the statement opening given, what the covered movements come to, the difference, and the way on."""
    from bookflow.core.money import minor_units_of
    currency=s.source.accounts[draft.account_id]['currency'];places=minor_units_of(currency)
    date=draft.header.opening_date;entered=draft.header.entered_balance
    done=sum(statement_amount(eligible[k]) for k in covered if k in eligible)
    book=sum(statement_amount(v) for v in eligible.values())
    outstanding=sum(statement_amount(eligible[k]) for k in eligible if k not in covered)
    details={'check':check,'opening_date':date,'currency':currency}
    if check=='unplaced_movements':
        placed={v.key_id for v in draft.selections}
        details.update(unplaced=len([k for k in eligible if k not in placed]),
            next='Every movement dated on or before '+date+' must be marked `covered` (the bank had cleared it by the '
            'opening statement) or `outstanding` (it had not): `reconcile candidates` lists them, `reconcile mark` with '
            'action covered or outstanding sets each one, then `reconcile preview` again.')
        return details
    difference=entered-done
    details.update(book_balance=book,book_balance_decimal=_decimal(book,places),
        statement_opening=entered,statement_opening_decimal=_decimal(entered,places),
        covered_total=done,covered_total_decimal=_decimal(done,places),
        outstanding_total=outstanding,outstanding_total_decimal=_decimal(outstanding,places),
        difference=difference,difference_decimal=_decimal(difference,places))
    details['next']=('The movements marked `covered` come to '+_decimal(done,places)+' and the statement opening given is '
        +_decimal(entered,places)+' (the books held '+_decimal(book,places)+' on '+date+'), a difference of '
        +_decimal(difference,places)+'. Start the opening from the last statement the old books reconciled and mark '
        'every item that statement had not cleared `outstanding` (outstanding checks, deposits in transit), the rest '
        '`covered`, until the covered movements equal the statement opening. A difference no outstanding item explains '
        'is the owner\'s to settle with an adjustment; do not post or tick an entry to make it zero. '+STOP_RULE)
    return details

def opening(s,draft):
    validate_evidence(s,draft.evidence_references)
    require(draft.kind=='opening' and draft.state=='open','E_RECONCILIATION_DRAFT_STATE')
    values,gl=account_population(s,draft.account_id,draft.header.opening_date)
    eligible={v['key_id']:v for v in values if v['effective_date']<=draft.header.opening_date}
    selected=whole_selection(s,draft)
    covered={v.key_id for v in draft.selections if v.action=='covered'}
    require({v['key_id'] for v in selected}==set(eligible),'E_RECONCILIATION_OPENING_UNPROVEN',
        lambda:opening_unproven(s,draft,'unplaced_movements',eligible,covered,gl))
    require(all(v.action in ('covered','outstanding') for v in draft.selections),'E_RECONCILIATION_MANIFEST')
    require(sum(statement_amount(eligible[k]) for k in covered)==draft.header.entered_balance,'E_RECONCILIATION_OPENING_UNPROVEN',
        lambda:opening_unproven(s,draft,'covered_total',eligible,covered,gl))
    require(sum(statement_amount(v) for v in eligible.values())==gl,'E_RECONCILIATION_OPENING_UNPROVEN',
        lambda:opening_unproven(s,draft,'ledger_total',eligible,covered,gl))
    return totals(0,draft.header.entered_balance,[eligible[k] for k in covered])

def chain(s,account):
    certs=s.by('certificates');active=[certs[v['certificate_id']] for v in s.rows['active_certificates'] if v['account_id']==account]
    return tuple(sorted(active,key=lambda v:v['statement_date']))

def claimed(s):
    claims=s.by('claims')
    return {v['key_id']:claims[v['claim_id']] for v in s.rows['current_members']}

_HEAD = object()

def _statement(s,draft, *, predecessor=_HEAD, released_keys=frozenset(), opening_draft=None, replacement_opening=None):
    require(draft.kind in ('statement','amendment') and draft.state=='open','E_RECONCILIATION_DRAFT_STATE',lambda:statement_only(draft))
    account_population(s,draft.account_id,draft.header.statement_date)
    state=next((v for v in s.rows['accounts'] if v['account_id']==draft.account_id),None)
    require(draft.base_chain_version==(state['version'] if state else 0),'E_RECONCILIATION_CHAIN_STALE')
    if opening_draft is not None:
        require(state is None and opening_draft.account_id==draft.account_id,'E_RECONCILIATION_MANIFEST')
        opening(s,opening_draft);beginning=opening_draft.header.entered_balance;previous_date=opening_draft.header.opening_date;after='opening date'
        excluded={v.key_id for v in opening_draft.selections if v.action=='covered'}
    else:
        require(state is not None and state['opening_id']==draft.base_opening_id,'E_RECONCILIATION_CHAIN_STALE')
        base=s.by('openings')[state['opening_id']]
        old=predecessor if predecessor is not _HEAD else (s.by('certificates')[state['head_certificate_id']] if state['head_certificate_id'] else None)
        if draft.kind=='statement':require(draft.base_head_id==state['head_certificate_id'],'E_RECONCILIATION_CHAIN_STALE')
        beginning=old['ending_balance'] if old else base['balance'];previous_date=old['statement_date'] if old else base['opening_date'];after='previous statement date' if old else 'opening date'
        excluded={v['key_id'] for v in s.rows['opening_members'] if v['opening_id']==base['id'] and v['classification']=='covered'}
    if replacement_opening is not None:
        require(replacement_opening.account_id==draft.account_id,'E_RECONCILIATION_MANIFEST')
        opening(s,replacement_opening)
        excluded={v.key_id for v in replacement_opening.selections if v.action=='covered'}
        if predecessor is None:
            beginning=replacement_opening.header.entered_balance;previous_date=replacement_opening.header.opening_date;after='opening date'
    require(previous_date<draft.header.statement_date,'E_RECONCILIATION_DATE',lambda:{
        'draft_kind':draft.kind,'statement_date':draft.header.statement_date,'previous_date':previous_date,
        'next':'The statement date must be after '+previous_date+', the '+after
               +('. '+FIRST_RECONCILIATION if opening_draft is not None else '.')})
    selected=whole_selection(s,draft)
    require(all(v.action=='mark' for v in draft.selections),'E_RECONCILIATION_MANIFEST')
    require(all(v['effective_date']<=draft.header.statement_date for v in selected),'E_RECONCILIATION_DATE')
    keys={v['key_id'] for v in selected}
    require(not keys&excluded and not keys&(set(claimed(s))-set(released_keys)),'E_RECONCILIATION_MEMBERSHIP_CONFLICT')
    return totals(beginning,draft.header.entered_balance,selected)

def certify(result,currency=None):
    def details():
        from bookflow.core.money import Money
        shown=(lambda v:Money(v,currency).amount) if currency else str
        return {'difference':shown(result.difference),'cleared_balance':shown(result.cleared_balance),
                'ending_balance':shown(result.ending_balance),'next':STOP_RULE}
    require(result.difference==0,'E_RECONCILIATION_DIFFERENCE',details)
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
