"""`bank-feed` and `bank-line`: statement files into the bank inbox (R108 increment 1, design/bank-feeds.md).

The work lives in ``company/bank_feeds.py``. Capability `bank-feed` (reads at member, writes at standard), with
`ledger.read` beside it: the inbox shows the books' entries a line matched. An import posts nothing to the books;
it is recorded as one batch (kind `bank-import`) so it can be reversed as one unit.
"""
from bookflow.company import bank_feeds, bank_feed_models as m
from bookflow.core.registry import Applied, Plan, command

_READ = (('ledger.read', 'member'),)

_IMPORT = (
    "Bring a bank or card statement file (OFX, QFX or CSV) into the account's bank inbox. Overlaps never duplicate: a "
    "line is known by its FITID (or, without one, a hash of the line and its occurrence in the file), so a download "
    "that overlaps an earlier one adds only the new lines; a FITID reused for another amount is kept apart and "
    "flagged; a new line that looks like one already in under another id or text is flagged possible_duplicate, never "
    "dropped. Each line the matcher pairs surely with an entry is matched; the rest wait. Nothing is posted. A file "
    "with any unreadable line is refused whole, listing every problem. An OFX file is checked against the account "
    "(E_ACCOUNT_MISMATCH), a CSV by its content (account_confirmed goes on); the same file twice is refused without again (E_ALREADY_IMPORTED). --dry-run is the "
    'preview. Example: {"account": "Checking", "attachment": "01J..."}.')


def plan_bank_feed_import(inp, ctx, s) -> Plan:
    prepared = bank_feeds.prepare(s, ctx, inp)
    return Plan(bank_feeds.output(prepared, dry_run=True), {'input': inp, 'prepared': prepared})


def apply_bank_feed_import(plan, ctx, s) -> Applied:
    from bookflow.commands.reconcile_cmds import _kept
    prepared = _kept(s, plan.data['prepared']) or bank_feeds.prepare(s, ctx, plan.data['input'])
    out, wrote = bank_feeds.write(s, ctx, prepared)
    return Applied(out, [], f'bank-feed import: {out.summary}', audited=wrote)


def plan_bank_feed_show(inp, ctx, s) -> Plan:
    return Plan(m.ImportShowOutput(**bank_feeds.show(s, inp.import_id, state=inp.state, limit=inp.limit,
                                                     cursor=inp.cursor)))


def plan_bank_feed_query(inp, ctx, s) -> Plan:
    return Plan(m.ImportQueryOutput(**bank_feeds.query(s, account=inp.account, include_reversed=inp.include_reversed,
                                                       limit=inp.limit, cursor=inp.cursor)))


def plan_bank_line_query(inp, ctx, s) -> Plan:
    return Plan(m.LineQueryOutput(**bank_feeds.line_query(s, inp)))


def _read(name, description, inp, out, planner, *, positional=(), errors=('E_VALIDATION', 'E_RECORD_NOT_FOUND')):
    cmd = command(name, scope='company', description=description, input_model=inp, output_model=out,
                  required_role='member', capability='bank-feed', positional=list(positional),
                  error_codes=list(errors))(planner)
    cmd.resource_requirements = _READ
    return cmd


bank_feed_import = command(
    'bank-feed import', scope='company', description=_IMPORT, input_model=m.ImportInput, output_model=m.ImportOutput,
    writes={'company'}, required_role='standard', capability='bank-feed', accepts_idempotency_key=True,
    error_codes=['E_VALIDATION', 'E_RECORD_NOT_FOUND', 'E_AMOUNT_PRECISION', 'E_ACCOUNT_MISMATCH',
                 'E_ALREADY_IMPORTED', 'E_IO'])(plan_bank_feed_import)
bank_feed_import.applier(apply_bank_feed_import)
bank_feed_import.resource_requirements = _READ
bank_feed_import.plan_in_write = True

bank_feed_show = _read(
    'bank-feed show', 'Show a bank import: its file, counts, who and why, its batch and state, and its lines '
    '(`--state waiting` for those still to sort). Example: {"import_id": "01J..."}.', m.ImportSelector,
    m.ImportShowOutput, plan_bank_feed_show, positional=('import_id',))
bank_feed_query = _read(
    'bank-feed query', "List bank imports newest first, one account's or all, each with its counts and batch "
    '(reversed ones with --include-reversed).', m.ImportQueryInput, m.ImportQueryOutput, plan_bank_feed_query)
bank_line_query = _read(
    'bank-line query', 'The bank inbox: lines by account, state (waiting, matched, settled...), import, dates, words '
    'or an exact amount, with counts by state. Example: {"account": "Checking", "state": "waiting"}.',
    m.LineQueryInput, m.LineQueryOutput, plan_bank_line_query)


