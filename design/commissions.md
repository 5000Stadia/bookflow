# Commissions (R189)

Design contract, 2026-10-31. R189 is approved for design; this document proposes commands and storage, not existing
product behavior. It owns the commission seam in [pos-workspace.md](pos-workspace.md) and
[point-of-sale.md](point-of-sale.md). Their older “parked” wording does not override R189's approval.

**Outcome.** Select the employee or affiliate who sold each eligible line, see exactly how their commission was
worked out, and carry an earned amount through accrual and payout without paying it twice. An invoice, sales receipt
and POS sale use the same facts. Correctness means Bookflow's totals, tax, ledger and commission due are logically
right, verified with independent arithmetic; resemblance to another interface is not the accounting test.

## 1. The anchor and the existing core

The anchor has a Sales Rep list, linked to employee, vendor or other-name records, a customer Rep default and a Rep
field on sales. Its reports can group/filter sales by rep. The 2016 manual directs readers to an add-on for commission
calculation; these pages do not establish an integrated per-line earning or payout ledger. Keep the customer/header
rep and its reports. Bookflow adds explicit per-line recipients, captured calculation and traceable settlement.

Today `schema.sales_reps` stores name type and initials; `sales_defaults.py` resolves job/customer header defaults;
`SalesHeaderProfile.sales_rep` captures the rep. None is the operator or a commission allocation. R110 may use that
header rep to select a price rule. Changing a commission recipient must not change that pricing input.

The shared sales core already captures item facts, revision history, discount shares, tax and settlement components.
R147 expands groups into member lines; subtotal and discount lines have no sale net. A percentage charge has its own
net. Commission uses each eligible line's final net after every manual and [promotion](promotions.md) discount,
never its taxable base, tax, tender, customer balance or current item price. The POS sale links through `pos_sales`;
do not invent a receipt `source_ref` input that the as-built POS does not have.

## 2. Layers and the simple route

Company settings expose these switches independently of permissions:

| Layer | Default | Gives | Requires |
|---|---|---|---|
| Commission attribution | Off | Item checkbox, line picker, attribution report | Nothing else |
| Commission calculation | Off | Percentage plans and earned-commission report | Attribution |
| Commission payouts | Off | Accrual runs, affiliate bills and settlement tracking | Calculation |
| Split a line's commission | Off | Several recipients sharing one line's commission | Attribution; calculation only to calculate money |

Turning on attribution creates no plans, accounts, liabilities or payroll records. A shop can use it forever alone.
Turning calculation on requires a published plan for each enabled item used in a new sale, but does not backfill old
sales. Turning payouts on requires an explicit expense account and other-current-liability account, both active and
in company currency. No accounts are silently created. Switching a layer off stops new use, preserves reads and
permits correction/reversal/settlement of existing obligations under their original capabilities. Disable attribution
only after dependent switches are off; refuse with named open tickets containing pending selections. Historical
commission data remains accessible from its documents and reports even with the switches off.

Settings are versioned `company update` fields: `commission_attribution_enabled`, `commission_calculation_enabled`,
`commission_payouts_enabled`, `commission_splits_enabled`, `commission_expense_account`, `commission_payable_account`.
An off-layer write returns `E_FEATURE_DISABLED` with the setting and enabling command; historical cleanup is the
explicit exception, not a way to create new work while disabled.

## 3. Recipients and entry

`commission-recipient` is a company list: name, kind `employee|affiliate`, active flag, employee ID or affiliate
contact details, optional sales-rep link, and optional payout vendor ID for an affiliate. An employee ID is required
for employee kind. An affiliate can be attributed before a vendor exists; a payout cannot. One active recipient per
employee, and one per linked sales rep; names are unique after the usual normalization. No bank details are copied
onto recipients. An employee never becomes a vendor to bypass payroll. Archive instead of deleting used recipients;
historical labels and identity survive renames and list merges.

