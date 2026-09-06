# Partial and progress work billing

## Boundary and requests

The six linked-billing commands support exact partial quantities, entered net
amounts and percentages. Source ownership, acceptance, operational completion,
current posting eligibility, composite authorization, permanent retries, closed
periods and immutable corrections retain the whole-line contract. No invoice
settlement, deposit application, stock fulfillment, change-order approval, delivery,
print editor, POS or commissions are introduced.

Conversion accepts one selection family:

- Existing omitted selection bills every remaining eligible portion. Existing
  line_ids bills the remaining portions of those selected current source lines.
- selections contains 1–200 distinct current line identities, each with exactly
  one positive quantity, net_amount or percent, or rebill_allocation_id. Quantity uses the existing maximum
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
source net. Explicit zero numeric inputs reject; the caller omits an unrequested
line. A positive quantity/percentage can round to zero net and still consumes its
selected spans when another selected line makes the destination gross positive.
The same rule covers a zero-price source line. Preview explicitly shows this
physical scope with zero net/tax. A solely zero-gross selection cannot post.

Extra charges use ordinary independent sale lines, never extra source entitlement.
A request beyond remaining scope returns E_VALUE_RANGE with remaining quantity/net
and explains the independent-line path. Billing reads and previews report zero
remaining quoted scope when fully consumed. The conversion form explains the path;
after posting, an "Add an unlinked line" action opens the existing sale editor.
The editor labels these additional lines separately from captured quoted lines.
This is an explicit second correcting write with its own preview/version check,
not a silently appended conversion charge. If no quoted work remains, open an
existing bill to add a line, or create an ordinary new invoice. Paid-receipt edits
with linked-work history require amount_received to equal the changed gross,
including after their last linked line is removed. Metadata/no-op or same-gross
edits can omit it; whenever supplied on a receipt update it must match exactly.
Ordinary receipt corrections retain their existing omission contract. Examples
show a fully billed quote plus an independently described extra charge; source
consumption remains100%. Formal change-order approval remains separate future work.

The paid-receipt amount_received equals the preview's gross including calculated
tax. Changed preview facts reject before asking for a new received total. Payment
of an existing invoice uses the separate customer-payment operation.

## Exact entitlement and allocation

Each active source root has one captured, versioned economic basis. Quantity Q in
integer microunits, full net N in integer minor units and all actual quoted economic
facts stay protected. For legacy WorkLineFacts1 the basis projection remains
exactly {basis_version:1, root_document_id, root_line_id, line:<WorkLineFacts>},
where line is its typed JSON-mode dump excluding completed_quantity_microunits
and billable. Keep every other declared WorkLineFacts member, including its
schema version, description, pricing/cost/markup, complete profile/origins and
ordered tax components. No revision/line-row identity, timestamp, work-document
header, operational schedule, document title or custom fields enter this hash.
Header agreement protections remain independently enforced by the inherited
source-edit contract. Serialize UTF-8 JSON with sorted keys, separators(',',':'),
ensure_ascii=False and no nonfinite values; SHA256 lowercase hexadecimal is
source_basis_hash. Independent validation reconstructs this projection from the
stored typed source, never from a supplied projection/hash. Same-facts revisions,
completion/billable changes and estimate-to-order lineage retain the basis. All active
allocations on the root share it. Source economic changes remain forbidden while
any allocation is active; new scope uses new line identities/roots. Once all active
allocations are released, an otherwise editable source can have a new basis.
Historic allocations continue to prove their amounts against their own source
revision and basis, never the new quote.

Current WorkLineFacts2 uses exactly
{basis_version:2, sales_tax_calculation:mode, economics:E, tax_rules:R}.
E is its validated JSON-mode model dump excluding exactly schema_version,
completed_quantity_microunits, billable, tax_minor_units, gross_minor_units and taxes.
R is the ordered list of complete captured rule objects from its taxes (empty when
none apply). Every other declared field stays, including net, quantity, unit,
description, cost, classification and nested profile/origins. Reject extra fields
before projection. Tax ordinals and document attribution wrappers live outside the
line and do not enter this hash. Root/source/revision identities remain separately
validated proof fields; they are not inserted into the basis2 payload. Apply the
same canonical UTF-8 JSON and SHA256 encoding above. Conversion preserves this
projection exactly even when destination composition redistributes derived cents.
Do not upgrade a consumed legacy lineage to obtain this behavior.

