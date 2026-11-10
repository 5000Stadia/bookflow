"""Typed lead contracts shared by every interface."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field
from bookflow.company.parties import AddressInput, ContactInput
from bookflow.company.scheduling_models import TodoOutput
from bookflow.core.models import WriteOutput
from bookflow.commands.common import CommonOut


class LeadFields(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, str_strip_whitespace=True)
    display_name: str = Field(min_length=1, max_length=200)
    company_name: str | None = Field(None, max_length=200)
    salutation: str | None = Field(None, max_length=32)
    first_name: str | None = Field(None, max_length=128)
    middle_name: str | None = Field(None, max_length=128)
    last_name: str | None = Field(None, max_length=128)
    job_title: str | None = Field(None, max_length=128)
    address: AddressInput | None = None
    contacts: list[ContactInput] = Field(default_factory=list, max_length=200)
    owner: str | None = Field(None, description='Current company member ID, username or me; defaults to creator on create. Explicit null means unassigned.')
    source: str | None = Field(None, max_length=500)
    rating: Literal['unrated', 'cold', 'warm', 'hot'] = 'unrated'
    summary: str | None = Field(None, max_length=65536)


class LeadCreateInput(LeadFields):
    pass


class LeadSelector(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    lead: str = Field(min_length=1, max_length=200, description='Stable lead ID or an exact unambiguous display name. Duplicate names require an ID.')


class LeadVersionInput(LeadSelector):
    expected_version: int = Field(ge=1, description='Version returned by lead show.')


class LeadUpdateInput(LeadFields, LeadVersionInput):
    display_name: str | None = Field(None, min_length=1, max_length=200)


class LeadCloseInput(LeadVersionInput):
    duplicate_of: str | None = Field(None, description='Optional same-company lead ID or unambiguous name; a duplicate link cannot form a cycle.')


class LeadArchiveInput(LeadVersionInput):
    archived: bool = True


class LeadListInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    search: str | None = Field(None, max_length=200)
    status: Literal['open', 'closed', 'converted', 'all'] = 'open'
    include_archived: bool = False
    owner: str | None = None
    limit: int = Field(50, ge=1, le=200)
    cursor: str | None = Field(None, max_length=4096)


class LeadOutput(CommonOut):
    display_name: str
    company_name: str | None
    salutation: str | None
    first_name: str | None
    middle_name: str | None
    last_name: str | None
    job_title: str | None
    address: AddressInput
    contacts: list[ContactInput]
    owner_id: str | None
    owner_name: str | None
    source: str | None
    rating: str
    summary: str | None
    status: str
    close_reason: str | None
    archived: bool
    duplicate_of: str | None
    next_follow_up: TodoOutput | None = None
    open_follow_up_count: int | None = None
    permitted_actions: list[str] = Field(default_factory=list)


class LeadWriteOutput(LeadOutput, WriteOutput):
    pass


class LeadPage(BaseModel):
    items: list[LeadOutput]
    count: int
    total_count: int
    has_more: bool
    next_cursor: str | None
