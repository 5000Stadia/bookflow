"""Inputs and outputs of `bank-feed` and `bank-line` (R108 increment 1, design/bank-feeds.md §3, §9)."""
from typing import Literal

from pydantic import Field, model_validator

from bookflow.commands.reconcile_import_cmds import CsvMapping
from bookflow.company import statement_files as files
from bookflow.company.reconciliation_commands_models import AccountSelector, Count, Currency, ID, Model, Units

LineState = Literal['waiting', 'matched', 'added', 'transfer', 'ignored', 'settled']
Outcome = Literal['new', 'already_in', 'in_statement', 'repeated_in_file']


class ImportInput(Model):
    account: AccountSelector
    content: str | None = Field(None, min_length=1, max_length=20_000_000, description=(
        "The statement file's text, exactly as downloaded from the bank. Give this or `attachment`."),
        json_schema_extra={'multiline': True, 'text_file': '.csv,.ofx,.qfx,.txt,text/csv,text/plain'})
    attachment: str | None = Field(None, min_length=1, max_length=26, description=(
        'An attachment in this company holding the file (`attachment add company_info <company id> FILE`). Give this '
        'or `content`.'))
    format: Literal['auto', 'ofx', 'qfx', 'csv'] = Field('auto', description='File format; auto reads it.')
    csv_mapping: CsvMapping = Field(default_factory=CsvMapping, description='CSV columns; ignored for OFX and QFX.')
    mapping_name: str = Field('', max_length=80, description="Use the CSV mapping saved under this name for the "
                                                             "account (shared with `reconcile import`).")
    save_mapping: str = Field('', max_length=80, description='Save the CSV mapping used under this name.')
    match_days: int = Field(files.MATCH_DAYS, strict=True, ge=0, le=60,
                            description='Days apart a line and an entry of the same amount may be and still match.')
    suggest_days: int = Field(files.SUGGEST_DAYS, strict=True, ge=0, le=120,
                              description='Days apart for a same-amount entry to be offered as a suggestion.')
    auto_match: bool = Field(True, description="Link each line the matcher pairs surely with its entry (the "
                                               "anchor's default); false leaves them waiting with the suggestion.")
    again: bool = Field(False, description='Import a file this account already received (its lines already in '
                                           'are skipped all the same).')
    confirm_account: bool = Field(False, description=(
        "Go on although the file's account number or kind differs from what this account last received "
        '(needs bank-feed.connect).'))
    account_confirmed: bool = Field(False, description=(
        "Go on although a file with no account number (CSV) looks like another account's statement."))
    limit: int = Field(100, strict=True, ge=0, le=1000, description='Lines to return in the result (the counts '
                                                                    'always cover the whole file).')

    @model_validator(mode='after')
    def target(self):
        if (self.content is None) == (self.attachment is None):
            raise ValueError('give exactly one of content or attachment')
        if self.suggest_days < self.match_days:
            raise ValueError('suggest_days must be at least match_days')
        return self


class Suggestion(Model):
    entry_id: ID
    date: str
    amount: Units
    number: str
    payees: tuple[str, ...]


class Line(Model):
    id: ID | None = Field(None, description='The inbox line (null on a dry run, or for a line not imported).')
    line_key: str
    fitid: str | None
    date: str
    amount: Units = Field(description="Minor units in the bank's sign: positive is money in, or a card payment.")
    amount_decimal: str
    payee: str
    memo: str
    number: str
    outcome: Outcome = Field(description='new; already_in (the inbox has it); in_statement (a reconciliation has it); '
                                         'repeated_in_file.')
    state: LineState | None = Field(None, description='Where a new line lands; null for a line not imported.')
    reason: str = ''
    entry_id: ID | None = Field(None, description='The transaction it is matched to.')
    suggestions: tuple[Suggestion, ...] = ()
    possible_duplicate_of: str | None = Field(None, description='A live inbox line this one probably repeats.')
    warnings: tuple[str, ...] = ()


