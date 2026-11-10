"""Posting one paycheck, settling its net pay by one check, voids, and whole-unit reversal (R116 increment 2).

One prepared draft posts once, as one payroll-owned journal entry dated on the actual pay date:

- Dr the wage account for each cash earning;
- Cr each employee tax and deduction to the liability account its item names;
- Dr the employer expense account and Cr the item's liability account for each employer tax or contribution;
- Cr the net-pay account for the net (gross less employee taxes and deductions).

Debits equal credits by construction (gross + employer cost on each side) and the journal writer checks it
again. A zero component posts no leg; a zero net posts no net leg and needs no payment. Net pay is
settled by one check through `company/checks.py` (Dr net pay / Cr bank, full net only), in the same
transaction as the paycheck ("Post and record check") or later with `payroll pay-net`. Both entries
carry a payroll ledger link, so other writers refuse them and their audit events need `payroll.read`.

Every amount is copied from the stored calculation; nothing is recalculated at post, void or reversal.
Voids negate the stored cents (the journal writer's own inverse batch) and append an exact negative
year-to-date event. Ledger memos and line descriptions are neutral (no employee, code or note) and the
general audit event carries a neutral reason; the reason and directive are kept in payroll's own history.
"""
from __future__ import annotations

import json
from contextlib import contextmanager

import sqlalchemy as sa

from bookflow.core.errors import BookflowError
from bookflow.company import batches, journals, payroll as p, payroll_models as m, payroll_owner, schema as c
from bookflow.core.ids import new_id
from bookflow.core.money import Money
from bookflow.core.registry import Applied, Plan
from bookflow.core.session import now_iso

KIND = 'payroll'
PAYCHECK = 'payroll_paycheck'
PAYMENT = 'payroll_net_payment'
NEUTRAL = 'Private payroll write; reason retained in payroll history.'
WORDS = {'cash_earning': 'Payroll wages', 'employee_tax': 'Payroll employee tax withheld',
         'employee_deduction': 'Payroll employee deduction', 'employer_tax': 'Payroll employer tax',
         'employer_contribution': 'Payroll employer contribution', 'net_pay': 'Payroll net pay'}
EMPLOYEE_SIDE = ('employee_tax', 'employee_deduction')
MAX_LEGS = 200  # the shared journal writer's line limit
_CENTS = ('gross', 'employee_tax', 'employee_deduction', 'employer_cost', 'net')


def require(s, *pairs):
    """Payroll's conditional resource rights: one declared `require_resource` site (payroll-post-v1)."""
    from bookflow.hub import access
    for capability, role in pairs:
        access.require_resource(s, capability, role)


def _agent(s, command):
    from bookflow.hub import capabilities
    capabilities.require_company(s, command)


def safe(ctx):
    """The context the ledger and general audit see: neutral reason, no keys, no directive."""
    return ctx.model_copy(update={'reason': NEUTRAL, 'source_ref': None, 'idempotency_key': None,
        'client_name': 'payroll', 'client_host': '', 'client_version': '', 'session_id': '',
        'request_id': new_id(), 'directive_id': None})


@contextmanager
def owned(s):
    """Payroll writing through the shared writers: reserved accounts admitted, no directive in general audit."""
    held = getattr(s, 'directive_code', None)
    try:
        s.directive_code = None
    except AttributeError:
        held = None
    try:
        with payroll_owner.writing():
            yield
    finally:
        if held is not None:
            s.directive_code = held


# ---------------------------------------------------------------- reads

def _row(s, paycheck):
    found = s.company.conn.execute(sa.select(c.payroll_paychecks).where(
        c.payroll_paychecks.c.id == paycheck)).mappings().first()
    if found is None:
        raise BookflowError('E_RECORD_NOT_FOUND', message='Paycheck not found.')
    return dict(found)


def _payment(s, paycheck, status='recorded'):
    found = s.company.conn.execute(sa.select(c.payroll_net_payments).where(
        c.payroll_net_payments.c.paycheck_id == paycheck, c.payroll_net_payments.c.status == status).order_by(
        c.payroll_net_payments.c.created_at.desc())).mappings().first()
    return dict(found) if found else None


def _ytd(s, employee, year):
    t = c.payroll_ytd_events
    row = s.company.conn.execute(sa.select(*(sa.func.coalesce(sa.func.sum(t.c[f'{n}_minor_units']), 0) for n in _CENTS))
        .where(t.c.employee_id == employee, t.c.tax_year == year)).one()
    return dict(zip(_CENTS, row))


