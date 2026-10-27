# Bulk import and migration

This contract covers roadmap item R107: bringing a business's records into Bookflow from a file, whatever program
they came from, and changing many records at once afterwards. Scope (Kabe, 2026-10-27, "full approve"): (1) CSV
import for any list or transaction type with a preview, per-line refusals, mappings saved per source and re-runs
that never duplicate; (2) guided opening balances without the anchor's own exports, typed or pasted from a
spreadsheet, with a tie-out to the balances the owner gives; (3) spreadsheet-style grid editing of many records,
previewed and audited like any change. It builds on `cutover` ([docs/cutover.md](../docs/cutover.md)), `reconcile
import`'s CSV mapping, the registry's pydantic inputs, `versioning.py`'s merged updates and `batch_invoicing.py`'s
row-by-row outcomes.

**What exists.** `cutover plan/apply/tie-out` moves a company in from the anchor's exports (IIF lists, trial
balance, open invoices, unpaid bills, stock, reconciliations, undeposited receipts, 1099 totals). It reads them into
`cutover_sources.Sources` (`TrialBalanceRow`, `OpenDocument`, `StockRow`), plans steps with an outside id each, runs
the ordinary commands with source reference `cutover:<outside id>`, reports finds on a rerun as `already_in`,
balances through a clearing account that must end at 0.00, and ties out. It requires a trial balance and reads only
the anchor's report layouts. `reconcile import` reads a bank CSV with a per-account saved mapping (`csv_mapping.*`,
`statement_csv_mappings`, header guessing in `statement_files.py`). Nothing reads a customer, item or invoice list
from an arbitrary CSV or edits many records at once. Every update command takes `expected_version` and merges edits
to different fields (`merged_over_versions`).

Sources for the anchor: **[M]** the supplied Missing Manual (2016), printed page numbers; **[E]** the anchor's
Enterprise 24.0 in-depth guide, page numbers. The guides describe screens, not mechanics; where they are silent this
document says so, and the rule given is Bookflow's.

## 1. The anchor's behaviour, and where Bookflow differs

**Matched.**

- **Grid editing of lists.** Add/Edit Multiple List Entries edits customers, vendors, items (and, in Enterprise,
  accounts) as a spreadsheet [M 118; E 35, 80]; columns are chosen and ordered to match a spreadsheet [M 119];
  filter, search and sort pick the records, with *Unsaved* and *with errors* views [M 120]; copy down and duplicate
  row [M 122]; clear column; a line used in a transaction cannot be deleted [M 123]. Excel data pastes in when
  columns and order match, customers before jobs, and referenced values (terms, tax codes) must exist [M 123]; a
  paste overwrites, bad cells turn red [M 126].
- **Partial save.** Save Changes saves every entry without errors, counts them, and leaves the rest to correct [M
  126].
- **Import wizard.** Customers, vendors, items and accounts import from Excel by template or Advanced Import, which
  maps file columns to fields under a kept, named mapping [M 127, 130]. Duplicate handling (ask, keep, replace) and
  error handling (blank the field, or skip the record) are choices; the preview can show *only errors*; a count of
  imported and erroneous records ends it [M 131].
- **Opening balances.** Most accounts come in as one journal entry from the old trial balance; receivables and
  payables as open invoices and unpaid bills (a journal line on them needs a customer or vendor); a bank account at
  its last reconciled balance plus what happened since [M 54, 433]. A customer's "opening balance" shortcut is
  advised against: it hides the invoices and cannot be paid against them [M 67]. Setup can bulk-enter names,
  products and bank accounts [M 15].

**Deliberate differences.**

- **Import is the command a person would use.** The wizard warns an import cannot be undone [M 128]. Here every row
  is the registry command's own input: validated, permission-checked and audited as a typed one. No write path
  exists that a command does not.
- **Transactions import too.** The wizard does lists only; transactions need IIF [M 697] or a third-party tool [M
  698]. Bookflow imports invoices, bills, payments, journal entries and the other documents from CSV.
- **The file finds its mapping.** The anchor's is picked from a menu [M 130]; Bookflow matches by headers (§4).
- **Re-runs cannot duplicate.** The guides give duplicate choices but no row identity, and Enterprise lists
  "mistakenly import the same transactions more than once" among its repair jobs [E 79]. Here every row has a key
  (§2) and a second run says `already_in`.
- **Decisions are remembered.** An unknown terms name is set up on the spot in the anchor [M 126]; here it is
  mapped, created (simple lists only) or refused, and the answer is saved with the mapping.
- **No delete in a grid or an import.** The anchor deletes lines [M 123]; Bookflow's deletes need explicit grants
  and no import or bulk-edit act reaches them (§8).
- **Grid saves are diffs.** Before and after for each change, merged with others' edits to other fields, refused on
  the same field, revertible (§7).