An item gains `commission_enabled` (false) and an optional `commission_plan_id`. Ordinary sales item kinds and
percentage-charge items may enable it. Subtotals, discounts and group headers may not; expanded group members use
their own flags. A customer's existing header sales rep suggests a linked active recipient, but never selects one
silently. The operator is not a default recipient.

**The required entry moment.** Immediately after adding an enabled item, the invoice form, sales receipt form and
POS open “Who gets this line's commission?” with active recipients, search, and any header-rep suggestion. The
selection is attached to that line before entry continues. Cancel removes the newly added line; cancelling an edit
keeps the old selection. A disabled item opens no picker. A stock giveaway still asks if its item is enabled; its net
and calculated commission are zero. No privileged hidden “skip” turns a required recipient into unassigned money.

The UI does not coalesce a newly scanned eligible item into an existing line before selection. After selection it
may combine only lines with identical item, unit, price, stock dimensions, recipient allocation and plan snapshot;
otherwise it creates another line. Groups ask once per enabled member, in order; cancelling the group-entry dialog
removes that whole uncommitted expansion. The cart's existing lines are untouched.

CLI/HTTP/MCP supply the same typed `commission` input on each line; they do not open a dialog. An omitted selection
returns `E_COMMISSION_REQUIRED` listing line keys and the recipient lookup command. Dry runs can return those needs
without a write. There is no automatic assignment to the caller. A recipient-selection capability permits only an
active recipient lookup and allocation, not access to pay rates or payroll.

Changing quantity or price keeps the chosen recipient, recomputes the estimate and shows the new basis. Changing the
item asks anew. Changing the customer/header rep updates the suggestion only, not previously chosen allocations.
Hold/resume keeps allocations but revalidates active recipients, enabled flags and the current plan on the newly
priced ticket; changes appear before Charge. Completed sales never reprice because a plan or customer changed.

## 4. Calculation and corrections

A plan has name, active published revision, inclusive effective dates, percentage (0–100, integer millionths of a
percent), and basis `net_sales`, earning event `sale_posted`. One item names one plan; no overlapping rule precedence
or per-recipient rate override. A new revision affects only new/repriced unposted lines. A published revision is
immutable; editing makes a new draft, and publishing makes it the default revision. No scheduler is needed.

Let N be an eligible line's final net in minor units and r its rate in millionths of a percent. Its total commission
is `round_half_even(N * r / 100000000)`, once per line. This rounding is captured independently of the document's tax
rounding. Fractional quantities and sub-cent unit prices first resolve through the existing extension calculation;
commission never rounds a unit price itself. Sales with no plan while calculation is on refuse before posting.

With splits off a line has exactly one recipient, share 100%. With splits on, distinct active recipients' positive
shares must sum to 100%, represented as integer millionths of a percent. Allocate the already-rounded line commission
by largest remainder, ties to captured allocation order. Do not separately round each percentage of the sale.
Attribution amounts use the same allocation rule applied to N, but commission is allocated from the line's total.

**Earning.** Posting an invoice or sales receipt records an earned event in that document's transaction. A POS
single-tender sale earns on its receipt; a split-tender sale earns on its invoice once, never on the payments.
Estimates, orders, held tickets, deposits, payments and cash-basis reports create no earnings. Earned is a calculation
subledger, not a GL liability until accrued (§5). Partial or late customer payments do not change earnings. There is
no collected-revenue basis in these increments; see K3. Calculation alone is useful without the payout layer.

**Returns.** A linked credit memo names original revision and stable line key. It consumes original remaining net,
quantity, tax and discount shares through the sales return contract, with no current-price lookup. For each allocation,
the cumulative commission reduction is `round_half_even(original commission * cumulative credited net / original net)`;
each new reversal is that cumulative target minus the amount already reversed. Zero original net gives zero. Final
full return reverses every remaining cent. Quantity-only zero-value returns reverse stock, not commission. The line
return commission is the sum of these recipient reductions, with no second rounding at line level. This may differ
by a cent from rounding a proportional line total; each recipient's entitlement is the conserved balance. No return
can consume more than the original net or reverse more than its original commission.