Allocation_version1 retains its original whole-root representation and null proof
columns. Allocation_version2 retains its exact legacy interval/hash proof and
basis1 interpretation. Allocation_version3 explicitly carries basis_version2 and
WorkLineFacts2; interval arithmetic is unchanged. Dispatch from the stored exact
integer discriminators, never current company defaults. Old rows/JSON/rowids and
proof bytes are not rewritten or reinterpreted; the co16 preserving exception only
widens the approved allocation discriminator constraints. Unknown competing local
guards must reject the upgrade atomically, not be discarded.

Let D=lcm(Q,max(N,1),100000000). The entitlement is the integer interval[0,D).
An entered quantity q requests q*D/Q coordinate units. A percentage p, expressed
as millionths of one percent, requests p*D/100000000 units. These are integers.
D fits an unsigned160-bit representation for the existing signed64 source bounds.
All intermediate calculations use integers; money never uses floating point.

For captured net N, an interval[a,b) owns
half_even(N*b/D)-half_even(N*a/D) minor units. Add interval results to obtain the
destination line's exact net. Full coverage telescopes to the quoted net.
For the captured line_component_half_even policy, each destination component
remains half_even(net*captured_rate/100000000). For line_combined_half_up and
invoice_combined_half_up, resolve all destination nets first, then use Row24's
exact line/document bucket rounding and immutable tax-ordinal allocation. Never
calculate tax separately per entitlement span. Zero net produces zero tax.
Quoted tax is informational:
independently rounded installment taxes may differ from the estimate's tax, and
no final installment silently absorbs a tax adjustment. Net uses the entitlement
basis; tax, gross and accounting legs retain ordinary sale arithmetic.

Select earliest free intervals in ascending coordinate order. Quantity and percent
requests consume their requested coordinate length, crossing occupied gaps without
consuming them. For a positive net request n on a free interval[a,b), let its net
capacity be half_even(N*b/D)-half_even(N*a/D). Skip zero-capacity free intervals
for net_amount requests: they are uncharged physical scope, not a prerequisite
for reaching chargeable work. If positive capacity<=n, consume that whole
interval and reduce n by capacity. Otherwise end at
(half_even(N*a/D)+n)*(D/N). This endpoint is inside that interval and gives exactly
n net minor units. Zero-net portions carry no tax but still represent physical scope.
Reject if the total requested net is unavailable.

Canonical spans are positive-width, sorted, nonoverlapping, coalesced when adjacent,
and bounded byD. At most200 spans belong to one destination line and2000 to a
conversion. If a request would exceed either bound, return E_VALUE_RANGE with
the source-line identity and recommended_net_amount equal to the sum of net
capacities of its earliest at-most200 positive-capacity free intervals. A net
request for that amount is always reachable within the line bound. For the total
conversion bound, recommend fewer selected lines. Never truncate financial work.
Repeated recommended net requests strictly decrease positive remaining net and
provide a finite completion path for every finite history. Remaining zero-capacity
intervals are explicitly uncharged physical scope; chargeable billing can finish
without allocating them, just as an uninvoiced zero-price line can remain no_charge.
No cap is imposed on a root's historical destination count. Read active intervals
incrementally and coalesce them; avoid loading an unbounded history into Python.
History still uses bounded, authority- and watermark-scoped pages.

Voiding or removing a linked line releases exactly its original spans. Rebilling
those spans uses selections[{line_id,rebill_allocation_id}] and a new permanent
key. The referenced immutable allocation must belong to that root in this company,
match its current economic basis, and have every span currently free. Otherwise
reject E_WORK_DEPENDENCY; never replace it with an earliest-free substitute or
partially reclaim it. Legacy full-root allocations can be rebilled this way when
the entire root is free. This mode reproduces the referenced spans, quantity and
net. Ordinary quantity/net/percent requests always make fresh earliest-free
allocations; they do not promise to reconstruct an earlier installment.
Identical destination composition, captured policy/rules and stable tax ordering
preserve tax. Exact rebill promises spans, quantity and net; regrouping released
portions or adding independent lines may change document-derived tax. Retained
legacy proofs still use their legacy calculation and original basis.
Neither a release nor
a later installment changes another issued invoice's amount, tax or allocation.
Two concurrent conversions cannot consume overlapping spans or incompatible bases.

## Truthful quantities and sale facts

A one-microunit source priced at100c can allocate40c without pretending that its
billed quantity is one whole microunit. Amount-derived quantities can be smaller
than six decimal places or nonterminating rationals. Preserve the original quoted
quantity and rate separately from the allocated quantity.

