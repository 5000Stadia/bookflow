"""Move money between two of the company's own accounts.

One noun and one verb. The posting is the account register's, in ``company/transfers.py``.
"""
from bookflow.core.registry import command
from bookflow.company import transfers
from bookflow.company.transfer_models import TransferPostInput, TransferWriteOutput

DESCRIPTION = (
    "Move money between two accounts the company already owns. `from_account` is credited"
    " and `to_account` is debited, which is the one rule that makes the signs come out right"
    " on both sides: a bank account the money leaves goes down, a bank account it arrives in"
    " goes up, and a credit card it is sent to has less owed on it. Both ends must be"
    " balance-sheet accounts the company owns -- bank, credit card, other current asset,"
    " fixed asset, other asset, other current liability, long term liability or equity. An"
    " income, expense, cost of goods sold, receivable, payable or non-posting account is"
    " refused by name with the reason, because a transfer never changes profit and never"
    " posts to a party ledger; so is the same account named at both ends. A transfer has"
    " exactly two legs of one amount, so it takes no line grid, no payee and no number of its"
    " own. Correct it with `register update` and void it with `journal void`; both keep the"
    " original entry and its history."
)

ERRORS = ['E_RECORD_NOT_FOUND', 'E_INACTIVE_REFERENCE', 'E_VALIDATION', 'E_VALUE_RANGE',
          'E_AMOUNT_PRECISION', 'E_PERIOD_CLOSED']


def _planner(inp, ctx, s):
    return transfers.prepare(s, ctx, inp)


transfer_post = command('transfer post', scope='company', description=DESCRIPTION,
                        input_model=TransferPostInput, output_model=TransferWriteOutput,
                        writes={'company'}, required_role='standard', capability='ledger.post',
                        accepts_idempotency_key=True, positional=[], error_codes=ERRORS)(_planner)
transfer_post.ledger = True
transfer_post.applier(transfers.apply)

TRANSFER_COMMANDS = [transfer_post]