**Outside this item:** IIF transactions and any IIF reader beyond `cutover`'s lists (K3); reading `.xlsx` directly
(K2; paste or save as CSV); payroll history; attachments; scheduled or watched-folder imports; bank statements
(`reconcile import`); undoing an import by deleting (§8).

**Prior art** (from knowledge, not re-checked against current releases; research `r26fa44`). *Odoo* `base_import`
matches columns by header, offers a rolled-back **Test**, remembers the column choice by header name, updates by an
external id, and commits a batch all-or-nothing. *ERPNext* Data Import: insert or update by name, a preview with
re-mapping, **partial success** with an exportable error log, chunked runs with progress, child rows below the
parent. *GnuCash*: named presets, a matcher that flags likely duplicates, lines grouped by a document id.
*Akaunting*: fixed-header sheets, queued jobs, no saved mapping or preview. *Adopted:* the dry-run test, mapping by
header, partial success with a fix-and-reupload loop, a stable external row key, a content match when there is none,
lines grouped by a document key, chunks with progress. *Rejected:* all-or-nothing as the default; a mapping keyed by
column name alone across sources; silent create of unknown references; fixed templates as the only contract; any
write around validation.

## 2. Rows and outcomes

### Import kinds

An **import kind** names a registry write command that creates (or, in `update` mode, updates) one record, plus its
**natural key**, **group key** and lines path for documents, which fields are references, and defaults. Mappable
fields are the command's own input paths as the CLI docs list them (`billing_address.city`, `lines[].item`), so a
field the importer never heard of still maps.

| Group | Kinds (first set) |
|---|---|
| Lists | `account`, `customer` (jobs via `parent`), `vendor`, `item` (service, non-inventory, inventory part, other charge, discount), `term`, `class`, `customer-type`, `vendor-type`, `payment-method`, `ship-method`, `sales-rep`, `price-level` |
| Documents | `invoice`, `bill`, `credit-memo`, `vendor-credit`, `sales-receipt`, `journal`, `payment` (applied to invoices by number), `bill-payment`, `check`, `card-charge`, `estimate`, `purchase-order`, `sales-order`, `inventory adjust` |

`import kinds` lists them with required fields, key, references, an example header and the command each row becomes.
A kind is a few lines of declaration and adds no write path. **Completeness is a test:** every registry write
command that writes only the company database is an import kind or is in `NOT_IMPORTABLE` with a reason
(`destructive`, `merge`, `void`, `delete`, `restore`, `needs-a-person`, `not-a-record`); a new command that is
neither fails the build. That is what "whatever else the registry has" means.

### A row becomes the command's input

1. The mapping (§4) turns a row into input paths: a column, a constant or a default; value maps replace known
   strings first (`"Net 30 days"` to `"Net 30"`).
2. Values are coerced by the field's declared type (§3). A blank cell is **omitted**, never sent empty; an update
   that should clear a field says so (`clear_on_blank`).
3. A document's lines are the rows sharing its group key (invoice number); header fields come from the first row and
   a later row that disagrees refuses the whole document (`header_conflict`). Line order is file order.
4. The command's own input model validates the dict, so typed and imported records fail with the same code and
   message. References pass as the file's text (name, full name, account number, id), which commands already accept;
   the importer adds no second resolver and no fuzzy match, only *suggestions* in the preview.
5. The command runs through the session path `cutover` nests commands with (`run_in_session`): the caller as actor
   with "on behalf of" preserved, the run's reason and directive, source reference `import:<run>:<row>`. A missing
   permission gives the command's `E_PERMISSION`; the importer grants nothing. A command that also writes the hub
   database is not importable (a test enumerates writes).

### The row key: why a re-run never duplicates

Each row (a document counts once) has a **key hash**, `sha256(source | kind | normalized key tuple)`, normalized by
NFC, trim, case-fold and collapsed spaces.

- **Natural key** from the mapping's `key` columns. Kind defaults: customer, vendor, item `full_name`; account
  `number` else `full_name`; invoice `number`; bill `vendor + reference`; journal `number`; payment `customer + date
  + amount + reference`.
- **Content key** when no key column exists: the row's mapped values plus an occurrence number among identical rows.
  The preview says `keyed by content: a re-run is safe only for unchanged rows`, and a **document** kind with no key
  refuses a real run without `--key-by-content` (an edited amount would import a second copy).
- **The ledger.** `import_keys (source, key hash)` is unique and names the record made. The command's write, its
  audit event and the key row commit or roll back together in one savepoint, so a record never exists without its
  key. Outcomes are read from this ledger, not by scanning the audit log as `cutover` does.
