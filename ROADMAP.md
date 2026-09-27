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

- [x] R9 Lists: chart of accounts, customers and jobs, vendors, items, supporting lists (spec row 5; accepted 2026-09-27) — design/specs/5-lists.md
- [x] R10 Identity, memberships, roles and agent tokens (spec row 7; accepted 2026-09-27) — design/specs/7-identity-isolation.md
- [x] R11 General ledger, register entry, closing date, foreign-tagged entry (spec row 8; accepted 2026-09-27) — design/specs/8-general-ledger.md
- [x] R12 MCP adapter and agent/GUI cooperation (spec row 9; accepted 2026-09-27) — design/specs/9-mcp-adapter.md
- [x] R13 Customer payments and invoice settlement (spec row 22; accepted 2026-09-27) — design/specs/22-customer-payments.md
- [x] R14 Sales-tax calculation policies (spec row 24; accepted 2026-09-27) — design/specs/24-sales-tax-policy.md
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
- [~] R28 Remaining failing browser tests and AdmissionCancelled reported as E_INTERNAL (with Codex) — notes/NOW.md
- [ ] R29 Revoked stream gets an error frame; disconnect cleanup faster than the keep-alive — notes/NOW.md
- [ ] R30 Human desktop and phone walkthrough of live V1, with a recorded verdict — notes/V1-HUMAN-WALKTHROUGH-20260917.md

## M4 — V2 functional roadmap (each increment selected with the human)

- [~] R31 Human-directed improved UI — design/V2-ROADMAP.md item 1
- [~] R55 R31 quick fixes — report date order, focus after submit, menu Register to the real register, ☰ in the phone header, tidy Overview tiles (S) — notes/mobile-audit-response-20260927.md
- [~] R56 R31 the form first on document forms, recent documents as compact rows (S) — notes/mobile-audit-response-20260927.md
- [~] R64 R31 a quieter document form — sticky save bar, Class and unit columns only when used, picker and date tidying, recording details folded (M) — notes/mobile-audit-response-20260927.md
- [~] R57 R31 reports open with their numbers — one-line filters, date chips, headline figure, statement-shaped layout on desktop (M) — notes/mobile-audit-response-20260927.md
- [~] R58 R31 lists and filters — pinned search as you type, filters apply themselves, phone filter sheet (M) — notes/mobile-audit-response-20260927.md
- [~] R60 R31 readable money and dates across all screens, exact values kept in exports (M–L) — notes/mobile-audit-response-20260927.md
- [~] R59 R31 ledger tables and money rows — right-aligned figures, currency in the header, fewer default columns (M) — notes/mobile-audit-response-20260927.md
- [~] R63 R31 an Overview with numbers — figures strip, needs attention, recent activity (M–L) — notes/mobile-audit-response-20260927.md
- [~] R65 R31 live totals on documents from the core preview as you type (M–L) — notes/mobile-audit-response-20260927.md
- [~] R61 R31 sections open on their list with + New; types under Settings; a command finder for every action (L) — notes/mobile-audit-response-20260927.md
- [~] R62 R31 the account register on a phone as a list of entries with an add sheet (M) — notes/mobile-audit-response-20260927.md
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

## M5 — Later: unselected candidates and hardening

- [ ] R48 Acknowledged opportunities awaiting selection (mileage, depreciation, finance charges, intercompany, OCR and others) — design/V2-ROADMAP.md
- [ ] R49 Multi-currency ledgers and non-US tax regimes, after a new design pass — design/V2-ROADMAP.md
- [ ] R50 Cursor signing-key rotation, required before any external or multi-tenant exposure — notes/DECISIONS-PENDING.md D3
- [ ] R51 Same-company hardening beyond the trusted-LAN premise — notes/V1-COMPLETION-AUDIT-20260917.md
- [ ] R52 Encryption at rest (parked) — notes/open-questions.md
- [ ] R53 Windows port with authenticated local hand-off — notes/open-questions.md
- [ ] R54 Hosted disposable demo sessions — notes/demo-session-preflight.md
