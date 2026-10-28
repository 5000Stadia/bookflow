# Bookflow roadmap

Double-entry accounting for small businesses, where people in the browser and their AI agents post to the same books.


## Vision

Bookflow is the books a small business owner and their AI assistant keep together. The plumber tells their agent at 9 pm to bill today's jobs and record the checks; the next morning they open Bookflow in the browser and find it all there, correct, signed with who did it and for whom, and they carry on from where the agent stopped. Neither one ever has to tidy up after the other.

It does everything a bookkeeper expects from the desktop accounting product they already know, with the same lists, forms, reports and words, so nothing is missing when the two are put side by side. It is open source and runs on the owner's own machine: no subscription, and no data leaving unless they send it.

Every finished milestone keeps these:
- **One set of books, many hands.** The browser, an AI agent, the command line and code all do the same things the same way, and each can see and continue the others' work.
- **An agent gets it right the first time.** A fresh agent with only Bookflow's own help does ordinary business work correctly, without reading code or guessing.
- **The books can be trusted.** Money is exact, the books always balance, history is never erased, closed periods stay closed, and every change says who made it, how, for whom and why.
- **Plain to use.** It reads in a bookkeeper's words, on a desk or a phone, and stays quick on real-sized books. It shows what this business uses: deeper layers (pick and pack, sites, time) stay out of the way until the owner turns them on, and the home screen draws the business's flow as a clear, good-looking picture, on desk and phone, that each person can tailor.

Further out, and open for now: the same core grows into the rest of a small business's system (customers and follow-ups, inventory and assembly, shipping, letters, marketing), each part built the same way.

## M1 — Foundation

- [x] R1 Company folders, schema migrations with verified backups (spec row 1) — design/architecture.md
- [x] R2 Versioned writes, idempotency and the audit log (spec row 2) — design/blueprint.md §6–7
- [x] R3 Host process, HTTP routes, tokens and the browser workbench (spec row 3) — design/blueprint.md §15
- [x] R4 Generated command and schema reference docs (spec row 4) — design/blueprint.md §16
- [x] R5 Notes, attachments and the activity feed (spec row 6) — design/blueprint.md §12
- [x] R6 Fractional quantities and live numeric entry (spec row 20) — design/numeric-entry.md
- [x] R7 Stable document identities, immutable revisions, attributed corrections (spec row 21) — design/architecture.md
- [x] R8 Authority revalidated at execution (spec row 23) — design/permission-resolution.md

## M2 — V1 accounting product

- [x] R9 Lists: chart of accounts, customers and jobs, vendors, items, supporting lists (spec row 5; every done-definition clause checked 2026-09-27) — design/specs/5-lists.md
- [x] R11 General ledger, register entry, closing date, foreign-tagged entry (spec row 8; every done-definition clause checked 2026-09-27) — design/specs/8-general-ledger.md
- [x] R12 MCP adapter and agent/GUI cooperation (spec row 9; every done-definition clause checked 2026-09-27) — design/specs/9-mcp-adapter.md
- [x] R73 repair the stale tests the acceptance checks found across rows 5, 7, 8, 9, 22 and 24 — notes/NOW.md
- [x] R75 investigate three suspected defects: deposit coordinate work E_INTERNAL "Unsupported source aggregate identity", a progress-billing schema constraint mismatch that differs between runs, and Find rates answering with a JSON flash on the Overview — notes/stale-tests-20260927.md
- [x] R76 every read-only command shows its result on its own page (payment calculate/suggest/invoices, reconcile candidates/preview, register calculate, sales-tax liability, deposit sources/items, activity), never a JSON notice on the Overview — notes/NOW.md
- [x] R15 Customer work, service sales, progress and work billing — design/customer-work.md
- [x] R16 Purchasing, receiving with shipping allocation, bills and vendor payments — design/architecture.md
- [x] R17 Deposits and bank reconciliation — design/architecture.md
- [x] R18 Credits, refunds and refund history — notes/V1-COMPLETION-AUDIT-20260917.md
- [x] R19 Per-user transaction deletion with preserved history — design/transaction-deletion.md
- [x] R20 Printed documents and whole filtered report printing — design/report-printing.md
- [x] R21 Company-wide cash or accrual reporting — design/specs/cash-basis-company-reporting.md
- [x] R22 Permission performance package — notes/permission-package-map-20260917/DESIGN.md