- **Existing records.** In `create` mode a row whose natural key matches a record no import made is `unchanged` when
  every mapped field is equal and `differs` otherwise (both values shown, nothing applied). `update` changes only
  matches (`missing` otherwise); `upsert` does both. An update sends the record's current `expected_version`, so it
  merges with other edits to other fields as a typed update does.

### Outcomes, atomic or not

Per row: `create`/`update` in a preview; then `created`, `updated`, `unchanged`, `exists`, `differs`, `already_in`,
`skipped` or `refused`, with a stable `code` (the command's error code, or `header_conflict`, `unknown_reference`,
`bad_value`, `no_key`, `duplicate_in_file`...), a message, the file line and the column.

- **Partial success is the default**: each row or document is its own savepoint, as in `batch_invoicing.py`; a
  refusal rolls back that row only and a refused document leaves no lines. `--atomic` makes the first refusal roll
  back the whole run, key ledger included. **Opening balances (§6) are atomic by default**: half an opening does not
  tie.
- **A runaway stops itself.** After `stop_after_refusals` (default 100) the run is `stopped`, keeping what it did; a
  wrong mapping refuses thousands of rows fast. `import run` resumes it once the cause is fixed.
- **The result is a file.** `import errors <run>` writes refused rows with their original columns plus `code`,
  `message`, `line`, ready to correct and re-upload as the same source; the re-upload imports only what is missing.
  Cells beginning `=`, `+`, `-` or `@` get a leading apostrophe so a spreadsheet runs no formula.
- **Period rules apply.** Closing date, locks, inactive references and sequences refuse rows through the commands;
  the importer bypasses none.

### References that do not exist

The preview groups unknown references by kind and value, with the rows they touch and up to three suggestions
(closest names by edit distance; the look-alike test `cutover` uses for duplicate parties).

| Reference | Default | May a run create it |
|---|---|---|
| Customer, vendor, item, account on a **document** | refuse (`unknown_reference`) | customer and vendor with `create_missing`, only if no look-alike exists; item and account never |
| Terms, customer or vendor type, payment or ship method, sales rep, price level, class | ask | with `create_missing`, from the name alone |
| A **list** row's own references (a customer's terms) | ask | as above |

*Ask* returns `decisions` and the run waits: `import decide <run> --map "Acme Plumbing=Acme Plumbing Inc." --create
"Net 45" --skip "Foo Co"`; `--remember` stores the answer in the mapping's value maps, so next month does not ask
again. A created record comes from its own create command in the same savepoint, with its own audit event. A
near-miss is never mapped on the importer's judgement: one typo silently making a second "Acme" is the failure this
prevents.

## 3. Reading files

A shared reader, `company/tabular.py`, owns decoding, delimiters, money, dates and booleans. `statement_files.py`
and `cutover_sources.py` keep their own readers (their behaviour is relied on); a test feeds all three the same
money and date strings and asserts they agree.

- **Text.** Strict UTF-8; a UTF-8 or UTF-16 byte order mark honoured and stripped; otherwise Windows-1252, flagged
  `encoding: cp1252 (guessed)` in the preview. A byte that decodes in neither refuses the file; nothing becomes `?`.
  CR, LF or CRLF; embedded newlines and doubled quotes in quoted cells; blank rows ignored.
- **Delimiter.** Comma, semicolon, tab or pipe, sniffed on the first 50 lines and kept in the mapping once
  confirmed. **Pasted TSV** (what Excel and Sheets copy) is a tab-delimited file arriving as text. The first row is
  the header unless `header_row: false` (columns become `Column A`, `Column B`); `skip_rows` passes title rows; a
  row with more or fewer cells than the header refuses as `row_width`.
- **Money.** Currency symbol, spaces and non-breaking spaces, thousands separators, `(1,234.56)` and `1234.56-`
  negative. The mapping holds `decimal` and `thousands`; detection reads the whole column and asks when `1.234`
  could be either. More decimals than the currency's places is `E_AMOUNT_PRECISION`, never rounded. Debit and credit
  columns, or a signed amount with a type column, are declared per kind (journal, payment).
- **Dates.** ISO, `YYYYMMDD`, slashed and dotted forms. The order of `01/02/2026` is decided per column by reading
  every value (a first part over 12 means day-first, a second part over 12 month-first); when all are ambiguous the
  preview asks and stores `date_order`. Two-digit years pivot at the company's current year. Bare 5-digit Excel
  serials are refused with a note to export dates as text.
- **Strings stay strings.** Phone numbers, postal codes and account numbers keep leading zeros; a cell is never
  evaluated as a formula. Booleans accept yes/no/true/false/1/0/y/n.
- **Size and transport.** The file streams; outcomes go to `import_rows`, so memory does not grow with rows
  (criterion 8). A file arrives as an attachment id, as `content`, or over MCP through `transport.input_file`.

## 4. Saved mappings

