"""Finance charges: late charges assessed on overdue customer balances by the company's policy (R120).

**The policy** is seven company preferences (`company update --finance-charge-...`), as the anchor keeps
them: an annual interest rate, a minimum charge, a grace period in days, the income account charges are
credited to, whether overdue finance charges are charged again, whether days count from the due date or
the invoice date, and whether charges are marked to be printed. Finance charges are off until the account
and a rate (or a minimum) are set.

**The computation**, for one customer or job as of the assessment date A, document by document over what
the customer still owes on A (each invoice and statement charge at its open balance after every payment
and credit applied to it on or before A):

- a document is overdue when A is more than ``grace`` days past its due date (a statement charge is due
  on its own date). The grace period only decides *whether* a document is charged; the days charged
  still start at the due date, or at the document date when the company counts from the invoice date --
  the anchor's guide counts a Net 30 invoice paid 10 days late as 10 days, or 40 from the invoice date;
- days start at the later of that date and the customer's last finance charge date, so no day is ever
  charged twice;
- interest is ``open balance x rate x days / 365``, summed exactly over the documents as one fraction and
  rounded once to the cent, half to even, the codebase's rounding; the minimum charge then applies;
- earlier finance charges are left out of the balance unless the company charges on overdue charges.

A payment part-way through a period lowers the open balance the whole period is charged on, because the
charge is computed on what is owed on A (the anchor's "overdue balance"), not on a daily balance.
Unapplied payments and credits are not subtracted: the anchor marks such a customer so the payment can be
applied first, and so does ``has_unapplied_credits`` here. There is no per-customer exemption in the
anchor either: a person (or agent) leaves a customer out by not selecting it.

**Assessing** writes one ordinary statement charge per selected customer or job on the Finance Charge
item (set up on first use on the finance charge account), dated A: Accounts Receivable is debited and the
finance charge account credited, and the charge ages, appears on statements and is paid like any other.
A ``finance_charges`` row records that it is one. A customer already charged as of A (or later) is not
charged again, so assessing twice is harmless. A closed period is refused.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date as _date
from typing import Any

import sqlalchemy as sa

from bookflow.company import schema as c
from bookflow.company.finance_charge_models import (
    FinanceChargeAssessOutput, FinanceChargeDocument, FinanceChargeRow,
)
from bookflow.core import clock
from bookflow.core.errors import BookflowError
from bookflow.core.exact import format_percentage_millionths, parse_percentage_millionths, round_ratio_half_even
from bookflow.core.ids import new_id
from bookflow.core.money import Money
from bookflow.core.registry import Applied, Plan, Touched

ITEM_NAME = 'Finance Charge'
ITEM_TYPES = ('other_charge', 'service')
DEFAULT_MEMO = 'Finance charge'
_RATE_DENOMINATOR = 100 * 1_000_000 * 365  # percent, millionths of a percent, days in a year


def _invalid(field: str, problem: str, **details) -> BookflowError:
    return BookflowError('E_VALIDATION', details={'fields': [{'field': field, 'problem': problem}], **details})


def _info(s) -> dict[str, Any]:
    return dict(s.company.conn.execute(sa.select(c.company_info)).mappings().one())


def _money(units: int, currency: str):
    from bookflow.company.ledger_reports import money
    return money(units, currency)


def _units(text: str, currency: str, field: str) -> int:
    from bookflow.company.sales_models import money
    return money(text, currency, field).minor_units


# ---------------------------------------------------------------------------- preferences

def validate_preferences(db, new: dict[str, Any], changed: set[str]) -> None:
    """`company update` checks and canonicalises the three finance charge values that are not plain."""
    currency = new['home_currency']
    if 'finance_charge_annual_rate' in changed and new.get('finance_charge_annual_rate') is not None:
        rate = parse_percentage_millionths(new['finance_charge_annual_rate'], field='finance_charge_annual_rate')
        if not 0 <= rate <= 100_000_000:
            raise _invalid('finance_charge_annual_rate', 'must be a percent from 0 to 100, e.g. 18 for 18% a year')
        new['finance_charge_annual_rate'] = format_percentage_millionths(rate)
    if 'finance_charge_minimum' in changed and new.get('finance_charge_minimum') is not None:
        units = _units(new['finance_charge_minimum'], currency, 'finance_charge_minimum')
        new['finance_charge_minimum'] = Money(units, currency).to_dict()['amount']
    if 'finance_charge_account_id' in changed and new.get('finance_charge_account_id') is not None:
        from bookflow.company.accounts import resolve_account
        account = resolve_account(db, new['finance_charge_account_id'])
        if not account['active']:
            raise BookflowError('E_INACTIVE_REFERENCE', details={'field': 'finance_charge_account_id', 'record_id': account['id']})
        if account['type'] not in ('income', 'other_income'):
            raise _invalid('finance_charge_account_id', f'"{account["full_name"]}" is a {account["type"].replace("_", " ")} '
                           'account; finance charges are credited to an income or other income account')
        new['finance_charge_account_id'] = account['id']


def _policy(s) -> dict[str, Any]:
    info = _info(s)
    currency = info['home_currency']
    rate = parse_percentage_millionths(info['finance_charge_annual_rate']) if info['finance_charge_annual_rate'] else 0
    minimum = _units(info['finance_charge_minimum'], currency, 'finance_charge_minimum') if info['finance_charge_minimum'] else 0
    if info['finance_charge_account_id'] is None or (rate == 0 and minimum == 0):
        raise BookflowError('E_VALIDATION', message='Finance charges are off: set the finance charge preferences first.', details={
            'fields': [{'field': 'finance_charge_account_id' if info['finance_charge_account_id'] is None else 'finance_charge_annual_rate',
                        'problem': 'finance charges are off until the finance charge account and an annual rate (or a minimum charge) are set'}],
            'next': 'bookflow company update --finance-charge-annual-rate 18 --finance-charge-minimum 2.00 '
                    '--finance-charge-account-id "Finance Charge Income"'})
    account = dict(s.company.conn.execute(sa.select(c.accounts).where(c.accounts.c.id == info['finance_charge_account_id'])).mappings().one())
    return dict(rate=rate, rate_text=format_percentage_millionths(rate), minimum=minimum, grace=info['finance_charge_grace_days'],
                calculated_from=info['finance_charge_from'], compound=bool(info['finance_charge_on_overdue_charges']),
                mark_to_print=bool(info['finance_charge_mark_to_print']), account=account, currency=currency,
                closing_date=info['closing_date'])


# ---------------------------------------------------------------------------- facts

def _charges(s) -> list[dict[str, Any]]:
    """Every finance charge whose statement charge is still posted."""
    f, t = c.finance_charges, c.transactions
    return [dict(row) for row in s.company.conn.execute(
        sa.select(f.c.customer_id, f.c.assessment_date, f.c.transaction_id, t.c.number)
        .join(t, t.c.id == f.c.transaction_id).where(t.c.status == 'posted')).mappings()]


def _documents(s, as_of: str) -> list[dict[str, Any]]:
    from bookflow.company import ledger_reports as ledger
    from bookflow.company.receivable_reports import _EFFECTS
    ledger.register_ledger_functions(s.company)
    cursor = s.company.raw.execute(
        _EFFECTS + "SELECT tx, party, net, document_type, document_number, document_date, aging_date "
                   "FROM dated WHERE net!='0' AND party IS NOT NULL ORDER BY aging_date, tx", {'as_of': as_of})
    columns = [col[0] for col in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def _days(later: str, earlier: str) -> int:
    return _date.fromisoformat(later).toordinal() - _date.fromisoformat(earlier).toordinal()


def compute(s, as_of: str, policy: dict[str, Any]) -> list[dict[str, Any]]:
    """One proposal per customer or job with an overdue balance on ``as_of``, in name order."""
    from bookflow.company.ledger_schema import SETTLEABLE_RECEIVABLE_TYPES
    from bookflow.company.party_merges import survivor
    charges = _charges(s)
    charge_ids = {row['transaction_id'] for row in charges}
    last: dict[str, str] = {}
    on_date: dict[str, dict[str, Any]] = {}
    for row in charges:
        party = survivor(s.company, 'customer', row['customer_id'])
        if row['assessment_date'] > last.get(party, ''):
            last[party] = row['assessment_date']
        if row['assessment_date'] >= as_of:
            on_date.setdefault(party, row)
    per: dict[str, dict[str, Any]] = {}
    for doc in _documents(s, as_of):
        net, party = int(doc['net']), doc['party']
        entry = per.setdefault(party, dict(numerator=0, overdue=0, credits=0, documents=[]))
        if net < 0:
            entry['credits'] += -net
            continue
        if doc['document_type'] not in SETTLEABLE_RECEIVABLE_TYPES:
            continue
        is_charge = doc['tx'] in charge_ids
        if is_charge and not policy['compound']:
            continue
        if _days(as_of, doc['aging_date']) <= policy['grace']:
            continue
        start = doc['aging_date'] if policy['calculated_from'] == 'due_date' else doc['document_date']
        if party in last and last[party] > start:
            start = last[party]
        days = _days(as_of, start)
        if days <= 0:
            continue
        entry['numerator'] += net * policy['rate'] * days
        entry['overdue'] += net
        entry['documents'].append(dict(transaction_id=doc['tx'], document_type=doc['document_type'], number=doc['document_number'],
                                       date=doc['document_date'], due_date=doc['aging_date'], open_balance=net,
                                       counted_from=start, days=days, finance_charge=is_charge))
    parties = [party for party, entry in per.items() if entry['documents'] or party in on_date]
    if not parties:
        return []
    customers = {row['id']: dict(row) for row in s.company.conn.execute(
        sa.select(c.customers.c.id, c.customers.c.full_name, c.customers.c.full_name_key, c.customers.c.parent_id,
                  c.customers.c.active).where(c.customers.c.id.in_(parties))).mappings()}
    rows = []
    for party in parties:
        entry, customer = per.get(party, dict(numerator=0, overdue=0, credits=0, documents=[])), customers.get(party)
        if customer is None:
            continue
        computed = round_ratio_half_even(entry['numerator'], _RATE_DENOMINATOR) if entry['documents'] else 0
        if entry['documents'] and computed < policy['minimum']:
            computed = policy['minimum']
        done = on_date.get(party)
        if done is None and computed == 0:
            continue
        rows.append(dict(customer_id=party, customer_label=customer['full_name'], key=customer['full_name_key'],
                         parent_id=customer['parent_id'], active=bool(customer['active']),
                         status='already_assessed' if done else 'will_assess',
                         overdue=entry['overdue'], last=last.get(party), computed=computed, charge=computed,
                         credits=entry['credits'], documents=entry['documents'],
                         transaction_id=done['transaction_id'] if done else None, number=done['number'] if done else None))
    rows.sort(key=lambda row: (row['key'], row['customer_id']))
    return rows


def _fingerprint(as_of: str, policy: dict[str, Any], rows: list[dict[str, Any]]) -> str:
    facts = dict(date=as_of, rate=policy['rate'], minimum=policy['minimum'], grace=policy['grace'],
                 calculated_from=policy['calculated_from'], compound=policy['compound'], account=policy['account']['id'],
                 rows=[[row['customer_id'], row['status'], row['overdue'], row['computed'], row['last'], row['credits']] for row in rows])
    return hashlib.sha256(json.dumps(facts, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _select(s, inp, rows: list[dict[str, Any]], currency: str) -> None:
    """Mark which proposals are assessed, and at what amount."""
    from bookflow.company.parties import resolve_party
    from bookflow.company.party_merges import survivor
    by_id = {row['customer_id']: row for row in rows}
    if inp.customers is None:
        for row in rows:
            if row['status'] == 'will_assess' and not row['active']:
                row['status'] = 'not_selected'
        return
    chosen = {}
    for index, choice in enumerate(inp.customers):
        party = survivor(s.company, 'customer', resolve_party(s.company, 'customer', choice.customer)['id'])
        row = by_id.get(party)
        if row is None:
            raise _invalid(f'customers.{index}.customer', f'"{choice.customer}" has no balance overdue past the grace period '
                           'on this date, so there is no finance charge to assess')
        if party in chosen:
            raise _invalid(f'customers.{index}.customer', 'name each customer once')
        chosen[party] = choice
        if choice.amount is not None and row['status'] == 'will_assess':
            units = _units(choice.amount, currency, f'customers.{index}.amount')
            if units <= 0:
                raise _invalid(f'customers.{index}.amount', 'a finance charge must be above 0.00; leave the customer out instead')
            row['charge'] = units
    for row in rows:
        if row['status'] == 'will_assess' and row['customer_id'] not in chosen:
            row['status'] = 'not_selected'


# ---------------------------------------------------------------------------- the item

def _item(s, account: dict[str, Any]) -> tuple[str | None, str]:
    """The item charges are written on: an item on the finance charge account, the anchor's own name first."""
    items = c.items
    found = s.company.conn.execute(sa.select(items.c.id, items.c.full_name).where(
        items.c.income_account_id == account['id'], items.c.type.in_(ITEM_TYPES), items.c.active.is_(True),
        items.c.sales_enabled.is_(True), items.c.other_charge_percent_millionths.is_(None))
        .order_by(items.c.full_name != ITEM_NAME, items.c.full_name)).mappings().first()
    if found is not None:
        return found['id'], found['full_name']
    from bookflow.company.lists import normalize_display_name
    _, key = normalize_display_name(ITEM_NAME)
    taken = s.company.conn.execute(sa.select(items.c.id).where(items.c.full_name_key == key)).first()
    if taken is not None:
        raise _invalid('finance_charge_account_id', f'the "{ITEM_NAME}" item does not post to "{account["full_name"]}", the finance '
                       f'charge account; point the item at that account (`item update "{ITEM_NAME}" --income-account-id ...`) '
                       'or create an Other Charge item that does')
    return None, f'{ITEM_NAME} (set up with this write)'


