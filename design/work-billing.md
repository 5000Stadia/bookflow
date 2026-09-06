# Linked work billing

## Implemented scope

Accepted estimates and work orders become invoices or paid sales receipts through
six shared commands. Default conversion bills all remaining eligible scope; selected
lines bill their remaining portions. Exact partial quantities, entered installment
amounts, percentages and source proofs follow the [progress-billing contract](progress-billing.md).
The workflow is described in [customer work and billing](customer-work-and-billing.md).
Source roots and immutable
revision-owned allocations retain lineage across multiple financial destinations.

Ordinary invoice and sales-receipt lines also accept exact amount pricing, retaining
a positive descriptive quantity and null unit price. Unit-price snapshots keep
their original representation. Customer payment application, deposits, delivery,
print editors, foreign sales, inventory fulfillment and formal change-order approval
remain separate future operations.

## Commands and inputs

The six commands are: `estimate invoice`, `estimate sales-receipt`, `estimate billing`,
`work-order invoice`, `work-order sales-receipt`, `work-order billing`.
Financial conversions are company-scoped, standard-role writes with ledger.post,
company audit and generic idempotency; the two billing reads require membership
and ledger.read. All source and destination access uses the same selected company
and existing authority boundary. Conversion is a ledger operation, including
ordinary open-period and current posting-account checks. Completion stays nonposting.

Composite access additionally requires the source work operation's authority:
customer-work write access for conversions and linked corrections/voids that change
consumption; customer-work read access for billing views, source snapshots and
linked history. Record both resource requirements in the shared command contract
and evaluate both through the common access checker before preview, execution or
any replay. The current blueprint4.3b implementation enforces membership/roles;
resolved capability grants/denies remain Row7 work. This increment does not claim
that granular denies already work. When that evaluator resolves capabilities, the
same composite requirements must deny access if either resource is denied; no
adapter or replay callback may bypass the common checker. Source details must not
be materialized in a sale response before their additional read check succeeds.

Conversion requires canonical source id, positive expected_version, permanent
conversion_key (1–128), financial date and optional destination number. Optional
line_ids selects 1–200 distinct current source line identities. Omission selects
all currently unbilled, billable lines. Explicitly selected nonbillable, consumed,
retired or foreign lines reject rather than silently disappearing. The destination
must contain at least one line and have a positive gross total. Zero-value source
lines can accompany positive lines and are then consumed, with no zero posting legs.

An unallocated line with zero gross has the derived state `no_charge`, not unpaid
or billed. Its ordered/completed quantities remain visible, billed quantity stays
zero, and remaining chargeable quantity/amount are zero. It need not be attached
to a financial sale to finish chargeable billing. A remaining document containing
only such lines reports "No charge remains; zero-price lines were not invoiced"
and offers no financial conversion. This state changes if an otherwise permitted
source revision gives the line a price. When a zero-price line is explicitly
included alongside a positive line, its actual allocation and billed quantity are
recorded normally. Thus a previously closed positive invoice need not be changed
merely to account for a zero-price line that was not selected.

Invoice adds optional AR account, terms and due-date overrides. Receipt requires
deposit_to and exact amount_received equal to the preview's gross total; its date
is the received date. It accepts payment_method and payment_reference. This records
a paid sale, never settlement of an already issued invoice. Receipt conversion
cannot consume work already represented by an active invoice. Existing invoices
are shown with their current balances; payment application remains the next
settlement operation.

Both conversions accept destination memo, destination custom_fields and
custom_field_kinds and optional expected_facts_fingerprint. No arbitrary customer,
item, quantity, rate, net, tax or source-line identity override is accepted in a
conversion. Revise eligible source facts or add independent actual-sale lines via
the ordinary sale editor. A proposal first becomes an estimate; there is no
proposal-to-sale shortcut in these commands.

## Eligibility, source ownership and captured facts

An estimate must be active and currently accepted. An estimate which already has
a work-order destination directs billing to that work order, regardless of its
operational status; it cannot offer a competing sale from the ancestor. Work orders
must be active and not cancelled. Billing may precede completion; preview shows
operational completion and ordered/billed quantities separately, without claiming
that billing proves fulfillment. Nonbillable lines are excluded from automatic
selection. Independent copies retain their independent roots and visible copy-of
status; alternatives remain separately agreed scope.

The stable (root_document_id, root_line_id) pair is the consumption identity. Every
active sale line linked to that root consumes its entire selected source quantity
and net/tax amount in this increment. Estimate-to-work-order conversion retains
the same roots and all existing consumption. It therefore remains valid after an
estimate has been billed, but cannot make those amounts available again. New work
order lines have independent roots. Billing reads show the current owner and links
to existing invoices/receipts before offering remaining work.

