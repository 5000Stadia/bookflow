"""Private paycheck preparation; no posting, tables, shipped rates or filing."""
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
    return Plan(p.output(p.row(s, inp.draft)))


def plan_list(inp, ctx, s):
    rows = s.company.conn.execute(sa.select(c.payroll_drafts).order_by(c.payroll_drafts.c.updated_at.desc(),
        c.payroll_drafts.c.id).limit(inp.limit)).mappings()
    return Plan(m.ListOutput(rows=[p.output(r) for r in rows]))


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
    'or exact quantity and dollar-rate strings, and manually calculated fixed taxes/deductions/employer costs. '
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
