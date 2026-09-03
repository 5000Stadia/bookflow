# Bookflow — blueprint

This document states what Bookflow is and how every part of it works. It is written for a reader with no prior knowledge of the project. Exact values carry this license: change it if it makes the product better, and say why.

## 1. What it is

Bookflow is a multi-company double-entry accounting system modeled on QuickBooks Desktop. It is a Python library first. A CLI, an HTTP host, and an MCP server are thin adapters over the library. Graphical clients, on desktop, web, or mobile, are built later against the HTTP host or the library directly.

Pillars, in rank order:

1. **Correct books.** Every posted transaction balances. History is never destroyed.
2. **Agent-usable.** An AI agent with only the documentation can operate every command.
3. **One contract, many surfaces.** CLI, HTTP, MCP, and GUI call the same commands and get the same results.
4. **Accountable.** Every write records actor, interface, principal, and reason.
5. **Isolated.** One company's data is invisible to anyone without membership.

## 2. Layering

```
clients      GUI (desktop / web / mobile)      AI agent      shell / scripts
                 |                                |               |
adapters     HTTP host (bookflow serve)      MCP server        CLI (bookflow ...)
                 \________________________________|_______________/
                                            |
core         commands  ->  services  ->  repositories  ->  SQLite (hub.db, company.db)
```

- **Core** contains all logic. It has no knowledge of terminals, HTTP, or MCP.
- **Adapters** parse their protocol into a command input plus a context, call the core, and render the command output. They contain no business logic.
- **Clients** never touch the database.

A desktop GUI on the same machine as the data may import the core directly. Any other machine talks to the HTTP host. The host is the only process that opens a company database on behalf of remote clients.

### 2.1 Technology

| Concern | Choice |
|---|---|
| Language | Python 3.12 or later |
| Package manager | uv |
| CLI | Typer |
| Command and record models | Pydantic v2 |
| Database access | SQLAlchemy 2 Core |
| Migrations | Alembic, one migration chain for hub.db and one for company.db |
| Storage | SQLite in WAL mode |
| Password hashing | argon2id |
| HTTP host | FastAPI over uvicorn |
| MCP | Official Python MCP SDK, tool schemas generated from command models |
| Tests | pytest |
| Identifiers | ULID strings, generated in the core |

Everything must remain portable to PostgreSQL. No SQLite-only SQL in repositories. No triggers.

## 3. Data on disk

```
<data_root>/                     default ~/.bookflow, override with BOOKFLOW_DATA_ROOT
  hub.db                         users, credentials, tokens, company registry, memberships, hub audit
  config.toml                    saved default company, client display name
  companies/
    <company_id>/
      company.db                 every table for one company
      attachments/
        <first two hex of sha256>/<sha256>      content-addressed file bodies
      backups/
        <company_id>-<timestamp>.db
```

- `company_id` is a ULID. Paths are never derived from the company name.
- A company directory is self-contained. Copying it into another data root and registering it with `bookflow company attach <path>` opens it with nothing lost.
- A company database is opened by exactly one process at a time for writing. In host mode, the host is that process. Opening a company database that lives on a network share is refused; the core checks the filesystem type and refuses with error `E_NETWORK_SHARE`.
- Every database records its schema version. Opening a database with an older version runs pending migrations after taking a backup into `backups/`. Opening a database with a newer version than the code knows is refused with `E_SCHEMA_TOO_NEW`.

## 4. Identity

### 4.1 Users

Table `users` in hub.db.

| Field | Type | Meaning |
|---|---|---|
| id | ULID | |
| kind | enum | `human`, `agent`, `system` |
| username | text, unique | login name for humans, handle for agents |
| display_name | text | |
| owner_user_id | ULID, nullable | for `agent` kind: the human that owns this agent. Required for agents. |
| password_hash | text, nullable | humans only |
| active | bool | |
| created_at, updated_at, version | | see section 6 |

There is exactly one `system` user per data root, created by `bookflow init`. Scheduled jobs and migrations act as it.

### 4.2 Tokens

Table `api_tokens` in hub.db. Tokens are for agents and for GUI or HTTP sessions.

| Field | Meaning |
|---|---|
| id | ULID |
| user_id | owner of the token |
| token_hash | sha256 of the secret; the secret is shown once at creation and never stored |
| label | free text |
| expires_at | nullable |
| last_used_at | |
| revoked_at | nullable |

### 4.3 Companies and memberships