A void reverses every remaining earned event exactly. A correction appends the old revision's reversal and the new
revision's entitlement, atomically, rather than editing past events. Recipient-only correction uses
`commission reattribute`, with original document version, `expected_attribution_version` and reason, creates negative/positive allocation events for
the same net and total commission, and changes no sales, tax or stock. It advances a document-scoped attribution
version atomically; reads project the latest allocation over immutable original facts, while history shows every
allocation revision. A concurrent reattribution using the old version refuses. It is refused if any affected amount has been
accrued or reserved for payout; first reverse eligible downstream work. Accounting corrections obey the sales core's
own payment/closed-period refusals. A standalone credit has no invented commission recipient: it carries
`commission_effect: none`; matching it to an original requires an explicit link before any reversal.

For returns after accrual, reverse the outstanding liability and expense on the return's open posting date, up to
that original allocation's still-unpaid accrued amount. Amounts already transferred to AP/payroll or paid create a
`recovery_pending` event instead; they are not new deductions, receivables or negative payments. Existing bills and
payroll are not silently changed. Resolve them through §5. Closed-period history remains fixed; a permitted current
period credit carries its own date and the original link. Restoring a voided document appends new events once and
does not silently restore a payout or revive an external payment.

## 5. Accrual, payout and reconciliation

`commission-run prepare` selects explicit unaccrued earned event IDs through a cutoff, groups by recipient/currency,
and produces a draft with exact source versions. Return reversals before accrual offset their original earnings;
zero totals produce no payable. Each reversal inherits its original source's accrual/transfer allocation: it cannot
be claimed again as an unrelated unaccrued negative amount. Preparing reserves nothing. `commission-run accrue` locks and rechecks the selected
events, claims only the still-unaccrued amounts, and posts **Dr Commission Expense / Cr Commissions Payable** in one
transaction with its immutable source allocations. Its date must be open and on/after every included event's date.
Two runs racing for the same earning cannot both accrue it. Reports distinguish earned, unaccrued, accrued awaiting
transfer, transferred, paid and recovery pending. “Due” is outstanding commission liability plus linked AP/payroll balances
after their payments, credits and adjustments. A recovery vendor credit reduces due without counting as cash paid;
waived and resolved recoveries have separate columns. An $8 unpaid affiliate bill credited $4 therefore shows $4
due, $0 cash paid and $4 recovery resolved.

**Affiliates.** `commission-run transfer` creates one ordinary vendor bill per affiliate and currency, dated in an
open period, with an expense row **Dr Commissions Payable / Cr Accounts Payable**. It links every bill amount to
accrued sources and requires the normal bill-post capability as well as `commission.payout`. No second expense is
posted. The existing bill-payment workflow posts **Dr AP / Cr Bank**; its partial payments, voids and reconciliation
drive the run's paid balance. The report exposes the bill and payment IDs, not an editable “paid” checkbox. Allocations
of each partial payment across source events use their remaining unpaid balances as weights, largest remainder
with source-event ID as tie breaker, capped to those balances. A payment void reverses its exact stored allocation
links, never redistributes against today's balances. Three successive one-cent payments against three one-cent
sources therefore pay each source once. Bank fees are
ordinary separate expenses, never lost from the commission basis.

**Employees.** `commission-run payroll-handoff` locks selected accrued amounts and produces a local statement with a
stable handoff ID, gross commission, recipient employee ID and source event IDs. It moves no cash and posts no tax.
R116 consumes that ID once. Its contract is to debit Commissions Payable for the gross commission, then credit the
payroll's net-pay/withholding liabilities under payroll's own rules; it must not debit commission expense again.
Until that integration exists, the run stays `awaiting_payroll`; exporting a statement never means paid. A permitted
bookkeeper can `commission-run link-payroll` to an existing payroll posting and payment only when the consuming
module exposes source allocations and validates the clearing debit, employee, currency, amount and unique handoff.
An arbitrary journal or an external reference string is insufficient. This leaves attribution, calculation and
affiliate payout usable without claiming employee payroll has been built. K1 governs the employee boundary.

