"""Independent validation of the complete private deposit funding graph."""
from fractions import Fraction
from bookflow.company.deposit_models import Intent, Effect
from bookflow.core.errors import BookflowError
from bookflow.core.exact import INT64_MAX
from bookflow.core.money import is_currency


def require(ok):
    if not ok:
        raise BookflowError('E_VALIDATION')


def validate_intent(intent):
    require(type(intent) is Intent)
    from pydantic import ValidationError
    try:
        Intent.model_validate(intent.model_dump(mode='python'))
    except ValidationError:
        require(False)
    require(is_currency(intent.currency))
    bank = intent.bank
    require(bank.active and bank.currency == intent.currency and bank.type == 'bank' and bank.system_role != 'undeposited_funds')
    rows = (*intent.sources, *intent.additional)
    require(bool(rows))
    require(len({r.row_id for r in rows}) == len(rows))
    require(len({r.ordinal for r in rows}) == len(rows))
    require(len({r.source.transaction_id for r in intent.sources}) == len(intent.sources))
    for row in intent.sources:
        src = row.source
        require(src.currency == intent.currency)
        if src.receipt_date > intent.date:
            raise BookflowError('E_DEPOSIT_DATE_BEFORE_SOURCE')
        require(src.uf_account != bank.id)
        require(sum(c.capacity for c in src.components) == src.cash_minor_units)
        keys = [c.key for c in src.components]
        require(len(set(keys)) == len(keys) and set(keys) <= set(src.semantic_presence))
        require(len(set(src.semantic_presence)) == len(src.semantic_presence))
        require(all(k.kind == 'payment' if src.source_type == 'payment' else k.kind in ('sale_net', 'sale_tax') for k in src.semantic_presence))
        require(all((k.tax_item != '') == (k.kind == 'sale_tax') for k in src.semantic_presence))
        active = [o.key for o in row.occurrences if o.present]
        require(len(set(active)) == len(active) and set(keys) <= set(active) <= set(src.semantic_presence))
        require(len({o.ordinal for o in row.occurrences}) == len(row.occurrences))
        require(row.memo_origin != 'source' or row.memo == src.source_memo)
        for component in src.components:
            if src.source_type == 'payment':
                require(component.cash.party_kind == 'customer' and component.cash.party_id == src.profile.payer.id)
                require(component.cash.class_id is None and component.credit_owner_party is not None and component.credit_owner_ar is not None)
            else:
                require(component.cash.party_kind == 'customer' and component.cash.party_id == src.profile.customer.id)
                require(component.sale_line is not None)
                cls = component.sale_line.class_id
                require(component.cash.class_id == (cls.id if cls else None))
                require((component.tax is not None) == (component.key.kind == 'sale_tax'))
    allowed = {'bank','other_current_asset','fixed_asset','other_asset','credit_card','other_current_liability','long_term_liability','equity','income','cost_of_goods_sold','expense','other_income','other_expense'}
    for row in intent.additional:
        require(row.units != 0 and row.dimensions.party_id is not None)
        account = row.account
        require(account.active and account.currency == intent.currency and account.id != bank.id)
        require(account.type in allowed and account.system_role is None)
    if intent.cash_back:
        account = intent.cash_back.account
        require(account.active and account.currency == intent.currency and account.id != bank.id)
        require(account.type in allowed - {'income', 'other_income', 'cost_of_goods_sold'} and account.system_role is None)


