"""Inputs and outputs of `import` and `batch` (R107 increments 0 and 1)."""
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class _Input(BaseModel):
    model_config = ConfigDict(extra='forbid')


class ImportKindsInput(_Input):
    pass


class ImportTemplateInput(_Input):
    kind: str = Field(min_length=1, max_length=32, description='Import kind, as `import kinds` lists it.')


class ImportCheckInput(_Input):
    kind: str = Field(min_length=1, max_length=32, description=(
        'What each row is: customer, vendor, item, account, term, class, customer-type, vendor-type, job-type, '
        'payment-method, ship-method, sales-rep, price-level, item-category, customer-message, employee, other-name.'))
    content: str | None = Field(None, min_length=1, max_length=16_000_000, description=(
        'The file as text: a CSV, or cells pasted from a spreadsheet (tab text). Give this or `attachment`.'))
    attachment: str | None = Field(None, min_length=26, max_length=26, description=(
        'An attachment holding the file, a CSV or an .xlsx workbook (`attachment add company_info <company id> '
        'FILE`); read as bytes, so its encoding is detected. Give this or `content`.'))
    sheet: str | None = Field(None, min_length=1, max_length=100, description=(
        'The worksheet of an .xlsx file to read (default: the first). Values as last saved; no formula is run.'))
    source: str | None = Field(None, min_length=1, max_length=200, description=(
        'The named origin of files like this one ("Square customer export"); row keys are unique within it, so a '
        're-run of the same source never duplicates. Defaults to the saved mapping\'s source, else "<kind> import".'))
    mapping: str | None = Field(None, min_length=1, max_length=200, description=(
        'Use this source\'s saved mapping (`import-mapping query`), as after reading a probable match\'s diff. A file '
        'lacking a column it maps is refused. Without it, a saved mapping whose headers equal the file\'s is used.'))
    mode: Literal['create', 'update', 'upsert'] = Field('create', description=(
        'create: make records the books lack (a match is `unchanged` or `differs`, never changed); update: change only '
        'matching records; upsert: both.'))
    columns: dict[str, str | None] | None = Field(None, description=(
        'File header to input path (`{"Cust Name": "name", "Town": "billing_address.city", "Fax": null}`), '
        'over the guessed or saved mapping; null ignores a column. Paths are the kind\'s create-command fields.'))
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


class ImportPreviewInput(ImportCheckInput):
    file_name: str | None = Field(None, max_length=255, description='A label for the file in results.')
    partial: bool = Field(False, description=(
        'False (the default): all or nothing. A file the check finds any problem in is refused whole, a run starts '
        'only when its preview refused no row, and a row refused while running undoes the run. True: each bad row is '
        'refused alone and the others import.'))
    stop_after_refusals: int = Field(100, ge=1, le=100_000, description=(
        'Refusals in one `import run` call after which a partial run stops itself, keeping what it did.'))
    allow: list[Literal['differs', 'skipped']] = Field(default_factory=list, description=(
        'All or nothing refuses a row that would change nothing because a record with its name differs (`differs`) '
        'or, in update mode, matches none (`skipped`); name the outcomes to accept instead.'))


class ImportAnswer(_Input):
    field: str = Field(min_length=1, max_length=200, description='The input path the unknown reference is in (terms_id).')
    value: str = Field(min_length=1, max_length=1000, description='The file text, exactly as `unknown_references` gives it.')
    action: Literal['map', 'create', 'skip'] = Field(description='Map it to an existing record, create it, or skip its rows.')
    target: str | None = Field(None, min_length=1, max_length=1000, description='For a map: the existing record.')
    input: dict[str, Any] | None = Field(None, description=(
        "For a create needing more than a name, the rest of its command's input: {\"kind\": \"standard\", \"due_days\": 45}."))