Table `companies` in hub.db: `id`, `display_name`, `path`, `created_at`, `active`.

Table `memberships` in hub.db: `user_id`, `company_id`, `role`, `granted_by`, `granted_at`, `revoked_at`.

Roles and what they may do:

| Role | Read | Write lists | Post transactions | Manage members | Company settings, closing date, delete |
|---|---|---|---|---|---|
| readonly | yes | no | no | no | no |
| standard | yes | yes | yes | no | no |
| admin | yes | yes | yes | yes | yes except delete |
| owner | yes | yes | yes | yes | yes |

An agent's memberships are granted by a human with admin or owner role on that company. An agent never inherits its owner's memberships.

### 4.4 Authentication per interface

| Interface | How the actor is established |
|---|---|
| CLI, local | The OS user is mapped to a Bookflow human user in `config.toml`. `bookflow init` creates this mapping for the first owner. `--as-token <secret>` acts as a token's user instead. |
| HTTP | Bearer token. |
| MCP | Token from the server's launch configuration. One MCP server process serves one token. |
| GUI | Whatever the GUI uses to obtain a token; the GUI presents a bearer token to the host, or, when in-process, a password login that yields a session token. |

## 5. The command contract

Every operation is a command. A command has a name, an input model, an output model, and a set of error codes. The CLI, HTTP host, and MCP server are generated from command definitions, not written by hand per command.

### 5.1 Names

`<noun> <verb>`, nouns singular: `company new`, `customer create`, `invoice post`, `audit list`. Nouns are QuickBooks Desktop names.

Verbs used across lists: `create`, `update`, `show`, `list`, `activate`, `deactivate`. Verbs used on transactions: `post`, `show`, `list`, `void`. Reports use `report <name>`.

### 5.2 Context

Every command receives a context that the adapter builds. No field of the context is accepted from command input; an adapter that receives a context field in the input rejects the call with `E_CONTEXT_IN_INPUT`.

| Field | Type | Source |
|---|---|---|
| actor_id | ULID | authenticated user |
| actor_kind | enum | from the user record |
| on_behalf_of | ULID, nullable | for agent actors, the owner user; for system, the schedule owner; null for humans |
| interface | enum | `cli`, `http`, `mcp`, `gui`, `system` |
| client_name | text | e.g. `bookflow-cli`, `bookflow-desktop` |
| client_version | text | |
| client_host | text | hostname of the machine the adapter runs on |
| session_id | ULID | one per CLI invocation, HTTP session, MCP server process, or GUI login |
| request_id | ULID | one per command call |
| idempotency_key | text, nullable | caller supplied; see 6.5 |
| reason | text, nullable | caller supplied; required for `agent` and `system` actors on any command that posts to the ledger, else `E_REASON_REQUIRED` |
| source_ref | text, nullable | caller supplied; an identifier for what triggered the write, such as an email id or attachment id |
| company_id | ULID, nullable | the company the command runs against; null for hub commands |

`reason`, `source_ref`, and `idempotency_key` are set by the caller through global flags on the CLI (`--reason`, `--source-ref`, `--idempotency-key`), request headers on HTTP (`X-Bookflow-Reason`, `X-Bookflow-Source-Ref`, `Idempotency-Key`), and named tool arguments on MCP.

### 5.3 Company selection

For company-scoped commands, the company is resolved in this order, first match wins:

1. `--company <id-or-display-name>` on the CLI, path segment on HTTP, tool argument on MCP.
2. Environment variable `BOOKFLOW_COMPANY`.
3. `default_company` in `config.toml`, set by `bookflow company use <id>`.

If the resolved company is not among the actor's memberships, the error is `E_COMPANY_NOT_FOUND`. The same error is returned whether the company does not exist or the actor lacks membership.

### 5.4 Output

Every command returns a structured result. On the CLI:

- `--json` prints the output model as one JSON document on stdout.
- Without `--json`, a human-readable table or summary goes to stdout.
- Warnings and progress go to stderr.
- Exit code 0 on success, 1 on a rejected command with a named error, 2 on invalid usage, 3 on internal failure.

Errors are JSON documents on stderr with `code`, `message`, and `details`. Error codes are stable strings prefixed `E_`. Every command's documentation lists the codes it can return.

### 5.5 Dry run

Every command that writes accepts `--dry-run`. It runs validation and returns the output model that a real run would return, with `dry_run: true`, and writes nothing, including nothing to the audit log.

