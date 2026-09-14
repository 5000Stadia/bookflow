"""Exact-version deletion of a captured check or card purchase."""
from typing import Literal
from pydantic import Field
from bookflow.company.journal_models import _Input, _Selector, _Version
from bookflow.core.models import WriteOutput


class _Delete(_Input):
    expected_version: _Version
    operation_key: str | None = Field(default=None, min_length=1, max_length=200,
        description='Permanent retry identity; otherwise the context idempotency key is retained.')


class CheckDeleteInput(_Delete):
    check: _Selector


class CardChargeDeleteInput(_Delete):
    card_charge: _Selector


class PurchaseDeleteOutput(WriteOutput):
    id: str
    family: Literal['check', 'card_charge']
    status: Literal['deleted'] = 'deleted'
    version: int
    revision_id: str
    from_status: Literal['posted', 'voided']
    cancellation_batch_id: str | None
    cancelled_stock_movements: int
    changed: bool = True
    idempotent_replay: bool = False
