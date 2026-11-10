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


# ---------------------------------------------------------------- conversion, history and imports (increment 2)

class ContactTransfer(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    lead_contact: str = Field(min_length=26, max_length=26, description='Lead contact ID to copy from.')
    customer_contact: str | None = Field(None, min_length=26, max_length=26, description=(
        'Existing customer contact ID to copy the named fields onto; null adds the lead contact as a new '
        'additional customer contact.'))
    fields: list[str] = Field(default_factory=list, max_length=20, description=(
        'Contact fields to copy onto an existing customer contact (display_name, names, phones, emails, website). '
        'Nothing is overwritten that is not named here. Ignored when customer_contact is null.'))


class LeadConvertInput(LeadVersionInput):
    target: Literal['new', 'existing'] = Field(description=(
        'new: create a top-level customer from the lead through native customer validation; existing: link the '
        'lead to an existing customer, copying nothing unless contact_transfers names it.'))
    customer: str | None = Field(None, min_length=1, max_length=200, description=(
        'Existing customer ID or exact unambiguous name; required for target existing.'))
    customer_expected_version: int | None = Field(None, ge=1, description=(
        'Version returned by customer show; required for target existing.'))
    name: str | None = Field(None, min_length=1, max_length=200, description=(
        'Customer name for target new; defaults to the lead display name. Customer names are unique.'))
    contact_transfers: list[ContactTransfer] | None = Field(None, max_length=200, description=(
        'Target existing only: explicit per-contact, per-field copies, shown in the dry-run preview.'))


class ContactMapping(BaseModel):
    lead_contact_id: str
    customer_contact_id: str | None
    action: Literal['created', 'updated']
    fields: list[str]


class CustomerMatch(BaseModel):
    id: str
    name: str
    active: bool
    reason: str


class ConversionOutput(BaseModel):
    id: str | None
    state: str
    target: str
    customer_id: str | None
    customer_name: str
    customer_version: int | None
    batch_id: str | None
    created_at: str | None = None
    created_by: str | None = None
    contact_map: list[ContactMapping] = Field(default_factory=list)


class LeadConvertOutput(WriteOutput):
    lead: LeadOutput
    conversion: ConversionOutput
    replayed: bool = Field(False, description='True when this repeats an already committed identical conversion.')
    matching_customers: list[CustomerMatch] = Field(default_factory=list, description=(
        'Customers whose name or contact email/phone matches the lead; shown before commit, never merged.'))
    copied_fields: list[str] = Field(default_factory=list, description='Customer fields filled from the lead.')
    next: list[str] = Field(default_factory=list)


class TimelineInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    lead: str | None = Field(None, min_length=1, max_length=200, description='Lead ID or exact unambiguous name.')
    customer: str | None = Field(None, min_length=1, max_length=200, description='Customer ID or exact unambiguous name.')
    limit: int = Field(50, ge=1, le=200)
    cursor: str | None = Field(None, max_length=4096)


class TimelineEntry(BaseModel):
    kind: Literal['note', 'todo', 'attachment', 'conversion', 'conversion_reversed']
    id: str
    at: str
    source_type: str
    source_id: str
    from_lead: str | None = Field(None, description='The lead this entry came from, when read on a customer.')
    title: str
    status: str | None = None


class TimelineOutput(BaseModel):
    lead_id: str | None
    lead_name: str | None
    customer_id: str | None
    customer_name: str | None
    items: list[TimelineEntry]
    count: int
    total_count: int
    has_more: bool
    next_cursor: str | None
    omitted: list[str] = Field(default_factory=list, description='Sources left out because the reader may not read them.')


class LeadImportRow(LeadFields):
    source_key: str = Field(min_length=1, max_length=200, description="The row's stable key within its source.")


class LeadImportInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    source: str = Field(min_length=1, max_length=200, description='Import source identity, such as a file or list name.')
    rows: list[LeadImportRow] = Field(min_length=1, max_length=500)


class LeadImportRowResult(BaseModel):
    source_key: str
    outcome: Literal['created', 'unchanged', 'would_create']
    lead_id: str | None


class LeadImportOutput(WriteOutput):
    source: str
    batch_id: str | None
    created: int
    unchanged: int
    rows: list[LeadImportRowResult]
    next: list[str] = Field(default_factory=list)