A **source** is a named origin of files for one kind ("Square customer export", "Old sheet: open bills"). Its
**mapping** is the recipe: columns to input paths, constants, defaults, formats (§3), key, mode, `on_unknown` and
value maps. A mapping changes only by saving a new version.

| Table | Holds |
|---|---|
| `import_sources` | id, unique `name`, `kind`, `active`, version, who and when |
| `import_mappings` | id, source, version, `header_fingerprint`, normalized `headers`, `columns`, `formats`, `key`, `mode`, `on_unknown`, `value_maps`, `defaults`, audit event; never updated or deleted |

**Matching a file.** Headers are normalized (trim, case-fold, collapsed spaces, BOM removed); the fingerprint is the
hash of the sorted set.

1. **Exact:** same set, same kind and source: used without asking.
2. **Probable:** all mapped headers present and at least 80 percent overlap: offered with a diff ("new: Region;
   missing: none"). A person confirms; an agent passes `--mapping NAME` after reading the diff. A mapped header that
   is *absent* never matches silently, so a renamed Amount column cannot post the wrong column.
3. **None:** `import preview` returns a **guessed mapping**: headers matched to the kind's field labels and paths
   and synonyms (as `statement_files.guess_mapping` does), each guess with confidence, sample values and the
   detected formats.

An agent turns a messy file into a mapping by previewing the guess, correcting it (`--set "Cust Name=customer"`) and
saving with `import-mapping save --from-run`. Saving is not an act on the books; a run with a mapping always
previews first. `statement_csv_mappings` stay as they are.

## 5. Preview, run and large files

An import is two calls on one stored run, so what ran is what was previewed.

- **`import preview`** reads the file (attachment, `content` or pasted text), applies the mapping, runs every row
  through its command in dry-run (validation, permissions, references, period rules, key ledger) and stores a run in
  state `previewed`: file SHA-256 and attachment, mapping version, per-row outcomes. It returns counts first
  (`create 188, exists 4, differs 2, refused 6`), then decisions needed, the first refused rows, and a sample of the
  exact records. It writes only import tables, never the books.
- **`import run <run>`** executes a bounded **slice** (default 500 rows or 20 seconds) and returns counts, `state`
  (`running`, `stopped`, `waiting`, `done`) and `next`. A caller loops; the browser polls its own run. No background
  process imports: **a run advances only when someone calls it**, so nothing writes while nobody looks. Rows
  revalidate when they run; a row the books now refuse is `refused`, not skipped.
- **Resume.** A run records its position and each row's outcome; after a crash, a closed browser or a lost
  connection, `import run` continues at the first row without one. The key ledger commits with each write, so a
  slice killed mid-way repeats nothing. `import cancel` keeps what was done.
- **Large files.** The first target is 10,000 rows. Limits are measured, not guessed: increment 1 measures on the
  demo company on the root NVMe and records in the as-built notes the parse and guess time, the preview time per row
  and the run time per row for `customer create` (lightest) and `invoice post` (heavy); increment 5 sets the slice,
  the refusal stop and a per-run row cap (starting value 20,000) from them. Acceptance is a relative bound
  (criterion 8), so the numbers can move without the contract lying. A larger file is refused at preview with its
  count and how to split it.
- **Backup.** A preview over 100 documents, or any opening-balance run, advises a backup first (the existing backup
  command) in `next`. The importer takes none itself.
- **Reading a run.** `import show` (counts, mapping version, file digest, who, why), `import query`, `import rows
  <run> --status refused --limit --cursor`, `import errors <run>`. Each made record leads back to its run through
  its audit event's `source_ref`.

## 6. Guided opening balances

**Decision: beside `cutover`, sharing its engine.** `cutover` holds the hard parts: the opening journal, the
clearing account that must reach 0.00, the `Opening balance` item, open invoices and bills as ordinary dated
documents, opening stock, `already_in` reruns and the tie-out. It lacks a way in for a business with no desktop
product: it reads the anchor's report layouts and requires a trial balance. So the guided path (a) reads plain
entered rows into the same `Sources` objects and (b) builds the trial balance from what the owner knows. `cutover
plan/apply/tie-out` is the only thing that posts; there is no second opening-balance engine.

`cutover` gains file kinds, stated explicitly and never detected, with plain columns:

| Kind | Columns |
|---|---|
| `entered_balances` | account (name or number), balance with `side` or a sign; one row per account the owner knows |
| `entered_open_invoices` | customer, number, date, due date or terms, open amount, PO number, memo |
| `entered_unpaid_bills` | vendor, reference, date, due date or terms, open amount, memo |
| `entered_stock` | item, quantity on hand, total value or unit cost |

Their readers (in `cutover_sources.py`, using `tabular.py`) fill `TrialBalanceRow`, `OpenDocument` and `StockRow`;
every exception of docs/cutover.md applies unchanged.

**The balances the owner gives** are what a statement or report shows: bank and card balances, loans, other assets
and liabilities, and optionally total receivables, payables and stock value. A total left out is **derived** from
the rows and labelled so. Equity is computed (assets less liabilities, to the system opening balance equity account)
unless the owner types equity accounts, and then the trial balance must balance itself (`trial_balance_unbalanced`).
The result is kept as an `entered_balances` company attachment, as `cutover apply` keeps its text files.

**Tie-out says what it proved.** Each `cutover tie-out` check gains `basis`: `given` (the owner's figure against the
rows or books) or `derived` (nothing independent). Results and page list them apart: "tied to the 12,430.55 you
gave" against "taken from your invoice list; not independently checked". `tied` needs every `given` check tied and
the clearing account at 0.00. Posting an opening that does not tie is a person's act (`accept_differences`).

**The draft.** Typed rows survive a closed browser in `opening_drafts` (id, `as_of`, entered files as attachment
ids, owner-given figures, state `draft`/`planned`/`applied`/`discarded`, version). `opening-draft create/update/
show/query/discard` and `plan/apply/tie-out` build a `CutoverInput` from the draft and call the cutover engine, so
an agent has one noun; `apply` after a stop continues, as `cutover apply` does. Each wizard step is a grid accepting
a paste (§3) with the import preview's cell errors and §2 decisions; open documents may create their customer or
vendor from the name (look-alikes listed first), and an unknown stock item may be made as an inventory part with the
company's default accounts, on a person's confirmation.

## 7. Grid editing: `bulk-edit`

**Records and fields.** Customers (and jobs), vendors, items and accounts, as the anchor's grid [M 118; E 80].
Columns are the scalar fields of each `update` command, never nested arrays (contacts, shipping addresses, members,
vendor profiles) and, for accounts, never `type`; custom fields appear as `custom:<name>`. `bulk-edit columns
<kind>` lists them. The grid opens on the list's current filter (the `query` command's filters), so "change terms on
80 customers" is a filter and a column.