def _payment_out(s, payment):
    if payment is None:
        return None
    from bookflow.company import check_numbers, check_printing
    held = check_numbers.current(s, payment['transaction_id'])
    return m.NetPaymentOut(id=payment['id'], check=payment['transaction_id'], account=payment['account_id'],
        date=payment['date'], amount_minor_units=payment['amount_minor_units'], status=payment['status'],
        check_number=held['check_number'] if held else None, printed=check_printing.printed(s, payment['transaction_id']))


def _status(pc, payment):
    if pc['status'] == 'voided':
        return 'voided'
    if pc['net_minor_units'] == 0:
        return 'no_payment_needed'
    return 'payment_recorded' if payment and payment['status'] == 'recorded' else 'payment_not_recorded'


def output(s, pc, components=None, payment=None, ytd=None, directive=None):
    if components is None:
        components = [dict(r) for r in s.company.conn.execute(sa.select(c.payroll_paycheck_components).where(
            c.payroll_paycheck_components.c.paycheck_id == pc['id']).order_by(
            c.payroll_paycheck_components.c.position)).mappings()]
        payment = _payment(s, pc['id']) or _payment(s, pc['id'], 'voided')
        ytd = _ytd(s, pc['employee_id'], pc['tax_year'])
        directive = s.company.conn.execute(sa.select(c.payroll_paycheck_events.c.directive_id).where(
            c.payroll_paycheck_events.c.paycheck_id == pc['id']).order_by(
            c.payroll_paycheck_events.c.version.desc()).limit(1)).scalar()
    debit = sum(x['amount_minor_units'] for x in components if x['debit_account_id'])
    credit = sum(x['amount_minor_units'] for x in components if x['credit_account_id'])
    return m.PaycheckOutput(id=pc['id'], version=pc['version'], status=pc['status'], draft=pc['draft_id'],
        draft_version=pc['draft_version'], employee=pc['employee_id'], pay_date=pc['pay_date'],
        period_start=pc['period_start'], period_end=pc['period_end'], journal=pc['transaction_id'],
        batch=pc['batch_id'], **{f'{n}_minor_units': pc[f'{n}_minor_units'] for n in _CENTS},
        debit_minor_units=debit, credit_minor_units=credit,
        components=[m.ComponentOut(position=x['position'], code=x['code'], kind=x['kind'], method=x['method'],
            amount_minor_units=x['amount_minor_units'], debit_account=x['debit_account_id'],
            credit_account=x['credit_account_id'], payee=x['payee'], rate_millionths=x['rate_millionths'],
            base_minor_units=x['base_minor_units']) for x in components],
        net_status=_status(pc, payment), net_payment=_payment_out(s, payment),
        year_to_date=m.YearToDate(tax_year=pc['tax_year'], **{f'{n}_minor_units': ytd[n] for n in _CENTS}),
        directive=directive)


def show(s, inp):
    return output(s, _row(s, inp.paycheck))


def page(s, inp):
    q = sa.select(c.payroll_paychecks).order_by(c.payroll_paychecks.c.pay_date.desc(),
        c.payroll_paychecks.c.created_at.desc(), c.payroll_paychecks.c.id)
    if inp.employee:
        # A former employee's paychecks stay listed; only new pay needs an active employee.
        q = q.where(c.payroll_paychecks.c.employee_id == p.employee(s, inp.employee, active=False))
    return m.PaycheckListOutput(rows=[output(s, dict(r)) for r in s.company.conn.execute(q.limit(inp.limit)).mappings()])


# ---------------------------------------------------------------- building a paycheck

def _components(payload, calc, config):
    items = {item.code: item for item in payload.items}
    rows = []
    for line in calc.trace:
        item = items.get(line.code) if line.kind != 'cash_earning' else None
        debit = (config.wage_account if line.kind == 'cash_earning'
                 else config.employer_tax_account if line.kind not in EMPLOYEE_SIDE else None)
        rows.append(dict(code=line.code, kind=line.kind, method=line.method, amount_minor_units=line.amount_minor_units,
            debit_account_id=debit, credit_account_id=item.liability_account if item else None,
            payee=item.payee if item else None, rate_millionths=line.rate_millionths, base_minor_units=line.base_minor_units))
    rows.append(dict(code='net', kind='net_pay', method='net', amount_minor_units=calc.net_minor_units,
        debit_account_id=None, credit_account_id=config.net_pay_account, payee=None, rate_millionths=None,
        base_minor_units=None))
    return [dict(row, position=i) for i, row in enumerate(rows, 1)]


