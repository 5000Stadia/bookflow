"""Private complete deposit report: admitted reader facts, exact arithmetic,
scoped optional UF and company-keyed continuation. No public registration.
"""
from bookflow.company.deposit_report_models import (
    AccountRoleTotal, BankRoleMovement, Composition, CompleteRelation, DepositDetailFilter, MovementTotals,
    Population, ReportDeposit, ReportRow, ReportTotals, ScopedAmount,
)
from bookflow.core.errors import BookflowError
from bookflow.core.exact import _require_i64


def _invalid():
    raise BookflowError('E_DEPOSIT_SOURCE_INVALID')


def _composition(effect, sign=1):
    values = dict(
        source=sum(r.source.cash_minor_units for r in effect.intent.sources),
        positive_additional=sum(max(0, r.units) for r in effect.intent.additional),
        negative_additional=sum(min(0, r.units) for r in effect.intent.additional),
        posting_total=effect.posting_total, subtotal=effect.subtotal,
        bank_total=effect.bank_total, cash_back=effect.cash_back,
    )
    return Composition(**{k: _require_i64(sign*v, field=k) for k, v in values.items()})


def _sum(rows):
    # Do not range-check prefixes: signed cancellation is exact before output.
    return Composition(**{
        key: _require_i64(sum(getattr(r.composition, key) for r in rows), field=key)
        for key in Composition.model_fields
    })


def aggregate(deposits: tuple[ReportDeposit, ...], inp: DepositDetailFilter,
              *, currency: str, destination_id: str | None) -> CompleteRelation:
    """Aggregate all selected rows before any future transport page is sliced.

    destination_id is the read owner's resolved selector, not a new lookup.
    This function does not certify the input population's completeness/rights.
    """
    statuses = inp.statuses()
    if (inp.deposit_to is None) != (destination_id is None):
        _invalid()
    if len({d.id for d in deposits}) != len(deposits):
        _invalid()
    population = Population(projection=inp.projection, destination_id=destination_id,
                            current_status_filter=inp.status)
    all_rows = []
    for document in deposits:
        revisions = {r.revision_id: r for r in document.revisions}
        batches = {b.id: b for b in document.batches}
        if len(revisions) != len(document.revisions) or len(batches) != len(document.batches):
            _invalid()
        if document.current_revision_id not in revisions:
            _invalid()
        # Structural conversion checks are not the owner's independent validator.
        for revision in revisions.values():
            if revision.effect.inverse_of is not None or revision.effect.intent.deposit_id != document.id or revision.effect.intent.currency != currency:
                _invalid()
        for batch in batches.values():
            if batch.revision_id not in revisions:
                _invalid()
            if batch.reverses_batch_id:
                target = batches.get(batch.reverses_batch_id)
                if target is None or target.kind == 'reversal' or target.revision_id != batch.revision_id:
                    _invalid()
            if batch.replaces_batch_id and batch.replaces_batch_id not in batches:
                _invalid()
        if document.status not in statuses:
            continue
        selected = (None,) if inp.projection == 'current' else document.batches
        for batch in selected:
            revision = revisions[document.current_revision_id if batch is None else batch.revision_id]
            effect = revision.effect
            bank = effect.intent.bank
            if destination_id is not None and bank.id != destination_id:
                continue
            sign = -1 if batch is not None and batch.kind == 'reversal' else 1
            roles = []
            seen = set()
            for role in revision.bank_effects:
                if role.transaction_id != document.id or role.currency != currency or role.identity in seen:
                    _invalid()
                seen.add(role.identity)
                if role.active and role.signed_debit:
                    roles.append(BankRoleMovement(role=role.role, row_id=role.row_id,
                        account_id=role.account_id,
                        signed_debit=_require_i64(sign*role.signed_debit, field='signed_debit'),
                        statement_amount=_require_i64(sign*role.statement_amount, field='statement_amount'),
                        kind='captured_business_role' if batch is None else 'accounting_movement'))
            all_rows.append(ReportRow(
                currency=currency, transaction_id=document.id, revision_id=revision.revision_id,
                current_revision_id=document.current_revision_id, current_status=document.status,
                number=revision.number, date=effect.intent.date if batch is None else batch.effective_date,
                destination_id=bank.id, destination_name=bank.name, batch=batch,
                composition=_composition(effect, sign), bank_roles=tuple(roles), source_count=len(effect.intent.sources),
                additional_count=len(effect.intent.additional), cell_count=len(effect.cells),
                effective_current_bank_total=(effect.bank_total if document.status == 'posted' else 0)
                    if batch is None else None,
            ))
    all_rows.sort(key=lambda r: (r.date, r.transaction_id.encode('utf-8'),
                                b'' if r.batch is None else r.batch.id.encode('utf-8')))
    rows = tuple(r for r in all_rows if inp.date_from <= r.date <= inp.date_to)
    movement = None
    if inp.projection == 'effective':
        def scoped(value):
            return ScopedAmount(currency=currency, population=population,
                                minor_units=_require_i64(value, field='movement'))
        opening = sum(r.composition.bank_total for r in all_rows if r.date < inp.date_from)
        period = sum(r.composition.bank_total for r in rows)
        closing = sum(r.composition.bank_total for r in all_rows if r.date <= inp.date_to)
        movement = MovementTotals(opening=scoped(opening), period=scoped(period), closing=scoped(closing))
    role_sums = {}
    for row in rows:
        for role in row.bank_roles:
            values = role_sums.setdefault((role.account_id, role.role), [0, 0])
            values[0] += role.signed_debit
            values[1] += role.statement_amount
    def role_amount(value):
        return ScopedAmount(currency=currency, population=population,
                            minor_units=_require_i64(value, field='bank_role_total'))
    roles = tuple(AccountRoleTotal(account_id=account, role=role,
                  signed_debit=role_amount(values[0]), statement_amount=role_amount(values[1]))
                  for (account, role), values in sorted(role_sums.items()))
    return CompleteRelation(rows=rows, totals=ReportTotals(
        currency=currency, population=population, row_count=len(rows),
        deposit_count=len({r.transaction_id for r in rows}),
        source_count=sum(r.source_count for r in rows),
        additional_count=sum(r.additional_count for r in rows), cell_count=sum(r.cell_count for r in rows),
        composition=_sum(rows), movement=movement, account_roles=roles,
        effective_current_bank_total=_require_i64(sum(r.effective_current_bank_total for r in rows),field='effective_current_bank_total') if inp.projection=='current' else None,
    ))


