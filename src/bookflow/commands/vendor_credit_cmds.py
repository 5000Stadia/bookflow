"""Enter a vendor credit, read it, void it, and point it at the bills it answers.

``vendor-credit post`` is the bill's verb with the signs swapped; ``vendor-credit apply`` and
``unapply`` are ``bill payment apply`` and ``unapply``, on the same settlement edge. The
accounting lives in ``company/vendor_credits.py``.
"""
from bookflow.core.registry import Plan, command
from bookflow.company import vendor_credits
from bookflow.company.vendor_credit_models import (
    VendorCreditApplyInput, VendorCreditOutput, VendorCreditPageOutput, VendorCreditPostInput,
    VendorCreditQueryInput, VendorCreditShowInput, VendorCreditUnapplyInput, VendorCreditVoidInput,
    VendorCreditWriteOutput,
)

_POST = (
    'Enter a vendor credit: money a vendor owes you back for a return, an overcharge or a'
    ' rebate. Accounts Payable is debited the total and each line credits the account the'
    ' original cost went to, so what the vendor is owed falls by exactly what was credited.'
    ' `expenses` is one to 200 rows of account, amount, memo, optional customer or job and'
    ' optional class, naming the accounts the credit gives back; a row without its own'
    ' `class_id` takes the credit’s, and `class_mode` set to `none` leaves one row'
    ' unclassified. `ap_account` is the Accounts Payable account the credit is credited'
    ' against and defaults to the only active one when the company has exactly one; it must'
    ' match the bills this credit will settle. `supplier_reference` is the vendor’s own'
    ' credit-note number, kept as typed. A credit is not a payable: it is never due, never'
    ' appears on `report unpaid-bills`, and settles nothing until `vendor-credit apply` points'
    ' it at a bill. Vendor credits take their own number series; they do not share the'
    ' bill series.'
)

_APPLY = (
    'Attach what a vendor credit still has free to one or more open bills of the same vendor.'
    ' Nothing is posted and no money moves: Accounts Payable already fell when the credit'
    ' posted, so this only decides which bills it answers, and those bills fall by exactly what'
    ' is attached to them. A bill settled in full by a credit reads `paid`, leaves'
    ' `report unpaid-bills` and ages to nothing, exactly as one settled by a check does. Free'
    " capacity is the credit's amount less what it currently answers -- never applied, or"
    ' freed by `vendor-credit unapply`. Each named bill takes what you name for it, or'
    ' everything still open on it when you name nothing; applying less than what is free'
    ' leaves the rest free. The vendor, the payable account and the currency must match the'
    ' credit exactly. `date` is the settlement date and defaults to the credit date; it may be'
    ' later but never earlier than the credit or than a bill it settles, and never on or'
    ' before the closing date.'
)

ERRORS = {
    'post': ['E_RECORD_NOT_FOUND', 'E_INACTIVE_REFERENCE', 'E_VALIDATION', 'E_VALUE_RANGE',
             'E_AMOUNT_PRECISION', 'E_PERIOD_CLOSED', 'E_DUPLICATE_NUMBER'],
    'void': ['E_RECORD_NOT_FOUND', 'E_VERSION_CONFLICT', 'E_VALIDATION', 'E_REASON_REQUIRED',
             'E_PERIOD_CLOSED', 'E_HAS_APPLICATIONS'],
    'apply': ['E_RECORD_NOT_FOUND', 'E_VALIDATION', 'E_AMOUNT_PRECISION', 'E_VALUE_RANGE',
              'E_PERIOD_CLOSED', 'E_VERSION_CONFLICT', 'E_APPLICATION_CAPACITY',
              'E_APPLICATION_INCOMPATIBLE', 'E_APPLICATION_INACTIVE'],
    'unapply': ['E_RECORD_NOT_FOUND', 'E_VERSION_CONFLICT', 'E_APPLICATION_INACTIVE',
                'E_VALIDATION', 'E_PERIOD_CLOSED'],
}

DESCRIPTIONS = {
    'post': _POST,
    'void': ('Void a vendor credit with a required reason. Its accounting is reversed at its own'
             ' date, its number stays occupied and its history stays readable. Anything it still'
             ' settles must be unapplied first, so voiding never silently reopens a bill.'),
    'apply': _APPLY,
    'unapply': ('Take a vendor credit back off the bills it settled, without moving any money.'
                ' The bills go back to open for what was applied and no account changes, which'
                ' leaves the credit standing as an unapplied debit against the vendor. Name'
                ' `bills` to detach only those; leave it out to detach everything still applied.'
                ' Each detachment is dated at the settlement date it takes back, so an'
                ' application dated on or before the closing date cannot be undone here.'),
    'show': ('Show a vendor credit: its captured vendor and payable, its credited lines, its'
             ' posting batches, what it still has free, and every bill it has been applied to'
             ' and detached from.'),
    'query': ('Page vendor credits in accounting-date and stable-id order, oldest first or newest'
              ' first, with exact vendor, date, status, number and credit-note-reference filters,'
              ' and a `bill` filter that answers what credited a given bill; restart on company'
              ' audit changes.'),
}


def _write(verb, model):
    def planner(inp, ctx, s):
        return vendor_credits.prepare(s, ctx, inp, verb)

    cmd = command(
        'vendor-credit ' + verb, scope='company', description=DESCRIPTIONS[verb],
        input_model=model, output_model=VendorCreditWriteOutput, writes={'company'},
        required_role='standard', capability='ledger.post', accepts_idempotency_key=True,
        positional=[] if verb == 'post' else ['credit'],
        version_source=None if verb == 'post' else ('vendor-credit show', 'credit', 'version'),
        error_codes=ERRORS[verb])(planner)
    cmd.ledger = True
    cmd.applier(vendor_credits.apply)
    return cmd


def _read(verb, model, output_model):
    def planner(inp, ctx, s):
        if verb == 'show':
            return Plan(vendor_credits.show(s, inp))
        return Plan(vendor_credits.page(s, ctx, inp))

    return command(
        'vendor-credit ' + verb, scope='company', description=DESCRIPTIONS[verb],
        input_model=model, output_model=output_model,
        required_role='member', capability='ledger.read',
        positional=[] if verb == 'query' else ['credit'],
        error_codes=['E_RECORD_NOT_FOUND'] + (['E_QUERY_STALE'] if verb == 'query' else []),
    )(planner)


vendor_credit_post = _write('post', VendorCreditPostInput)
vendor_credit_void = _write('void', VendorCreditVoidInput)
vendor_credit_apply = _write('apply', VendorCreditApplyInput)
vendor_credit_unapply = _write('unapply', VendorCreditUnapplyInput)
vendor_credit_show = _read('show', VendorCreditShowInput, VendorCreditOutput)
vendor_credit_query = _read('query', VendorCreditQueryInput, VendorCreditPageOutput)

VENDOR_CREDIT_COMMANDS = [vendor_credit_post, vendor_credit_show, vendor_credit_query,
                          vendor_credit_void, vendor_credit_apply, vendor_credit_unapply]
