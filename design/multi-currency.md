# Multi-currency and non-US tax (R127)

Design contract, 2026-11-09. Approved scope: **“Multi-currency and non-US tax: foreign-currency ledgers,
revaluation, and tax regimes beyond US sales tax, after a design pass. Approved (Kabe 2026-10-31: \"Full approve,
hold off on 113 125 155\"); design queued.”** This is the design pass, not an implementation. Commands and tables
below are proposed. Existing single-currency US books retain their behavior and every historical posting.

**Outcome.** Bill and pay in the currency agreed with a customer or vendor, reconcile a foreign bank in its own
units, and explain the home-currency result, including exchange differences. Separately, collect output tax,
recover eligible input tax and prepare a traceable VAT/GST return worksheet. Neither layer needs the other.

## 1. The anchor and the existing core

Reference inspection on 2026-11-09: the anchor enterprise 24 guide's contents and relevant PDF/printed pages 11
and 65. Its multicurrency references are prepayment exclusions, not a description of valuation mechanics. Do not
attribute an exchange algorithm to that guide. Supplementary anchor desktop help, “Set up and use Multicurrency”
(article `L3AqaWJ9i_US_en_US`), establishes one home currency, one currency per party/account, captured transaction
rates, matching settlement currencies and home-currency reports. The anchor's support answer “Adjust Foreign
Currency Vendor Balances” (community topic 17272) describes selecting currency, rate and accounts, calculating a
home-currency adjustment and saving it. Adopt those entry and review patterns. The anchor makes activation permanent;
Bookflow instead permits stopping new use while retaining history and cleanup. Downloaded rates are not adopted.
The exact balancing, basis consumption and VAT rules below are Bookflow contracts, not inferred anchor internals.

The 2026-09-03 currency decision D-004, read from the main checkout's private decision log, says: single immutable
home currency; foreign-tagged input converts at posting and retains original amount, currency and rate; Exchange
Gain/Loss account. R127 adds an opt-in ledger mode without changing that established route. The old blueprint's
“never in scope without a new design pass” restriction is satisfied by this document, not erased retroactively.

Existing owners under `src/bookflow`:

- `core/money.py` uses integer minor units and the bundled currency table's exponents; parsing rejects floats and
  excess decimal places. `core/exchange.py` canonicalizes positive decimal rates (1–12 integer digits, at most 18
  fractional digits) and converts using integer intermediates with half-even rounding and positive signed-64 bounds.
- `company/rates.py` provides versioned owner-entered rates, exact date/from/home lookup, `E_NO_EXCHANGE_RATE`, audit
  and paginated reads. There is no provider or last-known-rate fallback. `journal_foreign.py` captures original
  money, rate and source, preserves unchanged facts on correction and verifies their conversion independently.
- `company/journals.py` writes immutable revisions, posting batches, posting lines and attributed sources. Its
  present lines are home currency, accounts must match home, and generated effects are independently validated.
  Foreign tags are evidence, not balanced foreign holdings. `journal_models.py` supplies strict money/precision checks.
- `company/tax_calculations.py`, `tax_policy.py` and `tax_schema.py` capture tax rules and permanent tax ordinals.
  Policies are legacy line-component half-even, line-combined half-up and invoice-combined half-up, with exact
  numerators and deterministic component allocation. Preserve all three and their historical selection.
- `sales_tax_reports.py`, `sales_tax_payments.py` and `sales_tax_adjustments.py` reconcile US agency liabilities from
  immutable effects, including credits, remittances, adjustments and unattributed entries. Payment-receipt tax basis
  is explicitly unsupported; a financial cash-basis report does not make tax cash-based.
- `ledger_reports.py`, `balance_reports.py`, `report_basis.py` and `cash_report_view.py` own dated financial projections,
  report basis and stable authorized paging. `hub/permission_runtime.py`, `permission_access.py` and
  `agent_authority.py` own current authority. `company/batches.py` owns effect capture and resumable native inverses.

Extend those owners and the shared registry. Do not put accounting in adapters, turn the old journal converter into
a foreign subledger, or silently reinterpret old `original_currency` columns as outstanding foreign money.

## 2. Layers and the simple route

| Company layer | Default | Gives | Requires |
|---|---|---|---|
| Multi-currency ledgers | Off | Foreign parties, accounts, documents, settlement and realized FX | Existing immutable home currency; explicit account mappings |
| Currency revaluation | Off | Dated unrealized adjustments and valuation review | Multi-currency ledgers |
| VAT/GST | Off | Input/output tax codes and return worksheets | Registration and accounts; no FX requirement |
| Bulk currency/tax maintenance | Off | Imports and mass assignments | Relevant layer; existing reversible batches |

Single-currency users see their present lists, forms, defaults, commands and reports. Existing foreign-tagged journals
remain available with the layer off. Enabling posts nothing, fetches nothing and changes no old amount, rate, tax
policy, revision or report total. Existing parties resolve to home currency; existing account currency fields retain
their values. New metadata is additive, with no posting backfill. Historical facts remain legible in portable copies.

Simple FX route: Settings → enable Multi-currency → choose existing home realized gain/loss and rounding accounts →
create a EUR customer and EUR AR/bank accounts → enter the date's EUR-to-home rate → invoice → receive payment.
Revaluation, tax, imports, pipelines and approvals are unnecessary. VAT alone: enable VAT/GST → add registration,
agency and input/output/settlement accounts → publish a code → post sale or bill → prepare a worksheet.

Home currency is never changed here. A used party/account currency is immutable; create a separate profile/account
for another currency. Unused draft-only profiles may change with a versioned preview that invalidates their drafts.
Jobs inherit their parent's currency; incompatible reparenting and cross-currency merges refuse. Customer and vendor
profiles remain separate. Bank, credit card, AR and AP have one denomination each; income, expense, equity, inventory
and tax control accounts remain home-denominated but accept foreign transaction attribution through native owners.
AR/AP accounts are explicitly selected per currency; no silently created chart entries. FX system accounts cannot
be monetary accounts subject to revaluation. Inventory cost and asset basis stay home currency at acquisition.

Disabling blocks new foreign business, not settlement, correction, credits, revaluation reversal or history of
existing obligations. Existing foreign bank holdings may still be disposed of and reconciled. Revaluation can be
stopped independently; dependent new-use switches turn off before FX does. VAT disabling similarly permits return
completion and corrections for captured tax. No setting hides a liability or grants a permission.

## 3. Rates, posting units and rounding

A rate always means **home major units for one foreign major unit**: EUR/USD `1.125` means EUR 1 = USD 1.125.
Use existing `rate set/show/query` and exact-date lookup. A document may capture an explicit owner-entered override;
it is not silently written to the table. Show date, orientation, source, rate ID/version if selected, actor and
reason. A missing date refuses; “copy this earlier rate to this date” is an explicit versioned write. No inverse,
cross-rate triangulation, live lookup or implicit reuse. Home-to-home is exactly one, without a rate record.
Rate edits affect future selections only. Preview fingerprints include the chosen rate version; execution after an
edit refuses stale selection. Replaying a completed operation uses its captured rate, never today's table.

**Two measures, not two ledgers with independent truth.** Existing `posting_lines` remain the authoritative home
GL. New `currency_posting_lines` are immutable transaction-unit legs attached to the same posting batch and source
identities. Every new FX batch must balance in home GL **and separately in every transaction currency**. Each unit
leg carries currency, integer debit/credit, account/source identity and its allocated home effect. Several unit legs
may contribute to one home line; a foreign leg that converts to zero home units is allowed in this new owner only.
The existing positive-only converter and legacy journal validation are unchanged. Independent validation checks
both sums, ownership and the exact conversion/allocation; supplying a balanced but unrelated sidecar is forbidden.

For a EUR invoice, Dr AR EUR 100 / Cr Sales EUR 100 balances the EUR units while its home projections balance USD.
Sales' EUR units describe transaction activity, not a EUR monetary holding. Home-only adjustment legs carry home
transaction units and zero foreign units. No unit leg changes a foreign account's denomination. For cross-currency
bank exchange, use a dedicated system FX clearing owner: outgoing currency Dr Clearing / Cr Bank, incoming currency
Dr Bank / Cr Clearing. Their home clearing difference is closed to realized FX by home-only legs. Each currency
bucket balances and home clearing ends at zero per operation. There is no user-entered unbalanced currency plug.

For positive foreign units F, exponents f and h, and rate integer numerator r with scale s, exact home units are
`F * r * 10^h / 10^(f+s)`. Round half-even: a tie increments the quotient only when it is odd. Negative adjustments
round the absolute value and restore the sign. Persisted amounts and totals must fit signed 64-bit; intermediate
integer arithmetic is unbounded. Reject floats, booleans, unknown currencies and overprecision; never round input.
The currency exponent and calculation version are captured so a later currency-table update cannot rewrite history.

**Allocation fixture.** USD home, EUR invoice of two net lines EUR 0.01 and EUR 0.01, rate `1.5`, no tax. Total
EUR 0.02 converts to USD 0.03. Debit AR 3 cents; credit each net line's floor (1 cent), then give the remaining cent
to the first stable line ordinal: credits 2 and 1. Do not round both to 2. For ordinary documents, convert the gross
control total once; allocate home units to net/tax components by flooring exact conversions, then largest fractional
remainders, ties by permanent tax/line ordinal then binary component ID. Opposite-side components must sum to gross.
Credits copy original allocations; they do not repeat rounding at today's rate. A single EUR 0.01 at `1.5` rounds
to USD 0.02; at `2.5` it rounds to USD 0.02. JPY 100 at `0.0067` converts to USD 0.67, not USD 67.

Explicit multi-line FX journals are one transaction currency per entry, plus generated home-only adjustments.
Their debit and credit foreign totals must already agree. Convert that common total once and allocate each side
independently by the rule above. Rounding is disclosed in component allocations; the configured rounding account
is reserved for explicit settlement residual classification, never used to fix an arbitrary unbalanced input.
Legacy foreign-tagged journals still use their old per-line conversion/rejection rules and are labelled “Converted
input; no foreign balance.” Their effective posting currency is home even if their evidence tags differ.

## 4. Documents, settlement and revaluation

Sales, estimates/orders, bills, credits and payments capture the party's currency. Monetary amounts, prices and tax
are in that currency; quantities retain existing exact units. Conversion from work/order to posting revalidates the
posting date's rate, with previewed changes; it does not silently use the estimate date. Price levels must match the
document currency; a home catalogue price needs an explicit captured conversion or an entered foreign price.
Inventory receipts capture home acquisition cost; inventory issues/COGS retain historical home costing, with separate
balanced home-only batches. Neither inventory, fixed assets, revenue nor expenses are periodically revalued.

Applications require exact party/component ownership, matching AR/AP account and document currency under existing
settlement rules. A EUR invoice is settled by a EUR receipt, even if its deposit destination is a USD bank: record
both the EUR applied units and actual USD received. That is an explicit FX settlement, not a USD receipt allocated
to EUR debt. Bank fees are separately entered expense lines. Unsupported destinations/cross-foreign settlement
require an explicit bank exchange first; never use an invented rate. Unapplied monetary credits carry their own
currency and home basis; application consumes both sides' bases and records their difference once. Non-refundable
customer/vendor advances require the separately designed treatment in K3, not a guessed monetary classification.

Each open AR/AP source keeps original foreign units and historical home basis, remaining units/basis, and separate
unrealized adjustment allocations. Partial settlement consumes basis using cumulative proportional half-even:
`target = round_half_even(original_basis * cumulative_units_consumed / original_units)`; this operation consumes
target minus prior consumption. On the final application consume every remaining unit of basis. Each revaluation allocation captures its own units outstanding when created; use those units as its denominator
and consumption since that allocation as its cumulative numerator. Never reuse the original invoice denominator
for a later adjustment. Corrections undo captured allocations exactly.
Foreign bank/card holdings use acquisition lots, FIFO by accounting date then stable source ID, with each lot's
historical basis and adjustment balance. A disposal consumes lots by the same cumulative rule. Overdrafts/card debt
are separate liability lots; settlement closes them before creating an asset lot. No division by a zero net balance.
A backdated acquisition/disposal that would change an already-consumed FIFO ordering refuses until the dependent
disposals are reversed, even without a revaluation. Closed dependencies require an explicit current-date correction.

**Worked gain fixture, all home figures USD cents.** Invoice EUR 100 at `1.10`: Dr AR 11,000 / Cr Sales 11,000;
EUR units Dr AR 10,000 / Cr Sales 10,000. At month end `1.20`: Dr AR 1,000 / Cr Unrealized FX 1,000; EUR change zero.
Receive EUR 40 into a USD bank at `1.25`: consume historical AR 4,400 and its uplift 400; Dr Bank 5,000 / Cr AR 4,800 /
Cr Realized FX 200, plus Dr Unrealized FX 400 / Cr Realized FX 400. Net realized gain is 600; remaining AR is
EUR 60, historical USD 66, uplift USD 6, carrying USD 72. EUR settlement units Dr FX Clearing 4,000 / Cr AR 4,000;
home units route Bank through the same clearing owner, which closes to zero. Adjustment/reclassification legs are
home-only. The unrealized balance is now 600, not 1,000. A bill mirrors the signs: rising home cost is a loss.

Without revaluation the same receipt credits AR 4,400 and realized gain 600. With a EUR bank destination the bank
acquires EUR 40 at USD 50 basis; later conversion of that holding realizes its own additional difference. No double
count of the receivable gain. Revaluation of a still-held bank lot is unrealized in this design, regardless of how
the anchor labels bank adjustments; disposal reclassifies its accumulated adjustment to realized.

`currency-revaluation prepare` takes cutoff date, explicit currencies/accounts, exact dated rates and source
versions. It snapshots **as-of** open sources/lots, including older dated settlements, not today's remaining column.
For each source, new carrying target is the rounded remaining units at the selected rate. Delta is target minus
historical remaining basis minus prior effective adjustments. Post a linked home-only journal per account/currency
with per-source allocations and separate gain/loss control. Zero deltas post nothing. Rerunning unchanged sources
and rates produces zero; different rates produce only the delta. The preview distinguishes omitted eligible accounts.

Revaluation journals persist; they do not automatically reverse next morning. Settlement consumes/reclassifies their
allocated adjustments as above. Post requires an open date and unchanged source/rate watermark under the writer gate.
Reject a cutoff earlier than later effective settlement/revaluation events on selected sources; reverse those
eligible dependents first. After a posted run, a backdated posting affecting its scope refuses until dependent runs
are reversed/recomputed, including a new invoice that would have belonged in the run. Closed runs cannot be rewritten;
correction must use an explicit current open date with an audit link. No silent recalculation of a filed period.

Corrections, voids and deletion use native revision/cancellation rules and exact inverse home/unit/basis/tax facts.
A consumed source cannot be edited independently of its payment, return or revaluation dependencies. Closed dates
refuse original-date inverses; a current-date correcting document is separately previewed and never labelled an
historical undo. Reconciled/printed payment restrictions remain. Direct journals, imports and register edits cannot
bypass subledger ownership. Newly enabled foreign paths must include their whole correction/return/deletion lifecycle.

## 5. VAT/GST beside US sales tax

A versioned `tax_registration` identifies agency/vendor, registration number, effective dates, home filing currency,
period calendar, invoice-date tax basis and mapped input-recoverable, output-payable and net-settlement controls.
The agency's vendor identity does not turn a return into an AP bill. Controls are reserved to the tax owner and distinct
from `sales_tax_payable`. VAT can coexist with US history and registrations, but each document selects exactly one
model: `us_sales_tax`, `vat_gst` or `none`. Missing VAT codes refuse; they do not mean exemption. Existing US documents
and corrections retain their captured US model and policy. A home-only US sale follows exactly its existing path.

Published codes carry effective dates, registration, rate in integer millionths of a percent (0–100%), treatment
`standard|zero_rated|exempt|out_of_scope`, input recoverability percentage, inclusive/exclusive pricing and worksheet
box mappings. Distinguish zero-rated from exempt turnover even when both yield zero tax. Codes and registration
revisions are immutable once published; edits create prospective revisions. Item/party defaults only suggest codes.
The document captures selected revision, tax point, stable ordinals, rates, net/tax/gross, recovery and mapping facts.
Tax point defaults to document date; an explicit different tax point is previewed and checked against locked returns.
No automatic jurisdiction inference or claim that a generic code constitutes a country's compliant tax configuration.

Initial VAT policy is **per line, half-up**; it is separate from existing US policies. For exclusive pricing,
`tax = round_half_up(net * rate / 100000000)`. For inclusive gross G,
`tax = round_half_up(G * rate / (100000000 + rate))`, and net is G minus tax. Half-up means absolute exact halves
away from zero, with credit signs restored after calculation. Only one flat VAT component per line in this scope.
Recoverable input is `round_half_up(tax * recovery / 100000000)`; nonrecoverable is the remainder, included in expense
or inventory/asset acquisition cost. Discounts reduce the captured tax base through native discount allocations.
Linked returns consume original net/tax/recovery by cumulative proportional allocation with the final return taking
all remaining units; do not apply new codes or a new rate to an old credit. Unlinked credits require explicit codes.

**Tax fixture.** USD home sale EUR 100 exclusive, 20% VAT, `1.10`: Dr AR EUR 120 / Cr Sales EUR 100 / Cr Output Tax
EUR 20; home Dr AR 13,200 / Cr Sales 11,000 / Cr Output 2,200. Purchase EUR 60 inclusive, 20%, 75% recoverable:
net EUR 50, tax EUR 10, recoverable EUR 7.50, cost EUR 52.50; home Dr Cost 5,775 / Dr Input 825 / Cr AP 6,600.
EUR units balance 6,000 on each side. Net return liability is USD 13.75. EUR 0.03 exclusive at 20% produces tax
EUR 0.01 (exact 0.006); EUR 0.03 inclusive produces tax EUR 0.01 (exact 0.005), net EUR 0.02. These are VAT rules,
not a replacement for the legacy half-even US result. Convert already-calculated components with §3's allocation.

Tax controls are home obligations at the captured document rate. FX changes on AR/AP do not revalue output/input
tax, turnover or prior return boxes. Initial filing currency is home and tax conversion uses the document rate;
statutory alternative rates, cash schemes, reverse charge, compound taxes and other filing currencies are K2 scope.
Unsupported treatments fail explicitly before posting. Financial report cash basis never changes this tax-point rule.

`tax-return prepare` builds a draft worksheet for one registration and inclusive period, from immutable tax events.
Show output, recoverable input, nonrecoverable tax, zero/exempt/out-of-scope bases, adjustments, net payable/refund,
source documents and GL reconciliation. Include every control effect once; unattributed effects are shown and block
finalization. Drafts reserve nothing. `finalize` freezes mapping revision, source IDs, amounts and a watermark, claims
each event once under a uniqueness constraint, and posts Dr Output / Cr Input / Cr Net Settlement for the fixture
(2,200 / 825 / 1,375). Other signs reverse for a refund. This is a local worksheet, not an electronic submission.
Overlapping periods for the same registration refuse. Carry-forward balances remain separate from current tax boxes.

Return settlement records verified local bank payments/refunds against the net control, not another tax expense.
Partial remittance and credit carry-forward link to a return and cannot overconsume it. Finalization is reversible
while no remittance, later return carry-forward or recorded external filing depends on it. An explicit `mark-filed`
records the person's already-completed external act and reference; it sends nothing and does not create money.
Thereafter retain the frozen worksheet: late/backdated tax changes become linked correction events in the next open
worksheet with original tax-point/period visible. Never silently amend an externally filed return. A wrongly recorded
filing status needs an attributed correction, not erasure; actual external amendment remains outside this layer.

## 6. Storage and invariants

All accounting lives in the company file; no new service or hub monetary data. Proposed additions:

| Owner/table | Captured facts and constraints |
|---|---|
| Company settings and party/account currency profiles | Independent switches, currency, explicit mappings, version; used denomination immutable |
| `exchange_rate_revisions` | Append-only selected rate revisions behind existing rate IDs; no invented revisions for old history |
| `document_currency_profiles` | Revision, mode `home|converted_input|foreign_ledger`, transaction/home currencies, exponents, rate facts, conversion version |
| `currency_posting_lines` | Batch/source/account, currency debit/credit, home allocation link; immutable and exact-inverse linked |
| `currency_basis_lots/events` | Source identity, units/basis acquired and consumed, adjustment allocations, effective date, revision and inverse link |
| `currency_revaluation_runs/allocations` | Scope, cutoff, rates, source watermark, targets/deltas, journal and batch IDs, dependency fingerprint |
| `tax_registrations/code_revisions` | Agency, dates, mappings, percentages, treatment, calculation version and recovery policy |
| `vat_tax_events` | Original/correction source, ordinals, tax point, original/home base/tax/recovery, code and registration snapshots |
| `tax_return_revisions/claims/settlements` | Frozen source set, box mappings, period, reconciliation, GL links, filing evidence and inverse claims |

No new required field on an existing request with layers off. Missing legacy profile means its existing home GL and
optional converted-input evidence, never an FX lot. New schema version fences older writers that cannot enforce FX
ownership. Existing amount fields keep their meaning; outputs add explicitly named transaction/home measures.

Before commit, independently derive balances from original typed intent and captured source facts. Verify home GL,
each unit currency, component-to-control totals, nonnegative remaining capacities, exact final consumption, zero
clearing and tax-event-to-GL ties. Authority, feature state, versions, dates, rate selection and dependency watermarks
are rechecked under the existing transaction/publication gates. Journal, facts, audit, durable operation receipt and
batch effect commit together. An error leaves none of them. A complete retry returns the same identities; changed
input with the same operation key refuses. Audit records actor, interface, bound principal and reason/directive.

## 7. Commands, authority and agent use

These are typed registry contracts on Python, CLI, MCP, HTTP and browser. Writes take operation key, expected versions
for touched owners and ordinary reason/context. Prepare/dry-run returns exact entries, unit balances, rates, sources,
versions, fingerprint and blockers; it grants no authority and reserves no funds. Reads paginate with bound filters,
audit watermark and current permission checks. Full totals cover the selection, not just the visible page.

| Commands | Main inputs → result |
|---|---|
| `company update`, existing party/account create/update | switches, denomination, mappings → versioned settings/profile; no postings |
| Existing `rate set/show/query`; new rate import | date, foreign currency, decimal rate, expected version → captured owner-entered rate/batch |
| Native sales/purchase/journal/payment/transfer commands | transaction currency, rates, native lines/application versions, actual bank money → home/unit plans and immutable postings |
| `currency-revaluation prepare/show/query/post` | cutoff, selection, rates; run/version/fingerprint → draft or journal IDs and batch |
| `report currency-balances`, `report exchange-gains-losses` | as-of/range, currency/account/party → original basis, realized, adjustment and carrying totals |
| `tax-registration`, `tax-code` create/update/publish/show/query | dates, accounts, policy/mappings → versioned records |
| `tax-return prepare/show/query/finalize/reverse/mark-filed` | registration/period, versions/fingerprint, open date or evidence → worksheet, journal or filing record |
| `tax-return settle`, `vat adjust` | return or source, amount, date, bank or authorized adjustment reason → attributed postings |
| `batch reverse` | batch and preview fingerprint → exact native inverses, blockers and resumable progress |

Add scoped `currency.manage`, `currency.revalue`, `currency.read`, `vat.manage`, `vat.post`, `vat.return` and `vat.read`
capabilities in a new permission-catalog delta, never edit accepted ancestors. Management defaults to company admin;
post/revalue/return to standard bookkeeper, read to member, each still requiring native record/report authority.
Agent grants are explicit and effective rights intersect with the bound principal. Native invoice/bill authority
covers FX posting; it does not grant rate maintenance, revaluation or tax management. VAT posting additionally needs
`vat.post`. Return remittance requires return and bank-payment rights. Denies work identically on every surface;
company visibility precedes selector errors, totals, audit and batch detail. No interface or human-only shortcut.

Reuse `E_NO_EXCHANGE_RATE`, precision/range, version, permission and closed-period errors. Add actionable
`E_CURRENCY_MISMATCH`, `E_CURRENCY_DEPENDENCY`, `E_REVALUATION_STALE`, `E_TAX_MODEL_UNSUPPORTED`,
`E_TAX_RETURN_LOCKED` and `E_TAX_UNATTRIBUTED`, with only authorized affected IDs and the correcting command.
Off-layer creation returns `E_FEATURE_DISABLED` naming the setting. Unsupported workflows refuse before writing.
Register currency/tax setup and broad batch changes as confirm-first under principal-directives; ordinary clearly
requested invoicing needs no new approval queue. Help explains rate orientation and “record payment” versus sending
money. Agents discover “foreign invoice”, “exchange gain”, “revalue”, “input tax” and “VAT return” without source reads.

## 8. Reports, reversible batches and screens

Financial statements, trial balance, GL, budgets and company-wide totals remain **home currency**, summing captured
home postings, never translating again at report time. AR/AP detail shows foreign open units, historical home basis,
posted valuation adjustments and home carrying value; totals tie to controls at the same cutoff. Currency activity
reports group foreign totals by currency and never add EUR to JPY. A what-if valuation is labelled unposted and does
not replace GL results. Realized/unrealized reports explain opening adjustments, new valuations, reclassification and
closing adjustments. Bank reconciliation compares native bank units; home-only valuation is not a statement item.

Cash-basis projections use captured historical source allocations for collected revenue/paid expense and settlement
rates for cash and realized FX. In §4, recognize USD 44 revenue plus USD 6 realized gain, never USD 50 revenue plus
that gain. They retain bank holding FX effects and
exclude uncollected AR/AP revaluation together with those receivables/payables. Remove both the control adjustment
and its unrealized counterpart; retain realized settlement gain once. Show basis explicitly. Accrual tax worksheets
ignore the financial basis selector. Exports, printed statements, saved reports and cached totals use these same
owners, with explicit currency headings and drill-downs to rate, lot, tax event and revision.

Rate imports, bulk currency/code assignments, transaction imports, revaluation runs and bulk tax adjustments record
one shared batch and every landed effect inside its native transaction. `batch reverse --dry-run` preflights the
whole unit under the reverser's current rights. Reverse dependents newest first; restore list/rate values only if
versions still match; used rate revisions remain captured history. Source documents, later edits, applied payments,
revaluations, return claims, filed evidence and reconciliation can block inverses. No skip-changed “success”.
Sliced reversal uses native adapters, durable inverse IDs and lazy registration in a fresh CLI process; interruption
resumes without repeating a unit. Report `reversing/blocked` until all effects are cancelled. It is one business undo,
not a claim that a multi-slice batch rolls back atomically. A closed-date batch refuses rather than silently moving
its inverse; a separately requested current-date correction is clearly different and preserves the original batch.

At **1440 px**, foreign forms show currency and rate beside total; expandable details show home totals, basis and
rounding. The currency workspace lists dated rates and accounts, with revaluation Sources → Differences → Post.
VAT codes and return worksheets show net/output/input/recovery and linked documents. At **390 px**, use stacked
amounts with currency labels, sticky totals/actions, keyboard-safe sheets and 44 px targets; no clipped monetary
columns. A human can resume an agent's draft by ID. Off switches keep new controls out of ordinary US entry;
historical cleanup links remain visible. Rate warnings say the exact missing date and open the permitted rate form.

Acceptance:

1. Off-layer historical US fixtures, reports, requests and foreign-tagged journal corrections are unchanged after migration.
2. Every new operation and inverse balances home and each transaction currency; zero-home conversions retain unit facts.
3. §3's residual, JPY exponent, §4's partial settlement and §5's VAT fixture reconcile independently from posting sources.
4. Revaluation repeats produce zero; settlements consume/reclassify adjustments once; backdating cannot stale a posted run.
5. Native bank balances reconcile; aging and currency reports tie to GL; cash/accrual differences are explained, not hidden.
6. VAT return claims are unique, net controls reconcile, corrections retain filed snapshots and US tax remains separate.
7. Concurrent rate/period/authority/source changes refuse stale previews; crash/retry and sliced undo preserve exact effects.
8. Actual MCP and browser users continue each other's work at both widths with identical authority and useful errors.

## 9. Settled and K-questions

**Settled.** Immutable home currency; opt-in additive ledgers; one currency per used party/account; owner-entered
exact-date rates; captured dual measures and explicit clearing; half-even FX and deterministic allocation; persistent
revaluation with settlement reclassification; FIFO bank basis; separate home tax controls; per-line half-up VAT;
local worksheets; shared authority and native batch reversal. These are prospective versioned choices, not requests
for approval. The K defaults bound initial implementation; they authorize no outside service or irreversible act.

- **K1 — Rate services/outside parties.** Add a provider or automatic downloading? **Default:** no; owner-entered,
  dated rates and local imports only. A provider requires separate scope, privacy, cost and release authorization.
- **K2 — Jurisdiction scope.** Which statutory schemes and return formats must be certified first? **Default:** generic
  invoice-date VAT/GST, flat per-line inclusive/exclusive tax, partial input recovery and home-currency worksheets;
  no statutory filing/certification claim, electronic submission, reverse charge, compound tax, cash tax scheme,
  alternative statutory FX rate or non-home filing currency until its separate accounting contract is approved.
- **K3 — Additional monetary workflows.** Include non-refundable foreign advances, hedging, cross-company FX,
  payroll FX or foreign POS tender in this pass? **Default:** no; ordinary invoices, bills, receipts/payments,
  monetary credits/refunds, bank/card holdings, exchanges and eligible journals. Unsupported foreign extensions
  refuse explicitly; existing home workflows continue. Inventory cost integration is included, not periodic stock FX.
- **K4 — Historical conversion.** Adopt old converted-input tags or change home currency? **Default:** neither;
  historical home books stand. Any opening foreign holdings require a separately approved, reconciled cutover with
  source units and home basis, balanced nonduplicating opening entries and an open date; no inferred foreign debt.

## 10. Build increments

S is narrow, M is a bounded workflow, L crosses accounting owners. Each increment includes demo fixtures, generated
references and typed help; tests below are future build requirements. This design-only change adds no tests or code.

1. **Settings, denominations and rate revisions (M).** Add optional profiles, mappings, rate history and permissions.
   **Done when:** existing companies open unchanged; new foreign parties/accounts and dated rates are discoverable.
   **Tests:** off-layer migration parity, exponent/precision limits, immutable used currency, job/merge mismatches,
   concurrent rate versions, no network access, denied management and old-writer admission.
2. **Balanced currency posting core (L).** Unit legs, home allocation, independent validation and native journal ownership.
   **Done when:** every new FX batch balances both measures and legacy converted input keeps its exact behavior.
   **Tests:** §3 fixtures, zero-home legs, overflow, forged sidecars, source ownership, clearing, correction/void,
   closed dates and immutable migration. No generic home-only validator weakening.
3. **Foreign sales and purchasing (L).** Invoice/bill/receipt, work conversion, price capture, credits and home inventory basis.
   **Done when:** a foreign sale and purchase complete native lifecycle with captured tax and historical cost intact.
   **Tests:** order-date versus posting-date rate, discounts/returns, US tax parity, group/item pricing, inventory receipt/
   issue costs, missing support refusals, deletion bypass and all-interface permissions.
4. **Settlement, bank exchange and realized FX (L).** Application basis, monetary credits, FIFO holdings and reconciliation.
   **Done when:** partial/final payments consume exact units/basis and realized gain reconciles to actual bank money.
   **Tests:** §4 without revaluation, cross-home clearing, fees, refunds/unapply, competing applications, overdrafts,
   full-consumption residuals, currency mismatch, reconciled dependencies and permanent retry.
5. **Revaluation and financial reports (L).** Prepared runs, linked journals, adjustment consumption and home projections.
   **Done when:** §4 with revaluation reconciles, unchanged repeat posts nothing and reports tie at every cutoff.
   **Tests:** stale rates/sources, backdated additions, later settlement refusal, partial reclassification, bank lots,
   zero-balance cleanup, cash/accrual projections, paging/export parity and closed-period reversal.
6. **VAT/GST calculation and capture (L).** Registration/code revisions, purchase recovery and immutable tax events.
   **Done when:** §5 sale/purchase and return fixtures balance, and US policy results stay unchanged.
   **Tests:** inclusive half ties, zero/exempt distinction, discounts, partial recovery, final credit residuals,
   home allocations, tax-point locks, unsupported treatments, stock cost and code revision concurrency.
7. **Return worksheets and local settlement (M).** Draft/finalize, claims, net-control journals, remittance and filing evidence.
   **Done when:** worksheet boxes tie to events/GL, duplicate claims refuse and filed corrections remain traceable.
   **Tests:** overlapping periods, concurrent finalization, unattributed control effects, refunds/carry-forward,
   partial remittance/void, frozen mapping versions, external-evidence blockers and late tax corrections.
8. **Batch and browser completion (M).** Native inverse adapters, imports, currency/tax screens and portable-copy trial.
   **Done when:** every acceptance criterion passes and all batch changes undo through the existing door or name blockers.
   **Tests:** whole-unit preflight, lazy adapter loading, interrupted/resumed inverse and revocation, rate/code imports,
   390/1440 px keyboard/touch, no unauthorized HTML/JSON/export data and backup/restore with complete local facts.

**Closing trial.** A fresh agent using actual MCP help enters the rates and prepares the EUR invoice; a person posts
it in the browser, revalues and receives EUR 40. Swap actors to inspect basis/gains and finish settlement. Repeat with
an AP bill, foreign bank holding and USD exchange; independently add each currency's legs and the home GL. Prepare
the VAT fixture's worksheet, finalize, partially remit and inspect a late credit. Attempt a stale rate preview,
backdated invoice, closed-period inverse and denied return. Import a mistaken rate/code batch, reverse it, then repeat
with a dependent transaction and an interrupted reversal. Open a portable company copy and reproduce every report
without network access. Record first-attempt help friction and repair/retest material failures before closing the build.
