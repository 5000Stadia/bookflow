# Payroll (R116)

Design contract, 2026-10-31. Approved for design by Kabe on that date. R116 covers paychecks, withholding, benefits
and tax forms as its own module. Kabe's 2026-10-22 scope controls: paychecks with tax rates the owner enters;
**no e-filing**. Commands/tables below are proposed, not as-built documentation.

**Bookflow will not bundle or fetch tax tables, e-file returns, transmit direct deposit files, initiate bank
transfers, or pay tax agencies electronically.** It records payroll and payments made by the business, prepares
local check output and reviewable form data, and explains each calculation. There is no payroll subscription or
external provider credential. Missing rates are missing configuration, never zero by default. Owners enter their
rates, thresholds and effective dates; the program computes exactly from that captured configuration.

Correctness means the gross, bases, deductions, net, employer cost, liability balances and ledger entries are
logically right. Every calculated paycheck scenario below has an independently hand-worked acceptance fixture.
Passing a comparison with the anchor or comparing two adapters calling the same calculator is insufficient.

## 1. Anchor and existing core

The anchor enterprise 24 guide p. 63 offers hours-to-paycheck entry, payroll tax calculations, W-2s and paid-service
filing/deposit options. The 2016 manual chapter 15 distinguishes employee withholding from employer taxes, pay period
from payday, and reports from remittance. Its detailed payroll-item/paycheck instructions are in an online Appendix D,
not in the supplied PDF; those detailed screens were not verified. Preserve payroll item vocabulary, per-employee
paycheck detail, a liabilities view, payroll summary/detail reports and tax-form preparation data. Depart explicitly
from service-fed tax tables and external filing/payment. No assertion of complete anchor screen parity is made from
these pages; the closing trial compares the supported fields, and gaps outside the approved scope remain explicit.

Existing `company/schema.py::employees` stores nonsecret identity/contact/employment fields and tax-ID last four,
not tax elections or pay rates. Reuse its ID; never turn an employee into a vendor for payroll. `timekeeping.py`,
`timekeeping_schema.py` and `timesheets.py` save `time_activity` records, with separate billed-hours constraints;
they do not post payroll. `checks.py` translates checks into owned journal entries with numbering/printing guards.
`bills.py` and AP settlement handle vendor obligations, not employee tax bases. Sales-tax pay has agency attribution,
but it is not a general payroll liability clearing flow. Add a payroll-owned pay-liabilities command with explicit
source allocation; reuse the ledger, check instruments, audit, versioning and reconciliation infrastructure.

## 2. Independent module and optional layers

| Setting | Default | Use |
|---|---|---|
| `payroll_enabled` | Off | Manual earnings/deductions, owner-entered fixed rates/amounts, paycheck preview/post, liabilities and reports |
| `payroll_tables_enabled` | Off | Owner-entered annual percentage schedules and tax-election worksheet |
| `payroll_benefits_enabled` | Off | Reusable benefit/deduction assignments and employer contributions |
| `payroll_time_enabled` | Off | Explicit selection of existing time-entry portions as earnings inputs |
| `payroll_runs_enabled` | Off | Several paychecks prepared/posted as a resumable run |
| `payroll_forms_enabled` | Off | Versioned W-2/941/940 data worksheets and local exports |

Each deeper layer requires only payroll unless stated otherwise. A single paycheck with typed earnings and amounts
works forever without timesheets, runs, benefits, tables, approvals, commissions or form worksheets. Fixed deductions
and separately entered employer contributions remain possible in that simple route. A reusable benefit assignment
is convenience, not permission to deduct. Commission handoffs can be selected on a single paycheck whenever R189
payouts are enabled; no payroll run or tax-table layer is required.

Enable payroll by selecting active company-currency wage expense, employer-tax expense and Net Pay Payable accounts;
select tax/benefit liability accounts and payees per item. No chart accounts, rates, employees or tax elections are
silently created. Initial supported payroll currency is USD in a USD company; R127 governs expansion. Existing
employees migrate without a payroll profile. Disabling payroll stops new paychecks and drafts, but historical reads,
corrections, reversals and settlement of existing liabilities remain available with their capabilities. Disabling a
deeper layer stops new use; submitted snapshots and outstanding liabilities remain readable and settleable.

## 3. Employee, payroll items and owner entry

`payroll employee set` adds a private versioned payroll profile to an existing employee: pay frequency/periods per
year, hourly or fixed-per-period pay defaults, jurisdiction and tax-rule assignments, effective dates, withholding
elections, and optional benefit assignments. Employee inactivation stops ordinary new pay, but a final paycheck may
explicitly name a released employee with reason and employment date range. No inference of overtime entitlement,
worker classification or deduction legality. Salary is entered per period; annual salary conversion is an explicit
preview dividing by the chosen periods, rounded once, with no silent final-year true-up.

A payroll item is one of cash earning (regular, overtime, bonus, commission), reimbursement, employee tax, employee
deduction, employer tax, employer contribution, or noncash taxable benefit. Fields: name/code, kind, currency,
liability payee/account when needed, expense or clearing account, calculation method, effective revision, tax-base
membership and form reporting category. Percentage rates are integer millionths of a percent, money is integer
minor units, and quantities/hourly rates accept exact decimal strings. No floats, scripts, remote rules or eval.
Expense allocations can name job/class by exact portions; their rounded amounts must sum to the earning. Client
billing and payroll claims are separate: billing hours does not mean wages were paid.

**Owner setup route:** Payroll → Items & rates → New item → choose kind and method → enter rate/amount, applicable
base, annual cap/threshold if any, payee/account, effective-from/to dates and source note → Preview sample → Publish.
A source note records where the owner got the value and when they checked it; optional attachment stays local.
Published revisions are immutable. A new rate creates a prospective revision; overlapping effective dates for the
same assignment refuse. Entering zero requires an explicit zero rate or exemption and reason. Expired or absent
required assignments block posting with the exact missing item and date. Setup supplies field labels and empty
schemas, never a prepopulated statutory rate, cap or schedule. Demo rates are prominently fictional test data and
never copied into a real company. A preview changing after an edit requires recalculation before posting.

