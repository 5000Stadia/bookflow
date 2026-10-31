"""`bank-feed` and `bank-line`: statement files into the bank inbox (R108 increment 1, design/bank-feeds.md).

The work lives in ``company/bank_feeds.py``. Capability `bank-feed` (reads at member, writes at standard), with
`ledger.read` beside it: the inbox shows the books' entries a line matched. Import records one batch
(kind `bank-import`); any entries live rules add belong to that batch and reverse with it.
"""
import json

from bookflow.company import bank_feeds, bank_feed_models as m
from bookflow.core.registry import Applied, Plan, command

_READ = (('ledger.read', 'member'),)

_IMPORT = (
    "Bring a bank or card statement file (OFX, QFX or CSV) into the account's bank inbox. Overlaps never duplicate: a "
    "line is known by its FITID (or, without one, a hash of the line and its occurrence in the file), so a download "
    "that overlaps an earlier one adds only the new lines; a FITID reused for another amount is kept apart and "
    "flagged; a new line that looks like one already in under another id or text is flagged possible_duplicate, never "
    "dropped. Each line the matcher pairs surely with an entry is matched; the rest wait. Import itself posts "
    "nothing; live auto-add rules may post entries in its batch. A file "
    "with any unreadable line is refused whole, listing every problem. An OFX file is checked against the account "
    "(E_ACCOUNT_MISMATCH), a CSV by its content (account_confirmed goes on); the same file twice is refused without again (E_ALREADY_IMPORTED). --dry-run is the "
    "preview: check the named account, dates, warnings and overlap counts before importing, then report the counts. "
    "Find statement attachments with attachment list company_info <company id> (company show supplies the id). "
    "Do not bypass an account mismatch without the owner's account decision. Sorting uses bank-line commands; "
    'reconciliation is separate. Example: {"account": "Checking", "attachment": "01J..."}.')


def plan_bank_feed_import(inp, ctx, s) -> Plan:
    prepared = bank_feeds.prepare(s, ctx, inp)
    return Plan(bank_feeds.output(prepared, dry_run=True), {'input': inp, 'prepared': prepared})


def apply_bank_feed_import(plan, ctx, s) -> Applied:
    from bookflow.commands.reconcile_cmds import _kept
    from bookflow.company import bank_rules
    prepared = _kept(s, plan.data['prepared']) or bank_feeds.prepare(s, ctx, plan.data['input'])
    # Rules are read again inside this write: one changed since a preview is read as it is now.
    bank_rules.annotate(s, prepared)
    if not any(r.get('rule', {}).get('acts') for r in prepared['new']) and \
            not bank_rules._pending_lines(s, prepared['account_id']):
        out, wrote = bank_feeds.write(s, ctx, prepared)
        return Applied(out, [], f'bank-feed import: {out.summary}', audited=wrote)
    # A live auto-add or ignore rule fits a line (or one an earlier import left for it): the import commits first,
    # then each such rule acts on its lines as its establisher, each entry by its own typed command in its own
    # transaction, recorded in the import's batch.
    from bookflow.company import batches
    from bookflow.core.dispatch import _upsert_principals
    with s.commits.operation(batches.OWNER, s.hub, s.company):
        try:
            _upsert_principals(s, ctx)
            out, _ = bank_feeds.write(s, ctx, prepared)
            s.commits.commit(s.company, batches.OWNER)
        except BaseException:
            if s.company.write_transaction:
                s.company.raw.rollback()
            raise
        runs = bank_rules.run_live(s, ctx, prepared)
    out = _with_runs(s, out, prepared, runs)
    if ctx.idempotency_key:
        from bookflow.core import idempotency
        ihash = idempotency.input_hash(plan.data['input'].model_dump(mode='json'),
                                       s.company_row['id'] if s.company_row else None)
        body = out.model_dump(mode='json')
        _sort_module()._in_tx(s, lambda: idempotency.store(s.company, s.actor.id, ctx.idempotency_key,
                                                           'bank-feed import', ihash, ctx.request_id, body))
    return Applied(out, [], f'bank-feed import: {out.summary}', finalized=True, audited=True)