class AccountCheck(Model):
    account_id: ID
    account: str
    type: str
    last_import_at: str | None
    file_bank_number: str | None = None
    file_account_last4: str | None = None
    file_kind: Literal['bank', 'card'] | None = None
    known_bank_number: str | None = None
    known_account_last4: str | None = None
    known_kind: Literal['bank', 'card'] | None = None
    learns: str | None = Field(None, description='The number this import teaches the account (first or confirmed).')
    status: Literal['first', 'same', 'confirmed', 'no_id'] = Field(description=(
        "first (nothing known yet; this import teaches it), same, confirmed (differs; confirm_account given), "
        'no_id (a CSV carries none: check the overlap instead).'))


class Overlap(Model):
    account_id: ID
    account: str
    lines: Count
    percent: Count


class ImportCounts(Model):
    lines_in_file: Count
    new: Count
    already_in: Count
    in_statement: Count
    repeated_in_file: Count
    soft_duplicates: Count
    fitid_reused: Count
    matched: Count
    settled: Count
    waiting: Count


class ImportOutput(Model):
    import_id: ID | None = Field(description='The import (null on a dry run, or when nothing was new).')
    batch_id: ID | None = Field(description='Its batch, through which it is reversed as one unit.')
    account_id: ID
    currency: Currency
    format: Literal['ofx', 'qfx', 'csv']
    file_sha256: str
    date_from: str | None
    date_to: str | None
    sum: Units = Field(description="The new lines' total, bank sign.")
    sum_decimal: str
    counts: ImportCounts
    summary: str
    account_check: AccountCheck
    overlap_elsewhere: tuple[Overlap, ...] = Field(description="Other feed accounts already holding this file's "
                                                               'lines: the sign of a file sent to the wrong account.')
    warnings: tuple[str, ...]
    lines: tuple[Line, ...]
    has_more_lines: bool
    next: str


class ImportSelector(Model):
    import_id: ID = Field(description='The import.')
    state: LineState | None = Field(None, description='Only lines in this state.')
    limit: int = Field(100, strict=True, ge=1, le=1000)
    cursor: str | None = Field(None, max_length=64)


class ImportRow(Model):
    id: ID
    batch_id: ID
    batch_state: str
    account_id: ID
    account: str
    format: str
    file_sha256: str
    attachment_id: ID | None
    mapping_name: str | None
    date_from: str | None
    date_to: str | None
    lines_in_file: Count
    lines_new: Count
    counts: dict[str, int]
    warnings: tuple[str, ...]
    moved_from: str | None
    reason: str | None
    created_by: str
    created_via: str
    on_behalf_of: str | None
    created_at: str


class InboxLine(Model):
    id: ID
    import_id: ID
    account_id: ID
    line_key: str
    fitid: str | None
    date: str
    amount: Units
    amount_decimal: str
    payee: str
    memo: str
    number: str
    kind: str
    state: LineState
    live: bool
    entry_id: ID | None
    link_how: str | None
    reason: str | None
    possible_duplicate_of: str | None
    warnings: tuple[str, ...]
    version: int


class ImportShowOutput(Model):
    bank_import: ImportRow
    part_way: dict | None = Field(None, description=(
        'A reversal or move of this import under way or blocked: entries taken back and left, the lines still in '
        'the inbox, the account a move goes to, and `next`, the command that finishes it; null otherwise.'))
    by_state: dict[str, int]
    lines: tuple[InboxLine, ...]
    next_cursor: str | None


class ImportQueryInput(Model):
    account: AccountSelector | None = None
    include_reversed: bool = False
    limit: int = Field(50, strict=True, ge=1, le=500)
    cursor: str | None = Field(None, max_length=64)


class ImportQueryOutput(Model):
    imports: tuple[ImportRow, ...]
    next_cursor: str | None


