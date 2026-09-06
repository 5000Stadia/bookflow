# Customer work controls and staged coverage

This is a planning inventory, not an implementation or parity claim. It expands
[Customer work and billing](../customer-work-and-billing.md) and blueprint
sections 10.3 and 13.1. Each stable key is a control family for the next owning
plan to implement or explicitly stage with its dependency. No command or schema
is established by a row here.

**Observed family** identifies independently phrased reference coverage to
consider. **Approved extension** identifies the project's authorized connected
proposal/statement-of-work, estimate, work-order and billing workflow. **Contract**
identifies existing project integrity requirements. These labels do not make
observed behavior an approved calculation rule. Detailed acceptance states,
partial-billing arithmetic, tax allocation, rounding residues, source correction
limits and overrun treatment are defined by the [progress billing contract](../specs/18-progress-billing.md),
not inferred from these reference-control families.

Related keys H, L, F, D and W refer to the existing
[service-sales inventory](service-sales.md); their complete controls are reused,
not repeated here. **Statements** refers to
[financial-statements.md](financial-statements.md), including its staged common
report controls. A related inventory's coverage status does not establish that
customer-work conversion exists.

## Documents and estimate controls

| Key | Field or behavior; basis | Next requirement or staged dependency | Related existing inventory / contract |
|---|---|---|---|
| CW01 | Connected customer/job workspace; approved extension | Offer proposal/scope → estimate → work order → invoice or paid sales receipt, with entry permitted at any stage. Show existing work and billing before offering the next action. | H1, H13, W2; customer-work workflow |
| CW02 | Proposal / statement of work; approved extension | Capture customer, job/site, scope, inclusions, exclusions, timing and commercial terms; allow review and conversion. Keep this distinct from a customer account statement. | H2–H4, F1–F2, W2 |
| CW03 | Shared document facts; observed family + contract | Reuse customer/job, contact/address, number/date, purchase reference, terms, representative/class, custom fields, message and memo controls where applicable. The next plan must enumerate applicability and defaults for each non-posting form. | H1–H6, H8–H11, H14, F1–F3 |
| CW04 | Estimate lines and commercial totals; observed family | Capture ordered items, descriptions, quantities, units, estimated cost, selling price and totals; show origins and permitted overrides. Estimated costs are operational facts, not booked expenses. | L1–L4, L6; blueprint 10.2 |
| CW05 | Cost, markup and price-level editing; observed family | Inventory cost defaults, markup percentage or price-level choice, total override and recalculation. The calculation plan must define cost basis, precedence, zero-cost behavior, precision and which inputs are authoritative. No reciprocal calculation rule is settled here. | L2, L4–L5 |
| CW06 | Quoted tax and totals; observed family + contract | Capture applicable exemption, taxable lines, tax choices/components and net/tax/gross facts. Conversion must show any supported change from the saved quote; partial tax arithmetic and later tax policies require the owning calculation plan. | L10–L12, F3 |
| CW07 | Alternative estimates and copies; observed family | Support separately identified alternatives for one job and copying into a new numbered estimate. A copy must not inherit acceptance, billing consumption or successful delivery. Define how alternatives share scope before conversion. | F6, W2 |
| CW08 | Availability, acceptance and closure; observed family + planning requirement | Keep active/inactive availability distinct from the customer's selected alternative, operational completion and billed state. Specify accepted revision, decision evidence and supersession/cancellation behavior; do not infer acceptance from an active flag. Retain declined and superseded history. | F5–F6, W2 |
| CW09 | Estimate/progress preferences and numbering; observed family | Inventory separate estimate and progress feature preferences, duplicate-number behavior and automatic closure after conversion. Item-creation markup percent/account, job-status labels and default billable-time/expense preferences belong to their master/source settings plans. Define visibility/default scope and preservation when disabled; a visibility preference cannot authorize duplicate billing. | H6, H13, W2 |
| CW10 | Find and open estimates; observed family | Customer/job, date, number, amount and status search; previous/next navigation and report drill-down. Include inspectable inactive alternatives with clearly labelled filters. | F5 |
| CW11 | Reusable scope and estimate templates; observed family + approved extension | Staged template lifecycle: reusable scope/items, optional quantities, new customer/date/number, independent destination identity. Reuse is separate from replaying a conversion. | F6, D1–D3 |
| CW12 | Save, preview and revise; observed family + contract | Apply common unsaved-edit, save/close/new, version, preview freshness and audit controls to operational forms. Preserve historical snapshots; master edits or layout changes cannot silently reprice a saved source. | H4, F4, F9, D2 |
| CW13 | Non-posting source documents; observed family + approved extension | Proposal, estimate and work-order creation, edits and status changes create no receivable, revenue, expense, tax liability or payment. Only an explicitly chosen accounting transaction creates financial effects. | W2; blueprint 10.2–10.3 |

