# Row 24 — captured sales-tax calculation policies

Status: proposed plan; no runtime implementation. Parent owns this plan. Customer
payments owns the in-flight co14/sales changes. The pure arithmetic piece can be
built in isolation after plan PASS; schema and integration start from the reviewed
payment base. Full closure includes the payment and actual MCP witnesses below.

## Policy and scope

Company setting `sales_tax_calculation` and the same optional sales/work input
select one of these versioned algorithms:

| Value | Human label | Calculation boundary |
|---|---|---|
| `line_component_half_even` | Separate taxes per line — legacy rounding | Round each line/component independently, ties to even. |
| `line_combined_half_up` | Combined tax per line — half-cent up | Combine flat rates for each taxable line, round once, allocate the result among its components. |
| `invoice_combined_half_up` | Combined tax on taxable total — half-cent up | Combine compatible taxable lines and flat rates, round once per bucket, allocate among lines/components. |

New-company default is `invoice_combined_half_up`; upgraded companies explicitly
retain `line_component_half_even`. Enabling tax or installing an update never
silently changes an existing company's policy. The setting is available while tax
is disabled. Existing enabled/default-tax-item and exemption rules stay in force.
This is a calculation policy, not a tax-jurisdiction lookup or compliance claim.
The GUI shows the selected policy in tax settings and document tax details; there
is no per-entry rounding acceptance dialog. Quantity and calculator precision,
unit-price extension and price-level rounding keep their existing contracts.

Supported consumers are every currently implemented invoice, sales receipt,
estimate, work order and conversion/progress-billing path, including ordinary,
amount-priced and allocated lines. Payment allocations consume the resulting
stored cents. Credits/refunds, discounts, inventory-specific tax bases, compound
taxes and jurisdiction-specific caps are not implemented by this row: their
existing whole-goal modules must integrate through this same policy contract when
they add their own taxable bases. This row does not claim their completion or
silently alter their roadmap status. No outside rate/provider calls are added.

## Exact arithmetic and allocation

The pure calculator receives nonnegative integer line nets in home-currency minor
units, ordered stable line ordinals, captured tax rules and a policy. Each rule
has the existing integer percentage-millionths, tax-item identity, agency and
liability account. A component's exact tax numerator is `net * rate`; the common
denominator is 100000000. Intermediates are unbounded integers. Round only at the
policy's stated boundary; never truncate intermediates to six decimal places.

For nonnegative numerator n and denominator d, half-up is `q + (2*r >= d)` for
`q,r = divmod(n,d)`. Legacy uses the existing half-even helper unchanged. Persisted
component, line, document, account-side and accumulated monetary values must fit
the existing signed64 nonnegative bounds. Range errors precede effects. Preserve
zero-rate and rounding-to-zero commercial components; emit no zero ledger legs.

A taxable bucket contains only lines with the same currency, calculation policy
and complete captured flat-rule vector: tax-item IDs/versions, rates, agencies,
liability accounts and their captured facts. Normalize comparison by tax-item ID,
not display order. Taxable bases must have the same applicability; never merge
different rule vectors because their combined percentages happen to match.
Exempt/customer-exempt/disabled-tax lines have no taxable cells and contribute
neither base nor tax to a bucket. The current one-header rule-vector contract
usually produces one bucket; this definition also governs captured work facts.
Reject duplicate rule IDs and ambiguous/malformed bucket inputs.

For line-combined mode each taxable line is its own bucket. For invoice-combined
mode use the compatible lines together. Sum every exact line/component numerator
in the bucket and half-up round that sum once to obtain T. For every cell compute
its floor and fractional remainder against the common denominator. Start with
the floor cents, then assign `T - sum(floors)` extra cents by descending remainder;
break ties by ascending stable line ordinal, then binary tax-item ID. No cell gets
more than one extra cent. Zero exact tax never receives a cent. This is an internal
ledger attribution rule; it does not independently round each tax agency's total
or assert a jurisdiction's remittance calculation. Every bucket, line, liability
account and document reconciles to the one rounded amount actually charged.

Stable ordinals are document-local and never depend on display order, tax-group
member order, generated revision IDs or preview-generated random IDs. Existing
invoice keys reuse Row22's durable settlement line ordinals; extend the same
contract to sales receipts where needed. Work documents use an immutable mapping
owned by work document/line identity, with foreign keys to work_line_identities.
Existing identity populations are assigned in binary line-ID order; include
retired identities. New document lines receive 1..N in submitted order; appended
lines receive values above the owner's historical maximum, in submitted order.
Retirement never recycles an ordinal. Preview computes prospective ordinals without
inserting rows; commit uses those same values under the owner version/fingerprint.
Cloning/conversion makes a new document with its own submitted-order ordinals.
The captured revision carries its line ordinal so verification never needs a
current display order or another revision's ephemeral ID.

## Captured facts, defaults and history