### 5.6 Money on input

Amounts on input are decimal strings, never floats: `"123.45"`. An optional currency code follows: `"2345 JPY"`. The CLI accepts `--amount "2345 JPY"`. Amounts with more decimal places than the currency allows are rejected with `E_AMOUNT_PRECISION`.

### 5.7 Dates

Dates are ISO 8601 `YYYY-MM-DD`. Timestamps are ISO 8601 with timezone, stored as UTC. The company has a timezone setting used to interpret dates without times.

## 6. Records, versions, and concurrency

### 6.1 Common fields

Every table except `audit_events`, `audit_entries`, and `presence` carries:

| Field | Meaning |
|---|---|
| id | ULID |
| version | integer, starts at 1, increments on every write |
| created_at, created_by, created_via | timestamp, user id, interface |
| updated_at, updated_by, updated_via | timestamp, user id, interface |

Every `show` and `list` output includes these fields.

### 6.2 Versioned writes

An `update` command accepts `expected_version`. When present and equal to the current version, the write proceeds. When present and different, the command is rejected with `E_VERSION_CONFLICT` and details:

```json
{
  "current_version": 7,
  "updated_by": "01J...",
  "updated_by_name": "claude-agent",
  "updated_via": "mcp",
  "seconds_since_update": 0.6,
  "changed_fields": ["phone", "notes"]
}
```

unless the fields the caller is writing are disjoint from `changed_fields`, in which case the write is merged onto the current version, succeeds, and the output carries `merged_over_versions: [6, 7]`.

### 6.3 Blind writes

An `update` without `expected_version` proceeds against the current version. The output carries `previous_version`, `previous_updated_by`, `previous_updated_via`, and `seconds_since_previous_update`. If the previous write was by a different actor within the company's `recent_activity_window_seconds` (default 60), the output also carries `recent_concurrent_activity: true` and the CLI prints a warning to stderr.

### 6.4 Presence

Table `presence` in company.db: `record_type`, `record_id`, `user_id`, `interface`, `started_at`, `heartbeat_at`. A GUI registers presence when a user opens a record for editing and sends a heartbeat every 30 seconds. Presence older than 90 seconds is ignored. `show` output includes `editing_by` listing live presence. Presence never blocks a write.

### 6.5 Idempotency

Any `create` or `post` command may carry an idempotency key. Table `idempotency_keys` stores `key`, `actor_id`, `command`, `result_id`, `created_at`. A repeat of the same key by the same actor returns the original result with `idempotent_replay: true` and writes nothing. Keys expire after 30 days.

### 6.6 Deactivation, void, and deletion

List records are never deleted through the API. `deactivate` sets `active = false`; deactivated records are excluded from `list` unless `--include-inactive`, and cannot be referenced by new transactions. Transactions are never deleted; see section 10.5. The only deletion command is `company delete`, which requires the owner role and the literal company id repeated in `--confirm`.

## 7. Audit log

Two tables in company.db, plus the same pair in hub.db for hub commands.

`audit_events`: one row per command execution that wrote anything.

| Field | Meaning |
|---|---|
| id | ULID |
| at | timestamp |
| command | command name |
| actor_id, actor_kind, on_behalf_of | from context |
| interface, client_name, client_version, client_host | from context |
| session_id, request_id, idempotency_key | from context |
| reason, source_ref | from context |
| summary | one line, generated by the command, e.g. `posted invoice 1043 to Acme Plumbing for 1,250.00` |

`audit_entries`: one row per record touched by the event.

| Field | Meaning |
|---|---|
| id | ULID |
| event_id | |
| record_type, record_id | |
| action | `create`, `update`, `deactivate`, `activate`, `post`, `void`, `link`, `unlink` |
| version_before, version_after | |
| before | JSON snapshot of the record before, null on create |
| after | JSON snapshot after |

Rules:

- No command updates or deletes audit rows. The repository layer exposes only insert and read.
- `audit list` filters by actor, actor kind, interface, record type, record id, command, and time range, and returns events with entry counts. `audit show <event_id>` returns the event with entries and a computed field diff per entry.
- `activity <record_type> <record_id>` merges audit entries, notes, and attachment links for that record in time order.
- Reads are not audited.

## 8. Money and currency

### 8.1 Representation