def _convert(data):
    from bookflow.company.deposit_report_models import ReportRevision, ReportBatch
    from bookflow.company.bank_effects import BankEffect
    keys={r['id']:r for r in data.graph['bank_effect_keys']}
    revisions=[]
    for sel in data.selected.values():
        rid=sel.pin.revision_id
        roles=[]
        for value in data.graph['bank_effect_versions']:
            if value['revision_id']!=rid or not value['active']:continue
            key=keys[value['key_id']]
            roles.append(BankEffect(transaction_id=data.header['id'],role=key['role'],row_id=key['row_id'],
                account_id=value['account_id'],active=True,signed_debit=value['signed_debit'],statement_amount=value['statement_amount'],date=value['effective_date'],currency=value['currency']))
        revisions.append(ReportRevision(revision_id=rid,number=sel.number,effect=data.effects[rid],bank_effects=tuple(sorted(roles,key=lambda r:r.identity))))
    return ReportDeposit(id=data.header['id'],current_revision_id=data.header['current_revision_id'],status=data.header['status'],revisions=tuple(revisions),
        batches=tuple(ReportBatch(id=b['id'],revision_id=b['revision_id'],kind=b['kind'],effective_date=b['effective_date'],
                                 reverses_batch_id=b['reverses_batch_id'],replaces_batch_id=b['replaces_batch_id']) for b in data.graph['posting_batches']))