**Editing.** Cells, a header-matched or positional paste [M 123], and **column operations** over the selection: set,
copy down [M 122], clear, find and replace, add or multiply with a rounding rule ("price up 5 percent, round to
0.05"), set from another column. An operation expands to concrete cell changes before saving, so the audit holds
values, never formulas.

- **`bulk-edit preview <kind>`** takes changes or operations over a selection and stores a **diff**: per record
  `field: before to after` and its current `version`, counts, and per-record refusals found by running each `update`
  in dry-run (bad value, inactive reference, `E_NAME_TAKEN`, a system record). It writes only its tables.
- **`bulk-edit apply <preview>`** applies exactly that diff, one `update` per record sent with its previewed
  `expected_version`. Someone's edit meanwhile follows the existing rules: other fields **merge**
  (`merged_over_versions` recorded); the same field refuses that record with `E_VERSION_CONFLICT` naming who and
  when, and the rest proceed. Partial by default; `--atomic` rolls back on the first refusal.
- **Audit.** Each record's update is its own audit event (actor, via, on behalf of, reason, source reference
  `bulk-edit:<run>`). `bulk_edit_runs` and `bulk_edit_rows` (field, before, after, versions, outcome) answer "what
  did that edit change" in one query. No lock is held over a person's thinking; the diff carries versions.
- **`bulk-edit revert <run>`** previews the inverse against the versions the edit left, applies only fields still
  holding the edit's value and reports the rest as `changed_since`. It is an edit, so edit authority suffices.

## 8. Agents and permissions

New capabilities `import` and `bulk-edit` (reads at `member`, writes at `standard`), checked beside each row's own
command capability; an agent's authority is still the intersection with its bound human's. A permission layer per
increment is chained on the tip (`import-v1`, `import-mapping-v1`, `import-docs-v1`, `bulk-edit-v1`,
`opening-draft-v1`); none changes an existing capability or role default.

| Acts | Who |
|---|---|
| `import kinds`, `template`, `show`, `query`, `rows`, `errors`; `import-mapping show`, `query`, `history`, `match`; `bulk-edit columns`, `show`, `query`; `opening-draft show`, `query` | member |
| `import preview`, `decide`, `run`, `cancel`; `import-mapping save`, `deactivate`; `bulk-edit preview`, `apply`, `revert`; `opening-draft` writes, `plan`, `apply`, `tie-out` | standard, `import` or `bulk-edit` plus each row's command authority (an agent may do all of it, within the limits below) |
| `import approve`, `bulk-edit approve`; `accept_differences`; `create_missing` for a party when a look-alike exists | **people-only** (E_PERMISSION `people_only`, as `check print-confirm`) |

- **An agent can do the job:** read a messy file, propose a mapping, preview, answer the decisions it can (map to an
  existing name, skip), save the mapping and run. It cannot override a look-alike or post an opening that does not
  tie.