def _create_item(s, ctx, account: dict[str, Any], at: str):
    from bookflow.company import items as item_service
    code = s.company.conn.execute(sa.select(c.sales_tax_codes.c.id).where(
        c.sales_tax_codes.c.taxable.is_(False), c.sales_tax_codes.c.active.is_(True)).order_by(c.sales_tax_codes.c.code)).scalar()
    payload = {'name': ITEM_NAME, 'type': 'other_charge', 'sales_enabled': True, 'description': DEFAULT_MEMO,
               'income_account_id': account['id'], 'price': '0.00'}
    if code is not None:
        payload['sales_tax_code_id'] = code
    mutation = item_service.plan_item_create(s.company, payload, actor_id=s.actor.id, via=ctx.interface.value, at=at)
    item_service.persist_item_mutation(s.company, mutation)
    return mutation.after['id'], Touched(item_service.ITEM_RECORD_TYPE, str(mutation.after['id']), mutation.action, None,
                                         int(mutation.after['version']), mutation.after_snapshot, mutation.before_snapshot, db='company')


# ---------------------------------------------------------------------------- assess

def _output(as_of, policy, rows, *, mark_to_print, item_label, fingerprint, written: bool) -> FinanceChargeAssessOutput:
    currency = policy['currency']
    charged = [row for row in rows if row['status'] in ('will_assess', 'assessed')]
    total = sum(row['charge'] for row in charged)
    again = sum(row['status'] == 'already_assessed' for row in rows)
    verb = 'Assessed' if written else 'Would assess'
    summary = (f"{verb} {len(charged)} finance charge{'s' if len(charged) != 1 else ''} totalling "
               f"{Money(total, currency).to_dict()['amount']} as of {as_of}"
               + (f"; {again} already charged as of this date (or later) and not charged again" if again else '') + '.')
    out_rows = [FinanceChargeRow(
        customer_id=row['customer_id'], customer_label=row['customer_label'], parent_id=row['parent_id'], status=row['status'],
        overdue_balance=_money(row['overdue'], currency), last_finance_charge_date=row['last'],
        computed=_money(row['computed'], currency), charge=_money(row['charge'], currency),
        unapplied_credits=_money(row['credits'], currency), has_unapplied_credits=row['credits'] > 0,
        transaction_id=row['transaction_id'], number=row['number'],
        documents=[FinanceChargeDocument(**{**doc, 'open_balance': _money(doc['open_balance'], currency)}) for doc in row['documents']])
        for row in rows]
    return FinanceChargeAssessOutput(
        date=as_of, annual_rate=policy['rate_text'], minimum=_money(policy['minimum'], currency), grace_days=policy['grace'],
        calculated_from=policy['calculated_from'], on_overdue_charges=policy['compound'], mark_to_print=mark_to_print,
        account=policy['account']['full_name'], item=item_label, currency=currency, rows=out_rows,
        assessed_count=len(charged), total=_money(total, currency), facts_fingerprint=fingerprint, summary=summary)


