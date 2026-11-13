"""Simple purchase approval command contracts."""
from typing import Literal, Any
from pydantic import Field
from bookflow.company.journal_models import _Input, _Selector, _Version
from bookflow.company.purchase_draft_models import CancelInput

class SubmitInput(CancelInput):
    pass

class WithdrawInput(CancelInput):
    expected_request_version: _Version
    confirm: bool = False

class CommitInput(CancelInput):
    expected_request_version: _Version | None = None

class RequestInput(_Input):
    request: _Selector

class DecideInput(RequestInput):
    expected_version: _Version
    step_id: Literal['review'] = 'review'
    decision: Literal['approve', 'reject']
    comment: str | None = Field(default=None, max_length=2000)
    operation_key: str = Field(min_length=1, max_length=128)

class PolicyCreateInput(_Input):
    target: Literal['bill', 'purchase_order'] = 'bill'
    eligible_members: list[_Selector] = Field(min_length=1, max_length=100)
    allow_self: bool = False
    enabled: bool = True
    operation_key: str = Field(min_length=1, max_length=128)

class PolicyInput(_Input):
    policy: _Selector

class PolicyUpdateInput(PolicyCreateInput, PolicyInput):
    expected_version: _Version

class PolicyPublishInput(PolicyInput):
    expected_version: _Version
    operation_key: str = Field(min_length=1, max_length=128)
    confirm: bool = False

class PolicyListInput(_Input):
    limit: int = Field(default=50, ge=1, le=200)
    cursor: str | None = None

class PolicyOutput(_Input):
    id: str
    version: int
    target: Literal['bill', 'purchase_order']
    state: Literal['draft', 'published']
    eligible_members: list[str]
    allow_self: bool
    enabled: bool

class PolicyListOutput(_Input):
    items: list[PolicyOutput]
    next_cursor: str | None

class RequestOutput(_Input):
    id: str
    draft_id: str
    draft_version: int
    policy_revision_id: str
    version: int
    state: str
    current_step: int
    steps: list[dict[str, Any]]
    events: list[dict[str, Any]]

class CommitOutput(_Input):
    draft_id: str
    version: int
    state: Literal['committed'] = 'committed'
    request_id: str | None
    document_id: str
    document_version: int
    batch_id: str
    total_minor_units: int
    currency: str
