# Bookflow — intention

## What we're making

A multi-company double-entry accounting core for small businesses, exposed first as a Python library and a CLI, so that a GUI, an HTTP host, or an AI agent through MCP can all post to the same books. In the human's words: "a CLI interface and package with a clear set of functions an AI could easily use", so that "a plumber with very little accounting knowledge knows to just send everything to their agent to post" and "next time they pull up their Bookflow gui they see that the entry they asked their agent to post is posted."

## What "good" means here

Picture the plumber at 9pm asking their agent to post today's receipts, then opening the GUI the next morning. Every line below is what has to be true for that person.

- An agent that has never seen Bookflow can read the docs and post a correct transaction on its first attempt, with no source reading and no guessing at syntax.
- Every command works identically from the CLI, the MCP adapter, the HTTP host, and the browser workbench, with the same inputs, outputs, and errors.
- Every write records who did it, through which interface, on whose behalf, and why. The owner can list what their agent did this week from any surface.
- Two writers never silently destroy each other's changes. A rejected write says who changed the record, how long ago, and which fields.
- The books always balance. No command can post an unbalanced entry or post into a closed period.
- A user or agent with access to one organization cannot see that another organization or its companies exist.
- Money is exact. No amount is ever a float.
- A company file can be copied to another machine and opened there with nothing lost.
- Every field, option, and behavior the anchor offers on the equivalent list, form, or report exists here. Put side by side, nothing is missing.

**Anchor:** The desktop accounting application the human runs daily; the human names it and runs the comparison. The bar is its list design, its transaction forms, and its reports: what it lets a bookkeeper do without thinking about debits and credits, and what its audit trail and closing date protect.

## What everything passes through

- **Declarative ground truth.** Every document in this repo is written for an agent with no prior understanding of the project. It states what exists, what each command takes and returns, and what each table holds. It never explains philosophy, history, or reasoning. Enforced: schema and command references are generated from code and a test fails when they are stale.
- **One command contract.** Every operation is a named command with a typed input, a typed output, a stable error code, and a JSON form. Adapters translate; they never contain logic.
- **Conventional bookkeeping vocabulary.** Lists, items, jobs, classes, terms, memorized transactions. When Bookflow names a thing, it uses the name a bookkeeper already knows.
- **Integer minor units plus currency code** for every amount, everywhere.
- **The demo company exercises everything.** A change that adds a table or command extends the demo seed in the same change, and the demo is what testing runs against.

## What it must never do

- Never delete a posted transaction or an audit row. Transactions are voided or reversed.
- Never post an entry whose debits and credits differ.
- Never post into a period closed by the company's closing date.
- Never return, list, or reveal an organization or company the acting principal has no membership in.
- Never open a company database over a network file share. Multi-machine access goes through the host process.
- Never store an amount as a floating-point number.
- Never send data outside the machine. No telemetry, no rate fetching, no email, unless a command explicitly does so and the docs say it does.

## Where it goes

Public GitHub repository `5000Stadia/bookflow`, branch `main`. `design/` travels with the code. `notes/` never does. Pushing is standing authority at milestones that are implemented and tested: a passed spec row, or a design change the human has agreed to.

Irreversible acts registry: pushing to `main` (standing, at tested milestones), force-pushing or rewriting published history (asked), deleting any company data directory (asked), spending money (none authorized).

## The spec list