## M3 — V1 release and acceptance

- [x] R23 Finite V1 closeout: integration, evidence ledger, preserving LAN deployment — notes/V1-COMPLETION-AUDIT-20260917.md
- [x] R24 UI overhaul for desktop and phone — design/specs/ui-overhaul.md
- [x] R25 Company-local dates and mobile submit feedback — design/specs/ui-submit-dates.md
- [x] R26 V1 polish: menu, icons, login feedback, version guard, public docs, drifted tests — notes/NOW.md
- [x] R27 GitHub main current with the live release (d85e43f) — notes/status.sh

## M3.2 — Interface round (phone and desktop audit)

- [x] R31 Human-directed improved UI (phone and desktop audit round, R55–R65) — design/V2-ROADMAP.md item 1
- [x] R55 R31 quick fixes — report date order, focus after submit, menu Register to the real register, ☰ in the phone header, tidy Overview tiles (approved 2026-09-27) — notes/mobile-audit-response-20260927.md
- [x] R56 R31 the form first on document forms, recent documents as compact rows (approved 2026-09-27) — notes/mobile-audit-response-20260927.md
- [x] R64 R31 a quieter document form — sticky save bar, Class and unit columns only when used, picker and date tidying, recording details folded (approved 2026-09-27) — notes/mobile-audit-response-20260927.md
- [x] R57 R31 reports open with their numbers — one-line filters, date chips, headline figure, statement-shaped layout on desktop (approved 2026-09-27) — notes/mobile-audit-response-20260927.md
- [x] R58 R31 lists and filters — pinned search as you type, filters apply themselves, phone filter sheet (approved 2026-09-27) — notes/mobile-audit-response-20260927.md
- [x] R60 R31 readable money and dates across all screens, exact values kept in exports (approved 2026-09-27) — notes/mobile-audit-response-20260927.md
- [x] R59 R31 ledger tables and money rows — right-aligned figures, currency in the header, fewer default columns (approved 2026-09-27) — notes/mobile-audit-response-20260927.md
- [x] R63 R31 an Overview with numbers — figures strip, needs attention, recent activity (approved 2026-09-27) — notes/mobile-audit-response-20260927.md
- [x] R65 R31 live totals on documents from the core preview as you type (approved 2026-09-27) — notes/mobile-audit-response-20260927.md
- [x] R61 R31 sections open on their list with + New; types under Settings; a command finder for every action (approved 2026-09-27) — notes/mobile-audit-response-20260927.md
- [x] R62 R31 the account register on a phone as a list of entries with an add sheet (approved 2026-09-27) — notes/mobile-audit-response-20260927.md

## M3.5 — V1.5: finished and proven

