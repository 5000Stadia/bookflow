"""Inputs and outputs of `import` and `batch` (R107 increments 0 and 1)."""
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class _Input(BaseModel):
    model_config = ConfigDict(extra='forbid')


class ImportKindsInput(_Input):
    pass


class ImportTemplateInput(_Input):
    kind: str = Field(min_length=1, max_length=32, description='Import kind, as `import kinds` lists it.')


class ImportPreviewInput(_Input):
    kind: str = Field(min_length=1, max_length=32, description=(
        'What each row is: customer, vendor, item, account, term, class, customer-type, vendor-type, job-type, '
        'payment-method, ship-method, sales-rep, price-level, item-category, customer-message, employee, other-name.'))
    content: str | None = Field(None, min_length=1, max_length=16_000_000, description=(
        'The file as text: a CSV, or cells pasted from a spreadsheet (tab text). Give this or `attachment`.'))
    attachment: str | None = Field(None, min_length=26, max_length=26, description=(
        'An attachment holding the file (`attachment add company_info <company id> FILE`); read as bytes, so its '
        'encoding is detected. Give this or `content`.'))
    file_name: str | None = Field(None, max_length=255, description='A label for the file in results.')
    source: str | None = Field(None, min_length=1, max_length=200, description=(
        'The named origin of files like this one ("Square customer export"); row keys are unique within it, so a '
        're-run of the same source never duplicates. Defaults to "<kind> import".'))
    mode: Literal['create', 'update', 'upsert'] = Field('create', description=(
        'create: make records the books lack (a match is `unchanged` or `differs`, never changed); update: change only '
        'matching records; upsert: both.'))
    columns: dict[str, str | None] | None = Field(None, description=(
        'File header to input path (`{"Cust Name": "name", "Town": "billing_address.city", "Fax": null}`), '
        'over the guessed mapping; null ignores a column. Paths are the kind\'s create-command fields.'))
    constants: dict[str, Any] | None = Field(None, description=(
        'Input path to a value given to every row that has none (`{"type": "service"}`).'))
    key: list[str] | None = Field(None, max_length=5, description=(
        'Input paths that identify a row\'s record (default: name, with parent_id for jobs and sub-items; an '
        'account\'s number when mapped).'))
    delimiter: Literal[',', ';', '\t', '|'] | None = Field(None, description='Cell delimiter; sniffed when omitted.')
    header_row: bool = Field(True, description='False when the first row is data (columns become Column A, B...).')
    skip_rows: int = Field(0, ge=0, le=50, description='Title rows above the header to pass over.')
    decimal: Literal['.', ','] | None = Field(None, description='Decimal mark of money columns; read when omitted.')
    date_order: Literal['MDY', 'DMY', 'YMD'] | None = Field(None, description=(
        'Day and month order of date columns; read from every value when omitted, and asked when all are ambiguous.'))
    atomic: bool = Field(False, description=(
        'True: the first refusal while running undoes everything the run made (through the batch ledger), and a run '
        'whose preview refused rows does not start.'))
    stop_after_refusals: int = Field(100, ge=1, le=100_000, description=(
        'Refusals in one `import run` call after which the run stops itself, keeping what it did.'))


class ImportRunInput(_Input):
    run: str = Field(min_length=26, max_length=26, description='The run `import preview` returned.')
    limit: int = Field(500, ge=1, le=20_000, description='Most rows to run in this call (a slice).')
    seconds: int = Field(20, ge=1, le=600, description='Stop the slice after about this many seconds.')


class ImportRunSelector(_Input):
    run: str = Field(min_length=26, max_length=26, description='The import run.')


class ImportQueryInput(_Input):
    kind: str | None = Field(None, max_length=32, description='Only runs of this kind.')
    state: Literal['previewed', 'running', 'stopped', 'waiting', 'done', 'cancelled', 'rolled_back'] | None = Field(
        None, description='Only runs in this state.')
    limit: int = Field(50, ge=1, le=200, description='Most runs to return.')
    cursor: str | None = Field(None, max_length=26, description='`next_cursor` of the previous page.')


class ImportRowsInput(_Input):
    run: str = Field(min_length=26, max_length=26, description='The import run.')
    phase: Literal['preview', 'run'] | None = Field(None, description=(
        'Outcomes of the preview or of the run; the run once it has started, else the preview.'))
    status: str | None = Field(None, max_length=16, description='Only rows with this status (refused, created...).')
    limit: int = Field(100, ge=1, le=1000, description='Most rows to return.')
    cursor: int | None = Field(None, ge=0, description='`next_cursor` of the previous page (a row number).')


class ImportReverseInput(_Input):
    run: str = Field(min_length=26, max_length=26, description='The import run whose batch to reverse.')
    limit: int = Field(200, ge=1, le=20_000, description='Most records to reverse in this call (a slice).')
    fingerprint: str | None = Field(None, min_length=64, max_length=64, description=(
        'The `fingerprint` a dry run returned; the reversal is refused if the batch changed since.'))


