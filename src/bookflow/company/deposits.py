"""Pure deposit funding/disposition planner. No public lifecycle or persistence."""
from bookflow.company.deposit_models import Cell,Leg,Effect,Dimensions,ComponentOccurrence
from bookflow.core.errors import BookflowError
from bookflow.core.exact import INT64_MAX

EMPTY=Dimensions(party_kind=None,party_id=None,party_name=None,class_id=None,class_name=None)


def fail(code='E_DEPOSIT_TOTAL'):raise BookflowError(code)


def checked(n):
    if type(n) is not int or abs(n)>INT64_MAX:fail('E_VALUE_RANGE')
    return n


def occurrences(source,previous=()):
    """Preserve present zero identities; removed/reintroduced occurrences append."""
    present=set(source.semantic_presence);positive={c.key for c in source.components}
    maximum=max((o.ordinal for o in previous),default=0)
    result=[o.model_copy(update={'present':o.present and o.key in present}) for o in previous]
    retained={o.key for o in result if o.present}
    for key in sorted(positive-retained,key=lambda k:k.order()):
        maximum+=1;result.append(ComponentOccurrence(key=key,ordinal=maximum,present=True))
    return tuple(result)


def funding(intent):
    cells=[]
    for row in intent.sources:
        orders={o.key:o.ordinal for o in row.occurrences if o.present}
        for component in row.source.components:
            cells.append((row.ordinal,orders[component.key],row.row_id,component.capacity,component.cash))
    cells.extend((row.ordinal,0,row.row_id,row.units,row.dimensions) for row in intent.additional if row.units>0)
    return sorted(cells)


def prepare(intent):
    from bookflow.company.deposit_validation import validate_intent,validate
    validate_intent(intent)
    supplies=funding(intent); capacities=[x[3] for x in supplies]
    p=checked(sum(capacities));s=checked(sum(row.source.cash_minor_units for row in intent.sources))
    t=checked(s+sum(row.units for row in intent.additional));cash=intent.cash_back.units if intent.cash_back else 0
    bank=checked(t-cash)
    if t<=0 or cash>t or bank<0:fail()
    buckets=[]
    if cash:buckets.append(('cash_back',cash,intent.cash_back.account.id))
    buckets.extend(('additional:'+row.row_id,-row.units,row.account.id) for row in sorted(intent.additional,key=lambda x:x.ordinal) if row.units<0)
    if bank:buckets.append(('main_bank',bank,intent.bank.id))
    cells=[];legs=[]
    for name,units,account in buckets:
        total=sum(capacities)
        if units>total:fail()
        if name=='main_bank':allocation=capacities[:]
        else:
            qr=[divmod(units*c,total) for c in capacities];allocation=[q for q,r in qr]
            for i in sorted(range(len(qr)),key=lambda i:(-qr[i][1],supplies[i][0],supplies[i][1]))[:units-sum(allocation)]:allocation[i]+=1
        for i,n in enumerate(allocation):
            if n:
                row_order,ordinal,rowid,_,dims=supplies[i]
                cells.append(Cell(row_id=rowid,component_ordinal=ordinal,bucket=name,units=n))
                if name.startswith('additional:'):
                    dims=next(r.dimensions for r in intent.additional if name=='additional:'+r.row_id)
                legs.append(Leg(key=f'{name}/{rowid}/{ordinal}',account_id=account,signed_debit=n,currency=intent.currency,dimensions=dims))
                capacities[i]-=n
    if any(capacities):fail()
    for row in intent.sources:
        orders={o.key:o.ordinal for o in row.occurrences if o.present}
        for component in row.source.components:
            legs.append(Leg(key=f'uf/{row.row_id}/{orders[component.key]}',account_id=row.source.uf_account,
                signed_debit=-component.capacity,currency=intent.currency,dimensions=component.cash))
    for row in intent.additional:
        if row.units>0:legs.append(Leg(key='additional:'+row.row_id,account_id=row.account.id,signed_debit=-row.units,currency=intent.currency,dimensions=row.dimensions))
    result=Effect(intent=intent,posting_total=p,subtotal=t,bank_total=bank,cash_back=cash,cells=tuple(cells),legs=tuple(legs))
    validate(result);return result


def inverse(effect,identity):
    """Exact old-date inverse, retaining captured inactive references and provenance."""
    from bookflow.company.deposit_validation import validate
    validate(effect)
    if effect.inverse_of is not None:fail('E_VALIDATION')
    return effect.model_copy(update={'inverse_of':identity,'legs':tuple(leg.model_copy(update={'signed_debit':-leg.signed_debit}) for leg in effect.legs)})
