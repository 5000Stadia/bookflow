"""Private paycheck preparation, posting one paycheck, net pay by check, and voids; no tables, shipped rates or filing."""
import sqlalchemy as sa
from bookflow.company import payroll as p, payroll_models as m, schema as c
from bookflow.core.registry import Plan, command


def plan_setup(inp, ctx, s):
    return p.prepare_setup(inp, ctx, s)


def plan_settings(inp, ctx, s):
    return Plan(p.settings(s))


def plan_draft(inp, ctx, s):
    return p.prepare_draft(inp, ctx, s)


def plan_show(inp, ctx, s):
    return Plan(p.output(p.row(s, inp.draft), s))


def plan_list(inp, ctx, s):
    rows = s.company.conn.execute(sa.select(c.payroll_drafts).order_by(c.payroll_drafts.c.updated_at.desc(),
        c.payroll_drafts.c.id).limit(inp.limit)).mappings()
    return Plan(m.ListOutput(rows=[p.output(r, s) for r in rows]))


def plan_prepare(inp, ctx, s):
    return p.prepare_calculation(inp, ctx, s)


_ERRORS = ['E_VALIDATION', 'E_RECORD_NOT_FOUND', 'E_VERSION_CONFLICT', 'E_IDEMPOTENCY_MISMATCH']


def _read(name, model, out, planner, description):
    return command(name, scope='company', input_model=model, output_model=out, required_role='member',
        capability='payroll.read', error_codes=_ERRORS, description=description)(planner)


def _write(name, model, out, planner, description, configure=False):
    cmd = command(name, scope='company', input_model=model, output_model=out,
        required_role='admin' if configure else 'standard', capability='payroll.configure' if configure else 'payroll.prepare',
        writes={'company'}, error_codes=_ERRORS, description=description)(planner)
    cmd.resource_requirements = (('payroll.read', 'member'), ('company', 'admin')) if configure else (('payroll.read', 'member'),)
    cmd.plan_in_write = True
    cmd.applier(p.apply)
    return cmd


payroll_setup = _write('payroll setup', m.SetupInput, m.SettingsOutput, plan_setup,
    'Enable or disable private payroll (off by default). Requires company admin and payroll.configure/read. '
    'Enabling requires three existing active USD accounts: wage expense, employer-tax expense, net-pay liability. '
    'Creates no chart accounts or tax rates. Give expected_version (zero initially), a unique operation_key and reason. '
    'Disabling stops new drafts and calculations; authorized historical reads remain available.', True)
payroll_settings = _read('payroll settings', m.EmptyInput, m.SettingsOutput, plan_settings,
    'Read private payroll switch, selected accounts and settings version. No rates are bundled.')
payroll_draft = _write('payroll draft', m.DraftInput, m.DraftOutput, plan_draft,
    'Save a private paycheck draft: employee, period, optional actual pay date, USD earnings as fixed integer cents '
    'or exact quantity and dollar-rate strings, and owner-entered taxes/deductions/employer costs: fixed cents, or '
    'method=percent with rate_millionths (millionths of a percent) of named earning codes, rounded half-up once. '
    'Items need a liability account, payee and source note (including explicit zero). Confirm taxes_confirmed; '
    'an empty item list is an explicit no-tax calculation, never an inferred exemption. To replace a draft, give '
    'its ID and expected_version; replacement clears its calculation. A unique operation_key and reason are required. '
    'Nothing posts or claims time/commission. Then payroll prepare; reopen through payroll show.')
payroll_show = _read('payroll show', m.Selector, m.DraftOutput, plan_show,
    'Reopen a private paycheck draft by shared ID, including inputs and saved calculation trace. Requires payroll.read '
    'even when payroll is disabled. Ordinary employee reads contain no payroll details.')
payroll_list = _read('payroll list', m.ListInput, m.ListOutput, plan_list,
    'List recent private paycheck drafts for save/resume by permitted colleagues. Payroll.read is required.')
payroll_prepare = _write('payroll prepare', m.PrepareInput, m.DraftOutput, plan_prepare,
    'Calculate and save a draft using expected_version, a unique operation_key and reason. Extend exact quantity × '
    'rate and round half-up per earning line to integer cents; fixed employee taxes/deductions reduce net, employer '
    'costs do not. Negative net refuses without saving; zero net is allowed. Shows gross, net, employer expense, '
    'balanced conceptual totals and each arithmetic step. No ledger effects, tax tables, posting or filing. '
    'Dry-run previews without saving. A saved preparation advances the draft version; later edits clear it.')