**Recoveries.** If an unpaid transferred affiliate bill is fully reversible, cancel its transfer through the batch
door, then apply the return reduction to the restored commission liability. A partially paid bill blocks that
reversal. `commission recovery resolve` accepts one of: `waive` (expense retained, no new journal), or an explicitly
linked vendor credit/payroll adjustment/received repayment with verified original-source allocation. Each link is
unique, balances to the recorded recovery amount, and consumes only that remaining amount. A vendor credit against
an unpaid bill is **Dr AP / Cr Commission Expense**; cash repayment is **Dr Bank / Cr Commission Expense**. Payroll
adjustments are owned by R116. No automatic future-pay netting or employee wage deduction; K2 applies.

An accrual may be reversed only before a later transfer, return adjustment, recovery resolution or payment depends
on it. Transfer reversal requires native bill void/unapply and payroll release to be wholly possible. A printed
check, exported payroll handoff or externally sent statement is an irreversible artifact and blocks whole-batch
reversal; a local preview is not an export. Cancelling a handoff before export releases it. Recording a cash payment
does not move bank money electronically. Sending money or files to an outside service is outside R189 (K1).

### Worked correctness fixture (USD)

An invoice has taxable service A $100, non-taxable service B $50, then a taxable $30 subtotal discount. R147 gives
net shares $20/$10, so final nets are $80/$40. Taxable reduction is the whole $30 against A; at 10% tax is $7, not $8.
The invoice is **Dr AR $127, Dr Discounts $30, Cr Sales $150, Cr Tax Payable $7**. Debit and credit both equal $157.

A earns 10% for affiliate Ada = $8. B earns 5% for affiliate Bo = $2. Total earned = $10, no commission on the $7 tax.
Calculation alone changes no GL. Accrue: **Dr Expense $10 / Cr Commissions Payable $10**. Transfer: **Dr Commissions
Payable $10 / Cr AP $10**. Pay Ada $8: **Dr AP $8 / Cr Bank $8**; due remains Bo $2, AP $2, commission liability $0.
Pay Bo $2: due and AP become zero; expense remains $10.

Return half of A before accrual: gross $50, original discount $10, original taxable reduction $15, taxable base $35,
tax $3.50, customer credit $43.50. **Dr Sales $50, Dr Tax Payable $3.50, Cr Discounts $10, Cr AR $43.50**. Commission
reduction $4; earned is $6. After accrual but before transfer the same return also posts **Dr Commissions Payable $4 /
Cr Expense $4**. After Ada was paid it instead shows recovery pending $4 and makes no recovery journal until resolved.

A separate $0.05 net line at 10% earns $0.00 under half-even; $0.15 earns $0.02. A $0.05 commission split 50/50 allocates
$0.03/$0.02 in captured order. Successive partial returns must reduce the original $0.05 exactly, with no negative
balance. On the $0.03/$0.02 split, a half-net return reverses $0.02/$0.01 = $0.03 under
recipient-level cumulative rounding, even though independently rounding half of $0.05 would give $0.02. The final
half returns the remaining $0.01/$0.01 = $0.02. A full giveaway's commission is $0 even if its non-reducing discount
policy leaves tax due.

## 6. Storage and write invariants

All new tables are in the company file, use company-local foreign keys with RESTRICT, immutable history and the common
actor/interface/principal/reason audit envelope. Versioned list tables have normal revision rows and expected-version
checks. IDs below are stable ULIDs; a line key survives line reordering and maps explicitly to revision positions.

