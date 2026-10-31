"""Inputs and outputs of `bulk-edit` (R107 increment 6, design/bulk-import.md §7)."""
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Kind = Literal['customer', 'vendor', 'item', 'account']


class _Input(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Selection(_Input):
    filter: list[str] = Field(default_factory=list, max_length=20, description=(
        "The list's own `query` filters (`item query options` lists them), e.g. [\"category_id=Fixtures\"]: a reference "
        "filter takes the record's name or id."))
    query: str | None = Field(None, max_length=200, description='Search text, as the list\'s `query` takes it.')
    include_inactive: bool = Field(False, description='Include inactive records.')
    records: list[str] | None = Field(None, max_length=5000, description=(
        'Exactly these records (id or full name; an account\'s number), in this order, instead of a filter.'))


class ColumnsInput(_Input):
    kind: Kind = Field(description='customer, vendor, item or account.')


class GridInput(Selection):
    kind: Kind = Field(description='customer, vendor, item or account.')
    columns: list[str] | None = Field(None, max_length=40, description=(
        'Fields to show (`bulk-edit columns`); default a few per kind (an item\'s name, price, cost, description).'))
    limit: int = Field(50, ge=1, le=5000, description=(
        'Most records to return (a page); `total` counts the whole selection, which a preview or operation covers.'))
    offset: int = Field(0, ge=0, le=5000, description='Records to skip first, for the next page.')


class Change(_Input):
    record: str = Field(min_length=1, max_length=1004, description='Record id or full name (an account\'s number).')
    field: str = Field(min_length=1, max_length=120, description='Column, as `bulk-edit columns` names it.')
    value: Any = Field(None, description='New value as typed ("12.50", "Net 30", true); null or "" clears it.')


class Operation(_Input):
    op: Literal['set', 'clear', 'copy_down', 'replace', 'add', 'multiply', 'percent', 'set_from'] = Field(
        description=(
            'set: one value on every selected record; clear; copy_down: the first record\'s value on the rest; '
            'replace: `find` with `replace` in text; add: `by` to a number; multiply: by `by`; percent: up (or '
            'down, negative) by `by` percent; set_from: copy column `from` of the same record.'))
    field: str = Field(min_length=1, max_length=120, description='Column the operation changes.')
    value: Any = Field(None, description='For set.')
    by: str | None = Field(None, max_length=40, description='For add, multiply and percent: an exact decimal ("5").')
    round: str | None = Field(None, max_length=20, description=(
        'Round add, multiply and percent results half-up to this step ("0.05"); default the currency\'s places for '
        'money, a whole number for a count, six places for other decimals.'))
    find: str | None = Field(None, max_length=1000, description='For replace: the text to find (case-sensitive).')
    replace: str | None = Field(None, max_length=1000, description='For replace: what it becomes ("" deletes it).')
    from_: str | None = Field(None, alias='from', max_length=120, description='For set_from: the column to copy.')
    model_config = ConfigDict(extra='forbid', populate_by_name=True)


class PreviewInput(Selection):
    kind: Kind = Field(description='customer, vendor, item or account.')
    changes: list[Change] = Field(default_factory=list, max_length=20_000, description=(
        'Cell edits, applied first: [{"record": "Widget", "field": "price", "value": "14.00"}].'))
    paste: str | None = Field(None, max_length=2_000_000, description=(
        'Cells pasted from a spreadsheet (tab text). With a header row, columns match by name or label and a '
        'Name column picks the record; without one, rows fill the selection in order over `paste_columns`.'))
    paste_columns: list[str] | None = Field(None, max_length=40, description=(
        'The columns a header-less paste fills, left to right (default: the grid\'s columns).'))
    selection_fingerprint: str | None = Field(None, min_length=64, max_length=64, description=(
        'The grid\'s `selection_fingerprint`. A paste without a header row fills the selection by position, so over a '
        'filter or search it needs this, and is refused if the records changed since the grid (named `records` '
        'need none: their order is yours).'))
    operations: list[Operation] = Field(default_factory=list, max_length=50, description=(
        'Column operations over the selection, in order, after the cell edits: '
        '[{"op": "percent", "field": "price", "by": "5", "round": "0.05"}].'))
    atomic: bool = Field(False, description=(
        'All or nothing: refused at preview if any record is refused, and at apply the first refusal undoes the '
        'records already changed. Default: each record stands alone.'))


class RunSelector(_Input):
    run: str = Field(min_length=26, max_length=26, description='The grid edit (from `bulk-edit preview`).')


class ShowInput(RunSelector):
    outcome: str | None = Field(None, max_length=16, description=(
        'Only rows with this status or outcome (refused, conflict, merged, updated...).'))
    limit: int = Field(200, ge=1, le=5000, description='Most rows to return.')
    cursor: int | None = Field(None, ge=0, description='Rows after this seq (from `next_cursor`).')


class ApplyInput(RunSelector):
    limit: int | None = Field(None, ge=1, le=5000, description=(
        'Apply at most this many records in this call (a slice; the run stays `applying` and the same command goes '
        'on); default every one.'))
    cancel: bool = Field(False, description=(
        'Instead of going on, stop an unfinished apply: the records already changed get their prior values back as '
        'one unit and the rest never run (the edit ends `cancelled`).'))


class ReverseInput(RunSelector):
    skip_changed: bool = Field(False, description=(
        'Reverse every record except those edited again since (they are left as they are and listed; the batch is '
        'then reversed_partly). Without it, one such record refuses the whole reversal, naming it. Those later edits '
        'are someone\'s decision: report them, and pass this only when the owner agrees to leave them.'))
    fingerprint: str | None = Field(None, min_length=64, max_length=64, description=(
        'The `fingerprint` a dry run returned; the reversal is refused if the batch changed since.'))


class QueryInput(_Input):
    kind: Kind | None = Field(None, description='Only edits of this list.')
    state: Literal['previewed', 'applying', 'applied', 'applied_partly', 'rolled_back', 'cancelled', 'failed'] | None = Field(
        None, description='Only edits in this state.')
    limit: int = Field(50, ge=1, le=200, description='Most edits to return.')
    cursor: str | None = Field(None, max_length=64, description='From `next_cursor`.')


class ColumnsOutput(BaseModel):
    kind: str = Field(description='The list.')
    columns: list[dict[str, Any]] = Field(description=(
        'Each editable column: field, label, type (string, boolean, integer, enum, money, decimal, date, reference, '
        'custom), choices for an enum, the list a reference names, and whether it is shown by default.'))


class GridOutput(BaseModel):
    kind: str = Field(description='The list.')
    columns: list[dict[str, Any]] = Field(description='The columns shown, as `bulk-edit columns` gives them.')
    records: list[dict[str, Any]] = Field(description=(
        'id, version, label, and `values` (each field as shown: a reference by name, money as an amount).'))
    total: int = Field(description='Records the selection holds.')
    offset: int = Field(0, description='Records skipped before these.')
    truncated: bool = Field(description='True when more records match after these (pass `offset` for the next page).')
    selection_fingerprint: str | None = Field(None, description=(
        'The whole selection, in order: give it to a preview with a header-less paste.'))


class RunOutput(BaseModel):
    run: dict[str, Any] = Field(description=(
        'The edit: id, kind, state, label, atomic, reason, who and when, its batch (state, reversible, first blocker).'))
    counts: dict[str, int] = Field(description=(
        'selected, change, refused, unchanged, pending (still to apply); after apply also updated, merged, '
        'conflict, not_run.'))
    totals: dict[str, Any] = Field(description=(
        'Per money column: the sum before and after over the records that change (exact, in the currency).'))
    rows: list[dict[str, Any]] = Field(description=(
        'The diff, per record: seq, record, label, version, status, changes [{field, before, after}], the refusal '
        'at preview, and the outcome at apply (merged_over, version_after).'))
    next_cursor: int | None = Field(None, description='Pass as `cursor` for more rows.')
    skipped: list[dict[str, Any]] = Field(default_factory=list, description=(
        'After a reversal with skip_changed: the records left because they were edited again since.'))
    next: str | None = Field(None, description='The command to run next, or null.')


class QueryOutput(BaseModel):
    items: list[dict[str, Any]] = Field(description='Grid edits, newest first, with counts and batch state.')
    next_cursor: str | None = Field(description='Pass as `cursor` for the next page; null on the last.')


class ReverseOutput(BaseModel):
    run: str = Field(description='The grid edit.')
    batch: dict[str, Any] = Field(description='Its batch after this call.')
    state: str = Field(description='live (dry run), reversed or reversed_partly.')
    reversed_now: int = Field(description='Records restored by this call.')
    steps: list[dict[str, Any]] = Field(description=(
        'The plan: per record, the update that restores it (the first 50; `remaining` counts them all).'))
    remaining: int = Field(0, description='Records the plan restores (dry run), or still to restore.')
    blockers: list[dict[str, Any]] = Field(description='What blocks the reversal (empty when it can run).')
    skipped: list[dict[str, Any]] = Field(description=(
        'Records edited again since, left as they are (only with skip_changed), each naming who and when (the '
        'first 50; `skipped_total` counts them all).'))
    skipped_total: int = Field(0, description='How many records skip_changed leaves.')
    fingerprint: str | None = Field(description='Pass to the real reversal so it refuses if the batch changed since.')
    reversal_batch: str | None = Field(description='The batch recording the reversal, once begun.')
    event_id: str | None = Field(description='Audit event that finished the reversal.')
