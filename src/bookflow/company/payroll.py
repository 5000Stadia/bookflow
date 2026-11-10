"""Private payroll preparation, exact arithmetic and permanent company-local retries."""
import json
from decimal import Decimal, ROUND_HALF_UP, localcontext
import sqlalchemy as sa
from bookflow import BookflowError
from bookflow.company import schema as c, payroll_models as m
from bookflow.core.ids import new_id
from bookflow.core.session import now_iso
from bookflow.core.registry import Plan, Applied, Touched
from bookflow.core.idempotency import input_hash


def fail(field, message='Review this payroll field.'):
    # Never interpolate input, pay amounts, item labels or source notes in errors.
    raise BookflowError('E_VALIDATION', message=message, details={'fields': [{'field': field, 'problem': message}]})


def settings(s):
    row = s.company.conn.execute(sa.select(c.payroll_settings)).mappings().first()
    if row is None:
        return m.SettingsOutput()
    return m.SettingsOutput(**{k: v for k, v in row.items() if k != 'id'})


def account(s, selector, types, field):
    from bookflow.company.accounts import resolve_account
    try:
        row = resolve_account(s.company, selector)
    except BookflowError:
        fail(field, 'Select an existing payroll account.')
    if not row['active'] or row['currency'] != 'USD' or row['type'] not in types:
        fail(field, 'Select an active USD account of the required type.')
    return row['id']


def enabled(s):
    config = settings(s)
    if not config.enabled:
        raise BookflowError('E_FEATURE_DISABLED', message='Enable payroll before preparing or saving a paycheck.')
    currency = s.company.conn.execute(sa.select(c.company_info.c.home_currency)).scalar_one()
    if currency != 'USD':
        fail('currency', 'Payroll currently requires a USD company.')
    for field, types in (('wage_account', ('expense', 'other_expense')), ('employer_tax_account', ('expense', 'other_expense')),
                         ('net_pay_account', ('other_current_liability',))):
        account(s, getattr(config, field), types, field)
    return config


def employee(s, selector, active=True):
    from bookflow.company.list_service import resolve_selector
    from bookflow.company.lists import get_list_definition
    try:
        row = resolve_selector(s.company, c.employees, get_list_definition('employee'), selector)
    except BookflowError:
        fail('employee', 'Select an existing employee.')
    if active and not row['active']:
        fail('employee', 'Select an active employee; final-pay treatment is not available in this increment.')
    return row['id']


def row(s, draft):
    found = s.company.conn.execute(sa.select(c.payroll_drafts).where(c.payroll_drafts.c.id == draft)).mappings().first()
    if found is None:
        raise BookflowError('E_RECORD_NOT_FOUND', message='Payroll draft not found.')
    return dict(found)


def output(values, s=None, directive=None):
    """A draft as payroll readers see it; with a session, also its paycheck and latest revision's directive."""
    payload = json.loads(values['payload'])
    paycheck = None
    if s is not None:
        paycheck = posted_from(s, values['id'])
        if directive is None and has_column(s, 'payroll_draft_revisions', 'directive_id'):
            directive = s.company.conn.execute(sa.select(c.payroll_draft_revisions.c.directive_id).where(
                c.payroll_draft_revisions.c.draft_id == values['id']).order_by(
                c.payroll_draft_revisions.c.version.desc()).limit(1)).scalar()
    return m.DraftOutput(id=values['id'], version=values['version'], state=values['state'],
        settings_version=values['settings_version'], **payload,
        calculation=json.loads(values['calculation']) if values['calculation'] else None,
        paycheck=paycheck, directive=directive)


def has_column(s, table, column):
    return any(row[1] == column for row in s.company.conn.exec_driver_sql(f"PRAGMA table_info('{table}')"))


def posted_from(s, draft):
    """The paycheck posted from this draft, if any (increment 2); a posted draft is never edited again."""
    if not s.company.conn.exec_driver_sql("SELECT 1 FROM sqlite_master WHERE type='table' AND name='payroll_paychecks'").first():
        return None
    return s.company.conn.execute(sa.select(c.payroll_paychecks.c.id).where(
        c.payroll_paychecks.c.draft_id == draft)).scalar()


def require_unposted(s, draft):
    if posted_from(s, draft):
        fail('draft', 'This draft has a posted paycheck and is kept as posted. Save a new draft instead.')


def version(actual, expected):
    if actual != expected:
        raise BookflowError('E_VERSION_CONFLICT', message='Payroll draft or settings changed. Reopen before saving.',
                            details={'current_version': actual})


