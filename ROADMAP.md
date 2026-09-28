# Bookflow roadmap

Double-entry accounting for small businesses, where people in the browser and their AI agents post to the same books.

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

## M3.5 — V1.5: finished and proven

- [x] R10 Identity, memberships, roles and agent tokens (spec row 7; checked 2026-09-27, gaps R66–R68 fixed and walked through by hand) — design/specs/7-identity-isolation.md
- [x] R66 R10 membership grant/revoke over MCP stop revealing whether a username exists — design/specs/7-identity-isolation.md
- [x] R67 R10 revocation always suspends agents: new installs start in the current permission mode, agent commands refuse in the legacy mode — design/permission-resolution.md
- [x] R68 R10 agent commands: create an agent, assign and remove its principals, reauthorize it, on every surface — design/specs/7-identity-isolation.md
- [~] R13 Customer payments and invoice settlement (spec row 22; checked 2026-09-27: ledger, corrections, concurrency, permissions and agent/human continuation hold; R69–R70 remain) — design/specs/22-customer-payments.md
- [x] R69 R13 recover a payment whose save response was lost: the recovery button works in the browser — design/specs/22-customer-payments.md
- [~] R70 R13 the blind fresh-agent payment exercise with its interview, fixes and retest; the payment workflow doc gains the MCP and browser journeys — design/specs/22-customer-payments.md
- [~] R14 Sales-tax calculation policies (spec row 24; checked 2026-09-27: policies, arithmetic, migration and every surface hold; R71–R72 remain) — design/specs/24-sales-tax-policy.md
- [x] R71 R14 legacy partly billed work stays billable after upgrade — design/specs/24-sales-tax-policy.md
- [~] R72 R14 the blind-agent tax trial with its interview, fixes and retest; the 2000-span forecast recovery checked in the browser — design/specs/24-sales-tax-policy.md
- [~] R77 concurrent requests intermittently fail with AdmissionCancelled "publication admission changed" reported as E_INTERNAL (absorbs R28, the same bug): a typed retryable error or retry, never E_INTERNAL; the remaining failing browser tests with it — notes/NOW.md
- [~] R29 revoked streams: a revoked stream closes with no further data (the contract, witnessed in c116267); left: disconnect cleanup faster than the 15 s keep-alive — notes/NOW.md
- [~] R84 the agent can record bank deposits: `deposit sources` and `deposit post` refuse a standard agent with a bare E_PERMISSION while the catalog asks only ordinary access — notes/blind-trials-20260927.md
- [~] R79 invoices and other sales use the company's normal sales tax item when none is named, as the browser does; `use_defaults` resolves it, and the error says when no default exists (the trials' agents guessed a different tax item) — notes/blind-trials-20260927.md
- [~] R85 recording a customer payment warns when the same customer and check reference are already on file — notes/blind-trials-20260927.md
- [~] R86 help and errors an agent can act on first time: a worked example in each command's help, errors that name the reason and the valid fields (permission refusals, the reconcile opening step, plain money errors, reason needed even for previews), and findable oldest-first application, sales-tax owed, open invoices and payment methods — notes/blind-trials-20260927.md
- [~] R83 the demo sells stock today: demo documents dated after today move into the past so demo items are on hand — notes/blind-trials-20260927.md
- [~] R87 connecting an agent: the one-time token secret shown plainly with a Copy button, and a New agent button on the Agents panel (the person's yes on gate g9ed945) — notes/manual-agent-walkthrough-20260927.md
- [~] R80 a fresh agent that has never seen Bookflow runs a week of the plumber's ordinary work through MCP alone (invoice, take a payment, enter a bill, reconcile, run the month's report); interview it on where it hesitated or guessed, fix what is material, retest once — notes/mcp-blind-acceptance-runbook.md
- [ ] R30 the person's walkthrough of the live app on desktop and phone, offered as one plain `colony ready` with what to try — notes/V1-HUMAN-WALKTHROUGH-20260917.md
- [ ] R81 release v1.5: docs current, the demo resets cleanly, tag v1.5 — README.md

## M4 — V2 functional roadmap (held until after v1.5; each increment chosen by the person)

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
- [ ] R32 Job scheduling and fuller timekeeping — design/V2-ROADMAP.md item 2
- [ ] R33 Bulk import and migration — design/V2-ROADMAP.md item 3
- [ ] R34 Bank imports and feeds — design/V2-ROADMAP.md item 4
- [ ] R35 Budgets and richer reports — design/V2-ROADMAP.md item 5
- [ ] R36 Stock sales orders and fulfillment — design/V2-ROADMAP.md item 6
- [~] R37 Stock availability and on-order (on-order from open POs shipped; reservations wait on R36) — design/V2-ROADMAP.md item 7
- [ ] R38 Assembly production — design/V2-ROADMAP.md item 8
- [ ] R39 Advanced inventory — design/V2-ROADMAP.md item 9
- [ ] R40 Advanced pricing — design/V2-ROADMAP.md item 10
- [ ] R41 Unattended scheduling and reminders — design/V2-ROADMAP.md item 11
- [ ] R42 External document delivery — design/V2-ROADMAP.md item 12
- [ ] R43 Custom forms and print layouts — design/print-templates.md
- [ ] R44 POS and commissions — design/pos-workspace.md
- [ ] R45 CRM — design/V2-ROADMAP.md item 15
- [ ] R46 Payroll and filing — design/V2-ROADMAP.md item 16
- [ ] R47 Duplicate-record merge — design/V2-ROADMAP.md item 17

## M5 — Later: held until after v1.5

- [ ] R48 Acknowledged opportunities awaiting selection (mileage, depreciation, finance charges, intercompany, OCR and others) — design/V2-ROADMAP.md
- [ ] R49 Multi-currency ledgers and non-US tax regimes, after a new design pass — design/V2-ROADMAP.md
- [ ] R50 Cursor signing-key rotation, required before any external or multi-tenant exposure — notes/DECISIONS-PENDING.md D3
- [ ] R51 Same-company hardening beyond the trusted-LAN premise — notes/V1-COMPLETION-AUDIT-20260917.md
- [ ] R52 Encryption at rest (parked) — notes/open-questions.md
- [ ] R53 Windows port with authenticated local hand-off — notes/open-questions.md
- [ ] R54 Hosted disposable demo sessions — notes/demo-session-preflight.md
- [ ] R74 permission checks on activated roots cost about 3x (demo reset 104 s vs 33 s): reuse the hub permission snapshot across transactions only behind a fresh in-transaction version check — design/permission-resolution.md
- [ ] R78 agent administration reads like the rest of the product: a New agent button on the Agents panel, agents listed before they have a membership, the finder reaching installation commands (users, tokens, agents), the one-time token secret shown plainly with a copy button, names and dates instead of raw ids and codes on agent and token pages and errors, principal as a picker, and change history saying "Office assistant for k, via agent" — notes/manual-agent-walkthrough-20260927.md
- [ ] R82 negative amounts in parentheses as a company setting — notes/mobile-audit-response-20260927.md
