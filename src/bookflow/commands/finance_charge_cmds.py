"""Assess finance charges: late charges on overdue customer balances, by the company's policy (R120).

The policy is the company's finance charge preferences (`company update --finance-charge-...`); the
computation and the writing live in ``company/finance_charges.py``.
"""
from bookflow.company import finance_charges
from bookflow.company.finance_charge_models import FinanceChargeAssessInput, FinanceChargeAssessOutput
from bookflow.core.registry import Plan, command

DESCRIPTION = (
    'Assess finance charges on overdue customer balances as of a date (default today), by the company'
    ' finance charge preferences: annual rate, minimum charge, grace days, the finance charge account,'
    ' whether overdue finance charges are charged again, and whether days count from the due date or the'
    ' invoice date. Finance charges are off until those are set with `company update`. Dry-run is the'
    ' preview: every customer and job with a balance overdue past the grace period, with its overdue'
    ' balance, last finance charge date, the documents and days charged, and the proposed charge'
    ' (open balance x rate x days / 365 per document, days from the due date, or invoice date, or the last'
    ' finance charge date when later; summed exactly, rounded once to the cent half to even, then the'
    ' minimum). `has_unapplied_credits` marks a customer with payments or credits not yet applied, which'
    ' are not subtracted: apply them first. Omit `customers` to assess every proposed customer, or name'
    ' the ones to assess, each optionally with a changed `amount`. Each charge is one statement charge on'
    ' the Finance Charge item (set up on first use on the finance charge account), dated the assessment'
    ' date: Accounts Receivable is debited and the finance charge account credited, so it ages, shows on'
    ' `report statement` and is paid like any other charge; void it with `statement-charge void`. A customer'
    ' already charged as of the date is not charged again. A date in a closed period is refused.'
)

ERRORS = ['E_VALIDATION', 'E_RECORD_NOT_FOUND', 'E_INACTIVE_REFERENCE', 'E_AMOUNT_PRECISION', 'E_VALUE_RANGE',
          'E_PERIOD_CLOSED', 'E_PREVIEW_STALE']


def plan_assess(inp, ctx, s) -> Plan:
    return finance_charges.prepare(s, ctx, inp)


finance_charge_assess = command(
    'finance-charge assess', scope='company', description=DESCRIPTION,
    input_model=FinanceChargeAssessInput, output_model=FinanceChargeAssessOutput, writes={'company'},
    required_role='standard', capability='ledger.post', accepts_idempotency_key=True,
    error_codes=ERRORS)(plan_assess)
finance_charge_assess.ledger = True
finance_charge_assess.applier(finance_charges.apply)

FINANCE_CHARGE_COMMANDS = [finance_charge_assess]