The company field follows ordinary company-info expected-version, permissions,
audit, preview and stale-default rules. Sales/work inputs accept the same enum and
`use_defaults` entry. Explicit input records explicit origin; omission on creation
captures the company default. Omission on correction retains the recorded policy,
even when adding lines or changing quantity/date/customer. Explicit `use_defaults`
for this field reselects the company default; `refresh_defaults` refreshes it only
when its origin is default. Explicit policy never refreshes incidentally. Null is
invalid; this required effective value is not clearable. Inputs with explicit
policy and its use-defaults request conflict under existing field semantics.

Version-one commercial snapshots remain byte/wire compatible and mean the legacy
policy. Do not add default keys when serializing old profiles, work facts, outputs,
audit snapshots or idempotency receipts. Introduce a discriminated new snapshot
version that requires captured policy and stable ordinals; reject inconsistent
version/field combinations. Existing revised documents may create new-version
facts while retaining their previous legacy policy and origins. Metadata-only
edits and exact retries must not create a commercial revision merely to upgrade a
snapshot's representation. Compare normalized economic semantics for no-op checks
while retaining the original representation when unchanged.

Historical validation dispatches by captured snapshot version/policy, never the
current company default. Legacy per-component validations remain exact. Combined
policies require document/bucket-level recomputation: a WorkTaxComponent validator
cannot independently reject a valid allocated cell because it differs from its
separately rounded rate. Retain local type/reference/bounds checks and verify the
complete sibling set and bucket sum in the shared effect validator. No new pathway
may bypass tax/base/ownership validation by calling a low-level command directly.

Preview, save, refresh, show, query totals, history, print and generated schemas
must agree on policy, tax bases, cell allocations and totals. Fingerprints cover
policy, origin, captured rules, ordinal assignment and complete tax result. A
company policy edit invalidates a pending preview that selected the current
default; a pending ordinary correction retaining its own captured facts is not
recalculated from the new default. Inspect existing fingerprint semantics rather
than adding a second adapter-side calculation.

Policy changes on posted documents are ordinary attributed correcting edits with
expected version, preview protection and original-date/new-date period checks.
They append exact reversal/replacement postings and preserve original documents.
Voids/deletions and unapplication reverse recorded cents, not newly computed tax.
Future refund/credit writers must distinguish reversing original recorded tax
from creating a new taxable supply; they cannot substitute a current-policy
negative multiplication. Signed tax inputs remain unsupported in these current
positive sale forms. The existing per-user deletion roadmap is unchanged.

## Work conversion, progress and settlement

Work policy is part of the captured agreement and source basis. Accept/clone/
convert and bill carry that policy. Current live company defaults cannot reprice
an accepted source. Source-selection compatibility includes the policy; reject
mixing incompatible captured headers under the existing dependency error rather
than silently picking one policy. Source agreement edits remain blocked while
active bills consume its roots. Existing release/edit/rebill workflows remain.

Progress entitlement and exact allocated net math are unchanged. After all
destination lines are resolved, calculate tax over the destination document's
compatible buckets. Never calculate separately per entitlement span. Each new
installment rounds independently; its total tax need not add up to the estimate's
informational tax. No final installment silently absorbs tax residue.

For combined-invoice policy, inserting/removing an ordinary line can redistribute
tax cents on a retained linked line. Permit only this derived redistribution:
the captured policy, rules, source proof, net and quoted economics stay fixed.
Separate those protected source facts from document-level derived cell tax in
`billing_edits.protect_sale`; do not permit a policy/rate/price change to masquerade
as redistribution. Persist corresponding revised billing tax-attribution facts,
and use actual stored installment tax for previous/current/cumulative progress.
This is an explicit amendment to the existing whole-line equality check, not a
license to rewrite the source quote.

Remaining-tax forecasts calculate all remaining billable nets together under the
source policy and compatible rules, then allocate their rounded forecast to source
line ordinals. Label it a forecast for billing that remaining scope together;
future installment partitioning or extra sale lines can differ. Previous and
cumulative values remain actual postings and never use a current default or a
fresh forecast. Forecasts and previews reserve no entitlement or stored amounts.

On an applied invoice correction, feed the revised exact net/tax component cents
into Row22's complete-graph settlement restatement. Use its logical keys, version
guards, permanent original-intent replay, effective dates, period checks and
composite authority unchanged. Reallocation must neither recognize extra revenue
nor change the received cash. A rejected edit leaves invoice, applications,
allocations, keys, audit and ledger unchanged. Recovery of an old operation returns
its original result under current authority even after the company changes policy.

## Storage and implementation order

1. Build a pure `company/tax_calculations.py` engine and typed policy representation
   with independent arithmetic witnesses. It has no database/adapters dependencies.
   Preserve the existing legacy tax function for old facts. This is an intermediate
   gate only; it does not satisfy Row24's done.