def _with_runs(s, out, prepared, runs):
    """The import's result with what its live rules did: their lines as they now stand (acted on, or held back and
    why), each rule's run, and in `next` what was done and what was held back."""
    from bookflow.company import bank_sort
    ids = [v.id for v in out.lines if v.id and v.rule_id]
    now = {v['id']: v for v in bank_sort.lines(s, ids)} if ids else {}
    by_rule = {r['rule_id']: r for r in runs}
    lines = []
    for v in out.lines:
        current, run = now.get(v.id), by_rule.get(v.rule_id)
        if current is None or run is None:
            lines.append(v)
            continue
        if current['state'] != 'waiting':
            lines.append(v.model_copy(update={'state': current['state'], 'entry_id': current['entry_id'],
                                              'rule_acts': 'ignore' if current['state'] == 'ignored' else 'add',
                                              'rule_held': None}))
            continue
        held = next((h['reason'] for h in run['held'] if h['line_id'] == v.id), None)
        refused = next((f"{x['code']}: {x['message']}" for x in run['refused'] if x['line_id'] == v.id), None)
        pending = (json.loads(current['proposal'] or '{}').get('rule') or {}).get('pending')
        why = held or refused or run['reason'] or ('waits for the host to go on' if pending else run['outcome'])
        lines.append(v.model_copy(update={'rule_acts': 'held', 'rule_held': why}))
    added, ignored = sum(r['added'] for r in runs), sum(r['ignored'] for r in runs)
    step = out.next + (f' Live rules added {added} and ignored {ignored} line(s).' if added or ignored else '')
    for r in runs:
        if r['outcome'] in ('paused', 'held', 'skipped') and r['reason']:
            step += f" Bank rule {r['name']} {r['outcome']}: {r['reason']}."
        if r['held']:
            step += f" Bank rule {r['name']} left {len(r['held'])} line(s) waiting that may already be in the books."
        if r['later']:
            step += f" Bank rule {r['name']} goes on with {r['later']} more line(s) at the host's next tick."
    return out.model_copy(update={'lines': tuple(lines), 'next': step, 'automations': tuple(
        m.RuleRun(**{**r, 'refused': tuple(r['refused']), 'held': tuple(r['held'])}) for r in runs)})


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
    '(`--state waiting` for those still to sort). bank_import.counts records arrival outcomes; by_state and lines '
    'show the current inbox decisions. Example: {"import_id": "01J..."}.', m.ImportSelector,
    m.ImportShowOutput, plan_bank_feed_show, positional=('import_id',))
bank_feed_query = _read(
    'bank-feed query', "List bank imports newest first, one account's or all, each with its counts and batch "
    '(reversed ones with --include-reversed). Counts describe arrival outcomes, not current sorting; use '
    'bank-feed show for current by_state and lines.', m.ImportQueryInput, m.ImportQueryOutput, plan_bank_feed_query)
bank_line_query = _read(
    'bank-line query', "The bank inbox: lines by account, state, import, dates, words or exact amount, with counts. "
    "Page with limit and cursor. Inspect proposals and warnings; match only supported candidates, add with known "
    "payee/category, and accept transfers with bank-line transfer. Leave ambiguous lines waiting and report their "
    "ids and what is missing; never guess a category or ignore a line merely to empty the inbox. Matching does "
    'not reconcile or clear an entry. Example: {"account": "Checking", "state": "waiting"}.',
    m.LineQueryInput, m.LineQueryOutput, plan_bank_line_query)


# ------------------------------------------------------------------ reverse and move (increment 2)

_REVERSE = (
    "Undo a bank import as one unit: hide its lines (their keys are released, so the file can come in again), undo "
    "its matches, delete the entries it created (with preserved history) and restore the account's record of its "
    "number and last import. Newest first per account: a later live import blocks it (--through reverses the newer "
    "ones with it). Refused (E_BATCH_BLOCKED) naming every blocker and what clears it; then nothing changes. "
    "--dry-run shows the plan and a fingerprint. Pass it on the real call; follow next until reversed. "
    "For a wrong account, inspect this preview but execute bank-feed move instead of reversing first: move needs "
    "the live import. Report blockers; do not undo unrelated work to clear them without instruction. "
    'Example: {"import_id": "01J..."}.')
_MOVE = (
    "Move a bank import to the account it belongs in, in one step: reverse it, then import its own stored lines "
    "into the target (no file needed), deduped, matched and checked there; the new import records moved_from. "
    "Preview move with --dry-run too and pass reversal.fingerprint; follow next until target is present. "
    "Created entries are taken back; the target lines are sorted afresh and may need categorizing again. "
    "The target must be the owner's intended account; do not guess it. "
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
            "that entry is gone (entry_gone), this releases the line." + _ONE_OR_MANY + ' Example: {"line": "01J..."}.')
