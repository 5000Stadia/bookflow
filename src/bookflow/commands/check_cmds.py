"""Write a check, enter a credit card charge.

Two nouns rather than one: what funds them differs, a check number applies to one of them
and not the other, and a bookkeeper opening the document already knows which one they are
writing. The posting is the account register's, in ``company/checks.py``.
"""
from bookflow.core.registry import command
from bookflow.company import checks
from bookflow.company.check_models import (
    CardChargePostInput, CheckPostInput, MoneyOutWriteOutput,
)

_SHARED = (
    ' `expenses` is one to 199 lines of account, amount, memo and class saying what the money'
    ' was spent on; they must add up to `amount` exactly, and a total that does not is refused'
    " with the difference. A line's own `class_id` is that line's class and a line without one"
    " takes the document's `class_id`; set `class_mode` to `none` to leave one line unclassified"
    ' even when the document carries a class. `pay_to` names who the money went to, from the'
    ' vendor, customer, employee or other-name lists. Correct it with `register update` and void it with `journal void`;'
    ' both keep the original entry and its history.'
)

DESCRIPTIONS = {
    'check': (
        'Write a check on a bank account. The bank balance goes down by `amount` and the expense'
        ' accounts go up by their own line amounts. `account` must be a bank account. `number` is'
        ' the check number; leave it out to take the next number for this company.' + _SHARED),
    'card-charge': (
        'Enter a purchase made on a company credit card. What is owed on the card goes up by'
        ' `amount` and the expense accounts go up by their own line amounts. `account` must be a'
        ' credit card account. A card charge carries no check number.' + _SHARED),
}

ERRORS = ['E_RECORD_NOT_FOUND', 'E_INACTIVE_REFERENCE', 'E_VALIDATION', 'E_VALUE_RANGE',
          'E_AMOUNT_PRECISION', 'E_UNBALANCED_ENTRY', 'E_PERIOD_CLOSED', 'E_DUPLICATE_NUMBER']


def _post(noun, model):
    def planner(inp, ctx, s):
        return checks.prepare(s, ctx, inp, noun)

    cmd = command(noun + ' post', scope='company', description=DESCRIPTIONS[noun],
                  input_model=model, output_model=MoneyOutWriteOutput, writes={'company'},
                  required_role='standard', capability='ledger.post',
                  accepts_idempotency_key=True, positional=[], error_codes=ERRORS)(planner)
    cmd.ledger = True
    cmd.applier(checks.apply)
    return cmd


check_post = _post('check', CheckPostInput)
card_charge_post = _post('card-charge', CardChargePostInput)

CHECK_COMMANDS = [check_post, card_charge_post]