2. From the reviewed Row22 base, implement the next unused company migration
   (expected co15, verify numbering at that point): add the company policy with
   legacy migration default and any immutable ordinal mappings absent from Row22.
   No hub migration. Use preserving additive DDL, no table rebuild or rewriting
   historical JSON, audit, posting rows, rowids or local extension DDL. Fresh rollout
   explicitly sets its new-company default. Migration and company-info audit expose
   the retained policy. Unknown unsupported local shapes fail atomically.
3. Resolve all line nets/rules before the shared document tax pass; then serialize,
   fingerprint, validate and post through existing sales/work/billing services.
   Split historical snapshot types instead of weakening legacy checks. Extend the
   payment integration only through existing settlement hooks; coordinate ownership
   with that frozen base, not edits in its moving checkout.
4. Add thin company/document tax controls and readable policy/breakdown to existing
   forms, detail, preview and print. Generate references from registry/schema and
   extend deterministic demo manifests without rewriting old seed facts. Include
   active witnesses that distinguish line and aggregate cents, not only cancelled
   examples. Record the exact new ledger delta in seed tests.

Main integration stays parent-owned. No demo hosting, protected company access,
provider connections, push, or new communications authority is granted. Estimate:
first independently reviewable arithmetic gate within 45 minutes of code start;
record the remaining integration estimate after Row22 base is available. Report
material overrun and concrete progress; do not drop a gate to meet an estimate.

## Required evidence

Independent hand-fixed examples, with ordinary posted USD values:

| Input | Result |
|---|---|
| 150 cents at 7.25% | Exact $0.10875, rounded 11 cents in all three policies. |
| 100 cents at 2.5% | Legacy 2 cents; either half-up policy 3 cents. |
| 10-cent line, two 5% rules A/Z | Legacy 0+0; combined tax 1 cent, attributed A1/Z0. Separate half-up would wrongly charge 2 cents and is not either combined mode. |
| Two 5-cent lines at 10% | Legacy 0; line-combined 2 cents; invoice-combined 1 cent assigned to ordinal1. |
| Two 10-cent lines, two 5% rules A/Z | Invoice-combined 2 cents: ordinal1/A1, ordinal1/Z1, ordinal2/A0, ordinal2/Z0 under the stated global cell rule. |
| Reorder those lines or A/Z display order | Captured ordinals/IDs govern ties; assigned cents do not move. |

Cover just below/at/above half-cent; differing rates/agencies/accounts with equal
total percentage; exemption and disabled tax; zero net/rates; maximum rule/line
counts and signed64 result overflow without intermediate overflow; duplicate cells;
new/appended/retired/reordered lines and preview-versus-save ordinal stability.
Check exact bucket/component/line/account sums with independent expected values,
not only comparing the implementation with itself.

Preserving migration witnesses include an old company with posted, voided,
corrected, partially billed, amount-priced and paid documents; old preview/retry
receipts; inactive referenced records; and unknown supported local DDL. Preserve
historical raw bytes and results, company roots, rowids and exact old seed prefix.
No-op, show, history, print, old retry and reversal survive a policy default change.
New rollout uses the new default while migrated rollout remains legacy.

Exercise estimate→work order→multiple progress invoices/receipts, partial spans,
ordinary extra lines and changed line composition redistributing a linked tax
cent, source protection, release/rebill, remaining forecast and actual cumulative
tax. Apply payments, change invoice tax policy or composition, verify original
cash plus all active allocations and cents reconcile, then test unapply/void and
permanent replay. Include closed old/new/application dates, stale versions and
missing composite authority with complete rollback evidence.

Real desktop1280/phone390 company selection→preview→post→history/print and
agent↔human continuation must agree with CLI/HTTP/Python and actual MCP. A blind
agent gets ordinary tax/business wording, discovers the applicable setting and
preview, then reports the result and uncertainty; interview and fix/retest under
D109. No claim of legal compliance follows from that usability test.

Use an independent plan critic before code and an independent artifact critic on
the exact final candidate. Focused legacy sales/work/progress/payment, migration,
numeric, generated-reference and cross-interface checks are required; broaden only
for unresolved risk or changed shared code. Update owning stable contracts in the
implemented change, remove this plan/row only after every gate passes.

## Research basis and limits

[Minnesota Revenue Notice 05-08](https://www.revenue.state.mn.us/revenue-notice/05-08-sales-and-use-tax-rounding-item-or-invoice)
permits item or invoice calculation and specifies combined state/local rounding.
[Minnesota 297A.76](https://www.revisor.mn.gov/statutes/cite/297A.76) defines the
half-cent threshold. [New York TB-ST-860](https://www.tax.ny.gov/pubs_and_bulls/tg_bulletins/st/taxable_receipt.htm)
uses the combined rate on the taxable invoice total and distinguishes discounts
when determining that base. Sources were opened September6,2026; notice dates
remain 2005/2014 and the statute page is labelled2025. They motivate capabilities,
not jurisdiction selection. New-company default, internal cell allocation and
historical-preservation rules above are Bookflow design choices under the approved
goal. No federal rule is presented as universal state sales-tax authority.
