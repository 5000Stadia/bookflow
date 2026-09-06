# 18 — Partial and progress work billing

## Boundary and requests

Extend the six linked-billing commands with exact partial quantities, entered net
amounts and percentages. Source ownership, acceptance, operational completion,
current posting eligibility, composite authorization, permanent retries, closed
periods and immutable corrections retain the whole-line contract. No invoice
settlement, deposit application, stock fulfillment, change-order approval, delivery,
print editor, POS or commissions are introduced.

Conversion accepts one selection family:

- Existing omitted selection bills every remaining eligible portion. Existing
  line_ids bills the remaining portions of those selected current source lines.
- selections contains 1–200 distinct current line identities, each with exactly
  one positive quantity, net_amount or percent. Quantity uses the existing maximum
  six decimal places. Net amount is exact home-currency money, excluding tax.
  Percent permits at most six decimal places and is at most100.
- percent applies the same positive percentage to every eligible source line.

Both percent forms refer to ORIGINAL source scope. They do not mean a percentage
of the remaining balance. A request exceeding any selected line's remaining scope
or remaining net rejects with its source-line identity and available values; no
silent clipping or automatic overrun. Explicitly selected nonbillable, retired,
foreign or fully consumed lines reject. Existing automatic nonbillable exclusions
remain visible. A zero-price line can accompany positive billing; the destination
still requires positive gross. A positive entered net amount requires positive
source net. Zero-amount requests omit the line instead of consuming hidden scope.

Extra charges use ordinary independent sale lines, never extra source entitlement.
A request beyond remaining scope returns E_VALUE_RANGE with remaining quantity/net
and explains the independent-line path. Billing reads and previews report zero
remaining quoted scope when fully consumed. The conversion form explains the path;
after posting, an "Add an unlinked line" action opens the existing sale editor.
The editor labels these additional lines separately from captured quoted lines.
This is an explicit second correcting write with its own preview/version check,
not a silently appended conversion charge. If no quoted work remains, open an
existing bill to add a line, or create an ordinary new invoice. Paid-receipt edits
still require the actual received amount to equal the changed gross. Examples
show a fully billed quote plus an independently described extra charge; source
consumption remains100%. Formal change-order approval remains separate future work.

The paid-receipt amount_received equals the preview's gross including calculated
tax. Changed preview facts reject before asking for a new received total. Payment
of an existing invoice remains a distinct future operation.

## Exact entitlement and allocation

Each active source root has one captured economic basis: its quantity Q in integer
microunits, full net N in integer minor units, each original tax-component total,
item/unit/classification and remaining captured economic facts. Ignore only work
completion and billable flags when identifying that line basis. All active
allocations on the root share it. Source economic changes remain forbidden while
any allocation is active; new scope uses new line identities/roots. Once all active
allocations are released, an otherwise editable source can have a new basis.
Historic allocations continue to prove their amounts against their own source
revision and basis, never the new quote.

Let D=lcm(Q,max(N,1),100000000). The entitlement is the integer interval[0,D).
An entered quantity q requests q*D/Q coordinate units. A percentage p, expressed
as millionths of one percent, requests p*D/100000000 units. These are integers.
D fits an unsigned160-bit representation for the existing signed64 source bounds.
All intermediate calculations use integers; money never uses floating point.

For captured net N, an interval[a,b) owns
half_even(N*b/D)-half_even(N*a/D) minor units. Add interval results to obtain the
destination line's exact net. Full coverage telescopes to the quoted net.
Each destination line's tax component is half_even(net*captured_rate/100000000),
the ordinary sale rule. Calculate once per component on the combined line net,
never separately per span. Zero net produces zero tax. Quoted tax is informational:
independently rounded installment taxes may differ from the estimate's tax, and
no final installment silently absorbs a tax adjustment. Net uses the entitlement
basis; tax, gross and accounting legs retain ordinary sale arithmetic.