def _journal(pay_date, components):
    from bookflow.company.journal_models import JournalLineInput, JournalPostInput, MoneyInput
    lines = []
    for x in components:
        if not x['amount_minor_units']:
            continue
        for side, account in (('debit', x['debit_account_id']), ('credit', x['credit_account_id'])):
            if account:
                lines.append(JournalLineInput(account=account, side=side, description=WORDS[x['kind']],
                    amount=MoneyInput(minor_units=x['amount_minor_units'], currency='USD')))
    if len(lines) < 2:
        p.fail('earnings', 'This paycheck has no amounts to post; enter its earnings and prepare it again.')
    if len(lines) > MAX_LEGS:
        p.fail('items', f'A paycheck posts at most {MAX_LEGS} ledger lines; split its earnings and items over '
                        'two paychecks or combine items that share a liability account.')
    return JournalPostInput(date=pay_date, memo='Payroll paycheck', lines=lines)


def _later(s, pc_employee, year, pay_date, exclude=None):
    t = c.payroll_paychecks
    q = sa.select(t.c.id).where(t.c.employee_id == pc_employee, t.c.tax_year == year, t.c.status == 'posted',
                                t.c.pay_date > pay_date)
    if exclude:
        q = q.where(t.c.id != exclude)
    return s.company.conn.execute(q.limit(1)).scalar()


def _check_plan(s, ctx, pc, net, net_pay_account, check, prefix=''):
    """The ordinary check that settles this net pay, planned by the check writer itself. ``prefix`` names the
    command's own fields (`check_` on `payroll post`)."""
    from bookflow.company import checks
    from bookflow.company.check_models import CheckPostInput
    if check.number is not None and (check.to_be_printed or check.no_number):
        p.fail(prefix + 'number', 'Give a check number, or queue the check to print, or record it with no number: '
                                  'only one of them.')
    if check.to_be_printed and check.no_number:
        p.fail(prefix + 'no_number', 'A check queued to print has a number when printed; leave out no_number.')
    date = check.date or pc['pay_date']
    if date < pc['pay_date']:
        p.fail('date', 'A net-pay check may not be dated before the pay date.')
    if net <= 0:
        p.fail('net_check', 'This paycheck has zero net pay, so no payment is needed or recorded.')
    amount = Money(net, 'USD').amount
    inp = CheckPostInput(account=check.account, pay_to={'name_type': 'employee', 'name_id': pc['employee_id']},
        date=date, amount=amount, memo='Payroll net pay', number=check.number, to_be_printed=check.to_be_printed,
        no_number=check.no_number, expenses=[{'account': net_pay_account, 'amount': amount, 'memo': 'Payroll net pay'}])
    with owned(s):
        return checks.prepare(s, safe(ctx), inp, 'check', 'post')


def _link(transaction_id, role, paycheck):
    return ('payroll_ledger_links', 'payroll_ledger_link', 'id',
            [dict(id=new_id(), transaction_id=transaction_id, role=role, paycheck_id=paycheck)])


def _event(s, ctx, paycheck, action, version):
    s.company.conn.execute(c.payroll_paycheck_events.insert().values(id=new_id(), paycheck_id=paycheck, action=action,
        version=version, reason=ctx.reason, directive_id=ctx.directive_id, actor_id=s.actor.id, created_at=now_iso()))


def _receipt(s, ctx, inp, digest, command, out):
    s.company.conn.execute(c.payroll_operations.insert().values(operation_key=inp.operation_key, actor_id=s.actor.id,
        command=command, input_hash=digest, output=out.model_dump_json(), reason=ctx.reason))


def _defer(s):
    # The paycheck, its link and its journal reference each other inside one transaction.
    s.company.conn.exec_driver_sql('PRAGMA defer_foreign_keys = ON')