class ImportDecideInput(_Input):
    run: str = Field(min_length=26, max_length=26, description='The previewed run waiting on decisions.')
    map: list[str] | None = Field(None, max_length=500, description=(
        'Map file text to an existing record: "Net 30 days=Net 30", or "Terms:Net 30 days=Net 30" when two columns '
        'share the text.'))
    create: list[str] | None = Field(None, max_length=500, description=(
        'Create these when the run reaches them, through their own create command (never an account or item; a '
        'customer or vendor only when nothing looks like it): "Net 45".'))
    create_input: dict[str, dict[str, Any]] | None = Field(None, description=(
        'For a create needing more than a name, the rest of its command\'s input by file text: '
        '{"Net 45": {"kind": "standard", "due_days": 45}}.'))
    skip: list[str] | None = Field(None, max_length=500, description='Leave the rows with this text out.')
    answers: list[ImportAnswer] | None = Field(None, max_length=500, description=(
        'The same answers as data, each naming its field and the file text exactly, so a text holding "=" or ":", '
        'or unknown in two columns, needs no quoting: [{"field": "terms_id", "value": "A=B", "action": "map", '
        '"target": "Net 30"}].'))
    remember: bool = Field(False, description=(
        "Also keep the maps in the source's saved mapping (a new version), so the next file does not ask. Creates "
        'and skips are not remembered (a created record is found by name next time).'))


class ImportMappingSaveInput(_Input):
    source: str = Field(min_length=1, max_length=200, description=(
        'The source to save under ("Acme export"): a new source, or a new version of an existing one of the same '
        'kind. Saving the same recipe again changes nothing.'))
    from_run: str = Field(min_length=26, max_length=26, description=(
        'The previewed or run import whose mapping (columns, key, formats, constants, value maps) to save.'))


class ImportMappingSelector(_Input):
    source: str = Field(min_length=1, max_length=200, description='The saved source, by name.')


class ImportMappingShowInput(ImportMappingSelector):
    version: int | None = Field(None, ge=1, description='A version (default: the latest).')


class ImportMappingQueryInput(_Input):
    kind: str | None = Field(None, max_length=32, description='Only sources of this kind.')
    active: bool | None = Field(None, description='Only active (true) or deactivated (false) sources.')
    limit: int = Field(50, ge=1, le=200, description='Most sources to return.')
    cursor: str | None = Field(None, max_length=200, description='`next_cursor` of the previous page.')


class ImportMappingMatchInput(_Input):
    kind: str = Field(min_length=1, max_length=32, description='Import kind of the file.')
    content: str | None = Field(None, min_length=1, max_length=16_000_000, description='The file as text.')
    attachment: str | None = Field(None, min_length=26, max_length=26, description='An attachment holding the file.')
    sheet: str | None = Field(None, min_length=1, max_length=100, description='Worksheet of an .xlsx file.')
    delimiter: Literal[',', ';', '\t', '|'] | None = Field(None, description='Cell delimiter; sniffed when omitted.')
    header_row: bool = Field(True, description='False when the first row is data.')
    skip_rows: int = Field(0, ge=0, le=50, description='Title rows above the header to pass over.')
    source: str | None = Field(None, max_length=200, description='Only this source\'s mapping.')