def calculate(inp):
    trace = []
    gross = 0
    earned = {}
    with localcontext() as context:
        context.prec = 60
        for line in inp.earnings:
            exact = (Decimal(line.amount_minor_units) if line.amount_minor_units is not None
                     else Decimal(line.quantity) * Decimal(line.rate) * 100)
            cents = int(exact.quantize(Decimal('1'), rounding=ROUND_HALF_UP))
            if cents > 10**12:
                fail('earnings', 'Earning exceeds the supported amount range.')
            gross += cents
            earned[line.code] = cents
            trace.append(m.TraceLine(code=line.code, kind='cash_earning',
                method='fixed' if line.amount_minor_units is not None else 'quantity_times_rate',
                quantity=line.quantity, rate=line.rate, exact_minor_units=format(exact, 'f'), amount_minor_units=cents))
        totals = dict.fromkeys(('employee_tax', 'employee_deduction', 'employer_tax', 'employer_contribution'), 0)
        for item in inp.items:
            if item.method == 'fixed':
                amount = item.amount_minor_units
                trace.append(m.TraceLine(code=item.code, kind=item.kind, method='fixed',
                    exact_minor_units=str(amount), amount_minor_units=amount,
                    source_note=item.source_note, rounding='fixed_cents'))
            else:
                if any(code not in earned for code in item.base):
                    fail('items.base', 'A percentage base names only earning codes on this paycheck.')
                base = sum(earned[code] for code in item.base)
                # Exact cents x millionths-of-a-percent / 10^8, rounded once half-up to a cent.
                exact = Decimal(base) * Decimal(item.rate_millionths) / Decimal(m.RATE_SCALE)
                amount = int(exact.quantize(Decimal('1'), rounding=ROUND_HALF_UP))
                trace.append(m.TraceLine(code=item.code, kind=item.kind, method='percent',
                    exact_minor_units=format(exact, 'f'), amount_minor_units=amount, source_note=item.source_note,
                    rounding='half_up_once', rate_millionths=item.rate_millionths, base_minor_units=base))
            totals[item.kind] += amount
    net = gross - totals['employee_tax'] - totals['employee_deduction']
    if net < 0:
        fail('items', 'Employee taxes and deductions exceed cash earnings. Reduce them before calculating; nothing was saved.')
    cost = totals['employer_tax'] + totals['employer_contribution']
    return m.Calculation(gross_minor_units=gross, employee_tax_minor_units=totals['employee_tax'],
        employee_deduction_minor_units=totals['employee_deduction'], employer_cost_minor_units=cost,
        net_minor_units=net, total_expense_minor_units=gross + cost, debit_minor_units=gross + cost,
        credit_minor_units=net + sum(totals.values()), trace=trace)


def retry(s, ctx, inp, command, model):
    if not ctx.reason or not ctx.reason.strip():
        raise BookflowError('E_REASON_REQUIRED')
    record = s.company.conn.execute(sa.select(c.payroll_operations).where(
        c.payroll_operations.c.operation_key == inp.operation_key)).mappings().first()
    digest = input_hash(m.canonical_items(inp.model_dump(mode='json')), s.company_row['id'])
    if record:
        if record['actor_id'] != s.actor.id or record['command'] != command or record['input_hash'] != digest:
            raise BookflowError('E_IDEMPOTENCY_MISMATCH', message='This payroll operation key is already used.')
        return Plan(model.model_validate_json(record['output']), {'replay': True})
    return digest


def prepare_setup(inp, ctx, s):
    prior = retry(s, ctx, inp, 'payroll setup', m.SettingsOutput)
    if isinstance(prior, Plan):
        return prior
    old = settings(s)
    version(old.version, inp.expected_version)
    values = old.model_dump(exclude={'currency'})
    values.update(enabled=inp.enabled, version=old.version + 1)
    if inp.enabled:
        if s.company.conn.execute(sa.select(c.company_info.c.home_currency)).scalar_one() != 'USD':
            fail('currency', 'Payroll currently requires a USD company.')
        for field, types in (('wage_account', ('expense', 'other_expense')), ('employer_tax_account', ('expense', 'other_expense')),
                             ('net_pay_account', ('other_current_liability',))):
            values[field] = account(s, getattr(inp, field) or values[field], types, field)
    return Plan(m.SettingsOutput(**values), {'values': values, 'input': inp, 'hash': prior, 'command': 'payroll setup'})


def validate_inputs(s, inp):
    if inp.period_end < inp.period_start:
        fail('period_end', 'Period end must not precede period start.')
    if not inp.taxes_confirmed:
        fail('taxes_confirmed', 'Confirm that the manually entered fixed taxes and deductions are complete.')
    codes = [x.code for x in [*inp.earnings, *inp.items]]
    if len(set(codes)) != len(codes):
        fail('items', 'Use a distinct code for each earning and item.')
    earnings = {x.code for x in inp.earnings}
    net_pay = settings(s).net_pay_account
    for item in inp.items:
        if item.base is not None and not set(item.base) <= earnings:
            fail('items.base', 'A percentage base names only earning codes on this paycheck.')
        item.liability_account = account(s, item.liability_account, ('other_current_liability',), 'items.liability_account')
        if net_pay and item.liability_account == net_pay:
            # Net pay is reserved for payroll; a liability must stay remittable through an ordinary entry.
            fail('items.liability_account', "An item's liability account may not be the net-pay account; select the "
                                            "account that holds what is owed to the item's payee.")