An amount is `(minor_units: integer, currency: ISO 4217 code)`. Stored as two columns wherever an amount lives. Displayed using the currency's decimal places. Arithmetic is integer arithmetic. Allocation across lines uses largest-remainder rounding so totals always reconcile.

### 8.2 Home currency

The company has one home currency, chosen at rollout and immutable afterwards. Every ledger line is in home currency.

### 8.3 Foreign-tagged amounts

Any amount input may carry a foreign currency code. At posting the core:

1. Looks up the rate for the transaction date in `exchange_rates` (`date`, `from_currency`, `to_currency`, `rate` as a decimal string, `source`, `entered_by`). An explicit `--rate` on the command overrides the table and is recorded with source `manual`.
2. Converts to home currency, rounding to the home currency's precision.
3. Stores on the line: `amount` (home), plus `original_amount`, `original_currency`, `rate_used`.

If no rate exists and none is supplied, the command is rejected with `E_NO_EXCHANGE_RATE`.

When a foreign-tagged payment settles a home-currency receivable or payable and the converted amount differs from the open balance, the difference posts to the seeded account `Exchange Gain/Loss`.

Rates are entered with `rate set`. There is no automatic rate fetching. A fetcher may be added later as a separate command that the documentation names.

## 9. Company

### 9.1 Company info

Table `company_info` in company.db, exactly one row.

| Field | Meaning |
|---|---|
| legal_name | |
| display_name | shown in lists and the GUI |
| fein | text, formatted `NN-NNNNNNN`, validated for shape only |
| entity_type | `sole_proprietor`, `partnership`, `llc`, `s_corp`, `c_corp`, `nonprofit`, `other` |
| income_tax_form | `1040_schedule_c`, `1065`, `1120`, `1120s`, `990`, `other` |
| address_line1, address_line2, city, state, postal_code, country | |
| phone, email, website | |
| fiscal_year_start_month | 1 to 12 |
| home_currency | ISO 4217, immutable |
| timezone | IANA name |
| closing_date | date, nullable; see 10.6 |
| recent_activity_window_seconds | default 60 |
| default_chart | which seeded chart was applied at rollout |

### 9.2 Rollout

`bookflow company new` takes every field above as flags, or `--interactive` to prompt for each. It creates the directory, the database, the `company_info` row, the seeded chart of accounts named by `--chart`, the standard terms, payment methods, and sales tax codes listed in section 11, and grants the creating user the owner role. Output is the company id and display name.

Seeded charts, chosen by `--chart`: `general`, `service`, `construction_trades`, `retail`, `nonprofit`. Each is a data file in the package. `general` is the default.

### 9.3 Other company commands

`company list`, `company show`, `company update`, `company use`, `company attach <path>`, `company backup`, `company delete`.

## 10. The general ledger

### 10.1 Accounts

Table `accounts`.

| Field | Meaning |
|---|---|
| name | unique among siblings |
| number | text, optional, unique within the company when present |
| type | one of the QuickBooks account types below |
| parent_id | nullable; sub-accounts must share the parent's type |
| description | |
| active | |
| is_system | true for accounts the core requires and will not let be deactivated |

Account types and their normal balance:

| Type | Normal balance | Balance sheet or P&L |
|---|---|---|
| bank | debit | balance sheet |
| accounts_receivable | debit | balance sheet |
| other_current_asset | debit | balance sheet |
| fixed_asset | debit | balance sheet |
| other_asset | debit | balance sheet |
| accounts_payable | credit | balance sheet |
| credit_card | credit | balance sheet |
| other_current_liability | credit | balance sheet |
| long_term_liability | credit | balance sheet |
| equity | credit | balance sheet |
| income | credit | P&L |
| cost_of_goods_sold | debit | P&L |
| expense | debit | P&L |
| other_income | credit | P&L |
| other_expense | debit | P&L |
| non_posting | none | neither |

System accounts every company has: `Accounts Receivable`, `Accounts Payable`, `Undeposited Funds`, `Opening Balance Equity`, `Retained Earnings`, `Sales Tax Payable`, `Inventory Asset`, `Cost of Goods Sold`, `Exchange Gain/Loss`.

### 10.2 Transactions

Table `transactions` (header).

| Field | Meaning |
|---|---|
| type | see 10.3 |
| number | per-type sequence, text, editable, unique per type |
| date | transaction date |
| status | `posted`, `voided` |
| name_type, name_id | the customer, vendor, employee, or other name the transaction is with, when the type has one |
| memo | |
| total | home currency; meaning depends on type |
| form fields | type-specific, stored in the type's own table keyed by transaction id (e.g. `invoices` holds terms, due date, ship date) |
| voided_at, voided_by, void_reason, reversing_transaction_id | null unless voided |