## Work orders, conversion and progress billing

| Key | Field or behavior; basis | Next requirement or staged dependency | Related existing inventory / contract |
|---|---|---|---|
| CW14 | Work-order header and scheduling facts; approved extension | Number, customer/job, title, description, priority, site, assignees, scheduled/actual start and end. Preserve the blueprint's draft, scheduled, in-progress, on-hold, complete, invoiced and cancelled coverage while defining transitions and separate billing/payment measures. | H1–H4, W2; blueprint 13.1 |
| CW15 | Work-order lines; approved extension | Item, description, quantity, rate and billable flag; carry source facts without retyping and retain line ancestry. Record completed quantity separately from ordered quantity and billable/billed state. Completion must not bill nonbillable lines. Labor/time capture and dispatch automation remain separate dependencies. | L1–L4, W1–W2; blueprint 13.1–13.2 |
| CW16 | Source selection; observed family + approved extension | Show eligible sources for the selected customer/job with number, date, revision, amount and existing billing. Choose the agreed alternative explicitly; allow declining sources and entering independent actual work. | H13, W1–W2 |
| CW17 | Conversion preview and retained facts; approved extension + contract | Identify source revision/lines, destination type, carried customer/scope/items/quantities/prices/tax/notes/files, permitted overrides and resulting totals. Preserve saved facts; validate current posting eligibility without silently replacing them with master defaults. | H2–H4, L1–L4, L10–L12, F7, F9 |
| CW18 | Durable conversion and history; approved extension + contract | Create a linked destination while preserving the source. Both ends show revisions, status and links; multiple installments require more than one invoice pointer. Durable retry identity must survive ordinary request-cache expiry and return the original destination and its current state. | F5, F9, W2; blueprint 10.3 |
| CW19 | Shared billing consumption across the chain; approved extension + contract | Preserve source-line ancestry and exact live billed/remaining measures across estimate → work order → sale. A downstream copy of the same agreed work cannot reopen amounts already billed from its ancestor. Independent new scope must be distinguishable. | W1–W3 |
| CW20 | Full and remaining billing; observed family + approved extension | Offer full eligible work before any billing and only the unbilled remainder thereafter, including at completion. Show prior billing first; no second full sale for already consumed work. | W2 |
| CW21 | Whole-estimate percentage billing; observed family | Provide a percentage mode with a preview of each affected line. The conversion plan must define percentage base and whether an input represents this installment or a cumulative target; never leave the interpretation implicit. | W2, L2–L3 |
| CW22 | Selected lines and per-line progress; observed family | Select items independently and enter supported quantities, amounts or differing percentages. Explicitly address fractional material quantities, unit constraints and unselected work; a single project percentage is insufficient. | L1–L3, L7, W2 |
| CW23 | Estimated, prior, current and cumulative columns; observed family | Inspect estimated quantity/rate/amount; prior billed quantity/amount/percentage; current quantity/rate/amount/percentage; cumulative percentage and remaining work; tax and total. Distinguish read-only source/prior facts from editable current inputs and show recalculation in preview. | F3, W2 |
| CW24 | Progress column visibility and zero-current lines; observed family | Quantity/rate and percentage visibility controls preserve entered facts. Keep unbilled source context inspectable and allow zero-amount print suppression. The plan must specify non-posting display rows versus positive sale lines; never synthesize zero financial legs. | L1–L3, D1–D2 |
| CW25 | Exact partial amounts and tax; contract | The progress billing contract defines mutually exclusive selections, original-scope percentages, integer span allocations, exact quantities/net and ordinary per-installment tax. Full net reconciles to quoted net; installment taxes may differ from informational quoted tax. Settlement component allocation remains a distinct contract. | L2–L3, L11–L12, W2; blueprint 10.3–10.4 |
| CW26 | Source changes, overruns and change orders; observed family + contract | Show original scope, requested/accepted changes and cost/price impact. Specify authority, revision links and handling of billed lines before implementation; changing the source must not rewrite issued bills or silently permit overbilling. Formal change-order approval remains a staged design dependency. | F9, W2 |
| CW27 | Correcting or voiding linked bills; approved extension + contract | Preserve issued revisions and exact effects; define atomic allocation replacement/release, eligibility to rebill and source-edit limits in the conversion plan. A void/retry must not duplicate a sale or change unrelated issued installments to redistribute residue. | F9, W2; blueprint 10.2–10.4 |
| CW28 | Concurrent conversion and repeat requests; contract | Preview and execute against checked source/destination versions and current consumption. Atomically create the destination, lineage, allocations and audit, or reject without partial billing. Distinguish a retry from a deliberately new installment. | F4, F9, W2 |
| CW29 | Completion, invoice and paid-sale choice; approved extension | Completion alone proves neither billing nor payment. Offer an invoice for amounts owed, or a sales receipt only with actual payment facts, including received date, amount, method/reference and deposit destination as applicable. | W2, W6 |
| CW30 | Payment of existing invoices; approved extension | Show issued invoices and settle through customer payments; never replace an invoice with a second sale. Partially billed work offers only its remainder; settlements and receipts link to their actual obligations. Payment application and customer deposits remain owning-plan dependencies. | W9–W12; blueprint 10.4 |
| CW31 | Notes, files and source-document activity; approved extension + contract | Operational documents use notes/files/activity as a job log. Identify what carries forward, retain source links and access to original files/revisions, and distinguish internal notes from customer-visible content. | F1–F2, F7, F9; blueprint 12 |

