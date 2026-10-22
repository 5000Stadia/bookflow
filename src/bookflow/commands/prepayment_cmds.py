"""`prepayment` commands and `report customer-prepayments` (R124): deposits held until invoiced.

A customer prepayment (a deposit, down payment or retainer) is a liability until an invoice uses
it. Every effect is an ordinary numbered document (sales receipt, credit memo with its
application, customer refund); see `bookflow.company.prepayments` for the ledger shape and guards.
Each write needs what the documents it writes need: `ledger.post` at standard, and `attach`
`customer-work` as well.
"""
from __future__ import annotations

from bookflow.company import prepayments
from bookflow.company.prepayment_models import (
    CustomerPrepaymentsReportInput, CustomerPrepaymentsReportOutput, PrepaymentApplyInput, PrepaymentAttachInput,
    PrepaymentPageOutput, PrepaymentQueryInput, PrepaymentReceiveInput, PrepaymentRefundInput, PrepaymentShowInput,
    PrepaymentShowOutput, PrepaymentUnapplyInput, PrepaymentWriteOutput,
)
from bookflow.core.registry import Plan, command

_SYNONYMS = ('A customer prepayment is a deposit, down payment or retainer: money paid before the work bills. It is held '
             'on the Customer Prepayments liability (set up on first use), never income, until an invoice uses it or it '
             'is paid back.')
_WRITE_ERRORS = ['E_RECORD_NOT_FOUND', 'E_VALIDATION', 'E_PERIOD_CLOSED', 'E_INACTIVE_REFERENCE', 'E_AMOUNT_PRECISION',
                 'E_VALUE_RANGE', 'E_DUPLICATE_NUMBER', 'E_APPLIED_EXCEEDS_TOTAL', 'E_APPLICATION_INACTIVE',
                 'E_VERSION_CONFLICT', 'E_PREVIEW_STALE']


def plan_receive(inp, ctx, s):
    return prepayments.prepare_receive(s, ctx, inp)


def plan_attach(inp, ctx, s):
    return prepayments.prepare_attach(s, ctx, inp)


def plan_apply(inp, ctx, s):
    return prepayments.prepare_apply(s, ctx, inp)


def plan_unapply(inp, ctx, s):
    return prepayments.prepare_unapply(s, ctx, inp)


def plan_refund(inp, ctx, s):
    return prepayments.prepare_refund(s, ctx, inp)


def _write(verb, model, description, planner, apply, *, positional=(), errors=(), resources=()):
    cmd = command('prepayment ' + verb, scope='company', description=description, input_model=model,
                  output_model=PrepaymentWriteOutput, writes={'company'}, required_role='standard',
                  capability='ledger.post', accepts_idempotency_key=True, positional=list(positional),
                  error_codes=_WRITE_ERRORS + list(errors))(planner)
    cmd.ledger = True
    if resources:
        cmd.resource_requirements = tuple(resources)
        cmd.authorization = 'ledger.post and customer-work standard role'
    cmd.applier(apply)
    return cmd


prepayment_receive = _write(
    'receive', PrepaymentReceiveInput,
    _SYNONYMS + ' Take one: name the customer or job, or the document it is for (an accepted estimate or a work order, as '
    '"estimate 1001" or "work-order 1002"), the amount, and optionally date, payment method, reference, deposit-to (default '
    'Undeposited Funds, banked later with `deposit post`), class and memo. It is an ordinary sales receipt with one '
    'Customer Prepayment line: it prints as the customer\'s receipt and deposits, corrects, voids and deletes as any '
    'sales receipt, while nothing is drawn from it. An invoice made from the document (`estimate invoice`, `work-order '
    'invoice`) uses it by itself; `prepayment apply` uses it on any invoice. Example: {"document": "work-order 1002", '
    '"amount": "200.00", "payment_method": "Check", "reference": "4471"}.',
    plan_receive, prepayments.apply_receive)

prepayment_attach = _write(
    'attach', PrepaymentAttachInput,
    'Move what a prepayment receipt still holds to one of its customer\'s documents (an accepted estimate or a work order), '
    'or back to the customer when no document is named. What was already drawn stays where it went. The receipt\'s '
    'history keeps every move. Example: {"receipt": "57", "document": "work-order 1002"}.',
    plan_attach, prepayments.apply_attach, positional=['receipt'],
    resources=(('customer-work', 'standard'),))