- [x] R10 Identity, memberships, roles and agent tokens (spec row 7; checked 2026-09-27, gaps R66–R68 fixed and walked through by hand) — design/specs/7-identity-isolation.md
- [x] R66 R10 membership grant/revoke over MCP stop revealing whether a username exists — design/specs/7-identity-isolation.md
- [x] R67 R10 revocation always suspends agents: new installs start in the current permission mode, agent commands refuse in the legacy mode — design/permission-resolution.md
- [x] R68 R10 agent commands: create an agent, assign and remove its principals, reauthorize it, on every surface — design/specs/7-identity-isolation.md
- [x] R13 Customer payments and invoice settlement (spec row 22; checked 2026-09-27: ledger, corrections, concurrency, permissions and agent/human continuation hold; R69–R70 remain) — design/specs/22-customer-payments.md
- [x] R69 R13 recover a payment whose save response was lost: the recovery button works in the browser — design/specs/22-customer-payments.md
- [x] R70 R13 the blind fresh-agent payment trial with its interview, fixes and one retest; the payment workflow doc gains the MCP and browser journeys. Passes when a fresh agent, with only its own help and MCP and no source reading, completes every task correctly on its first attempt and its interview turns up nothing material; after fixes, one retest decides it — notes/blind-trials-20260927.md
- [x] R14 Sales-tax calculation policies (spec row 24; checked 2026-09-27: policies, arithmetic, migration and every surface hold; R71–R72 remain) — design/specs/24-sales-tax-policy.md
- [x] R71 R14 legacy partly billed work stays billable after upgrade — design/specs/24-sales-tax-policy.md
- [x] R72 R14 the blind-agent sales-tax trial with its interview, fixes and one retest. Passes when a fresh agent, with only its own help and MCP and no source reading, completes every task correctly on its first attempt and its interview turns up nothing material; after fixes, one retest decides it — notes/blind-trials-20260927.md
- [x] R77 concurrent requests intermittently fail with AdmissionCancelled "publication admission changed" reported as E_INTERNAL (absorbs R28, the same bug): a typed retryable error or retry, never E_INTERNAL; the remaining failing browser tests with it — notes/NOW.md
- [x] R29 revoked streams: a revoked stream closes with no further data (the contract, witnessed in c116267); left: disconnect cleanup faster than the 15 s keep-alive — notes/NOW.md
- [x] R84 the agent can record bank deposits: `deposit sources` and `deposit post` refuse a standard agent with a bare E_PERMISSION while the catalog asks only ordinary access — notes/blind-trials-20260927.md
- [x] R79 invoices and other sales use the company's normal sales tax item when none is named, as the browser does; `use_defaults` resolves it, and the error says when no default exists (the trials' agents guessed a different tax item) — notes/blind-trials-20260927.md
- [x] R85 recording a customer payment warns when the same customer and check reference are already on file — notes/blind-trials-20260927.md
- [x] R86 help and errors an agent can act on first time: a worked example in each command's help, errors that name the reason and the valid fields (permission refusals, the reconcile opening step, plain money errors, reason needed even for previews), and findable oldest-first application, sales-tax owed, open invoices and payment methods — notes/blind-trials-20260927.md
- [x] R83 the demo sells stock today: demo documents dated after today move into the past so demo items are on hand — notes/blind-trials-20260927.md
- [x] R87 connecting an agent: the one-time token secret shown plainly with a Copy button, and a New agent button on the Agents panel (the person's yes on gate g9ed945) — notes/manual-agent-walkthrough-20260927.md
- [x] R80 a fresh agent that has never seen Bookflow runs a week of the plumber's ordinary work through MCP alone (invoice, take a payment, enter a bill, reconcile, run the month's report). Passes when, with only its own help and MCP and no source reading, it completes every task in the week correctly on its first attempt and its interview turns up nothing material; after fixes, one retest decides it — notes/blind-trials-20260927.md
- [x] R74 permission checks on current-mode installs cost about 3x; the demo reset through MCP exceeds the client time limit: reuse the permission snapshot only behind a fresh in-transaction version check, and nothing the product does times out (including a large company backup or restore forwarded from the CLI, which waits at most 30 s) — design/permission-resolution.md
- [x] R88 agent administration reads like the rest of the product: user and membership lists page; the finder reaches users, tokens, agents and organizations; names and readable dates instead of raw ids, timestamps and codes on agent and token pages; the principal picked from a list; plain errors; document history says "for k, via agent" — notes/V1.5-resort-draft.md
- [x] R89 agents and users can be deactivated (the gap recorded when R68 shipped) — notes/V1.5-resort-draft.md
- [x] R82 negative amounts in parentheses as a company setting — notes/V1.5-resort-draft.md
- [x] R90 result tables show record names, not raw ids — notes/V1.5-resort-draft.md
- [x] R91 the 2000-span sales-tax forecast recovery works in the browser without timing out — notes/V1.5-resort-draft.md
- [x] R92 Overview activity and audit summaries read in plain words with the actor's name — notes/V1.5-resort-draft.md
- [x] R93 payment results carry a short summary: invoices paid, what is still due, unapplied credit labelled as credit — notes/V1.5-resort-draft.md
- [x] R94 deposit, payment and bill-payment lists in the ledger style, and the wide aging, open-invoice and unpaid-bill tables as phone cards — notes/V1.5-resort-draft.md
- [x] R95 small form polish: invoice and sales take today's date when none is given, as the browser does; the invoice form shows the default sales tax item before posting; "Clear Description" only when useful; register query dates and parameters consistent with other queries — notes/V1.5-resort-draft.md
- [x] R96 diagnose and fix the older failing tests (FIRST the real defect that invoice and payment deletion is refused on activated installs even after an explicit grant; the concurrent recovery apply that errors instead of replaying; the demo-figure tests stale after R83; the ~30 tests pinned to a migration head or catalog; the error matrix missing rows for card-charge delete, check delete and item-receipt commands, the zero-value-items co45 upgrade test, the service-sales and work-billing demo tests pinned to stale whole-company figures, the Reference Plumbing Co demo option still fixed to 2026 dates, the 20 deposit authority tests that need the current permission rules, sales-tax-code list E_USAGE, delete admission, payment recheck, statement-charge surfaces, receivable report staleness, audit reachable when activated, history redirects, deleted-deposit page, routed-command delete forms): fix what is real, repair what is stale — notes/V1.5-resort-draft.md
- [x] R97 payment calculate with auto-calculate off and no amount returns the field error row 22's spec describes — design/specs/22-customer-payments.md
- [x] R98 notes/status.sh reports commits behind and the command count correctly (tooling) — notes/status.sh
- [x] R99 bug sweep, one pass: every known defect in the named sources (notes, trial reports, current test runs, existing Codex reviews, TODO/FIXME) that matters to someone using Bookflow (the plumber, their agent or the books), listed as its own item or folded into one. Purely internal or theoretical faults no user would meet are dropped or go to the future list, and working choices are not re-examined. Once the list is written the sweep is closed: no further hunting or new audits for V1.5; bugs met while doing V1.5 work are still fixed Result: 24 defects still present (9 new, 15 already covered) — notes/bug-sweep-20260928.md
- [x] R100 tests share one demo company per run: built once, each test gets its own copy, so setup takes seconds instead of minutes and the full suite is practical. One bounded tooling change, built after the current builders finish; nothing further on test methodology for V1.5 — notes/NOW.md
- [x] R131 everyday reports the anchor has: customer and vendor balance summary and detail, open purchase orders, purchases by vendor and by item, deposit detail, and a transaction list by date — notes/V1.5-scope-sort-draft.md
- [x] R132 early-payment discounts taken inside the terms window when receiving a customer payment or paying a bill, posted to a discount account (accounting change approved by the person) — notes/V1.5-scope-sort-draft.md
- [x] R133 back up and restore from the product: a verified, portable copy of the company, and restoring or attaching one — notes/V1.5-scope-sort-draft.md
- [x] R134 1099 vendor summary report (report only; filing stays in the future list) — notes/V1.5-scope-sort-draft.md
- [x] R135 card credits (a refund onto a credit card) and a plain "Credit Card" payment method — notes/V1.5-scope-sort-draft.md
- [x] R136 reconciling a credit card account against its statement works as a bank reconciliation does (verify; fix only if missing) — notes/V1.5-scope-sort-draft.md
- [x] R137 selling stock not yet on hand goes through with a warning: costed provisionally at the average cost (else the item's purchase cost, else zero with a warning), trued up by an entry dated at the receipt that covers the shortfall and linked to each sale it corrects (accounting change approved by the person) — notes/V1.5-scope-sort-draft.md
- [x] R140 lists show real figures: vendor open balance and item quantity on hand (today always 0) — notes/bug-sweep-20260928.md
- [x] R141 the audit trail and activity respect a member's explicit capability denies — notes/bug-sweep-20260928.md
- [x] R142 the last two unplain agent errors: an over-long reason, and a deposit over 200 rows — notes/bug-sweep-20260928.md
- [x] R143 CLI lists print curated default columns (decision D13), not every column — notes/bug-sweep-20260928.md
- [x] R144 time activities have browser pages like other documents — notes/bug-sweep-20260928.md
- [x] R145 report CSV export works over the CLI and MCP as in the browser — notes/bug-sweep-20260928.md
- [x] R146 editing or voiding a transaction after its reconciliation is finished is fenced (warn plainly as QuickBooks does; the reconciliation report shows the difference; approved on gate g8b3cb3) — notes/bug-sweep-20260928.md
- [x] R147 discount, subtotal and group items and percentage charges work on sales as QuickBooks does: a subtotal sums the lines above, a percentage charge or discount applies to the line or subtotal above, a discount posts to its item's account and reduces taxable sales by its tax code, a group expands into its members (approved on gate g8b3cb3) — notes/bug-sweep-20260928.md
- [x] R157 no page carries bloat: the "All fields and technical details" section loads only when opened (a long-quote invoice was ~1.5 MB and 32 s), with a page-size limit test so it can't creep back
- [x] R30 the person's walkthrough of the live app on desktop and phone, offered as one plain `colony ready` with what to try — notes/V1-HUMAN-WALKTHROUGH-20260917.md
- [x] R81 release v1.5: no known bugs, the full test suite passes clean with nothing skipped as flaky, docs current (the README says demo dates follow the reset day), the demo resets cleanly, tag v1.5. Reviews of V1.5 changes: fix what is material, then move on after one re-review. The bug sweep (R99) closes when its one-pass list is written — README.md

## M5 — v1.6: ready for real use

Kabe can keep a real company's books in Bookflow with their AI agent. Done when one real month has been kept
in Bookflow alongside the old books: Kabe reconciled the bank and set the closing date, the two agree, and
neither Kabe nor the agent had to redo the other's work (pause for Kabe's approval). Shaped by the outside
review cb5fcd7 (gate g96052d).