Table `transaction_lines`.

| Field | Meaning |
|---|---|
| transaction_id, line_no | |
| account_id | |
| debit, credit | home currency minor units; exactly one non-zero |
| original_amount, original_currency, rate_used | null unless foreign-tagged |
| name_type, name_id | customer, job, vendor, employee, or other name |
| item_id | nullable |
| quantity | decimal string, nullable |
| class_id | nullable |
| description | |
| billable | bool |
| sales_tax_code_id | nullable |

Every posted transaction satisfies: sum of debits equals sum of credits, every line's account is active and not `non_posting`, every AR line names a customer or job, every AP line names a vendor.

### 10.3 Transaction types

Each type is a form that produces lines. The posting rule is fixed per type.

| Type | Command | Name | Lines produced |
|---|---|---|---|
| journal_entry | `journal post` | optional per line | as entered |
| invoice | `invoice post` | customer or job | Dr AR; Cr income per item; Cr Sales Tax Payable; inventory items also Dr COGS, Cr Inventory Asset |
| sales_receipt | `sales-receipt post` | customer or job | Dr deposit-to account or Undeposited Funds; Cr income; tax and inventory as invoice |
| credit_memo | `credit-memo post` | customer or job | reverse of invoice |
| payment | `payment receive` | customer or job | Dr Undeposited Funds or bank; Cr AR; applies to open invoices |
| deposit | `deposit post` | none | Dr bank; Cr Undeposited Funds and any other lines |
| bill | `bill post` | vendor | Dr expense, COGS, or Inventory Asset per line; Cr AP |
| bill_payment | `bill pay` | vendor | Dr AP; Cr bank or credit card; applies to open bills |
| check | `check write` | any | Cr bank; Dr per line |
| credit_card_charge | `charge post` | vendor | Cr credit card; Dr per line |
| transfer | `transfer post` | none | Dr to account; Cr from account |
| inventory_adjustment | `inventory adjust` | none | Dr or Cr Inventory Asset; offset to adjustment account |
| vendor_credit | `vendor-credit post` | vendor | reverse of bill |

Forms and their tables are built in the release after the ledger; see section 21.

### 10.4 Applications

Table `applications`: `paying_transaction_id`, `paid_transaction_id`, `amount`. Payments apply to invoices, bill payments to bills, credits to either. Open balance of a transaction is its total minus applied amounts. Aging reports read this table.

### 10.5 Void

`<type> void <id>` requires a reason. It sets status `voided`, zeroes nothing, and posts a reversing transaction of the same type dated the same day, linked by `reversing_transaction_id`, so the ledger history of both remains. Voiding a transaction with applications is rejected with `E_HAS_APPLICATIONS` until the applications are removed by voiding or unapplying the paying transaction.

### 10.6 Closing date

When `closing_date` is set, any post, void, or update whose transaction date is on or before it is rejected with `E_PERIOD_CLOSED`. Changing the closing date requires the admin or owner role and is audited. There is no password override; the audit log is the control.

### 10.7 Numbering

Each transaction type has a sequence in `sequences` (`type`, `next_number`, `prefix`). Posting takes the next number unless the caller supplies one. A supplied duplicate is rejected with `E_DUPLICATE_NUMBER`.

## 11. Lists

All lists share the common fields of section 6.1, `active`, and the five verbs `create`, `update`, `show`, `list`, `activate`, `deactivate`. Names are unique within a list, case-insensitively. Hierarchical lists have `parent_id` and a computed `full_name` of the form `Parent:Child:Grandchild`, matching QuickBooks. Hierarchy depth is limited to 5.

### 11.1 Customers and jobs

Table `customers`. A job is a customer with `parent_id` set; jobs may nest to depth 5.

Fields: `name`, `company_name`, `salutation`, `first_name`, `last_name`, `bill_address` (line1, line2, city, state, postal_code, country), `ship_address` (same shape), `phone`, `alt_phone`, `fax`, `email`, `cc_email`, `website`, `contact`, `alt_contact`, `terms_id`, `sales_tax_code_id`, `sales_tax_item_id`, `resale_number`, `credit_limit`, `price_level_id` (nullable, reserved), `preferred_payment_method_id`, `account_number`, `job_status` (`none`, `pending`, `awarded`, `in_progress`, `closed`, `not_awarded`), `job_start`, `job_projected_end`, `job_end`, `job_description`, `job_type`, `linked_vendor_id` (nullable, see 11.3), `notes`.