SalesLineProfile pricing versions remain version1 unit, version2 amount and
version3 allocated. Tax alone does not introduce another pricing version. A new
revision-owned TaxAttribution v1 carries policy, origin, buckets, tax ordinals and
cells outside those pricing facts; allocation3 separately identifies basis2. Only
linked conversion and its guarded corrections can create this basis; ordinary SalesLineInput cannot
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

The original progress storage change is preserving company co0012; retain that
historical migration. Its changes widen only the known price-basis and quantity
constraints/nullability in sales_line_profiles to support allocated facts. Widen
quantity_microunits in work_billing_allocations only for new allocated records.
Existing monetary/quantity values, snapshots, local columns, generated columns,
constraints, indexes, views and triggers remain intact or migration fails atomically.
Keep historical migration witnesses pinned to their own artifact heads.

Allocation rows gain allocation_version(default1), source_basis_hash,
denominator_hex and spans_json. Version1 rows keep all three new proof fields null
and retain their original full-root meaning, including existing whole-line retry
keys and immutable history. Version2 rows require the complete legacy proof;
Row24 version3 requires the explicit basis2 proof under the same interval bounds.
Denominator
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
source/commercial authority before any preview or replay. Current posting
eligibility applies only to a new financial effect. A matching committed retry
returns current destination history even when an item/account has since been
deactivated, the source is no longer eligible, or the original period is closed.
It creates no effects and does not revalidate old posting eligibility. Current
authorization and request identity still apply before both cache/durable replay.

All first conversions atomically write the financial aggregate, allocation proofs,
permanent conversion row, source revision/version bump preserving commercial facts and audit event
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
preview yields E_PREVIEW_STALE. Its details include facts_fingerprint and bounded
consumption_changes (at most200, one latest attributed allocation-changing event
per source root): root_document_id, root_line_id, transaction_id, audit_event_id,
updated_by, updated_via, seconds_since_update and changed_fields. The field list
names billing_consumption and the changed allocation/revision or status. Derive
these from source-linked immutable allocation history and the associated sale
correction/void events, including releases with no remaining active allocation.
Label them latest billing changes, not an exhaustive diff since an unknown preview
time. Never infer the writer from the work source's unchanged timestamp. All event
details retain company/source authorization. Dry-run reserves no key, identifier, sequence or
span and has no accounting/operational/audit effects.

## Public shapes

New conversion results carry billing_progress for every current source line,
keyed by line_id, root_document_id and root_line_id. Its previous, current,
cumulative and remaining objects expose quantity/quantity_fraction,
scope_percent/scope_percent_fraction and net_minor_units/tax_minor_units/
gross_minor_units. Previous is posted allocation state before the proposed bill;
current is this bill; cumulative includes this bill; remaining is after this bill
with forecast tax on remaining net. Independent charges are excluded. Replays and
ordinary sale writes leave this projection empty; billing reads give current state.

New conversion members are selections, percent. They are mutually exclusive with
line_ids. Omitted members select existing remaining behavior; explicitly null
selection families or selection values reject E_VALIDATION. selections is a
nonempty list of up to200 unique line_id values. Each object has line_id and
exactly one of quantity, net_amount, percent, rebill_allocation_id; extra members
reject. line_id is the current stable work-line identity from BillingLineOutput,
not its source_line_id revision row. rebill_allocation_id is the canonical ID from
revision.billing_sources[].id, including older/voided revision history.
Quantity and percentage are plain positive decimal strings with no exponent or
sign and at most six fractional places; percent is <=100. Quantity accepts a
leading decimal point (`.5` becomes `0.5`). JSON numeric values reject.
net_amount uses existing SalesMoneyInput or decimal money-string syntax and must
be positive home-currency money. Malformed/foreign/duplicate identity or shape
errors use E_VALIDATION with the indexed field path. Valid numeric requests beyond
remaining scope/net or fragmentation bounds use E_VALUE_RANGE with line_id and
available quantity/net; unavailable or incompatible rebill proofs use E_WORK_DEPENDENCY.

