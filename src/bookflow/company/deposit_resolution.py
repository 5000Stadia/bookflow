"""Resolve an ordinary private inline deposit from current company source facts.

No draft/public command or write activation. G2 owns final version checks,
replacement occurrence mapping, dependencies and the one aggregate transaction.
"""
from bookflow.company import sales_defaults as defaults, deposit_sources, deposits, document_effects as effects, journals, schema as c
from bookflow.company.deposit_models import InlineDocument, Account, Additional, CashBack, Intent, SourceRow, Dimensions, amount
from bookflow.core.errors import BookflowError
from bookflow.core.ids import new_id
from bookflow.hub.access import require_resource


def resolve(s, inp: InlineDocument, *, deposit_id):
    if type(inp) is not InlineDocument:
        raise BookflowError('E_VALIDATION')
    require_resource(s,'ledger.post','standard')
    currency=s.company_info_row['home_currency']
    account=lambda selector:resolve_account(s,selector)
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
        additional.append(resolve_additional(s,entry,new_id(),ordinal,None))
    cash=CashBack(account=account(inp.cash_back.account),units=amount(inp.cash_back.amount,currency),memo=inp.cash_back.memo) if inp.cash_back else None
    intent=Intent(deposit_id=deposit_id,date=inp.date,currency=currency,bank=account(inp.deposit_to),sources=tuple(sources),additional=tuple(additional),cash_back=cash)
    effect=deposits.prepare(intent)
    from bookflow.company.deposit_validation import validate_current
    validate_current(effect,s)
    return effect


def resolve_account(s, selector, previous=None):
    raw=defaults._row(s.company,'account',selector,active=False)
    if previous is not None and raw['id']==previous.id:
        return previous
    journals.active(raw,'account')
    return Account(**{k:raw[k] for k in ('id','name','full_name','number','type','system_role','active','currency')},
        normal_balance=defaults.NORMAL_BALANCE[raw['type']])


def resolve_additional(s, value, row_id, ordinal, previous):
    tables={'customer':c.customers,'vendor':c.vendors,'employee':c.employees,'other_name':c.other_names}
    party=effects.rows(s,tables[value.received_from.kind],tables[value.received_from.kind].c.id==value.received_from.id)
    if len(party)!=1:
        raise BookflowError('E_RECORD_NOT_FOUND')
    old=previous.dimensions if previous else None
    retained=old and (old.party_kind,old.party_id)==(value.received_from.kind,value.received_from.id)
    if not retained:journals.active(party[0],value.received_from.kind)
    cls=defaults._row(s.company,'class_id',value.class_id,active=False) if value.class_id is not None else None
    if cls and not (old and cls['id']==old.class_id):journals.active(cls,'class')
    method=defaults._row(s.company,'payment_method',value.payment_method,active=False) if value.payment_method is not None else None
    oldmethod=previous.payment_method if previous else None
    if method and not (oldmethod and method['id']==oldmethod.id):journals.active(method,'payment_method')
    dimensions=Dimensions(party_kind=value.received_from.kind,party_id=party[0]['id'],
        party_name=old.party_name if retained else party[0].get('full_name') or party[0]['name'],
        class_id=cls['id'] if cls else None,
        class_name=old.class_name if cls and old and cls['id']==old.class_id else (cls.get('full_name') or cls['name']) if cls else None)
    return Additional(row_id=row_id,ordinal=ordinal,account=resolve_account(s,value.from_account,previous.account if previous else None),
        units=amount(value.amount,s.company_info_row['home_currency']),dimensions=dimensions,memo=value.memo,check_number=value.check_number,
        payment_method=oldmethod if method and oldmethod and method['id']==oldmethod.id else defaults._ref(method) if method else None)