Select earliest free intervals in ascending coordinate order. Quantity and percent
requests consume their requested coordinate length, crossing occupied gaps without
consuming them. For a positive net request n on a free interval[a,b), let its net
capacity be half_even(N*b/D)-half_even(N*a/D). If capacity<=n, consume that whole
interval and reduce n by capacity. Otherwise end at
(half_even(N*a/D)+n)*(D/N). This endpoint is inside that interval and gives exactly
n net minor units. Zero-net portions carry no tax but still represent physical scope.
Reject if the total requested net is unavailable.

Canonical spans are positive-width, sorted, nonoverlapping, coalesced when adjacent,
and bounded byD. At most200 spans belong to one destination line and2000 to a
conversion. If a request would exceed either bound, return E_VALUE_RANGE with a
request to bill a smaller amount or fewer lines; never truncate financial work.
No cap is imposed on a root's historical destination count. Read active intervals
incrementally and coalesce them; avoid loading an unbounded history into Python.
History still uses bounded, authority- and watermark-scoped pages.

Voiding or removing a linked line releases exactly its original spans. Rebilling
those spans with a new permanent key preserves their net; identical grouping and
captured rates preserve tax. Regrouping released portions can change rounded tax.
Neither a release nor
a later installment changes another issued invoice's amount, tax or allocation.
Two concurrent conversions cannot consume overlapping spans or incompatible bases.

## Truthful quantities and sale facts

A one-microunit source priced at100c can allocate40c without pretending that its
billed quantity is one whole microunit. Amount-derived quantities can be smaller
than six decimal places or nonterminating rationals. Preserve the original quoted
quantity and rate separately from the allocated quantity.

Introduce version3 allocated SalesLineProfile facts. Only linked conversion and
its guarded corrections can create this basis; ordinary SalesLineInput cannot
supply a proof, source identity, arbitrary tax or allocated pricing mode. Existing
version1 unit-price and version2 amount-price snapshots retain their serialization
and ordinary arithmetic. A full original interval may retain its existing whole-
line representation; partial or fragmented remaining coverage uses allocated facts.

Allocated facts carry the source revision/root identity, source economic basis,
D and canonical spans. The immutable source snapshot retains the complete quoted
quantity, rate/amount, component taxes and classifications. The invoice's net and
taxes use the net allocation and captured tax rates; its displayed quoted rate is not asserted to be
an independently rounded multiplication producing that net.

Quantity equals Q*sum(span lengths)/(D*1000000) in the quoted selling unit.
The base quantity allocates the captured full base quantity by the same fraction.
Expose reduced positive numerator/denominator pairs as canonical integer strings.
Render an exact decimal when representable within six places, otherwise an exact
fraction. Public quantity display strings state this convention. Raw integer
quantity_microunits/base_quantity_microunits are present only when exactly
representable, and otherwise null for allocated facts. No rounded substitutes or
fake zero/one quantities enter accounting facts. Ordinary quantity fields remain
positive integer microunits and their ordinary input precision is unchanged.

In an ordinary sale correction, a retained allocated line is identified by its
line_id and matching item; no other economic/default-reset input is accepted for
that line. Reconstruct its saved proof/facts through the core. The browser renders
those economic fields read-only and submits only the retained identity/item.
Removing the whole line is allowed; independent ordinary actual-sale lines can
be added. Reordering retained lines is allowed. Header financial/date/memo/custom
changes retain the existing guarded contract. A no-op creates no new history.

## Storage and independent validation

Add preserving company co0012. Widen only the known price-basis and quantity
constraints/nullability in sales_line_profiles to support allocated facts. Widen
quantity_microunits in work_billing_allocations only for new allocated records.
Existing monetary/quantity values, snapshots, local columns, generated columns,
constraints, indexes, views and triggers remain intact or migration fails atomically.
Keep historical migration witnesses pinned to their own artifact heads.

Allocation rows gain allocation_version(default1), source_basis_hash,
denominator_hex and spans_json. Version1 rows keep all three new proof fields null
and retain their original full-root meaning, including existing whole-line retry
keys and immutable history. Version2 rows require the complete proof. Denominator
and endpoints use fixed-width40-character lowercase hexadecimal encoding of the
unsigned integer; lexical binary order is integer order. Public proof output uses
canonical decimal strings, not database encoding. spans_json is a bounded array
of start/end pairs. Stored allocated quantity is null only when its exact quantity
cannot be represented in microunits; its source snapshot still has the full Q.

