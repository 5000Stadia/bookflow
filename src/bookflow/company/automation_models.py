"""Input and output shapes for automations (R187 increment 2, design/principal-directives.md §3)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from bookflow.company.shipment_models import _Strict

State = Literal['draft', 'live', 'paused']
_KIND = 'The kind: delivery (a send-mode delivery schedule); bank-rule and invoice once their rules exist.'


class AutomationQueryInput(_Strict):
    kind: str | None = Field(default=None, max_length=32, description=_KIND)
    state: State | None = Field(default=None, description='Only rules in this state.')
    limit: int = Field(default=100, ge=1, le=500)


class AutomationSelector(_Strict):
    kind: str = Field(max_length=32, description=_KIND)
    rule: str = Field(min_length=1, max_length=100, description='The rule: its name or id.')


class AutomationOutput(BaseModel):
    kind: str
    capability: str = Field(description='What making it live needs (automate.<kind>).')
    noun: str
    rule_id: str
    name: str
    state: str = Field(description='draft (runs nothing), live (runs under its establisher) or paused.')
    what: str = Field('', description='What it does unattended, in words.')
    established_by: str | None = Field(None, description='Who made it live; it runs under their authority.')
    established_by_name: str | None = None
    established_for: str | None = Field(None, description="An agent establisher's principal.")
    established_at: str | None = None
    paused_reason: str | None = None
    draft_reason: str | None = Field(None, description='Why it last returned to draft from live.')
    would_pause: str | None = Field(None, description=(
        'A live rule whose establisher no longer holds the authority: why its next firing pauses it.'))
    show_command: str = ''
    live_command: str = Field('', description='How a holder of the capability makes it live.')
    warnings: list[str] = Field(default_factory=list)


class AutomationRow(BaseModel):
    kind: str
    rule_id: str
    name: str
    state: str
    established_by_name: str | None = None
    established_at: str | None = None
    paused_reason: str | None = None
    would_pause: str | None = None


class AutomationPageOutput(BaseModel):
    rows: list[AutomationRow] = Field(default_factory=list)
    count: int
    truncated: bool = False