- [x] R161 the audit log is protected like the ledger: audit rows cannot be updated or deleted in company or hub databases (append-only enforced in storage, not only by description)
- [x] R162 a closing date set in the future is caught: the change warns that it stops all posting until that date, and the setup screen says so
- [x] R163 an agent cannot quietly make the books balance: unexplained adjustments into reconciled accounts, suspense or opening-balance equity are surfaced for the owner (shaped by the R164 study)
- [x] R164 study: how AI agents keeping books over many months go wrong (AccountingBench and newer), which of those failures Bookflow's guards already stop, and which slip through — notes/research/
- [x] R158 reconciliation "mark all": tick every item up to the statement date in one step (company/reconciliation_queries.py has mark_all; needs a command, example, MCP mapping and a GUI witness)
- [x] R159 a receipt applied across hundreds of invoices previews and pages quickly: at 403 invoices the invoice-correction preview takes ~25 s and each settlement page ~8 s (answers correct; each page recomputes the whole receipt)
- [x] R160 the standard company profile's version moves when its contents change (R135 added a Credit Card payment method; version still 1)
- [x] R156 Scheduled backups: the host backs companies up on a schedule to a place no agent can write, keeps the last few copies, and one restore is rehearsed
- [x] R166 move a real company in: at a period boundary the agent brings in lists, opening balances and open invoices and bills from the old books (QuickBooks Desktop exports), with a dry run, saved mappings, stable outside ids and an exception report; a tie-out shows the opening trial balance and receivable and payable agings match the old books to the cent
- [x] R167 bank statements come in for reconciliation: import CSV, OFX and QFX statements and match them to entries (live feeds stay in R108)
- [x] R168 the real books are protected: the closing date and user roles are set only by a person, never by an agent; the bookkeeping agent works without admin; the real company lives apart from demo and test data; hash-linked audit checkpoints go with each backup so a rewritten history or a rollback is detected
- [x] R175 adjust sales tax due, as the anchor does: a document that adds to or reduces what is owed to one sales tax agency (date, amount, offset account), shown under that agency in the liability report and settled by sales-tax pay; the move-in uses it for an opening sales tax balance when the source has one agency
- [x] R170 fit check on a realistic fake company (Kabe: 'numbers are numbers, you can just make a bunch of fake to test'): three months of a small trade business's records (old-books exports at a cutover, bank and card statements, the week-by-week paperwork) with an answer key; each kind of transaction marked does it, workaround or missing by replaying it through Bookflow; then an agent keeps one month of it blind, reconciles it to the statement and a person closes it
- [x] R173 an "Ask My Accountant" home for what the agent isn't sure about: Uncategorized Expense and Income accounts, `account uncategorized`, counted on the Overview (gate gad7a1a)
- [x] R174 a person may finish a statement that won't tie with a labelled adjustment to Reconciliation Discrepancies; agents never can (gate gad7a1a)
- [x] R176 a bounced customer check in one step, as the anchor's Record Bounced Check: the receipt's invoices reopen, the bank's fee comes out of the bank to an expense, and a charge back to the customer is billed (gate g55f9e5)
- [x] R177 write off a bad debt without cash, as the anchor does: receive payment for 0.00 with the open balance taken as a discount or credit to a Bad Debt account, by a person or an agent with a reason (gate g55f9e5)
- [x] R178 vendor credits with item lines: returning stock to the supplier is one document that takes the items out at the credited cost (gate g55f9e5)
- [x] R179 the move-in brings the rest of the old books: uncleared bank and card items and the last reconciliation (so the first one certifies), customers' sales tax items, the 1099 totals paid so far this year, and undeposited checks as receipts waiting to be deposited (gate g55f9e5)
- [ ] R165 fixes from Kabe's live use of v1.5 and v1.6, as they arrive. Parked 2026-10-16: Kabe has no plan to use it for real now ("indefinitely parked unless there is some beneficial internal testing")

