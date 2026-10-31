# Bulk import and migration

This contract covers roadmap item R107: bringing a business's records into Bookflow from a file, whatever program
they came from, and changing many records at once afterwards. Scope (Kabe, 2026-10-27, "full approve"): (1) CSV
import for any list or transaction type with a preview, per-line refusals, mappings saved per source and re-runs
that never duplicate; (2) guided opening balances without the anchor's own exports, typed or pasted from a
spreadsheet, with a tie-out to the balances the owner gives; (3) spreadsheet-style grid editing of many records,
previewed and audited like any change; and, added the same day ("Maintaining this reversability for all large batch
imports or modifications is quite helpful"), (4) every import run and every grid edit **reversible as one unit** through
the shared batch mechanism of §8a, the one [bank feeds](bank-feeds.md) (R108) uses for statement imports. It builds on `cutover` ([docs/cutover.md](../docs/cutover.md)), `reconcile
import`'s CSV mapping, the registry's pydantic inputs, `versioning.py`'s merged updates and `batch_invoicing.py`'s
row-by-row outcomes.

> **Changed 2026-10-27 (R187, reshaped the same day).** Builds to [one permission model](principal-directives.md). Replaces the
> earlier note that lifted agent size limits with a confirmed directive. The limits of §8 and K1 now attach to a capability,
> **`import.approve`**: an actor who holds it runs any size, approves waiting runs and accepts differences; an actor who does not,
> human or agent, stops in `waiting` above the thresholds. Humans hold it at standard as today; agents only when their principal
> grants it. No directive lifts a limit, and there is no agent policy or confirmation.

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
  the same field, reversible as one unit (§8a).

**Outside this item:** IIF transactions and any IIF reader beyond `cutover`'s lists (K3); reading `.xlsx` directly
(K2; paste or save as CSV); payroll history; attachments; scheduled or watched-folder imports; bank statements
(`reconcile import`, bank feeds); `cutover` stays as it is (a later adopter of the mechanism, §8a).

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

> **A quick check first (Kabe, 2026-10-27):** "So we're not wasting the user's time or crunching invalid data, can we do
> a quick validation pass on the data which is lightweight and simple and surface to the user the problematic elements
> of the data that are not valid?" Before any preview or run, `import check` reads the file once and checks every cell
> against its field without running a command:
>
> - Is the mapping complete? Every required field needs a column.
> - Does each cell fit its field's type? Money, quantity, date, number, yes/no and choice lists are checked against
>   the field's length and pattern.
> - Does every reference exist? Names, numbers and codes are looked up in one query per list, held in memory.
> - Are row keys repeated within the file?
>
> It returns every problem at once, grouped by column, with row numbers and the exact cell text. Example: "Amount,
> rows 14, 92: '$599.♎56' is not an amount". `import preview` and `import run` start with the same check and stop
> there if anything is wrong, so no command is dry-run on data that cannot land. The check is measured on 10,000 rows;
> its target is a few seconds, against the preview's minutes. It changes nothing and needs only the kind's read
> permission.

> **Decided by Kabe, 2026-10-27, superseding the partial-success default below:** "I think all or nothing seems like
> the move really. Let's say the name 'Johnson, Beck ♎️' is invalid. Well I'm fine there if we drop the odd character,
> not fine if we don't import the whole name. A dollar value of $599.♎️56 however indicates the data is junk and we
> should deny the whole thing until it figures itself out."
>
> - **All or nothing is the default.** An import lands every row or none; a refused row refuses the run, and the
>   preview lists every refused row with its reason so one fix-and-rerun clears them all. Partial success becomes the
>   opt-in (`--partial`).
> - **Text keeps its words.** A character a text field cannot hold (NUL and other control characters, except tab and
>   line breaks where the field allows them) is dropped and the rest of the value imported; the result lists each
>   dropped character by row, column and position, so nothing changes unseen. Characters a field can hold (accents,
>   other scripts, symbols, emoji) are kept as written.
> - **Numbers, money, dates and references are exact or refused.** A stray character in an amount, quantity, date,
>   rate, id or reference means the data is junk: the row, and so the run, is refused, never repaired.
> - **Empty is allowed unless something truly needs it** (Kabe: "Reflex should allow empty fields unless it's
>   architecturally mandatory to ground its index field (which may or may not exist) or a transaction type that may
>   inherently require something to transact"). A blank cell, or a column the file lacks, leaves that field empty or at
>   the default it would have when entered by hand. Only two things are required: what identifies the record (a
>   customer's or item's name, an account's name or number, the row key when one is mapped) and what the transaction
>   cannot exist without (an invoice's customer and a line with an amount; a payment's customer, amount and deposit
>   account; a journal's balanced lines with accounts). An invoice line with no item is a description and amount line,
>   as typed by hand. The import is never stricter than the single command: its required fields are exactly that
>   command's. A blank is not a reference that fails: a named item, customer or account that does not exist ("Mulch"
>   not in the item list) is refused in the check, or created on request (`create_missing`).

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
the clearing account at 0.00. Posting an opening that does not tie (`accept_differences`) needs the capability
`opening.accept-differences`: people hold it at standard, an agent only by its principal's grant (as built, increment 7).

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
- **`bulk-edit reverse <run>`** (changed 2026-10-27; was `revert`, which restored only fields still holding the edit's
  value) restores each record's prior values as one unit through the batch mechanism (§8a). It is refused, naming each
  record, if a record was edited again since (its version is no longer the one the edit left). An opt-in
  `--skip-changed` reverses the rest and leaves those records, and the run is then `reversed_partly` and lists them.
  It is an edit, so edit authority suffices.

## 8. Agents and permissions

> **Changed 2026-10-27.** The earlier default here was "Revert of an import is a plan only": `import revert-plan` printed
> the deactivate or void commands for someone to run, and `bulk-edit revert` restored only untouched fields. Kabe widened
> it ("Maintaining this reversability for all large batch imports or modifications is quite helpful. Consider customer
> batch changes that have unexpected results or are incorrectly described from the user originally"). Now every import
> run and grid edit is a **batch** that `import reverse` / `bulk-edit reverse` undo as one unit, newest first, for as
> long as nothing later depends on it (§8a). `revert-plan` is replaced by `reverse --dry-run`. Question 3 changed with it.

New capabilities `import` and `bulk-edit` (reads at `member`, writes at `standard`), checked beside each row's own
command capability; an agent's authority is still the intersection with its bound human's. A permission layer per
increment is chained on the tip (`import-v1`, `import-mapping-v1`, `import-docs-v1`, `bulk-edit-v1`,
`opening-draft-v1`); none changes an existing capability or role default.

| Acts | Who |
|---|---|
| `import kinds`, `template`, `show`, `query`, `rows`, `errors`; `import-mapping show`, `query`, `history`, `match`; `batch show`, `query`; `bulk-edit columns`, `show`, `query`; `opening-draft show`, `query` | member |
| `import preview`, `decide`, `run`, `cancel`; `import-mapping save`, `deactivate`; `bulk-edit preview`, `apply`, `reverse`; `import reverse`; `opening-draft` writes, `plan`, `apply`, `tie-out` | standard, `import` or `bulk-edit` plus each row's command authority (an agent may do all of it, within the limits below, without `import.approve`) |
| `import approve`, `bulk-edit approve`; `accept_differences`; `create_missing` for a party when a look-alike exists; any run above the thresholds | standard plus capability **`import.approve`** (humans hold it; an agent when granted) |

> **Superseded (2026-10-28 and increment 7).** `import.approve` was never built: approval and the size thresholds were
> dropped for imports and grid edits (see increments 4 and 6), a look-alike is refused for every actor, and
> `accept_differences` is the capability `opening.accept-differences` (As built: increment 7).

- **An actor without `import.approve` can do the job:** read a messy file, propose a mapping, preview, answer the decisions it can
  (map to an existing name, skip), save the mapping and run. It cannot override a look-alike or post an opening that does
  not tie.
- **Beyond a size, a run needs `import.approve`.** A run by an actor without it that would create more than 200
  documents, or a bulk-edit changing more than 100 records, stops in `waiting` ("waiting for approval") until a holder
  runs `approve`; a holder's own run goes straight through. The capability, not an instruction, lifts the limit; a look-alike
  customer and an opening that does not tie (`accept_differences`) also need it. The batch (§8a) records who approved and the
  size. Company settings (admin) hold the thresholds (K1); setting them very high is the way to drop the limit, since every
  batch reverses.
- **Nothing here deletes by itself.** No kind, field or step can delete, void, merge, restore or unmerge, and none can
  deactivate through a mapping; a test enumerates the commands every path reaches. The one exception is reversal
  (§8a), which runs the ordinary inverse commands (a family's `delete`, else `void`; a list record's `deactivate`)
  under the caller's own grants, so a caller without the delete grant for a created family is refused naming it.
- **Isolation.** A principal who cannot read a list cannot preview against it (what exists but is invisible reads as
  unknown); sources, mappings, runs and files are per company.
- **Discovery.** The command finder answers "import", "csv", "spreadsheet", "migrate", "opening balance", "paste",
  "reprice", "mass update" and "bulk edit"; `import kinds` then `import template <kind>` then `import preview` is
  the first path, and the preview names the next command.

### 8a. Reversible batches (the shared mechanism)

A **batch** is any multi-record import or modification: an import run, a grid edit, a bank statement import
([bank feeds](bank-feeds.md)), and later the operations of the last paragraph. It is recorded when it writes and can be
**reversed as one unit**: newest first within its scope, for as long as nothing later depends on it. The mechanism is
small and the same for every kind.

**Ledger** (increment 0; `batches` and `batch_effects`, append-only except `state`):

| Table | Holds |
|---|---|
| `batches` | id, `kind` (`import`, `bulk-edit`, `bank-import`, `bank-sort`...), `scope` (what "newest first" is measured in), `ordering` (`strict` or `record`), label, `state` (`live`, `reversing`, `blocked`, `reversed`, `reversed_partly`), counts, who, how, for whom, why, the run it came from, `reversed_by`, `reversed_at`, `reversal_reason`, reversal audit event |
| `batch_effects` | batch, seq, `record_type`, `record_id`, `action` (`created`, `updated`, `linked`, `rule_learned`...), `version_after` (the record's version the batch left), `before` (JSON of prior values for `updated`), audit event of the write |

A kind registers in code a small adapter: its **scope**, its **ordering**, how to **read an effect's current state**, and
its **inverse** per action. The write path (`import run`, `bulk-edit apply`, `bank-feed import`) records an effect in the
same savepoint as the command's write and audit event, so an effect never exists without its record or the reverse.
`import_rows` and `bulk_edit_rows` keep the row outcomes and the preview's before-and-after as before; the ledger holds
what reversal needs. Cost is one row per touched record, with `before` only for updates (a 300-item reprice: 300 small
rows).

**Ordering.** `strict` (bank imports; scope the account): refused while a later live batch exists in the scope. `record`
(imports, grid edits, bulk sorts): there is no ordering between batches; dependence is judged per record, by the rules
below, so two edits to different customers reverse independently.

**What blocks a reversal.** Computed by a **plan** (`reverse --dry-run`), never by a parallel dependency engine:

- the ordering rule above;
- **edited again:** a record's current version differs from `version_after` (the record was changed since; this is how a
  grid edit's "edited again since" is found, per record, naming it);
- **a created record is in use:** the plan dry-runs the inverse command and reports its own refusal by name (a
  customer with invoices, an invoice with payments applied, an entry reconciled or deposited, a closed period). The
  mechanism adds no knowledge of those rules;
- **no authority:** the caller lacks the grant the inverse command needs.

The refusal is `E_BATCH_BLOCKED` with `details.blockers` (up to 50 and the total): the record, the code, who changed it
and when where known, and the command that clears it. A blocked reversal changes nothing.

**What a reversal does.** For `updated` effects it sends the previewed `before` values as an ordinary update with the
version the batch left, so a concurrent edit is refused by the same version check. For `created` effects it runs the
family's inverse: `delete` with preserved history (R151) where the family has one, else `void` for a document, else
`deactivate` for a list record (the name is released for a later import: a reversed batch releases its `import_keys`
rows, so the corrected file imports again as new). Links (matches, learned rules) are retired. The batch, its effects
and the audit events stay, marked `reversed` with who, how, for whom and why. Atomic up to a measured slice (default 200
records; increment 0 measures); a larger batch runs in slices in state `reversing` that the same command resumes, with
the plan checked whole first and the scope frozen meanwhile. A reversal is itself recorded as a batch (`reversal`), so
what was undone can be read, and a reversed batch is never reopened: to redo, run the corrected import.

**Commands.** `batch show`, `batch query` (all batches, with `reversible` and the first blocker) are shared;
`import reverse`, `bulk-edit reverse` and `bank-feed reverse` are thin, domain-named doors with their own help over one
function. All take `--dry-run`, `reason`, and a plan fingerprint is re-derived inside the write.

**Retention.** Effects are small and kept for ever; no purge. Measured for bank feeds in bank-feeds.md §6 (about 820
bytes per line against about 49 KB average per transaction); for a grid edit it is a row of a few hundred bytes per
record.

**Existing batch operations that could adopt it later.** Not part of R107 or R108; a separate roadmap item. Each already
writes many records in one call, so each would add only an adapter. *Natural fits:* `batch-invoice post` (one invoice per
customer; inverse `invoice delete`), `memorized process` and `memorized-group enter` (the entries a run posted),
`finance-charge assess` (statement charges; inverse `statement-charge void`), `check print-run` (the print batch before it
is confirmed), `inventory-count post` (adjustments; has `inventory-count void` already), `barcode generate` and `notice
generate` (labels and notices created in bulk), `cutover apply` (a whole opening; the clearing-account tie-out makes it
the hardest), and R112's `delivery batch` (cancel the drafts). *Already reversible by their own pair, to be listed in
`batch query` but not re-implemented:* `customer`, `vendor`, `item` and `account merge` (with `unmerge`), `deposit post`
(`deposit void`), `inventory-transfer`, `shipment` and returns. Each adopter gets a scope, an ordering and an inverse; none
changes its command's behaviour.

## 9. Surfaces

### Storage and migrations

One company migration per increment, numbered after main's head when it merges (increments 0 and 1 merged as
co0108 on co0107); each keeps every existing row, index and trigger.

| Increment | Tables |
|---|---|
| 0 Batch ledger | `batches`, `batch_effects` (§8a) |
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
| `import show`, `query`, `rows`, `errors` | §5 |
| `import reverse`, `bulk-edit reverse`, `batch show`, `query` | §8a (`reverse` replaces `revert-plan` and `revert`) |
| `import-mapping save`, `show`, `query`, `history`, `match`, `deactivate` | §4 |
| `bulk-edit columns`, `preview`, `apply`, `approve`, `reverse`, `show`, `query` | §7 |
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
   17; `bulk-edit reverse` restores all 300 prices, or is refused naming item 17 when it was edited again, and
   `--skip-changed` restores the other 299.
8. **Scale and honesty.** A 10,000-row customer file previews and runs with progress; a process killed mid-run
   resumes with no duplicates; throughput is within 1.25 times a script looping the same single command in-process
   over the same rows; memory at 10,000 rows is within 10 percent of that at 1,000 (both measured and recorded).
9. **Nothing deleted, nothing around the door.** No path deletes, voids, merges or writes the hub; every write is a
   registry command; exported error files run no formula.
10. **A phone does it.** A pasted list is imported, and 20 items repriced, on a 390 px screen.
11. **A batch comes back as one.** A 200-row customer import with terms mapped wrongly, and a repricing of 300 items, are
    each reversed in one step: the lists and prices equal the snapshot taken before the batch (created records gone
    from lists, in preserved history), and the corrected file then imports as new rows. A created customer with an
    invoice, or an item edited since, is refused with that record named and the command that clears it, and nothing is
    changed. The reversal needs the caller's own delete and update grants.

**Measure** (for the roadmap): closing-trial prompts done correctly on the first attempt, out of these, read from
the trial record.

| Prompt | Expected |
|---|---|
| "Here's my customer list from my old system" | `import preview --kind customer`; map terms; save the mapping; run; counts |
| "Same export as last month" | the saved mapping matches; one preview, one run; only new rows created |
| "Load these 40 open invoices from the spreadsheet" | `import preview --kind invoice` keyed by number; unknown customers named, not made |
| "Starting from nothing: bank has 12,430.55, I owe 3,100 on bills, here are the invoices and stock" | `opening-draft create`, entered kinds, `plan`, `tie-out` (given and derived), `apply` |
| "Raise every price in the Fixtures category by 5 percent" | `bulk-edit preview` then `apply`; the diff reported back |
| "That import was wrong" | `import show`, `import reverse --dry-run`, then `import reverse`; blockers reported |
| "Undo that price change" | `bulk-edit reverse --dry-run`, then `reverse`; records edited since are named |

## 11. Build increments

Ordered by what a business needs first: lists in, the mapping that makes next month free, the screens a person uses,
documents, scale, editing, then the opening. A company that never imports reads exactly as before. Sizes are builder
time: S 30-60 min, M 1.5-2.5 h, L 4-6 h.

0. **Batch ledger (M).** Migration 0; `batches`, `batch_effects`, the adapter registry, `batch show`, `batch query`,
   the generic plan and reverse, slices and the `reversing` state, `E_BATCH_BLOCKED`; the slice size measured. Built by
   whichever of R107 and R108 starts first. *Done when* a test adapter's batch reverses whole, is refused with its
   blocker named, and resumes after a kill between slices. *Tests:* ordering `strict` and `record`, edited-again,
   inverse refusals reported by name, atomicity, no effect without its write.
1. **Reader, list kinds, preview and run (L).** Migration 1; `tabular.py` and its agreement test; the kind
   declaration and `NOT_IMPORTABLE` test; kinds `account`, `customer`, `vendor`, `item`, the simple lists; guessed
   mapping; keys and ledger; `preview`, `run` (slices, resume, atomic, refusal stop), `show`, `query`, `rows`,
   `errors`; the baseline measurements (§5). *Done when* a 200-row customer file with 5 bad rows previews with the 5
   listed, runs 195, reruns with 0 created, and the 5 fixed rows then import alone; a killed slice resumes with no
   duplicates; the single-command comparison and baseline numbers are in the as-built notes; criteria 2, 4 (CLI) and
   5 hold, and `import reverse` removes the 195 created and releases their keys (criterion 11, lists). *Tests:* §3 fixtures (BOM, cp1252, UTF-16, semicolons, TSV, quoted newlines, parentheses, trailing minus,
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
   `create_missing`; `payment` and `bill-payment` application by number; the `--key-by-content` guard. *Done when* 40
   invoices of 3 lines import keyed by number, a wrong-customer document is refused whole, a rerun creates none, and
   payments land on the right invoices. *Tests:* each kind against its typed command (criterion 5); closed period;
   duplicate numbers; a look-alike customer refused, for every actor; reversing a document import (deleted with
   preserved history, refused once a payment is applied, naming the payment).

   > **Changed by Kabe, 2026-10-28.** `approve`, the agent threshold and the "waits for a holder of `import.approve`"
   > check are dropped: Kabe ruled out size thresholds (sensitivity is impact, never quantity). Whether an actor may
   > import documents is a permission like any other, one model for every actor ([principal
   > directives](principal-directives.md), R187): the `import` capability beside each document's own command
   > capability. The behavioural rule lives in the agent guide instead: an import that sets how the business works
   > from then on gets one check of its exact details, from the preview, with the principal; a plainly named import
   > just runs. A look-alike customer is refused for every actor, not only agents. §8's size limits and K1 no longer
   > apply to imports; increments 6 and 7 revisit them for bulk edits and openings.
5. **Scale (M).** Row cap, slice and refusal defaults from the baseline; 10,000-row runs for a list and for
   invoices; memory bound; backup advice. *Done when* criterion 8 holds with numbers recorded. *Tests:* throughput
   against the looped command, kill and resume at three points, memory at 1,000 and 10,000 rows, a file one row over
   the cap.
6. **Bulk edit (L).** Migration 6; `bulk-edit` commands; column set per kind; operations; diff; version handling;
   `bulk-edit reverse` with its refusals and `--skip-changed`; the grid page and phone cards; `approve`. *Done when* criterion 7 holds in the browser and over MCP and
   20 items are repriced from a phone; criterion 11 holds for the repricing. *Tests:* rounding (half-up on the currency's places), merge and conflict
   rows, a refused row in the middle, reverse after a later edit, the grid at 300 and 2,000 rows (time recorded).

   > **Changed by Kabe, 2026-10-28.** `bulk-edit approve` and the agent threshold (§8's "a bulk-edit changing more
   > than 100 records ... stops in `waiting`") are dropped, as for document imports: sensitivity is impact, never
   > quantity, and whether an actor may bulk-edit is a permission like any other ([principal
   > directives](principal-directives.md), R187): the `bulk-edit` capability beside each record's own `update`
   > capability. In place of approval, the agent guide teaches the rule: an edit that sets how the business works
   > from then on (new terms or prices on many records) gets one clear check of its exact details, from the preview's
   > diff and totals, with the principal before it runs; a plainly named edit just runs. Neither `import.approve` nor
   > K1 applies to grid edits.
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
tie-out's given and derived lines, the bulk edit's audit trail and its reversal, and a wrongly mapped import undone in one step. One more run starts on a phone.

## 12. Open questions

Each default stands if nobody decides. K1 to K3 are Kabe's.

- **K1. Size limits. Recast by Kabe's R187 direction (2026-10-27).** The limits are sizes beyond which an actor needs
  `import.approve`, not an agent rule. *Default:* an actor without it creating more than 200 documents, or a bulk-edit changing
  more than 100 records, waits for a holder; an opening that does not tie needs it whatever the size; lists of any size and
  mappings never wait. Humans hold the capability at standard, so only agents feel the limit unless the principal withholds it.
  Settable by admin; reversal is the net. (A scheduled or watched-folder import would be an automation needing a new
  `automate.<kind>` capability under [principal directives](principal-directives.md) §3; it is outside this item.)
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
3. **Reverting an import.** *Changed 2026-10-27.* *Default:* every import run and grid edit is reversible as one unit
   through the shared batch mechanism (§8a), with the caller's own grants; the old default (`revert-plan` only) is
   dropped. Reversal is refused while a created record is in use or an edited record changed again; `--skip-changed` is
   opt-in for grid edits only.
4. **Negative document totals.** *Default:* an invoice or bill file with a negative total refuses, pointing to the
   credit memo or vendor credit kind.
5. **Renaming in the grid.** *Default:* allowed for names and account numbers; a rename onto an existing name is
   `E_NAME_TAKEN` for that row (merging stays `customer merge`).
6. **History import.** *Default:* past-dated documents import as ordinary dated documents under the closing date;
   opening balances come first and only later documents after them, and the opening page says so.
7. **Opening stock via the `item` kind.** *Default:* it creates the item with no quantity; stock comes through the
   opening step or an `inventory adjust` import.

## As built: increments 0–1

- **Migration co0108** (on co0107; first written as co0105 and renumbered at each merge of main) adds the batch
  ledger and the list-import tables in one step: `batches`, `batch_effects`, `import_runs`, `import_rows`,
  `import_keys` (indexed by batch and by record), and an index on `path` for each of the eight hierarchy lists. Guards: effects and row outcomes are immutable; nothing
  is ever deleted; a batch changes only `state` and its reversal columns, and never once `reversed`; a run only its
  state (and batch, version); a key only its release, once. A partial unique index keeps one standing key per
  `(source, key hash)`. One migration for both increments (not one each): fewer shared files to re-chain.
- **One write path, no second engine.** `dispatch._apply` calls `s.batch_recorder` (when a batch set one, and only
  with a company open) just before the company commit, after the command's audit event: an import row's key, its
  batch effect and its `run` outcome commit with the record or not at all (tested by failing the ledger write: the
  record, its audit event and its key all roll back). Rows run through the registry command nested in the session as
  `cutover` nests its steps, with source reference `import:<run>:<row>`; the importer grants nothing.
- **Reversal** (`company/batches.py`) plans first, whole (`--dry-run` returns the plan and a fingerprint the real
  call can carry). The plan takes one entry per record: the batch's first touch decides the inverse (a record it made
  is retired however often it then changed it) and the prior values are each field's value before the batch's first
  change to it, so a record one batch touched twice returns to its state before the batch. Blockers: a later live
  batch in the scope (`strict` ordering); a record a **later live batch** touched (`later_batch`, naming it: batches
  unwind newest first); a record **edited again** (its version is not the one its newest effect in any batch left,
  reversals included, so once the later batch is reversed the earlier one reverses too; who and when named); a
  created record **in use**, found by the database's own foreign keys (other than its own detail rows and records the
  same batch made) and by the references that have none (`SOFT_REFERENCES`: a journal line's or revision's
  `name_id`, memorized transactions' references, notes, attachment links, to-dos, a sales rep's name, an other name's
  conversion, a vendor's rate level, tax items on deposits and applications, accountant adjustments' accounts, merge
  history), naming the document where the referring row carries one (`invoice 1`); and the inverse command's own
  refusal from a dry run, permission included. A test scans the company schema and fails on any unkeyed `*_id`
  column that is neither a declared reference nor in `NOT_REFERENCES` with its reason. `E_BATCH_BLOCKED` carries up
  to 50 blockers, the total and what clears each; a blocked reversal changes nothing. Inverses: an updated record
  gets its prior values back through its ordinary update (selected by the kind's own input field, `customer_type`),
  carrying the version the plan read; the prior values cover every field the update changed, read from the
  record as shown before and as the update returned it, so rows it made on the side (a primary contact a phone made)
  go with it and `show` is as before. A **created list record is renamed `<name> (reversed <batch>)` and
  deactivated** (lists have no delete, and names are unique among inactive records too, so the rename is what frees
  the name for the corrected file; an account's number is cleared and a sales rep's initials take five characters
  of its own id); its keys are released in the deactivate's transaction. Each inverse command is an effect of a
  `reversal` batch, so a reversal killed anywhere (between slices, or between a record's rename and deactivate)
  resumes from the recorded steps (`state reversing`); tested at both points. The reversal batch and the batch's
  `reversing` state are written in one transaction that re-reads the batch, so a second reversal begun meanwhile is
  refused (`E_VERSION_CONFLICT`). **Probed once:** by the dry run whose fingerprint the real call carries, else by the
  first slice; a later slice re-checks only what can have changed (versions, uses, later batches). **Frozen across
  slices:** an edit after a slice blocks the rest with `E_BATCH_BLOCKED` saying how far it got and naming the record;
  an inverse refused while running stops the slice there the same way; what ran stays reversed, state `reversing`.
- **Atomic, decided:** commands commit one by one, so `--atomic` is kept by compensation: an atomic run starts only
  when its preview refused nothing, and the first refusal while running reverses what the run made through its batch
  (`rolled_back`). If that undo is refused (planned whole first, so it changed nothing) the run ends `failed`, its
  note names the blocker, and `import reverse` finishes the undo once it is cleared; a failed run never continues.
  Opening a run's batch and marking it `running` is one transaction.
- **Reader** `company/tabular.py`: every §3 fixture is a test (BOM, cp1252 flagged as guessed, UTF-16, semicolons,
  pasted tab text, quoted newlines, parentheses, trailing minus, decimal commas, ambiguous dates asked, leading zeros,
  spreadsheet serials refused, formula-safe error files), and it agrees with `statement_files` and `cutover_sources`
  on the shared money and date strings. Two-digit years pivot at the company's current year, as §3 says. UTF-16
  without a byte order mark is recognised by its zero bytes (flagged as guessed); a NUL anywhere else refuses the file
  with its line. The refused-rows file guards formula text only, as the report CSV does (a number, phone or date such
  as `-5` or `+1 415 555 0100` is written as it is), and the reader drops the guard's apostrophe, so the file imports
  again with its own values. A money cell with no number (`TBD`, `$`) refuses its row. Typed commands have no shared
  text validator, so NUL in typed input is left to each command (not changed here).
- **Kinds:** 17 list kinds (the §2 list plus job-type, item-category, customer-message, employee, other-name), each a
  declaration over its create and update commands; fields come from the command's own input model. References
  (`terms_id`, `parent_id`, an item's `*_account_id`...) are resolved from the file's text by the list's own selector
  keys (id, name, code, an account's number), because those inputs take ids; no fuzzy match, suggestions only.
  `NOT_IMPORTABLE` names every other company-only write with a reason (and `PLANNED` the §2 document kinds); a test
  fails on any command that is none of these. Modes `create`, `update` and `upsert` (update effects keep the prior
  values, input-shaped, so the update's reversal restores them exactly).
- **Permissions:** layer `import-v1` on `delivery-queue-v1` (re-chained at each merge of main) adds the capability `import` (member reads, standard writes; held
  by every role at member and by standard, admin and owner at standard). No existing default changes. `import.approve`
  (R187) is not needed yet: list imports never wait; it arrives with document imports (increment 4). Decided: `batch
  show` and `query` sit under `import` too, until bank feeds adds its own door. Holding `import` reveals nothing the
  caller could not read directly: `import preview`, `show`, `rows`, `errors` and `reverse` need the kind's own read
  permission (`customer show`'s), a preview of an attachment needs `attachment` read, and `batch show` the read
  permission of every kind of record the batch touched; `batch query` lists such a batch with its first blocker
  withheld (`E_PERMISSION`).
- **Not built here** (later increments): saved mappings and `decide` (unknown references are listed with
  suggestions; a run refuses them row by row), the browser pages, documents, the scale caps.

**Baseline measurements** (`tools/measure_imports.py`, one process per size, the root NVMe, a fresh company on the
general chart, `customer` rows with email, phone, terms, credit limit, city and postal code; seconds, measured
2026-10-27 on this branch):

| Rows | Parse and guess | Preview | Run | Reverse dry run | Reverse | Loop of `customer create` | Peak RSS |
|---|---|---|---|---|---|---|---|
| 1,000 | 0.016 | 7.42 (7.4 ms/row) | 18.63 (18.6 ms/row) | 21.09 | 79.18 (79.2 ms/row) | 57.72 (57.7 ms/row) | 293 MB |
| 10,000 | 0.042 | 74.01 (7.4 ms/row) | 256.18 (25.6 ms/row) | 405.85 | 1,177.21 (117.7 ms/row) | 861.03 (86.1 ms/row) | 340 MB |

The single-command baseline is `client.run('customer create', ...)` looped in one process, which opens a session per
call; the import's run nests the same command in one session, so it runs at about a third of the loop's time per row
(run over loop: 0.32 at 1,000 rows, 0.30 at 10,000). Per-row cost grows with the list for the run, the loop and the
reversal alike (the commands' own name checks on a larger table); the reversal's dry-run probes grow fastest (21 s to
406 s, 1,000 to 10,000), the first thing for increment 5 to look at. Peak memory rose 16 percent from 1,000 to 10,000
rows in a process that also holds the baseline loop; criterion 8's 10 percent is increment 5's to measure alone. A
reversal costs about four commands per created record (two dry-run probes in
the plan, then the rename and the deactivate) plus the whole-plan check. `invoice post` is measured with the
document kinds (increment 4); slice, refusal-stop and row-cap defaults stay as §5 gives them until increment 5.

**After the review fixes** (same tool and machine, 2026-10-27; `import run` and `import reverse` each called again
until done at their default slice, 200 records per reversal call, the real reversal carrying the dry run's
fingerprint; the single-command loop not rerun):

| Rows | Parse and guess | Preview | Run | Reverse dry run | Reverse (calls) | Reverse over run | Peak RSS |
|---|---|---|---|---|---|---|---|
| 1,000 | 0.017 | 7.67 (7.7 ms/row) | 21.39 (21.4 ms/row) | 20.33 (20.3 ms/row) | 53.88 (5; 53.9 ms/row) | 2.52 | 289 MB |
| 10,000 | 0.052 | 84.78 (8.5 ms/row) | 228.99 (22.9 ms/row) | 205.27 (20.5 ms/row) | 514.57 (50; 51.5 ms/row) | 2.25 | 326 MB |

The reversal is now linear (51 to 54 ms per record at both sizes, where the default slice made it about 176 s at
1,000 rows and hours at 10,000) and costs 2.3 to 2.5 times the run: two commands per created record (the rename and
the deactivate) against the run's one, with the whole-plan probe paid once, by the dry run. The dry run itself is
linear too (20 ms per record; it was 21 to 41), its subtree reads now a range on the indexed `path`.

## As built: increment 2 and Kabe's rules

- **`import check`** (`imports.check_report`, a member read under `import`, the kind's own read permission) reads the
  file once and tests every cell against its field without running a command: the mapping is complete (every field the
  create command requires has a column or a constant), each cell fits its field's type, and, for text, the length,
  pattern and range the command's input model declares; keys repeated within the file; and every reference, looked up
  in one query per list held in memory (`Books`). It returns every problem at once, grouped by column, then cell text,
  with row numbers and the exact cell (`summary`: "Credit Limit, rows 2, 4: not an amount: '$599.♎56'"), the dropped
  characters, and the unknown references (with suggestions, look-alikes and whether an import may create them).
  `preview` starts with the same check and, all or nothing, refuses the whole file (`E_VALIDATION`, the check in
  `details`) before any command runs and before any run is stored; an incomplete mapping is refused even with
  `partial`. An all-or-nothing **run** starts with the half of the check the books can change since the preview (every
  reference still found or decided) and refuses before writing if one is gone; the file's half cannot change, the run
  holds what was previewed. Lists of 1,000 groups, dropped characters and references are shown in full beyond which
  the totals still count every one.
- **Codes.** A cell that the command's own input model would refuse (too long, too short, pattern, range, a required
  field blank) carries the command's code, `E_VALIDATION`, so importing and typing still fail alike (criterion 5); the
  reader's own codes (`bad_value`, `E_AMOUNT_PRECISION`, `ambiguous_date`, `no_key`, `duplicate_in_file`, `row_width`)
  stay. A row with any problem never runs: its mapped input is not stored.
- **All or nothing by default.** `atomic` gave way to `partial` (default false; `import_runs.atomic` stores its
  inverse, so no column changed). With `partial`, each bad row is refused alone, as increment 1 did.
- **Text keeps its words.** `tabular.clean_text` drops what no text field holds: control characters (C0, DEL, C1), lone
  surrogates and U+FFFE/U+FFFF; tab and line breaks are kept only in long text (a field with no length limit or 1,000
  characters or more: notes, descriptions) and dropped elsewhere. Each dropped character is reported with row, line,
  column, 1-based position in the cell and its name; the preview and `import show` carry them (`dropped`). A NUL in a
  cell no longer refuses the file; a file that is not text still does (more than 8 control characters and over 10
  percent of the first 64 KB, or a PDF, image, old `.xls` or other archive signature). Headers lose control characters
  silently. Accents, scripts, symbols and emoji are kept as written. A field ending `_id` (a sales rep's `name_id`) is
  treated as the reference it is.
- **Junk is refused.** A control character in a number, date, choice or reference refuses its row (`bad_value`), as a
  stray character in an amount already did; junk no longer sways the column's detected number format.
- **Empty is allowed unless truly needed.** `import_kinds.required(kind)` is the create command's own required inputs,
  read from its model; a test asserts it equals the command's for all 17 kinds. Blank cells and absent columns are
  omitted, so a field takes its hand-entry default; only in `create` mode is a blank required cell a problem.
- **Saved mappings (co0110,** on co0109, delivery batches; `import_sources`, append-only `import_mappings`, immutable `import_decisions`;
  guards refuse deletes and any change but a source's active flag and forward version). Fingerprint: SHA-256 of the
  sorted set of normalized headers. A preview with no `mapping` and no `columns` uses the single exact match among the
  kind's active sources (narrowed by `source` when given; the mapping's source becomes the run's, so keys join last
  month's); two exact matches, or a probable one (every mapped header present, Jaccard overlap at least 0.8), become a
  `mapping` decision with the diff, and the preview is refused or its run waits until `mapping NAME` is passed. A
  saved mapping maps only its own columns: new headers stay unmapped and are listed. `mapping NAME` refuses a file
  lacking any column it maps. `import-mapping save --from-run` saves a new version (or nothing, when the recipe equals
  the latest), keeping the value maps the source already holds; `show`, `history`, `query`, `match`, `deactivate`
  complete the set. Decided: no `event_id` column on mappings (the save's audit event names the mapping), and the run's
  own record of which version it used lives in its stored mapping (`saved`, `matched`), not a new column.
- **Decisions.** An unknown reference asks: the preview stores the run as `waiting`, with each unknown value once
  (field, column, rows, suggestions: look-alikes by `cutover.alike_names` first, then edit distance). `import decide`
  maps (`"Net 30 days=Net 30"`, or `"Terms:..."` when two columns share the text; the target must exist), creates
  (dry-run first; never an account or item; a customer or vendor only when nothing looks like it; a create needing more
  than a name takes the rest in `create_input`, as a term's `kind`), or skips (the rows are `skipped_by_decision` and go in
  the error file). Each value is decided once. The created record is made through its own create command when the
  first row needing it runs, as an effect of the run's batch, so the run's reversal retires it too (the batch's in-use
  check now passes over any record the same batch made). `remember` adds the maps to the source's value maps as a new
  version; value maps apply to the cell before it is read. Decided: unknown references are decisions, not check
  problems: the check reports them, the preview waits, and nothing undecided can land.
- **`.xlsx`** (`company/xlsx.py`): a zip attachment is read with openpyxl in read-only mode with `data_only` (saved
  values, never formulas), `keep_vba` and `keep_links` off; the first sheet or `sheet`. Cells become the text the CSV
  reader expects (plain decimals, ISO dates, true/false). A formula with no saved value refuses the file naming its
  cells; an encrypted workbook, a non-xlsx zip, or one unpacking past 256 MB or 200:1 per large member is refused.
  **Vetting** (2026-10-27): openpyxl 3.1.5, MIT, pure Python, the reader pandas uses for .xlsx; last release
  2024-06-28 (a slow but steady cadence; mature rather than abandoned). Known vulnerabilities (OSV): one, an XML
  external entity issue fixed in 2.4.2 (2017). It evaluates no formula and runs no macro; external links are not read
  with `keep_links=False`. Its own documentation says it does not guard against entity-expansion attacks unless
  defusedxml is installed, so defusedxml 0.7.1 (PSF licence, no known vulnerabilities) is a dependency too and the
  reader refuses to run if openpyxl is not using it. Dependency et-xmlfile 2.0.0 (MIT, no known vulnerabilities).
  Pinned exactly in `pyproject.toml` and `uv.lock` (`uv add --no-sync`).
- **Permissions:** the eight commands are layer `import-mapping-v1` on `delivery-batch-v1` (814 commands, descriptor
  66a2d6a6; `import-v1` stays as merged, 797, 0db21ee9); `import check` and the mapping reads at member, `import
  decide`, `import-mapping save` and `deactivate` at standard. The layer re-declares `batches.require_read` with the
  `show` of every list an import's rows refer to (adding sales tax codes and units of measure).

**Check timings** (`tools/measure_imports.py ROWS --check`, root NVMe, the customer file of the baseline; seconds,
2026-10-27): 1,000 rows 0.18 (a file with a junk amount in every tenth row: 0.12, 100 problems); 10,000 rows 1.35
(junk: 0.87, 1,000 problems). Against the preview's 7.7 s and 85 s, the check is about 1/50 of it; the target was a
few seconds at 10,000.

### As built: the review of increment 2 (`notes/reviews/r107-inc2-review.md`)

Each finding has a test that failed before its fix (`test_import_rules.py`, `test_import_mappings.py`,
`test_tabular.py`, from the review's probes).

- **Amounts are exact or refused** (High 1). `tabular._number_parts` reads money and plain numbers alike: a sign,
  parentheses, one currency symbol or three-letter code (money) and `%` (numbers) only at the edges; inside, digits,
  one decimal mark and thousands marks only in groups of three. `12,50` and `1,5` with `.` the decimal mark, `1$5`,
  `1 234` (unless the thousands mark is a space) and `$5$` are `bad_value`, never 1250, 15 or 1234. Decided: the
  older readers (`statement_files`, `cutover_sources`) keep reading `12,50` as 1250; they read report exports a
  program grouped, and the agreement test still holds on every amount all three accept.
- **Only words lose characters** (High 2). `imports.free_text` decides: a name, address, note or description is words
  (a name even as the key: Kabe's own example is a name); a number, date, choice, reference, `_id`, a patterned field,
  an email, phone, fax, postal code, currency, initials, barcode, `*_number`, and any mapped key other than the name are
  identifiers, where any control character (a tab or line break inside too) refuses the row (`bad_value`). In a
  single-line text field a tab or line break becomes one space (CR LF is one break), reported with
  `replaced_with: SPACE`; long text keeps them.
- **The check refuses what the run would** (High 3). `check_rows` runs each row's input through its command's own
  `input_model` (`validate_input`), references replaced by a placeholder id, so model validators and requirements
  that depend on another field are met in the check (standard terms need `due_days`, `fixed_percent` needs `percent`,
  `discount_percent` 0 to 100); then what the create command refuses from its input alone (`_input_rules`: a list name
  with a colon; an item's fields by type, through `items._determined_accounts`, `_service_defaults` and
  `_validate_profile`). A row that will update is checked as the update's input; in upsert, a row that would create
  as the create's, so its required fields are enforced. An amount beyond signed 64-bit minor units is
  `E_VALUE_RANGE`. A reference to an inactive record is `E_INACTIVE_REFERENCE` unless the update leaves it as it is.
  A parent named later in the file is the file's own (`file_names`): the preview shows the row `create`, and the run
  makes the parent's row first, then the child (`run_slice`'s `process`, depth-bounded); a row naming itself as its
  parent is refused. `ok` is true only when the file can run as it is: `clean` (no cell problem) and nothing in
  `to_decide` (a decimal mark, a date order, a saved mapping, each unknown reference). The preview still refuses only
  on problems; decisions make it wait. A property test feeds generated rows of tricky cells: whenever the check is
  clean, the preview refuses no row and the run lands it.
- **.xlsx read once, bounded** (High 4). openpyxl's reader runs without its worksheet step (which scans each sheet
  whole for a `<dimension>`); the chosen sheet is parsed once by openpyxl's own `WorkSheetParser` on saved values,
  from the cells it holds: the declared dimension is ignored, empty rows are not padded, a value past column 1,024
  refuses (`too_wide`), and rows are capped at `tabular.MAX_ROWS` (20,000, §5's starting per-run cap), which now caps
  a text file too (`too_many_rows`). A formula with no saved value (array formulas too) refuses (`unsaved_formula`),
  and so does a saved error such as `#REF!` (`error_value`); damaged XML and a refused entity are "the workbook is
  damaged" (`not_xlsx`). Merged cells read as their first cell; the rest of the merge is blank (read-only mode).
  Measured (2026-10-28, root NVMe, other builders' tests running): the review's 4.9 KB file (declared `A1:XFD2000`, a
  value at XFD2000) is refused in under 0.01 s, and with its far value in column B instead it reads its 3 rows in
  under 0.01 s (was 14.3 s); a 200,000-row, 3-column workbook (12.5 MB zip, no `<dimension>`) is refused at the cap
  after 1.7 s with peak memory 50 MB (was 75 s and 190 MB to read it all); 20,000 rows read in 1.7 s, 42 MB.
- **All or nothing is total** (Medium 5). In an atomic run a `differs` row (create mode) and a `skipped` row (update
  mode, no match) are refused unless the caller named the outcome in `allow` (`["differs"]`, `["skipped"]`); the
  preview shows them refused and the run will not start. `import cancel` of an atomic run with a live batch reverses
  what it made, any reference a decision created included, then ends `cancelled`.
- **`remember` needs a named source** (Medium 6). A run keyed under the shared default (`customer import`) refuses
  `remember`, and `import-mapping save` refuses that name.
- **`PK` is a header, not an archive** (Medium 7): only `PK\x03\x04` (read as a workbook, which refuses a zip without a
  zip's structure) and the empty or spanned zip signatures are archives.
- **Lows.** `import-mapping show`, `history` and `deactivate` need the source's kind's read permission, and `query`
  lists only sources of readable kinds; `import check` offers suggestions and look-alikes only from lists the caller
  may read. Constants and a saved mapping's defaults are read and checked as cells are (a problem of the mapping, said
  once). A remembered decision keys on the file's own text (`file_value`), before any value map changed it.
- **Check timings, after these fixes** (`tools/measure_imports.py ROWS --check`, 2026-10-28, under the same load): the
  per-row model check costs about a third more: 1,000 rows 0.24 s (junk file 0.19 s); 10,000 rows 1.87 s (junk
  1.49 s), still within the target of a few seconds.
- **Permissions**: the eight commands of increment 2 move from `import-v1` (restored as merged) to
  `import-mapping-v1` (above). The migration is co0110, on main's co0109 (delivery batches).

## As built: increment 3

- **One page, `/c/<company>/import`** (`adapters/workbench/import_pages.py`, `templates/import.html`, `static/import.css`
  and `import.js`), reached from the Company menu ("Import records from a file") and an **Import…** link on each of the
  17 list kinds' list pages. Steps: **File** (kind; choose, drop or paste; a pasted table shows at once as a table, in
  the browser, before anything is posted; a template download per kind), **Check** (counts bar; every problem at once,
  grouped by column in the file's order, with row numbers and the exact cell; the columns as rows at 1440 and as cards
  at 390, each with three samples and a field picker; the formats, saved mapping and unknown references to decide),
  **Preview** (counts per outcome, refused rows, a sample of the exact records, and one confirmation naming the count:
  "Import 4 records"), **Run** (a progress card), **Done** (counts, refused rows, "Save this mapping as", Undo). At
  390 px the steps are a vertical stepper and nothing scrolls sideways (asserted at every step).
- **Only the registry's commands.** The page runs `import kinds`, `template`, `check`, `preview`, `decide`, `run`,
  `cancel`, `reverse`, `errors`, `show` and `import-mapping save`; no migration, no new permission layer, and the
  page's writes need the grants the commands need. Decided: the check's answers are given beside the problems, and
  the Preview button runs `import preview` then, if the run waits, `import decide` with the page's answers in the same
  post (decisions need a stored run, so they cannot be made before a preview). A term is created as standard terms
  due in N days (the page asks N); a list whose create needs more than a name is offered map or skip only.
- **The file is an attachment.** A chosen file or a pasted table is kept as an attachment on the company
  (`attachment add company_info`, through the same published transfer the HTTP route uses) and the import reads it by
  id through `tabular.read`: text in any encoding the reader detects (the cp1252 note shows), or `.xlsx`. Uploads over
  16 MB (the commands' own `content` limit) are refused before anything is stored. Corrected files are uploaded on the
  Check step; the columns chosen by hand carry over (one the new file lacks is dropped).
- **htmx for every post**, multipart where a file goes; refusals answer 200 with the error in place on the step they
  came from (a file that was chosen stays chosen). Each answer that names a run pushes `?run=<id>`, so a reload, or
  opening the page again after a lock screen, lands on the run's step; a `running` run then continues by itself (one
  slice of 100 rows or 4 seconds per request, `hx-trigger="load"`), and Pause stops asking. Nothing imports while the
  page is closed.
- **All or nothing stays the default.** A file with problems cannot preview; "Import the other N rows only" is its own
  button (`partial`), and its refused rows download from the Preview and Done steps as `import errors`' CSV (UTF-8
  with a byte order mark so a spreadsheet reads accents; its formula guard unchanged). Downloads are plain GET links
  (reads need no workbench header).
- **Undo** plans first (`import reverse` dry run): "Undo reverses all 5 records it made or changed", then one
  confirmation; a blocked plan lists each blocker with what clears it, and nothing changes. A long reversal continues
  by slices as the run does.
- **`import check` gains `samples`**: up to three distinct non-blank values per column, as the file has them (cut at
  60 characters), for the Columns step. `docs/cli/import.md` regenerated.
- **Tests:** `tests/test_import_pages_browser.py` (real Chrome, 1440 and 390): criterion 1's customer file
  (Windows-1252, semicolons, renamed headers, a day-first date on a job row, parenthesized negatives with decimal
  commas, two unknown terms) uploaded with a junk amount and an unmapped name; the check lists both; the columns are
  mapped on the page; the corrected file is uploaded; 'Net 30 days' is mapped and 'Due in 60' created; the formula row
  is left out by the partial button; the preview's counts and sample; Import; the refused row downloaded by a real
  click (its `=1+2` written `'=1+2`); the mapping saved; Undo confirmed and every record inactive. A pasted vendor
  table imports the same way, and a run left after one row resumes when its page is opened again. Screens:
  `/tmp/m4-shots/import3/`.
- **The review of increment 3** (`notes/reviews/r107-inc3-review.md`), each with a test that failed before:
  - *A true confirmation.* `import show` (and so preview and decide) gains `outlook`: what a run would land with the
    answers given so far, row by row: `create`, `update`, `answered` (waiting rows whose references are all mapped or
    created), `land` (the three), `left_out` (a skip), `waiting`, `unchanged`, `differs`, `missing`, `refused`. The
    counts bar, the heading and the button use it: "Import 4 vendors (2 left out as you chose)?".
  - *No dead ends.* `import check` applies `customer create`'s job rule (job fields and inherited address or contacts
    need a parent), so check and run agree. What still reaches Preview refused (a record that `differs`, say) is
    listed with its reason and the same two ways on as Check: the corrected file and Check again, or "Import the
    other N rows only" (a partial preview of the same file, mapping, formats and answers). No disabled button.
  - *By hand.* "Map the columns below by hand" sends every column as shown, so no saved mapping is asked about again;
    an empty choice is sent as "not imported" and kept on every later check.
  - *Cancel and Undo.* A stopped, paused, cancelled or failed run that made records offers Undo (with who ran it).
    Cancel says what happens first: a partial run's records stay until undone; an all-or-nothing run's are undone.
  - *Answers kept.* `import decide` gains `answers`, each naming its field and the exact text (no "=" or "Column:"
    parsing); a refusal names the reference (`details.ref`). A wrong answer cancels the run made to take it and shows
    Check again with every answer as given and the refusal beside it. `import check` gains `choices`: the active
    records of each list the caller may read (up to 2000), the "Use" picker's options. A typed target is a read of
    its list. `remember` with only creates and skips remembers nothing and is not refused.
  - *Due days* for a new term come from its words: the first number ("Net45" 45), 0 for receipt or COD, else 30.
  - *Import files are not attachments.* Chosen here: `attachment add` with `record_type: import_file` (linked to the
    company, captioned with the kind, never `attachment link`ed), no migration. `attachment get`, `text` and `list`
    show such a file only to a caller who may read its kind. Adding one unlinks the same person's earlier file of the
    kind and anyone's older than 7 days, unless a run landed records from it (or is running); a file the check refuses
    is unlinked at once. Unlinked bodies go with `attachment collect`.
  - *Stale confirm.* A run that lands nothing ends "Nothing new to import: all rows were already in", without Save
    mapping or Undo.
  - *Lows.* A post over 18 MB is refused from its Content-Length, and the body is read no further than that; a form
    over 4,000 fields (a file over about 1,000 columns wide) is refused on the page. At 390 px every link and choice is at
    least 44 px tall, problem groups collapse with counts once there are over 12, the questions come before the column
    cards, and the partial choice is set apart. A reload keeps the preview's sample (from its stored rows).
    "E-mail address", "Client" and "Job started" are guessed (a customer's aliases sit beside the party ones in
    `import_kinds.py`).

## As built: increment 4

- **Document kinds** (`import_kinds.DOC_KINDS`, `company/import_documents.py`): `invoice`, `credit-memo`,
  `sales-receipt`, `estimate`, `sales-order`, `bill`, `vendor-credit`, `purchase-order`, `journal`, `check`,
  `card-charge`, `payment`, `bill-payment` and `inventory-adjustment` (`PLANNED` is empty). Each is a declaration over
  its command (`invoice post`, `payment receive`, `bill pay`...); fields are the command's own input paths, a line's
  written `lines[].item` (`expenses[].account`, `applications[].invoice`, `bills[].bill`). Money, quantity, date and
  reference fields are typed by their leaf; a line's unit price or rate keeps sub-cent places as typed. Documents are
  create-only: `mode` update or upsert is refused, naming the document's own update command.
- **Group key, header, lines.** Rows sharing the key (an invoice's number; a bill's or vendor credit's vendor and
  reference; a check's account and number; a payment's customer, date, amount and reference; a bill payment's date,
  account and check number or reference) are one document. Header fields come from the first row that fills each; a
  later row filling one differently refuses the whole document (`header_conflict`, row and both values named). Each
  row filling any line field is a line, in file order. A row whose key cells are all blank continues the document
  above (the number written on its first row only); on the first row it is `no_key`. With no key mapped (a card
  charge has none) each row is its own document keyed by content, and `import run` refuses without `key_by_content`.
  The run counts a document once; `import_rows.spans` (**co0113**, on co0112) keeps its file rows, so problems,
  refusals and the error file name and write every row of a refused document with its own line.
- **The check catches what the run would refuse** without running the command: every cell as before; the required
  header fields and "no line" (`no_lines`); each document's input through the command's own input model, a model error
  on `lines.2.item` reported on the file row of that line; inactive references; a number the books already use
  (`E_DUPLICATE_NUMBER`, the series the command checks, an invoice's shared with credit memos; a deleted document
  keeps its number and the message says so); a journal that does not balance (exact integers); a payment's invoices
  (found by number, the payment's customer's, not voided or deleted) and applied amounts that exceed it or are
  missing when it applies to more than one invoice; a bill payment's bills by number. Unknown customers, vendors,
  items and accounts are the decisions of increment 2 (named, never made), for header and line references alike.
- **Reference policy and `create_missing`.** `import preview` takes `create_missing`: each unknown reference an import
  may make from its name alone is decided `create` at the preview and made by its own command when its first row
  runs, as an effect of the run's batch. A customer or vendor that looks like an existing one is never made, by any
  actor (it stays to decide, and the preview's notes say why); `import decide --create` refuses it too. Items and
  accounts are never made; terms, which need more than a name, still ask.
- **Applied by number.** A payment row's `applications[].invoice` is the invoice number; the run sends the invoice's id
  and current version (`expected_version`, read as the row runs) and, for a payment applied to one invoice with no
  amount, the whole payment. Its `operation_key` is `import-<run>-<row>`, so a reversed and re-imported payment is
  new. A bill payment's bills are found by number the same way; `bill pay` may write one payment per vendor, and each
  is an effect of the batch (the key names the first).
- **Same as typing** (criterion 5): the document reaches its command as a person would type it, so totals, lines,
  postings and error codes are the command's own; `tests/test_import_documents.py` compares the shown document of
  every kind imported and typed, an invoice with sub-cent rates rounded both ways, a closed period's and a used
  number's codes, and a caller denied `ledger.post` refused each document with the typed `E_PERMISSION`.
- **Reversal** (`batches.DocumentRecord`): a created document is retired by its family's delete, history preserved
  (`invoice`, `credit-memo`, `sales-receipt`, `bill`, `journal`, `check`, `card-charge`, `payment delete`), else its
  void (`vendor-credit`, `purchase-order`, `estimate`, `sales-order`, `bill payment`, `inventory void`), under the
  caller's own explicit delete grant. A payment the batch made is unapplied first (`payment unapply`), then deleted;
  its later step's grant is checked in the plan. A document is **in use** while a standing payment or credit is applied
  to it (`applications`, `ap_applications`, not reversed, the payer not voided), and the blocker names the payment
  (`invoice INV-1 is in use: payment 1 refers to it`) unless the same batch made it. **Edited again** for a document is
  a revision after its first (a settlement changes its version, not its revisions). The released keys let the same
  file preview as new, and the reversal retires each document's number first (*Number reuse after reversal* below),
  so the corrected file imports again with the same numbers. A bill payment the batch made is unapplied (`bill
  payment unapply`) and then voided, as a bank import's
  reversal does; a purchase order is read from `purchase_orders` and its revisions from `purchase_order_revisions`.
  An all-or-nothing undo (a refusal mid-run, or a cancel) runs its nested deletes under the run's stored reason, so
  a run called without one still lands nothing.
- **Kabe's 2026-10-28 ruling** replaces `approve` and the agent threshold (§11, increment 4): no size threshold, no
  `import.approve` wait; the rule for an import that sets how the business works is in the agent guide ("Importing
  invoices, bills or payments from a file"). Decided: an invoice line with no item is refused by `invoice post`
  itself (its line requires an item), so the import refuses it too, never stricter or looser than typing; a constant
  (`constants {"lines[].item": "Services"}`) fills blank items when the person wants description-and-amount lines.
- **Permissions:** layer `import-documents-v1` on `bank-feed-reverse-v1` (828 commands, descriptor 6418e208, re-chained at the merge of main 5de700e0; `require_read` pinned at batches.py 381/383) adds no command,
  capability or default; it re-declares `batches.require_read` with the document kinds' `show` reads (`ledger.read`,
  `customer-work` at member). No new write command, so the importable table is unchanged apart from the 14 commands
  leaving `PLANNED`.
- **The review of increment 4** (`notes/reviews/r107-inc4-review.md`; each reproduced probe is a test in
  `tests/test_import_documents.py`, failing before the fix):
  - *The check catches what the run would refuse*, now also: a date on or before the closing date (posting kinds;
    estimates, sales orders and purchase orders post nothing), an unknown line or customer tax code (a reference to
    the sales tax code list, so it is a decision like any unknown name), a cheque payee no name list holds, what the
    whole file's payments apply to an invoice against its open balance (`E_APPLICATION_CAPACITY`; two payments of one
    file that together overpay it are caught, not only each alone), a cheque number already on that bank account
    with leading zeros ignored (`check_numbers.canonical`, the command's own key) or on another cheque of the file,
    and a number on two documents of the file (a bill keyed by vendor and reference).
  - *Payments group only by an explicit key.* Decided: a mapped payment number or reference (with the customer)
    makes rows one payment; a row naming no reference under a payment that has one continues it when its customer,
    date and amount are blank or the same (the reference written on its first row only); later rows leaving the
    customer, date and amount blank continue the payment above. Two rows with the same customer, date and amount and
    no reference are ambiguous (one payment over two invoices, or two equal payments) and both are refused in the
    check (`ambiguous_payment`: map a reference column, or put one payment per row with its later rows' key cells
    blank); they are never merged.
  - *`create_missing` look-alikes.* New names are compared with each other too: two look-alikes in one file are both
    held, neither made. The look-alike test (`imports.alike`) widens `cutover.alike_names` with word order
    (`Smith, Bob` / `Bob Smith`) and small misspellings: plural endings off and words sorted, an edit distance of at
    most 1 in a name of 4 to 7 letters and at most 2 from 8 letters on (`Acme Supply` / `Acme Suplies`); numbers
    must match exactly. A caller who may not read the customer or vendor list cannot rule a look-alike out, so the
    preview makes nothing for them (a note says why) and `import decide --create` refuses with `E_PERMISSION`.
  - *Rows.* A document's rows must be together: decided, a key repeated after another document refuses that
    document whole (`key_apart`: sort the file by its key columns), rather than gathering it with a warning, since
    gathering guesses which rows belong where. A blank-key row under a row refused for its width continues that
    refused row, never the document before it. A sales line with Rate and Amount columns sends the rate only (the
    command refuses both) and the amount must equal quantity times rate as the command extends it (half to even,
    once) exactly (`amount_mismatch`); with no Rate column, Amount is the line amount. A journal with a Debit and a
    Credit column maps (`lines[].debit`, `lines[].credit` become the line's side and amount). An estimate line's
    estimated unit cost is money (sub-cent, as typed) and its markup percent a decimal.
  - *Number reuse after reversal* (Navigator, decided 2026-10-28; no migration). Reversing an import retires each
    document's number before removing it, so the corrected file imports again with the same numbers and the books
    equal one import (`test_every_kind_imports_reverses_to_the_exact_prior_state_and_the_same_file_imports_again`,
    all 14 kinds). The retired form is `<number>~R<batch>`: the original (cut to fit 64 characters), `~R` and the
    import batch id's last 6 characters upper-cased, taking 10 or the whole id if a row of that table already holds
    it, so it never collides with a real number; the columns have no pattern beyond non-blank and 64 characters.
    Where the family's update takes a number (`invoice`, `credit-memo`, `sales-receipt`, `estimate`, `sales-order`,
    `bill`, `vendor-credit`, `purchase-order`, `journal`, `check`, `payment`), a `renumber` step runs that update
    (`<noun> update`, a revision and an audit event of its own, `source_ref reverse:<batch>` under the reversal's
    reason; a payment's after its unapply, with operation key `retire-<id>`), then the delete or void. A check's
    number in the file is its cheque number, so its update renumbers the cheque (`check_instruments`) and the
    journal-series document number stays. The probe dry-runs the renumber and the retiring command against the
    standing document, so a refusal (a closed period, a missing grant: the update needs `ledger.post`, or
    `customer-work` for estimates and sales orders) blocks before anything changes. A reversal that stopped after
    renumbering goes on from there: the renumber is not repeated, and that one revision is not an `edited_again`.
    Kinds with no such update (`bill payment void`, `inventory void`, `card-charge delete`) renumber inside the
    retiring command's own transaction (`batches._retire`, through dispatch's `batch_before` hook, which then plans
    in that write), with a bill payment's cheque number on its instrument; there the original stays on the revision
    and in the batch effect, not in a revision of its own. The reversal's batch effect for the step carries
    `number` and `retired_as` (`batch show <reversal>`).
    Where the original shows: the document's first revision (`invoice history --include-deleted`, the
    `transaction_revisions` rows), the renumbering's audit event (`audit list --record-id <id> --command
    "invoice update"`) and the batch effect. `<noun> show --include-deleted` and `transaction find` (voided kinds
    only; it leaves deleted ones out) show the retired number, whose `~R` marks it removed by an import reversal
    (`test_a_reversed_import_retires_its_numbers_where_history_find_and_audit_say_so_and_a_typed_delete_keeps_it`).
    Lookups by number across kinds (`sales.resolve` with several types, `money_out.resolve`,
    `prepayments.resolve_document`) prefer the standing document (`document_numbers.prefer_live`), so a voided one
    of another kind never makes it ambiguous; within one kind the unique constraints allow one row per number.
    *Known limit against the anchor:* a document a person deletes or voids keeps its number, as before; the anchor
    frees a deleted document's number. The check still says so for those (`a deleted document keeps its number`).
- **Seeded check** (`test_seeded_document_imports_payments_and_reversals_keep_the_invariants`, seeds 5, 41, 107, 20
  steps each): invoice files from two sources, reruns, partial payments to random open invoices and reversals of
  random live batches; after every step the books balance (Python integers over posting lines), every invoice's due
  equals the model (payments landed on the right invoices), receivables equal the dues, a rerun creates nothing, a
  reversal blocked by an applied payment changes nothing, and undoing the last change restores every account and
  due exactly.

**Timings** (`tools/measure_document_imports.py INVOICES`, root NVMe, 3-line invoices keyed by number, other builders'
tests running; seconds, 2026-10-28):

| Invoices (rows) | Check | Preview | Run | Reverse dry run | Reverse | Loop of `invoice post` | Run over loop | Peak RSS |
|---|---|---|---|---|---|---|---|---|
| 40 (120) | 0.08 | 2.21 | 6.24 | 2.29 | 5.18 | 7.66 | 0.81 | 311 MB |
| 1,000 (3,000) | 0.55 | 54.43 | 187.69 | 57.10 | 148.78 | 236.35 | 0.79 | 310 MB |

The run costs about 0.19 s per invoice at both sizes (a fifth less than looping the command, which opens a session
per call), the preview about 0.055 s per invoice, and the reversal about 0.15 s per invoice (one delete each).

## As built: increment 6

- **Migration co0116** (on co0115; numbered co0115 until item pictures took that number at the merge): `bulk_edit_runs` (one grid edit: the selection, what was asked, its label, the
  count of records it leaves as they are; only state, version and the batch, once set, change) and `bulk_edit_rows`
  (its diff, one row per record changed or refused, with field, before and after, input-shaped and as shown, the
  version the preview read, and the apply outcome written once: `updated`, `merged`, `conflict`, `refused`,
  `not_run`). Nothing is deleted. `bulk_edit_rows.record_id` is ledger history (`NOT_REFERENCES`), never a use of
  the record.
- **Commands** (`company/bulk_edit.py`, `commands/bulk_edit_cmds.py`): `bulk-edit columns`, `grid`, `show`, `query`
  (member reads) and `preview`, `apply`, `reverse` (standard writes), under the new capability `bulk-edit` (layer
  `bulk-edit-v1`, re-chained at the merges onto `item-picture-v1`: 845 commands, descriptor c4664e87), each record checked again by its own
  `update` and `show`. No `approve`, no threshold (Kabe's 2026-10-28 ruling above).
- **Columns** are the update command's scalar inputs that its `show` also gives, so a reversal can restore each
  exactly: never nested records (addresses, contacts, members, vendor profiles, site settings), never an account's
  `type`; custom fields as `custom:<name>`, by their definition. Decided: an address's lines are not columns here
  (an update of part of a nested address would need its own restoring rule); `cycle_class` is left out because
  `item show` does not give it back.
- **Selection** is the list's own `query` (its filters and search text, paged), or named records (id, full name, an
  account's number), at most 5,000. **Operations** expand to values before anything is stored: cell edits first,
  then a paste (by header, a Name column picking the record; or by position over the columns shown), then column
  operations in order (`set`, `clear`, `copy_down`, `replace`, `add`, `multiply`, `percent`, `set_from`). Numbers
  are Decimals at 60 digits, never floats; a computed amount rounds half-up (away from zero at the half) to the
  currency's places, a count to a whole number, another decimal to six places, or to the `round` step asked
  ("0.05"). A blank has nothing to add to or multiply and stays blank. References are typed by name and resolved by
  the import's own resolver; an unknown one refuses the preview with suggestions. Money totals before and after are
  Python integers (millionths), never SQL sums.
- **Preview** reads each record as its `show` gives it (the show planner, in this session, after one read check),
  works out every field before and after, and dry-runs the record's `update` with the version it read: a refused
  record keeps the command's code (`E_NAME_TAKEN`, `E_VALIDATION`...). All or nothing refuses the preview itself
  when any record would be refused.
- **Apply** sends exactly the diff, one ordinary `update` per record with the previewed `expected_version`, source
  reference `bulk-edit:<run>`: another edit to other fields merges (the row shows `merged` and the versions), one to
  the same field refuses that record (`conflict`, the command's message naming who and when) and the rest go on;
  the run ends `applied` or `applied_partly`. Each update's batch effect (the prior values, read from `show` just
  before it, through the import's `before_values`) and its row outcome are written in the update's own transaction.
  All or nothing undoes what it changed through the batch at the first refusal (`rolled_back`, the rest `not_run`;
  `failed` if that undo is refused). A killed apply resumes at the first row without an outcome.
- **Reverse** is the batch ledger's, looping its slices in one call. `skip_changed` (new in `batches.reverse`)
  leaves every record changed since (`edited_again`, `later_batch`), returns them as `skipped`, and ends the batch
  `reversed_partly`; any other blocker still refuses the whole. Blockers are given back named for a person (record,
  who by name, when).
- **A fix in the shared plan** (found by the seeded test, seed 107): "edited again" compared a record's version with
  its newest effect in any batch, so an edit typed between two batches became invisible once the later batch was
  reversed (its reversal left the version compared with), and reversing the earlier batch wrote over it. The plan
  now walks the writes recorded on the record since the batch's own (`batches._chains`): each must follow the one
  before (version plus one, or the same for a write that changed nothing), and a later batch whose change still
  stands (live, part way, or partly reversed without reaching the record) blocks as `later_batch`. Imports and bank
  sorts get the same check; documents keep their revision check.
- **The page** `/c/<company>/bulk-edit` ("Edit many…" on the customer, vendor, item and account lists): Grid
  (search, inactive, columns; a cell is sent only once it changes, so a 2,000-row grid posts what was typed; the
  column operation and the paste above the grid), Changes (each record before and after, money totals, refusals;
  one confirmation, "Change 20 items"), Done (outcomes; Undo plans first; a blocked plan names each record changed
  since and offers "Leave 1, undo the other 19"). htmx for every post; a refusal answers 200 in place. At 390 px
  each record is a card and nothing scrolls sideways. Screens: `/tmp/m4-shots/bulkedit/`.
- **Agent guide:** "Changing many records at once": a plainly named edit just runs; one that sets how the business
  works from then on gets one check of its exact diff and totals with the owner first.
- **Tests:** `tests/test_bulk_edit.py` (criterion 7 and 11 on 300 items: 300 pairs and 300 audit events, item 17
  merged then conflicting, reverse refused naming it, `skip_changed` restoring 299, a clean reversal equal to the
  snapshot; rounding; a refused record in the middle; paste, copy down, set from; customer terms by name; a custom
  field; grants; an edit between two batches; the seeded property: random grid edits, edits by another user
  between preview and apply, single edits and reversals, after every step each item equals a replay of the writes
  still standing), `tests/test_bulk_edit_surfaces.py` (Python, CLI, HTTP and MCP alike),
  `tests/test_bulk_edit_browser.py` (20 items repriced and undone in real Chrome at 1440 and 390).

### As built: the review of increment 6 (`notes/reviews/r107-inc6-review.md`)

Kabe's rule governs each fix: every batch is reversible as one unit, a preview shows exactly what will change, and
nothing ever ends up changed but impossible to undo.

- **An unfinished apply is never undone in part** (high). `bulk-edit reverse` refuses a run still `applying`,
  saying to finish it (`bulk-edit apply <run>`) or cancel it (`bulk-edit apply <run> --cancel`). Cancel marks the
  rows still waiting `not_run` and reverses what landed through the batch, as one unit; the run ends `cancelled`
  (a new state in co0116), or `failed` with its batch still live if that reversal is refused, so Undo finishes it
  later. An apply refuses to resume once its batch is no longer live (the run ends `cancelled`, the rest
  `not_run`), and the ledger itself (`batches.record_effect`, shared by imports and bank sorts) refuses an effect
  written into a batch that is not live, so a write can never land in a closed batch.
- **Counts are the whole edit's** (medium). Lists stay capped at 50 for display, but a blocked reversal says the
  true total (`details.total`), a plan gives `remaining` (every record it restores) and `skipped_total`, and the
  page's "Undo restores all N", "N changed since" and "Leave K, undo the other M" use them.
- **The browser apply runs in slices** (medium). `bulk-edit apply --limit N` changes at most N records and leaves
  the run `applying`; the page applies 50 at a time (about 2 s), each its own command, so the single writer is free between
  slices, and shows a progress bar (`counts.pending`). A reload, or a stopped apply, shows "Still applying" with
  Finish applying and Cancel, never Undo; a reload that arrives while a slice is still writing waits for it (up to
  20 s) instead of showing the busy refusal.
- **Big lists on a phone** (medium). The grid shows 50 records a page (`bulk-edit grid --offset`, default `limit`
  50, `total` the whole selection), with "Only the 50 shown" or "All N matching" for what a change covers; Preview
  and Apply sit in a bar fixed to the foot of the screen; Changes and Done list 50 rows at a time (`show --cursor`)
  with a filter by outcome, and every count is the whole edit's. Checked in Chrome at 390 px with 2,000 items.
- **Lows.** A `later_batch` blocker says what clears it: "reverse batch X first" only while X can be reversed;
  once X was reversed partly without reaching the record, it says that change stands and the record can only be
  put right by hand or left (`batch_reversible`). `skip_changed` leaves only record-level changes: never the
  ordering rule of a strict kind (a later live batch in a bank import's scope). A paste without a header row over a
  filter or search needs the grid's `selection_fingerprint` (the selection's records in order) and is refused if
  the records changed since; named `records` need none. The 300-item test has its own 900 s timeout.
- **Also found:** every grid cell rendered blank (in Jinja `r.values` is a dict's `values` method); the template now
  reads `r['values']`, and the browser test checks a cell's value.
- **Tests:** in `tests/test_bulk_edit.py`, an unfinished apply refused undo until it is finished or cancelled; an
  apply refused once its batch is not live (and the ledger's own refusal); true totals on 60 items with 55 edited
  since; grid pages and the paste fingerprint; a later batch named only when it can be reversed; a strict kind's
  ordering never skipped. In `tests/test_bulk_edit_browser.py`, 2,000 items at 390 px: 50 cards, the bar in view,
  "All 2000 matching", the changes paged, the apply in slices, a reload part way showing Still applying, then done.
  After merging main (7f307eb8, item pictures), the migration is co0116 on co0115 and `bulk-edit-v1` sits on `item-picture-v1` (845 commands, descriptor c4664e87).

**Timings** (`tools/measure_bulk_edit.py ROWS`, root NVMe, service items, three columns, a 5 percent rise rounded
to 0.05 over every item; seconds, 2026-10-28):

| Rows | Grid (command) | Grid page render | Page HTML | Preview | Apply | Reverse dry run | Reverse | Peak RSS |
|---|---|---|---|---|---|---|---|---|
| 300 | 0.78 | 0.05 | 185 KB | 3.85 | 11.52 | 3.13 | 14.81 | 317 MB |
| 2,000 | 4.48 | 0.16 | 1.1 MB | 24.66 | 71.96 | 18.24 | 59.69 | 320 MB |

Linear: the grid costs about 2.3 ms a record (each read through `item show`), the preview 12 ms (read and dry-run),
the apply 36 ms (a read for the prior values and the update with its audit event), the reversal 30 to 49 ms.

## As built: increment 7

- **Migration co0117** (on co0116): `opening_drafts`, one row per draft: the cutover date, each step's rows as pasted
  text or an attachment id (with a date order when given), the totals the owner gave, the stock items a person
  confirmed may be made, the clearing account named, the state (`draft`, `planned`, `applying`, `applied`,
  `rolled_back`, `failed`, `reversed`, `discarded`), the batch once applied, the last plan's or apply's summary, and a
  version. Guards: never deleted; identity and creation fixed; every change moves the version forward. Decided: the
  pasted text lives in the draft row (bounded at 2 MB a step), not in an attachment per paste: an update is then one
  write with its audit event and no orphan files; `cutover apply` still keeps each step's text as a company attachment
  when it posts, as it keeps any text file, and a step may name an attachment instead.
- **The entered kinds** (`cutover_sources.py`, read through `tabular.py`, never detected from headings):
  `entered_balances` (Account by name, number or ID, Balance on the account's own side; a Side column or Debit and
  Credit columns say otherwise), `entered_open_invoices`, `entered_unpaid_bills` (Customer or Vendor, Number or
  Reference, Date, Due date or Terms, Open amount, PO, Memo; a negative amount is a credit) and `entered_stock`
  (Item, Quantity, Total value or Unit cost, both checked against each other). Headers are matched by synonyms; dates
  read in the column's order, and an all-ambiguous column is refused (`ambiguous_date`) unless `date_order` is given
  (`CutoverFile.date_order`, entered kinds only). They fill the engine's own `OpenDocument` and `StockRow`; balances
  become `EnteredBalance` rows, and `cutover.build` turns them into the `TrialBalance` (`_entered_trial_balance`):
  each account resolved against the chart (an unknown one stays as typed, so `unmapped_account` names it), the side
  from the account's type, receivables, payables and inventory derived from the documents and stock that post when
  the owner left them out, and Opening Balance Equity derived as the balancing figure unless an equity account was
  typed (then `trial_balance_unbalanced` applies). A due date left out is worked out from standard terms (`Net 30`
  from the date), for the posting and the tie-out's aging alike. A stock row names an existing inventory part (by ID
  or full name), or is made as a new one when confirmed (`make_items`) with the company's default accounts: the first
  active income account named for sales (not returns), else the first income account; Cost of Goods Sold; Inventory
  Asset; its cost the value over the quantity.
- **`basis`** on every check: `CutoverCheck` (plan), `CutoverTieRow`, `CutoverTieSection` and `CutoverStockSection`,
  and the tie-out's new `checks` list (one per trial balance figure, given first, each with `tied` and a sentence:
  "tied to the 12,430.55 you gave", "taken from your invoice list; not independently checked", "worked out as assets
  less liabilities; not independently checked"). Every figure of an export is `given`, so the anchor-style path reads
  as before plus the new fields. Decided: `tied` needs every check tied, derived ones too (a derived figure the books
  do not show means the posting went wrong), and the clearing account at 0.00; this includes design §6's "every given
  check tied".
- **Commands** (`company/opening_drafts.py`, `commands/opening_draft_cmds.py`): `opening-draft create`, `update`
  (carrying `expected_version`; an empty step clears it; back to `draft`), `show` (each step with what its rows alone
  show to be wrong, by line), `query`, `discard`, `plan` (the engine's plan, state `planned`), `apply`, `tie-out` and
  `reverse` (added beside §9's list: the domain door to the batch reversal, as `import reverse` and `bulk-edit reverse`
  are). The totals given become one more `entered_balances` file on the receivables, payables and inventory accounts
  by ID. `cutover plan/apply/tie-out` is the only thing that posts; the draft adds no posting path.
- **All or nothing, and a stop continues.** `apply` writes the batch (kind `opening`) and the draft's `applying` in the
  command's own transaction, then runs `cutover.apply` with a recorder that writes each created record's effect in its
  step's transaction (`cutover.step_recorder`). A command refused part way (`E_CUTOVER_INCOMPLETE`) reverses the batch
  at once: `rolled_back`, and the error says nothing of the opening stays; if that undo is itself refused the draft is
  `failed` and the error names why (`opening-draft reverse` finishes it). A stop any other way (a killed process) leaves
  `applying` with the batch live, and the same `apply` continues in that batch, every record made before `already_in`.
- **Reversible as one unit** through the shared ledger: invoices, bills and journals by their families' delete with
  history kept, credits and stock adjustments by void, customers, vendors, items and terms renamed and deactivated; a
  record in use since (a payment applied to an opening invoice) refuses the whole with the record named. Records a
  reversed opening made are no longer `already_in` for a later opening (`Books` passes over created effects of
  reversed `opening` batches), so a corrected opening posts afresh. Two fixes in the shared plan (`batches._in_use`):
  a row belonging to a document the same batch made (an opening invoice's lines naming the customer and item it
  made; a stock placement, through its movement) is no use of the record, and such a record's inverse is probed by
  running it once those documents are gone, not by a dry run beforehand.
  *Left for R186:* accounts. An account that has carried postings is never deactivated (accounts keep their
  history), so the batch does not record the accounts an opening makes; after a reversal the clearing account (and,
  on an anchor-style draft, any account made from a chart export) stands at 0.00 and the next opening uses it again.
  Retiring it needs an inverse for "an account whose every posting was reversed", which the accounts module does not
  have. A separate `opening-draft reverse` needs the caller's own explicit delete grants for the families it deletes
  (`transaction.invoice.delete`, `credit_memo`, `bill`, `journal_entry`, `sales_receipt`; vendor credits and stock
  adjustments are voided under `ledger.post`), as every batch reversal does (§8a). The automatic undo of a refusal
  part way does not (review M1, below).
- **`accept_differences`: a capability, not people-only.** principal-directives.md keeps people-only for the five acts
  no capability could make safe; posting an opening that does not tie is not one of them. So it is the capability
  `opening.accept-differences` (standard; held by standard, admin and owner as before, by an agent only through its
  principal's explicit grant and while the principal holds it, checked at one declared site,
  `opening_drafts.require_accept`). It takes the place §4 gave `import.approve`, whose other acts were dropped on
  2026-10-28. It downgrades only `receivables_do_not_tie`, `payables_do_not_tie`, `inventory_does_not_tie` and
  `trial_balance_unbalanced` to warnings marked "accepted"; the difference stays in the clearing account, and tie-out
  says not tied until it is found.
- **Confirmation (rule ucad913).** No approve step and no threshold. The agent guide's "Opening balances for a business
  with no old books" teaches one check of the exact details with the owner before `apply` (the date, the plan's counts
  and totals, which checks are given and which derived), then apply on their yes; `accept_differences` is the owner's
  decision. `plan` and `apply` help say the same.
- **Permissions:** layer `opening-draft-v1` on `user-home-v1` (re-chained at the merges of main 0b3769e8 and 5c2616b3; 859 commands, descriptor 91a7dc32): `show` and `query`
  member reads, the other seven standard, all under `import`; the new capability above. No existing capability, role
  default or threshold changes.
- **The page** `/c/<company>/opening` (Company menu: "Opening balances"; `opening_pages.py`, `opening.html`,
  `opening.css`): with no draft, a short explanation, "opening balances come first; enter later work dated after"
  (question 6), the start form (date and the totals known) and the drafts; a draft's steps Date and checks, Balances
  you know, Open invoices, Unpaid bills, Stock (each a grid taking a paste under its shown header, its row count and
  problems by line; stock with "Make these new items"), and Tie out and post (the plan, the checks given and derived
  apart, Post naming the date, "post it anyway" only when every blocker is acceptable, then the tie-out given and
  derived apart, and Undo planning first). htmx for every post; a refusal answers 200 in place; a reload returns to
  the draft and step. At 390 px the steps stack and nothing scrolls sideways (asserted at every step). Screens:
  `/tmp/m4-shots/opening/` (each step at 1440 and 390).
- **Question 7** stands: stock comes in by the stock step (or an `inventory adjust` import), never through the `item`
  kind.
- **Exceptions reachable from entered rows** (tested): `unmapped_account`, `missing_open_invoices`,
  `missing_unpaid_bills`, `missing_inventory_valuation`, the three `*_do_not_tie`, `document_after_cutover`,
  `document_without_date`, `type_conflict`, `negative_stock`, `possible_duplicate_customer`, `unknown_terms`,
  `several_trial_balances` (entered balances beside a trial balance export), `trial_balance_unbalanced`,
  `entries_before_cutover`, `period_closed`, and the new `duplicate_account`, `unreadable_amount`, `ambiguous_date`,
  `missing_column`, `unknown_item`, `stock_value_mismatch`. The rest of docs/cutover.md's list concerns exports the
  guided path never reads (IIF lists and their row checks, report titles, dates and totals, reconciliations,
  uncleared items, Undeposited Funds' report, 1099s) and cannot arise from typed rows.
- **Tests:** `tests/test_opening_balances.py` (criterion 6 with 4 balances, 12 invoices, 8 bills and 30 stock items
  worked by hand; a wrong total refused leaving nothing; accepting a difference; a refusal part way rolled back and a
  stop continued; reversal as one unit and refused naming the payment; typed and derived equity; the reachable
  exceptions; the same data through `cutover` from anchor-style exports giving identical balances, aging and stock),
  `tests/test_opening_balances_surfaces.py` (Python, CLI, HTTP and MCP alike), `tests/test_opening_balances_browser.py`
  (a person pastes, plans, posts and reads the tie-out in real Chrome at 1440 and 390).

### As built: the review of increment 7

Reviewed at 8ce229b4; each finding fixed with a test (`tests/test_opening_balances.py` unless named).

- **H1. Stock sold since blocks the undo** (shared, `batches._document_uses`). A document the batch made that moved an
  item's stock is in use while a later movement of that item, by a document the batch did not make and still
  standing, follows it: the refusal names it ("invoice 2001 (it moves Part 10 later)"). This covers a document
  import's inventory adjustment too. Test: Part 10 sold after the opening.
- **H2. An undo stopped part way is finished first.** `apply` is refused while the draft's batch is `reversing` or
  `blocked`; `opening-draft reverse` that stops part way leaves the draft `failed`, whose `next` is reverse; and
  `cutover`'s `Books` no longer takes as already in any record an undo of an opening touched (record by record, from
  the reversal batch's effects) or any document since deleted or voided. Test: a killed apply, an undo stopped after
  one slice, apply refused, the undo finished, the same draft applied again and tied, with a credit memo and a vendor
  credit (credits on the entered path).
- **M1. All or nothing for every caller.** Decided by the coordinator: a command's automatic undo of its own writes in
  the same call, nothing depending on them, is part of its all-or-nothing promise and needs no delete grant (the
  caller had the authority to write them). `batches.reverse(own_writes=True)` sets that for its run, and
  `access.require_explicit_grant` honours it for the five document deletes only (`batches.OWN_ROLLBACK_DELETES`); only
  `opening_drafts._roll_back` passes it. A later, separate reverse still needs the caller's own grants. Test: the
  default owner, holding no delete grant, refused part way, leaves nothing.
- **L1.** A record the batch's own documents use is still dry-run in the plan; only its refusal for something other
  than permission is set aside (it is probed again by running, once those documents are gone).
- **L2.** Decided: accounts stay unretired (above); `opening-draft reverse` help says so, and its result lists in
  `stays` what the opening changed outside its batch.
- **L3.** A second opening is refused at `apply` while another stands (applying, applied or failed), naming it.
- **L4.** What an opening changes outside its batch is named, not undone: `apply` returns `outside_batch` (the clearing
  account, a default sales tax item it set, the next invoice number it carried on, the steps kept as attachments),
  kept in the draft's summary, and `reverse` returns it as `stays`. The draft's state changes inside apply and
  reverse (applying, applied, rolled back, failed, reversed) each write an audit event.
- **L5.** An undo that finishes on a draft that never finished applying returns it to `rolled_back`, editable and
  applied again with nothing pasted anew; an applied opening undone ends `reversed`.
- **L6.** `require_accept` answers `principal_lacks_capability` when the principal does not hold the capability; the
  stale `import.approve` mentions are corrected (principal-directives.md §4 and §6, §8 here marked superseded), and
  §6's "a person's act" now names the capability. Test: an agent through HTTP, ungranted, granted with a principal
  who lacks it, granted with one who holds it (a dry run), and a person.
- **L7.** The same-books test compares every aging bucket and each invoice's date and due date too; the reversal
  test asserts the blocker names the payment on invoice 1012; a new stock item's cost rounds half-up.
- **Import reversal** (`tests/test_import_documents.py`): a customer `create_missing` made for its own invoice is no
  use of it, and a payment applied later to that invoice is named.