class BatchShowInput(_Input):
    batch: str = Field(min_length=26, max_length=26, description='The batch.')


class BatchQueryInput(_Input):
    kind: str | None = Field(None, max_length=32, description='Only batches of this kind (import, reversal...).')
    state: Literal['live', 'reversing', 'blocked', 'reversed', 'reversed_partly'] | None = Field(
        None, description='Only batches in this state.')
    scope: str | None = Field(None, max_length=200, description='Only batches in this scope.')
    limit: int = Field(50, ge=1, le=200, description='Most batches to return.')
    cursor: str | None = Field(None, max_length=26, description='`next_cursor` of the previous page.')


class ImportKindsOutput(BaseModel):
    kinds: list[dict[str, Any]] = Field(description=(
        'Each kind: name, command, required fields, default key, references and an example header.'))


class ImportTemplateOutput(BaseModel):
    kind: str = Field(description='The kind.')
    command: str = Field(description='The command each row runs.')
    header: list[str] = Field(description='A header row that maps without asking.')
    example: list[str] = Field(description='One example row.')
    content: str = Field(description='Header and example as CSV text.')
    fields: list[dict[str, Any]] = Field(description='Every mappable input path, its type and whether required.')


class ImportRunOutput(BaseModel):
    run: dict[str, Any] = Field(description=(
        'The run: id, kind, mode, state, source, file digest, encoding, delimiter, rows, batch, who, why.'))
    counts: dict[str, int] = Field(description='Rows by outcome (create, created, already_in, refused...).')
    mapping: dict[str, Any] = Field(description='Columns to input paths (with how each was found), key, formats.')
    unknown_references: list[dict[str, Any]] = Field(description=(
        'Unknown references grouped by field and value, with the rows they touch and up to three suggestions.'))
    decisions: list[dict[str, Any]] = Field(description='What the preview needs decided before the rows can run.')
    refused: list[dict[str, Any]] = Field(description='The first refused rows: row, line, column, code, message.')
    sample: list[dict[str, Any]] = Field(description='A sample of the exact command inputs the rows become.')
    notes: list[str] = Field(description='Things worth knowing: a guessed encoding, rows keyed by content.')
    done_now: int = Field(0, description='Rows this call ran.')
    next: str | None = Field(description='The command to run next, or null when the run is finished.')


class ImportQueryOutput(BaseModel):
    items: list[dict[str, Any]] = Field(description='Runs, newest first, with their counts.')
    next_cursor: str | None = Field(description='Pass as `cursor` for the next page; null on the last.')


class ImportRowsOutput(BaseModel):
    run: str = Field(description='The run.')
    phase: str = Field(description='preview or run.')
    items: list[dict[str, Any]] = Field(description='Rows: row, line, status, code, message, column, record.')
    next_cursor: int | None = Field(description='Pass as `cursor` for the next page; null on the last.')


class ImportErrorsOutput(BaseModel):
    run: str = Field(description='The run.')
    file_name: str = Field(description='A file name for the error file.')
    rows: int = Field(description='Refused rows in it.')
    content: str = Field(description=(
        'CSV of the refused rows with their own columns plus code, message and line, ready to correct and import '
        'again as the same source; a cell starting =, +, - or @ is written with a leading apostrophe.'))


class BatchOutput(BaseModel):
    batch: dict[str, Any] = Field(description=(
        'The batch: kind, scope, ordering, label, state, counts, who, how, for whom, why, reversal; `reversible` and '
        '`first_blocker` from a plan; `effects` (up to 200) on show.'))


class BatchQueryOutput(BaseModel):
    items: list[dict[str, Any]] = Field(description='Batches, newest first, with `reversible` and the first blocker.')
    next_cursor: str | None = Field(description='Pass as `cursor` for the next page; null on the last.')


class BatchReverseOutput(BaseModel):
    batch: dict[str, Any] = Field(description='The batch after this call.')
    state: str = Field(description='live (dry run), reversing (more slices to run) or reversed.')
    reversed_now: int = Field(description='Records reversed by this call.')
    remaining: int = Field(description='Records still to reverse.')
    steps: list[dict[str, Any]] = Field(description='The plan: per record, the inverse commands still to run.')
    blockers: list[dict[str, Any]] = Field(description='What blocks the reversal (empty when it can run).')
    fingerprint: str | None = Field(description='Pass to the real run so it refuses if the batch changed since.')
    reversal_batch: str | None = Field(description='The batch recording the reversal, once begun.')
    event_id: str | None = Field(description='Audit event that finished the reversal.')
    next: str | None = Field(None, description='The command to run next, or null.')