prepayment_apply = _write(
    'apply', PrepaymentApplyInput,
    'Use held prepayment money on an invoice: a credit memo draws it from the receipts, oldest first, and is applied to the '
    'invoice in the same step. Draws from the document the invoice was made from by default; or name a document, the '
    'customer (money not on any document) or the receipts. Amount defaults to the lesser of what is held and what the '
    'invoice owes; more than either is refused (E_APPLIED_EXCEEDS_TOTAL). This is also how a kept fee becomes income: '
    'invoice the fee, then apply the deposit to it. Undo with `prepayment unapply`. Example: {"invoice": "1043", '
    '"document": "work-order 1002", "amount": "150.00"}.',
    plan_apply, prepayments.apply_apply)

prepayment_unapply = _write(
    'unapply', PrepaymentUnapplyInput,
    'Undo a draw: unapply its credit memo from the invoice (or void the refund that paid it back) and void the credit '
    'memo, in one step, returning the money to its receipts. A reason is required. Refused when the draw was applied '
    'inside a closed period, since its undoing carries the application date: correct it in an open period instead. '
    'Example: {"draw": "1044"}.',
    plan_unapply, prepayments.apply_unapply, positional=['draw'],
    errors=['E_REASON_REQUIRED', 'E_HAS_APPLICATIONS'])

prepayment_refund = _write(
    'refund', PrepaymentRefundInput,
    'Pay held prepayment money back to the customer: a credit memo draws it from the receipts, oldest first, and a '
    'customer refund pays it from the bank account named, by the payment method named. Name the document, the customer '
    '(money not on any document) or the receipts; amount defaults to everything held there. Example: {"document": '
    '"estimate 1001", "bank_account": "Checking", "payment_method": "Check", "check_number": "2210"}.',
    plan_refund, prepayments.apply_refund, errors=['E_APPLICATION_INCOMPATIBLE', 'E_CREDIT_UNAVAILABLE'])


@command('prepayment show', scope='company', required_role='member', capability='ledger.read',
         description='What is held and where it went: for one prepayment receipt, a customer-work document (with, for a '
                     'work order, its estimate\'s money) or a customer (all its documents too), every receipt with its '
                     'amount, drawn and held, and every draw with its invoice or refund.',
         input_model=PrepaymentShowInput, output_model=PrepaymentShowOutput,
         error_codes=['E_RECORD_NOT_FOUND', 'E_VALIDATION'])
def prepayment_show(inp, ctx, s):
    return Plan(prepayments.show(s, inp))


@command('prepayment query', scope='company', required_role='member', capability='ledger.read',
         description='List prepayment receipts (deposits, down payments, retainers) with amount, drawn and held, oldest '
                     'first; filter by customer, document, dates or held_only. Paged by next_cursor.',
         input_model=PrepaymentQueryInput, output_model=PrepaymentPageOutput,
         error_codes=['E_RECORD_NOT_FOUND', 'E_VALIDATION'])
def prepayment_query(inp, ctx, s):
    return Plan(prepayments.query(s, inp))


@command('report customer-prepayments', scope='company', required_role='member', capability='reports',
         description='Customer prepayments held on as_of, by customer, document and receipt: each receipt and each draw '
                     'to that date with the customer\'s running held balance. The total is the Customer Prepayments '
                     'account on the balance sheet for the same date; the report refuses rather than print a figure the '
                     'account contradicts. Customer balances, aging and statements stay receivable reports and leave held '
                     'money out.',
         input_model=CustomerPrepaymentsReportInput, output_model=CustomerPrepaymentsReportOutput,
         error_codes=['E_RECORD_NOT_FOUND', 'E_INTERNAL'])
def report_customer_prepayments(inp, ctx, s):
    return Plan(prepayments.report(s, inp))


PREPAYMENT_COMMANDS = [prepayment_receive, prepayment_attach, prepayment_apply, prepayment_unapply, prepayment_refund,
                       prepayment_show, prepayment_query, report_customer_prepayments]