- **The go is a person's above a threshold.** An agent run that would create more than 200 documents, or a bulk-edit
  changing more than 100 records, stops in `waiting` ("waiting for your go") until a person taps `approve`. Company
  settings (admin) hold the thresholds (K1).
- **Nothing here deletes.** No kind, field or step can delete, void, merge, restore or unmerge, and none can
  deactivate through a mapping; a test enumerates the commands every path reaches. `import revert-plan <run>`
  returns, for each record a run made, the exact deactivate or void command to be run through the ordinary commands
  by someone with those authorities (question 3).
- **Isolation.** A principal who cannot read a list cannot preview against it (what exists but is invisible reads as
  unknown); sources, mappings, runs and files are per company.
- **Discovery.** The command finder answers "import", "csv", "spreadsheet", "migrate", "opening balance", "paste",
  "reprice", "mass update" and "bulk edit"; `import kinds` then `import template <kind>` then `import preview` is
  the first path, and the preview names the next command.

## 9. Surfaces

### Storage and migrations

One company migration per increment, numbered after main's head when it merges (co0104 today; others are adding
co0105); each keeps every existing row, index and trigger.

| Increment | Tables |
|---|---|
| 1 Lists and runs | `import_runs` (kind, mode, atomic, state, source, mapping, attachment, file SHA-256, counters, cursor, reason, version, times), `import_rows` (run, row, group, key hash, status, record type and id, code, message, line, column), `import_keys` (source, key hash unique, record, run) |
| 2 Mappings | `import_sources`, `import_mappings` (append-only), `import_decisions` (run, reference kind, value, action, target) |
| 4 Documents | `company_info.import_agent_limit`, `bulk_agent_limit` |
| 6 Bulk edit | `bulk_edit_runs`, `bulk_edit_rows` |
| 7 Opening | `opening_drafts` |

Rows are never updated except a run's state and counters, and never deleted; rows of a preview never run may be
compacted after 30 days (the file stays an attachment). Uploaded files are company data in its attachment store.

### Commands

| Command | Does |
|---|---|
| `import kinds`, `template` | what can be imported; header and example per kind |
| `import preview`, `decide`, `run`, `cancel`, `approve` | §2, §5, §8 |
| `import show`, `query`, `rows`, `errors`, `revert-plan` | §5 |
| `import-mapping save`, `show`, `query`, `history`, `match`, `deactivate` | §4 |
| `bulk-edit columns`, `preview`, `apply`, `approve`, `revert`, `show`, `query` | §7 |
| `opening-draft create`, `update`, `show`, `query`, `discard`, `plan`, `apply`, `tie-out` | §6 |
| `cutover` | gains the `entered_*` kinds and `basis` on checks |

All are registry commands, so the CLI, HTTP, MCP and browser reach them identically, each with a dry run,
idempotency on writes (`import run` replays a slice on the same key) and a worked example in its help.

### Browser

- **Import** joins each list's page and the Company menu, one page of steps each reachable again: **File** (drop,
  choose or paste; a pasted table shows at once), **What is it** (kind; the matched mapping with its diff, or the
  guess), **Columns** (file column against field, samples beside, formats editable), **Check** (counts, Problems
  with cell and reason, Decisions, a sample), **Run** (progress by slice; stop, resume), **Done** (counts, the
  refused rows to download, "Save this mapping as", the records made). Forms use htmx; each is tested with a real
  Chrome submit.
- **Edit many** is a button on the customer, vendor, item and account lists: the grid starts from the current filter
  with chosen columns [M 119], edited cells marked, a column menu of operations, Preview changes (before and after
  per cell, refused rows flagged) and Save; a record changed by someone else since the grid opened is flagged first.
- **Opening balances** is a guided page in Company setup: date and checks, balances you know, open invoices, unpaid
  bills, stock, then Tie out and Post with given and derived apart. Paste works in every step.
- **Phone (390 px).** Import is a vertical stepper: columns as cards (file column, three samples, a field picker),
  the preview as a problems list under a sticky counts bar, Run as a progress card that resumes after a lock screen.
  Edit many shows records as cards with the chosen columns editable and the operations as a form above them; no wide
  grid, paste replaced by operations and file import. Every screen works at 1440 and 390.

### An agent's first time

"Import this supplier list": `bookflow import preview --kind vendor --attachment <id> --reason "Import supplier
list" --json` returns the guessed mapping (`Supplier` to `name`, `Pay terms` to `terms_id`), two unknown terms in
`decisions` and the first refused rows. The agent runs `import decide <run> --map "Pay terms:Net 30 days=Net 30"
--remember`, `import-mapping save --from-run <run> --source "Acme export"` and `import run <run>`.

## 10. Measures and acceptance criteria

