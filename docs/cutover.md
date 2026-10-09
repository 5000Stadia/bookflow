# Moving a company in

`cutover plan`, `cutover apply` and `cutover tie-out` bring a company in from its old books at a period boundary: its lists, its opening balances, and its open invoices and bills, from QuickBooks Desktop exports. The three commands take the same input, so the same arguments run each step.

## Before the move-in

- The company has a chart of accounts with its system accounts (`company new` applies the `general` chart unless told otherwise; `chart apply` applies one later). The move-in maps the old accounts onto it and makes the rest.
- Sales tax is turned on in the company setup when the old books charge sales tax, so customers come in with their tax codes.
- The company's closing date, if any, is before the oldest open document.
- The books hold no entries dated on or before the cutover date other than the move-in's own.

## The exports

The cutover date is `as_of`: the date of the old books' trial balance. The opening journal is dated that day, each open document keeps its own date, and new work starts the next day.

| `kind` | Export | Required |
|---|---|---|
| `iif` | File > Utilities > Export > Lists to IIF Files: Chart of Accounts, Customer List, Vendor List, Item List (one file or several) | the chart of accounts, unless every trial balance account already exists here |
| `trial_balance` | Reports > Accountant & Taxes > Trial Balance, accrual basis, as of the cutover date | yes |
| `open_invoices` | Reports > Customers & Receivables > Open Invoices, as of the cutover date | when the trial balance carries receivables |
| `unpaid_bills` | Reports > Vendors & Payables > Unpaid Bills Detail, Dates: All, as of the cutover date | when the trial balance carries payables |
| `ar_aging`, `ap_aging` | A/R Aging Summary and A/P Aging Summary, as of the cutover date | no; `cutover tie-out` compares against them when given |
| `inventory_valuation` | Reports > Inventory > Inventory Valuation Summary, as of the cutover date | when the trial balance carries inventory |

Each report is exported to a comma separated values file (Excel > Create New Worksheet > Create a comma separated values (.csv) file). The kind of each file is read from its own headings; `kind` names it when it cannot be.

Attach each export once and pass its id on every call: `attachment add company_info <company id> FILE` (over MCP the file goes in `transport.input_file`), then `{"attachment": "<id>"}` in `files`. A file can also be given as its text (`{"content": "...", "name": "trial_balance.csv"}`); `cutover apply` keeps each text file as an attachment on the company and returns its id in `files[].attachment`, and `cutover plan` shows that id on later runs, so tie-out and reruns pass ids rather than the text. `files[].sha256` identifies each file's content and `files[].decided_by` says whether its kind was given or read from its headings.

Every row of an IIF list must have the fields its `!` header names: a row with a field more, or more than one field fewer (an export may leave out an empty last field), is refused as `row_width`, and a row whose Y/N or whole-number columns hold anything else is refused as `row_shifted`, because a tab added or dropped moves every later field into the wrong column. A report CSV row with more or fewer cells than its column headings is refused the same way. Export the lists and reports again rather than retyping them.

```sh
bookflow attachment add company_info "$company_id" trial_balance.csv --company "Riverbend Plumbing" --reason "Old books export" --json
bookflow cutover plan --as-of 2026-09-30 --files '[{"attachment": "<id>"}, {"attachment": "<id>"}]' --company "Riverbend Plumbing" --json
bookflow cutover apply --as-of 2026-09-30 --files '[...]' --company "Riverbend Plumbing" --reason "Move in from the old books" --json
bookflow cutover tie-out --as-of 2026-09-30 --files '[...]' --company "Riverbend Plumbing" --json
```

## A worked example: plan, apply, tie-out

Over MCP, attach each export first, then pass only attachment ids. The ids stay the same on every call, so the plan, the move-in and the tie-out read exactly the same files, and no file is ever retyped.

1. Find the company id: `{"command": "company show", "input": {}}` returns `company_id`.
2. Attach each export once. The launcher reads the file itself (it must sit under a directory the MCP server was started with as `--input-dir DIR`):

   ```json
   {"command": "attachment add", "reason": "Old books export",
    "input": {"record_type": "company_info", "record_id": "<company_id>", "original_filename": "trial_balance.csv",
              "media_type": "text/csv", "caption": "Old books export"},
    "transport": {"input_file": "/home/me/exports/trial_balance.csv"}}
   ```

   Each result's `attachment.id` is that file's id. Do the same for the IIF lists, the open invoices, the unpaid bills, the inventory valuation and the two aging summaries.