def _runtime(s,inp,*,binding):
    import sqlalchemy as sa
    from bookflow.company import schema as c, deposit_read_authority as authority, deposit_read_facts as facts
    from bookflow.company import deposit_report_models as m, deposit_queries as queries
    authority.authenticate(s,binding)
    inp.statuses()
    bank=None
    if inp.deposit_to:
        from bookflow.company.accounts import resolve_account
        bank=resolve_account(s.company,inp.deposit_to)['id']
    ids=tuple(s.company.conn.execute(sa.select(c.transactions.c.id).where(c.transactions.c.type=='deposit').order_by(c.transactions.c.id)).scalars())
    allowed=[]
    for identity in ids:
        try:authority.admit(s,[identity],binding=binding)
        except BookflowError as error:
            if error.code in ('E_PERMISSION','E_COMPANY_NOT_FOUND','E_RECORD_NOT_FOUND'):continue
            raise
        allowed.append(identity)
    loaded=facts.load_complete(s,allowed,binding=binding)
    currency=s.company_info_row['home_currency']
    relation=aggregate(tuple(_convert(d) for d in loaded),inp,currency=currency,destination_id=bank)
    lookup={d.header['id']:d for d in loaded};rows=[]
    for row in relation.rows:
        data=lookup[row.transaction_id]
        sel=next(sel for sel in data.selected.values() if sel.pin.revision_id==row.revision_id)
        batch=next((b for b in data.graph['posting_batches'] if row.batch and b['id']==row.batch.id),None)
        groups=queries.collections(data,sel.pin)
        rows.append(m.RuntimeRow(movement=row,selected=sel,**groups,current_version=data.header['version'],recorded_at=batch['created_at'] if batch else None,
                                 links=data.links,current_references=data.references))
    selected_ids={r.movement.transaction_id for r in rows}
    opening_proof=[]
    if inp.projection=='effective':
        for data in loaded:
            if data.header['status'] not in inp.statuses():continue
            for batch in data.graph['posting_batches']:
                effect=data.effects[batch['revision_id']]
                if batch['effective_date']<inp.date_from and (bank is None or effect.intent.bank.id==bank):
                    selected_ids.add(data.header['id'])
                    opening_proof.append([data.header['id'],batch['id'],batch['revision_id'],batch['kind'],batch['effective_date'],effect.model_dump(mode='json')])
    evidence=tuple(d.evidence for d in loaded if d.header['id'] in selected_ids)
    uf=m.UFUnavailable(reason='not_requested');uf_proof=None
    if inp.include_uf_bridge:
        from bookflow.company.deposit_report_uf import _load
        uf,uf_evidence,uf_proof=_load(s,m.DepositReportPeriod(date_from=inp.date_from,date_to=inp.date_to),binding=binding)
        evidence+=uf_evidence
    contract=inp.model_dump(mode='json',exclude={'limit','cursor'},exclude_unset=True)
    # No clock or raw audit watermark. Full rows/composition/totals enter before
    # slicing. UF proof exists only for a completely entitled bridge.
    content=[contract,bank,[s.company_row['id'],s.company_row['display_name'],s.company_info_row['legal_name'],currency],[r.model_dump(mode='json') for r in rows],relation.totals.model_dump(mode='json'),uf.model_dump(mode='json',exclude={'evidence'}),uf_proof,sorted(opening_proof,key=lambda r:(r[0],r[1]))]
    return tuple(rows),relation.totals,uf,evidence,content,lookup


def _metadata(s,inp,fp):
    from bookflow.company import deposit_report_models as m, deposit_queries as queries
    at=queries.now()
    return m.ReportMetadata(company_id=s.company_row['id'],display_name=s.company_row['display_name'],legal_name=s.company_info_row['legal_name'],currency=s.company_info_row['home_currency'],
        period=m.DepositReportPeriod(date_from=inp.date_from,date_to=inp.date_to),projection=inp.projection,
        schema_revision=s.company.raw.execute('SELECT version_num FROM alembic_version').fetchone()[0],
        knowledge_observed_at=at,generated_at=at,cutoff_after_evaluation_date=inp.date_to>at[:10],snapshot_reference=fp)


def detail(s,inp,*,binding):
    from bookflow.company import deposit_report_models as m, deposit_queries as queries, deposit_read_pages as pages
    inp=queries.checked(inp,m.DepositDetailInput)
    rows,totals,uf,evidence,content,_=_runtime(s,inp,binding=binding)
    chunk,fp,next_,previous=pages.page(s,binding,'report.detail',rows,content,inp.limit,inp.cursor)
    return m.DepositDetailPage(metadata=_metadata(s,inp,fp),items=chunk,totals=totals,uf=uf,evidence=evidence,next_cursor=next_,previous_cursor=previous)
