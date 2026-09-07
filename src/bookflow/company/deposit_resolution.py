"""Resolve an ordinary private inline deposit from current company source facts.

No draft/public command or write activation. G2 owns final version checks,
replacement occurrence mapping, dependencies and the one aggregate transaction.
"""
from bookflow.company import sales_defaults as defaults, deposit_sources, deposits
from bookflow.company.deposit_models import InlineDocument, Account, Additional, CashBack, Intent, SourceRow, Dimensions, amount
from bookflow.core.errors import BookflowError
from bookflow.core.ids import new_id
from bookflow.hub.access import require_resource


def resolve(s, inp: InlineDocument, *, deposit_id):
    if type(inp) is not InlineDocument:
        raise BookflowError('E_VALIDATION')
    require_resource(s,'ledger.post','standard')
    currency=s.company_info_row['home_currency']
    def account(selector):
        row=defaults._active(defaults._row(s.company,'account',selector,active=False),'account')
        return Account(id=row['id'],name=row['name'],full_name=row['full_name'],number=row['number'],normal_balance=defaults.NORMAL_BALANCE[row['type']],
            type=row['type'],system_role=row['system_role'],active=bool(row['active']),currency=row['currency'])
    sources=[]
    for ordinal,entry in enumerate(inp.sources,1):
        source=deposit_sources.load(s,entry.source)
        if source.source_type!=entry.source_type:
            raise BookflowError('E_DEPOSIT_SOURCE_INELIGIBLE')
        if source.expected_header_version!=entry.expected_version:
            raise BookflowError('E_PREVIEW_STALE',details={'reason':'deposit_source'})
        entered='memo_override' in entry.model_fields_set
        sources.append(SourceRow(row_id=new_id(),ordinal=ordinal,source=source,occurrences=deposits.occurrences(source),
            memo=entry.memo_override if entered else source.source_memo,memo_origin='entered' if entered else 'source'))
    additional=[]
    for ordinal,entry in enumerate(inp.additional,len(sources)+1):
        # An ordinary post cannot adopt another deposit's old line identity.
        if entry.line_id is not None:
            raise BookflowError('E_VALIDATION')
        from bookflow.company import schema as c
        import sqlalchemy as sa
        tables={'customer':c.customers,'vendor':c.vendors,'employee':c.employees,'other_name':c.other_names}
        table=tables[entry.received_from.kind]
        party=s.company.conn.execute(sa.select(table).where(table.c.id==entry.received_from.id)).mappings().first()
        if party is None:raise BookflowError('E_RECORD_NOT_FOUND')
        party=defaults._active(dict(party),entry.received_from.kind)
        cls=defaults._active(defaults._row(s.company,'class_id',entry.class_id,active=False),'class') if entry.class_id is not None else None
        method=defaults._active(defaults._row(s.company,'payment_method',entry.payment_method,active=False),'payment_method') if entry.payment_method is not None else None
        additional.append(Additional(row_id=new_id(),ordinal=ordinal,account=account(entry.from_account),units=amount(entry.amount,currency),
            dimensions=Dimensions(party_kind=entry.received_from.kind,party_id=party['id'],party_name=party.get('full_name',party['name']),
                class_id=cls['id'] if cls else None,class_name=cls.get('full_name',cls['name']) if cls else None),
            memo=entry.memo,check_number=entry.check_number,payment_method=defaults._ref(method) if method else None))
    cash=CashBack(account=account(inp.cash_back.account),units=amount(inp.cash_back.amount,currency),memo=inp.cash_back.memo) if inp.cash_back else None
    intent=Intent(deposit_id=deposit_id,date=inp.date,currency=currency,bank=account(inp.deposit_to),sources=tuple(sources),additional=tuple(additional),cash_back=cash)
    effect=deposits.prepare(intent)
    from bookflow.company.deposit_validation import validate_current
    validate_current(effect,s)
    return effect