def prepare_post(inp, ctx, s):
    prior = p.retry(s, ctx, inp, 'payroll post', m.PaycheckOutput)
    if isinstance(prior, Plan):
        return prior
    config = p.enabled(s)
    draft = p.row(s, inp.draft)
    p.version(draft['version'], inp.expected_version)
    if p.posted_from(s, draft['id']):
        raise BookflowError('E_VALIDATION', message='This draft is already posted; a draft posts one paycheck once.',
                            details={'fields': [{'field': 'draft', 'problem': 'already posted'}]})
    if draft['state'] != 'prepared' or not draft['calculation']:
        p.fail('draft', 'Prepare (calculate) this draft before posting it.')
    if draft['settings_version'] != config.version:
        raise BookflowError('E_PREVIEW_STALE', message='Payroll settings changed after this calculation; prepare it again.')
    payload = m.DraftInput(**json.loads(draft['payload']), operation_key=inp.operation_key)
    if not payload.pay_date:
        p.fail('pay_date', 'Enter the actual pay date before posting; a future payroll stays a draft.')
    emp = p.employee(s, payload.employee)
    p.validate_inputs(s, payload)
    calc = p.calculate(payload)
    if calc.model_dump() != m.Calculation.model_validate_json(draft['calculation']).model_dump():
        raise BookflowError('E_PREVIEW_STALE', message='The saved calculation no longer matches its inputs; prepare it again.')
    year = int(payload.pay_date[:4])
    if _later(s, emp, year, payload.pay_date):
        p.fail('pay_date', 'A later paycheck for this employee in the same year is already posted; '
                           'void it first or date this paycheck on or after it.')
    if inp.net_check is not None:
        require(s, ('payroll.pay', 'standard'))
        _agent(s, 'payroll pay-net')
    components = _components(payload, calc, config)
    with owned(s):
        fresh = journals.prepare(s, safe(ctx), _journal(payload.pay_date, components), 'post', owner=KIND)
    at = now_iso()
    pc = dict(id=new_id(), draft_id=draft['id'], draft_version=draft['version'], employee_id=emp,
        transaction_id=fresh.data['header']['id'], batch_id=None, version=1, status='posted', currency='USD',
        pay_date=payload.pay_date, period_start=payload.period_start, period_end=payload.period_end, tax_year=year,
        gross_minor_units=calc.gross_minor_units, employee_tax_minor_units=calc.employee_tax_minor_units,
        employee_deduction_minor_units=calc.employee_deduction_minor_units,
        employer_cost_minor_units=calc.employer_cost_minor_units, net_minor_units=calc.net_minor_units,
        wage_account_id=config.wage_account, employer_account_id=config.employer_tax_account,
        net_pay_account_id=config.net_pay_account,
        snapshot=json.dumps({'input': json.loads(draft['payload']), 'calculation': calc.model_dump()}, sort_keys=True),
        created_at=at, created_by=s.actor.id, voided_at=None)
    check = _check_plan(s, ctx, pc, calc.net_minor_units, config.net_pay_account, inp.net_check,
                        'check_') if inp.net_check else None
    before = _ytd(s, emp, year)
    ytd = {n: before[n] + pc[f'{n}_minor_units'] for n in _CENTS}
    preview = output(s, pc, [dict(x, paycheck_id=pc['id']) for x in components], None, ytd, ctx.directive_id)
    return Plan(preview, dict(command='payroll post', input=inp, hash=prior, paycheck=pc, components=components,
                              journal=fresh, check=check, net_check=inp.net_check))


def _items(components, sign):
    return json.dumps({f"{x['kind']}:{x['code']}": sign * x['amount_minor_units'] for x in components
                       if x['kind'] != 'net_pay'}, sort_keys=True)


def _record_payment(s, ctx, pc, check_plan, version):
    """Persist the planned check under payroll ownership and the payment row that settles the net."""
    from bookflow.company import checks
    with owned(s):
        applied = checks.apply(check_plan, safe(ctx), s, owner_rows=lambda txn: (_link(txn, 'net_payment', pc['id']),))
    inp = check_plan.data['input']
    payment = dict(id=new_id(), paycheck_id=pc['id'], transaction_id=applied.output.id,
        account_id=applied.output.document.account_id, date=str(inp.date)[:10],
        amount_minor_units=pc['net_minor_units'], status='recorded', version=1, created_at=now_iso(),
        created_by=s.actor.id, voided_at=None)
    s.company.conn.execute(c.payroll_net_payments.insert().values(**payment))
    batches.record_effect(s, pc['batch_id'], record_type=PAYMENT, record_id=payment['id'], action='created',
                          version_after=version, event_id=batches.last_event_id(s))
    return payment


