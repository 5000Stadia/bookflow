"""Inputs and output of restoring a deleted document as a new one linked to it."""
from typing import Literal

from pydantic import Field

from bookflow.company.journal_models import _Input, _Selector, _Date, _Number
from bookflow.core.models import WriteOutput

_DATE = ('Accounting date of the restored document. Omitted, it is the deleted document\'s own '
         'date; give an open date when that date is now inside a closed period.')
_NUMBER = ('Number of the restored document. Omitted, it takes the next number in its series: '
           'the deleted document keeps its own number in its retained history.')


class JournalRestoreInput(_Input):
    journal: _Selector
    date: _Date | None = Field(default=None, description=_DATE)
    number: _Number | None = Field(default=None, description=_NUMBER)


class InvoiceRestoreInput(_Input):
    invoice: _Selector
    date: _Date | None = Field(default=None, description=_DATE)
    number: _Number | None = Field(default=None, description=_NUMBER)


class RestoreOutput(WriteOutput):
    family: Literal['journal_entry', 'invoice']
    deleted_id: str
    deleted_number: str
    deleted_revision_id: str
    restored_id: str
    restored_number: str
    date: str
    total_minor_units: int
    currency: str
    left_out: list[str] = Field(default_factory=list, description='What the deleted document carried that the restored one does not, and why.')
    restored_at: str | None = None
    restored_by: str | None = None
    changed: bool = True
    idempotent_replay: bool = False
