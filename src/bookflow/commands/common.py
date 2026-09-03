"""Output builders shared by hub and company commands."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from bookflow.core.models import WriteOutput
from bookflow.core.session import Session, localize
from bookflow.hub import access
from bookflow.hub.users import user_names


class CommonOut(BaseModel):
    id: str
    version: int
    created_at: str
    created_by: str
    created_via: str
    updated_at: str
    updated_by: str
    updated_via: str


def common_out(s: Session, row: dict[str, Any]) -> dict[str, Any]:
    return {k: (localize(s, row[k]) if k in ("created_at", "updated_at") else row[k]) for k in CommonOut.model_fields}


class OrganizationOutput(CommonOut):
    organization_id: str
    display_name: str
    access: str | None
    role: str | None
    path: str | None
    is_demo: bool


def organization_output(s: Session, row: dict[str, Any]) -> OrganizationOutput:
    role = access.org_role(s, row["id"])
    if role is None and s.is_hub_admin:
        acc = "hub_admin"
    elif role is not None:
        acc = "organization"
    else:
        acc = "company"
    return OrganizationOutput(**common_out(s, row), organization_id=row["id"], display_name=row["display_name"], access=acc, role=role, path=str(s.abs_path(row["path"])), is_demo=bool(row["is_demo"]))


class CompanySummary(CommonOut):
    company_id: str
    organization_id: str
    organization_name: str
    display_name: str
    legal_name: str
    home_currency: str
    schema_revision: str
    is_demo: bool
    access: str | None
    role: str | None
    path: str | None
    registered_by_name: str | None


def company_summary(s: Session, row: dict[str, Any], organization_name: str, names: dict[str, str] | None = None) -> CompanySummary:
    acc, role = access.company_role(s, row["id"], row["organization_id"])
    names = names if names is not None else user_names(s, {row["created_by"]})
    return CompanySummary(**common_out(s, row), company_id=row["id"], organization_id=row["organization_id"], organization_name=organization_name,
                          display_name=row["display_name"], legal_name=row["legal_name"], home_currency=row["home_currency"],
                          schema_revision=row["schema_revision"], is_demo=bool(row["is_demo"]), access=acc, role=role,
                          path=str(s.abs_path(row["path"])), registered_by_name=names.get(row["created_by"]))


class Empty(BaseModel):
    model_config = {"extra": "forbid"}


class ListInput(BaseModel):
    model_config = {"extra": "forbid"}


class NameInput(BaseModel):
    model_config = {"extra": "forbid", "str_strip_whitespace": True}
    name: str = Field(description="Display name")


__all__ = ["CommonOut", "OrganizationOutput", "CompanySummary", "WriteOutput", "Empty", "ListInput", "NameInput", "common_out", "organization_output", "company_summary"]