| Record | Fields and constraints |
|---|---|
| `commission_recipients` | id, normalized unique name, kind, employee/contact identity, sales_rep_id, payout_vendor_id, active, version; kind/link checks |
| `commission_plans`, revisions | id, name, state, version; immutable rate, basis, earning event, effective dates, rounding |
| Items | enabled flag and plan FK; disabled by migration for every existing item |
| Sales line captured `commission` | enabled-at-entry, allocation order/recipient IDs and labels/shares, plan ID/revision/rate/basis/rounding, final net and calculated amounts; null on old sales |
| `commission_events` | id, document/revision/line key, allocation key, event kind, source-event link, signed earned amount, currency, effective date, net/return basis, audit event; unique producing operation identity + allocation + event kind, with original earning a separate FK; partial credits each have their own producer revision and reattribution has its own operation/attribution revision |
| `commission_runs`, run sources | id, version, cutoff/posting date, state, recipient/currency totals; source-event amount claimed, accrual journal IDs, transfer bill/handoff IDs; cannot claim more than remaining |
| `commission_settlement_links` | source allocation, bill/payroll/payment/recovery revision, amount, reversal-of; insert-only, unique consuming source identity |

Amounts use integer minor units and explicit currency. Derived balances are queries over immutable events/links;
caches must reconcile to those facts. Sales post/void/restore, event append, idempotency receipt and batch effect commit
together. Never infer an old line's commission from today's item or default a missing migration snapshot to a rate.
Index by recipient/date, document/revision/line, source-event ID and run state. Customer/rep/vendor merges retain the
captured labels; a merge cannot create duplicate active recipient links and must name that blocker in preview.

## 7. Commands, authority and agent use

These are proposed typed commands in the common registry; all four adapters expose the same contracts. Selectors
accept ID or unambiguous name; writes take `operation_key`, reason/context and `expected_version` where a record exists.
Retries with the same key/input return the committed result; changed input refuses. Dry runs return exact sources,
totals, journals, touched versions and blockers, never reserve money. A stale run refuses before any posting.

| Commands | Principal inputs → output |
|---|---|
| `commission-recipient create/update/activate/deactivate/show/list` | identity, kind, links → versioned record; list is paginated |
| `commission recipient-options` | item, document kind → selectable IDs/labels only; no rates or payout details |
| `commission-plan create/update/publish/show/list` | rate, dates → draft/published revision |
| `item update` | enabled, plan → versioned item; requires ordinary item write and commission manage |
| Existing sales post/correct/preview; POS preview/complete | lines with `{commission: {recipients: [{recipient, share}]}}` → captured facts and totals; server owns rate and amounts |
| `commission reattribute` | document, line key, document/attribution versions, allocations, reason → append-only correction IDs and new attribution version |
| `report commission-attribution`, `report commissions` | date range, recipient/customer/item, status → rows and reconciled totals, drill-down IDs |
| `commission-run prepare/show/list/accrue/transfer` | explicit source selection, versions, dates → draft, journal or bill IDs and batch ID |
| `commission-run payroll-handoff/link-payroll` | run, sources; consuming payroll IDs → handoff/verified settlement |
| `commission recovery resolve` | recovery ID, disposition, verified settlement reference → remaining recovery |
| Existing `bulk-edit` / `batch reverse` | customer rep defaults or item settings; batch ID → exact before/after or inverses |

Structured errors: `E_COMMISSION_REQUIRED` names unresolved line keys; `E_COMMISSION_PLAN` names the missing or
out-of-date item plan; `E_COMMISSION_SPLIT` gives required sum and actual sum; `E_COMMISSION_SOURCE_CLAIMED` names
the consuming run; `E_COMMISSION_RECOVERY_PENDING` names unresolved recovery and allowed dispositions.
Reuse `E_PERMISSION`, `E_FEATURE_DISABLED`, version conflict, closed-period and dependency errors with actionable
details. A POS preview mismatch includes changed commission facts even if the sale total is unchanged: acceptance
of the old amount alone must not authorize a new recipient or rate. Bind expected preview to all calculation inputs.