def apply_post(plan, ctx, s):
    if plan.data.get('replay'):
        return Applied(plan.preview, [], 'Payroll retry.')
    d = plan.data
    pc, fresh = dict(d['paycheck']), d['journal']
    _defer(s)
    pc['batch_id'] = batches._insert_batch(s, ctx, kind=KIND, label='Paycheck', scope='payroll', ordering='record',
                                           source_type=PAYCHECK, source_id=pc['id'], reason=NEUTRAL)
    with owned(s):
        s.company.conn.execute(c.payroll_paychecks.insert().values(**pc))
        s.company.conn.execute(c.payroll_paycheck_components.insert(),
                               [dict(x, paycheck_id=pc['id']) for x in d['components']])
        journals.persist_prepared(fresh, safe(ctx), s, command_name='payroll post', noun='paycheck', number='',
                                  first=(_link(pc['transaction_id'], 'paycheck', pc['id']),))
    batches.record_effect(s, pc['batch_id'], record_type=PAYCHECK, record_id=pc['id'], action='created',
                          version_after=1, event_id=batches.last_event_id(s))
    s.company.conn.execute(c.payroll_ytd_events.insert().values(id=new_id(), employee_id=pc['employee_id'],
        tax_year=pc['tax_year'], paycheck_id=pc['id'], kind='paycheck', reverses_id=None,
        **{f'{n}_minor_units': pc[f'{n}_minor_units'] for n in _CENTS}, items=_items(d['components'], 1),
        created_at=now_iso()))
    _event(s, ctx, pc['id'], 'post', 1)
    if d['check'] is not None:
        _record_payment(s, ctx, pc, d['check'], 2)
        s.company.conn.execute(c.payroll_paychecks.update().where(c.payroll_paychecks.c.id == pc['id']).values(version=2))
        _event(s, ctx, pc['id'], 'pay_net', 2)
    out = output(s, _row(s, pc['id']))
    _receipt(s, ctx, d['input'], d['hash'], d['command'], out)
    return Applied(out, [], 'Paycheck posted.', audited=True)


# ---------------------------------------------------------------- paying, voiding

def _current(s, inp):
    pc = _row(s, inp.paycheck)
    p.version(pc['version'], inp.expected_version)
    return pc


def _not_reversing(s, pc):
    """A paycheck whose batch reversal is part way is finished by `batch reverse`, not by a direct write."""
    state = s.company.conn.execute(sa.select(c.batches.c.state).where(c.batches.c.id == pc['batch_id'])).scalar()
    if state in ('reversing', 'blocked'):
        raise BookflowError('E_VALIDATION', message=(
            "A reversal of this paycheck's batch is part way; run `batch reverse` on that batch again to finish it."),
            details={'fields': [{'field': 'paycheck', 'problem': 'batch_reversal_in_progress'}], 'batch': pc['batch_id']})


def prepare_pay(inp, ctx, s):
    prior = p.retry(s, ctx, inp, 'payroll pay-net', m.PaycheckOutput)
    if isinstance(prior, Plan):
        return prior
    # Settling a posted paycheck stays available with the payroll layer off (design section 2).
    pc = _current(s, inp)
    _not_reversing(s, pc)
    if pc['status'] != 'posted':
        p.fail('paycheck', 'A voided paycheck has no net pay to record.')
    if _payment(s, pc['id']):
        p.fail('paycheck', "This paycheck's net pay is already recorded as paid; void that payment first to replace it.")
    check = _check_plan(s, ctx, pc, pc['net_minor_units'], pc['net_pay_account_id'], m.NetCheck(
        **inp.model_dump(include={'account', 'date', 'number', 'to_be_printed', 'no_number'})))
    preview = output(s, pc).model_copy(update={'version': pc['version'] + 1, 'net_status': 'payment_recorded'})
    return Plan(preview, dict(command='payroll pay-net', input=inp, hash=prior, paycheck=pc, check=check))


def apply_pay(plan, ctx, s):
    if plan.data.get('replay'):
        return Applied(plan.preview, [], 'Payroll retry.')
    d = plan.data
    pc = d['paycheck']
    _defer(s)
    _record_payment(s, ctx, pc, d['check'], pc['version'] + 1)
    _bump(s, pc)
    _event(s, ctx, pc['id'], 'pay_net', pc['version'] + 1)
    out = output(s, _row(s, pc['id']))
    _receipt(s, ctx, d['input'], d['hash'], d['command'], out)
    return Applied(out, [], 'Net pay recorded.', audited=True)