### 11.2 Vendors

Table `vendors`. Fields: `name`, `company_name`, `salutation`, `first_name`, `last_name`, `address`, `phone`, `alt_phone`, `fax`, `email`, `cc_email`, `website`, `contact`, `alt_contact`, `terms_id`, `account_number`, `tax_id`, `eligible_1099` (bool), `default_expense_account_id`, `linked_customer_id` (nullable), `notes`.

### 11.3 Customer and vendor link

A customer and a vendor may be linked when they are the same legal entity. `customer link-vendor <customer> <vendor>` sets both sides; `unlink` clears both. The link changes nothing about posting. `show` on either side includes the other. The GUI may offer to copy contact fields across the link; the core does not.

### 11.4 Employees

Table `employees`. Fields: `name`, `first_name`, `middle_name`, `last_name`, `address`, `phone`, `email`, `ssn_last4`, `hire_date`, `release_date`, `notes`. Employees may be named on checks and on time entries. Payroll fields are absent by design and will be added to this table by a later migration.

### 11.5 Other names

Table `other_names`. Fields: `name`, `company_name`, `address`, `phone`, `email`, `contact`, `account_number`, `notes`. Used for owners, partners, and payees that are neither customers nor vendors. QuickBooks lets an other name be converted to a customer or vendor once; Bookflow provides `other-name convert --to customer|vendor`, which creates the target, deactivates the other name, and rewrites `name_type` on its transactions in one audited event.

### 11.6 Items

Table `items`. `type` is one of:

| Type | Purpose | Accounts |
|---|---|---|
| service | labor and services | income; optional expense for purchased services |
| inventory_part | stocked goods | income, COGS, asset |
| non_inventory_part | goods not tracked in stock | income; optional expense |
| other_charge | fees, shipping, miscellaneous | income; optional expense |
| subtotal | sums preceding lines on a form | none |
| group | a bundle of other items | none |
| discount | percentage or amount off the preceding line or subtotal | income or expense |
| payment | a payment recorded on an invoice | deposit-to |
| sales_tax_item | one rate payable to one agency | Sales Tax Payable; agency is a vendor |
| sales_tax_group | several sales tax items applied together | none |
| inventory_assembly | built from other items | as inventory_part; reserved, not built in release 1 |

Fields: `name`, `type`, `parent_id`, `description`, `purchase_description`, `price` (amount or nullable), `cost`, `income_account_id`, `expense_account_id`, `asset_account_id`, `sales_tax_code_id`, `preferred_vendor_id`, `manufacturer_part_number`, `unit_of_measure`, `reorder_point`, `quantity_on_hand` (derived, never edited directly), `average_cost` (derived), `percent` (for discount and sales tax items), `tax_agency_vendor_id`, `group_members` (item id and quantity, for group and assembly).

Inventory valuation is average cost. Quantity on hand and average cost are recomputed from inventory-affecting transactions and adjustments; they are never set by an `update`.

### 11.7 Classes

Table `classes`. Fields: `name`, `parent_id`. Company setting `use_classes` controls whether forms prompt for one.

### 11.8 Terms

Table `terms`. Fields: `name`, `kind` (`standard` or `date_driven`), `due_days`, `discount_days`, `discount_percent`, `due_day_of_month`, `discount_day_of_month`, `due_next_month_if_within_days`. Seeded: `Due on receipt`, `Net 15`, `Net 30`, `Net 60`, `1% 10 Net 30`, `2% 10 Net 30`.

### 11.9 Payment methods

Table `payment_methods`. Fields: `name`, `kind` (`cash`, `check`, `credit_card`, `debit_card`, `ach`, `other`). Seeded: `Cash`, `Check`, `Visa`, `MasterCard`, `American Express`, `Discover`, `ACH`, `Other`.

### 11.10 Sales tax codes

Table `sales_tax_codes`. Fields: `code` (three characters), `description`, `taxable` (bool). Seeded: `Tax` taxable, `Non` non-taxable. Sales tax items and groups live in `items`.

### 11.11 Price levels and ship methods

Reserved. Tables are not created in release 1; the customer field `price_level_id` exists and is null.