def prepare_draft(inp, ctx, s):
    prior = retry(s, ctx, inp, 'payroll draft', m.DraftOutput)
    if isinstance(prior, Plan):
        return prior
    config = enabled(s)
    old = row(s, inp.draft) if inp.draft else None
    version(old['version'] if old else 0, inp.expected_version)
    if old:
        require_unposted(s, old['id'])
    emp = employee(s, inp.employee)
    # Validate a copy: canonical request hashing must not depend on selector resolution.
    clean = inp.model_copy(deep=True)
    validate_inputs(s, clean)
    payload = clean.model_dump(mode='json', exclude={'draft', 'expected_version', 'operation_key'})
    payload['employee'] = emp
    at = now_iso()
    values = dict(id=old['id'] if old else new_id(), employee_id=emp, version=(old['version'] if old else 0) + 1,
        state='draft', currency='USD', pay_date=inp.pay_date, payload=json.dumps(payload), calculation=None,
        settings_version=config.version, created_at=old['created_at'] if old else at, updated_at=at)
    return Plan(output(values, directive=ctx.directive_id), {'values': values, 'old': old, 'input': inp, 'hash': prior, 'command': 'payroll draft'})


def prepare_calculation(inp, ctx, s):
    prior = retry(s, ctx, inp, 'payroll prepare', m.DraftOutput)
    if isinstance(prior, Plan):
        return prior
    config = enabled(s)
    old = row(s, inp.draft)
    version(old['version'], inp.expected_version)
    require_unposted(s, old['id'])
    payload = m.DraftInput(**json.loads(old['payload']), operation_key=inp.operation_key)
    employee(s, payload.employee)
    validate_inputs(s, payload)
    result = calculate(payload)
    values = {**old, 'version': old['version'] + 1, 'state': 'prepared', 'settings_version': config.version,
              'calculation': result.model_dump_json(), 'updated_at': now_iso()}
    return Plan(output(values, directive=ctx.directive_id), {'values': values, 'old': old, 'input': inp, 'hash': prior, 'command': 'payroll prepare'})


def apply(plan, ctx, s):
    if plan.data.get('replay'):
        return Applied(plan.preview, [], 'Payroll retry.')
    data = plan.data
    values = data['values']
    command = data['command']
    if command == 'payroll setup':
        existing = s.company.conn.execute(sa.select(c.payroll_settings.c.id)).first()
        if existing:
            s.company.conn.execute(c.payroll_settings.update().where(c.payroll_settings.c.id == 1).values(**values))
        else:
            s.company.conn.execute(c.payroll_settings.insert().values(id=1, **values))
        record_type, record_id = 'payroll_settings', '1'
    else:
        if data['old']:
            s.company.conn.execute(c.payroll_drafts.update().where(c.payroll_drafts.c.id == values['id']).values(**values))
        else:
            s.company.conn.execute(c.payroll_drafts.insert().values(**values))
        s.company.conn.execute(c.payroll_draft_revisions.insert().values(draft_id=values['id'], version=values['version'],
            snapshot=plan.preview.model_dump_json(), reason=ctx.reason, actor_id=s.actor.id, created_at=now_iso(),
            directive_id=ctx.directive_id))
        record_type, record_id = 'payroll_draft', values['id']
    s.company.conn.execute(c.payroll_operations.insert().values(operation_key=data['input'].operation_key,
        actor_id=s.actor.id, command=command, input_hash=data['hash'], output=plan.preview.model_dump_json(), reason=ctx.reason))
    # Free text and operation keys remain private even if the caller types a pay amount there.
    from bookflow.core.audit import write_event_to
    safe = ctx.model_copy(update={'reason': 'Private payroll change; reason retained in payroll history.',
        'source_ref': None, 'idempotency_key': None, 'client_name': 'payroll', 'client_host': '', 'client_version': '',
        'session_id': '', 'request_id': new_id(), 'directive_id': None})
    touch = Touched(record_type, record_id, 'create' if values['version'] == 1 else 'update',
                    values['version'] - 1 or None, values['version'], {'version': values['version']}, db='company')
    write_event_to(s.company, safe, command, 'Private payroll record saved.', [touch],
                   actor_id=s.actor.id, actor_kind=s.actor.kind)
    return Applied(plan.preview, [], 'Private payroll record saved.', audited=True)