def _bump(s, pc, **values):
    done = s.company.conn.execute(c.payroll_paychecks.update().where(c.payroll_paychecks.c.id == pc['id'],
        c.payroll_paychecks.c.version == pc['version']).values(version=pc['version'] + 1, **values))
    if done.rowcount != 1:
        raise BookflowError('E_VERSION_CONFLICT', message='Paycheck changed. Reopen before saving.')


def _reconciled(s, transaction_id):
    keys, members = c.reconciliation_keys, c.reconciliation_current_members
    return s.company.conn.execute(sa.select(keys.c.id).join(members, members.c.key_id == keys.c.id).where(
        keys.c.transaction_id == transaction_id).limit(1)).first() is not None


def _matched(s, transaction_id):
    return s.company.conn.execute(sa.select(c.bank_lines.c.id).where(c.bank_lines.c.entry_id == transaction_id,
        c.bank_lines.c.live == 1).limit(1)).first() is not None


def payment_blockers(s, payment, *, reversal=False):
    """What stops a net payment's check being voided: (code, message) pairs, never amounts."""
    from bookflow.company import check_printing
    txn = payment['transaction_id']
    found = []
    if check_printing.in_printer(s, txn):
        found.append(('in_print_run', "The net-pay check is in a print run waiting for confirmation; confirm or cancel that run first."))
    if reversal and check_printing.printed(s, txn):
        found.append(('printed', 'The net-pay check has been printed; void it with `payroll void-payment`, which keeps the paper record, instead of reversing the batch.'))
    if _reconciled(s, txn):
        found.append(('reconciled', 'The net-pay check is cleared in a finished reconciliation; undo that reconciliation first.'))
    if _matched(s, txn):
        found.append(('bank_matched', 'The net-pay check is matched to a bank line; unmatch it first.'))
    return found


def paycheck_blockers(s, pc, *, payment_reversed_first=False):
    found = []
    if pc['status'] != 'posted':
        found.append(('voided', 'This paycheck is already voided.'))
        return found
    if not payment_reversed_first and _payment(s, pc['id']):
        found.append(('net_paid', "Its net pay is recorded as paid; void that check first with `payroll void-payment`."))
    if _later(s, pc['employee_id'], pc['tax_year'], pc['pay_date'], exclude=pc['id']):
        found.append(('later_paycheck', 'A later paycheck for this employee in the same year depends on its year-to-date facts; void that one first.'))
    return found


def _refuse(found):
    code, message = found[0]
    raise BookflowError('E_RECONCILIATION_DEPENDENCY' if code in ('reconciled', 'bank_matched') else 'E_VALIDATION',
        message=message, details={'fields': [{'field': 'paycheck', 'problem': code}], 'blockers': [x[0] for x in found]})


def _void_check_plan(s, ctx, payment):
    from bookflow.company import checks
    from bookflow.company.check_models import CheckVoidInput
    header = journals.resolve(s, payment['transaction_id'])
    with owned(s):
        return checks.prepare(s, safe(ctx), CheckVoidInput(check=header['id'], expected_version=header['version']),
                              'check', 'void')


def _void_journal_plan(s, ctx, pc):
    from bookflow.company.journal_models import JournalVoidInput
    header = journals.resolve(s, pc['transaction_id'])
    with owned(s):
        return journals.prepare(s, safe(ctx), JournalVoidInput(journal=header['id'], expected_version=header['version']),
                                'void', owner=KIND)


def prepare_void_payment(inp, ctx, s):
    prior = p.retry(s, ctx, inp, 'payroll void-payment', m.PaycheckOutput)
    if isinstance(prior, Plan):
        return prior
    pc = _current(s, inp)
    _not_reversing(s, pc)
    payment = _payment(s, pc['id'])
    if payment is None:
        p.fail('paycheck', 'This paycheck has no recorded net payment to void.')
    found = payment_blockers(s, payment)
    if found:
        _refuse(found)
    check = _void_check_plan(s, ctx, payment)
    preview = output(s, pc).model_copy(update={'version': pc['version'] + 1, 'net_status': 'payment_not_recorded'})
    return Plan(preview, dict(command='payroll void-payment', input=inp, hash=prior, paycheck=pc, payment=payment, check=check))