_IGNORE = ("Set a waiting bank line aside with a reason: already_entered, not_ours, duplicate (a possible_duplicate "
           "flag) or other. `bank-line unmatch` brings it back." + _ONE_OR_MANY
           + ' Example: {"line": "01J...", "ignore_reason": "duplicate"}.')
_TRANSFER = ("Record a waiting bank line as a transfer between two of your accounts, as one entry: with its other "
             "end (`pair`, or the one proposed when exactly one fits both ways), both lines link to an existing "
             "transfer of that amount nearby, or to one posted with transfer post; with `to` and no line yet, the "
             "transfer is posted now and the other account's line matches it when imported; that line, arriving "
             'later, completes it (never a second transfer). Several fits are listed, never chosen. '
             'Example: {"line": "01J...", "pair": "01J..."}.')
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
        return Plan(output(**_PENDING_SORT) if issubclass(output, m.SortOutput) else output(**_pending_undo(inp)),
                    {'input': inp})

    def applier(plan, ctx, s):
        applied = _finalized(s, ctx, name.replace(' ', '.'), lambda: work(s, ctx, plan.data['input'], dry_run=False),
                             output)
        if ctx.idempotency_key:
            # Its writes committed through nested commands, so the key is kept here: a retry with it returns this
            # result. (Lost before this, a retry is refused by the line's state; nothing is ever done twice.)
            from bookflow.core import idempotency
            ihash = idempotency.input_hash(plan.data['input'].model_dump(mode='json'),
                                           s.company_row['id'] if s.company_row else None)
            out = applied.output.model_dump(mode='json')
            _sort_module()._in_tx(s, lambda: idempotency.store(s.company, s.actor.id, ctx.idempotency_key, name, ihash,
                                                               ctx.request_id, out))
        return applied
    cmd = command(name, scope='company', description=description, input_model=model, output_model=output,
                  writes={'company'}, required_role='standard', capability='bank-feed', positional=list(positional),
                  accepts_idempotency_key=True, error_codes=list(errors))(planner)
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

_DETAILS = (" The selection must come to the line exactly; otherwise it is refused showing the difference remaining "
            "(`details.difference`). The act is its own batch (`bank-line undo <batch_id>` takes it back) and is "
            "recorded on the line's import, so reversing the import takes it back too.")
_DEPOSIT = ("Add more details: enter a waiting bank line of money in as a deposit of the customer payments and sales "
            "receipts waiting in Undeposited Funds that it banked, run as you with `deposit post` on the line's "
            "account and date, so the deposit is exactly the one you would type. A payment already deposited is "
            "refused by `deposit post`'s own code. `adjustment_account` enters a difference as one more row (other money "
            "received from a payee, or money kept back such as a bank fee) instead of refusing." + _DETAILS
            + ' Example: {"line": "01J...", "payments": ["01J...", "01J...", "01J..."]}.')
_PAY_BILLS = ("Add more details: mark open bills paid from a waiting bank or card line of money out, run as you "
              "with `bill pay` from the line's account on its date, so the payment is exactly the one you would "
              "type. One line is one payment to one vendor; name `amount` per bill for a part payment. No method: "
              "refused, asking -- ask the person, never guess." + _DETAILS
              + ' Example: {"line": "01J...", "bills": [{"bill": "01J..."}], "method": "Bank Transfer"}.')
_DETAIL_ERRORS = [*_POST_ERRORS, 'E_VALUE_RANGE', 'E_DEPOSIT_SOURCE_INELIGIBLE', 'E_DEPOSIT_SOURCE_CLAIMED',
                  'E_DEPOSIT_SOURCE_INVALID', 'E_DEPOSIT_DATE_BEFORE_SOURCE', 'E_APPLICATION_CAPACITY',
                  'E_APPLICATION_INCOMPATIBLE', 'E_APPLICATION_INACTIVE']
bank_line_deposit = _sorter('bank-line deposit', m.DepositLineInput,
                            lambda s, ctx, inp, dry_run: _sort_module().details(s, ctx, 'deposit', inp,
                                                                                dry_run=dry_run),
                            _DEPOSIT, errors=_DETAIL_ERRORS, output=m.DetailsOutput)
bank_line_pay_bills = _sorter('bank-line pay-bills', m.PayBillsLineInput,
                              lambda s, ctx, inp, dry_run: _sort_module().details(s, ctx, 'bills', inp,
                                                                                  dry_run=dry_run),
                              _PAY_BILLS, errors=_DETAIL_ERRORS, output=m.DetailsOutput)
