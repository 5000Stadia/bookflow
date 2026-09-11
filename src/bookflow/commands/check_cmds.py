"""Write a check, enter a credit card charge, and everything you do to one afterwards.

Two nouns rather than one: what funds them differs, a check number applies to one of them
and not the other, and a bookkeeper opening the document already knows which one they are
writing. Both carry the same six verbs every other document carries -- enter it, find it,
read it, correct it, void it, and read what it used to say. The posting is the account
register's, in ``company/checks.py``.
"""
from bookflow.core.registry import Plan, command
from bookflow.company import checks
from bookflow.company.check_models import (
    CardChargeHistoryInput, CardChargePostInput, CardChargeQueryInput, CardChargeShowInput,
    CardChargeUpdateInput, CardChargeVoidInput, CheckHistoryInput, CheckPostInput,
    CheckQueryInput, CheckShowInput, CheckUpdateInput, CheckVoidInput, MoneyOutHistoryOutput,
    MoneyOutOutput, MoneyOutPageOutput, MoneyOutWriteOutput,
)

_SHARED = (
    ' `expenses` is one to 199 lines of account, amount, memo and class saying what the money'
    ' was spent on; they must add up to `amount` exactly, and a total that does not is refused'
    " with the difference. A line's own `class_id` is that line's class and a line without one"
    " takes the document's `class_id`; set `class_mode` to `none` to leave one line unclassified"
    ' even when the document carries a class. `pay_to` names who the money went to, from the'
    ' vendor, customer, employee or other-name lists.'
)

_CORRECTION = (
    ' Every field is optional and a field left out keeps what was captured. Supply `expenses`'
    ' to replace the whole grid, carrying each surviving row’s `line_id` and omitting it on a'
    ' new row; leave `expenses` out to correct the header alone and keep the rows exactly as'
    ' they were captured. A replaced grid resolves `class_mode: inherit` against the'
    ' `class_id` supplied on this call, so send the document’s class again with the rows if it'
    ' still applies. The old accounting is reversed at its original date and replaced in full'
    ' at the new one; every earlier revision stays readable.'
)

DESCRIPTIONS = {
    'check': {
        'post': (
            'Write a check on a bank account. The bank balance goes down by `amount` and the expense'
            ' accounts go up by their own line amounts. `account` must be a bank account. `number` is'
            ' the check number; leave it out to take the next number for this company.' + _SHARED),
        'update': ('Correct a check, including moving it to another bank account.' + _CORRECTION),
        'void': ('Void a check with a required reason: the cheque that was lost, stale or never'
                 ' cashed. Its accounting is reversed exactly, at the check’s own date, so no'
                 ' earlier period moves; its number stays occupied and its history stays readable.'),
        'show': ('Show a check: its current or a selected earlier revision, the bank account it is'
                 ' drawn on, who it was paid to, the expense lines, the posting batches and the'
                 ' figures on its own footer.'),
        'query': ('Page checks in accounting-date and stable-id order, oldest first or newest first,'
                  ' with bank-account, payee, date, status, number and text filters; restart on'
                  ' company audit changes.'),
        'history': ('Page a check’s immutable revisions in revision-number order, each with what its'
                    ' own footer showed and the correction and void batches it carries.'),
    },
    'card-charge': {
        'post': (
            'Enter a purchase made on a company credit card. What is owed on the card goes up by'
            ' `amount` and the expense accounts go up by their own line amounts. `account` must be a'
            ' credit card account. A card charge carries no check number.' + _SHARED),
        'update': ('Correct a credit card charge, including moving it to another card.' + _CORRECTION),
        'void': ('Void a credit card charge with a required reason. Its accounting is reversed'
                 ' exactly, at the charge’s own date, and its history stays readable.'),
        'show': ('Show a credit card charge: its current or a selected earlier revision, the card it'
                 ' was charged to, who it was paid to, the expense lines, the posting batches and the'
                 ' figures on its own footer.'),
        'query': ('Page credit card charges in accounting-date and stable-id order, oldest first or'
                  ' newest first, with card-account, payee, date, status, number and text filters;'
                  ' restart on company audit changes.'),
        'history': ('Page a credit card charge’s immutable revisions in revision-number order, each'
                    ' with what its own footer showed and the batches it carries.'),
    },
}

