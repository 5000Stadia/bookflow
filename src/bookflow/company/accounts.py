"""Row 5 chart-of-accounts validation, projection, and mutation plans."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, ValidationError
import sqlalchemy as sa

from bookflow.company import schema
from bookflow.company.charts import ACCOUNT_TYPES, SYSTEM_ROLE_TYPES
from bookflow.company import list_service
from bookflow.company.lists import get_list_definition, hierarchy_projection, normalize_display_name
from bookflow.core.errors import BookflowError
from bookflow.core.ids import is_ulid, new_id
from bookflow.core.money import Money, is_currency
from bookflow.core.session import now_iso
from bookflow.storage.engine import Database


AccountType = Literal[
    "bank",
    "accounts_receivable",
    "other_current_asset",
    "fixed_asset",
    "other_asset",
    "accounts_payable",
    "credit_card",
    "other_current_liability",
    "long_term_liability",
    "equity",
    "income",
    "cost_of_goods_sold",
    "expense",
    "other_income",
    "other_expense",
    "non_posting",
]

NormalBalance = Literal["debit", "credit", "none"]
StatementFamily = Literal["balance_sheet", "profit_and_loss", "neither"]

NORMAL_BALANCE: dict[str, NormalBalance] = {
    "bank": "debit",
    "accounts_receivable": "debit",
    "other_current_asset": "debit",
    "fixed_asset": "debit",
    "other_asset": "debit",
    "accounts_payable": "credit",
    "credit_card": "credit",
    "other_current_liability": "credit",
    "long_term_liability": "credit",
    "equity": "credit",
    "income": "credit",
    "cost_of_goods_sold": "debit",
    "expense": "debit",
    "other_income": "credit",
    "other_expense": "debit",
    "non_posting": "none",
}

STATEMENT_FAMILY: dict[str, StatementFamily] = {
    **{
        account_type: "balance_sheet"
        for account_type in (
            "bank",
            "accounts_receivable",
            "other_current_asset",
            "fixed_asset",
            "other_asset",
            "accounts_payable",
            "credit_card",
            "other_current_liability",
            "long_term_liability",
            "equity",
        )
    },
    **{
        account_type: "profit_and_loss"
        for account_type in (
            "income",
            "cost_of_goods_sold",
            "expense",
            "other_income",
            "other_expense",
        )
    },
    "non_posting": "neither",
}

_NUMBER = re.compile(r"^[0-9]{1,7}$", re.ASCII)
_INSTITUTION_TYPES = frozenset(
    {
        "bank",
        "accounts_receivable",
        "other_current_asset",
        "fixed_asset",
        "other_asset",
        "accounts_payable",
        "credit_card",
        "other_current_liability",
        "long_term_liability",
    }
)
_REIMBURSABLE_TYPES = frozenset({"expense", "cost_of_goods_sold"})
_REIMBURSABLE_INCOME_TYPES = frozenset({"income", "other_income"})
_PROTECTED_SYSTEM_FIELDS = frozenset(
    {"name", "type", "parent_id", "currency", "system_role", "active"}
)

_ITEM_ACCOUNT_TYPES: dict[str, frozenset[str]] = {
    "income_account_id": frozenset({"income", "other_income"}),
    "expense_account_id": frozenset({"expense", "other_expense", "cost_of_goods_sold"}),
    "cogs_account_id": frozenset({"cost_of_goods_sold"}),
    "asset_account_id": frozenset({"other_current_asset", "fixed_asset", "other_asset"}),
    "deposit_account_id": frozenset({"bank", "other_current_asset"}),
    "liability_account_id": frozenset(
        {"accounts_payable", "other_current_liability", "long_term_liability"}
    ),
    "accumulated_depreciation_account_id": frozenset({"fixed_asset", "other_asset"}),
    "depreciation_expense_account_id": frozenset({"expense", "other_expense"}),
    "gain_loss_account_id": frozenset(
        {"income", "other_income", "expense", "other_expense"}
    ),
}

_LOGICAL_FIELDS = (
    "name",
    "number",
    "type",
    "parent_id",
    "description",
    "currency",
    "tax_line",
    "institution_name",
    "institution_account_last4",
    "routing_number_last4",
    "provider_profile_ref",
    "next_check_number",
    "check_reorder_number",
    "order_printable_checks",
    "default_class_id",
    "track_reimbursable_expenses",
    "reimbursable_income_account_id",
    "note",
)


def _validation(field: str, problem: str, **details: Any) -> BookflowError:
    return BookflowError(
        "E_VALIDATION",
        details={"fields": [{"field": field, "problem": problem}], **details},
    )


class _StrictInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)


class AccountCreateInput(_StrictInput):
    name: str = Field(min_length=1, max_length=200)
    number: str | None = Field(default=None, pattern=r"^[0-9]{1,7}$")
    type: AccountType
    parent_id: str | None = None
    description: str | None = Field(default=None, max_length=200)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    tax_line: str | None = Field(default=None, min_length=1, max_length=128)
    institution_name: str | None = Field(default=None, min_length=1, max_length=200)
    institution_account_last4: str | None = Field(default=None, min_length=4, max_length=4)
    routing_number_last4: str | None = Field(
        default=None, pattern=r"^[0-9]{4}$"
    )
    provider_profile_ref: str | None = Field(default=None, min_length=1, max_length=255)
    next_check_number: str | None = Field(default=None, min_length=1, max_length=64)
    check_reorder_number: str | None = Field(default=None, min_length=1, max_length=64)
    order_printable_checks: bool | None = None
    default_class_id: str | None = None
    track_reimbursable_expenses: bool = False
    reimbursable_income_account_id: str | None = None
    note: str | None = None


class AccountUpdateInput(_StrictInput):
    account: str = Field(description="Account id or canonical full name")
    expected_version: int | None = Field(default=None, ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=200)
    number: str | None = Field(default=None, pattern=r"^[0-9]{1,7}$")
    type: AccountType | None = None
    parent_id: str | None = None
    description: str | None = Field(default=None, max_length=200)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    tax_line: str | None = Field(default=None, min_length=1, max_length=128)
    institution_name: str | None = Field(default=None, min_length=1, max_length=200)
    institution_account_last4: str | None = Field(default=None, min_length=4, max_length=4)
    routing_number_last4: str | None = Field(
        default=None, pattern=r"^[0-9]{4}$"
    )
    provider_profile_ref: str | None = Field(default=None, min_length=1, max_length=255)
    next_check_number: str | None = Field(default=None, min_length=1, max_length=64)
    check_reorder_number: str | None = Field(default=None, min_length=1, max_length=64)
    order_printable_checks: bool | None = None
    default_class_id: str | None = None
    track_reimbursable_expenses: bool | None = None
    reimbursable_income_account_id: str | None = None
    note: str | None = None


@dataclass(frozen=True)
class AccountMutation:
    action: Literal["create", "update", "activate", "deactivate"]
    before: Mapping[str, Any] | None
    after: Mapping[str, Any]


@dataclass(frozen=True)
class ProjectionUpdate:
    record_id: str
    values: Mapping[str, Any]


@dataclass(frozen=True)
class AccountUpdatePlan:
    mutation: AccountMutation | None
    projections: tuple[ProjectionUpdate, ...] = ()
    changed_fields: tuple[str, ...] = ()

    @property
    def affected_descendant_ids(self) -> tuple[str, ...]:
        return tuple(item.record_id for item in self.projections)


@dataclass(frozen=True)
class AccountActivePlan:
    requested_id: str
    requested_active: bool
    mutations: tuple[AccountMutation, ...]

    @property
    def changed(self) -> bool:
        return bool(self.mutations)

    @property
    def affected_ids(self) -> tuple[str, ...]:
        return tuple(str(mutation.after["id"]) for mutation in self.mutations)


def _parse_create(payload: AccountCreateInput | Mapping[str, Any]) -> AccountCreateInput:
    if isinstance(payload, AccountCreateInput):
        return payload
    try:
        return AccountCreateInput.model_validate(dict(payload))
    except ValidationError as exc:
        fields = [
            {
                "field": ".".join(str(part) for part in item["loc"]) or "input",
                "problem": item["msg"],
            }
            for item in exc.errors(include_url=False)
        ]
        raise BookflowError("E_VALIDATION", details={"fields": fields}) from None


def _company_info(db: Database) -> dict[str, Any]:
    row = db.conn.execute(sa.select(schema.company_info)).mappings().first()
    if row is None:
        raise BookflowError("E_INTERNAL", details={"record_type": "company_info"})
    return dict(row)


def _row(db: Database, record_id: str) -> dict[str, Any]:
    found = db.conn.execute(
        sa.select(schema.accounts).where(schema.accounts.c.id == record_id)
    ).mappings().first()
    if found is None:
        raise BookflowError(
            "E_RECORD_NOT_FOUND",
            details={"selector": None, "suggestions": []},
        )
    return dict(found)


def resolve_account(db: Database, selector: Any) -> dict[str, Any]:
    definition = get_list_definition("account")
    assert definition is not None
    try:
        return list_service.resolve_selector(db, schema.accounts, definition, selector)
    except BookflowError as exc:
        if exc.code == "E_RECORD_NOT_FOUND" and isinstance(selector, str) and is_ulid(selector):
            raise BookflowError(
                "E_RECORD_NOT_FOUND",
                details={"selector": None, "suggestions": []},
            ) from None
        raise


def _active_reference(
    db: Database, table: sa.Table, record_id: str, *, field: str, record_type: str
) -> dict[str, Any]:
    return list_service.require_active_reference(
        db, table, record_id, field=field, record_type=record_type
    )


def _number_available(
    db: Database, number: str | None, *, exclude_id: str | None = None
) -> None:
    if number is None:
        return
    if not isinstance(number, str) or not _NUMBER.fullmatch(number):
        raise _validation("number", "must contain one through seven ASCII digits")
    query = sa.select(schema.accounts.c.id).where(
        schema.accounts.c.number_key == number
    )
    if exclude_id is not None:
        query = query.where(schema.accounts.c.id != exclude_id)
    collision = db.conn.execute(query).first()
    if collision is not None:
        raise BookflowError(
            "E_NAME_TAKEN", details={"field": "number", "value": number}
        )


def _validate_type_profile(values: Mapping[str, Any]) -> None:
    account_type = values["type"]
    if account_type not in ACCOUNT_TYPES:
        raise _validation("type", "is not a supported account type")
    if values.get("provider_profile_ref") is not None:
        raise _validation(
            "provider_profile_ref",
            "is unavailable until the protected provider store exists",
        )
    institution_fields = ("institution_name", "institution_account_last4")
    if account_type not in _INSTITUTION_TYPES:
        invalid = [field for field in institution_fields if values.get(field) is not None]
        if invalid:
            raise _validation(
                invalid[0], f"is not available for account type {account_type}"
            )
    bank_fields = (
        "routing_number_last4",
        "next_check_number",
        "check_reorder_number",
        "order_printable_checks",
    )
    if account_type != "bank":
        invalid = [field for field in bank_fields if values.get(field) is not None]
        if invalid:
            raise _validation(
                invalid[0], f"is available only for bank accounts"
            )
    if values.get("tax_line") is not None and account_type == "non_posting":
        raise _validation("tax_line", "is not available for non-posting accounts")

    tracking = bool(values.get("track_reimbursable_expenses", False))
    income_id = values.get("reimbursable_income_account_id")
    if tracking and account_type not in _REIMBURSABLE_TYPES:
        raise _validation(
            "track_reimbursable_expenses",
            "is available only for expense and cost-of-goods-sold accounts",
        )
    if tracking and income_id is None:
        raise _validation(
            "reimbursable_income_account_id",
            "is required when reimbursable-expense tracking is enabled",
        )
    if not tracking and income_id is not None:
        raise _validation(
            "reimbursable_income_account_id",
            "must be null when reimbursable-expense tracking is disabled",
        )

    role = values.get("system_role")
    if role is not None:
        expected_type = SYSTEM_ROLE_TYPES.get(str(role))
        if expected_type is None or expected_type != account_type:
            raise BookflowError(
                "E_SYSTEM_RECORD",
                details={"record_id": values.get("id"), "fields": ["system_role", "type"]},
            )
        if not values.get("active", True):
            raise BookflowError(
                "E_SYSTEM_RECORD",
                details={"record_id": values.get("id"), "fields": ["active"]},
            )


def _validate_references(
    db: Database,
    values: Mapping[str, Any],
    *,
    reference_changes: set[str],
    require_active_parent: bool,
) -> None:
    parent_id = values.get("parent_id")
    if parent_id is not None:
        parent = _row(db, str(parent_id))
        if require_active_parent and not parent["active"]:
            raise BookflowError(
                "E_INACTIVE_REFERENCE",
                details={"field": "parent_id", "record_type": "account", "record_id": parent_id},
            )
        if parent["type"] != values["type"]:
            raise _validation("parent_id", "must name an account of the same type")

    class_id = values.get("default_class_id")
    if class_id is not None and "default_class_id" in reference_changes:
        if not bool(_company_info(db)["use_classes"]):
            raise _validation(
                "default_class_id",
                "cannot be assigned while company classes are disabled",
            )
        _active_reference(
            db, schema.classes, str(class_id), field="default_class_id", record_type="class"
        )

    income_id = values.get("reimbursable_income_account_id")
    if income_id is not None:
        income = _active_reference(
            db,
            schema.accounts,
            str(income_id),
            field="reimbursable_income_account_id",
            record_type="account",
        )
        if income["type"] not in _REIMBURSABLE_INCOME_TYPES:
            raise _validation(
                "reimbursable_income_account_id",
                "must name an active income or other-income account",
            )


def _transaction_count(db: Database, account_id: str) -> int:
    if not sa.inspect(db.conn).has_table("posting_lines"):
        return 0
    value = db.conn.execute(
        sa.text("SELECT count(*) FROM posting_lines WHERE account_id = :account_id"),
        {"account_id": account_id},
    ).scalar_one()
    return int(value)


def balance_expression(db: Database):
    """Exact normal-side text with numeric collation for SQL projection and sorting."""
    from bookflow.company.ledger_reports import register_ledger_functions
    register_ledger_functions(db)
    db.raw.create_function("bookflow_account_normal", 2,
        lambda value, side: str(-int(value) if side == "credit" else int(value)), deterministic=True)
    db.raw.create_collation("bookflow_integer", lambda left, right: (int(left) > int(right)) - (int(left) < int(right)))
    if not sa.inspect(db.conn).has_table("posting_lines"):
        return sa.literal("0").collate("bookflow_integer")
    lines = schema.posting_lines
    net = sa.select(sa.func.coalesce(sa.func.bookflow_sum_int(lines.c.debit_minor_units - lines.c.credit_minor_units), "0")).where(
        lines.c.account_id == schema.accounts.c.id).correlate(schema.accounts).scalar_subquery()
    side = sa.case((schema.accounts.c.type.in_([kind for kind, normal in NORMAL_BALANCE.items() if normal == "credit"]), "credit"), else_="debit")
    return sa.func.bookflow_account_normal(net, side).collate("bookflow_integer")


def _active_master_uses(db: Database, account_id: str) -> dict[str, int]:
    uses: dict[str, int] = {}
    for field in _ITEM_ACCOUNT_TYPES:
        count = int(
            db.conn.execute(
                sa.select(sa.func.count())
                .select_from(schema.items)
                .where(
                    schema.items.c.active.is_(True),
                    schema.items.c[field] == account_id,
                )
            ).scalar_one()
        )
        if count:
            uses[field] = count
    count = int(
        db.conn.execute(
            sa.select(sa.func.count())
            .select_from(schema.accounts)
            .where(
                schema.accounts.c.active.is_(True),
                schema.accounts.c.reimbursable_income_account_id == account_id,
            )
        ).scalar_one()
    )
    if count:
        uses["reimbursable_income_account_id"] = count
    count = int(
        db.conn.execute(
            sa.select(sa.func.count())
            .select_from(
                schema.vendor_expense_accounts.join(
                    schema.vendors,
                    schema.vendors.c.id == schema.vendor_expense_accounts.c.vendor_id,
                )
            )
            .where(
                schema.vendor_expense_accounts.c.active.is_(True),
                schema.vendors.c.active.is_(True),
                schema.vendor_expense_accounts.c.account_id == account_id,
            )
        ).scalar_one()
    )
    if count:
        uses["vendor_expense_account"] = count
    return uses


# Owned account slots only. Historical rows remain uses after removal or void.
_CAPTURED_ACCOUNT_QUERIES = (
    ("co0009", (
        "SELECT 1 FROM sales_line_profiles WHERE json_extract(item_snapshot, '$.income_account.id') = :account_id LIMIT 1",
        "SELECT 1 FROM sales_profiles WHERE control_account_id = :account_id OR EXISTS (SELECT 1 FROM json_each(profile_snapshot, '$.tax_rules') WHERE json_extract(value, '$.liability_account.id') = :account_id) LIMIT 1",
        "SELECT 1 FROM sales_tax_components WHERE liability_account_id = :account_id LIMIT 1",
    )),
    ("co0010", (
        "SELECT 1 FROM work_lines WHERE json_extract(facts_snapshot, '$.profile.income_account.id') = :account_id OR EXISTS (SELECT 1 FROM json_each(facts_snapshot, '$.taxes') WHERE json_extract(value, '$.rule.liability_account.id') = :account_id) LIMIT 1",
        "SELECT 1 FROM work_revisions WHERE EXISTS (SELECT 1 FROM json_each(facts_snapshot, '$.profile.tax_rules') WHERE json_extract(value, '$.liability_account.id') = :account_id) LIMIT 1",
    )),
    ("co0014", (
        "SELECT 1 FROM payment_profiles WHERE ar_account_id = :account_id OR deposit_account_id = :account_id LIMIT 1",
    )),
    ("co0020", (
        "SELECT 1 FROM deposit_profiles WHERE bank_account_id = :account_id OR json_extract(facts_snapshot, '$.intent.bank.id') = :account_id OR json_extract(facts_snapshot, '$.intent.cash_back.account.id') = :account_id OR EXISTS (SELECT 1 FROM json_each(facts_snapshot, '$.intent.additional') WHERE json_extract(value, '$.account.id') = :account_id) LIMIT 1",
    )),
)


def _has_captured_posting_use(db: Database, account_id: str) -> bool:
    """Check the owned snapshot, including the conn-only undo domain view."""
    from types import SimpleNamespace
    from bookflow.storage.migrate import FeatureRevision, feature_admission

    # Single-purpose view for feature_admission's revision-marker read only.
    revision_view = SimpleNamespace(raw=db.conn.connection.driver_connection)
    for revision, queries in _CAPTURED_ACCOUNT_QUERIES:
        def resolve(queries=queries):
            return any(db.conn.execute(sa.text(sql), {"account_id": account_id}).first()
                       is not None for sql in queries)
        resolver = feature_admission(revision_view, FeatureRevision('company', revision), resolver=resolve)
        if resolver is not None and resolver():
            return True
    return False


def _validate_type_transition(
    db: Database, current: Mapping[str, Any], proposed: Mapping[str, Any]
) -> None:
    old_type, new_type = str(current["type"]), str(proposed["type"])
    if old_type == new_type:
        return
    if current.get("system_role") is not None:
        raise BookflowError(
            "E_SYSTEM_RECORD",
            details={"record_id": current["id"], "fields": ["type"]},
        )
    if old_type in {"accounts_receivable", "accounts_payable"} or new_type in {
        "accounts_receivable",
        "accounts_payable",
    }:
        raise BookflowError(
            "E_TYPE_CHANGE",
            details={"record_id": current["id"], "from": old_type, "to": new_type},
        )
    children = int(
        db.conn.execute(
            sa.select(sa.func.count())
            .select_from(schema.accounts)
            .where(schema.accounts.c.parent_id == current["id"])
        ).scalar_one()
    )
    if children or _transaction_count(db, str(current["id"])):
        raise BookflowError(
            "E_TYPE_CHANGE",
            details={
                "record_id": current["id"],
                "from": old_type,
                "to": new_type,
                "children": children,
                "has_transactions": bool(_transaction_count(db, str(current["id"]))),
            },
        )
    invalid_uses = []
    for field in _active_master_uses(db, str(current["id"])):
        if field in _ITEM_ACCOUNT_TYPES and new_type not in _ITEM_ACCOUNT_TYPES[field]:
            invalid_uses.append(field)
        elif field == "reimbursable_income_account_id" and new_type not in _REIMBURSABLE_INCOME_TYPES:
            invalid_uses.append(field)
        elif field == "vendor_expense_account" and new_type not in _REIMBURSABLE_TYPES:
            invalid_uses.append(field)
    if invalid_uses:
        raise BookflowError(
            "E_TYPE_CHANGE",
            details={
                "record_id": current["id"],
                "from": old_type,
                "to": new_type,
                "invalidated_references": sorted(invalid_uses),
            },
        )

    if _has_captured_posting_use(db, str(current["id"])):
        raise BookflowError(
            "E_TYPE_CHANGE",
            message="This account is used by saved transaction or work facts and cannot change type.",
            details={"record_id": current["id"], "from": old_type, "to": new_type,
                     "reason": "captured_posting_use"},
        )


def _payload_from_row(row: Mapping[str, Any]) -> dict[str, Any]:
    return {field: row.get(field) for field in _LOGICAL_FIELDS}


def plan_account_create(
    db: Database,
    payload: AccountCreateInput | Mapping[str, Any],
    *,
    actor_id: str,
    via: str,
    record_id: str | None = None,
    at: str | None = None,
) -> AccountMutation:
    """Validate an ordinary account create without writing."""
    parsed = _parse_create(payload)
    info = _company_info(db)
    values = parsed.model_dump(mode="python")
    values["currency"] = values["currency"] or info["home_currency"]
    display, name_key = normalize_display_name(values["name"])
    values["name"] = display
    if not is_currency(values["currency"]):
        raise _validation("currency", f"unknown currency code {values['currency']!r}")
    _number_available(db, values["number"])
    _validate_type_profile(values)
    _validate_references(
        db,
        values,
        reference_changes={"parent_id", "default_class_id", "reimbursable_income_account_id"},
        require_active_parent=True,
    )

    identifier = record_id or new_id()
    parent = _row(db, values["parent_id"]) if values["parent_id"] is not None else None
    depth = 1 if parent is None else int(parent["depth"]) + 1
    full_name, full_name_key = hierarchy_projection(
        display, None if parent is None else str(parent["full_name"]), depth=depth
    )
    definition = get_list_definition("account")
    assert definition is not None
    list_service.assert_name_available(db, schema.accounts, definition, full_name)
    timestamp = at or now_iso()
    after = {
        **list_service.create_metadata(
            actor_id, via, record_id=identifier, at=timestamp
        ),
        "active": True,
        "seed_key": None,
        **values,
        "name_key": name_key,
        "number_key": values["number"],
        "full_name": full_name,
        "full_name_key": full_name_key,
        "depth": depth,
        "path": f"/{identifier}/" if parent is None else f"{parent['path']}{identifier}/",
        "system_role": None,
    }
    return AccountMutation("create", None, after)


def plan_account_update(
    db: Database,
    record_id: str,
    changes: Mapping[str, Any],
    *,
    actor_id: str,
    via: str,
    at: str | None = None,
) -> AccountUpdatePlan:
    """Validate an account patch and its derived subtree projections."""
    unknown = set(changes) - set(_LOGICAL_FIELDS)
    if unknown:
        raise _validation("input", f"unknown account fields {sorted(unknown)}")
    current = _row(db, record_id)
    for required in ("name", "type", "currency"):
        if required in changes and changes[required] is None:
            raise _validation(required, "must not be null")
    payload = _payload_from_row(current)
    payload.update(dict(changes))
    parsed = _parse_create(payload)
    proposed = parsed.model_dump(mode="python")
    display, name_key = normalize_display_name(proposed["name"])
    proposed["name"] = display
    if not is_currency(proposed["currency"]):
        raise _validation("currency", f"unknown currency code {proposed['currency']!r}")

    actual_requested = {
        field
        for field in changes
        if proposed.get(field) != current.get(field)
    }
    protected = sorted(
        actual_requested & _PROTECTED_SYSTEM_FIELDS
        if current.get("system_role") is not None
        else ()
    )
    if protected:
        raise BookflowError(
            "E_SYSTEM_RECORD",
            details={"record_id": current["id"], "fields": protected},
        )
    _validate_type_transition(db, current, proposed)
    if (
        "currency" in actual_requested
        and _transaction_count(db, record_id)
    ):
        raise BookflowError(
            "E_RECORD_IN_USE",
            details={"record_id": record_id, "field": "currency", "dependents": [{"record_type": "posting_line", "count": _transaction_count(db, record_id)}]},
        )
    if "currency" in actual_requested and _has_captured_posting_use(db, record_id):
        raise BookflowError(
            "E_RECORD_IN_USE",
            message="This account is used by saved transaction or work facts and cannot change currency.",
            details={"record_id": record_id, "field": "currency", "reason": "captured_posting_use"},
        )
    _number_available(db, proposed["number"], exclude_id=record_id)
    _validate_type_profile({**current, **proposed})
    _validate_references(
        db,
        proposed,
        reference_changes=actual_requested,
        require_active_parent="parent_id" in actual_requested,
    )

    hierarchy = None
    if actual_requested & {"name", "parent_id"}:
        definition = get_list_definition("account")
        assert definition is not None

        def same_type(root: Mapping[str, Any], parent: Mapping[str, Any]) -> None:
            if parent["type"] != proposed["type"]:
                raise _validation("parent_id", "must name an account of the same type")

        hierarchy = list_service.plan_reparent(
            db,
            schema.accounts,
            definition,
            current,
            parent_id=proposed["parent_id"],
            name=display,
            parent_rule=same_type,
        )
        root_projection = hierarchy.updates[0]
        hierarchy_values = {
            "name": root_projection.name,
            "name_key": root_projection.name_key,
            "parent_id": proposed["parent_id"],
            "full_name": root_projection.full_name,
            "full_name_key": root_projection.full_name_key,
            "depth": root_projection.depth,
            "path": root_projection.path,
        }
    else:
        hierarchy_values = {
            field: current[field]
            for field in (
                "name",
                "name_key",
                "parent_id",
                "full_name",
                "full_name_key",
                "depth",
                "path",
            )
        }

    logical_changes = tuple(sorted(actual_requested))
    if not logical_changes:
        return AccountUpdatePlan(None)
    timestamp = at or now_iso()
    after = {
        **current,
        **proposed,
        **hierarchy_values,
        "number_key": proposed["number"],
        **list_service.update_metadata(current, actor_id, via, at=timestamp),
    }
    projections = ()
    if hierarchy is not None:
        projections = tuple(
            ProjectionUpdate(
                update.id,
                {
                    "full_name": update.full_name,
                    "full_name_key": update.full_name_key,
                    "depth": update.depth,
                    "path": update.path,
                },
            )
            for update in hierarchy.updates[1:]
        )
    return AccountUpdatePlan(
        AccountMutation("update", current, after), projections, logical_changes
    )


def _deactivation_uses(db: Database, account_id: str) -> tuple[dict[str, Any], ...]:
    counts = _active_master_uses(db, account_id)
    transaction_count = _transaction_count(db, account_id)
    if transaction_count:
        counts["posting_line"] = transaction_count
    # Vendor expense-account rows are explicit soft form defaults.
    counts.pop("vendor_expense_account", None)
    return tuple(
        {"record_type": field, "count": counts[field]} for field in sorted(counts)
    )


def _validate_stored_account(
    db: Database, row: Mapping[str, Any], *, activating: bool = False
) -> None:
    display, key = normalize_display_name(row["name"])
    if display != row["name"] or key != row["name_key"]:
        raise _validation("name", "does not match its normalized storage projection")
    _number_available(db, row.get("number"), exclude_id=str(row["id"]))
    if not is_currency(row.get("currency")):
        raise _validation("currency", f"unknown currency code {row.get('currency')!r}")
    _validate_type_profile(row)
    _validate_references(
        db,
        row,
        reference_changes=set(),
        require_active_parent=activating,
    )
    parent = _row(db, str(row["parent_id"])) if row.get("parent_id") else None
    depth = 1 if parent is None else int(parent["depth"]) + 1
    full_name, full_name_key = hierarchy_projection(
        display,
        None if parent is None else str(parent["full_name"]),
        depth=depth,
    )
    path = f"/{row['id']}/" if parent is None else f"{parent['path']}{row['id']}/"
    structural = {
        "number_key": row.get("number"),
        "full_name": full_name,
        "full_name_key": full_name_key,
        "depth": depth,
        "path": path,
    }
    invalid = [field for field, value in structural.items() if row.get(field) != value]
    if invalid:
        raise _validation(
            invalid[0],
            "does not match the account's structural projection",
            record_id=row["id"],
            invalid_fields=invalid,
        )


def plan_account_active_change(
    db: Database,
    record_id: str,
    active: bool,
    *,
    actor_id: str,
    via: str,
    cascade: bool = False,
    at: str | None = None,
) -> AccountActivePlan:
    """Plan idempotent activation or dependency-safe cascade deactivation."""
    if not isinstance(active, bool):
        raise _validation("active", "must be a boolean")
    requested = _row(db, record_id)
    _validate_stored_account(db, requested, activating=active)
    if requested.get("system_role") is not None and bool(requested["active"]) is not active:
        raise BookflowError(
            "E_SYSTEM_RECORD",
            details={"record_id": record_id, "fields": ["active"]},
        )
    if bool(requested["active"]) is active:
        return AccountActivePlan(record_id, active, ())

    definition = get_list_definition("account")
    assert definition is not None
    if active:
        planned_rows = list_service.plan_activation(db, schema.accounts, requested)
    else:
        planned_rows = list_service.plan_deactivation(
            db, schema.accounts, requested, cascade=cascade
        )
        for row in planned_rows:
            _validate_stored_account(db, row)
            if row.get("system_role") is not None:
                raise BookflowError(
                    "E_SYSTEM_RECORD",
                    details={"record_id": row["id"], "fields": ["active"]},
                )
            dependents = _deactivation_uses(db, str(row["id"]))
            if dependents:
                raise BookflowError(
                    "E_RECORD_IN_USE",
                    details={"record_id": row["id"], "dependents": list(dependents)},
                )

    timestamp = at or now_iso()
    action: Literal["activate", "deactivate"] = "activate" if active else "deactivate"
    mutations = tuple(
        AccountMutation(
            action,
            row,
            {
                **row,
                "active": active,
                **list_service.update_metadata(row, actor_id, via, at=timestamp),
            },
        )
        for row in planned_rows
    )
    return AccountActivePlan(record_id, active, mutations)


def persist_account_mutation(db: Database, mutation: AccountMutation) -> None:
    if mutation.before is None:
        db.conn.execute(schema.accounts.insert().values(**dict(mutation.after)))
        return
    db.conn.execute(
        schema.accounts.update()
        .where(schema.accounts.c.id == mutation.after["id"])
        .values(**dict(mutation.after))
    )


def persist_account_update(db: Database, plan: AccountUpdatePlan) -> None:
    if plan.mutation is not None:
        persist_account_mutation(db, plan.mutation)
    for projection in plan.projections:
        db.conn.execute(
            schema.accounts.update()
            .where(schema.accounts.c.id == projection.record_id)
            .values(**dict(projection.values))
        )


def persist_account_active_change(db: Database, plan: AccountActivePlan) -> None:
    for mutation in plan.mutations:
        persist_account_mutation(db, mutation)


def has_transactions(db: Database, account_id: str) -> bool:
    return bool(_transaction_count(db, account_id))


def logical_account_snapshot(row: Mapping[str, Any]) -> dict[str, Any]:
    """Return the deterministic nonsecret stored account audit snapshot."""
    common = (
        "id",
        "version",
        "created_at",
        "created_by",
        "created_via",
        "updated_at",
        "updated_by",
        "updated_via",
        "active",
        "seed_key",
        "full_name",
        "depth",
        "system_role",
    )
    result = {field: row.get(field) for field in (*common, *_LOGICAL_FIELDS)}
    result["provider_profile_ref"] = None
    return result


def _project_account_values(
    row: Mapping[str, Any],
    *,
    home_currency: str,
    child_count: int,
    transaction_count: int,
    net_balance: int = 0,
) -> dict[str, Any]:
    result = logical_account_snapshot(row)
    result["provider_profile_ref"] = None
    result["is_system"] = row.get("system_role") is not None
    from bookflow.core.exact import _require_i64
    normal = -net_balance if NORMAL_BALANCE[str(row["type"])] == "credit" else net_balance
    result["balance"] = Money(_require_i64(normal, field="balance"), home_currency).to_dict()
    result["available_balance"] = None
    result["normal_balance"] = NORMAL_BALANCE[str(row["type"])]
    result["statement_family"] = STATEMENT_FAMILY[str(row["type"])]
    result["has_transactions"] = bool(transaction_count)
    result["child_count"] = child_count
    result["has_children"] = bool(child_count)
    return result


def project_account(db: Database, row: Mapping[str, Any]) -> dict[str, Any]:
    """Project stored account fields, own ledger balance and dependency facts."""
    child_count = int(
        db.conn.execute(
            sa.select(sa.func.count())
            .select_from(schema.accounts)
            .where(schema.accounts.c.parent_id == row["id"])
        ).scalar_one()
    )
    normal_amount = int(db.conn.execute(sa.select(balance_expression(db)).where(
        schema.accounts.c.id == row["id"])).scalar_one_or_none() or 0)
    net_amount = -normal_amount if NORMAL_BALANCE[str(row["type"])] == "credit" else normal_amount
    return _project_account_values(
        row,
        home_currency=str(_company_info(db)["home_currency"]),
        child_count=child_count,
        transaction_count=_transaction_count(db, str(row["id"])),
        net_balance=net_amount,
    )


def list_accounts(
    db: Database,
    *,
    query: str | None = None,
    filters: tuple[str, ...] | list[str] = (),
    sort: str | None = None,
    direction: Literal["asc", "desc"] = "asc",
    include_inactive: bool = False,
) -> list[dict[str, Any]]:
    definition = get_list_definition("account")
    assert definition is not None
    is_system = schema.accounts.c.system_role.is_not(None)
    rows = list_service.list_rows(
        db,
        schema.accounts,
        definition,
        query=query,
        filters=filters,
        sort=sort,
        direction=direction,
        include_inactive=include_inactive,
        filter_expressions={"is_system": is_system},
        sort_expressions={
            "balance": balance_expression(db),
            "hierarchy_order": schema.accounts.c.path,
        },
    )
    if not rows:
        return []
    child_counts = {
        str(parent_id): int(count)
        for parent_id, count in db.conn.execute(
            sa.select(schema.accounts.c.parent_id, sa.func.count())
            .where(schema.accounts.c.parent_id.is_not(None))
            .group_by(schema.accounts.c.parent_id)
        ).all()
    }
    transaction_counts: dict[str, int] = {}
    if sa.inspect(db.conn).has_table("posting_lines"):
        transaction_counts = {
            str(account_id): int(count)
            for account_id, count in db.conn.execute(
                sa.text(
                    "SELECT account_id, count(*) FROM posting_lines "
                    "GROUP BY account_id"
                )
            ).all()
        }
    home_currency = str(_company_info(db)["home_currency"])
    from bookflow.company.ledger_reports import net_balances
    balances = net_balances(db) if sa.inspect(db.conn).has_table("posting_lines") else {}
    return [
        _project_account_values(
            row,
            home_currency=home_currency,
            child_count=child_counts.get(str(row["id"]), 0),
            transaction_count=transaction_counts.get(str(row["id"]), 0),
            net_balance=balances.get(str(row["id"]), 0),
        )
        for row in rows
    ]


__all__ = [
    "ACCOUNT_TYPES",
    "NORMAL_BALANCE",
    "STATEMENT_FAMILY",
    "AccountActivePlan",
    "AccountCreateInput",
    "AccountMutation",
    "AccountType",
    "AccountUpdateInput",
    "AccountUpdatePlan",
    "has_transactions",
    "list_accounts",
    "logical_account_snapshot",
    "persist_account_active_change",
    "persist_account_mutation",
    "persist_account_update",
    "plan_account_active_change",
    "plan_account_create",
    "plan_account_update",
    "project_account",
    "resolve_account",
]