| # | What to build now | What done looks like |
|---|---|---|
| 1 | Package skeleton, command registry, data root, hub database, users, and company rollout. `bookflow init` creates the data root, the system user, and a hub-admin owner mapped from the OS login. `organization new`, `organization list`, `organization show`, `organization rename [--move]`. `bookflow company new` runs the rollout inside an organization (legal name, FEIN, address, phone, email, fiscal year start, home currency, income tax form, interactive or by options) and creates a company folder named after the company inside the organization's folder, holding `bookflow-company.toml`, `company.db`, `attachments/`, `backups/`, and `exports/`, with the creator as owner member. `company list`, `company show`, `company rename [--move]`, `company use`, `company attach`, `company detach`, `demo reset`, `upgrade`, `hub audit list`, `hub audit show`. Company selection precedence: `--company`, `BOOKFLOW_COMPANY`, saved default. Every write accepts `--dry-run`. Filesystem locality and the data-root lock per blueprint 3.2. | On a fresh machine: init, create an organization, create a company in it, list it, show its info, rename it with and without moving the folder, all with `--json` and with identical results through the library. Two companies in one organization whose names sanitize to the same folder name get distinct folders. A member of one organization cannot list, open, or infer another organization or its companies. A company folder copied to a second data root and attached there shows the same info, the same folder contents, and the same creator name. A second process running any command on the same data root while one is in progress fails with a named error that names the holder. A folder on a network filesystem is refused. Every error path has a named code and the same code through the library and the CLI. |
| 2 | Command contract, audit log, versioning, and concurrency rules. Context object on every command (actor, on_behalf_of, interface, client, session, request id, idempotency key, reason, directive, source_ref). Directives as standing instructions cited by code. Append-only audit table storing after-snapshots grouped by event, before-state derived on read, snapshots compressed over 512 bytes. Row versioning, versioned and blind writes, disjoint-field merge, recent-activity flag, advisory presence records. company-scoped `audit list`, `audit show`, and cursor-based `audit tail`, and `hub audit tail`. | Updating company info twice from two sessions produces the conflict, merge, and warning behaviors as specified in the blueprint, and the audit list shows both writes with actor and interface. An agent-context write without reason or directive is rejected; one citing a directive shows the directive code and text in `audit list`. Audit tables stay under 6 times live data, in aggregate, on the fixed fixture. |
| 3 | HTTP host and workbench. `bookflow serve` exposes every registered command over HTTP with the same inputs, outputs, and errors as the CLI, holds the data-root lock while it runs and runs reads concurrently and every write on one writer thread, serves a browser login (password set with `user set-password`, session cookies) and company picker, bearer tokens for programs, generated workbench pages (a table for every list, a field view with presence and audit for every record, a form for every command carrying `expected_version`, the audit page), a server-sent-events endpoint for the event feed, and the loopback hand-off by which a CLI or library call on the host's machine forwards to it. Loopback by default, `--allow-network` otherwise. | In a browser: log in, pick the company, update company info from a generated form, see the result and the audit event with interface `http`. A second process posts the same update over HTTP with a bearer token. Every command registered so far has a working page without any page-specific code. A subscriber to the events endpoint receives the update as it happens and resumes from its cursor after a disconnect. |
| 4 | Generated documentation. `docs/schema/` from the models and `docs/cli/` from the registry, each page stating fields, types, flags, outputs, and error codes. A test fails when generated docs differ from the committed ones. README gains the run command. `docs/agent-guide.md` walks an agent from authentication to a post against the demo company, with every example verified by a test. A fresh agent given only the guide and a list of test records posts them all correctly, then reports anything unclear or anything it did not reach for first; a second fresh agent from a different provider repeats the test; both reports are addressed in the guide. | An agent given only `docs/` can run the row 1 and row 2 commands correctly without reading source. |
| 5 | Lists. Chart of accounts with standard account types, numbers, hierarchy, and a seeded default chart chosen at rollout. Customers with jobs, vendors, employees, other names, items with all standard item types, classes, terms, payment methods, sales tax codes and sales tax items, customer types, vendor types, job types, sales reps, ship methods, customer messages, price levels, units of measure, and custom field definitions and values. Each list carries the complete field set of its blueprint section, and each blueprint section is a full inventory of the anchor's equivalent before the row is built. Create, update, show, list, activate, deactivate on each. Names unique within a list. Optional link between a customer and a vendor. `undo <event>` for list-record events. | Every list has all operations from the CLI with JSON output and from the workbench, hierarchy works to three levels, deactivation hides from default lists, and the docs from row 4 cover every list. |
| 6 | Notes and attachments on any record. Polymorphic notes table, content-addressed attachment store with a link table, and `bookflow activity <record>` returning the merged chronological feed of notes, attachment events, and audit events. Workbench record pages show the feed and accept note and file uploads. | A note and a PDF can be attached to a customer, an account, and the company itself from the workbench and the CLI, and the feed lists them in time order with actors. |
| 7 | Identity and isolation. `user add`, `membership grant` and `revoke`, hashed revocable tokens for agents, each token bound to the human it acts on behalf of, memberships with roles (owner, admin, standard, readonly), permission checks on every command, company listing limited to memberships, workbench buttons limited by role. | An agent token with membership in organization A cannot list, open, or infer organization B or its companies; a token with membership in one company of A cannot see A's other companies. A readonly member cannot write from the CLI, HTTP, or the workbench. Tests prove all three. |
| 8 | General ledger. Transactions as header plus lines, journal entry command, posting rules, void, closing date, and the trial balance and general ledger reports. Money type with home currency, exchange rate table, and foreign-tagged entry converting at posting. | A balanced journal entry posts from the workbench and the CLI, an unbalanced one is rejected, a void reverses it, posting before the closing date is rejected, and the trial balance balances. A JPY-tagged line posts in home currency and keeps the original amount and rate. |
| 9 | MCP adapter. An MCP server exposes every registered command as a tool with schemas generated from the registry, plus `bookflow_help` returning any command's documentation page. | An MCP client lists accounts and posts a journal entry citing a directive, and the audit page shows interface `mcp` with the directive text. |

**Next ID:** 10