## 12. Notes, attachments, and activity

### 12.1 Notes

Table `notes`. Fields: `record_type`, `record_id`, `body` (text, Markdown allowed), `author_id`, `interface`, `at`, `edited_at`, `kind` (`comment` or `system`). Any record in any table in company.db may carry notes, including `company_info`. `note add <record_type> <record_id> --body`, `note edit`, `note list`. Editing keeps the original in the audit log. Notes are never deleted.

### 12.2 Attachments

Table `attachments`: `sha256`, `size_bytes`, `media_type`, `original_filename`, `uploaded_by`, `uploaded_at`. The file body lives at `attachments/<sha256[:2]>/<sha256>`. Uploading the same bytes twice creates one body and one row.

Table `attachment_links`: `attachment_id`, `record_type`, `record_id`, `linked_by`, `linked_at`, `caption`. One attachment may link to many records.

Commands: `attachment add <record_type> <record_id> <path>`, `attachment link`, `attachment unlink`, `attachment list`, `attachment get <id> --out <path>`. Maximum size per file is a company setting, default 25 MB. Unlinking the last link does not delete the body; `company compact` removes unlinked bodies and is audited.

### 12.3 Activity feed

`activity <record_type> <record_id>` returns, in time order, every audit entry for the record, every note, and every attachment link, each tagged with its kind and actor. `--since`, `--until`, and `--kinds` filter it. This is the chronological job history the GUI shows on any record.

## 13. Work orders and scheduler

Built after release 1. Designed here so release 1 leaves room.

### 13.1 Work orders

Table `work_orders`: `number`, `customer_id` (customer or job), `title`, `description`, `status` (`draft`, `scheduled`, `in_progress`, `on_hold`, `complete`, `invoiced`, `cancelled`), `priority`, `scheduled_start`, `scheduled_end`, `actual_start`, `actual_end`, `site_address`, `assignees` (employee ids), `invoice_transaction_id` (nullable). Table `work_order_lines`: `item_id`, `description`, `quantity`, `rate`, `billable`. `work-order invoice <id>` creates an invoice from billable lines and links it. Work orders carry notes and attachments like any record, and the activity feed is their job log.

### 13.2 Time entries

Table `time_entries`: `employee_id`, `customer_id`, `item_id` (service), `date`, `duration_minutes`, `billable`, `work_order_id`, `notes`. Billable time is pulled onto invoices. This is the table payroll will read for hourly employees.

### 13.3 Scheduler

Table `schedules` in company.db: `name`, `owner_user_id`, `command`, `input` (JSON), `run_at` (one-off, nullable), `rrule` (RFC 5545 recurrence, nullable), `next_run_at`, `last_run_at`, `last_result`, `enabled`. The host process runs due schedules as the `system` user with `on_behalf_of` the owner and `interface` `system`. First uses: memorized transactions (`invoice memorize`, `bill memorize`) and reminders (`reminder add`), which surface through `reminder list` and through the HTTP host as a notification endpoint the GUI polls.

## 14. Reports

Every report reads `transaction_lines`, `transactions`, and `applications` only. Every report takes `--from`, `--to`, `--basis accrual|cash`, `--json`, and `--csv`. Cash basis treats income and expense as occurring when payment applies, using `applications`.

Release 2 reports: trial balance, general ledger, profit and loss, balance sheet, AR aging summary and detail, AP aging summary and detail, customer balance detail, vendor balance detail, sales by customer, sales by item, inventory valuation summary, transaction list by date, audit trail.

Trial balance and general ledger ship with the ledger in release 1.

## 15. Adapters

### 15.1 CLI

`bookflow <noun> <verb> [args] [flags]`. Global flags: `--company`, `--json`, `--dry-run`, `--reason`, `--source-ref`, `--idempotency-key`, `--as-token`, `--data-root`. `bookflow --help` and `bookflow <noun> --help` are generated from command definitions and include every flag, every output field, and every error code.

### 15.2 HTTP host

`bookflow serve --bind 127.0.0.1:8123`. Routes are `POST /companies/{company_id}/commands/{command_name}` with the input model as the JSON body and the output model as the response. Hub commands are `POST /commands/{command_name}`. Errors return status 400 for named errors with the error JSON as body, 401 for missing or bad token, 404 for `E_COMPANY_NOT_FOUND`, 409 for `E_VERSION_CONFLICT`, 500 for internal failure. `GET /openapi.json` is generated. Binding to a non-loopback address requires `--allow-network`, and the docs state that TLS termination is the deployer's job.