Carry the selected work revision's customer/job, addresses, PO, shipping, class,
sales rep, message, description, item/unit identities and factors, quantity, selling
rate or amount, exemption, selected tax components and their rates. Carry source
scope and the complete quoted line facts in an immutable billing snapshot, separate
from ordinary invoice memo and customer-facing content. Internal estimated costs,
markup evidence and source annotations stay internal. Ancestry reaches the exact
source revision and original note/file bytes; no attachment copy or sending occurs.

Captured commercial facts do not silently refresh from current masters. All
referenced identities must still exist in this company. Newly posting financial
accounts, customer, selling items and selected tax items/agencies require current
eligibility; unchanged descriptive references may remain historical with an
explicit warning. Current company sales-tax policy must support invoice-date
recognition; the existing payment-receipt-policy posting rejection remains.
If current enabled/disabled tax policy conflicts with captured taxable facts,
reject and identify the conflict, without silently changing the agreed quote.

The captured line's income account and each captured component's liability account
govern the postings. A changed current item/tax mapping does not substitute a new
account: retain the captured account and show a mapping-change warning. Validate
each captured posting account's current existence, active state, compatible account
type and currency. Validate current selling-item type/eligibility against the
captured supported type and the captured tax agency/item identity against current
eligibility. An incompatible captured mapping rejects rather than choosing a new
classification. AR/deposit control accounts are separately selected below.

Select the invoice AR account or receipt deposit account through the ordinary
financial resolver. Invoice terms default to the captured source terms; calculate
due/discount dates from that captured term and the new financial date, unless
explicitly overridden. New financial choices require current eligibility. Receipt
payment defaults follow ordinary current sale rules. Destination custom values
use the existing conversion carry/patch/default/required-field rules, with new
destination-owned value ids and explicit omission warnings.

## Exact amount-priced sales

SalesLineInput accepts optional net_amount. Exactly one of unit_price or net_amount
may be explicitly supplied, and neither accepts null. A supplied net_amount selects
amount pricing, retains a positive descriptive quantity, and stores a null unit
price. Tax components are each half-even(net × captured rate), exactly as for an
ordinary whole sale. Unit-price lines keep half-even(quantity × unit_price).
Cost and markup are not new ordinary sales inputs in this increment.

Quantity-only edits preserve an amount-priced line's exact net. A supplied unit_price
or explicit price_level selects rate pricing and clears the saved amount basis;
use_defaults unit_price returns to catalog pricing. A supplied net_amount clears
rate mode. Explicit net_amount with price_level or price_basis_amount rejects.
Explicit net_amount with use_defaults containing unit_price also rejects; there is
no precedence rule that silently discards either requested mode.
Item/unit changes retain explicitly entered amount with a warning, matching saved
explicit-rate behavior. Refresh preserves explicit overrides. Preview and both
independent arithmetic validation paths enforce the same precedence.

The ordinary sale line output makes unit_price nullable and exposes pricing_basis
and authoritative net; new amount facts are versioned. Old schema-version-one
unit-price snapshots remain accepted and are not rewritten. No fake zero price or
quantity-one substitution represents a quoted amount. Examples: quantity2/net10.01
becomes rate blank, net10.01, tax0.80 at8%, gross10.81; ordinary quantity2/rate5.00
remains net10.00, tax0.80, gross10.80.

## Corrections, voids and source edits

Billing allocations are immutable revision-owned rows. Their active meaning is
derived from a posted transaction and its current revision, never an independently
mutable billed counter. At most one active full-line allocation may consume a root.
Independent validation checks storage under the company writer exclusion, including
all pending lines in the same request. A storage guard also rejects duplicate active
ownership at the transaction's final current-revision boundary. Unrelated invoices
and their allocations never change as a side effect.

Ordinary invoice/sales-receipt update preserves existing linked lines by line_id.
Changing their item/unit/quantity/description/class/price/tax facts rejects with
E_WORK_DEPENDENCY and the source identity. Removing an entire linked line is an
explicit correction which releases that root; the remaining sale must still be
positive. Adding ordinary independent lines is allowed and consumes no work root.
Changing the customer or captured commercial header of a sale with retained linked
lines rejects; number, memo, financial date, eligible financial control/payment
choices and destination custom values may change under ordinary sale guards.
Whole-line allocation rows are carried to each replacement revision without
changing their source snapshot. An update that actually changes nothing creates
no revision, allocation, batch or audit event.

Void uses the existing required reason, original-date reversal and closed-period
guards. Voiding the sale releases only that sale's active roots; its allocation
history and conversion key remain inspectable. An intentional rebill needs a new
conversion key and the current source version. Replaying the old key returns the
old now-voided destination. Copies of an invoice create independent actual sales
only if a later owning command explicitly offers that behavior; no implicit copy
can retain source consumption or pretend to be a retry.