Replace the whole-root exclusivity guards with rejection-only guards for valid
proof shape, within-row disjoint intervals, compatible active source basis and
cross-transaction active overlap. A legacy allocation occupies the full root.
Check both allocation insertion and activation of a transaction's current revision.
A correcting transaction can replace its own old allocations; other active sales
remain protected. Immutable update/delete/REPLACE protections remain. Database
guards never calculate or post accounting effects.

The independent validator reads stored source facts and active ownership, derives
requested selection and exact net/component tax/quantity fractions, and compares
all pending sales, allocation proof and source/audit rows. It must not accept the
resolver's own totals or classifications as independent evidence. Corrections
compare retained proof with the exact prior immutable allocation. Check current
posting eligibility and source/commercial authority before any preview or replay.

All first conversions atomically write the financial aggregate, allocation proofs,
permanent conversion row, same-facts source revision/version bump and audit event
under the existing company writer exclusion. Injected failures roll back all of
these. Financial and operational permanent keys still share a company namespace.
When adding optional selection inputs, omit absent new fields from the legacy
request-hash serialization: existing Row17 keys must continue to match their
original typed request after upgrade and generic-cache expiry. New partial intent
is hashed distinctly. Replay returns the destination's current state, including
voided state, before stale source-version rejection and without reserving spans.

Fingerprints include every current source root's consumption, not only selected
lines, plus selected spans/basis, captured facts, current financial eligibility and
custom/payment choices. Source edits yield E_VERSION_CONFLICT; changed resolved
preview yields E_PREVIEW_STALE. Dry-run reserves no key, identifier, sequence or
span and has no accounting/operational/audit effects.

## Browser, documentation and completion evidence

Billing forms offer remaining work, selected remaining lines, one original-scope
percentage, or per-line quantity/net/percentage. Show estimated, previously billed,
current, cumulative and remaining quantities/net and the source rate. Show quoted
tax, actual previously billed tax, current invoice tax and tax estimated for billing
the remaining net together. Never label quoted tax minus billed tax as a tax debt
or remaining entitlement. Cumulative gross uses actual posted installment taxes. Label
percentages by their original-scope basis. Readable quantity fractions have quoted
quantity context. Column visibility for quantity/rate and percentage is preserved
in the page URL. Financial completion does not claim physical fulfillment.

Keep existing bills with statuses and current amounts due visible. Distinguish
unallocated physical scope from remaining chargeable scope: zero remaining money
can coexist with a tiny unallocated quantity. A partly allocated line may report
partially_billed even when no charge remains; available actions depend on remaining
gross and eligibility. Print suppression and printable progress layouts remain
in the later print-template contract; no print-editor implementation is added here.

Every adapter uses the same typed input, exact output and errors. Generated docs
include quantity, net-amount, whole/per-line percentage, remaining work, correction,
void/rebill and original-key replay examples. Actual MCP transport is still Row9;
core attribution checks are not transport acceptance.

Both seeds append independent progress examples, preserving every old command
prefix, source record/history/file and net/reference balance. New financial examples
end voided. Independent arithmetic witnesses cover small/tied net and component
amounts, one-microunit sources, nonterminating quantities, mixed selection modes,
fragmented gaps, late void/rebill in different orders, and full net telescoping sums.
Tax witnesses cover half-cent ties, different installment groupings and quote/actual
rounding differences, with ordinary per-line/component tax and no tax-only spans.
Tests include legacy keys after cache expiry/upgrades, two concurrent partial writes,
retained-proof correction/no-op, source edits before/after consumption, readonly/
cross-company replay denial, required custom fields and original source bytes,
closed periods, wrong-account/tax/proof/quantity fault injection, rollback and
populated/local-extension migration. Exercise complete CLI/library/HTTP and actual
1280/390 browser journeys. Independent accounting/schema review precedes a
preserving live-demo refresh.