class ImportCheckOutput(BaseModel):
    ok: bool = Field(description=(
        'True only when the file can be previewed and run as it is: no cell has a problem and nothing waits on a '
        'decision (`to_decide`).'))
    clean: bool = Field(description='True when no cell has a problem; decisions may still be pending.')
    to_decide: list[dict[str, Any]] = Field(description=(
        'Decisions to make before the run: a decimal mark, a date order, which saved mapping, each unknown '
        'reference (map, create or skip).'))
    kind: str = Field(description='The kind.')
    source: str = Field(description='The source the rows would be keyed in.')
    rows: int = Field(description='Data rows read.')
    rows_with_problems: int = Field(description='Rows with at least one problem.')
    problems_total: int = Field(description='Problems found, every cell counted.')
    problems: list[dict[str, Any]] = Field(description=(
        'Grouped by column, then cell text: column, code, value (the exact cell text), message and rows.'))
    summary: list[str] = Field(description='Each group as a line: "Credit Limit, rows 14, 92: not an amount: ..."')
    dropped: list[dict[str, Any]] = Field(description=(
        'Characters a text field cannot hold (NUL, control codes), dropped: row, line, column, position, character.'))
    dropped_total: int = Field(description='Characters dropped in all.')
    unknown_references: list[dict[str, Any]] = Field(description=(
        'References the books lack, by field and value, with rows, suggestions, look-alikes and whether an import '
        'may create them; a preview waits for `import decide` on each.'))
    matched: dict[str, Any] | None = Field(description='The saved mapping used or offered, with its diff.')
    mapping: dict[str, Any] = Field(description='Columns to input paths, key, formats, value maps.')
    decisions: list[dict[str, Any]] = Field(description='What must be decided before a preview can run.')
    notes: list[str] = Field(description='Things worth knowing: a guessed encoding, rows keyed by content.')
    samples: dict[str, list[str]] = Field({}, description='Up to three values of each column, as the file has them.')
    choices: dict[str, list[str]] = Field({}, description=(
        'For each list an unknown reference may map to and the caller may read: its active records by name (at most '
        '2000), to pick from.'))
    next: str | None = Field(description='What to do next.')


class ImportMappingOutput(BaseModel):
    source: dict[str, Any] = Field(description='The source: id, name, kind, active, version, who and when.')
    mapping: dict[str, Any] = Field(description=(
        'The mapping version: headers, fingerprint, columns, formats, key, mode, value maps, defaults, run.'))
    saved: bool = Field(description='True when this call saved a new version.')


class ImportMappingHistoryOutput(BaseModel):
    source: dict[str, Any] = Field(description='The source.')
    versions: list[dict[str, Any]] = Field(description='Every mapping version, newest first.')


class ImportMappingQueryOutput(BaseModel):
    items: list[dict[str, Any]] = Field(description='Sources by name.')
    next_cursor: str | None = Field(description='Pass as `cursor` for the next page; null on the last.')


class ImportMappingMatchOutput(BaseModel):
    kind: str = Field(description='The kind.')
    fingerprint: str = Field(description='SHA-256 of the sorted normalized headers.')
    headers: list[str] = Field(description='The file\'s headers.')
    candidates: list[dict[str, Any]] = Field(description=(
        'Each active source of the kind: match (exact, probable, none), version, overlap, new, missing and '
        'missing_mapped headers.'))


class ImportRunInput(_Input):
    run: str = Field(min_length=26, max_length=26, description='The run `import preview` returned.')
    limit: int = Field(500, ge=1, le=20_000, description='Most rows to run in this call (a slice).')
    seconds: int = Field(20, ge=1, le=600, description='Stop the slice after about this many seconds.')


class ImportRunSelector(_Input):
    run: str = Field(min_length=26, max_length=26, description='The import run.')


class ImportQueryInput(_Input):
    kind: str | None = Field(None, max_length=32, description='Only runs of this kind.')
    state: Literal['previewed', 'running', 'stopped', 'waiting', 'done', 'cancelled', 'rolled_back', 'failed'] | None = Field(
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
    matched: dict[str, Any] | None = Field(None, description='The saved mapping used or offered, with its diff.')
    dropped: list[dict[str, Any]] = Field([], description=(
        'Characters no text field holds, dropped from the file: row, line, column, position, character.'))
    dropped_total: int = Field(0, description='Characters dropped in all.')
    done_now: int = Field(0, description='Rows this call ran.')
    outlook: dict[str, int] = Field({}, description=(
        'A preview only: what running it would land with the answers given so far: create, update, answered (rows '
        'waiting on a reference, answered with a map or create), land (the three together), left_out (by a skip), '
        'waiting (still unanswered), unchanged, differs, missing, refused.'))
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
