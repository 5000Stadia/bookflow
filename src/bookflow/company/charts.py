"""Frozen packaged chart manifests and atomic chart-application planning."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from importlib.resources import files
import json
import re
from typing import Callable

from pydantic import BaseModel, ConfigDict
import sqlalchemy as sa

from bookflow.company import schema
from bookflow.company.lists import normalize_display_name
from bookflow.core.errors import BookflowError
from bookflow.core.ids import new_id
from bookflow.core.session import now_iso
from bookflow.storage.engine import Database


ACCOUNT_TYPES = frozenset({
    "bank", "accounts_receivable", "other_current_asset", "fixed_asset", "other_asset",
    "accounts_payable", "credit_card", "other_current_liability", "long_term_liability",
    "equity", "income", "cost_of_goods_sold", "expense", "other_income", "other_expense",
    "non_posting",
})
SYSTEM_ROLE_TYPES = {
    "accounts_receivable": "accounts_receivable",
    "accounts_payable": "accounts_payable",
    "undeposited_funds": "other_current_asset",
    "opening_balance_equity": "equity",
    "retained_earnings": "equity",
    "sales_tax_payable": "other_current_liability",
    "inventory_asset": "other_current_asset",
    "cost_of_goods_sold": "cost_of_goods_sold",
    "exchange_gain_loss": "other_income",
}
_NUMBER = re.compile(r"^[0-9]{1,7}$")


class AccountTemplate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    key: str
    number: str
    name: str
    type: str
    system_role: str | None = None


class ChartManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    template_id: str
    version: int
    display_name: str
    description: str
    accounts: tuple[AccountTemplate, ...]


class ChartSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    template_id: str
    version: int
    display_name: str
    description: str
    account_count: int


@dataclass(frozen=True)
class ChartApplicationPlan:
    manifest: ChartManifest
    company_before: dict
    company_after: dict
    account_rows: tuple[dict, ...]


def _invalid(problem: str, **details) -> BookflowError:
    return BookflowError("E_CHART_INVALID", details={"problem": problem, **details})


def validate_manifest(manifest: ChartManifest, required_roles: frozenset[str]) -> None:
    """Reject a packaged chart that cannot be installed as one complete account set."""
    if manifest.version < 1 or not manifest.accounts:
        raise _invalid("manifest version and account collection must be nonempty", template_id=manifest.template_id)
    keys: set[str] = set()
    names: set[str] = set()
    numbers: set[str] = set()
    roles: set[str] = set()
    for account in manifest.accounts:
        if not account.key or account.key in keys:
            raise _invalid("account keys must be unique and nonempty", key=account.key)
        if not _NUMBER.fullmatch(account.number) or account.number in numbers:
            raise _invalid("account numbers must be unique one-to-seven digit strings", number=account.number)
        if account.type not in ACCOUNT_TYPES:
            raise _invalid("unsupported account type", account=account.key, type=account.type)
        display, name_key = normalize_display_name(account.name)
        if display != account.name or name_key in names:
            raise _invalid("account names must be normalized and unique", account=account.key, name=account.name)
        if account.system_role:
            if account.system_role in roles or SYSTEM_ROLE_TYPES.get(account.system_role) != account.type:
                raise _invalid("system role is duplicated or incompatible with account type", account=account.key, role=account.system_role)
            roles.add(account.system_role)
        keys.add(account.key)
        names.add(name_key)
        numbers.add(account.number)
    if roles != required_roles:
        raise _invalid("manifest must assign each required system role exactly once", missing=sorted(required_roles - roles), extra=sorted(roles - required_roles))


@lru_cache(maxsize=1)
def _catalog() -> tuple[tuple[ChartManifest, ...], frozenset[str], str]:
    payload = json.loads(files("bookflow.data").joinpath("chart_manifests.json").read_text(encoding="utf-8"))
    version = payload["version"]
    common = [
        AccountTemplate(key=key, number=number, name=name, type=account_type, system_role=role)
        for key, number, name, account_type, role in payload["common_accounts"]
    ]
    required_roles = frozenset(payload["required_system_roles"])
    manifests = []
    for template_id, definition in payload["templates"].items():
        specific = [
            AccountTemplate(key=f"account.{number}", number=number, name=name, type=account_type)
            for number, name, account_type in definition["accounts"]
        ]
        manifest = ChartManifest(
            template_id=template_id,
            version=version,
            display_name=definition["display_name"],
            description=definition["description"],
            accounts=tuple((*common, *specific)),
        )
        validate_manifest(manifest, required_roles)
        manifests.append(manifest)
    if payload["selection_default"] not in {item.template_id for item in manifests}:
        raise _invalid("selection_default must name a packaged manifest")
    return tuple(manifests), required_roles, payload["selection_default"]


def default_template_id() -> str:
    return _catalog()[2]


def required_system_roles() -> frozenset[str]:
    return _catalog()[1]


def list_manifests() -> tuple[ChartSummary, ...]:
    return tuple(
        ChartSummary(
            template_id=item.template_id,
            version=item.version,
            display_name=item.display_name,
            description=item.description,
            account_count=len(item.accounts),
        )
        for item in _catalog()[0]
    )


def get_manifest(template_id: str) -> ChartManifest:
    for manifest in _catalog()[0]:
        if manifest.template_id == template_id:
            return manifest
    raise BookflowError("E_RECORD_NOT_FOUND", details={"record_type": "chart", "selector": template_id, "suggestions": []})


def plan_chart_application(
    db: Database,
    template_id: str,
    *,
    actor_id: str,
    via: str,
    id_factory: Callable[[], str] = new_id,
    at: str | None = None,
) -> ChartApplicationPlan:
    """Validate the whole chart and return rows for one later atomic apply."""
    manifest = get_manifest(template_id)
    company = db.conn.execute(sa.select(schema.company_info)).mappings().first()
    if company is None:
        raise _invalid("company_info is missing")
    before = dict(company)
    if before["default_chart"] is not None or before["default_chart_version"] is not None:
        raise BookflowError("E_CHART_EXISTS", details={"template_id": before["default_chart"], "version": before["default_chart_version"]})

    existing = db.conn.execute(
        sa.select(
            schema.accounts.c.id, schema.accounts.c.name_key, schema.accounts.c.full_name_key,
            schema.accounts.c.number_key, schema.accounts.c.system_role,
        )
    ).mappings().all()
    existing_names = {row["name_key"] for row in existing} | {row["full_name_key"] for row in existing}
    existing_numbers = {row["number_key"] for row in existing if row["number_key"] is not None}
    existing_roles = {row["system_role"] for row in existing if row["system_role"] is not None}
    conflicts = []
    timestamp = at or now_iso()
    rows = []
    for account in manifest.accounts:
        display, name_key = normalize_display_name(account.name)
        reasons = []
        if name_key in existing_names:
            reasons.append("name")
        if account.number in existing_numbers:
            reasons.append("number")
        if account.system_role and account.system_role in existing_roles:
            reasons.append("system_role")
        if reasons:
            conflicts.append({"account": account.key, "fields": reasons})
            continue
        account_id = id_factory()
        rows.append({
            "id": account_id, "version": 1,
            "created_at": timestamp, "created_by": actor_id, "created_via": via,
            "updated_at": timestamp, "updated_by": actor_id, "updated_via": via,
            "active": True, "seed_key": f"chart.{manifest.template_id}.{account.key}",
            "name": display, "name_key": name_key, "parent_id": None,
            "full_name": display, "full_name_key": name_key, "depth": 1, "path": f"/{account_id}/",
            "number": account.number, "number_key": account.number, "type": account.type,
            "currency": before["home_currency"], "track_reimbursable_expenses": False,
            "system_role": account.system_role,
        })
    if conflicts:
        raise _invalid("existing accounts conflict with the complete chart", conflicts=conflicts)
    after = {
        **before,
        "version": before["version"] + 1,
        "updated_at": timestamp,
        "updated_by": actor_id,
        "updated_via": via,
        "default_chart": manifest.template_id,
        "default_chart_version": manifest.version,
    }
    return ChartApplicationPlan(manifest, before, after, tuple(rows))


def apply_chart_application(db: Database, plan: ChartApplicationPlan) -> tuple[dict, tuple[dict, ...]]:
    """Apply a previously validated plan inside the caller's open transaction."""
    for row in plan.account_rows:
        db.conn.execute(schema.accounts.insert().values(**row))
    changed = {
        key: plan.company_after[key]
        for key in ("version", "updated_at", "updated_by", "updated_via", "default_chart", "default_chart_version")
    }
    result = db.conn.execute(
        schema.company_info.update()
        .where(
            schema.company_info.c.id == plan.company_before["id"],
            schema.company_info.c.version == plan.company_before["version"],
            schema.company_info.c.default_chart.is_(None),
            schema.company_info.c.default_chart_version.is_(None),
        )
        .values(**changed)
    )
    if result.rowcount != 1:
        raise BookflowError("E_CHART_EXISTS", details={"problem": "company chart state changed after planning"})
    ids = [row["id"] for row in plan.account_rows]
    stored = tuple(
        dict(row)
        for row in db.conn.execute(
            sa.select(schema.accounts).where(schema.accounts.c.id.in_(ids)).order_by(schema.accounts.c.path)
        ).mappings().all()
    )
    return plan.company_after, stored