def void_payment(s, ctx, pc, payment, check_plan, action):
    from bookflow.company import checks
    with owned(s):
        checks.apply(check_plan, safe(ctx), s, owner_rows=lambda txn: (_link(txn, 'net_payment_void', pc['id']),))
    done = s.company.conn.execute(c.payroll_net_payments.update().where(c.payroll_net_payments.c.id == payment['id'],
        c.payroll_net_payments.c.status == 'recorded').values(status='voided', version=payment['version'] + 1,
        voided_at=now_iso()))
    if done.rowcount != 1:
        raise BookflowError('E_VERSION_CONFLICT', message='The net payment changed. Reopen before saving.')
    _bump(s, pc)
    _event(s, ctx, pc['id'], action, pc['version'] + 1)


def apply_void_payment(plan, ctx, s):
    if plan.data.get('replay'):
        return Applied(plan.preview, [], 'Payroll retry.')
    d = plan.data
    _defer(s)
    void_payment(s, ctx, d['paycheck'], d['payment'], d['check'], 'void_payment')
    out = output(s, _row(s, d['paycheck']['id']))
    _receipt(s, ctx, d['input'], d['hash'], d['command'], out)
    return Applied(out, [], 'Net payment voided.', audited=True)


def prepare_void(inp, ctx, s):
    prior = p.retry(s, ctx, inp, 'payroll void', m.PaycheckOutput)
    if isinstance(prior, Plan):
        return prior
    pc = _current(s, inp)
    _not_reversing(s, pc)
    found = paycheck_blockers(s, pc)
    if found:
        _refuse(found)
    journal = _void_journal_plan(s, ctx, pc)
    preview = output(s, pc).model_copy(update={'version': pc['version'] + 1, 'status': 'voided', 'net_status': 'voided'})
    return Plan(preview, dict(command='payroll void', input=inp, hash=prior, paycheck=pc, journal=journal))


def void_paycheck(s, ctx, pc, journal_plan, action):
    with owned(s):
        journals.persist_prepared(journal_plan, safe(ctx), s, command_name='payroll void', noun='paycheck', number='',
                                  first=(_link(pc['transaction_id'], 'paycheck_void', pc['id']),))
    _bump(s, pc, status='voided', voided_at=now_iso())
    t = c.payroll_ytd_events
    original = dict(s.company.conn.execute(sa.select(t).where(t.c.paycheck_id == pc['id'], t.c.kind == 'paycheck')).mappings().one())
    s.company.conn.execute(t.insert().values(id=new_id(), employee_id=original['employee_id'], tax_year=original['tax_year'],
        paycheck_id=pc['id'], kind='void', reverses_id=original['id'],
        **{f'{n}_minor_units': -original[f'{n}_minor_units'] for n in _CENTS},
        items=json.dumps({k: -v for k, v in json.loads(original['items']).items()}, sort_keys=True), created_at=now_iso()))
    _event(s, ctx, pc['id'], action, pc['version'] + 1)


def apply_void(plan, ctx, s):
    if plan.data.get('replay'):
        return Applied(plan.preview, [], 'Payroll retry.')
    d = plan.data
    _defer(s)
    void_paycheck(s, ctx, d['paycheck'], d['journal'], 'void')
    out = output(s, _row(s, d['paycheck']['id']))
    _receipt(s, ctx, d['input'], d['hash'], d['command'], out)
    return Applied(out, [], 'Paycheck voided.', audited=True)


def apply(plan, ctx, s):
    command = plan.data.get('command') if not plan.data.get('replay') else None
    if command is None:
        return Applied(plan.preview, [], 'Payroll retry.')
    return {'payroll post': apply_post, 'payroll pay-net': apply_pay, 'payroll void': apply_void,
            'payroll void-payment': apply_void_payment}[command](plan, ctx, s)


# ---------------------------------------------------------------- whole-unit reversal (batch reverse)

def _read(s, batch):
    require(s, ('payroll.read', 'member'), ('ledger.read', 'member'))


def _authority(s):
    require(s, ('payroll.read', 'member'), ('payroll.void', 'standard'), ('ledger.post', 'standard'))
    _agent(s, 'payroll void')


