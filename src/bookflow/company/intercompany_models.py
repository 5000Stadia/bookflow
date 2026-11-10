"""Typed company-local intercompany settings and untargeted draft contracts."""
from pydantic import BaseModel, ConfigDict, Field
from bookflow.company.journal_models import _Date, _Version
from bookflow.core.exact import INT64_MAX


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)


class Empty(Strict):
    pass


class SettingsOutput(Strict):
    version: int
    linked_entries_enabled: bool
    templates_enabled: bool
    bulk_enabled: bool


class SettingsUpdate(Strict):
    expected_version: int = Field(ge=0, strict=True, description='Version from settings show; zero before first setup.')
    linked_entries_enabled: bool = Field(strict=True, description='Enable local relationship setup and drafts; this increment cannot post.')
    templates_enabled: bool = Field(False, strict=True, description='Reserve the optional template layer; commands arrive in a later increment.')
    bulk_enabled: bool = Field(False, strict=True, description='Reserve the optional bulk layer; commands arrive in a later increment.')


class DraftCreate(Strict):
    date: _Date = Field(description='Proposed accounting date; saving a draft does not post or reserve a period.')
    amount_minor_units: int = Field(gt=0, le=INT64_MAX, strict=True, description='Positive proposed amount in integer minor units, never a floating-point amount.')
    currency: str | None = Field(None, min_length=3, max_length=3, description='Home currency; omit to use this company\'s home currency.')
    offset_account: str | None = Field(None, min_length=1, max_length=400, description='Optional eligible local account ID or full name. No counterpart lookup occurs.')
    memo: str | None = Field(None, max_length=1000, description='Local user-authored proposal; never fetches or targets another company.')


class DraftUpdate(DraftCreate):
    draft: str = Field(min_length=26, max_length=26, description='Local draft ID.')
    expected_version: _Version = Field(description='Version read from draft show; replacement preserves its ID and original attribution.')


class DraftSelect(Strict):
    draft: str = Field(min_length=26, max_length=26, description='Local draft ID.')


class DraftList(Strict):
    limit: int = Field(50, ge=1, le=200, description='Maximum local drafts, in stable ID order.')
    cursor: str | None = Field(None, min_length=26, max_length=26, description='next_cursor from the previous page in this company.')


class DraftOutput(Strict):
    id: str
    version: int
    date: str
    amount_minor_units: int
    currency: str
    offset_account_id: str | None
    memo: str | None
    status: str = 'draft'
    created_at: str
    created_by: str
    created_via: str
    updated_at: str
    updated_by: str
    updated_via: str


class DraftPage(Strict):
    items: list[DraftOutput]
    next_cursor: str | None = None