# ------------------------------------------------------------------ reverse and move (increment 2)

_REVERSE = (
    "Undo a bank import as one unit: hide its lines (their keys are released, so the file can come in again), undo "
    "its matches, delete the entries it created (with preserved history) and restore the account's record of its "
    "number and last import. Newest first per account: a later live import blocks it (--through reverses the newer "
    "ones with it). Refused (E_BATCH_BLOCKED) naming every blocker and what clears it; then nothing changes. "
    '--dry-run shows the plan and a fingerprint. Example: {"import_id": "01J..."}.')
_MOVE = (
    "Move a bank import to the account it belongs in, in one step: reverse it, then import its own stored lines "
    "into the target (no file needed), deduped, matched and checked there; the new import records moved_from. "
    'Refused whole if the reversal is blocked or the target refuses. Example: {"import_id": "01J...", "to": '
    '"Savings"}.')


_PENDING = m.ReverseOutput(imports=(), state='live', lines=0, matches=0, entries=0, reversed_now=0, remaining=0,
                           steps=(), blockers=(), fingerprint=None, reversal_batches=(), summary='')


def plan_bank_feed_reverse(inp, ctx, s) -> Plan:
    from bookflow.company import bank_reversal
    if s.dry_run:
        return Plan(bank_reversal.reverse(s, ctx, inp, dry_run=True)['out'], {'input': inp})
    bank_reversal.import_row(s, inp.import_id)
    return Plan(_PENDING, {'input': inp})


def apply_bank_feed_reverse(plan, ctx, s) -> Applied:
    from bookflow.commands.import_cmds import _finalized
    from bookflow.company import bank_reversal
    return _finalized(s, ctx, 'bank-feed.reverse', lambda: bank_reversal.reverse(
        s, ctx, plan.data['input'], dry_run=False)['out'].model_dump(), m.ReverseOutput)


def plan_bank_feed_move(inp, ctx, s) -> Plan:
    from bookflow.company import bank_reversal
    if s.dry_run:
        return Plan(bank_reversal.move(s, ctx, inp, dry_run=True), {'input': inp})
    bank_reversal.import_row(s, inp.import_id)
    return Plan(m.MoveOutput(reversal=_PENDING, target=None), {'input': inp})


def apply_bank_feed_move(plan, ctx, s) -> Applied:
    from bookflow.commands.import_cmds import _finalized
    from bookflow.company import bank_reversal
    return _finalized(s, ctx, 'bank-feed.move', lambda: bank_reversal.move(
        s, ctx, plan.data['input'], dry_run=False).model_dump(), m.MoveOutput)


_REVERSE_ERRORS = ['E_VALIDATION', 'E_RECORD_NOT_FOUND', 'E_BATCH_BLOCKED', 'E_VERSION_CONFLICT']
bank_feed_reverse = command(
    'bank-feed reverse', scope='company', description=_REVERSE, input_model=m.ReverseInput,
    output_model=m.ReverseOutput, writes={'company'}, required_role='standard', capability='bank-feed',
    positional=['import_id'], error_codes=_REVERSE_ERRORS)(plan_bank_feed_reverse)
bank_feed_reverse.applier(apply_bank_feed_reverse)
bank_feed_reverse.resource_requirements = _READ
bank_feed_move = command(
    'bank-feed move', scope='company', description=_MOVE, input_model=m.MoveInput, output_model=m.MoveOutput,
    writes={'company'}, required_role='standard', capability='bank-feed', positional=['import_id'],
    error_codes=[*_REVERSE_ERRORS, 'E_ACCOUNT_MISMATCH'])(plan_bank_feed_move)
bank_feed_move.applier(apply_bank_feed_move)
bank_feed_move.resource_requirements = _READ


# ------------------------------------------------------------------ sorting the inbox (increment 3)

_ONE_OR_MANY = (' Give `line`, or `lines` for several as one batch (undone together by `bank-line undo`); a batch is '
                'all or nothing. Reversing the import undoes it too.')
_MATCH = ("Match a waiting bank line to the entry it is, already in the books; the entry is not changed. Name the "
          "entry, or leave it out to take the line's one candidate; when several fit equally they are listed, never "
          "chosen. Amounts must agree exactly." + _ONE_OR_MANY + ' Example: {"line": "01J...", "entry": "01J..."}.')
_ADD = ("Enter a waiting bank line in the books, run as you with the account's own command (check post or deposit "
        "post on a bank account, card-charge post or card-credit post on a card), so the entry is exactly the one you "
        "would type: the line's date and amount, your payee and account (or splits), class and memo (default the "
        "bank's text). No payee or account: refused, asking -- ask the person, never guess. A closed period refuses "
        "with the command's own code." + _ONE_OR_MANY
        + ' Example: {"line": "01J...", "payee": "Shell", "account": "Fuel"}.')
