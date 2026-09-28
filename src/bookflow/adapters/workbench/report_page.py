"""What every report page shows above and around its own statement.

A report opens with its numbers: the page runs the report on its company-calendar defaults
instead of opening on an empty filter form, folds the filters into one line a person reads
("Jan 1 – Sep 27 · Accrual · Edit"), offers a handful of date ranges that run it again, and
puts the one figure the report is usually opened for at the top. That figure is always a
total the report command itself returned; nothing here adds, subtracts or rounds money.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Callable

from bookflow.adapters.workbench import date_defaults as DateDefaults
from bookflow.adapters.workbench import report_print as ReportPrint
from bookflow.adapters.workbench.forms import translate
from bookflow.core.errors import BookflowError

# The figure at the top of each report: which of the command's own totals, and its name.
# A report listed with no total has no single figure worth a headline; its heading says
# what the rows are instead.
HEADLINES = {
    "profit-and-loss": ("net_income", "Net income"),
    "profit-and-loss-by-class": ("net_income", "Net income"),
    "profit-and-loss-by-job": ("net_income", "Net income"),
    "income-tax-summary": ("net_income", "Net income"),
    "balance-sheet": ("assets", "Total assets"),
    "cash-flows": ("net_change_in_cash", "Net change in cash"),
    "ar-aging": ("total", "Total open"),
    "ap-aging": ("total", "Total open"),
    "collections": ("overdue", "Total overdue"),
    "open-invoices": ("balance", "Total open"),
    "unpaid-bills": ("balance", "Total open"),
    "sales-by-customer": ("income", "Total income"),
    "sales-by-item": ("income", "Total income"),
    "sales-by-rep": ("income", "Total income"),
    "expenses-by-vendor": ("expense", "Total expense"),
    "inventory-valuation": ("asset_value", "Inventory value"),
    "stock-status": ("asset_value", "Inventory value"),
    "unbilled-costs": ("remaining", "To bill"),
    "statement": ("closing", "Balance due"),
    "trial-balance": (None, "Account balances"),
    "general-ledger": (None, "Ledger entries"),
    "transaction-detail": (None, "Transactions by account"),
    "missing-checks": (None, "Check numbers"),
    "customer-balance-summary": ("balance", "Total balance"),
    "customer-balance-detail": ("balance", "Total balance"),
    "vendor-balance-summary": ("balance", "Total balance"),
    "vendor-balance-detail": ("balance", "Total balance"),
    "open-purchase-orders": ("open_balance", "Open balance"),
    "purchases-by-vendor": ("amount", "Total purchases"),
    "purchases-by-item": ("amount", "Total purchases"),
    "deposit-detail": ("deposited", "Total deposited"),
    "transaction-list-by-date": (None, "Transactions by date"),
    "vendor-1099-summary": ("reportable", "Reportable payments"),
    "reconciliation-discrepancy": (None, "Reconciliations and what changed in them"),
}
# Inputs the summary line already names, or that only steer paging.
SUMMARISED = {"date_from", "date_to", "as_of", "basis", "limit", "cursor"}


def open_report(cmd, attempted: dict[str, str], read: Callable[[dict], dict]):
    """Run a report on the values its form opened with: (result, inputs, error).

    A report whose required inputs have no value yet -- nothing to default them from --
    is not an error to show: it is a report that opens on its form, so a refusal of the
    inputs themselves returns nothing and the form stays open. Any other refusal (the
    member may not read it, the books moved) is the page's answer and is shown.
    """
    try:
        raw, _headers, _preview = translate(cmd, dict(attempted), None)
        raw.pop("cursor", None)
        result = read(raw)
    except BookflowError as error:
        if error.code == "E_VALIDATION":
            return None, None, None
        return None, None, error.to_dict()
    return result, cmd.input_model.model_validate(raw).model_dump(), None


def context(cmd, company_id: str, company: dict | None, result: dict | None,
            inputs: dict | None) -> dict[str, Any] | None:
    """The summary line, the date chips and the headline for one report result."""
    if not result or inputs is None or not isinstance(result.get("metadata"), dict):
        return None
    verb = cmd.verb
    metadata = result["metadata"]
    period = metadata.get("period") or {}
    key, label = HEADLINES.get(verb, (None, None))
    totals = result.get("totals") or {}
    figure = totals.get(key) if key else None
    today = DateDefaults.company_today(company)
    chips = []
    for name, dates in DateDefaults.presets(cmd.name, today):
        chips.append({"label": name, "active": all(str(inputs.get(field)) == value for field, value in dates.items()),
                      "url": ReportPrint.return_url(company_id, verb, {**inputs, **dates})})
    fields = cmd.input_model.model_fields
    extra = [name for name, value in inputs.items()
             if name not in SUMMARISED and value not in (None, False, [], "")
             and not (name in fields and value == fields[name].default)]
    return {
        "date_from": period.get("date_from"),
        "date_to": period.get("date_to"),
        "basis": metadata.get("basis"),
        "extra_filters": len(extra),
        "chips": chips,
        "headline": label,
        "figure": figure if isinstance(figure, dict) else None,
        "today_date": date.fromisoformat(today),
    }
