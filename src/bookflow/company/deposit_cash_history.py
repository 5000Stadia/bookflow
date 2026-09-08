"""Admitted all-history cash supplier for the private UF report.

History owns immutable row/endpoint proof, the source owner owns the positive
cash partition, and the read validator owns exact batch inverses. Current
eligibility is never used to discard a historical receipt.
"""
from dataclasses import dataclass, replace
from pydantic import ValidationError
from bookflow.company import schema as c, deposit_sources as sources
from bookflow.company import deposit_read_authority as authority, deposit_read_validation as validation
from bookflow.company import deposit_dependency_history as history
from bookflow.company.deposit_report_models import UFEvent, Signed
from bookflow.company.deposit_models import Frozen, ID
from bookflow.company.journal_models import _Date
from bookflow.core.errors import BookflowError


class CashHistoryEffect(Frozen):
    transaction_id: ID
    revision_id: ID
    batch_id: ID
    posting_line_id: ID
    attribution_ids: tuple[ID, ...]
    account_id: ID
    effective_date: _Date
    signed_minor_units: Signed


@dataclass(frozen=True)
class ValidatedCashHistory:
    transaction_id: str
    source_type: str
    revision_ids: tuple[str, ...]
    cash_account_ids: tuple[str, ...]
    cash_effects: tuple[CashHistoryEffect, ...]
    uf_effects: tuple[UFEvent, ...]
    evidence: authority.ReadEvidence


def load_cash_history(s, source_ids, *, binding, uf_account):
    """Load a complete caller-discovered source population in the current TX.

    Direct-bank endpoints are validated against their actual captured cash
    account through the source owner's generic partition export. They never
    become a CashSource/UF claim; only canonical-UF lines emit UFEvent values.
    """
    ids=tuple(sorted(set(source_ids)))
    evidence=authority.admit(s,ids,binding=binding)
    headers=authority.select(s,c.transactions,c.transactions.c.id,ids)
    validation.require(len(headers)==len(ids) and all(r['type'] in ('payment','sales_receipt') for r in headers))
    graphs={r['id']:{'header':r,**{name:[] for name in sources.TABLES}} for r in headers}
    # Exact stored rows, not document_effects.rows' omitted-null wire view.
    for name in sources.TABLES:
        table=getattr(c,name)
        for row in authority.select(s,table,table.c.transaction_id,ids):
            graphs[row['transaction_id']][name].append(row)
    reader=history.History(s)
    events={r['audit_event_id'] for g in graphs.values() for name,rows in g.items() if name!='header' for r in rows if 'audit_event_id' in r}
    history._authorize_binding_graph(s,binding,ids,tuple(sorted(events)),write=False)
    event_rows={r['id']:r for r in authority.select(s,c.audit_events,c.audit_events.c.id,events)}
    evidence=replace(evidence,events=tuple(sorted(set(evidence.events)|events)),
                     selected_pins=tuple(sorted((identity,r['id']) for identity,g in graphs.items() for r in g['transaction_revisions'])),
                     observable_fields=('cash_history.revisions','cash_history.uf_effects','cash_history.cash_accounts'))
    result=[]
    try:
        for identity in ids:
            graph=graphs[identity];header=graph['header']
            validation.require(reader.take('transaction',identity) is not None)
            validation.audit_rows(reader,{k:v for k,v in graph.items() if k!='header'})
            validation.posting_lifecycle(graph,header)
            revs=sorted(graph['transaction_revisions'],key=lambda r:r['revision_number'])
            validation.require(revs and header['current_revision_id']==revs[-1]['id'])
            accounts=[];previous=None
            for number,rev in enumerate(revs,1):
                validation.require(rev['revision_number']==number and rev['supersedes_revision_id']==previous)
                previous=rev['id']
                batch=validation.exact_one([b for b in graph['posting_batches'] if b['revision_id']==rev['id'] and b['kind']!='reversal'])
                candidates=[e for e in reader.entries['transaction'][identity]
                            if (history._audit_image(e['after']) or {}).get('current_revision_id')==rev['id']
                            and (history._audit_image(e['after']) or {}).get('status')=='posted']
                validation.require(bool(candidates))
                endpoint=min(candidates,key=lambda e:e['seq'])
                # chain() checks predecessor/commercial ownership at this exact
                # observed endpoint; do not invent a posted historical header.
                cutoff=reader.cutoff
                try:
                    reader.cutoff=endpoint['seq']
                    validation.require(bool(reader.chain('transaction',identity)))
                finally:reader.cutoff=cutoff
                saved=history._audit_image(endpoint['after'])
                profile=validation.exact_one([r for r in graph['payment_profiles' if header['type']=='payment' else 'sales_profiles'] if r['revision_id']==rev['id']])
                cash_account=profile['deposit_account_id' if header['type']=='payment' else 'control_account_id']
                endpoint_graph=dict(graph,header=saved,posting_batches=[batch])
                endpoint_graph['payment_component_keys']=[k for k in graph['payment_component_keys'] if event_rows[k['audit_event_id']]['seq']<=endpoint['seq']]
                source=sources.project_cash(endpoint_graph,cash_account=cash_account,home_currency=s.company_info_row['home_currency'])
                validation.require(source.business_batch_id==batch['id'] and source.revision_id==rev['id'])
                accounts.append(cash_account)
            batches={b['id']:b for b in graph['posting_batches']}
            effects=[];cash_effects=[]
            account_by_revision=dict(zip((r['id'] for r in revs),accounts))
            for line in graph['posting_lines']:
                batch=batches[line['batch_id']]
                if line['account_id']!=account_by_revision[batch['revision_id']]:continue
                cash_effects.append(CashHistoryEffect(transaction_id=identity,revision_id=batch['revision_id'],batch_id=batch['id'],
                    posting_line_id=line['id'],attribution_ids=tuple(sorted(r['id'] for r in graph['posting_line_sources'] if r['posting_line_id']==line['id'])),
                    account_id=line['account_id'],effective_date=batch['effective_date'],signed_minor_units=line['debit_minor_units']-line['credit_minor_units']))
                if line['account_id']!=uf_account:continue
                effects.append(UFEvent(identity=line['id'],transaction_id=identity,batch_id=batch['id'],effective_date=batch['effective_date'],
                    units=line['debit_minor_units']-line['credit_minor_units']))
            result.append(ValidatedCashHistory(identity,header['type'],tuple(r['id'] for r in revs),tuple(accounts),tuple(cash_effects),tuple(effects),evidence))
        validation.require(not reader.unknown)
        return tuple(result)
    except (ValidationError,history.MissingHistory,ValueError,KeyError,TypeError,IndexError) as error:
        raise BookflowError('E_DEPOSIT_SOURCE_INVALID') from error
    except BookflowError as error:
        if error.code in ('E_VALIDATION','E_DEPOSIT_SOURCE_INELIGIBLE'):
            raise BookflowError('E_DEPOSIT_SOURCE_INVALID') from error
        raise
