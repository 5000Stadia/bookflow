"""What a `reconcile import` came back with, as a person reads it.

The command answers in minor units (an ending balance of 623695 is 6,236.95); this turns every
figure into money text the `money` filter shows, and each statement line into one row with its
outcome, so the page reads the same after Preview and after Submit.
"""
from typing import Any

from bookflow.core.money import CURRENCIES

STATUS = {
    "matched": "Matched",
    "suggested": "Suggested",
    "unmatched": "Not in the books",
    "reconciled": "Already reconciled",
    "duplicate": "Repeated in the file",
}
COUNTS = (("lines", "Lines"), ("matched", "Matched"), ("suggested", "Suggested"),
          ("unmatched", "Not in the books"), ("reconciled", "Already reconciled"),
          ("duplicate", "Repeated in the file"), ("newly_marked", "Ticked by this import"),
          ("cleared_without_line", "Ticked without a statement line"),
          ("previously_imported", "Imported before"))


def decimal(units: Any, currency: str) -> str:
    """Minor units as a plain decimal string ("623695" USD -> "6236.95"); '' when absent."""
    if units is None or units == "":
        return ""
    units = int(units)
    places = CURRENCIES.get(currency, (2,))[0]
    sign = "-" if units < 0 else ""
    units = abs(units)
    if not places:
        return sign + str(units)
    return f"{sign}{units // 10 ** places}.{units % 10 ** places:0{places}d}"


def _candidate(value: dict, currency: str) -> dict:
    return dict(date=value.get("date", ""), amount=decimal(value.get("amount"), currency),
                payee=", ".join(value.get("payees") or ()), number=value.get("number") or "",
                memo=value.get("memo") or "")


def view(result: dict | None) -> dict | None:
    """The import's summary, counts and per-line outcomes, with money as decimals."""
    if not isinstance(result, dict) or "lines" not in result or "counts" not in result:
        return None
    currency = result.get("currency") or ""
    lines = []
    for line in result.get("lines") or ():
        ticked = ("Ticked by this import" if line.get("marked")
                  else "Already ticked" if line.get("already_marked") else "")
        lines.append(dict(
            date=line.get("date", ""), payee=line.get("payee") or "", memo=line.get("memo") or "",
            number=line.get("number") or "",
            amount=line.get("amount_decimal") or decimal(line.get("amount"), currency),
            status=line.get("status", ""), status_label=STATUS.get(line.get("status"), line.get("status", "")),
            reason=line.get("reason") or "", ticked=ticked,
            previously_imported=bool(line.get("previously_imported")),
            suggestions=[_candidate(s, currency) for s in line.get("suggestions") or ()]))
    counts = result["counts"]
    draft = result.get("draft") or None
    return dict(
        currency=currency, format=(result.get("format") or "").upper(),
        statement_date=result.get("statement_date"),
        ending_balance=decimal(result.get("ending_balance"), currency),
        counts=[dict(key=k, label=label, value=counts.get(k, 0)) for k, label in COUNTS
                if counts.get(k) or k in ("lines", "matched", "suggested", "unmatched")],
        draft=draft, draft_started=bool(result.get("draft_started")), lines=lines,
        cleared_without_line=[_candidate(s, currency) for s in result.get("cleared_without_line") or ()],
        next_step=result.get("next_step") or "")