def prepare(s, ctx, inp) -> Plan:
    from bookflow.company.journals import open_dates
    from bookflow.company.memorized_schedule import today
    policy = _policy(s)
    as_of = inp.date or today(s.company_tz)
    open_dates(s, [as_of])
    rows = compute(s, as_of, policy)
    fingerprint = _fingerprint(as_of, policy, rows)
    if inp.expected_facts_fingerprint is not None and inp.expected_facts_fingerprint != fingerprint:
        raise BookflowError('E_PREVIEW_STALE', details={'reason': 'finance_charge_facts', 'current_facts_fingerprint': fingerprint})
    _select(s, inp, rows, policy['currency'])
    _, item_label = _item(s, policy['account'])
    mark = policy['mark_to_print'] if inp.mark_to_print is None else inp.mark_to_print
    output = _output(as_of, policy, rows, mark_to_print=mark, item_label=item_label, fingerprint=fingerprint, written=False)
    return Plan(output, dict(input=inp, fingerprint=fingerprint))


def apply(plan: Plan, ctx, s) -> Applied:
    from bookflow.company import document_effects as effects, sales
    from bookflow.company.sales_models import StatementChargePostInput
    from bookflow.core import audit
    inp = plan.data['input']
    fresh = prepare(s, ctx, inp)
    if fresh.data['fingerprint'] != plan.data['fingerprint']:
        raise BookflowError('E_PREVIEW_STALE', details={'reason': 'finance_charge_facts', 'current_facts_fingerprint': fresh.data['fingerprint']})
    policy = _policy(s)
    out = fresh.preview
    as_of, currency, mark = out.date, policy['currency'], out.mark_to_print
    at, event = clock.now_iso(), new_id()
    touched: list[Touched] = []
    item_id, item_label = _item(s, policy['account'])
    selected = [row for row in out.rows if row.status == 'will_assess']
    if item_id is None and selected:
        item_id, created = _create_item(s, ctx, policy['account'], at)
        item_label = ITEM_NAME
        touched.append(created)
    records, rows = [], []
    for row in out.rows:
        if row.status != 'will_assess':
            rows.append(row)
            continue
        charge = StatementChargePostInput.model_validate(dict(
            date=as_of, customer=row.customer_id, item=item_id, amount=row.charge.amount,
            description=inp.memo or DEFAULT_MEMO))
        step = sales.prepare(s, ctx, charge, 'statement_charge', 'post')
        written = sales.apply(step, ctx, s).output
        basis = [doc.model_dump(mode='json') for doc in row.documents]
        record = dict(id=new_id(), transaction_id=written.id, customer_id=row.customer_id, assessment_date=as_of,
                      overdue_balance_minor_units=row.overdue_balance.minor_units, computed_minor_units=row.computed.minor_units,
                      amount_minor_units=row.charge.minor_units, currency=currency, annual_rate=policy['rate_text'],
                      grace_days=policy['grace'], calculated_from=policy['calculated_from'], on_overdue_charges=policy['compound'],
                      to_be_printed=mark, basis=json.dumps(basis, sort_keys=True, separators=(',', ':')),
                      created_at=at, created_by=s.actor.id, created_via=ctx.interface.value, audit_event_id=event)
        records.append(record)
        touched.append(Touched('finance_charge', record['id'], 'create', None, 1, effects.decoded(record), db='company'))
        rows.append(row.model_copy(update=dict(status='assessed', transaction_id=written.id, number=written.number)))
    output = out.model_copy(update=dict(rows=rows, item=item_label, summary=out.summary.replace('Would assess', 'Assessed', 1)))
    if not records:
        return Applied(output, [], 'no finance charges to assess')
    summary = f"assessed {len(records)} finance charge{'s' if len(records) != 1 else ''} as of {as_of}"
    audit.write_event_to(s.company, ctx, 'finance-charge assess', summary, touched, actor_id=s.actor.id,
                         actor_kind=s.actor.kind, directive_code=getattr(s, 'directive_code', None), event_id=event)
    s.company.conn.execute(c.finance_charges.insert(), records)
    return Applied(output, touched, summary, audited=True)
