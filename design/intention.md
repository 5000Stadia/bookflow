# Bookflow — intention

## What we're making

A multi-company accounting core modeled on QuickBooks Desktop, exposed first as a Python library and a CLI, so that a GUI, an HTTP host, or an AI agent through MCP can all post to the same books. In the human's words: "a CLI interface and package with a clear set of functions an AI could easily use", so that "a plumber with very little accounting knowledge knows to just send everything to their agent to post" and "next time they pull up their Bookflow gui they see that the entry they asked their agent to post is posted."

## What "good" means here

Picture the plumber at 9pm asking their agent to post today's receipts, then opening the GUI the next morning. Every line below is what has to be true for that person.

- An agent that has never seen Bookflow can read the docs and post a correct transaction on its first attempt, with no source reading and no guessing at syntax.
- Every command works identically from the CLI, the MCP adapter, the HTTP host, and the browser workbench, with the same inputs, outputs, and errors.
- Every write records who did it, through which interface, on whose behalf, and why. The owner can list what their agent did this week from any surface.
- Two writers never silently destroy each other's changes. A rejected write says who changed the record, how long ago, and which fields.
- The books always balance. No command can post an unbalanced entry or post into a closed period.
- A user or agent with access to one company cannot see that another company exists.
- Money is exact. No amount is ever a float.
- A company file can be copied to another machine and opened there with nothing lost.

**Anchor:** QuickBooks Desktop (Pro/Premier). The human runs it. The bar is its list design, its transaction forms, and its reports: what it lets a bookkeeper do without thinking about debits and credits, and what its audit trail and closing date protect.

## What everything passes through

- **Declarative ground truth.** Every document in this repo is written for an agent with no prior understanding of the project. It states what exists, what each command takes and returns, and what each table holds. It never explains philosophy, history, or reasoning. Enforced: schema and command references are generated from code and a test fails when they are stale.
- **One command contract.** Every operation is a named command with a typed input, a typed output, a stable error code, and a JSON form. Adapters translate; they never contain logic.
- **QuickBooks vocabulary.** Lists, items, jobs, classes, terms, memorized transactions. When Bookflow names a thing, it uses the QuickBooks Desktop name.
- **Integer minor units plus currency code** for every amount, everywhere.
- **The demo company exercises everything.** A change that adds a table or command extends the demo seed in the same change, and the demo is what testing runs against.

## What it must never do

- Never delete a posted transaction or an audit row. Transactions are voided or reversed.
- Never post an entry whose debits and credits differ.
- Never post into a period closed by the company's closing date.
- Never return, list, or reveal a company the acting principal has no membership in.
- Never open a company database over a network file share. Multi-machine access goes through the host process.
- Never store an amount as a floating-point number.
- Never send data outside the machine. No telemetry, no rate fetching, no email, unless a command explicitly does so and the docs say it does.

## Where it goes

Local git repository at `/home/k/Projects/Bookflow`. Public GitHub remote to be created by the human under the 5000Stadia organization when they choose; until then, nothing is pushed. `design/` travels with the code. `notes/` never does. The human publishes; the Navigator asks each time.

Irreversible acts registry: pushing to a remote (asked), deleting any company data directory (asked), spending money (none authorized).

## The spec list

| # | What to build now | What done looks like |
|---|---|---|
| 1 | Package skeleton, data root, hub database, users, and company rollout. `bookflow init` creates the data root and a local owner. `bookflow company new` runs the rollout (legal name, FEIN, address, phone, email, fiscal year start, home currency, income tax form) and creates a company folder named after the company holding `bookflow-company.toml`, `company.db`, `attachments/`, `backups/`, and `exports/`. `bookflow company list`, `company show`, `company rename`, `company attach`. Company selection precedence: `--company`, `BOOKFLOW_COMPANY`, saved default. | On a fresh machine: init, create a company, list it, show its info, all with `--json`. Two companies with the same name get distinct folders. The company folder, copied elsewhere and attached, opens with nothing lost. |
| 2 | Command contract, audit log, versioning, and concurrency rules. Context object on every command (actor, on_behalf_of, interface, client, session, request id, idempotency key, reason, directive, source_ref). Directives as standing instructions cited by code. Append-only audit table storing after-snapshots grouped by event, before-state derived on read, snapshots compressed over 512 bytes. Row versioning, versioned and blind writes, disjoint-field merge, recent-activity flag, advisory presence records. `bookflow audit list` and `audit show`. | Updating company info twice from two sessions produces the conflict, merge, and warning behaviors as specified in the blueprint, and the audit list shows both writes with actor and interface. An agent post without reason or directive is rejected; one citing a directive shows the directive text in `audit show`. Audit tables stay under 1.5 times live data on the fixture. |
| 3 | HTTP host and workbench. `bookflow serve` exposes every registered command over HTTP with the same inputs, outputs, and errors as the CLI, serves a browser login and company picker, and serves generated workbench pages: a table for every list, a field view with activity feed for every record, a form for every command, and the audit page. Loopback by default, `--allow-network` otherwise. | In a browser: log in, pick the company, update company info from a generated form, see the result and the audit event with interface `http`. A second process posts the same update over HTTP with a bearer token. Every command registered so far has a working page without any page-specific code. |
| 4 | Generated documentation. `docs/schema/` from the models and `docs/cli/` from the registry, each page stating fields, types, flags, outputs, and error codes. A test fails when generated docs differ from the committed ones. README gains the run command. | An agent given only `docs/` can run the row 1 and row 2 commands correctly without reading source. |
| 5 | Lists. Chart of accounts with QuickBooks account types, numbers, hierarchy, and a seeded default chart chosen at rollout. Customers with jobs, vendors, employees, other names, items with all QuickBooks item types, classes, terms, payment methods, sales tax codes and sales tax items. Create, update, show, list, activate, deactivate on each. Names unique within a list. Optional link between a customer and a vendor. | Every list has all operations from the CLI with JSON output and from the workbench, hierarchy works to three levels, deactivation hides from default lists, and the docs from row 4 cover every list. |
| 6 | Notes and attachments on any record. Polymorphic notes table, content-addressed attachment store with a link table, and `bookflow activity <record>` returning the merged chronological feed of notes, attachment events, and audit events. Workbench record pages show the feed and accept note and file uploads. | A note and a PDF can be attached to a customer, an account, and the company itself from the workbench and the CLI, and the feed lists them in time order with actors. |
| 7 | Identity and isolation. Hashed revocable tokens for agents, each token bound to the human it acts on behalf of, memberships with roles (owner, admin, standard, readonly), permission checks on every command, company listing limited to memberships, workbench buttons limited by role. | An agent token with membership in company A cannot list, open, or infer company B. A readonly member cannot write from the CLI, HTTP, or the workbench. Tests prove all three. |
| 8 | General ledger. Transactions as header plus lines, journal entry command, posting rules, void, closing date, and the trial balance and general ledger reports. Money type with home currency, exchange rate table, and foreign-tagged entry converting at posting. | A balanced journal entry posts from the workbench and the CLI, an unbalanced one is rejected, a void reverses it, posting before the closing date is rejected, and the trial balance balances. A JPY-tagged line posts in home currency and keeps the original amount and rate. |
| 9 | MCP adapter. An MCP server exposes every registered command as a tool with schemas generated from the registry, plus `bookflow_help` returning any command's documentation page. | An MCP client lists accounts and posts a journal entry citing a directive, and the audit page shows interface `mcp` with the directive text. |

**Next ID:** 10