Any root with active billed consumption freezes its source economic line and
prevents removal or replacement through every work document carrying that root.
The billed source's agreed customer/commercial header also remains frozen while
any of its roots is actively consumed. Operations, memo, availability, completion
and billable eligibility remain editable subject to existing Row16 rules. Once all
consuming sales are voided or corrected to remove a root, an otherwise editable
source may revise it; old allocations still name their own exact source revision.
Existing estimate/work-order agreement freezes remain independently applicable.
New scope uses new lines/roots. Formal change-order approval remains staged and
does not require an invented human gate for these existing engineering rules.

## Transaction, replay and preview

First financial conversion atomically writes the destination sale, balanced effects,
source-line allocations, permanent conversion relation, a same-facts source revision
and source version increment, and one attributed company audit event. Financial
and work validators independently rederive the pending aggregate from typed input
and stored source facts. Caller-provided pending totals, roots or source snapshots
cannot bypass validation. Any failure rolls back every component and key reservation.

Permanent conversion keys are unique company-wide across operational and financial
conversions. Both routes enforce collisions against both durable stores under the
writer lock and corresponding storage guards. Same typed original intent returns
the existing destination's current state before stale source-version rejection;
different intent rejects E_CONVERSION_KEY_REUSED. Authorization and ordinary generic
cache input matching precede replay. Cached financial conversion receipts refresh
through the durable link, and cannot recreate missing/mismatched destinations.

Dry-run reserves nothing, including sequences, ids, roots or keys. Fingerprints
cover source revision/version, selected line roots/facts, current consumption of every source root (including unselected lines),
destination financial/custom defaults and relevant posting eligibility. Execution
replans under writer exclusion; a changed source returns E_VERSION_CONFLICT and a
changed resolved preview returns E_PREVIEW_STALE. Source changes caused by explicit
sale correction/void are visible in the consumption portion even when the source
document itself was not edited. A replay never increments any version.

## Storage, browser and verification

Company migration co0011 is independent of future application metadata. It widens the
existing sales_line_profiles unit-price nullability/check and adds a price-basis
discriminator with a backward-compatible rate default. Every other existing
column, constraint, row, index, view and trigger, including local additions, survives
unchanged or the migration fails atomically. Old amount/price/tax values and posting history are unchanged.

Immutable work_billing_conversions and work_billing_allocations enforce
source work-document/revision/line/root ownership and destination transaction/
revision/line ownership. Conversion rows own the durable key and original request
hash, both birth revisions and source version. Allocation rows capture the full
source quantity/net/tax and typed work facts from an explicit source revision.
Version1 allocations consume a full root. Version2 allocations carry exact partial-span
proofs under the [progress-billing contract](progress-billing.md).
No cross-company reference and no mutable global invoice pointer is introduced.

Billing reads show source revision/current owner, each line's estimated, billed and
remaining quantity/net/tax, billable/completed facts, and bounded linked destination
history with current statuses. Results are bounded to the source's200 lines plus
200 linked destinations per page with the existing authority/query/watermark cursor
contract. Sale detail and history expose source revision links. Browser forms use
structured line selection, destination payment/account choices, required preview,
visible existing bills and appropriate invoice/receipt actions on desktop/phone.
Money and captured scope stay inside their containing sections. Help includes
"this work is finished; make an invoice" and "they paid; make a sales receipt";
it accurately stages invoice settlement and sending.

Both seed manifests append demonstrations covering all six commands, amount sales,
selection, source-to-sale links, retry, correction, void and deliberate rebill.
The existing seed prefixes and net/reference balances remain intact. Gross debit
and credit oracles include the new original/replacement/reversal activity. Added financial
demonstrations are voided after their history witnesses so existing demonstration
balances remain unchanged. Tests separately exercise active invoices and receipts.

Verification covers: library/CLI/HTTP/browser parity; complete work→invoice and paid
receipt chains; read-only/cross-company denial including cached replay; exact amount
arithmetic; two concurrent whole-root conversions; billing before/after work-order
creation; nonbillable/zero/retired lines; current posting eligibility and stale
previews; required custom fields and original source files; correction/void/rebill
without touching other sales; injected audit/source/allocation/posting failures;
fresh/populated/local-extension migration and rollback; preserved demo histories,
balances and original attachment bytes. Accounting/schema review and preservation checks cover the live-demo refresh.
Progress billing remains the next increment.

The MCP adapter remains the explicit unimplemented Row9 target. Current verification
covers registry/documentation discovery and core attribution with interface=mcp,
including rejection and durable replay, without calling that an MCP transport test.
Row9's acceptance must exercise actual MCP discovery, execution, errors and replay
for this registered billing contract before claiming cross-adapter completion.
