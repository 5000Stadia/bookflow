"""Inputs and outputs of membership plans and dated customer enrollments (R188 increment 1)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from bookflow.company.journal_models import _Date, _Version
from bookflow.company.sales_models import Selector


class _Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)


MemberNumber = str
_NUMBER = dict(min_length=1, max_length=40, pattern=r'^[A-Za-z0-9][A-Za-z0-9-]*$',
               description='Optional private member number: letters, digits and hyphens, unique within the company; never printed by default.')


class Named(_Strict):
    id: str
    label: str


class PlanCreateInput(_Strict):
    name: str = Field(min_length=1, max_length=200, description='Plan name, unique, such as "Trade members".')
    price_level: Selector = Field(description='ID or name of an existing active home-currency price level members are priced at.')
    description: str | None = Field(None, max_length=500, description='What the plan is for; internal only.')


class PlanUpdateInput(_Strict):
    plan: Selector = Field(description='Membership plan ID or name.')
    expected_version: _Version | None = None
    name: str | None = Field(None, min_length=1, max_length=200)
    price_level: Selector | None = Field(None, description='Another existing price level; saved invoices keep the level they captured.')
    description: str | None = Field(None, max_length=500)
    active: bool | None = Field(None, description='false stops new enrollments and new invoices using the plan; saved invoices keep their prices.')


class PlanSelectInput(_Strict):
    plan: Selector = Field(description='Membership plan ID or name.')


class PlanListInput(_Strict):
    include_inactive: bool = Field(False, description='Include deactivated plans.')
    limit: int = Field(100, ge=1, le=500, description='Most plans to return, in name order.')
    cursor: str | None = Field(None, min_length=1, max_length=64, description='next_cursor from the previous page.')


class PlanOutput(_Strict):
    id: str
    version: int
    name: str
    description: str | None
    active: bool
    price_level: Named
    enrollments: int = Field(description='Enrollments that name the plan, of any status.')


class PlanWriteOutput(PlanOutput):
    changed: bool = True


class PlanPage(_Strict):
    items: list[PlanOutput]
    next_cursor: str | None = None
    layer_enabled: bool = Field(description='Whether membership pricing is on for the company (company update --membership-pricing-enabled).')


class MembershipSetInput(_Strict):
    customer: Selector = Field(description="Customer or job ID or name. A job enrolled itself uses its own enrollment; one not enrolled inherits its nearest ancestor's unless its inheritance is off.")
    plan: Selector = Field(description='Active membership plan ID or name.')
    start_date: _Date = Field(description='First company-local transaction date covered, inclusive.')
    end_date: _Date | None = Field(None, description='Last transaction date covered, inclusive; omit for no end yet.')
    member_number: MemberNumber | None = Field(None, **_NUMBER)
    membership: Selector | None = Field(None, description='An existing enrollment ID to change instead of enrolling again; needs expected_version.')
    expected_version: _Version | None = Field(None, description="The enrollment's version you read, when changing one.")


class MembershipEndInput(_Strict):
    membership: Selector = Field(description='Enrollment ID.')
    expected_version: _Version | None = None
    end_date: _Date = Field(description='Last transaction date the enrollment covers, inclusive; on or after its start date.')


class MembershipShowInput(_Strict):
    customer: Selector = Field(description='Customer or job ID or name.')
    as_of: _Date | None = Field(None, description='Date whose effective enrollment to report; today when omitted.')


class MembershipOutput(_Strict):
    id: str
    version: int
    customer: Named
    plan: Named
    price_level: Named
    start_date: str
    end_date: str | None
    status: Literal['active', 'suspended', 'ended']
    member_number: str | None


class MembershipWriteOutput(_Strict):
    membership: MembershipOutput
    changed: bool = True


class CustomerMembershipOutput(_Strict):
    customer: Named
    layer_enabled: bool
    as_of: str
    effective: MembershipOutput | None = Field(description='The enrollment covering as_of, if any and the layer is on: its own, or for a job the one it inherits.')
    history: list[MembershipOutput] = Field(description='Every enrollment of this customer, newest start first.')
    effective_owner: Named | None = Field(None, description='Whose enrollment applies on as_of: this customer, or the ancestor a job inherits from.')
    inherits: bool | None = Field(None, description="For a job, whether it inherits its nearest ancestor's enrollment; null for a top-level customer.")
    inheritance_version: int | None = Field(None, description="Version of the job's inheritance setting; null while it has none (it inherits).")


# ---------------------------------------------------------------------------- increment 2

class InheritanceSetInput(_Strict):
    customer: Selector = Field(description='Job ID or name (a customer with a parent).')
    inherit: bool = Field(description="true: the job uses its nearest ancestor's enrollment when it has none of its own; false: only its own.")
    expected_version: _Version | None = Field(None, description="The setting's version you read (inheritance_version from customer membership show); omit for a job with no setting yet.")


class InheritanceOutput(_Strict):
    customer: Named
    inherit: bool
    version: int | None = Field(description='Version of the setting row; null when the job has none.')
    changed: bool = True


class MemberListInput(_Strict):
    plan: Selector | None = Field(None, description='Only members of this plan (ID or name).')
    as_of: _Date | None = Field(None, description='Date whose effective memberships to list; today when omitted.')
    include_inherited: bool = Field(True, description="Include jobs priced through an ancestor's enrollment.")
    limit: int = Field(100, ge=1, le=500, description='Most customers to return, in name order.')
    cursor: str | None = Field(None, min_length=1, max_length=64, description='next_cursor from the previous page.')


class MemberRow(_Strict):
    customer: Named
    membership: str = Field(description='Enrollment ID that prices this customer on as_of.')
    plan: Named
    price_level: Named
    start_date: str
    end_date: str | None
    owner: Named = Field(description='Whose enrollment it is: the customer itself or the ancestor a job inherits from.')
    inherited: bool


class MemberPage(_Strict):
    items: list[MemberRow]
    as_of: str
    layer_enabled: bool
    next_cursor: str | None = None


class EnrollManyInput(_Strict):
    customers: list[Selector] = Field(min_length=1, max_length=200, description='Customers or jobs to enroll (IDs or names), each once.')
    plan: Selector = Field(description='Active membership plan ID or name.')
    start_date: _Date = Field(description='First company-local transaction date covered, inclusive.')
    end_date: _Date | None = Field(None, description='Last transaction date covered, inclusive; omit for no end yet.')


class EnrollManyOutput(_Strict):
    batch: str | None = Field(description='Reversible batch ID (`batch reverse <id>`); null in a dry run.')
    enrolled: list[MembershipOutput]
    count: int