What R107 is for: a business arrives from any program, or none, and its books are whole in an afternoon with proof;
next month's file is one step; a price list changes in a minute and every change is accounted for. Each criterion is
tested in the increment that names it; the closing trial runs the whole.

1. **A messy file in.** A fresh agent with only Bookflow's help and MCP, given a customer file it has never seen
   (renamed headers, semicolons, Windows-1252, day-first dates, parenthesized negatives, two unknown terms), reaches
   the correct list on its first attempt: counts and a sampled field-by-field comparison match.
2. **Re-runs never duplicate.** Property: a file cut into pieces, duplicated, reordered, rerun, interrupted and
   resumed in any sequence leaves the records running it once would; a changed amount in an old row is `differs` or
   an update by mode, never a second record.
3. **Next month, no setup.** The same headers run from the saved mapping in two calls; an extra column asks once; a
   missing mapped header refuses to match.
4. **Per-line refusals.** 200 rows with 5 bad: 195 in, 5 listed with line, column, code and reason; the error file,
   corrected and re-uploaded, imports those 5 and only them.
5. **Same as typing.** Random rows through import and through the single command give identical records, postings
   and error codes (audit differs only in `source_ref`); a caller without the permission gets the same refusal.
6. **Opening balances prove.** A business with no desktop product pastes bank balances, 12 open invoices, 8 bills
   and 30 stock items; the books at the date show them, receivables, payables and stock tie to the figures given,
   `derived` checks are named as such, the clearing account is 0.00, and a deliberately wrong total refuses.
7. **Bulk edit is accounted for.** Repricing 300 items by 5 percent previews 300 before-and-after pairs and saves
   300 audit events; another user's edit to a different field of item 17 merges, to the same field refuses only item
   17; revert restores the rest.
8. **Scale and honesty.** A 10,000-row customer file previews and runs with progress; a process killed mid-run
   resumes with no duplicates; throughput is within 1.25 times a script looping the same single command in-process
   over the same rows; memory at 10,000 rows is within 10 percent of that at 1,000 (both measured and recorded).
9. **Nothing deleted, nothing around the door.** No path deletes, voids, merges or writes the hub; every write is a
   registry command; exported error files run no formula.
10. **A phone does it.** A pasted list is imported, and 20 items repriced, on a 390 px screen.

**Measure** (for the roadmap): closing-trial prompts done correctly on the first attempt, out of these, read from
the trial record.

| Prompt | Expected |
|---|---|
| "Here's my customer list from my old system" | `import preview --kind customer`; map terms; save the mapping; run; counts |
| "Same export as last month" | the saved mapping matches; one preview, one run; only new rows created |
| "Load these 40 open invoices from the spreadsheet" | `import preview --kind invoice` keyed by number; unknown customers named, not made |
| "Starting from nothing: bank has 12,430.55, I owe 3,100 on bills, here are the invoices and stock" | `opening-draft create`, entered kinds, `plan`, `tie-out` (given and derived), `apply` |
| "Raise every price in the Fixtures category by 5 percent" | `bulk-edit preview` then `apply`; the diff reported back |
| "That import was wrong" | `import show`, `revert-plan`; no delete |

## 11. Build increments

Ordered by what a business needs first: lists in, the mapping that makes next month free, the screens a person uses,
documents, scale, editing, then the opening. A company that never imports reads exactly as before. Sizes are builder
time: S 30-60 min, M 1.5-2.5 h, L 4-6 h.

1. **Reader, list kinds, preview and run (L).** Migration 1; `tabular.py` and its agreement test; the kind
   declaration and `NOT_IMPORTABLE` test; kinds `account`, `customer`, `vendor`, `item`, the simple lists; guessed
   mapping; keys and ledger; `preview`, `run` (slices, resume, atomic, refusal stop), `show`, `query`, `rows`,
   `errors`; the baseline measurements (§5). *Done when* a 200-row customer file with 5 bad rows previews with the 5
   listed, runs 195, reruns with 0 created, and the 5 fixed rows then import alone; a killed slice resumes with no
   duplicates; the single-command comparison and baseline numbers are in the as-built notes; criteria 2, 4 (CLI) and
   5 hold. *Tests:* §3 fixtures (BOM, cp1252, UTF-16, semicolons, TSV, quoted newlines, parentheses, trailing minus,
   decimal commas, ambiguous dates, leading zeros); the criterion 2 property; permission parity; the completeness
   test.
2. **Saved mappings and decisions (M).** Migration 2; `import-mapping` commands; fingerprint, exact and probable
   matching with its diff; value maps; `decide` and `--remember`; look-alike suggestions. *Done when* criterion 3
   holds over CLI and MCP and an unknown terms name is decided once. *Tests:* synonyms, extra and missing columns, a
   renamed required header, two sources with equal headers, version history.