### 15.3 MCP server

`bookflow mcp --token <secret>`. One tool per command, named `bookflow_<noun>_<verb>`, with the input schema generated from the input model plus `company_id`, `reason`, `source_ref`, and `idempotency_key`. Tool descriptions are the command descriptions from the documentation. `bookflow_help` returns the documentation page for any command.

## 16. Documentation contract

The reader is an agent that has never seen Bookflow and cannot read the source. Every page states facts; none explains reasoning.

| Location | Content | Produced by |
|---|---|---|
| `README.md` | what it is, install, the one command to run, where the docs are | hand |
| `docs/cli/<noun>.md` | every command of that noun: purpose in one sentence, every flag with type and default, output fields, error codes, one example invocation and its JSON output | `bookflow docs generate`, from command definitions |
| `docs/schema/<table>.md` | every field with type, nullability, meaning, and references | `bookflow docs generate`, from models |
| `docs/concepts.md` | the command contract, context, concurrency rules, money, company selection, exit codes, stated declaratively | hand, verified by tests where possible |
| `docs/index.md` | the entry point; lists every page | generated |

A test regenerates the docs and fails when the result differs from the committed files.

## 17. Decision hierarchy

When two good things conflict, the earlier line wins.

1. Balanced books beat everything. A command that would unbalance the ledger fails, whatever else it would achieve.
2. Never lose history. A slower audit write beats a faster unaudited one.
3. Isolation beats convenience. A command that could reveal another company's existence is wrong even if it would be useful.
4. Correct output beats fast output. Fast beats pretty.
5. Explicit beats inferred. An ambiguous name, account, or date is rejected with a named error rather than guessed. The one exception is section 6.2's disjoint-field merge, which is reported, never silent.
6. Machine-readable on stdout beats friendly on stdout. Friendly on stderr beats machine-readable on stderr.
7. QuickBooks' name for a thing beats a better name.
8. When a rule is ambiguous, the interpretation that gives the human more information wins.

## 18. Budgets

| Target | Condition |
|---|---|
| Any list or show command returns in under 100 ms | 10,000 records in the list, SQLite on local SSD |
| Posting a 20-line transaction completes in under 50 ms | same |
| Trial balance over 100,000 lines returns in under 2 s | same |
| A company database with 100,000 transactions stays under 500 MB excluding attachments | |
| CLI cold start under 300 ms | Python 3.12, warm disk cache |
| Full test suite under 60 s | |

## 19. Out of scope for release 1, and beyond

Not in release 1: transaction forms other than journal entries, reports beyond trial balance and general ledger, work orders, time entries, scheduler, price levels, ship methods, inventory assemblies, GUI of any kind, rate fetching, email, bank feeds, encryption at rest.

Never in scope without a new design pass: payroll, multi-currency ledgers, non-US tax regimes.

## 20. Risks

| Risk | What it would look like |
|---|---|
| Adapter logic drift | HTTP and CLI accept different inputs for the same command. Guard: adapters are generated from the same definitions and a test calls every command through all three. |
| Silent merge surprises | A disjoint-field merge lands a change the caller did not expect. Guard: the output names the merged versions and the CLI warns. |
| SQLite on a shared drive | Corruption. Guard: refusal on network filesystems and the host-only rule. |
| Migration on an open file | Two processes migrate at once. Guard: an exclusive lock file per company during migration. |
| Docs go stale | An agent follows a page that no longer matches. Guard: generation plus the freshness test. |
| Attachment store bloat | Unlinked bodies accumulate. Guard: `company compact`. |
| Precision drift on rates | Repeated conversions compound rounding. Guard: convert once at posting, store the rate, never re-convert. |

## 21. Build order and the final checklist

The build order is the spec list in `design/intention.md`, rows 1 to 8. That list is release 1.

Release 1 is done when a stranger, given a fresh machine and the README, can: initialize a data root, create a company, add accounts and customers, attach a receipt image to a customer, post a balanced journal entry from the CLI, post another over HTTP from a second process, list accounts from an MCP client, see all of it in the audit log with correct actors and interfaces, and reproduce all of it on a second copy of the company directory.

Release 2 is forms (section 10.3) and reports (section 14). Release 3 is work orders, time, and the scheduler (section 13). The GUI is designed after release 2 against the HTTP host.