3. Plan, writing nothing:

   ```json
   {"command": "cutover plan", "input": {"as_of": "2026-09-30", "files": [
     {"attachment": "<accounts.iif id>"}, {"attachment": "<customers.iif id>"}, {"attachment": "<vendors.iif id>"},
     {"attachment": "<items.iif id>"}, {"attachment": "<trial_balance.csv id>"}, {"attachment": "<open_invoices.csv id>"},
     {"attachment": "<unpaid_bills.csv id>"}, {"attachment": "<inventory_valuation.csv id>"}]}}
   ```

   Read `summary` and `blocking` first: `blocking` has one line per blocking exception, naming its file, line and problem, and it is kept even when a long result is compacted. Fix each (export the file again, or add a `mappings` entry) and plan again until `ready` is true. A few files can be planned on their own to find a problem faster; without the trial balance the plan says so once, as a note, and `ready` stays false. `clearing.parts` shows what each part of the move-in posts to the clearing account (the opening journal, the invoices and credit memos, the bills and vendor credits, the opening stock and the opening sales tax) and `clearing.net` is 0.00 when they tie.
4. Move in with the same input and a reason: `{"command": "cutover apply", "reason": "Move in from the old books", "input": {...the same as_of and files...}}`. Its `clearing.parts` are built the same way as the plan's, so the two reconcile part by part. Running it again makes nothing new: every record already made is `already_in`.
5. Tie out with the same files plus the two aging summaries: `{"command": "cutover tie-out", "input": {"as_of": "2026-09-30", "files": [...the same ids..., {"attachment": "<ar_aging.csv id>"}, {"attachment": "<ap_aging.csv id>"}]}}`. `tied` is true when the trial balance, receivables, payables, stock and lists match and the clearing account is 0.00; otherwise its rows name each difference.

When the MCP server has no input directory, give a file once as its text (`{"content": "...", "name": "trial_balance.csv"}`): `cutover apply` keeps it as an attachment and returns its id in `files[].attachment` for every later call. Copy the text exactly; a retyped row with a tab added or dropped is refused as `row_width` or `row_shifted`.

## `cutover plan`

Reads the files and returns, writing nothing:

- `mappings`: every old-books account, customer, job, vendor, item and terms name, with the Bookflow record it stands for or `create`. An account maps by its system role (receivables, payables, undeposited funds, inventory, sales tax payable, opening balance equity, retained earnings, cost of goods sold), then by full name and type, then is made from the account list with its number. A number already used by an account of another name is an exception.
- `steps`: every write in order: accounts, terms, customers and jobs, vendors, items, the clearing account and the `Opening balance` item, invoices and credit memos, bills and vendor credits, opening stock, the opening journal, then deactivating what was inactive in the old books. Each step names its outside id.
- `journal`: the opening journal's lines.
- `clearing`: the clearing account's parts (the opening journal's balancing line, the invoices and credit memos, the bills and vendor credits, the opening stock and the opening sales tax adjustment), each debit positive with the records that carry it, and their `net`, 0.00 once everything ties. `cutover apply` returns the same parts.
- `blocking`: each blocking exception as one line, `FILE line N: code: problem`, ahead of the rest of the result.
- `checks`: the trial balance's debits against its credits, its receivables against the open invoices and credits, its payables against the unpaid bills and credits, its inventory against the items' asset values.
- `counts`, first: records and totals by kind (invoices, credit memos, bills, vendor credits, stock and the journal carry their total amount), so the whole run reads at a glance even when the step list is long.
- `exceptions`: every problem, `blocking` first, then `warning` and `note`. `cutover apply` refuses with `E_CUTOVER_BLOCKED` while any blocking exception stands. A record matched to one here is compared with the old books: a term whose days or discount differ is blocking; an account matched to one that keeps another number, such as Retained Earnings, is a note.

`mappings` in the input decides what the plan cannot: an old-books name to a Bookflow ID, number or full name, or `create`. Passing back the plan's own `mappings` pins every target.

## `cutover apply`

Runs the plan's steps through the ordinary commands (`account create`, `customer create`, `invoice post`, `bill post`, `inventory adjust`, `journal post` and the rest), each as its own audited write whose source reference is `cutover:` followed by the record's outside id. Before a step runs, the company audit trail is read for a write with that source reference; one found is reported as `already_in` and not made again. A rerun therefore makes only what is missing, and a run that stopped with `E_CUTOVER_INCOMPLETE` continues from where it stopped. `--idempotency-key` replays the stored result of the same request.

What it posts:

| Old books | Bookflow |
|---|---|
| Every trial balance account except receivables, payables, inventory and (with one agency) sales tax payable | One opening journal dated `as_of`, balanced by one line to the clearing account |
| An open invoice | An invoice for its open balance, with its number, date, due date, terms and customer P.O., of one `Opening balance` line: debit receivables, credit the clearing account |
| An open credit memo or unapplied payment | A credit memo for its amount: debit the clearing account, credit receivables |
| An unpaid bill | A bill for its open balance, with its date, due date, terms and reference number, of one expense line on the clearing account |
| A bill credit | A vendor credit for its amount on the clearing account |
| An item's stock | An inventory adjustment dated `as_of`: its quantity on hand and asset value, against the clearing account |
| Sales Tax Payable, when the old books' sales tax items name one agency | A sales tax adjustment (`sales-tax adjust`) dated `as_of` for that agency against the clearing account, so `sales-tax liability` shows it under the agency and `sales-tax pay` pays it |

