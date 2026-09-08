"""Exact UF equations and admitted complete company control population.

Optional section denial is distinct from base authentication, missing account,
corruption and I/O. Financial history validation stays with its existing owners.
"""
from bookflow.company.deposit_report_models import (
    DepositReportPeriod, UFBridge, UFEvent, UFAmounts,
)
from bookflow.core.errors import BookflowError
from bookflow.core.exact import _require_i64


def bridge(*, currency: str, account_id: str, period: DepositReportPeriod,
           receipts: tuple[UFEvent, ...], memberships: tuple[UFEvent, ...],
           ledger: tuple[UFEvent, ...]) -> UFBridge:
    """Pure calculation; input constructors and this result convey no authority.

    Release effective dates must already have been resolved and validated by
    the supplier through the claim's actual reversal batch, not source_date.
    """
    streams = (receipts, memberships, ledger)
    for stream in streams:
        if len({e.identity for e in stream}) != len(stream):
            raise BookflowError('E_DEPOSIT_SOURCE_INVALID')
    opening = tuple(sum(e.units for e in stream if e.effective_date < period.date_from) for stream in streams)
    closing = tuple(sum(e.units for e in stream if e.effective_date <= period.date_to) for stream in streams)
    def amounts(values):
        r, d, l = values
        values = dict(receipts=r, deposited=d, source_backed=r-d, ledger=l, unexplained=l-(r-d))
        return UFAmounts(**{key: _require_i64(value, field=key) for key, value in values.items()})
    return UFBridge(
        currency=currency, account_id=account_id, period=period,
        opening=amounts(opening), change=amounts(tuple(c-o for o, c in zip(opening, closing))),
        closing=amounts(closing),
        receipt_effect_count=sum(e.effective_date <= period.date_to for e in receipts),
        membership_effect_count=sum(e.effective_date <= period.date_to for e in memberships),
        ledger_line_count=sum(e.effective_date <= period.date_to for e in ledger),
    )


def _load(s,period,*,binding):
    import sqlalchemy as sa
    from dataclasses import replace
    from pydantic import ValidationError
    from bookflow.company import schema as c, deposit_read_authority as authority, deposit_read_facts as facts
    from bookflow.company import deposit_dependency_history as history, deposit_read_validation as validation
    from bookflow.company.deposit_cash_history import load_cash_history
    from bookflow.company.deposit_report_models import UFComplete,UFUnavailable
    # Base admission is outside the section-specific permission handler.
    authority.authenticate(s,binding)
    accounts=list(s.company.conn.execute(sa.select(c.accounts).where(c.accounts.c.system_role=='undeposited_funds')).mappings())
    if not accounts:return UFUnavailable(reason='no_uf_account'),(),None
    validation.require(len(accounts)==1 and accounts[0]['currency']==s.company_info_row['home_currency'])
    account=accounts[0]['id']
    sources=tuple(s.company.conn.execute(sa.select(c.transactions.c.id).where(c.transactions.c.type.in_(('payment','sales_receipt')))).scalars())
    deposits=tuple(s.company.conn.execute(sa.select(c.transactions.c.id).where(c.transactions.c.type=='deposit')).scalars())
    # Discover owner identities before decoding any potentially denied body.
    line_owners=tuple(s.company.conn.execute(sa.select(c.posting_lines.c.transaction_id).where(c.posting_lines.c.account_id==account).distinct()).scalars())
    try:
        evidence=authority.admit(s,set(sources)|set(deposits)|set(line_owners),binding=binding)
        cash=load_cash_history(s,sources,binding=binding,uf_account=account)
        loaded=facts.load_complete(s,deposits,binding=binding)
        ledger_rows=[dict(r) for r in s.company.conn.execute(sa.select(c.posting_lines).where(c.posting_lines.c.account_id==account)).mappings()]
        batch_rows=authority.select(s,c.posting_batches,c.posting_batches.c.id,[r['batch_id'] for r in ledger_rows])
        batches={b['id']:b for b in batch_rows}
        history._authorize_binding_graph(s,binding,line_owners,tuple(sorted({b['audit_event_id'] for b in batch_rows})),write=False)
        evidence=replace(evidence,events=tuple(sorted(set(evidence.events)|{b['audit_event_id'] for b in batch_rows})),observable_fields=('company_uf.receipts','company_uf.memberships','company_uf.ledger'))
        reader=history.History(s)
        validation.audit_rows(reader,{'posting_lines':ledger_rows,'posting_batches':batch_rows})
        receipts=tuple(e for value in cash for e in value.uf_effects)
        memberships=[]
        for data in loaded:
            dbatches={b['id']:b for b in data.graph['posting_batches']}
            for member in data.graph['deposit_memberships']:
                batch=dbatches[member['batch_id']]
                sign=1
                if member['kind']=='release':
                    batch=validation.exact_one([b for b in dbatches.values() if b['reverses_batch_id']==batch['id']])
                    sign=-1
                memberships.append(UFEvent(identity=member['id'],transaction_id=data.header['id'],batch_id=batch['id'],
                                          effective_date=batch['effective_date'],units=sign*member['amount_minor_units']))
        ledger=[]
        for row in ledger_rows:
            batch=batches[row['batch_id']]
            validation.require(batch['transaction_id']==row['transaction_id'] and row['currency']==s.company_info_row['home_currency'])
            ledger.append(UFEvent(identity=row['id'],transaction_id=row['transaction_id'],batch_id=batch['id'],effective_date=batch['effective_date'],units=row['debit_minor_units']-row['credit_minor_units']))
        value=bridge(currency=s.company_info_row['home_currency'],account_id=account,period=period,receipts=receipts,memberships=tuple(memberships),ledger=tuple(ledger))
        proof=[[e.model_dump(mode='json') for e in sorted(stream,key=lambda e:e.identity) if e.effective_date<=period.date_to] for stream in (receipts,memberships,ledger)]
        proofs=(evidence,)+tuple(d.evidence for d in loaded)+tuple(d.evidence for d in cash)
        return UFComplete(data=value,evidence=proofs),proofs,proof
    except BookflowError as error:
        if error.code in ('E_PERMISSION','E_COMPANY_NOT_FOUND'):
            return UFUnavailable(reason='not_authorized'),(),None
        raise
    except (ValidationError,history.MissingHistory,ValueError,KeyError,TypeError,IndexError) as error:
        raise BookflowError('E_DEPOSIT_SOURCE_INVALID') from error


def uf_bridge(s,period,*,binding):
    from bookflow.company.deposit_queries import checked
    return _load(s,checked(period,DepositReportPeriod),binding=binding)[0]
