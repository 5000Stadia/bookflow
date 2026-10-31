"""Build ordinary Make Deposits inputs from a POS day; never post another receipt."""
import json
import sqlalchemy as sa
from pydantic import Field
from bookflow.company import accounts, pos_layer, pos_sales, schema as c
from bookflow.company.deposit_models import InlineDocument
from bookflow.company.items import MoneyOutput
from bookflow.company.pos_sale_models import _Strict
from bookflow.company.sales_models import Selector
from bookflow.company.journal_models import _Date
from bookflow.core.errors import BookflowError
from bookflow.core.money import Money
from bookflow.core.registry import Applied, Plan, Touched


class PrepareInput(_Strict):
    pos_register: Selector | None = None
    date: _Date | None = None
    shift: str | None = None
    payment_methods: list[Selector] | None = Field(None, description='Methods to bank; omitted selects cash.')


class PrepareOutput(_Strict):
    document: InlineDocument
    excluded_sources: list[str]
    cash_refunds: MoneyOutput
    warning: str


def prepare(s, ctx, inp):
    from bookflow.company import pos_day
    from bookflow.company.pos_day_models import DayInput
    from bookflow.company import deposit_sources, deposit_dependencies
    pos_layer.require_enabled(s)
    report = pos_day.report(s, DayInput(pos_register=inp.pos_register, date=inp.date, shift=inp.shift))
    reg = pos_sales.register(s, report.pos_register.id)
    if not reg['refund_account_id']:
        raise BookflowError('E_VALIDATION', message='Set the register refund bank account before making a deposit.')
    from bookflow.company import list_service
    from bookflow.company.lists import get_list_definition
    methods = None if inp.payment_methods is None else {list_service.resolve_selector(
        s.company, c.payment_methods, get_list_definition('payment-method'), x)['id'] for x in inp.payment_methods}
    if methods == set():
        raise BookflowError('E_VALIDATION', message='Choose at least one payment method.')
    query = sa.select(c.pos_sales).where(c.pos_sales.c.register_id == reg['id'])
    if inp.shift:
        query = query.where(c.pos_sales.c.shift_id == inp.shift)
    sources, excluded, cash_ids, all_cash_ids = [], [], [], []
    source_dates = []
    for row in s.company.conn.execute(query).mappings():
        tx = s.company.conn.execute(sa.select(c.transactions).where(c.transactions.c.id == row['document_id'])).mappings().one()
        rev = s.company.conn.execute(sa.select(c.transaction_revisions).where(c.transaction_revisions.c.id == tx['current_revision_id'])).mappings().one()
        if not inp.shift and rev['date'] != report.date:
            continue
        if tx['status'] != 'posted':
            continue
        tenders = json.loads(row['tenders'])
        if row['document_type'] == 'sales_receipt' and rev['revision_number'] > 1:
            snapshot = s.company.conn.execute(sa.select(c.sales_profiles.c.profile_snapshot).where(
                c.sales_profiles.c.revision_id == rev['id'])).scalar_one()
            method_id = json.loads(snapshot)['payment_method']['id']
            kind = s.company.conn.execute(sa.select(c.payment_methods.c.kind).where(
                c.payment_methods.c.id == method_id)).scalar_one()
            kind = next((t['kind'] for t in tenders if t['payment_method_id'] == method_id), kind)
            tenders = [dict(payment_method_id=method_id, kind=kind)]
        # Single receipts today. Split sales expose payment ids in their tender facts.
        ids = [(t.get('payment_id'), t) for t in tenders] if row['document_type'] == 'invoice' else [(row['document_id'], tenders[0])]
        for identity, tender in ids:
            if tender['kind'] == 'cash':
                all_cash_ids.append(identity)
            if not (tender['kind'] == 'cash' if methods is None else tender['payment_method_id'] in methods):
                continue
            if not identity:
                raise BookflowError('E_WORK_DEPENDENCY', message='This split sale has no captured payment identity; use Make Deposits to select its payments.')
            if tender['kind'] == 'cash':
                cash_ids.append(identity)
            if deposit_dependencies.active_claims(s, [identity]):
                excluded.append(identity)
                continue
            try:
                source = deposit_sources.load(s, identity)
            except BookflowError as error:
                if error.code != 'E_DEPOSIT_SOURCE_INELIGIBLE':
                    raise
                excluded.append(identity)
                continue
            source_dates.append(source.receipt_date)
            sources.append(dict(source_type=source.source_type, source=identity, expected_version=source.expected_header_version))
    if not sources:
        raise BookflowError('E_WORK_DEPENDENCY', message='No undeposited receipts remain for these methods and this day or shift. Open Make Deposits to review existing deposits.')
    # A shift variance belongs to the cash bundle once. Refuse ambiguous partial hand-offs:
    # the normal deposit claim guard then prevents two prepared copies banking that bundle.
    variance = sum(x.over_short.minor_units for x in report.shifts if x.over_short is not None) if cash_ids else 0
    if variance and not inp.shift and any(
            pos_day._local_day(s, x.opened_at) != report.date or
            (x.closed_at and pos_day._local_day(s, x.closed_at) != report.date) for x in report.shifts):
        raise BookflowError('E_WORK_DEPENDENCY', message='This drawer difference spans dates. Choose Deposit this shift to include the entire shift.')
    if variance and set(cash_ids) != set(all_cash_ids):
        raise BookflowError('E_WORK_DEPENDENCY', message='Select every cash method to hand off this drawer difference once.')
    if variance and any(x.closed_at is None for x in report.shifts):
        raise BookflowError('E_WORK_DEPENDENCY', message='Close all shifts for the day before handing off a drawer difference.')
    if variance and set(cash_ids).intersection(excluded):
        raise BookflowError('E_WORK_DEPENDENCY', message='Part of this cash bundle is already deposited or paid directly to a bank. Review its Cash Over and Short line in Make Deposits before depositing the remainder.')
    mutation = None
    if inp.shift and report.shifts[0].closed_at:
        source_dates.append(pos_day._local_day(s, report.shifts[0].closed_at))
    document = dict(mode='inline', deposit_to=reg['refund_account_id'], date=max([report.date, *source_dates]),
                    memo=f"POS {reg['name']} {report.date}", sources=sources, additional=[])
    if variance:
        account = s.company.conn.execute(sa.select(c.accounts).where(c.accounts.c.full_name_key == 'cash over and short')).mappings().first()
        if account is None:
            mutation = accounts.plan_account_create(s.company, {'name': 'Cash Over and Short', 'type': 'expense'}, actor_id=s.actor.id, via=ctx.interface.value)
            account = mutation.after
        if not account['active'] or account['type'] != 'expense' or account['currency'] != s.company_info_row['home_currency']:
            raise BookflowError('E_VALIDATION', message='Cash Over and Short must be an active expense account in the home currency.')
        amount = Money(abs(variance), s.company_info_row['home_currency']).amount
        if variance > 0:
            document['additional'] = [dict(received_from=dict(kind='customer', id=reg['walk_in_customer_id']),
                from_account=account['id'], amount=amount, memo='Drawer overage')]
        else:
            document['cash_back'] = dict(account=account['id'], amount=amount, memo='Drawer shortage')
    warning = ('Cash refunds already credit the refund bank account. This deposit banks gross receipts plus or minus '
               'the drawer difference; the deposit and refunds together give net cash. Opening float stays in the till. '
               'If you change selected receipts, review the Cash Over and Short amount too; it belongs to the complete bundle.')
    return Plan(PrepareOutput(document=InlineDocument.model_validate(document), excluded_sources=excluded,
                              cash_refunds=report.cash_refunds, warning=warning), {'mutation': mutation})


def apply(plan, ctx, s):
    mutation = plan.data['mutation']
    touched = []
    if mutation:
        accounts.persist_account_mutation(s.company, mutation)
        touched.append(Touched('account', mutation.after['id'], 'create', None, 1, dict(mutation.after), db='company'))
    return Applied(plan.preview, touched, 'prepared POS cash hand-off to Make Deposits')
