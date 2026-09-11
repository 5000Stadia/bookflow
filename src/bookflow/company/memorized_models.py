"""What a memorized transaction takes and what it gives back.

Every input here is deliberately **flat and scalar**. A memorized template is edited from a
generated form on every surface, and a nested schedule object would give that form a branch
nobody has a control for. The one structured field is ``payload``, which is not this module's
shape at all: it is the target command's own input, carried through untouched.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from bookflow.commands.common import CommonOut
from bookflow.company.memorized_schema import FREQUENCIES, MODES
from bookflow.core.models import WriteOutput

Frequency = Literal['never', 'daily', 'weekly', 'every_other_week', 'every_four_weeks',
                    'twice_a_month', 'monthly', 'every_other_month', 'quarterly',
                    'twice_a_year', 'annually', 'every_other_year']
Mode = Literal['on_demand', 'remind', 'enter_automatically']
Status = Literal['active', 'paused', 'finished', 'deleted']

assert set(FREQUENCIES) == set(Frequency.__args__), 'the frequency list and the schedule table disagree'
assert set(MODES) == set(Mode.__args__), 'the mode list and the schedule table disagree'

_Date = Annotated[str, Field(min_length=10, max_length=10, pattern=r'^[0-9]{4}-[0-9]{2}-[0-9]{2}$')]
_Name = Annotated[str, Field(min_length=1, max_length=160)]
_Selector = Annotated[str, Field(min_length=1, max_length=64)]
_Version = Annotated[int, Field(strict=True, ge=1)]


class _Input(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


# ---------------------------------------------------------------- templates

class MemorizedCreateInput(_Input):
    name: _Name
    command: Annotated[str, Field(min_length=1, max_length=64)]
    payload: dict[str, Any]
    frequency: Frequency = 'never'
    start_date: _Date | None = None
    stop_date: _Date | None = None
    remaining_count: Annotated[int, Field(strict=True, ge=1)] | None = None
    days_in_advance: Annotated[int, Field(strict=True, ge=0, le=365)] = 0
    mode: Mode = 'on_demand'
    group: _Selector | None = None
    group_ordinal: Annotated[int, Field(strict=True, ge=1)] | None = None


class MemorizedUpdateInput(_Input):
    memorized: _Selector
    expected_version: _Version
    name: _Name | None = None
    payload: dict[str, Any] | None = None
    frequency: Frequency | None = None
    start_date: _Date | None = None
    stop_date: _Date | None = None
    remaining_count: Annotated[int, Field(strict=True, ge=1)] | None = None
    days_in_advance: Annotated[int, Field(strict=True, ge=0, le=365)] | None = None
    mode: Mode | None = None
    status: Literal['active', 'paused'] | None = None
    group: _Selector | None = None
    group_ordinal: Annotated[int, Field(strict=True, ge=1)] | None = None


class MemorizedDeleteInput(_Input):
    memorized: _Selector
    expected_version: _Version


class MemorizedShowInput(_Input):
    memorized: _Selector
    occurrence_limit: Annotated[int, Field(strict=True, ge=1, le=200)] = 50


class MemorizedListInput(_Input):
    status: Status | None = None
    mode: Mode | None = None
    due_only: bool = False
    query: Annotated[str, Field(max_length=160)] | None = None
    as_of: _Date | None = None
    limit: Annotated[int, Field(strict=True, ge=1, le=200)] = 50
    cursor: Annotated[str, Field(max_length=256)] | None = None


class MemorizedEnterInput(_Input):
    memorized: _Selector
    date: _Date | None = None


class MemorizedProcessInput(_Input):
    memorized: _Selector | None = None
    as_of: _Date | None = None
    limit: Annotated[int, Field(strict=True, ge=1, le=200)] = 50


class MemorizedOccurrenceActionInput(_Input):
    occurrence: _Selector
    expected_version: _Version


# ---------------------------------------------------------------- groups

class MemorizedGroupCreateInput(_Input):
    name: _Name
    frequency: Frequency = 'never'
    start_date: _Date | None = None
    stop_date: _Date | None = None
    remaining_count: Annotated[int, Field(strict=True, ge=1)] | None = None
    days_in_advance: Annotated[int, Field(strict=True, ge=0, le=365)] = 0
    mode: Mode = 'on_demand'


class MemorizedGroupUpdateInput(_Input):
    memorized_group: _Selector
    expected_version: _Version
    name: _Name | None = None
    frequency: Frequency | None = None
    start_date: _Date | None = None
    stop_date: _Date | None = None
    remaining_count: Annotated[int, Field(strict=True, ge=1)] | None = None
    days_in_advance: Annotated[int, Field(strict=True, ge=0, le=365)] | None = None
    mode: Mode | None = None
    status: Literal['active', 'paused'] | None = None


class MemorizedGroupDeleteInput(_Input):
    memorized_group: _Selector
    expected_version: _Version


class MemorizedGroupShowInput(_Input):
    memorized_group: _Selector
    occurrence_limit: Annotated[int, Field(strict=True, ge=1, le=200)] = 50


class MemorizedGroupListInput(_Input):
    status: Status | None = None
    query: Annotated[str, Field(max_length=160)] | None = None
    limit: Annotated[int, Field(strict=True, ge=1, le=200)] = 50
    cursor: Annotated[str, Field(max_length=256)] | None = None


class MemorizedGroupEnterInput(_Input):
    memorized_group: _Selector
    date: _Date | None = None


# ---------------------------------------------------------------- outputs

class ScheduleOutput(BaseModel):
    frequency: str
    anchor_day: int | None
    start_date: str | None
    next_date: str | None
    stop_date: str | None
    remaining_count: int | None
    days_in_advance: int
    mode: str
    status: str


class ReferenceOutput(BaseModel):
    """One edge of the derived index, with what the row it names looks like now."""

    field_path: str
    ordinal: int
    target_noun: str
    target_id: str
    name: str | None
    present: bool
    active: bool
    active_when_captured: bool


class OccurrenceOutput(BaseModel):
    id: str
    version: int
    template_id: str
    template_name: str | None = None
    revision_id: str
    command: str
    origin: str
    slot_date: str
    due_date: str
    entry_date: str | None
    status: str
    transaction_id: str | None
    document_number: str | None
    error_code: str | None
    error_message: str | None
    attempt_count: int
    group_run_id: str | None
    group_ordinal: int | None


class MemorizedSummary(BaseModel):
    id: str
    name: str
    command: str
    status: str
    mode: str
    frequency: str
    next_date: str | None
    days_in_advance: int
    remaining_count: int | None
    group_name: str | None
    due_count: int
    blocked_count: int
    last_entered_on: str | None
    version: int


class MemorizedWriteOutput(CommonOut, WriteOutput):
    name: str
    command: str
    current_revision_id: str
    revision_version: int
    schedule: ScheduleOutput
    group_id: str | None = None
    group_ordinal: int | None = None
    reference_schema: str
    references: list[ReferenceOutput] = Field(default_factory=list)
    deleted: bool = False
    idempotent_replay: bool = False


class MemorizedOutput(CommonOut):
    name: str
    command: str
    current_revision_id: str
    revision_version: int
    payload: dict[str, Any]
    schedule: ScheduleOutput
    group_id: str | None
    group_name: str | None
    group_ordinal: int | None
    reference_schema: str
    references: list[ReferenceOutput]
    fixed_fields: list[str]
    recomputed_fields: list[str]
    due_slots: list[str]
    occurrences: list[OccurrenceOutput]


class MemorizedListOutput(BaseModel):
    items: list[MemorizedSummary]
    next_cursor: str | None = None
    as_of: str
    due_total: int
    blocked_total: int


class MemorizedEntryOutput(WriteOutput):
    as_of: str
    entered: list[OccurrenceOutput] = Field(default_factory=list)
    blocked: list[OccurrenceOutput] = Field(default_factory=list)
    pending: list[OccurrenceOutput] = Field(default_factory=list)
    entered_count: int = 0
    blocked_count: int = 0
    pending_count: int = 0
    idempotent_replay: bool = False


class OccurrenceWriteOutput(WriteOutput):
    occurrence: OccurrenceOutput
    idempotent_replay: bool = False


class MemorizedGroupSummary(BaseModel):
    id: str
    name: str
    status: str
    mode: str
    frequency: str
    next_date: str | None
    days_in_advance: int
    remaining_count: int | None
    member_count: int
    version: int


class MemorizedGroupWriteOutput(CommonOut, WriteOutput):
    name: str
    schedule: ScheduleOutput
    member_count: int
    deleted: bool = False
    idempotent_replay: bool = False


class GroupRunOutput(BaseModel):
    id: str
    slot_date: str
    entry_date: str
    members: list[OccurrenceOutput]


class MemorizedGroupOutput(CommonOut):
    name: str
    schedule: ScheduleOutput
    members: list[MemorizedSummary]
    due_slots: list[str]
    runs: list[GroupRunOutput]


class MemorizedGroupListOutput(BaseModel):
    items: list[MemorizedGroupSummary]
    next_cursor: str | None = None