Rational objects are {numerator:"2",denominator:"5"}: reduced, nonnegative numerator
and positive denominator, canonical decimal integer strings; zero is0/1. Ordinary
sales outputs retain quantity/base_quantity display strings and integer microunits.
Allocated SalesLineOutput adds quantity_fraction and base_quantity_fraction and
uses those for its quantity/base_quantity strings; nullable raw microunits are
set only when exact. The fraction fields are null for ordinary lines. Allocated
unit_price displays the quoted rate (null for a quoted amount); pricing_basis is
allocated and quoted_quantity gives the full original quantity as a display string.
The browser labels the rate Quoted rate. Work BillingLineOutput retains its quoted
and completed quantity strings and adds billed_quantity_fraction,
remaining_quantity_fraction, billed_scope_percent_fraction and billed_scope_percent.
The latter is100*active_span_length/D, not net billed percent. All fractions use
the exact-display convention above. state gains partially_billed; no-charge and
uncharged remaining physical scope are labelled separately from billed money.

SalesLineProfile schema_version3 has pricing_basis=allocated and allocation_proof
with source_document_id, source_revision_id, source_line_id (revision-row ID),
root_document_id, root_line_id, source_basis_hash, quoted_quantity_microunits,
quoted_base_quantity_microunits, quoted_net_minor_units, denominator and spans.
Quoted numbers remain nonnegative signed64 integers (quantities positive).
denominator is a canonical positive decimal string; spans is a list of
{start:"0",end:"40000000"} decimal-string endpoints. These are output/internal
facts, never ordinary editable sale input. The same proof fields are exposed on
BillingSourceOutput for allocation_version2 (legacy basis1) and allocation_version3
(explicit basis_version2); version1 leaves them null. Retain
the complete immutable source snapshot separately as in the inherited contract.

Example selection additions to the existing versioned, dated conversion input:
{"selections":[{"line_id":"<current line ID>","quantity":"0.25"}]},
{"selections":[{"line_id":"<current line ID>","net_amount":"40.00"}]},
{"percent":"25"}, and
{"selections":[{"line_id":"<current line ID>","rebill_allocation_id":"<released allocation ID>"}]}.
IDs in angle brackets are placeholders for canonical IDs returned by billing reads.
For a one-microunit source with net100c, a40c allocation outputs quantity="1/2500000",
quantity_fraction={numerator:"1",denominator:"2500000"}, quantity_microunits=null,
quoted_quantity="0.000001", net_minor_units=40 and pricing_basis="allocated".

The [customer-work preferences contract](customer-work-preferences.md) defines
estimate creation and progress controls, bounded recovery while progress is disabled,
and optional final-estimate inactivation. Acceptance is retained. Numbering retains
the existing shared sequence and duplicate rejection. This allocation contract
does not establish full preference or printable-layout parity.

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
rounding differences under all three captured policies, exact compatible buckets
and cell attribution, with no tax-only spans.
Tests include legacy keys after cache expiry/upgrades, two concurrent partial writes,
retained-proof correction/no-op, source edits before/after consumption, readonly/
cross-company replay denial, required custom fields and original source bytes,
closed periods, wrong-account/tax/proof/quantity fault injection, rollback and
populated/local-extension migration. Exercise complete CLI/library/HTTP and actual
1280/390 browser journeys. Independent accounting/schema review precedes a
preserving live-demo refresh.


## Captured-policy forecast and correction amendment (Row24)

All-remaining tax forecasts calculate every remaining billable net together under
the captured source policy/rules, in current displayed order with the prospective
destination's tax ordinals. Map cells back to source line IDs. Return
forecast_basis=all_remaining_together, can_bill_together and bounded eligibility
reasons. The forecast remains mathematically complete above 200 spans per line or
2000 per conversion; it is not an executable descriptor. Do not truncate scope,
relax limits or silently split posting. Above the caps label it hypothetical,
show recommended_net_amount or fewer-line recovery, and explain that separate
installments may round differently. With progress disabled retain the exact
recommended-net exception for a fragmented root and complete-line subset recovery.
Within both limits an unchanged complete-remaining conversion reproduces every
forecast cell and aggregate. Reordering, scope or eligibility changes require a
fresh preview. Previous/cumulative tax always comes from actual posted revisions;
current company settings and forecasts never replace historical values. Inspection
retains valid current knowledge even when a future conversion is ineligible.

Otherwise permitted composition changes may redistribute derived tax cents on a
retained combined-policy linked line. Captured policy/rules, proof, net, quantity
and other quoted economics remain fixed. Recalculate the whole destination and
persist revised attribution, including allocation tax facts; never reinterpret a
policy/rate/price change as redistribution. Linked receipt corrections retain the
exact amount_received predicates above, including after removal of the last linked
line. Applied invoice corrections preserve cash and restate existing settlements
under their ordinary date/version/composite-authority guards; they are not banned
merely because the invoice is paid. Reversal uses stored amounts and provenance.
