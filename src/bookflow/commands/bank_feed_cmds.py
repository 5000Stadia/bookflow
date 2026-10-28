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
