# Bookflow roadmap

Double-entry accounting for small businesses, where people in the browser and their AI agents post to the same books.


## Vision

Bookflow is the books a small business owner and their AI assistant keep together. The plumber tells their agent at 9 pm to bill today's jobs and record the checks; the next morning they open Bookflow in the browser and find it all there, correct, signed with who did it and for whom, and they carry on from where the agent stopped. Neither one ever has to tidy up after the other.

It does everything a bookkeeper expects from the desktop accounting product they already know, with the same lists, forms, reports and words, so nothing is missing when the two are put side by side. It is open source and runs on the owner's own machine: no subscription, and no data leaving unless they send it.

Every finished milestone keeps these:
- **One set of books, many hands.** The browser, an AI agent, the command line and code all do the same things the same way, and each can see and continue the others' work.
- **An agent gets it right the first time.** A fresh agent with only Bookflow's own help does ordinary business work correctly, without reading code or guessing.
- **The books can be trusted.** Money is exact, the books always balance, history is never erased, closed periods stay closed, and every change says who made it, how, for whom and why.
- **Plain to use.** It reads in a bookkeeper's words, on a desk or a phone, and stays quick on real-sized books.

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

- [~] R161 the audit log is protected like the ledger: audit rows cannot be updated or deleted in company or hub databases (append-only enforced in storage, not only by description)
- [~] R162 a closing date set in the future is caught: the change warns that it stops all posting until that date, and the setup screen says so
- [ ] R163 an agent cannot quietly make the books balance: unexplained adjustments into reconciled accounts, suspense or opening-balance equity are surfaced for the owner (shaped by the R164 study)
- [~] R164 study: how AI agents keeping books over many months go wrong (AccountingBench and newer), which of those failures Bookflow's guards already stop, and which slip through — notes/research/
- [~] R158 reconciliation "mark all": tick every item up to the statement date in one step (company/reconciliation_queries.py has mark_all; needs a command, example, MCP mapping and a GUI witness)
- [~] R159 a receipt applied across hundreds of invoices previews and pages quickly: at 403 invoices the invoice-correction preview takes ~25 s and each settlement page ~8 s (answers correct; each page recomputes the whole receipt)
- [~] R160 the standard company profile's version moves when its contents change (R135 added a Credit Card payment method; version still 1)
- [ ] R156 Scheduled backups: the host backs companies up on a schedule to a place no agent can write, keeps the last few copies, and one restore is rehearsed
- [~] R166 move a real company in: at a period boundary the agent brings in lists, opening balances and open invoices and bills from the old books (QuickBooks Desktop exports), with a dry run, saved mappings, stable outside ids and an exception report; a tie-out shows the opening trial balance and receivable and payable agings match the old books to the cent
- [~] R167 bank statements come in for reconciliation: import CSV, OFX and QFX statements and match them to entries (live feeds stay in R108)
- [ ] R168 the real books are protected: the closing date and user roles are set only by a person, never by an agent; the bookkeeping agent works without admin; the real company lives apart from demo and test data; hash-linked audit checkpoints go with each backup so a rewritten history or a rollback is detected
- [ ] R170 fit check on Kabe's real company: list the kinds of transactions in two or three months of its statements and books, each marked does it, workaround or missing; the missing ones that matter join this milestone (needs Kabe's statements)
- [ ] R165 fixes from Kabe's live use of v1.5 and v1.6, as they arrive

## M4 — After V1.5: concepts to develop

- [ ] R151 Restore a deleted document from its preserved history
- [ ] R117 Merge duplicates: combine duplicate customers, vendors, items or accounts and move every reference to the survivor.
- [ ] R101 Sales orders: a non-posting customer order that reserves stock, invoices partly as items ship, tracks backorders, and can raise a purchase order for shortfalls.
- [ ] R102 Reservation-aware stock: stock status shows on hand, committed to open sales orders, on order and available, so buying decisions see real demand.
- [ ] R103 Assembly builds: building a finished item from its bill of materials consumes the components and moves their cost into the assembly.
- [ ] R104 Advanced inventory: multiple sites and bins, transfers, serial and lot numbers, physical counts, a choice of costing method, and allocating a later freight bill to received stock.
- [ ] R106 Job scheduling and timesheets: employee and job calendars, weekly timesheets and clock-in/out, feeding the existing time entries and billing.
- [ ] R107 Bulk import and migration: CSV and IIF import with saved mappings, spreadsheet-style bulk editing, and guided opening balances for invoices, bills and stock.
- [ ] R108 Bank feeds: import statements (OFX, QFX, CSV), match them to entries, suggest the rest with rules, and optionally connect live feeds; manual reconciliation stays.
- [ ] R109 Budgets and comparisons: budget entry, budget-versus-actual, prior-period and prior-year comparisons, and saved custom report layouts.
- [ ] R110 Advanced pricing: promotions and rules by quantity, customer and date, beyond today's price levels.
- [ ] R111 Unattended scheduling and reminders: the host runs memorized transactions on schedule, assigns to-dos, and sends overdue notices without a person pressing a button.
- [ ] R112 Document delivery: send invoices and statements from Bookflow, track delivery, and schedule recurring sends.
- [ ] R113 Custom forms and print layouts: a visual designer for invoices and other documents, plus receipt, envelope and label profiles and mixed print batches.
- [ ] R114 POS and commissions: a touch checkout screen, a POS start screen, per-line salesperson attribution, and commission calculation and payout.
- [ ] R115 CRM: leads, a sales pipeline, follow-ups and customer communication history beyond contact records.
- [ ] R116 Payroll: paychecks, withholding, benefits, tax forms and electronic filing, designed as its own module.
- [ ] R118 Mileage and trips: log vehicle trips by job and turn them into expenses or billable charges.
- [ ] R119 Fixed assets: depreciation schedules and loan amortization that post their own entries.
- [ ] R120 Finance charges: assess late charges on overdue customer balances by a company policy.
- [ ] R121 Accountant review: a period hand-off an outside accountant can review and adjust, with the changes coming back attributed.
- [ ] R122 Approvals: purchase and bill approval steps before posting.
- [ ] R123 Intercompany: linked transactions that post matching entries in two companies of one organization.
- [ ] R124 Customer deposits on orders: money received before invoicing, held as a liability until the order bills.
- [ ] R125 Receipt capture: read a photographed receipt or bill and draft the entry for review.
- [ ] R126 Payment providers: take card and bank payments through a provider and record them automatically.
- [ ] R127 Multi-currency and non-US tax: foreign-currency ledgers, revaluation, and tax regimes beyond US sales tax, after a design pass.
- [ ] R128 Wider exposure: cursor key rotation, same-company hardening, encryption at rest and hosted demo sessions, for use beyond a trusted local network.
- [ ] R130 Windows: a supported Windows build with an authenticated local hand-off.
- [ ] R138 Find any transaction: search across every transaction type by name, number, amount or date range
- [ ] R139 Barcode and cycle counts: scan items for sales, receiving and counts, and count stock by rotating cycles instead of all at once
- [ ] R148 Customer collections: short payments and bad-debt write-offs, bounced payments, moving a credit between jobs
- [ ] R149 Vendor refunds received as deposits against vendor credits
- [ ] R150 Unit prices finer than a cent
- [ ] R152 Check printing workflow: a print queue, reprints and alignment
- [ ] R153 Very large deposits (over 200 rows) and billing part of a purchase order
- [ ] R154 Convert a non-inventory item into an inventory item
- [ ] R155 Compact storage: pack company files to about 500 MB per 100,000 transactions
