"""Complete recovery intents: fixed saved provenance and explicit new calculation."""
from typing import Annotated, Literal
from uuid import UUID
from pydantic import Field, model_validator, field_validator
from bookflow.company.sales_models import StrictModel, Fingerprint
from bookflow.company.payment_models import OperationKey, Page

ID = Annotated[str, Field(pattern=r'^[0-9A-HJKMNP-TV-Z]{26}$')]
Version = Annotated[int, Field(strict=True, ge=1)]
Count = Annotated[int, Field(strict=True, ge=0, le=9223372036854775807)]
Units = Count

class HeaderIntent(StrictModel):
    action: Literal['keep', 'set']
    amount_origin: Literal['entered', 'selection_total', 'unresolved'] | None = None
    amount_minor_units: Units | None = None
    currency: Annotated[str, Field(pattern=r'^[A-Z]{3}$')] | None = None

    @model_validator(mode='after')
    def shape(self):
        present = self.model_fields_set - {'action'}
        if self.action == 'keep':
            if present:
                raise ValueError('keep has no submitted value or mode')
        elif self.amount_origin == 'entered':
            if self.amount_minor_units is None or self.currency is None:
                raise ValueError('entered requires exact money and currency')
        elif self.amount_origin not in ('selection_total', 'unresolved') or present & {'amount_minor_units', 'currency'}:
            raise ValueError('derived or unresolved set requires only its mode')
        return self

class RecoveryEntry(StrictModel):
    invoice_id: ID
    observed_invoice_version: Version
    action: Literal['set', 'remove', 'calculate']
    amount_minor_units: Units | None = None
    currency: Annotated[str, Field(pattern=r'^[A-Z]{3}$')] | None = None
    amount_origin: Literal['entered', 'calculated', 'unresolved'] | None = None
    retained_calculation_revision_id: ID | None = None
    attempted_calculated_minor_units: Units | None = None

    @model_validator(mode='after')
    def shape(self):
        values = self.model_fields_set - {'invoice_id', 'observed_invoice_version', 'action'}
        if self.action == 'remove':
            if values:
                raise ValueError('remove has no amount, origin, provenance or hint')
        elif self.action == 'calculate':
            if values - {'attempted_calculated_minor_units'}:
                raise ValueError('calculate accepts only an optional untrusted hint')
        else:
            if 'attempted_calculated_minor_units' in values or self.currency is None:
                raise ValueError('set requires currency and forbids calculation hints')
            if self.amount_origin == 'unresolved':
                if self.amount_minor_units is not None or self.retained_calculation_revision_id is not None:
                    raise ValueError('unresolved has no money or provenance')
            elif self.amount_origin in ('entered', 'calculated'):
                if self.amount_minor_units is None:
                    raise ValueError('resolved set requires exact money')
                if (self.amount_origin == 'calculated') != (self.retained_calculation_revision_id is not None):
                    raise ValueError('only saved calculated money requires revision provenance')
            else:
                raise ValueError('set requires an explicit origin')
        return self

class BeginInput(StrictModel):
    recovery_key: OperationKey
    selection: ID
    expected_version: Version
    local_baseline_revision: ID
    attempt_generation: str
    declared_entry_count: Count
    intent_hash: Fingerprint
    header_intent: HeaderIntent

    @field_validator('attempt_generation')
    @classmethod
    def canonical_uuid(cls, value):
        if str(UUID(value)) != value:
            raise ValueError('generation must be a canonical UUID')
        return value

class RecoveryInput(StrictModel):
    recovery_id: ID

class UploadInput(RecoveryInput):
    chunk_index: Count
    entries: list[RecoveryEntry] = Field(min_length=1, max_length=200)

class SealInput(RecoveryInput):
    expected_recovery_version: Version

class CompareInput(RecoveryInput):
    attempt_generation: str
    intent_hash: Fingerprint

class CompareItemsInput(CompareInput, Page):
    facts_fingerprint: Fingerprint
    kind: Literal['changes', 'problems'] = 'changes'

class ApplyInput(CompareInput, SealInput):
    expected_selection_version: Version
    expected_facts_fingerprint: Fingerprint

class AbortInput(SealInput):
    disposition: Literal['discard_entire_attempt']

class ReplaceInput(SealInput):
    replacement: BeginInput

class ShowInput(StrictModel):
    recovery_id: ID | None = None
    recovery_key: OperationKey | None = None

    @model_validator(mode='after')
    def selector(self):
        if (self.recovery_id is None) == (self.recovery_key is None):
            raise ValueError('provide exactly one recovery id or key')
        return self

class ItemsInput(RecoveryInput, Page):
    kind: Literal['entries', 'chunks', 'missing_ranges'] = 'entries'

class QueryInput(Page):
    selection: ID | None = None
    state: Literal['uploading', 'sealed', 'applied', 'aborted', 'superseded'] | None = None
