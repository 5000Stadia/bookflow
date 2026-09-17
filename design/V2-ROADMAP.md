# V2 and beyond — functional roadmap

Human-directed scope, 2026-09-17. These are future product features, not current V1 blockers or automatic implementation assignments. Sequence after item1 is provisional. Preserve working V1 functions while selecting later increments with the human.

1. **Human-directed improved user UI.** Work with the human on desktop/mobile navigation, task workspaces, forms, visual hierarchy and interaction. Use real screen examples and human feedback to guide the design.
2. **Job scheduling and fuller timekeeping.** Employee/job calendars, weekly timesheets and clock-in/out; retain current time entry and billing.
3. **Bulk import and migration.** CSV/IIF import, saved mappings, spreadsheet bulk editing and guided opening invoices/bills/inventory conversion; retain current exports.
4. **Bank imports and feeds.** Statement ingestion, matching, categorization rules and connected feeds; retain manual reconciliation.
5. **Budgets and richer reports.** Budget entry, actual-versus-budget and prior-period/year comparisons, saved customized reports/groups; existing reports/exports/printing remain current.
6. **Stock sales orders and fulfillment.** Reservations, partial shipments, backorders, pick/pack/ship and shipping labels.
7. **Accurate stock availability/on-order.** Derive outstanding purchase quantities and reservation-aware availability; current stock-status on-order remains zero and available equals on-hand.
8. **Assembly production.** Build finished items by consuming BOM components, with quantity/cost effects; definitions already exist.
9. **Advanced inventory.** Warehouses/bins, transfers, serials/lots, physical counts, costing choices and allocation of later separate freight bills. Receiving-time shipping allocation already exists.
10. **Advanced pricing.** Conditional promotions and quantity/customer/date rules beyond current price levels.
11. **Unattended scheduling and reminders.** Background due processing, assigned tasks and overdue notifications; memorized transactions/groups and user-triggered due processing already exist.
12. **External document delivery.** Integrated sending, delivery tracking and scheduled invoices/statements; document generation already exists.
13. **Custom forms/print layouts.** Visual designer, custom forms, receipt/envelope/label profiles and mixed print groups; ordinary printing already exists.
14. **POS and commissions.** Touch checkout, POS starting screen, employee/affiliate attribution and commission calculation/payout.
15. **CRM.** Leads, pipeline, follow-ups and marketing/communications beyond customer/contact records.
16. **Payroll and filing.** Payroll, withholding, benefits, tax forms and electronic filing, separately designed and scoped.
17. **Duplicate-record merge.** Combine duplicate customers/vendors/items/accounts with appropriate reference reassignment.

## Acknowledged opportunities requiring selection

Mileage/trips; automatic depreciation/loan schedules; finance-charge assessment; accountant exchange; procurement approvals; linked intercompany transactions; liability-backed sales-order deposits; barcode/cycle-count workflows; receipt OCR and payment-provider functions. Inventorying QuickBooks functionality does not commit Bookflow to every feature. Multi-currency ledgers and non-US tax regimes remain outside scope without a new design pass.

## Current exception

**Company-wide cash/accrual reporting is implemented, not deferred to V2.** Company settings controls compatible report defaults with per-report overrides and actual cash recognition. Historical postings remain unchanged; operational reports retain their existing meaning. See [implementation and acceptance](specs/cash-basis-company-reporting.md).

This roadmap supersedes older absence claims when a feature has since shipped. It does not undo current invoices, purchasing/receiving, deposits, reconciliation, credits/refunds, recurring templates, statements, exports or printing.