## M4 — After V1.5: concepts to develop

- [ ] R172 a local agent: rerun the week-of-work trial on open-weight models served on a small business's own hardware, classify their failures, and test interface changes (typed everyday tools, shorter help and results) that let a smaller model pass without hurting hosted ones On hold: Kabe 2026-10-22 "Not now" (large downloads, no GPU).
- [x] R151 Restore a deleted document from its preserved history
- [x] R117 Merge duplicates: combine duplicate customers, vendors, items or accounts and move every reference to the survivor. (Customers and vendors 2026-10-09; accounts and items 2026-10-22, with refusals for stocked items and settlement accounts: design/list-merge.md)
- [x] R180 Quick on real-sized books: built-in performance logging and tracking (timings per command, recorded and reported) to optimise against; measure everyday commands on a company with years of history, and bring posting, deposits and reconciliation of 1,000+ rows down to seconds (Kabe 2026-10-22: "Add and build. We should have a smart performance logging and tracking we can optimize the system against").
- [x] R101 Sales orders: a non-posting customer order that reserves stock, invoices partly as items ship, tracks backorders, and can raise a purchase order for shortfalls.
- [x] R102 Reservation-aware stock: stock status shows on hand, committed to open sales orders, on order and available, so buying decisions see real demand.
- [x] R103 Assembly builds: building a finished item from its bill of materials consumes the components and moves their cost into the assembly.
- [x] R104 Advanced inventory: multiple sites and bins, transfers, serial and lot numbers, physical counts, a choice of costing method, and allocating a later freight bill to received stock.
- [x] R181 Pick, pack and ship: picking lists, packing slips and shipments recorded apart from invoicing, so a warehouse can ship before it bills (Kabe 2026-10-22: after R104). Picking and packing are optional layers on ship, off by default and turned on in company settings when a business needs them; ship works alone (Kabe 2026-10-27).
- [x] R182 Agent-facing easy deployment: an AI agent given only the GitHub address installs Bookflow, sets up a company and connects to it in as few, plain steps as possible, from one install guide written for agents (Kabe 2026-10-26: "agent facing easy deployment").
- [x] R183 Colony deployment trial: a simulated business owner (a separate test-user agent) works with a fresh colony test-deployment project that starts from nothing but the GitHub address. It deploys Bookflow, then invoices, receives payments, posts expenses and deposits the payments; the books are checked afterwards, every friction is fixed, and the trial is rerun until it goes smoothly (Kabe 2026-10-26: "see that everything goes smoothly from an agent controlled engagement of book flow"). Each run is also audited for whether the process is the most straightforward and elegant it can be, not just whether it works (Kabe 2026-10-26: "audit if the process is the most straightforward and elegent").
- [ ] R184 Overview flow map (on hold: waits until the core features now on the roadmap are in, R181 increments 4–5 included; Kabe 2026-10-27): an attractive Overview (the page analogous to the desktop product's home screen) that draws how the business's work flows (customers, vendors, banking, employees, company) as a picture, showing only what the business uses. Each person can hide or show its parts, and optional layers (pick and pack, sites, time, and the like) are turned on from there when needed. Check: Kabe picks a mockup direction, then the built page matches it at 1440 and 390, with hide/show and turn-on working in a real browser (Kabe 2026-10-27).
- [ ] R185 Elegance pass, after R184: an agent walks every section on phone and desktop and judges how elegant and pleasant it is to use (layout, spacing, hierarchy, wording, small frictions). Until now the work has favoured function over display. Findings are ranked, fixed in batches and shown before and after. Check: before and after shots of every section at 1440 and 390, with Kabe's verdict on a sample (Kabe 2026-10-27).
- [x] R106 Job scheduling and timesheets: employee and job calendars, weekly timesheets and clock-in/out, feeding the existing time entries and billing.
- [~] R107 Bulk import and migration: CSV and IIF import with saved mappings, spreadsheet-style bulk editing, and guided opening balances for invoices, bills and stock. Approved (Kabe 2026-10-27: "107 sounds fantastic ... its support for generic use of Bookflow is valid and functionally beneficial as well, full approve"): CSV import for any list or transaction with saved mappings and safe re-runs, guided opening balances without the desktop product, and grid editing of many records.
- [~] R108 Bank feeds: import bank and card statement files (OFX, QFX, CSV) into a bank inbox at any time, never duplicating overlaps; match each line to an entry, add it in one click, or ignore it; sort repeat payees with rules (written or learned) and recognise transfers; manual reconciliation stays. Every import is reversible, newest first per account (at the least the last one), so a file imported into the wrong account is undone in one step and can be moved to the right one. A live-feed connection is wired but not connected; live bank integration is considered later. Approved (Kabe 2026-10-27: "full approved ... sensible and proficient if we have the common import files ready and available for use and down the road ... we can consider actual live bank integrations"; reversible imports: "I would at the very least like to build this with the last import being reversible").
- [ ] R186 Reversible batches: the existing batch operations (batch invoices, memorized group runs, list merges and the like) adopt the shared reversal that R107 and R108 build, so a batch with unexpected results, or one the person described wrongly, is undone as one unit, newest first, while nothing later depends on it. After R107 and R108 (Kabe 2026-10-27: "Maintaining this reversability for all large batch imports or modifications is quite helpful. Consider customer batch changes that have unexpected results or are incorrectly described from the user originally.").
- [~] R187 One permission model for every actor: permission levels decide what work can be done, the same whether a human or an agent acts and whether through the browser, the command line or MCP. The principal who deploys Bookflow grants each actor its level, including whether it may establish automations (bank auto-add rules, recurring invoices, scheduled sends), and carries the responsibility. Commands now reserved for people become capabilities the principal can grant. Directives and reasons record why work was done. Setup gives light guidance so the principal frames an agent's mandate mindfully. No nested-mandate machinery, and no size limits: sensitivity is about impact. Commands that could damage (delete, void, reverse, send outside, broad changes) carry a confirm-first mark, and the agent guide has the agent confirm in plain words with the principal before running them ("just to be clear, you want X?"). R107, R108 and R112 build to this (Kabe 2026-10-27).
- [ ] R188 Discounts, memberships and promotions tied to customers: discount codes, membership pricing and buy-X-get-one-free offers attached to the customer list, beyond R110's quantity, customer and date rules. On hold: parked by Kabe 2026-10-28, needs his yes before starting.
- [ ] R189 Commissions: per-line salesperson or affiliate attribution on invoices, sales receipts and POS (design/pos-workspace.md), with commission calculation and payout. On hold: split from R114 on 2026-10-28; needs Kabe's yes before starting.
- [x] R109 Budgets and comparisons: budget entry, budget-versus-actual, prior-period and prior-year comparisons, and saved custom report layouts.
- [x] R110 Advanced pricing: promotions and rules by quantity, customer and date, beyond today's price levels.
- [x] R111 Unattended scheduling and reminders: the host runs memorized transactions on schedule, assigns to-dos, and sends overdue notices without a person pressing a button.
- [x] R112 Document delivery: send invoices and statements from Bookflow, track delivery, and schedule recurring sends. Scope (Kabe 2026-10-27): build it whole, drafted email with the PDF attached, the outbox, tracking and schedules, wired to a sending connection that is decided later; until one is connected, nothing leaves the machine and messages wait in the outbox, ready to send.
- [ ] R113 Custom forms and print layouts: a visual designer for invoices and other documents, plus receipt, envelope and label profiles and mixed print batches. On hold: needs Kabe's yes before starting (his 10-24 approved set did not include it).
- [ ] R114 Point of sale (approved and prioritized, Kabe 2026-10-28): a touch checkout that works like a sales receipt, with its own POS home screen of customizable tap boxes, each either a group of items (tap to go in) or a single item (tap to ring it up); a picture per item, the same picture the item shows everywhere else in Bookflow, so photos taken of items appear in POS by themselves; a quick lookup across all inventory items; per-group view styles (block, list and one or two more), remembered automatically per group and usable on the POS home too, which starts in block view for simplicity; and a sale with no customer, or a customer when wanted. An optional layer, off until turned on. Commission attribution and payout split out to R189 (still parked).
- [ ] R190 Camera scanning (Kabe 2026-10-28, with R114): a phone's camera scans barcodes in every Scan field (POS ring-up, picking, packing, counts), beeps, and moves straight on to the next scan, exactly as if the item had been chosen by its barcode; handheld USB and Bluetooth scanners keep working as today. Phones only allow the camera on a secure page, so the Bookflow host serves HTTPS on the local network with its own certificate, trusted once per phone through a short setup step.
- [ ] R191 Each person's own Home (Kabe 2026-10-28, with R114): "Home" means a person's own start page, distinct from the Overview (R184). Every user can choose the page that opens first when they sign in (the Overview, POS, the pick screen, a report, any page), and a Home button on every page takes them back to it. A POS user lands on the POS ready to sell, and with a POS-only permission level sees no accounting pages they can't use, because menus show only what a person may do.
- [ ] R115 CRM: leads, a sales pipeline, follow-ups and customer communication history beyond contact records. On hold: needs Kabe's yes before starting (his 10-24 approved set did not include it).
- [ ] R116 Payroll: paychecks, withholding, benefits, tax forms and electronic filing, designed as its own module. Scope (Kabe 2026-10-22): paychecks with tax rates the owner enters; no e-filing. On hold: needs Kabe's yes before starting (his 10-24 approved set did not include it).
- [x] R118 Mileage and trips: log vehicle trips by job and turn them into expenses or billable charges.
- [x] R119 Fixed assets: depreciation schedules and loan amortization that post their own entries.
- [x] R120 Finance charges: assess late charges on overdue customer balances by a company policy.
- [x] R121 Accountant review: a period hand-off an outside accountant can review and adjust, with the changes coming back attributed.
- [ ] R122 Approvals: purchase and bill approval steps before posting. On hold: needs Kabe's yes before starting (his 10-24 approved set did not include it).
- [ ] R123 Intercompany: linked transactions that post matching entries in two companies of one organization. On hold: needs Kabe's yes before starting (his 10-24 approved set did not include it).
- [x] R124 Customer deposits on orders: money received before invoicing, held as a liability until the order bills.
- [ ] R125 Receipt capture: read a photographed receipt or bill and draft the entry for review. On hold: Kabe 2026-10-22 "Not now".
- [ ] R126 Payment providers: take card and bank payments through a provider and record them automatically. On hold: Kabe 2026-10-22 "Not now" (outside account and real money).
- [ ] R127 Multi-currency and non-US tax: foreign-currency ledgers, revaluation, and tax regimes beyond US sales tax, after a design pass. On hold: needs Kabe's yes before starting (his 10-24 approved set did not include it).
- [ ] R128 Wider exposure: cursor key rotation, same-company hardening, encryption at rest and hosted demo sessions, for use beyond a trusted local network. On hold: needs Kabe's yes before starting (his 10-24 approved set did not include it).
- [ ] R130 Windows: a supported Windows build with an authenticated local hand-off. On hold: no Windows machine here to build and test on.
- [x] R138 Find any transaction: search across every transaction type by name, number, amount or date range
- [x] R139 Barcode and cycle counts: scan items for sales, receiving and counts, and count stock by rotating cycles instead of all at once
- [x] R148 Customer collections: short payments and bad-debt write-offs, bounced payments, moving a credit between jobs
- [x] R149 Vendor refunds received as deposits against vendor credits
- [x] R150 Unit prices finer than a cent
- [x] R152 Check printing workflow: a print queue, reprints and alignment
- [x] R153 Very large deposits (over 200 rows) and billing part of a purchase order
- [x] R154 Convert a non-inventory item into an inventory item
- [ ] R155 Compact storage: pack company files to about 500 MB per 100,000 transactions On hold: needs Kabe's yes before starting (his 10-24 approved set did not include it).


## Later

- [ ] R171 measured parity with the anchor: replay real QuickBooks Desktop books (a shipped sample company, then Kabe's own file with his yes) into Bookflow and compare every standard report to the cent on both bases; each difference becomes a defect, a documented choice or a known limit (needs the QuickBooks Desktop SDK on Windows) Set aside (Kabe 2026-10-27): correctness is shown by Bookflow's own output being logically right (an invoice totals correctly, its payment pays it, the reports say so), not by comparing output with the desktop product. The checks that already do this (hand-worked figures, seeded ties) are the standard.