class LineQueryInput(Model):
    account: AccountSelector | None = None
    state: LineState | None = None
    import_id: ID | None = Field(None, description='Only lines this import brought in.')
    date_from: str | None = Field(None, pattern=r'^\d{4}-\d{2}-\d{2}$')
    date_to: str | None = Field(None, pattern=r'^\d{4}-\d{2}-\d{2}$')
    text: str | None = Field(None, min_length=1, max_length=200, description='Words in the payee, memo or number.')
    amount: str | None = Field(None, max_length=40, description='An exact amount, bank sign ("-4.50").')
    include_reversed: bool = False
    limit: int = Field(100, strict=True, ge=1, le=1000)
    cursor: str | None = Field(None, max_length=64)


class LineQueryOutput(Model):
    lines: tuple[InboxLine, ...]
    counts: dict[str, int] = Field(description='Live lines matching the filters other than state, by state.')
    next_cursor: str | None


# ------------------------------------------------------------------ reverse and move (increment 2)

# Created entries a reversal deletes per call. Measured (tools/measure_bank_reversal.py): about 0.25 s an entry, so 200
# took 49 s in one call, close to an agent tool call's minute; 100 keeps a slice near 25 s.
SLICE = 100

class ReverseInput(Model):
    import_id: ID = Field(description='The import to reverse.')
    through: bool = Field(False, description=(
        'Also reverse every later live import of the same account, newest first, in one plan (each import blocks '
        'the reversal of those before it).'))
    limit: int = Field(SLICE, strict=True, ge=1, le=20_000, description=(
        'Most created entries to delete in this call (a slice, about a quarter second each); the inbox is released '
        'with the last one.'))
    fingerprint: str | None = Field(None, min_length=64, max_length=64, description=(
        'The `fingerprint` a dry run returned; refused if anything changed since.'))


class MoveInput(Model):
    import_id: ID = Field(description='The import that went into the wrong account.')
    to: AccountSelector = Field(description='The account its lines belong in.')
    confirm_account: bool = Field(False, description=(
        "Go on although the import's account number or kind differs from what the target last received "
        '(needs bank-feed.connect).'))
    account_confirmed: bool = Field(False, description=(
        "Go on although lines with no account number look like another account's statement."))
    auto_match: bool = Field(True, description='Link each line the matcher pairs surely with its entry.')
    match_days: int = Field(files.MATCH_DAYS, strict=True, ge=0, le=60, description='As for `bank-feed import`.')
    suggest_days: int = Field(files.SUGGEST_DAYS, strict=True, ge=0, le=120, description='As for `bank-feed import`.')
    limit: int = Field(SLICE, strict=True, ge=1, le=20_000, description='Most created entries to delete in this call.')
    fingerprint: str | None = Field(None, min_length=64, max_length=64, description=(
        'The `fingerprint` a dry run returned; refused if anything changed since.'))


class ReverseOutput(Model):
    imports: tuple[ID, ...] = Field(description='The imports reversed (or to reverse), newest first.')
    state: str = Field(description='live (dry run), reversing (more slices to run), blocked, or reversed.')
    lines: Count = Field(description='Inbox lines the reversal hides (their keys are released).')
    matches: Count = Field(description='Matches it undoes.')
    entries: Count = Field(description='Entries the imports created that it deletes (with preserved history).')
    reversed_now: Count = Field(description='Entries deleted by this call.')
    remaining: Count = Field(description='Steps still to run.')
    steps: tuple[dict, ...] = Field(description='The plan: each entry to delete, then the inbox release.')
    blockers: tuple[dict, ...] = Field(description=(
        'What blocks it (empty when it can run): code, the record, message, and the command that clears it.'))
    total_blockers: Count = 0
    fingerprint: str | None = Field(description='Pass to the real run so it is refused if anything changed since.')
    reversal_batches: tuple[ID, ...] = Field(description='The batches recording the reversals, once begun.')
    summary: str
    next: str | None = Field(None, description='The command to run next, or null.')


class MoveOutput(Model):
    reversal: ReverseOutput
    target: ImportOutput | None = Field(description=(
        'The re-import into the target: a preview on a dry run, the new import once done; null while the reversal '
        'still has slices to run.'))
    next: str | None = Field(None, description='The command to run next, or null.')