| Capability | Human default | Scope |
|---|---|---|
| `commission.attribute` | standard | Select recipients on sales actor may create; narrow lookup |
| `commission.read` | standard | Attribution and calculated earnings; requires sale read for document drill-down |
| `commission.manage` | admin | Recipients, plans, item commission settings; company settings still need company admin |
| `commission.correct` | standard | Reattribute eligible historical lines, with sale access |
| `commission.accrue` | standard | Accrue selected earned amounts and view run reconciliation |
| `commission.payout` | standard | Transfer/handoff, view settlement and resolve recoveries; underlying ledger/bill/payroll rights also required |

Agents receive none of these new capabilities until the principal grants them; effective authority intersects the
agent's and principal's rights. Denies bind at every interface. Add one permission catalog layer at the then-current
tip and extend the POS clerk deny-list test. The plain clerk gains nothing automatically: a “Clerk with commission
attribution” preset adds only `commission.attribute`. Its sales show/preview projection omits rates, earned amounts,
vendor and employee private fields, retaining chosen recipient display names. A bookkeeper preset can be extended
with read/attribute/accrue; payout and management are separate editable grants, not a new role.

Mark historical reattribution, accrual/transfer reversals, payout release, recovery resolution and broad edits
`confirm_first`. Follow R187: a clearly named ordinary act needs no repeated question; a broad lasting change gets
one check of exact dry-run details even when named. Reasons/directives record intent and are not another authority
system. A fresh agent discovers “commission”, “salesperson”, “affiliate”, “who sold it” and “pay commissions” through
help, previews a sale, fills missing recipients, and resumes a human's run by ID. No agent-specific payout loophole.

## 8. Reversible batches

Assigning a rep to many customers uses existing bulk edit and its R186 batch. Item/plan assignment runs and every
multi-recipient accrual or transfer record `batches.record_effect` inside each domain transaction, including partial
runs and retries. There is one `batch reverse <id> --dry-run` door. Inverses restore prior customer/item fields,
reverse accrual journals, or void transfer bills and release handoffs as native domains permit. Check the entire
unit before changing anything, reverse newest dependent work first, name every blocker, and resume sliced inverses.

A sale capturing a newly assigned default/plan is a dependency of that batch; a later field edit also blocks restore.
A returned, transferred, paid, printed or exported source blocks the relevant inverse as specified in §5. Do not
report a partial cancellation as whole reversal. Reversers need their own grants for every inverse; batches confer
no authority. Historical source snapshots and audit remain after reversal. An interrupted run's landed effects are
one visible unit, never an unrecorded tail.

## 9. Screens and acceptance

At **390 px**, the item form shows one Commission enabled checkbox, then a plan field only if calculation is on.
The entry picker is a full-height sheet with sticky search, 44 px targets, clear Cancel/Select and focus returned to
the line. Splits are under “Split” only when enabled. A selected line displays “Commission: Ada”; money appears only
with read permission. The commission list uses recipient cards with earned/due/paid labels and expandable source
rows. A run has separate Sources, Totals and Post steps with a sticky action; no horizontally clipped amount columns.

At **1440 px**, the sale grid has a recipient column and an accessible modal picker; report filters and recipient
totals sit above a sortable table. Selecting a row opens its calculation and source document alongside it. A payout
run shows sources left, ledger preview and outstanding blockers right. Permission-hidden amounts never appear in
HTML, JSON, search or exports. Customer receipt output omits private commission information by default.

Acceptance criteria:

1. With every switch off, existing forms and sales outputs are unchanged; attribution alone requires no money setup.
2. All three entry routes prompt at item entry, preserve/cancel exactly as §3, and reject missing API allocations.
3. §5's hand-worked totals, tax and journals match; tests independently sum stored facts rather than call the pricing
   function to manufacture expected answers. A generated zero-net reward earns zero; group members stay independent.