## Reports, output and staged prerequisites

All report families below are planned. Financial columns need reconciliation to
their applicable posting controls; quantities, estimates, commitments and billing
progress need their operational sources. An estimated cost is not an actual cost,
a billed percentage is not physical completion, and neither is cash received.

| Key | Field or behavior; basis | Next requirement or staged dependency | Related existing inventory / contract |
|---|---|---|---|
| CW32 | Estimates by customer/job and billing against estimates; observed family | Inventory alternative/status listing and per-installment original/prior/current/cumulative/remaining billing with source drill-down. Define which estimate revision and alternatives enter totals. | F5, W2; Statements drill-down; blueprint 14 |
| CW33 | Estimated versus actual cost and revenue; observed family | Summary/detail by job and item, plus billed revenue against proposed revenue. Separate cost and revenue comparisons; actual cost requires supported expense/purchase/time attribution. A scope letter alone supplies no numeric revenue baseline. | W1–W2; Statements columns/groups; blueprint 14 |
| CW34 | Job and item profitability; observed family | Staged cost attribution and reconciliation: summary/detail, job financial results, item profitability and unbilled costs. Catalog cost or ledger-leg counts cannot substitute for actual job/item costs. | L7, W1; Statements comparative reports; blueprint 14 |
| CW35 | Job costs, unpaid bills and unassigned expenses; observed family | Staged purchasing/settlement and cost attribution: vendor→job and job→vendor summary/detail, job-cost detail, unpaid bills by job/vendor, and expenses missing job assignment. | W1; Statements columns/groups; blueprint 14 |
| CW36 | Cost to complete; observed family | Staged job-cost forecasting: summary by job and detail by item with over/under-estimate measures. Capture dated/versioned remaining-cost assumptions and separate them from actual cost and original budget; define variance and completion measures before output. | W2; Statements comparative reports; blueprint 14 |
| CW37 | Work in progress and committed costs; observed family | Staged purchase orders, labor/time and job forecasting: expected versus actual revenue and outstanding cost commitments. Define how commitments are relieved by actual bills/labor costs to prevent double counting. No revenue-recognition or accounting method is selected here. | W1–W3; Statements comparative reports; blueprint 14 |
| CW38 | Open purchase orders; observed family | Staged purchasing/receiving: open commitments by job and vendor, summary/detail as applicable, with source quantities and fulfillment/billing status. Work orders are not purchase orders. | W3; blueprint 10.3, 14 |
| CW39 | Time and billed/unbilled hours; observed family | Staged employee and subcontractor/vendor time: job summary/detail, person, service item/activity, person/job and person/activity views. Track billable separately from actually billed and retain invoice source links; person/service billing rates need an owning time plan. | W1; blueprint 13.2, 14 |
| CW40 | Job status, contacts and outstanding balances; observed family | Job/project status filters and contact listing; staged customer/job open balances and class aging require dated settlement sources. Keep job status, work-order status, billed status and paid status separately labelled. | H12, W9–W12; Statements columns/groups |
| CW41 | Common report controls; observed family + contract | Reuse date/as-of semantics, basis eligibility, snapshot metadata, bounded reads, complete totals, filters/columns/grouping/sort, summary/detail and drill-down. Stage saved definitions, comparison columns/charts, print/export/refresh and delivery under the common report plan. | Statements period, basis, identity/source, paging, columns/groups, reuse/discovery, display output; blueprint 14 |
| CW42 | Estimate/scope/work-order and progress layouts; observed family + approved extension | Staged reusable templates: screen versus print fields, cost/markup visibility, column headings/order, quantity/rate or amount layouts, progress columns, custom fields, logo/fonts and multipage output. Preserve facts when layouts change; explicitly control disclosure of internal cost/markup/notes. | D1–D3, F2, H14 |
| CW43 | Printing, files and delivery; observed family + approved extension | Staged render/print preview, immediate or queued printing where supported, PDF/export and separate send/later actions. Specify applicability per document rather than assuming all forms share queue options. Identify saved revision, artifact and recipients; retries never recreate accounting effects. | D4–D6; blueprint 13.4 |
| CW44 | Agent discovery and truthful action outcomes; approved extension | Shared typed business operations and examples cover create, convert, complete, bill, receive payment and deliver. Preview without sending; create-only grants no send authority. Failed delivery leaves the saved document retryable; queued/handed-off is not sent. | F4, D5–D6, W2, W9; customer-work workflow |
| CW45 | Initial item and currency boundary; contract | Next plan must explicitly choose supported source and convertible item types. Services, nonstock goods and fixed additional charges in home currency align with the service-sale boundary; their existing sale support does not prove estimate cost/markup or conversion support. | L1–L4, L10–L12 |
| CW46 | Stock, assemblies and fulfillment; observed family | Staged sales-order document, inventory/costing, units, availability, commitments, pick/pack/ship, partial-versus-complete shipment/backorders and material-quantity rules. A quote may describe goods only under explicit non-posting rules; conversion must reject unsupported financial/stock effects. | L7, W3, D8 |
| CW47 | Aggregate, percentage and special item roles; observed family | Staged subtotal, discount, percentage charge, group/package, payment, return/refund and negative-fee roles with ordered calculation, tax and allocation contracts. Fixed charges do not implement percentage charges; group headings cannot double count members. | L8–L9, L13 |
| CW48 | Time, reimbursable expense, mileage and deposits; observed family + dependency | Staged source capture/rates and billed-state allocation for time/cost/mileage; separate customer deposit liability/application/refund lifecycle. Neither estimated cost nor advance cash is automatically an earned sale or ordinary invoice payment. | W1, W9–W12; blueprint 10.4, 13.2, 14 |

The next plan must select a coherent supported subset and leave the remaining
keys explicitly staged. This inventory does not prescribe a partial-rounding
algorithm, approval state machine, cost-recognition method, inventory engine,
payment provider or delivery implementation. It does not change the review status
of the existing service-sales increment.