_ENTRY_ERRORS = ['E_RECORD_NOT_FOUND', 'E_INACTIVE_REFERENCE', 'E_VALIDATION', 'E_VALUE_RANGE',
                 'E_AMOUNT_PRECISION', 'E_UNBALANCED_ENTRY', 'E_PERIOD_CLOSED', 'E_DUPLICATE_NUMBER']
# Each verb declares only what it can raise: a new document has no version to be stale, and a
# void reads no accounts, allocates no number and cannot be out of balance.
WRITE_ERRORS = {
    'post': _ENTRY_ERRORS,
    'update': _ENTRY_ERRORS + ['E_VERSION_CONFLICT'],
    'void': ['E_RECORD_NOT_FOUND', 'E_VERSION_CONFLICT', 'E_VALIDATION', 'E_REASON_REQUIRED',
             'E_PERIOD_CLOSED'],
}

# The selector each noun's commands name the document with.
POSITIONAL = {'check': 'check', 'card-charge': 'card_charge'}

INPUTS = {
    'check': {'post': CheckPostInput, 'update': CheckUpdateInput, 'void': CheckVoidInput,
              'show': CheckShowInput, 'query': CheckQueryInput, 'history': CheckHistoryInput},
    'card-charge': {'post': CardChargePostInput, 'update': CardChargeUpdateInput,
                    'void': CardChargeVoidInput, 'show': CardChargeShowInput,
                    'query': CardChargeQueryInput, 'history': CardChargeHistoryInput},
}

READ_OUTPUTS = {'show': MoneyOutOutput, 'query': MoneyOutPageOutput, 'history': MoneyOutHistoryOutput}


def _write(noun, verb):
    def planner(inp, ctx, s):
        return checks.prepare(s, ctx, inp, noun, verb)

    cmd = command(f'{noun} {verb}', scope='company', description=DESCRIPTIONS[noun][verb],
                  input_model=INPUTS[noun][verb], output_model=MoneyOutWriteOutput,
                  writes={'company'}, required_role='standard', capability='ledger.post',
                  accepts_idempotency_key=True, clearable=verb == 'update',
                  positional=[] if verb == 'post' else [POSITIONAL[noun]],
                  version_source=None if verb == 'post' else (
                      f'{noun} show', POSITIONAL[noun], 'version'),
                  error_codes=WRITE_ERRORS[verb])(planner)
    cmd.ledger = True
    cmd.applier(checks.apply)
    return cmd


def _read(noun, verb):
    def planner(inp, ctx, s):
        if verb == 'show':
            return Plan(checks.show(s, inp, noun))
        if verb == 'query':
            return Plan(checks.page(s, ctx, inp, noun))
        return Plan(checks.history(s, ctx, inp, noun))

    return command(f'{noun} {verb}', scope='company', description=DESCRIPTIONS[noun][verb],
                   input_model=INPUTS[noun][verb], output_model=READ_OUTPUTS[verb],
                   required_role='member', capability='ledger.read',
                   positional=[] if verb == 'query' else [POSITIONAL[noun]],
                   error_codes=['E_RECORD_NOT_FOUND', 'E_VALIDATION']
                   + (['E_QUERY_STALE'] if verb != 'show' else []))(planner)


VERBS = ('post', 'show', 'update', 'void', 'query', 'history')

CHECK_COMMANDS = [(_write if verb in ('post', 'update', 'void') else _read)(noun, verb)
                  for noun in ('check', 'card-charge') for verb in VERBS]

check_post, check_show, check_update, check_void, check_query, check_history = CHECK_COMMANDS[:6]
(card_charge_post, card_charge_show, card_charge_update, card_charge_void,
 card_charge_query, card_charge_history) = CHECK_COMMANDS[6:]
