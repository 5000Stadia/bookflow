"""Pure captured-rule tax calculation; no company defaults or persisted state.

Call ``calculate_tax(lines, policy=..., currency=...)`` after resolving line nets
and applicable rules. Empty rules mean exempt/customer-exempt/disabled tax. Every
rule applies to the whole line net; partial or compound bases are unsupported.
Tax ordinals belong to the caller's document, independently of settlement keys.
The result is canonical by ordinal and binary rule ID, including zero cells.

Input rule fields match captured sales TaxRule facts. Their complete provenance
survives in each cell, but only IDs/rates/agency/account determine compatibility.
These local immutable types deliberately do not import sales/work dependencies.
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from bookflow.core.errors import BookflowError
from bookflow.core.exact import _require_i64, round_ratio_half_even
from bookflow.core.money import is_currency

TAX_DENOMINATOR = 100_000_000


class TaxPolicy(StrEnum):
    LINE_COMPONENT_HALF_EVEN = "line_component_half_even"
    LINE_COMBINED_HALF_UP = "line_combined_half_up"
    INVOICE_COMBINED_HALF_UP = "invoice_combined_half_up"


class _Fact(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True,
                              revalidate_instances="always")


class TaxReference(_Fact):
    id: str
    label: str
    version: int


class TaxAccount(_Fact):
    id: str
    name: str
    full_name: str
    number: str | None
    type: str
    normal_balance: Literal["debit", "credit"]


class TaxRule(TaxReference):
    rate_percent_millionths: int
    agency: TaxReference
    liability_account: TaxAccount


class TaxLine(_Fact):
    tax_ordinal: int
    net_minor_units: int
    rules: tuple[TaxRule, ...] = ()


class TaxCell(_Fact):
    tax_ordinal: int
    rule: TaxRule
    exact_numerator: int
    tax_minor_units: int


class TaxLineTotal(_Fact):
    tax_ordinal: int
    net_minor_units: int
    tax_minor_units: int
    gross_minor_units: int


class TaxBucket(_Fact):
    tax_ordinals: tuple[int, ...]
    net_minor_units: int
    exact_numerator: int
    tax_minor_units: int
    gross_minor_units: int
    cells: tuple[TaxCell, ...]


class TaxLiabilityTotal(_Fact):
    agency_id: str
    liability_account_id: str
    tax_minor_units: int


class TaxAccountTotal(_Fact):
    liability_account_id: str
    tax_minor_units: int


class TaxCalculation(_Fact):
    policy: TaxPolicy
    currency: str
    buckets: tuple[TaxBucket, ...]
    lines: tuple[TaxLineTotal, ...]
    liabilities: tuple[TaxLiabilityTotal, ...]
    accounts: tuple[TaxAccountTotal, ...]
    net_minor_units: int
    tax_minor_units: int
    gross_minor_units: int


def _invalid(field: str, problem: str) -> BookflowError:
    return BookflowError("E_VALIDATION", details={
        "fields": [{"field": field, "problem": problem}]})


def _amount(value: int, field: str) -> int:
    _require_i64(value, field=field)
    if value < 0:
        raise _invalid(field, "must be nonnegative")
    return value


def _identity(value: str, field: str) -> None:
    if not value or value != value.strip() or "\x00" in value:
        raise _invalid(field, "must be a nonempty stable ID without padding or NUL")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise _invalid(field, "must be valid UTF-8") from None


def _economic_vector(line: TaxLine) -> tuple[tuple[str, int, str, str], ...]:
    return tuple((r.id, r.rate_percent_millionths, r.agency.id, r.liability_account.id)
                 for r in sorted(line.rules, key=lambda r: r.id.encode("utf-8")))


def calculate_tax(
    lines: Sequence[TaxLine], *, policy: TaxPolicy | str, currency: str,
) -> TaxCalculation:
    """Calculate one home-currency document, returning complete bounded cents.

    Accepts an empty document (useful for all-remaining forecasts). Collection
    limits belong to callers: forecasts may exceed the 200-line entry limit.
    Rules are flat percentages in [0,100], as in existing captured sales facts.
    Duplicate ordinals/rule IDs and malformed facts raise E_VALIDATION; signed64
    storage overflow raises E_VALUE_RANGE. Exact numerators are unbounded and
    must be encoded losslessly by any later snapshot serializer.
    """
    if not isinstance(policy, str):
        raise _invalid("policy", "must name a supported tax calculation policy")
    try:
        mode = TaxPolicy(policy)
    except ValueError:
        raise _invalid("policy", "must name a supported tax calculation policy") from None
    if not is_currency(currency):
        raise _invalid("currency", "must be a supported home currency")
    if not isinstance(lines, Sequence) or isinstance(lines, (str, bytes)):
        raise _invalid("lines", "must be a sequence of TaxLine facts")
    checked = []
    ordinals = set()
    for line in lines:
        if not isinstance(line, TaxLine):
            raise _invalid("lines", "must contain TaxLine facts")
        try:
            line = TaxLine.model_validate(line)
        except ValidationError as exc:
            raise _invalid("lines", str(exc)) from None
        _require_i64(line.tax_ordinal, field="tax_ordinal", positive=True)
        if line.tax_ordinal in ordinals:
            raise _invalid("tax_ordinal", "duplicate tax ordinal")
        ordinals.add(line.tax_ordinal)
        _amount(line.net_minor_units, "line.net")
        rule_ids = set()
        for rule in line.rules:
            for field, value in (("rule.id", rule.id), ("agency.id", rule.agency.id),
                                 ("liability_account.id", rule.liability_account.id)):
                _identity(value, field)
            for ref in (rule, rule.agency):
                _require_i64(ref.version, field="rule.version", positive=True)
            rate = _amount(rule.rate_percent_millionths, "rule.rate")
            if rate > TAX_DENOMINATOR:
                raise _invalid("rule.rate", "must be at most 100 percent")
            if rule.id in rule_ids:
                raise _invalid("rule.id", "duplicate tax rule ID on one line")
            rule_ids.add(rule.id)
        checked.append(line)
    checked.sort(key=lambda line: line.tax_ordinal)

    groups = defaultdict(list)
    for line in checked:
        if line.rules:
            # Currency and policy are common to this entire document. Empty-rule
            # lines never enter a taxable bucket, even when their net is positive.
            key = (_economic_vector(line) if mode == TaxPolicy.INVOICE_COMBINED_HALF_UP
                   else line.tax_ordinal)
            groups[key].append(line)

    buckets = []
    line_taxes = defaultdict(int)
    liability_taxes = defaultdict(int)
    account_taxes = defaultdict(int)
    for members in groups.values():
        raw = [(line.tax_ordinal, rule, line.net_minor_units * rule.rate_percent_millionths)
               for line in members
               for rule in sorted(line.rules, key=lambda r: r.id.encode("utf-8"))]
        numerator = sum(n for _, _, n in raw)
        if mode == TaxPolicy.LINE_COMPONENT_HALF_EVEN:
            cents = [round_ratio_half_even(n, TAX_DENOMINATOR) for _, _, n in raw]
        else:
            q, r = divmod(numerator, TAX_DENOMINATOR)
            target = q + (2 * r >= TAX_DENOMINATOR)
            cents = [n // TAX_DENOMINATOR for _, _, n in raw]
            rank = sorted(range(len(raw)), key=lambda i: (
                -(raw[i][2] % TAX_DENOMINATOR), raw[i][0], raw[i][1].id.encode("utf-8")))
            for i in rank[:target - sum(cents)]:
                cents[i] += 1
        cells = []
        for (ordinal, rule, n), amount in zip(raw, cents, strict=True):
            amount = _amount(amount, "component.tax")
            cells.append(TaxCell(tax_ordinal=ordinal, rule=rule,
                                 exact_numerator=n, tax_minor_units=amount))
            line_taxes[ordinal] = _amount(line_taxes[ordinal] + amount, "line.tax")
            key = (rule.agency.id, rule.liability_account.id)
            liability_taxes[key] = _amount(liability_taxes[key] + amount, "liability.tax")
            account_id = rule.liability_account.id
            account_taxes[account_id] = _amount(account_taxes[account_id] + amount, "account.tax")
        net = _amount(sum(line.net_minor_units for line in members), "bucket.net")
        tax = _amount(sum(cents), "bucket.tax")
        buckets.append(TaxBucket(tax_ordinals=tuple(line.tax_ordinal for line in members),
                                 net_minor_units=net, exact_numerator=numerator,
                                 tax_minor_units=tax, gross_minor_units=_amount(net + tax, "bucket.gross"),
                                 cells=tuple(cells)))

    totals = tuple(TaxLineTotal(
        tax_ordinal=line.tax_ordinal, net_minor_units=line.net_minor_units,
        tax_minor_units=line_taxes[line.tax_ordinal],
        gross_minor_units=_amount(line.net_minor_units + line_taxes[line.tax_ordinal], "line.gross"),
    ) for line in checked)
    net = _amount(sum(line.net_minor_units for line in totals), "document.net")
    tax = _amount(sum(line.tax_minor_units for line in totals), "document.tax")
    return TaxCalculation(
        policy=mode, currency=currency, buckets=tuple(buckets), lines=totals,
        liabilities=tuple(TaxLiabilityTotal(agency_id=agency, liability_account_id=account,
                                          tax_minor_units=amount)
                          for (agency, account), amount in sorted(liability_taxes.items())),
        accounts=tuple(TaxAccountTotal(liability_account_id=account, tax_minor_units=amount)
                       for account, amount in sorted(account_taxes.items())),
        net_minor_units=net, tax_minor_units=tax,
        gross_minor_units=_amount(net + tax, "document.gross"),
    )