4. Partial returns, full void, restore, reattribution, changed plans and a closed original period preserve history and
   reconcile every cent. No payment, retry, customer credit application or POS split tender earns twice.
5. An affiliate can be accrued, transferred, partially paid and fully paid through native bills; liability/AP/run
   reports agree. Employee export never displays paid. A paid return creates a visible recovery, never a wage deduction.
6. Same input yields the same output/error through Python, CLI, HTTP and actual MCP. Unauthorized direct endpoints
   cannot expose rates or release money. Cross-company selectors reveal nothing.
7. A customer-rep batch and a transfer run reverse as whole units, or name blockers with no changes. Crash/retry and
   concurrent claims cannot double-accrue, double-transfer or lose a batch effect.
8. Browser journeys pass at 390 and 1440 px with keyboard and touch, including a held ticket resumed by another actor.

## 10. Decisions settled and K-questions

Settled: per-line selection is distinct from header rep/operator; item flag controls the prompt; cancelling a new
selection removes that entry; percentage of final net, earned on sale posting; explicit accrual rather than hidden GL
posting; half-even per-line commission, then split; immutable source revisions; affiliate payout through AP; employee
handoff through payroll; no electronic payout provider; R186 owns mass reversal. These are implementation defaults,
not requests for permission. Reversible later changes require prospective versions, never rewritten old earnings.

Only these questions belong to Kabe; defaults let the design proceed without authorizing outside actions:

- **K1 — Employee payout and outside payment scope.** Should R189 include anything beyond the local payroll handoff
  and affiliate AP workflow? **Default:** no direct employee wage payment, no provider connection, bank instruction or
  file transmission; employee payment completion waits for R116's verified consumer. It does not block attribution,
  earning or affiliate payout recording. Any outside release requires its separately authorized integration.
- **K2 — Contract/legal recovery policy.** May paid commission be recovered or deducted from later earnings, and for
  which recipient agreements? **Default:** no automatic offsets/deductions; show recovery pending and require an
  explicit waive or verified recovery transaction. Bookflow does not choose an employment or affiliate contract.
- **K3 — Additional earning schemes.** Are collection-based earning, margin, targets or tiered bonuses required in
  this scope? **Default:** net-sales percentage earned at posting only; no inferred collection policy. Add a separately
  designed optional layer if requested; never silently reinterpret an existing plan.

## Appendix: references read and adoption

Read 2026-10-31, documentation only; nothing executable fetched. Product names and branded local filenames are
deliberately omitted under the repository naming instruction. **Public reference A** is the first public system
named in the task; **public reference B** is the second. Titles, section paths and versions below identify the pages.

| Reference and locator | Observed behavior | Adopt / reject |
|---|---|---|
| Anchor 2016 manual, printed pp. 71, 141–142 (PDF 102, 172–173); printed p. 603 (PDF 634) | Customer rep, rep name types, sales-report filtering; commission add-on note | Keep rep defaults/reporting. Do not claim these establish per-line calculation or payment. |
| Anchor enterprise 24 guide, PDF/printed p. 85 | Rep performance reports | Retain attribution reporting, add separate due/paid reconciliation. |
| A, official docs, “Sales Person”, “Calculate Incentive For Sales Team” (`sales-person`, `calculate-incentive-for-sales-team`) | Transaction Sales Team contribution shares and percentage incentives | Adopt explicit shares and distinct attribution/calculation; reject whole-document allocation as the only granularity because R189 is per-line. |
| A, official docs, “Sales Partner Commission” (`sales-partner`) | External partner and supplier settlement identity | Adopt affiliate/vendor linkage; attribution alone cannot stand for payment. |
| B, official Sales documentation, “Commissions”, version saas-19.1, sections “Commission plan structure” and “Plan approval” | Target/achievement plans, measures and published configuration | Adopt versioned active plans; omit target machinery from the simple percentage route. This page was readable; the 19.0 route timed out. |
| B, official Payroll documentation 19.0, “Commissions”, “Create warrant payslips” | Employee commission proceeds through a payroll run | Adopt a payroll handoff boundary; reject treating an employee commission as an ordinary vendor expense/payment. |