def _paychecks():
    from bookflow.company import payroll_paychecks
    return payroll_paychecks


def plan_post(inp, ctx, s):
    return _paychecks().prepare_post(inp, ctx, s)


def plan_pay(inp, ctx, s):
    return _paychecks().prepare_pay(inp, ctx, s)


def plan_void(inp, ctx, s):
    return _paychecks().prepare_void(inp, ctx, s)


def plan_void_payment(inp, ctx, s):
    return _paychecks().prepare_void_payment(inp, ctx, s)


def plan_paycheck(inp, ctx, s):
    return Plan(_paychecks().show(s, inp))


def plan_paychecks(inp, ctx, s):
    return Plan(_paychecks().page(s, inp))


def apply_paycheck(plan, ctx, s):
    return _paychecks().apply(plan, ctx, s)


_POST_ERRORS = _ERRORS + ['E_PREVIEW_STALE', 'E_PERIOD_CLOSED', 'E_SYSTEM_RECORD', 'E_RECONCILIATION_DEPENDENCY']


def _ledger(name, model, planner, capability, description):
    cmd = command(name, scope='company', input_model=model, output_model=m.PaycheckOutput, required_role='standard',
        capability=capability, writes={'company'}, error_codes=_POST_ERRORS, description=description)(planner)
    cmd.resource_requirements = (('payroll.read', 'member'), ('ledger.post', 'standard'))
    cmd.plan_in_write = True
    cmd.applier(apply_paycheck)
    return cmd


payroll_post = _ledger('payroll post', m.PostInput, plan_post, 'payroll.post',
    'Post one prepared paycheck draft to the ledger as one payroll-owned journal entry dated on its actual pay date: '
    'Dr wage expense per earning and employer expense per employer item; Cr each item liability account and the '
    'net-pay liability. Amounts are the saved calculation, never recalculated. Requires payroll.post, payroll.read, '
    'ledger.post, an enabled layer, unchanged settings, an open period, expected_version and a unique operation_key '
    'and reason. A draft posts once. Optional net_check also records the one check settling the whole net pay in '
    'the same transaction (needs payroll.pay); zero net needs no payment. Reverse as one unit with batch reverse.')
payroll_pay_net = _ledger('payroll pay-net', m.PayNetInput, plan_pay, 'payroll.pay',
    "Record the one ordinary check that settles a posted paycheck's whole net pay: Dr net-pay liability, Cr the bank "
    'account. Dated on or after the pay date; number, to_be_printed or no_number as for any check. Requires '
    'payroll.pay, payroll.read, ledger.post, an enabled layer, the paycheck expected_version, operation_key and reason.')
payroll_void = _ledger('payroll void', m.PaycheckWriteInput, plan_void, 'payroll.void',
    'Void a posted paycheck: its journal entry is voided and an exact negative year-to-date event is appended. '
    'Refuses while its net payment is recorded (void that first), when a later paycheck for the employee and year '
    'is posted, or when the period is closed. Works with the layer off. Give expected_version, operation_key, reason.')
payroll_void_payment = _ledger('payroll void-payment', m.PaycheckWriteInput, plan_void_payment, 'payroll.void',
    "Void a paycheck's net-pay check through the check machinery, leaving the paycheck posted with net pay unpaid. "
    'A printed check may be voided (the paper record stays); refuses while it is in an unanswered print run, cleared '
    'in a reconciliation or matched to a bank line. Give the paycheck expected_version, operation_key and reason.')
payroll_paycheck = _read('payroll paycheck', m.PaycheckSelector, m.PaycheckOutput, plan_paycheck,
    'Reopen a posted or voided paycheck: components, accounts, net-pay status and check, and the employee year to '
    'date. Requires payroll.read even when payroll is disabled.')
payroll_paychecks = _read('payroll paychecks', m.PaycheckListInput, m.PaycheckListOutput, plan_paychecks,
    'List posted and voided paychecks, newest pay date first, optionally for one employee. Requires payroll.read.')