def _target(s, effect):
    if effect['record_type'] == PAYMENT:
        payment = dict(s.company.conn.execute(sa.select(c.payroll_net_payments).where(
            c.payroll_net_payments.c.id == effect['record_id'])).mappings().one())
        return _row(s, payment['paycheck_id']), payment
    return _row(s, effect['record_id']), None


def _problems(s, effect, pending_payment):
    pc, payment = _target(s, effect)
    found = []
    if payment is not None:
        if payment['status'] == 'recorded':
            found = payment_blockers(s, payment, reversal=True)
            dates = [payment['date']]
        else:
            return pc, payment, [], False
    else:
        found = paycheck_blockers(s, pc, payment_reversed_first=pending_payment)
        dates = [pc['pay_date']]
    try:
        journals.open_dates(s, dates)
    except BookflowError as error:
        found.append(('closed_period', error.message or 'The books are closed through that date.'))
    return pc, payment, found, True


def _plan(s, ctx, batch, effects, done, probe, blockers):
    if ctx is not None and not (ctx.reason or '').strip():
        # Payroll's own history needs the reason; refuse before the shared door commits the reversal batch.
        raise BookflowError('E_REASON_REQUIRED', message='Give a reason for reversing this paycheck (--reason).')
    steps, work = [], []
    ordered = sorted(effects, key=lambda e: e['seq'], reverse=True)  # the net payment before its paycheck
    pending_payment = any(e['record_type'] == PAYMENT and (PAYMENT, e['record_id']) not in done for e in ordered)
    for effect in ordered:
        key = (effect['record_type'], effect['record_id'])
        if key in done:
            continue
        pc, payment, found, needed = _problems(s, effect, pending_payment)
        for code, message in found:
            blockers.append(dict(seq=effect['seq'], record_type=effect['record_type'], record_id=effect['record_id'],
                                 code=code, message=message, details={'paycheck': pc['id']}))
        if not needed:
            continue
        if probe:
            try:
                _authority(s)
            except BookflowError as error:
                blockers.append(dict(seq=effect['seq'], record_type=effect['record_type'], record_id=effect['record_id'],
                                     code=error.code, message=error.message, details=error.details))
        step = dict(seq=effect['seq'], record_type=effect['record_type'], record_id=effect['record_id'],
            label='Net-pay check' if payment else 'Paycheck', version=pc['version'],
            steps=[dict(operation='payroll void-payment' if payment else 'payroll void', paycheck=pc['id'],
                        expected_version=pc['version'])])
        steps.append(step)
        work.append(dict(step, effect=effect))
    return dict(steps=steps, work=work, blockers=blockers, records=len(effects), pending=len(work))


def _run(s, ctx, batch, reversal_id, item):
    effect = item['effect']
    with s.commits.operation(batches.OWNER, s.company):
        s.company.raw.execute('BEGIN IMMEDIATE')
        try:
            if (effect['record_type'], effect['record_id']) in batches._done_steps(s, batches._batch(s, batch['id'])):
                s.commits.commit(s.company, batches.OWNER)
                return
            _defer(s)
            _authority(s)
            # Run newest first, so a paycheck's net payment is already voided when the paycheck's turn comes.
            pc, payment, found, needed = _problems(s, effect, False)
            if found:
                raise BookflowError('E_BATCH_BLOCKED', message=found[0][1], details={'blockers': [
                    dict(seq=effect['seq'], record_type=effect['record_type'], record_id=effect['record_id'], code=code,
                         message=message) for code, message in found]})
            if payment is not None:
                if needed:
                    void_payment(s, ctx, pc, payment, _void_check_plan(s, ctx, payment), 'reverse_payment')
            else:
                void_paycheck(s, ctx, pc, _void_journal_plan(s, ctx, pc), 'reverse')
            after = _row(s, pc['id'])
            batches.record_effect(s, reversal_id, record_type=effect['record_type'], record_id=effect['record_id'],
                action='reversed', version_after=after['version'],
                before=dict(of_seq=effect['seq'], step=0, command='batch reverse'), event_id=batches.last_event_id(s))
            s.commits.commit(s.company, batches.OWNER)
        except BaseException:
            if s.company.write_transaction:
                s.company.raw.rollback()
            raise


batches.register(batches.Adapter(KIND, planner=_plan, runner=_run, reader=_read,
    release_document_numbers=False, blocks=True, label='Payroll paychecks', private_context=safe))
