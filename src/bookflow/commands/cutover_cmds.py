"""`cutover plan`, `cutover apply`, `cutover tie-out`: move a company in from its old books."""
from __future__ import annotations

from bookflow.core.registry import Plan, command
from bookflow.company.cutover_models import (
    CutoverApplyInput, CutoverApplyOutput, CutoverPlanOutput, CutoverTieOutInput, CutoverTieOutOutput,
)

_FILES = (" The files are the old books' exports: IIF list exports (chart of accounts, customers, vendors, items) "
          "and report CSVs (Trial Balance, Open Invoices, Unpaid Bills Detail, optionally the A/R and A/P Aging "
          "Summaries and the Inventory Valuation Summary), and for the rest of the old books each bank and card "
          "account's Reconciliation Summary, the transactions that had not cleared (Transaction Detail and Undeposited "
          "Funds' QuickReport, Cleared: No) and the 1099 Summary for January 1 to the cutover. Attach each export once and pass its id: "
          "`attachment add company_info <company id> FILE` (over MCP the file goes in transport.input_file), then "
          "`files: [{\"attachment\": \"<id>\"}, ...]` on every call. Text works too (`{\"content\": ..., \"name\": ...}`): "
          "`cutover apply` keeps each text file as an attachment and returns its id in `files`, so later calls pass the "
          "id instead of the text.")


@command("cutover plan", scope="company", required_role="member", capability="ledger.read",
         description=("Move a company in from its old books (QuickBooks Desktop IIF and report exports): start here "
                      "to import or migrate its accounts, customers, vendors, items, opening balances and open "
                      "invoices and bills, then run `cutover apply` and `cutover tie-out` with the same input. "
                      "Reads the export files and returns what a move-in at the cutover date would make: the "
                      "resolved mapping of every account, customer, vendor, item and term, every write in order, the "
                      "opening journal, the tie checks and every exception. Writes nothing." + _FILES),
         input_model=CutoverApplyInput, output_model=CutoverPlanOutput,
         error_codes=["E_RECORD_NOT_FOUND", "E_IO", "E_DB_BUSY"])
def plan_cutover(inp, ctx, s):
    from bookflow.company import cutover
    return cutover.plan(s, ctx, inp)


def _apply():
    from bookflow.company import cutover

    def planner(inp, ctx, s):
        return cutover.prepare_apply(s, ctx, inp)

    cmd = command("cutover apply", scope="company", required_role="standard", capability="ledger.post",
                  description=("Move the company in from its old books: make the accounts, terms, customers, vendors and "
                               "items, post each open invoice, credit, bill and vendor credit for its open balance with "
                               "its own number and dates, bring in each item's opening stock, and post one opening "
                               "journal at the cutover date for every other trial-balance account against a clearing "
                               "account that ends at 0.00. Given the rest of the old books, it also brings each uncleared "
                               "check, deposit and charge as its own document, leaves each account's last reconciliation as "
                               "the opening the next `reconcile start` follows, brings the receipts waiting in Undeposited "
                               "Funds for `deposit post` to pick, and sets each 1099 vendor's payments so far this year "
                               "(`vendor 1099-opening`). Runs the ordinary commands, each write carrying the source "
                               "reference `cutover:` plus its outside id, so a rerun makes only what is missing. "
                               "Refused while the plan has blocking exceptions." + _FILES),
                  input_model=CutoverApplyInput, output_model=CutoverApplyOutput, writes={"company"},
                  accepts_idempotency_key=True,
                  error_codes=["E_CUTOVER_BLOCKED", "E_CUTOVER_INCOMPLETE", "E_RECORD_NOT_FOUND", "E_IO", "E_DB_BUSY",
                               "E_REASON_REQUIRED"])(planner)
    cmd.applier(cutover.apply)
    return cmd


cutover_apply = _apply()


@command("cutover tie-out", scope="company", required_role="member", capability="reports",
         description=("Compare the books with the old books as of the cutover date: the trial balance account by "
                      "account, and receivables and payables aging customer by customer and vendor by vendor, column "
                      "by column, plus the clearing account, which ties at 0.00; and, when given, each bank and card "
                      "account against its statement plus what was in transit with its opening proven, the receipts "
                      "waiting for deposit, and the 1099 payments so far this year. Lists every difference." + _FILES),
         input_model=CutoverTieOutInput, output_model=CutoverTieOutOutput,
         error_codes=["E_RECORD_NOT_FOUND", "E_IO", "E_DB_BUSY", "E_QUERY_STALE"])
def tie_out_cutover(inp, ctx, s):
    from bookflow.company import cutover
    return Plan(cutover.tie_out(s, ctx, inp))


CUTOVER_COMMANDS = [plan_cutover, cutover_apply, tie_out_cutover]