3. **Import pages (L).** The browser steps and phone layout of §9 for list kinds; paste; refused-rows download;
   mapping save. *Done when* a person imports the criterion 1 file from the browser at 1440 and 390 by a real Chrome
   submit, pasted table included, and the refused-rows file opens in a spreadsheet running no formula. *Tests:* htmx
   submit in Chrome; progress resume after reload; screenshots at both widths.
4. **Documents (L).** Migration 4; document kinds, group keys, header conflicts, line order; reference policy and
   `create_missing`; `payment` and `bill-payment` application by number; the `--key-by-content` guard; `approve` and
   the agent threshold. *Done when* 40 invoices of 3 lines import keyed by number, a wrong-customer document is
   refused whole, a rerun creates none, payments land on the right invoices, and an agent run over the threshold
   waits for a person. *Tests:* each kind against its typed command (criterion 5); closed period; duplicate numbers;
   a look-alike customer refused for an agent.
5. **Scale (M).** Row cap, slice and refusal defaults from the baseline; 10,000-row runs for a list and for
   invoices; memory bound; backup advice. *Done when* criterion 8 holds with numbers recorded. *Tests:* throughput
   against the looped command, kill and resume at three points, memory at 1,000 and 10,000 rows, a file one row over
   the cap.
6. **Bulk edit (L).** Migration 6; `bulk-edit` commands; column set per kind; operations; diff; version handling;
   revert; the grid page and phone cards; `approve`. *Done when* criterion 7 holds in the browser and over MCP and
   20 items are repriced from a phone. *Tests:* rounding (half-up on the currency's places), merge and conflict
   rows, a refused row in the middle, revert after a later edit, the grid at 300 and 2,000 rows (time recorded).
7. **Guided opening balances (L).** Migration 7; the `entered_*` kinds and readers; `basis` on tie-out;
   `opening-draft`; the guided page; `accept_differences`. *Done when* criterion 6 holds and the same data moved in
   through `cutover` from anchor-style exports yields the same books. *Tests:* each exception of docs/cutover.md
   reachable from entered rows; derived against given; an atomic refusal leaves nothing; a rerun after a stop
   continues; derived and typed equity.
8. **Agent pass and close (S).** Command finder terms, agent guide chapter, regenerated docs, the `NOT_IMPORTABLE`
   table reviewed, README, demo (a messy CSV and a paste). *Done when* the closing trial passes.

**Closing trial.** A fresh agent with only Bookflow's help and MCP, given §10's prompts with a messy customer file,
an invoice file, a pasted bank balance and stock list and an item category, does each correctly on its first
attempt. Checked afterwards as the person: records and counts, the saved mapping used again on a second file, the
tie-out's given and derived lines, the bulk edit's audit trail and its revert. One more run starts on a phone.

## 12. Open questions

Each default stands if nobody decides. K1 to K3 are Kabe's.

- **K1. Agent limits.** How much an agent may do before a person's go. *Default:* an agent run creating more than
  200 documents, a bulk-edit changing more than 100 records, and any opening that does not tie wait for a person;
  lists of any size and mappings never wait. Admin-settable.
- **K2. Reading `.xlsx` directly.** The anchor reads Excel files [M 127]; Bookflow has no spreadsheet library.
  *Default:* no; paste (Excel copies tab text) or save as CSV. A later item could add a read-only reader of values
  on the first sheet (no formulas, macros or links), at the cost of a dependency and a parser of untrusted files.
- **K3. IIF transactions.** The roadmap line says "CSV and IIF import"; `cutover` reads IIF lists. *Default:* IIF
  stays with `cutover`, lists only; no TRNS/SPL reader, since the anchor's reports reach the same data through
  `cutover` and a second IIF reader doubles the surface.

For the builder, defaults standing unless review finds otherwise:

1. **Ambiguous date order.** *Default:* ask in the preview, never guess; the browser preselects month-first, as
   `reconcile import`'s auto mode reads US dates.
2. **Row cap per run.** *Default:* 20,000 until increment 5 measures; larger files are split.
3. **Reverting an import.** *Default:* `revert-plan` only; an executing `revert` would be one more audited act with
   the authorities of the deactivate and void commands.
4. **Negative document totals.** *Default:* an invoice or bill file with a negative total refuses, pointing to the
   credit memo or vendor credit kind.
5. **Renaming in the grid.** *Default:* allowed for names and account numbers; a rename onto an existing name is
   `E_NAME_TAKEN` for that row (merging stays `customer merge`).
6. **History import.** *Default:* past-dated documents import as ordinary dated documents under the closing date;
   opening balances come first and only later documents after them, and the opening page says so.
7. **Opening stock via the `item` kind.** *Default:* it creates the item with no quantity; stock comes through the
   opening step or an `inventory adjust` import.