The arithmetic, liability timing, return caps and recovery behavior in this document are Bookflow decisions, not
claims of parity with either public reference. Source documentation describes mechanisms, not proof of correctness.

## 11. Build increments

Sizes describe scope rather than elapsed time: S is a narrow useful slice, M one bounded workflow, L a cross-cutting accounting change. Each increment adds its
demo fixtures, typed help, generated command/schema references and appropriate permission catalog checks. No product
implementation is authorized by this documentation commit itself.

1. **Attribution on invoice lines (S).** Switch, recipient list, item flag, one-recipient input/picker and attribution
   report for invoices; no rates or payments. **Done when:** an invoice credits different lines to two people and the
   report survives reopening, with zero setup beyond names and item flags. **Tests:** prompt/cancel, inactive/missing
   recipient, capability denial, legacy invoice unchanged, one 390 px browser journey and CLI/MCP contract checks.
2. **Sales receipts and POS (M).** Shared line facts in receipt/POS, groups, scan coalescing, hold/resume and clerk preset.
   **Done when:** all §3 routes preserve distinct recipients and the clerk sees no earnings. **Tests:** tap/scan/group
   at both widths, different recipients for identical items, retry and held-ticket stale recipient, native receipt parity.
3. **Calculation and earning history (M).** Plan revisions, exact net basis, earned events, reports, source-linked
   partial return/void/restore and reattribution. **Done when:** earned reports tie to §5 without any GL effect.
   **Tests:** independent mixed-tax fixture, half-cent rounding, fractional quantities, closed-period current credit,
   cumulative returns, correction and idempotency; R188 disabled and enabled cases when that layer is available.
4. **Accrual and affiliate payout (L).** Run claims, accrual journals, affiliate bills, partial-payment projection,
   recovery states, R186 adapters and source dependency checks. **Done when:** §5's full affiliate route reconciles
   and reverses where allowed. **Tests:** competing runs, interrupted runs/reversal, paid/printed blockers, return
   before/after accrual/transfer/payment, payment void, account/currency validation, all-interface authority checks.
5. **Employee handoff and recoveries (M).** Local handoff, export tracking, cancel/release and typed R116 consumption
   boundary; recovery resolution. **Done when:** employee obligations stay visible and no export claims payment;
   a verified consumer can clear each source once. If R116 is absent, the consumer command refuses actionably.
   **Tests:** duplicate handoff consumption, wrong employee/amount/clearing account, exported-batch blocker, waive
   versus verified repayment, missing payroll dependency. No payroll withholding implementation in this increment.
6. **Optional splits and batch finishing (M).** Split picker/allocation, customer-rep and item-plan batch journeys,
   report drill-down and final browser polish. **Done when:** allocation cents and all acceptance criteria reconcile.
   **Tests:** 50/50 residual and repeated returns, invalid totals, history after rename/merge, two-width accessibility,
   batch dependency/refusal/resume, and the closing trial below.

**Closing trial.** In a seeded company, a fresh agent using actual MCP help and a person using the browser alternate:
attribute two invoice lines, ring up a receipt and a held POS sale, apply the mixed taxable discount above, return
half a line, accrue and transfer affiliate earnings, record a partial payment, and answer “what is still due and
why?” Prepare an employee handoff without calling it paid. Reverse a mistaken customer-rep batch; try a transfer
reversal blocked by payment and explain the named blocker. Repeat the earned/due totals by hand from source rows,
not from another report, verify the GL, and check the 390/1440 layouts. Record first-attempt task results and the
agent's help/clarification friction; fix material findings and rerun the failed scene before closing the build.