The owner chooses each item/jurisdiction's inclusion matrix: earnings/reimbursements/noncash benefits included,
and deductions reducing that particular base. A deduction may reduce income-tax wages but not social-insurance
wages. “Pretax” alone is insufficient. Reimbursement defaults to an explicitly selected nontaxable category, with
owner confirmation at item publication. Employer contributions can also produce a noncash taxable benefit basis,
but the same expense must not be posted twice: the contribution carries the expense and the paired benefit is
basis-only. Standalone imputed benefits use a specified expense/offset account pair. Net cash excludes noncash
benefits; tax wages can include them. A benefit needing unsupported treatment is refused, not guessed.

## 4. Calculation contract

The paycheck contains employee, period start/end, actual pay date, earnings/deduction inputs, source IDs, configuration
revisions and YTD version. Pay date drives posting and tax year/quarter; service period is separate. A future payroll
is a draft until its actual pay date is known. A posted paycheck represents wages made available on that date; the
net-pay clearing document records its instrument. A payable still uncleared is shown as “payment not recorded”,
never “cash paid”. If the actual date changes before payment, void/recreate under the correction rules; forms must
not silently attribute wages to the intended date. Scheduling a run never posts unattended.

Calculation order is fixed:

1. Extend each cash earning as exact quantity × rate, round half-up to cents per earning line.
   Take fixed earnings as entered. Total cash gross G includes captured commission handoffs C; tag their clearing
   debit so ordinary wage expense is G-C, not G.
2. Extend reimbursements R and noncash benefits I separately. Apply fixed/percentage employee deductions in explicit
   item order; their bases name earning components, never another tax or their own result. Annual deduction caps
   limit remaining amount; a cap must identify its employee-year accumulator. No negative-net clipping.
3. For each tax, build its own wage base W from included earnings/benefits less expressly exempt deductions, floored
   at zero. Use current-year cumulative wages Y before this paycheck. With threshold H and wage cap U, taxable
   amount is `max(0, min(Y + W, U) - max(Y, H))`; unbounded U is allowed, H defaults to explicitly entered zero.
   Y accumulates the full eligible W, including wages above caps, separately by jurisdiction/base identity.
4. Apply fixed amount, flat percentage, or the optional annual percentage method below. Round each tax item once
   half-up to cents. Employer taxes/contributions have separate items/bases and never reduce employee cash net.
5. Net N = G + R - employee deductions - employee taxes. Refuse N < 0 and name the excess; zero is allowed with no
   payment instrument. No automatic deduction deferral, negative paycheck or inferred loan. Total employer expense
   and every liability are displayed before Post. Reversals negate stored cents, never recalculate at new rates.

Fixed method is an owner-entered amount per paycheck, appropriate for independently computed withholding. A manual
amount override requires `payroll.override`, reason, original calculated amount and source note; it is visible in
reports. No generic “trust input” flag bypasses balancing, source claims or negative-net checks. A flat rule records
rate, base, floor/cap and payer side; no supposedly universal federal flat-tax default is supplied.

### Owner-entered tables and elections

