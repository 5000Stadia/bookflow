"""Move money between two of the company's own accounts, and everything you do to one after.

One noun and the six verbs every other document carries. The posting is the account
register's, in ``company/transfers.py``.
"""
from bookflow.core.registry import Plan, command
from bookflow.company import transfers
from bookflow.company.transfer_models import (
    TransferHistoryInput, TransferHistoryOutput, TransferOutput, TransferPageOutput,
    TransferPostInput, TransferQueryInput, TransferShowInput, TransferUpdateInput,
    TransferVoidInput, TransferWriteOutput,
)

_ENDS = (
    " Both ends must be balance-sheet accounts the company owns -- bank, credit card, other"
    " current asset, fixed asset, other asset, other current liability, long term liability or"
    " equity. An income, expense, cost of goods sold, receivable, payable or non-posting"
    " account is refused by name with the reason, because a transfer never changes profit and"
    " never posts to a party ledger; so is the same account named at both ends."
)

DESCRIPTIONS = {
    'post': (
        "Move money between two accounts the company already owns. `from_account` is credited"
        " and `to_account` is debited, which is the one rule that makes the signs come out right"
        " on both sides: a bank account the money leaves goes down, a bank account it arrives in"
        " goes up, and a credit card it is sent to has less owed on it." + _ENDS +
        " A transfer has exactly two legs of one amount, so it takes no line grid, no payee and"
        " no number of its own."),
    'update': (
        "Correct a transfer. Every field is optional and a field left out keeps what was"
        " captured. Changing `to_account` moves the money out of the account it went into and"
        " into the new one at the transfer's own date, because the old accounting is reversed"
        " there in full and replaced; changing `from_account` does the same on the other side."
        + _ENDS + " Every earlier revision stays readable."),
    'void': (
        "Void a transfer with a required reason. Its accounting is reversed exactly, at the"
        " transfer's own date, so neither account moves in any other period; its number stays"
        " occupied and its history stays readable."),
    'show': (
        "Show a transfer: its current or a selected earlier revision, the account the money came"
        " out of and the account it went into with what each one did, the posting batches and"
        " the amount on its own footer."),
    'query': (
        "Page transfers in accounting-date and stable-id order, oldest first or newest first,"
        " with either-end, from-account, to-account, date, status, number and text filters;"
        " restart on company audit changes."),
    'history': (
        "Page a transfer's immutable revisions in revision-number order, each with the two ends"
        " it named at the time and the correction and void batches it carries."),
}

_ENTRY_ERRORS = ['E_RECORD_NOT_FOUND', 'E_INACTIVE_REFERENCE', 'E_VALIDATION', 'E_VALUE_RANGE',
                 'E_AMOUNT_PRECISION', 'E_PERIOD_CLOSED']
WRITE_ERRORS = {
    'post': _ENTRY_ERRORS,
    'update': _ENTRY_ERRORS + ['E_VERSION_CONFLICT'],
    'void': ['E_RECORD_NOT_FOUND', 'E_VERSION_CONFLICT', 'E_VALIDATION', 'E_REASON_REQUIRED',
             'E_PERIOD_CLOSED'],
}

INPUTS = {'post': TransferPostInput, 'update': TransferUpdateInput, 'void': TransferVoidInput,
          'show': TransferShowInput, 'query': TransferQueryInput, 'history': TransferHistoryInput}
READ_OUTPUTS = {'show': TransferOutput, 'query': TransferPageOutput,
                'history': TransferHistoryOutput}


def _write(verb):
    def planner(inp, ctx, s):
        return transfers.prepare(s, ctx, inp, verb)

    cmd = command('transfer ' + verb, scope='company', description=DESCRIPTIONS[verb],
                  input_model=INPUTS[verb], output_model=TransferWriteOutput, writes={'company'},
                  required_role='standard', capability='ledger.post',
                  accepts_idempotency_key=True, clearable=verb == 'update',
                  positional=[] if verb == 'post' else ['transfer'],
                  version_source=None if verb == 'post' else ('transfer show', 'transfer', 'version'),
                  error_codes=WRITE_ERRORS[verb])(planner)
    cmd.ledger = True
    cmd.applier(transfers.apply)
    return cmd


def _read(verb):
    def planner(inp, ctx, s):
        if verb == 'show':
            return Plan(transfers.show(s, inp))
        if verb == 'query':
            return Plan(transfers.page(s, ctx, inp))
        return Plan(transfers.history(s, ctx, inp))

    return command('transfer ' + verb, scope='company', description=DESCRIPTIONS[verb],
                   input_model=INPUTS[verb], output_model=READ_OUTPUTS[verb],
                   required_role='member', capability='ledger.read',
                   positional=[] if verb == 'query' else ['transfer'],
                   error_codes=['E_RECORD_NOT_FOUND', 'E_VALIDATION']
                   + (['E_QUERY_STALE'] if verb != 'show' else []))(planner)


transfer_post = _write('post')
transfer_update = _write('update')
transfer_void = _write('void')
transfer_show = _read('show')
transfer_query = _read('query')
transfer_history = _read('history')

TRANSFER_COMMANDS = [transfer_post, transfer_show, transfer_update, transfer_void,
                     transfer_query, transfer_history]