The clearing account is `Cutover Clearing`, an other current asset account made on first use, or the account named by `clearing_account`. It is 0.00 exactly when the documents, the stock and the opening sales tax adjustment equal the trial balance's receivables, payables, inventory and sales tax payable. Receivables and payables are never lines of the opening journal.

## `cutover tie-out`

Compares the books as of `as_of` with the old books:

- `trial_balance`: every account's balance, debit positive, against the trial balance file.
- `receivables`: every customer and job's aging, total and each column (current, 1-30, 31-60, 61-90, over 90), against the A/R Aging Summary file, or the open invoices when no aging file is given.
- `payables`: every vendor's aging against the A/P Aging Summary file, or the unpaid bills.
- `inventory`: every item's quantity on hand and asset value against the Inventory Valuation Summary.
- `lists`: what came in against the IIF lists, field by field: active or inactive, account type and number, job status and description, customer and vendor terms and credit limit, 1099 eligibility, item type, price and cost, and terms days and discount. Differences by design (an account matched to one here that keeps its own number, an item the move-in skips) are `notes`.
- `clearing`: the clearing account's balance.

`tied` is true when every compared figure matches to the cent, every list field compared matches and the clearing account is 0.00. With `detail` `differences` (the default) only rows that do not match are listed; `all` lists every compared row.

## Exceptions

| Code | Severity | Meaning |
|---|---|---|
| `unmapped_account` | blocking | A trial balance account is in no account list and matches no account here |
| `number_taken` | blocking | An account number here belongs to an account of another name |
| `type_conflict` | blocking | A name here belongs to an account or item of another type |
| `name_taken` | blocking | An account mapped to `create` has the name of an account here |
| `mapping_not_found` | blocking | A mapping names a record this company does not have |
| `control_account_mapping` | blocking | A receivable or payable account was mapped to an account of another type |
| `several_control_accounts` | blocking | More than one receivable or payable account carries a balance and the documents do not say which they sit in |
| `receivables_do_not_tie`, `payables_do_not_tie`, `inventory_does_not_tie` | blocking | The documents or the stock do not add up to the trial balance's figure |
| `missing_open_invoices`, `missing_unpaid_bills`, `missing_inventory_valuation` | blocking | The trial balance carries the figure and its file was not given |
| `duplicate_file`, `duplicate_document` | blocking | The same export or the same document was given twice |
| `entries_before_cutover` | blocking | The books hold entries dated on or before `as_of` that the move-in did not make |
| `period_closed` | blocking | The closing date is on or after an open document's date |
| `as_of_mismatch`, `cash_basis_trial_balance`, `trial_balance_unbalanced`, `total_mismatch`, `subtotal_mismatch` | blocking | A report was exported for another date or basis, or does not add up to its own totals |
| `no_chart` | blocking | The company has no chart of accounts |
| `no_trial_balance` | blocking, or a note in `cutover plan` | No trial balance file was given. `cutover plan` checks files given in pieces and says once that `cutover apply` needs the trial balance; `cutover apply` and `cutover tie-out` refuse without it |
| `no_account_list` | warning | No chart of accounts IIF was given, so items whose accounts are not already here cannot be planned; it names them once |
| `account_number_dropped` | warning | An account comes in without its number |
| `unknown_terms`, `unknown_class` | warning | A terms or class name is not in this company's lists; the document keeps its due date |
| `item_skipped` | warning | A group, assembly, payment or sales tax group item, or an item whose account Bookflow does not allow, is not brought in |
| `undeposited_funds` | warning | The balance comes in as one opening amount that Make Deposits cannot pick |
| `sales_tax_payable` | warning | The old books' sales tax items name several agencies or none (or the one agency is an existing vendor not flagged as a tax agency), so the opening sales tax owed comes in as one journal amount not tied to an agency and `sales-tax pay` cannot pay it; pay it with a check or journal entry against Sales Tax Payable |
| `row_width`, `row_shifted` | blocking | A list or report row has a field more or fewer than its headings, or a field in the wrong column |
| `term_settings_differ` | blocking | A term here has other days or discount than the old books' term of that name |
| `item_settings_differ` | warning | An item here has another price or cost than the old books' item of that name |
| `account_number_differs` | note | An account matched to one here keeps its own number |
| `open_balance_changed` | warning | A document brought in earlier now shows another open balance in the old books |
| `sales_tax_disabled` | warning | The old books charge sales tax and this company has it turned off |