def validate(effect):
    """Reconstruct rational quotas and exact dimensional legs, never call planner."""
    require(type(effect) is Effect)
    from pydantic import ValidationError
    try:
        Effect.model_validate(effect.model_dump(mode='python'))
    except ValidationError:
        require(False)
    intent = effect.intent
    validate_intent(intent)
    supplies = {}
    expected_legs = {}
    for row in intent.sources:
        ordering = {o.key: o.ordinal for o in row.occurrences if o.present}
        for component in row.source.components:
            ordinal = ordering[component.key]
            supplies[(row.ordinal, ordinal, row.row_id)] = (component.capacity, component.cash)
            expected_legs[f'uf/{row.row_id}/{ordinal}'] = (row.source.uf_account, -component.capacity, component.cash)
    for row in intent.additional:
        if row.units > 0:
            supplies[(row.ordinal, 1, row.row_id)] = (row.units, row.dimensions)
            expected_legs['additional:' + row.row_id] = (row.account.id, -row.units, row.dimensions)
    p = sum(n for n, _ in supplies.values())
    t = sum(r.source.cash_minor_units for r in intent.sources) + sum(r.units for r in intent.additional)
    cash = intent.cash_back.units if intent.cash_back else 0
    bank = t - cash
    require(0 < p <= INT64_MAX and 0 < t <= INT64_MAX and 0 <= bank <= INT64_MAX and 0 <= cash <= t)
    require((effect.posting_total, effect.subtotal, effect.bank_total, effect.cash_back) == (p, t, bank, cash))
    buckets = []
    if cash:
        buckets.append(('cash_back', cash, intent.cash_back.account.id, None))
    for row in sorted(intent.additional, key=lambda r: r.ordinal):
        if row.units < 0:
            buckets.append(('additional:' + row.row_id, -row.units, row.account.id, row.dimensions))
    if bank:
        buckets.append(('main_bank', bank, intent.bank.id, None))
    remaining = {key: n for key, (n, _) in supplies.items()}
    expected_cells = {}
    for name, size, account, dimensions in buckets:
        total = sum(remaining.values())
        require(size <= total)
        quota = {key: Fraction(size * n, total) for key, n in remaining.items()}
        allocation = {key: value.numerator // value.denominator for key, value in quota.items()}
        priority = sorted(quota, key=lambda key: (-(quota[key] - allocation[key]), key))
        for key in priority[:size - sum(allocation.values())]:
            allocation[key] += 1
        for key, n in allocation.items():
            if n:
                _, ordinal, row_id = key
                expected_cells[(row_id, ordinal, name)] = n
                expected_legs[f'{name}/{row_id}/{ordinal}'] = (account, n, dimensions or supplies[key][1])
                remaining[key] -= n
    require(not any(remaining.values()))
    actual_cells = {(c.row_id, c.component_ordinal, c.bucket): c.units for c in effect.cells}
    require(len(actual_cells) == len(effect.cells) and actual_cells == expected_cells)
    sign = -1 if effect.inverse_of is not None else 1
    actual_legs = {leg.key: (leg.account_id, sign * leg.signed_debit, leg.dimensions) for leg in effect.legs}
    require(len(actual_legs) == len(effect.legs) and actual_legs == expected_legs)
    require(all(leg.currency == intent.currency and 0 < abs(leg.signed_debit) <= INT64_MAX for leg in effect.legs))
    require(sum(leg.signed_debit for leg in effect.legs) == 0)


def validate_current(effect, s, *, replacing_deposit=None):
    """Recheck stored source pins/claims in the caller's current company snapshot.

    Prospective source actions use their owning full validator and G2's aggregate
    comparison instead. This function never treats supplied captured facts as
    proof of current authority or eligibility.
    """
    import sqlalchemy as sa
    from bookflow.company import schema as c, deposit_sources, journals
    validate(effect)
    require(effect.inverse_of is None)
    journals.open_dates(s, [effect.intent.date])
    for row in effect.intent.sources:
        actual=deposit_sources.load(s,row.source.transaction_id)
        if actual.model_dump(mode='json') != row.source.model_dump(mode='json'):
            raise BookflowError('E_PREVIEW_STALE', details={'reason':'deposit_source'})
        claimed=s.company.conn.execute(sa.select(c.deposit_current_memberships).where(
            c.deposit_current_memberships.c.source_transaction_id==actual.transaction_id)).mappings().first()
        if claimed is not None and claimed['transaction_id']!=replacing_deposit:
            raise BookflowError('E_DEPOSIT_SOURCE_CLAIMED')
    accounts=[effect.intent.bank]+[r.account for r in effect.intent.additional]
    if effect.intent.cash_back:accounts.append(effect.intent.cash_back.account)
    for account in accounts:
        actual=s.company.conn.execute(sa.select(c.accounts).where(c.accounts.c.id==account.id)).mappings().first()
        from bookflow.company.sales_defaults import NORMAL_BALANCE
        require(actual is not None and actual['active'])
        require(all(actual[field]==getattr(account,field) for field in ('id','name','full_name','number','type','system_role','currency')))
        require(account.normal_balance==NORMAL_BALANCE[actual['type']] and account.currency==s.company_info_row['home_currency'])
    parties={'customer':c.customers,'vendor':c.vendors,'employee':c.employees,'other_name':c.other_names}
    for row in effect.intent.additional:
        dimensions=row.dimensions
        table=parties[dimensions.party_kind]
        party=s.company.conn.execute(sa.select(table).where(table.c.id==dimensions.party_id)).mappings().first()
        require(party is not None and party['active'] and dimensions.party_name==(party.get('full_name') or party.get('name')))
        if dimensions.class_id is not None:
            cls=s.company.conn.execute(sa.select(c.classes).where(c.classes.c.id==dimensions.class_id)).mappings().first()
            require(cls is not None and cls['active'] and dimensions.class_name==(cls.get('full_name') or cls['name']))
        if row.payment_method is not None:
            method=s.company.conn.execute(sa.select(c.payment_methods).where(c.payment_methods.c.id==row.payment_method.id)).mappings().first()
            require(method is not None and method['active'] and row.payment_method.label==method['name'] and row.payment_method.version==method['version'])