_UNMATCH = ("Return a bank line to waiting: undo its match or transfer link (the entry stays), or its ignore. A line "
            "entered as an entry is taken back with that entry (its delete or void) or the import's reversal; once "
            "that entry is gone, this releases the line." + _ONE_OR_MANY + ' Example: {"line": "01J..."}.')
_IGNORE = ("Set a waiting bank line aside with a reason: already_entered, not_ours, duplicate (a possible_duplicate "
           "flag) or other. `bank-line unmatch` brings it back." + _ONE_OR_MANY
           + ' Example: {"line": "01J...", "ignore_reason": "duplicate"}.')
_TRANSFER = ("Record a waiting bank line as a transfer between two of your accounts, as one entry: with its other "
             "end (`pair`, or the one proposed when exactly one fits both ways), both lines link to an existing "
             "transfer of that amount nearby, or to one posted with transfer post; with `to` and no line yet, the "
             "transfer is posted now and the other account's line matches it when imported. Several fits are listed, "
             'never chosen. Example: {"line": "01J...", "pair": "01J..."}.')
_UNDO = ("Undo a bank-line batch (the batch_id of a --lines act) as one unit: delete the entries it made (with "
         "preserved history, by each family's own command) and return its lines to how they stood. Refused, naming "
         'each blocker, when a line was sorted again since or an entry was edited or used. --dry-run shows the plan. '
         'Example: {"batch": "01J..."}.')
_SORT_ERRORS = ['E_VALIDATION', 'E_RECORD_NOT_FOUND', 'E_VERSION_CONFLICT']
_POST_ERRORS = [*_SORT_ERRORS, 'E_AMOUNT_PRECISION', 'E_PERIOD_CLOSED', 'E_INACTIVE_REFERENCE', 'E_DUPLICATE_NUMBER']


def _sorter(name, model, work, description, errors=_SORT_ERRORS, output=m.SortOutput, positional=()):
    from bookflow.commands.import_cmds import _finalized

    def planner(inp, ctx, s):
        if s.dry_run:
            return Plan(output(**work(s, ctx, inp, dry_run=True)), {'input': inp})
        return Plan(output(**_PENDING_SORT) if output is m.SortOutput else output(**_pending_undo(inp)), {'input': inp})

    def applier(plan, ctx, s):
        return _finalized(s, ctx, name.replace(' ', '.'), lambda: work(s, ctx, plan.data['input'], dry_run=False),
                          output)
    cmd = command(name, scope='company', description=description, input_model=model, output_model=output,
                  writes={'company'}, required_role='standard', capability='bank-feed', positional=list(positional),
                  error_codes=list(errors))(planner)
    cmd.applier(applier)
    cmd.resource_requirements = _READ
    return cmd


_PENDING_SORT = dict(lines=(), entries=(), batch_id=None, summary='')


def _pending_undo(inp):
    return dict(batch_id=inp.batch, state='live', lines=0, entries=0, reversed_now=0, remaining=0, steps=(),
                blockers=(), fingerprint=None, reversal_batch_id=None, summary='')


def _sort_module():
    from bookflow.company import bank_sort
    return bank_sort


bank_line_match = _sorter('bank-line match', m.MatchInput,
                          lambda s, ctx, inp, dry_run: _sort_module().act(s, ctx, 'match', inp, dry_run=dry_run), _MATCH)
bank_line_unmatch = _sorter('bank-line unmatch', m.LinesInput,
                            lambda s, ctx, inp, dry_run: _sort_module().act(s, ctx, 'unmatch', inp, dry_run=dry_run),
                            _UNMATCH)
bank_line_ignore = _sorter('bank-line ignore', m.IgnoreInput,
                           lambda s, ctx, inp, dry_run: _sort_module().act(s, ctx, 'ignore', inp, dry_run=dry_run),
                           _IGNORE)
bank_line_add = _sorter('bank-line add', m.AddInput,
                        lambda s, ctx, inp, dry_run: _sort_module().add(s, ctx, inp, dry_run=dry_run), _ADD,
                        errors=_POST_ERRORS)
bank_line_transfer = _sorter('bank-line transfer', m.TransferInput,
                             lambda s, ctx, inp, dry_run: _sort_module().transfer(s, ctx, inp, dry_run=dry_run),
                             _TRANSFER, errors=_POST_ERRORS)
bank_line_undo = _sorter('bank-line undo', m.UndoInput,
                         lambda s, ctx, inp, dry_run: _sort_module().undo(s, ctx, inp, dry_run=dry_run), _UNDO,
                         errors=['E_VALIDATION', 'E_RECORD_NOT_FOUND', 'E_BATCH_BLOCKED', 'E_VERSION_CONFLICT'],
                         output=m.UndoOutput, positional=('batch',))