Publication 15-T's Worksheet 1A provides the annualize/select/deannualize shape. Bookflow's table editor accepts tax
year, jurisdiction, effective range, filing-status/election variant, periods P, annual adjustment fields and rows
`lower, upper_exclusive, base_tax, marginal_rate`. Rows cover zero through infinity without gaps/overlap; adjacent
base taxes must produce continuous totals. Invalid bounds, currency, negative rates or missing variants refuse.
Owner-provided annual adjustment values, legacy allowance amount and special-case adjustments are data, not bundled
tax tables. Publication 15 distinguishes wage taxes, supplemental wages and remittance/reporting; Bookflow retains
those categories instead of presenting a generic percentage as a complete US tax calculation.
[IRS Publication 15-T, Worksheet 1A](https://www.irs.gov/publications/p15t) and
[Publication 15](https://www.irs.gov/publications/p15) are the method references, read 2026-10-31.

For a 2020-or-later election capture status, Step 2 checkbox, annual Step 3 credit K, Step 4(a) other income O,
Step 4(b) deduction D, extra withholding E per pay period, and effective date. Select the matching owner-entered
standard/Step-2 schedule. Let S be the owner-entered annual worksheet adjustment for the selected status/checkbox:
`A = max(0, W*P + O - D - S)`. Select the row containing A; `T = base_tax + (A-lower)*rate/100`.
Withhold `round_half_up(max(0, T/P - K/P) + E)` to cents, retaining exact intermediates. For legacy elections use
`A=max(0, W*P - allowances*owner_entered_allowance_amount)`, the legacy schedule, K=0 and captured E. Do not map a
legacy election to a newer form silently. Exempt is an explicit dated election that bypasses only the named income
tax item, not all taxes. No intermediate whole-dollar rounding in this design.

Supplemental wages/commissions require an owner-selected treatment: combine with regular wages for this paycheck,
or an explicit supplemental flat-rate item and configured eligibility/cumulative threshold. Aggregate catch-up with
prior checks is not computed automatically in these increments; use an owner-calculated amount with documented
basis. A commission does not silently get a particular statutory rate. Nonresident adjustments or other unsupported
special cases require explicit manual calculation mode and complete bases; never reuse a standard schedule by guess.

## 5. Posting, paying and source handoffs

A posted paycheck creates one owned ledger transaction with source components for every earning, withholding and
employer cost. With ordinary cash wages G less commission C, reimbursements R, employee deductions D, taxes T,
employer costs X and employer liabilities L (X=L for cash contributions/taxes):

- Dr Wages Expense G-C; Dr Commissions Payable C; Dr Reimbursement Expense R.
- Cr employee deduction liabilities D; Cr employee tax liabilities T; Cr Net Pay Payable G+R-D-T.
- Dr Employer Payroll Expense X; Cr employer liabilities L.

Accounts may be split by payroll item, job/class and payee; each posting component is attributable. Noncash expense/
offset pairs are balanced separately as §3 specifies. Never plug an imbalance into a rounding account. Each preview
must prove total debits = credits and all employee/subledger components sum to those ledger legs.

`payroll pay-net` selects an employee's explicit posted net-pay components and creates a payroll-owned payment
Dr Net Pay Payable / Cr Bank, with a check instrument or externally-paid reference. A single-check route offers
“Post and record check”: one transaction for paycheck, clearing payment, number and batch, using the same contracts.
No bank transmission occurs. Ordinary `check post` must not be used as proof of payroll settlement. Payroll's check
adapter owns the payment, uses existing numbering/print queue/reconciliation, and rejects generic check/journal edits
that would break allocation links. Printed is not bank-cleared; reports distinguish payment recorded, printed and
reconciled. Partial payments are recorded and displayed, never claimed as full net paid.

`payroll pay-liabilities` selects payee, liability item components, amount per component, date, bank account and
check/external reference; posts Dr selected liabilities / Cr Bank. It is a new payroll flow, not sales-tax pay or a
vendor bill. Multiple payees require separate payment documents within one batch. Payee and currency must match
selected sources; allocations may not exceed outstanding amounts, and date cannot precede source liability. A
partial remittance leaves its remainder. A reversal restores the exact allocations. Fees post separately. Generic
journals touching payroll liabilities show in reconciliation as unattributed differences and never mark sources paid.
The owner can correct them explicitly; no report hides them just because its payroll subledger balances.

### R189 commission handoff

`commission-run payroll-handoff` already defines the seam: stable ID, employee, currency, gross and accrued source
event amounts. Prepare may display it but reserves nothing new. Paycheck post atomically consumes the entire
handoff once, validates employee/USD/remaining amount and original Commissions Payable account, and debits that
liability, not Commission Expense. No partial handoff consumption: split the eligible unconsumed handoff upstream
before selecting it. A unique active claim plus append-only release history prevents two paychecks consuming it.
Commission's handoff reservation and payroll's consumption are different states of the same source allocation.
Post links the payroll transaction and component allocations to the handoff in the same company transaction.

The commission report shows transferred gross, withholding/deduction allocation and remaining net separately. Allocate
each employee tax/deduction across cash earnings proportionally to gross, largest remainder with stable earning-line
ID tie break, cap a source's combined allocation at its gross and distribute residual to remaining capacity. If
noncash taxes or deductions exceed total cash gross, cap all cash allocations at gross and attribute the remainder
to reimbursements explicitly; negative net still refuses. This attribution explains settlement, not tax treatment.
Allocate net payments proportionally to remaining net source balances, capped, with the same stable tie rule. For
commission, “settled through payroll” is its attributed withholding/deductions plus its recorded net payments; “cash
paid to employee” is only its net payment share. At posting, withholding/deduction shares count as transferred to
payroll liabilities, not remitted to agencies. An unpaid paycheck never shows the whole commission as cash paid.
`commission-run link-payroll` verifies these native source allocations and payment IDs; arbitrary journal IDs fail.

Void of an unpaid, unexported eligible paycheck reverses exact liability/tax/YTD effects and releases its handoff to
`awaiting_payroll` once. It does not reverse the upstream commission accrual. Reverse the later payroll batch first,
then the upstream transfer/accrual if eligible. Printed/exported/paid dependencies block batch reversal. A return
of a paid commission remains `recovery_pending` under R189: **no automatic future-wage deduction or tax refund**.
An actual separately recorded repayment can resolve recovery under its native contract; payroll adjustment deductions
for recoveries remain unavailable pending K2. Paid wages/taxes must not be silently rewritten to recover commission.

## 6. Time, YTD, corrections and runs

Time input selects exact `time_activity` revision and quantity portions, not an editable timesheet total. Payroll
uses the core's exact stored hour scale. One active payroll claim per source portion prevents double pay; partial
claims cannot exceed original quantity. Billable status does not affect wage eligibility. Open clocks are excluded.
Editing paid portions refuses until eligible payroll is reversed; increasing time adds an unclaimed portion. Billing
locks and payroll locks both apply. Manual hours remain usable with time off; overlapping dates warn but do not
forbid a legitimate second paycheck. A salary paycheck has no time claim unless explicitly selected.

YTD is a journal-like payroll event projection by employee, tax year, jurisdiction, wage-base identity and item.
Store full eligible wages, capped taxable wages, employee withholding, employer tax, deductions, contributions and
net payments separately. Use ordered pay date plus employee-year sequence; lock that employee-year when posting.
The calculation captures a YTD version; a competing post invalidates it and demands a new preview. Same-date checks
have explicit sequence. Backdated insertion ahead of an existing check is refused; unwind eligible later checks
newest first or make an explicit current-date adjustment. Never silently recompute posted later checks at a new cap.

Midyear setup requires `payroll opening`: owner-entered YTD by employee/base/item, cutoff date, payee liabilities
still outstanding, and evidence. Choose `books_already_include` with exact existing ledger balances to reconcile, or
`post_opening` with explicit equity offset; cannot do both. Already-paid opening components have no live payable.
An opening without complete cap bases blocks computed payroll, rather than guessing zero. Mark data worksheets
incomplete for fields the owner did not supply. Opening is an R186 batch; a later paycheck using it blocks reversal.

Unpaid/unremitted/unexported paychecks may be voided in an open original period; full stored inverse, source release
and YTD inverse commit atomically. Later YTD-dependent checks block even if GL periods are open. Correction is void
and replacement with links, not mutation. If original period is closed or money was paid, `payroll adjustment`
creates a current open-date, explicitly reviewed delta with original employee/paycheck, earning/tax categories,
reporting period and reason. It never runs withholding formulas on negative wages. The owner supplies each signed
component and a balanced ledger preview; separate payable/refund/receivable accounts distinguish money still owed
from money recoverable. No automatic tax receivable, employee deduction or refund, and recovery deductions remain
subject to K2. Filed-period differences appear in an adjustment worksheet; old exported snapshots stay immutable.

Adjustment dates are explicit: `posting_date` determines the open GL period; `tax_effective_date` and
`tax_year` identify the original payroll reporting period being corrected. For an adjustment they override the
ordinary pay-date reporting rule. Signed wage/base/withholding events update only that named employee-year,
not the posting-date year; payment/repayment events keep their actual cash dates. Each adjustment locks and advances
the affected employee-year version, invalidating all unposted previews using it. If later posted checks in that
same tax year depend on a changed cap/base, preview lists them and posting requires a correction plan containing
explicit owner-computed deltas for each affected check (including explicit zero with a reason). Commit that bounded
plan atomically, retain each original calculation, and append adjustment events; never rerun old withholding
formulas silently. Other tax years are independent. Missing dependent deltas return `E_PAYROLL_ADJUSTMENT_DEPENDENCY`
with the affected check IDs. Reversal of the adjustment plan uses its R186 batch and refuses later dependent use.
Paid amounts produce receivables only when explicitly entered with accounts/evidence, not merely because a tax
worksheet decreases. Form corrections and external recovery eligibility are separate from recording these facts.

Paychecks/payroll payments have **void/correct**, not user deletion in these increments. Their family disposition is
deferred under [transaction-deletion.md](transaction-deletion.md); generic journal/check deletion must recognize their
ownership marker and refuse with the payroll command. A future delete capability needs its own reviewed admission
graph, numbering, restoration and history design. Void retains a check's number; printing cannot be undone.

Run states: draft → ready → posting → posted, with failed/interrupted progress and cancelled draft states. Ready is a
captured preview, not approval or a reservation. Run post revalidates each child; each paycheck, source claims, audit,
idempotency receipt and `batches.record_effect` land in one write transaction. Failures pause subsequent slices and
expose exactly the landed children, never claim the run was atomic. Resume uses fixed child operation IDs. A single
paycheck is also a one-effect batch; a combined post/check includes both effects. There is no private payroll undo.

`batch reverse <id> --dry-run` preflights the entire landed unit, newest dependencies first, naming later YTD use,
time/source edits, payments, remittances, closing dates, printing, export or reconciliation blockers. Its native
aggregate inverse reverses payments before paychecks where permitted and releases sources, preserving every history
row. It fences new dependent writes while a sliced reversal resumes; reversed children cannot be posted again under
the old key. Every inverse checks the reverser's own grants. Printed checks, exported pay stubs/forms/handoffs and
recorded external release block whole-batch reversal. Previewing on screen does not. Cancellation of a wholly pending
print run is allowed; partial cancellation never counts as reversal of the payroll run. Local exported files are
irreversible artifacts under R186 even if the user says they have not sent them.

## 7. Reports and form data

Reports share captured payroll facts and explain mismatches against GL, rather than deriving tax wages from expense
accounts. Include Payroll Summary (employee/period gross, bases, deductions, net, employer costs), Payroll Detail
(item/source calculation), Employee Earnings/YTD, Payroll Liabilities (payee/item, accrued/settled/outstanding),
Payment History and Payroll Reconciliation (GL versus attributable sources and unexplained difference). Pay stubs
show period/pay date, earnings quantity/rate, deductions, net, YTD, check/reference and whether payment is recorded.
History views never recompute old checks using today's employee defaults. Use pay date for tax-period reports;
service-date labor cost is a separate filter clearly labeled and never reused as tax wages.

Forms layer produces **review worksheets, not filing-ready forms**. Owner chooses tax year/form revision and enters
form mapping and any year-specific rates/credits. No official form template or tax table is downloaded. Local PDF/
CSV/JSON exports say “Review data — not filed”; no filed checkbox is inferred from export. Optional `record-filed`
records the owner's outside act, date/reference and exact worksheet snapshot, never transmits. It is confirm-first.

| Worksheet | Required facts and reconciliation |
|---|---|
| W-2 | Employee/employer identity references; boxes 1/2 taxable wages/FIT, 3/4 social-insurance wages/tax, 5/6 Medicare wages/tax including employee-only additional tax; separate tips, elective deferrals/box-12 code amounts, box-13 flags, other deductions and state/local wages/taxes. Sum employee rows to annual payroll category totals. Unsupported categories stay missing, never zero-filled. |
| 941 | Quarter wages, FIT withheld, separate social-insurance/tip/Medicare/additional-tax bases and employee/employer taxes, dated liability schedule, fractions-of-cents adjustment, other entered adjustments/credits, deposits and balance/overpayment. Deposits are payments, not liability dates. Reconcile quarter wages to employee reports and tax components to GL. |
| 940 | Annual total payments, exempt payments, wages above per-employee cap, taxable FUTA wages, owner-entered gross rate/credit and credit-reduction adjustments, liability by quarter and deposits. No assumption of maximum state credit or no credit reduction. |

Identity master retains last four only. Initial worksheets export internal employee IDs, names/address and masked
identifier fields; the owner supplies full identifiers outside Bookflow. Thus they cannot be mistaken for submit-ready
W-2s. Adding full tax-ID storage or official form rendering is a separate scope/privacy decision (K1). Each worksheet
has source IDs/versions, mapping revision, calculation trace, unresolved fields and adjustments. An owner can export
incomplete review data with prominent omissions; `record-filed` requires a complete owner-supplied external reference,
not a claim that the incomplete worksheet was an official filed form. Tax-period adjustments produce revised
worksheets and a difference report; they never overwrite an earlier exported/recorded snapshot.

For 941 reconciliation retain computed paycheck cents and a separate owner-reviewed fractions-of-cents adjustment
when form-level multiplication differs; never redistribute it to employees. Form mappings are immutable versions,
not generic formulas. Changes to mappings create new worksheets. Forms cannot manufacture unsupported legal credits.

## 8. Storage, commands and permissions

Company file only; stable ULIDs, RESTRICT company-local FKs, immutable published revisions and posted facts. Monetary
fields include currency. Index employee/pay-date/sequence, item/year, payee/due date, source claims and run status.

| Record | Fields and invariants |
|---|---|
| `payroll_profiles`, revisions | employee FK, effective interval, pay defaults/elections/assignment IDs, version; no overlapping active assignment |
| `payroll_items`, revisions; `payroll_rate_tables`, rows | kind, accounts/payee, method/base membership/caps/threshold, dates, exact rate/row boundaries, source note, publishing audit |
| `payroll_drafts`, revisions; `payroll_runs`, children | period/pay date, employee, exact inputs/config/YTD versions, preview trace, state/version, stable child operation IDs |
| `paychecks`, `paycheck_components` | owned ledger transaction/revision, original/replacement IDs, immutable input/calculation/base/YTD snapshot, account/posting-source links, source line/job/class |
| `payroll_source_claims`, events | paycheck component, time portion or commission handoff/source allocation, claim/release, source revision; unique active claim and quantity cap enforced in writer |
| `payroll_ytd_events`, employee-year version | source paycheck/opening/adjustment and signed base/item amounts, unique producing operation; append-only inverse links |
| `payroll_payment_allocations` | owned payment/posting source, liability component, amount/reversal-of; active sum at most outstanding; payee/currency agreement |
| `payroll_worksheets`, revisions/artifacts | form/year/quarter, source/mapping revisions, rows, unresolved fields, output hash, exported/recorded-outside event |

No secrets go into ordinary employee output, audit text, operation-key logs or error payloads. Payroll permission is
required for rates, elections, pay details and worksheet artifacts. Ordinary accounting readers still see legitimate
GL totals; the payroll UI does not promise to hide accounting totals from people granted ledger access. General
employee, commission recipient and timesheet readers gain no payroll detail. Do not cache pay data in browser local
storage; unauthorized responses omit it server-side, including search, attachments and exports.

Proposed typed command registry; all adapters expose identical inputs/outputs/errors. Every write takes operation
key, reason/context and expected object versions; permanent retries check current authority and refuse changed
input. `--dry-run` returns exact journal/source/YTD effects, touched versions and blockers without claiming sources.

| Commands | Main inputs → output |
|---|---|
| `payroll employee set/show/list` | employee, dated pay/election assignment → versioned private profile |
| `payroll item create/update/publish/show/list`; `payroll rate-table create/update/publish/show/list` | explicit methods/rates/dates/accounts/rows → validated revisions |
| `paycheck draft/update/show/list/calculate/post` | employee, period/date, item lines, source IDs, expected preview/version → trace, gross/net/liabilities, posted ID/batch |
| `paycheck void`; `payroll adjustment preview/post` | original/version, date, reason, explicit components → balanced inverse/delta and linked source history |
| `payroll opening preview/post` | YTD, balances, cutoff, reconciliation mode → opening facts/batch or mismatch |
| `payroll-run prepare/show/list/calculate/post/resume/cancel` | explicit employees/drafts/versions, dates → child IDs, results/progress, batch |
| `payroll liabilities`; `payroll pay-net/pay-liabilities`; `payroll payment void` | explicit source allocations, payee, bank/date/instrument/version → balances or owned payment/batch |
| `report payroll-summary/detail/earnings/liabilities/payments/reconciliation` | period/employee/item/payee/cursor → exact totals and authorized drill-down |
| `payroll forms prepare/show/export/record-filed` | kind/period/mapping, snapshot, format or outside reference → worksheet, omissions, immutable artifact/event |
| `batch reverse` | batch, dry-run/resume → whole-unit inverses or named blockers |

| Capability | Human default | Scope |
|---|---|---|
| `payroll.read` | admin | Private reports/profile/paycheck reads; does not grant ledger or employee editing |
| `payroll.prepare` | admin | Drafts, calculations, run preparation; requires payroll.read |
| `payroll.configure` | admin | Profiles/items/rates/opening setup; company switches also need company admin |
| `payroll.post` | admin | Post paychecks/openings/adjustments; read/prepare prerequisites |
| `payroll.override` | admin | Documented manual tax override/adjustment; post still required |
| `payroll.pay` | admin | Record net/liability payments; funding account permission and check rights still apply |
| `payroll.void` | admin | Paycheck/payment void; batch inverse additionally needs all native inverse rights |
| `payroll.forms` | admin | Prepare/export/record outside filing; requires read |

Agents receive no new payroll capabilities by default; principal intersection and denies apply across interfaces.
Payroll preparer preset = read/prepare; Payroll processor adds post/pay; Payroll administrator adds configure/override/
void/forms. These are editable grants, not new roles or mandates. The POS clerk and ordinary time-entry user receive
none. Tax-table publishing, broad assignments/openings, adjustment, void/reversal, printing, export and recording
outside payment/filing carry `confirm_first`. Follow R187's named-act and one-exact-preview rules; no size threshold,
mandatory human-only approval token, or second confirmation after an explicit ordinary instruction.

Errors name the repair: `E_PAYROLL_RATE_MISSING` (item/date/variant), `E_PAYROLL_BASE_INCOMPLETE` (opening base),
`E_PAYROLL_PREVIEW_STALE` (changed input/config/YTD), `E_PAYROLL_SOURCE_CLAIMED` (authorized consuming ID),
`E_PAYROLL_NEGATIVE_NET` (excess), `E_PAYROLL_BACKDATE` (later dependent check), `E_PAYROLL_UNSUPPORTED_TREATMENT`
(method/category), `E_PAYROLL_ALLOCATION` (remaining and requested). Reuse native capability, company isolation,
closed-period, version and dependency errors. Same-company errors reveal private details only to payroll readers.

An agent discovers “pay employees”, “withholding”, “payroll liabilities” and “commission payroll” through help. It
asks for genuinely missing rates or payday, never fetches a table or guesses an exemption. It can resume a human's
draft by ID, explain the line arithmetic, post under its grant, and report “payment recorded” without claiming bank
transmission. A reason names the principal's request; it is not an authorization substitute.

## 9. Screens

At **390 px**, Employees → Payroll appears only with the layer/grants. The start screen offers New paycheck,
Liabilities and Reports. One employee per card; period and pay date stay visible. Earnings, deductions/taxes, net and
employer cost are separate sections with a sticky Calculate/Post action. Each calculated row opens “How calculated”
with base, rate, rounding, cap remaining and source note. Tables/benefits/time/run controls appear only when enabled.
Liabilities show payee, due amount and selected allocation cards; Pay records the chosen instrument. Forms display
“Review data — not filed”, missing fields and export action. Error focus lands on the missing field without losing
input. Tax rates have explicit percent units; extra withholding says “per paycheck”, credits “annual”.

At **1440 px**, a payroll run table shows employee, gross, employee withholding/deductions, net, employer cost and
status. Selecting a row opens calculation and ledger preview beside it. Liability payment allocations and remaining
amounts stay visible together. Rates editor shows effective dates and old/new differences; table rows show inclusive
lower and exclusive upper bounds. Reports have drill-down to original components and payment IDs. Both widths use
44 px targets, keyboard navigation, accessible labels, non-color-only warnings and wrapped money labels without
horizontal clipping. Pay-stub preview is local; export/print records the irreversible artifact before release.

## 10. Hand-worked acceptance fixtures

All dollar/rate values here are **fictional configuration tests, not tax guidance or bundled tables**. Store the
inputs and expected cents as independent fixtures, with this arithmetic beside them. Every new calculation method
or computed paycheck scenario added by a builder must add a hand-worked oracle; never generate expected output by
calling production calculation. Unless stated otherwise, YTD is zero, USD and the period/pay date are open.

**P1 — simple, no optional layers.** 40 hours × $25 = G $1,000. Employee tax T at entered 10% of gross = $100;
employer tax at 5% = $50. Net = $900. Dr Wages $1,000, Dr Employer Tax Expense $50; Cr Employee Tax Payable $100,
Cr Employer Tax Payable $50, Cr Net Pay Payable $900. Both sides $1,050. Record net check: Dr Net Pay Payable $900 /
Cr Bank $900. Pay taxes $120, allocated employee $100/employer $20: Dr respective liabilities $100+$20 / Cr Bank
$120. Outstanding employer tax $30; total bank credit $1,020. Final $30 payment clears all liabilities; bank $1,050.
Void an eligible unprinted $30 payment restores exactly that liability, never reopens wages or recomputes tax.

**P2 — benefits, unequal bases and reimbursement.** Salary $2,000, reimbursement $100, employee retirement $100
reduces only income-tax base. Income base $1,900 at 10% = $190. Social base $2,000 at 6% = $120. Employer social 6%
= $120 and fixed benefit $50. Net = 2,000+100-100-190-120 = $1,690. Dr Wages $2,000, Reimbursement $100, Employer
Tax $120, Benefit Expense $50 = $2,270. Cr Retirement Payable $100, Income Tax $190, Employee Social $120,
Employer Social $120, Benefit Payable $50, Net Pay $1,690 = $2,270. Time off and tables off do not block this.

**P3 — annualized table, modern election.** G=W=$1,000; P=12; O=$1,200; D=$600; owner adjustment S=$600; credit
K=$120; E=$3. A=12,000+1,200-600-600=$12,000. Fictional rows: [0,10,000) base $0 rate 0%; [10,000,infinity)
base $0 rate 10%. T=(12,000-10,000)×10%=$200 annually. Per pay = (200-120)/12+3 = $9.666… → $9.67. Net $990.33.
Dr Wages $1,000 / Cr Income Tax Payable $9.67, Cr Net Pay $990.33. No employer tax in this explicitly configured
fixture. Legacy counterpart: G=$1,000, P=12, allowances=2, owner allowance=$500, same table, E=$3. A=$11,000,
T=$100, withholding=100/12+3=$11.333… → $11.33; net $988.67; debit $1,000 equals those two credits.

**P4 — cap and threshold crossing.** G=W=$200, Y=$950. Employee capped item rate 6%, cap $1,000 gives
min(1,150,1,000)-950=$50, tax $3. Employer counterpart $3. Additional employee-only item threshold $1,000,
unbounded cap, rate 1% gives 1,150-max(950,1,000)=$150, tax $1.50. Net $195.50. Dr Wages $200, Employer Tax $3;
Cr employee capped liability $3, additional liability $1.50, employer liability $3, Net Pay $195.50 = $203.
YTD full wages become $1,150, capped taxable wages increase $50; additional taxable wages increase $150. A later
check depends on these bases and blocks reversal. Two concurrent previews cannot both use $950.

**P5 — commission, no second expense.** Prior earned/accrued commission $200 already posted Dr Commission Expense /
Cr Commissions Payable $200. Handoff H has that employee/USD/$200. Paycheck regular G $800 plus H $200 = $1,000,
entered employee tax 10%=$100, employer 5%=$50, net $900. Dr Wages $800, Dr Commissions Payable $200,
Dr Employer Tax Expense $50; Cr Employee Tax $100, Employer Tax $50, Net Pay $900 = $1,050 each. Commission's
withholding share 200/1,000×100=$20; net share $180. A $450 net payment assigns $90 to commission and $360 to
regular wages; commission has $90 net outstanding, $20 assigned withholding, $90 cash paid, not $200 cash paid.
Remaining $450 assigns the remaining $90/$360. No commission expense is added by payroll; H cannot be consumed
again. An unpaid eligible void restores $200 Commissions Payable and releases H once. A $40 post-payment sales
return creates recovery pending $40, no wage deduction and no tax reversal.

**P6 — cents, hours, noncash and refusal.** Separate isolated checks:

- Time source 0.3333 hours × $30 = $9.999 → $10.00; 5% employee tax $0.50; net $9.50. Dr Wages $10 / Cr Tax $0.50,
  Net Pay $9.50. Claim 0.3333 hours once, not the rounded $10 as a time quantity. A 0.001 hour × $5 earning is
  $0.005 → $0.01; no taxes configured, Dr Wages $0.01 / Cr Net Pay $0.01.
- Cash wages $500, employer benefit contribution $50 with paired basis-only imputed benefit $50; employee tax base
  $550 at 10%=$55. Net $445. Dr Wages $500, Benefit Expense $50; Cr Benefit Payable $50, Tax $55, Net Pay $445.
  Both $550; imputed component adds neither cash nor another $50 expense.
- Cash gross $10, employee fixed deduction $10, no taxes: Dr Wages $10 / Cr Deduction Payable $10; net zero, no bank
  instrument. Deduction $11 instead gives net -$1 and refuses, leaving no GL, YTD or source claim.

**P7 — opening and forms.** Import P1 as already-paid year-to-date history, with wages $1,000, employee tax $100,
employer tax $50, zero outstanding and explicit ledger reconciliation. A second P1 paycheck yields year-to-date
wages $2,000, employee tax $200, employer tax $100, new unpaid net $900 and new tax payable $150; no second expense
for the opening. Opening components map to the same reports as native facts, with an opening-source label. A form
fixture maps wages $2,000/FIT $200 to W-2 review boxes 1/2 and quarter FIT $200 to 941; zero social/FUTA categories
are explicit fixture exemptions, not inferred missing mappings. Total payroll tax payments $150 from the opening are separate
from $300 total configured tax liabilities; these fictional employer items require a mapping marked non-federal,
so federal 941 contains only FIT liability $200 and opening FIT deposits $100, leaving $100, while the payroll GL
also holds the new non-federal employer $50. FUTA-specific form fixture: owner supplies total/exempt/over-cap
wages $2,000/$200/$800 → taxable $1,000, gross rate 6%, credit 5%, reduction 0% → FUTA $10. These are worksheet
inputs with separately supplied opening FUTA liability $10 and deposits $4, balance $6, not an invented paycheck.

**P8 — explicit prior-year adjustment, no automatic recovery.** P1 was the last paycheck of the prior year,
fully paid and taxes remitted. This year the owner records an independently established $100 gross overstatement,
$10 employee tax overstatement and $5 employer tax overstatement, and explicitly supplies recoverable amounts and
receivable accounts with evidence. No deduction from another paycheck is authorized. Current open-date entry:
Dr Employee Receivable $90, Dr Employee Tax Recoverable $10, Dr Employer Tax Recoverable $5;
Cr Wages Expense $100, Cr Employer Tax Expense $5. Both sides $105. Prior-year wage/base event -$100, employee
withholding event -$10 and employer-tax event -$5 revise that year's P1 totals to wages $900, employee tax $90,
employer tax $45. Original cash paid remains $900 to employee and $150 to agencies; $105 is recoverable, not cash
returned and not a negative outstanding payable. Current-year wage/cap/withholding totals change by zero. The old
export remains fixed; a correction worksheet shows the deltas. Without explicit recoverable accounts/evidence,
this recovery entry refuses rather than inventing assets. With later affected checks in the original year, the
same adjustment also refuses until their explicit correction plan is supplied. This fixture is entered adjustment
arithmetic, never a computed negative paycheck.

Acceptance beyond the arithmetic: run P1/P2 for different employees as one batch, reverse before external artifacts
and show zero net effects and restored source availability; add a payment/print/export or later cap-dependent check
and refuse whole reversal. Simulate crashes between children and during inverse slices. Test rate/table boundaries,
zero/absent/exempt distinctions, expired revisions, half-cent rounding, negative net, source races and closed dates.
Test time billing and payroll claims together, unauthorized payroll reads through every adapter, masked exports,
GL unattributed differences, generic-journal bypass refusal and R189 verified links. Actual MCP and browser
continuation must use shared IDs at 390/1440 px. Exported form correction must preserve original and new totals.

## 11. Settled decisions and K-questions

Settled: separate opt-in module; simple manual route stands alone; owner-entered versioned rules; exact intermediates
and half-up payroll rounding; payroll-owned liability settlement; gross commission clears prior accrual; explicit
actual pay date and YTD ordering; no silent backdated recalculation; no automatic recovery netting; native check
infrastructure without bank transmission; private payroll projections; review worksheets with masked identities;
R186 for every run and inverse; no deletion until a separate lifecycle is designed.

Only these are reserved for Kabe; defaults do not stop the local bookkeeping increments:

- **K1 — Form scope and private identifiers.** Is official printable form completion/full employee tax-ID storage
  wanted beyond masked review worksheets? **Default:** worksheets/export only, full identifiers completed outside
  Bookflow, no filing-ready claim. Legal form certification and private-data storage need their own design if chosen.
- **K2 — Legal recovery policy.** Which employee agreements/jurisdictions permit recovery of paid commission or
  other wage overpayment through later payroll? **Default:** none automatically; recovery stays pending or is
  separately repaid/waived under R189. No payroll recovery deduction command is enabled by these increments.
- **K3 — Outside money/file release.** Any future bank/provider transmission or tax-payment connection?
  **Default:** none. Local records, check output and outside-payment references only. No e-filing and no bundled
  tables remain the approved scope, not unanswered questions. A future connection needs separate authorization.

## Appendix: reference record and adoption

Read 2026-10-31, read-only public docs/source and relevant local PDF ranges; nothing executable fetched. Public
reference A/B identify the first/second public systems named in the task. Branded filenames/hosts are omitted under
the naming rule; official page slug and version/repository path identify them without spelling another product name.

| Source | Mechanism observed | Adopt / reject and why |
|---|---|---|
| Anchor enterprise 24 guide PDF p. 63 | Hours/payroll service, checks, W-2 and electronic options | Adopt entry/report separation; reject service-fed tables and transmission under Kabe's scope. |
| Anchor 2016 manual PDF 446–454 / printed 415–423; bill caution PDF 213 | Payroll setup, periods/payday, withholding versus employer liabilities; warns against ordinary bill/check payroll entry | Adopt separate payroll-owned sources and settlement. Detailed payroll items/liability screens are referred to an online Appendix D absent from this PDF; not claimed verified. |
| A official HR docs `salary-component`, `salary-structure`, `salary-structure-assignment`, `income-tax-slab` | Fixed/formula components, reusable assigned structures, effective dates and slab ranges | Adopt dated employee assignments, component bases and editable ranges. Reject arbitrary formula conditions and projected annual true-up as mandatory: manual single checks must work alone and US withholding needs its own method. |
| B official documentation source branch `19.0`, `content/applications/hr/payroll/salaries.rst` and `salary_rules.rst` | Structures, ordered rules, fixed/percentage/script methods, inputs, account mapping and dated parameters | Adopt traceable ordered components/account mapping and separate inputs. Reject executable scripts, floating output and installed localization rates. Official HTML timed out; read the public documentation source instead. |
| IRS Publication 15 (2026), sections 7, 9–14, [official publication](https://www.irs.gov/publications/p15) | Supplemental wages, separate employee/employer taxes, liability dates versus deposits, reconciliation and adjustments | Adopt separate bases and remittance facts; reject treating withholding as paid taxes or assuming a credit. Owner supplies values and legal treatment. |
| IRS Publication 15-T (2026), Worksheet 1A, [official publication](https://www.irs.gov/publications/p15t) | Annual adjustment, schedule selection, deannualization, credits and extra withholding | Adopt the parameterized calculation shape; no published numerical tables or constants are shipped. P3 uses fictional values to test the mechanism. |

Safety review: no executable rules/source are copied or installed. All outside material informs explicit typed data
and arithmetic. Inputs cannot trigger network access or code execution. Form worksheets are Bookflow's data contract,
not an assertion that a historical desktop manual establishes current filing law.

## 12. Build increments

S = narrow useful slice; M = bounded workflow; L = cross-cutting accounting work. Every increment adds its demo
fixture, help/schema references, current permission layer and preservation checks. No product code ships in this
commit. Later increments never become prerequisites for the early simple route.

1. **Private paycheck calculator (S).** Payroll switch, private draft/read/prepare, manual earning/fixed tax inputs,
   calculation trace and save/resume; no posting. **Done when:** P1 can be entered, hand-checked and reopened by a
   permitted colleague without GL effects or a table. **Tests:** P1 preview, exact cents, negative net, no ordinary
   employee-read leakage, stale draft, CLI/MCP and 390 px workflow.
2. **Single posting and net pay (L).** Owned paychecks, flat-rate items, component/YTD facts, source-free posting,
   net-pay settlement/check adapter, void and R186 adapter. **Done when:** P1 records one check and eligible reversal
   restores books; all bypass routes refuse. **Tests:** P1/P6 cents and zero net, duplicate post, closed date, permission
   intersection, print/reconciliation blockers, paired post/check atomicity and direct journal refusal.
3. **Liabilities and reconciliation (M).** Payee/source allocations, partial remittance, reports, payment void.
   **Done when:** P1's $120 then $30 payments reconcile every component to GL. **Tests:** racing over-allocation,
   wrong payee/currency, unattributed journal difference, lost-response retry and batch inverse authority.
4. **Benefits, caps and openings (L).** Inclusion matrices, noncash pairing, employer contributions, YTD cap/threshold
   locks, midyear opening and explicit adjustment lifecycle. **Done when:** P2/P4/P6/P7/P8 balance and incomplete bases
   block computation. **Tests:** independent bases, competing same-date posts, cap boundary, backdate refusal,
   paid/closed-period adjustment report, no negative formula tax, preserved opening provenance and R186 reversal.
5. **Owner-entered tables (M).** Validated schedules/elections/variants, publishing, calculation trace and manual
   override evidence. **Done when:** P3's modern and legacy checks reproduce every intermediate without shipped
   rates. **Tests:** gap/overlap/continuity, boundaries, annual credits versus per-check extra, exemption scope,
   expiration, missing special-case treatment, changed-rate stale previews and no network access.
6. **Time and commissions (M).** Source portion claims, immutable handoff consumption/release, R189 settlement links
   and report projections. **Done when:** P5/P6 pay each source once with no double commission expense. **Tests:**
   concurrent consumers, wrong employee/amount/currency, partial time, billed-time independence, payment shares,
   handoff release once and paid-return recovery with no deduction.
7. **Runs and both-width finish (M).** Optional multi-employee runs, resumable children, whole-unit inverse planning,
   presets and full browser workflows. **Done when:** P1/P2 run reverses together or refuses before any inverse.
   **Tests:** interrupted post/reverse, stale child, later YTD/check/remittance/export dependency, revoked rights,
   keyboard/touch at 390 and 1440, layer-off cleanup and agent continuation.
8. **Form data and close (M).** Review worksheets, mappings, missing-field handling, immutable local export and
   outside-filing record, correction differences. **Done when:** P7 and employee/quarter/year reconciliation pass
   without calling output filed or silently filling missing data. **Tests:** masked identities, quarter/year/date
   boundaries, fractions-of-cents difference, FUTA cap/credit inputs, export reversal blocker and closing trial.

**Closing trial.** A fresh agent using actual MCP help and a person alternating 390/1440 px enter fictional owner
rates, prepare P1, inspect its gross-to-net and post/record a check. Remit $120 and explain the $30 still owed. Enable
benefits/tables and reproduce P2/P3; bring P5's handoff through payroll and record half its net payment. Explain
commission cash paid versus remaining net and amounts withheld. Consume time once; try the same claim again.
Post a two-employee run, reverse it through `batch reverse`, then repeat with a printed child and observe a complete
refusal. Prepare form review data and show missing identifiers, deposits versus liabilities and immutable export
history. Independently total every source, liability and ledger entry against the fixtures. Record first-attempt
results and agent help friction, correct material failures and repeat failed scenes before the build closes.
