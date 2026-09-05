# Service invoices and sales receipts

## Target

Row 15 adds home-currency service billing through the shared command registry:
invoice and sales-receipt post, show, query, history, update and void. Each sale
has a real commercial profile and ordered item lines, immutable revision facts,
independently balanced effects and exact source attribution. Desktop and phone
users can enter, preview, post and inspect the sale without entering debits and
credits. This is an increment toward the complete customer-work-and-billing
workflow, not the completion of every sales form or the final visual design.

## Supported sales and explicit limits

The first sales increment accepts service, non_inventory_part and fixed-amount
other_charge items enabled for sales. Each line sells a positive quantity at a
nonnegative home-currency unit price. Zero-priced lines are permitted; the whole
sale must be positive. Subtotals, discounts, groups, percentage charges, payment
items and stocked/assembly sales remain subsequent increments and are rejected
with a field-specific error, never treated as ordinary revenue. Stock sales must
wait for inventory movements and costing. Foreign sales are rejected explicitly;
existing foreign journals keep their current contract.

Sales tax supports configured sales_tax_item and sales_tax_group masters, with
separate captured component rates and agency/liability references. This increment
supports the company's invoice_date liability policy. A taxable sale under
payment_receipt policy is rejected pending the settlement/tax-policy increment.
Nontaxable service sales remain possible. Neither delivery nor receipt posting
claims that any external payment processor or email provider was contacted.

The comparison inventory is design/inventories/service-sales.md. Its staged
controls remain explicit future work rather than claims of complete form parity.

## Commands and form fields

All commands are company-scoped. Reads require ledger.read and membership; writes
require ledger.post and standard role, with the existing readonly ceiling and
idempotency keys. Writes use the ledger guard and a single company transaction.
Both nouns have independent numbering sequences starting at 1, empty prefix;
explicit trimmed case-sensitive numbers reserve only that type's number. Failed
writes and replay consume no number. Query filters date range, customer, number,
status; limit 1–200, default 50, signed continuation invalidated by company writes.
History uses the same limit bounds, signed cursor binding and company-audit
invalidation as query, including type/document/principal/permissions; an audited
write invalidates the current-header-plus-history page rather than mixing states.
History pages immutable revisions; show accepts number or id plus optional
revision_number. A selector must match the requested type even when it is an id.

Post fields: date, optional number, customer (customer or job selector), lines
(1–200), memo, customer_message, customer_purchase_order, terms, due_date,
billing_address, shipping_address, ship_date, ship_method, sales_rep, class_id,
customer_tax_code, sales_tax_item, price_level, custom_fields and
custom_field_kinds. Address overrides use the existing typed address shape.
Invoice adds optional ar_account: require an explicit active AR account unless
exactly one active AR exists. Sales receipt instead requires deposit_to (active
bank or system Undeposited Funds), requires a resolved payment_method (explicit or customer default), and accepts payment_reference,
and rejects invoice-only terms/due_date/AR inputs. No automatic cash assumption.

Line inputs: optional current line_id for an update, item selector, quantity
(decimal string, default 1, at most six fractional digits), optional unit,
unit_price (exact nonnegative integer money shape or decimal string), description, class_id,
tax_code. No floats, negative quantities/prices, arbitrary account overrides or
caller-supplied posting legs. The command resolves accounts from the item.
Income mapping must be an active income or other_income account. Tax liability
mapping must be an active other_current_liability account with the sales_tax_payable
system role, agency an active
flagged tax-agency vendor. Missing mappings are field errors.

Update takes the noun's document selector, expected_version and optional form
fields; omitted fields retain saved values, explicit null clears only clearable
optional fields. A submitted line collection replaces the collection, preserving
only current owned line identities supplied once; removed identities cannot be
reused. New lines receive new identities. refresh_defaults explicitly refreshes
master-derived facts and is visible in preview. Document equality includes captured field origins and rules as well as displayed
values. An origin-only change is a real revision with ordinary exact reversal and
replacement batches and audit; a fully equal document creates no audit, revision
or batch. Void requires context reason, keeps the
original total/history, appends one exact reversal and is a no-op when repeated.

## Defaults, prices, units and captured facts

Resolve customer/job defaults through the existing customer projection, including
ancestor inheritance and owned/inherited contacts and ship-to collections. Capture
only the public business facts needed on the document: customer/job id and label,
company/person display names, effective billing address, selected/default ship-to,
email/phone, resale number, terms, sales representative and shipping method labels.
Do not copy protected payment metadata, private notes or an entire customer row.
Absent shipping override selects the effective active default shipping address,
or no shipping address when there is none. Explicit overrides never edit masters.
Capture issuer name/address using the existing journal issuer rule.

Header defaults: customer/job then company for sales_tax_item; effective customer
tax code, terms, price level, class, representative, shipping and payment method.
Line class: explicit, then item default, then header class. Line tax code:
explicit, then item default, then customer code; absent code is nontaxable. An
explicit or default nontaxable customer code exempts all lines. Company sales-tax
disabled means no tax and rejects explicitly requested taxable treatment. A
taxable line requires a valid tax item/group. Non-taxable lines have zero tax.

Explicit unit_price wins. Otherwise resolve the effective price-level rule first:
a fixed per-item price needs no item price, a cost-based percentage needs cost,
and current_custom_price needs price_basis_amount. Standard-price percentages
and fallback with no matching rule require item price. Missing the selected
rule's actual base requires an explicit price or usable rule; do not reject an
unused missing standard price. Apply the selected price level when enabled. When price levels are disabled, stored customer assignments are retained but do
not apply to new pricing; preview says standard/manual price is in use. Explicit
non-null level selection while disabled is a field error. Explicit unit_price
still wins and can be used regardless of retained assignments. Fixed-percent levels adjust item
price; per-item rows use fixed price or percent on standard_price/cost. A
current_custom_price basis uses a separate explicit price_basis_amount input in
home-currency minor units per base unit. It is never the last calculated price.
Retain this captured base on edits; absent base is a field error on new selection.
Reapplying or refreshing the same rule uses that base once, never compounds it. Missing per-item row leaves the
standard price. Apply level rounding to exact rational minor units: subtract
offset, round to increment in nearest-half-even/up/down mode, add offset; reject
negative or out-of-range results. Capture the resolved source and rounding facts.

Item prices are per base unit. With a unit set, use its active default sales unit
(or base unit if unset); an explicit unit must be an active member of the item's
active set. Capture unit label and base_factor_nanounits. Without a set the unit
is null and factor 1. An explicit unit price is per selected unit; derived price
is base price times unit factor rounded half-even to a minor unit. Quantity and base_quantity store signed64 micro-units. Base quantity is quantity
times factor rounded half-even once to six decimals; reject overflow or positive
quantity collapsing to zero. Output uses canonical decimal strings; never float. Line net is quantity times selected
unit price, rounded half-even once to currency minor units. Each tax component
rounds line net times its percentage half-even to minor units independently.
Tax groups expand their active member tax items once each in stored order; no
compounding. Gross = net + component taxes; total = sum line gross. All monetary
results and accumulated batch sides fit signed64. Rate zero and rounding-to-zero
components are retained commercially and produce no zero accounting legs.

Standard terms: due = sale date + due_days. Date-driven terms: clamp desired day
to month's end, advance month if before sale date or within the configured
threshold (inclusive), clamp again. Explicit due_date overrides calculation and
cannot precede invoice date. Capture discount percentage and computed discount
date from the same terms (standard days, or clamped day in the due month's
calendar); the discount is offered metadata, not a silently posted deduction.
Reject dates outside supported ISO years. With no terms, due date is invoice date.

Existing unchanged fields/lines retain captured labels, unit factors, mappings,
tax rules and prices even if masters change or become inactive. Every replacement
business effect still requires its mapped posting accounts active; an inactive
account must be reactivated or explicitly remapped by selecting/refreshing a valid
item before an edit can post. Exact reversals alone may use inactive accounts. Changed quantity
or explicit price recalculates using those saved rules. Newly selected references
must be active. refresh_defaults reloads default-origin facts for retained selectors and
requires those references active. Reversals always use exact historical facts.

## Storage and financial effects

### Edit dependency and preview contract

Each resolved form fact stores its origin: `explicit` (including explicit null)
or `default`, and the relevant default source id. An edit omitting a field keeps
both value and origin. `use_defaults` is a bounded list of header field names;
each line has the same list for its fields. Naming a field there clears its
explicit override and resolves its default; supplying it explicitly in the same
input is a field error. This mechanism is distinct from explicit null. Header
refresh and per-line refresh reload only default-origin facts and current labels
of selected references; they never overwrite explicit text, money or addresses.
The preview identifies refreshed and dependent changed fields.

| Controlling change | Dependent resolution |
|---|---|
| Customer/job | Capture new party identity; recompute default-origin billing/shipping, terms, tax code/item, price level, representative, shipping/payment method and class; keep explicit overrides. Re-evaluate line taxability under new customer exemption and default-origin line class/pricing. |
| Date or terms | Recompute default-origin due date and term discount dates from the captured term rule; changing terms captures its new rule. Explicit due date stays fixed but must remain on/after issue date. |
| Header/line price level | Recompute default-origin price using the selected captured/new level; explicit unit prices stay fixed. A line level overrides header; explicit null means no level for that line. |
| Item | Capture selected item profile and its mappings; recompute default-origin description/unit/price/class/tax code; explicit overrides must remain compatible with the new item. |
| Unit | Capture valid new factor/label and recalculate base quantity/default-origin selected-unit price. Explicit price remains per the selected unit and preview shows that retained price. |
| Quantity or explicit price | Recalculate net/tax/gross using captured tax selection and historical unit/price rules; no master refresh. |
| Customer tax code, line tax code or tax item | Recompute affected taxability/components using selected rules; new selectors capture current master facts, unchanged selectors use saved rules unless refresh requested. Customer exemption always wins. |
| Header class | Recompute default-origin line classes; explicit line classes remain. |

Line replacement inputs omit optional defaultable leaves to preserve the matching
current line's origins and facts. New lines resolve new defaults. The required item
selector identifies the current item; omitted quantity retains the old quantity
on a matched update line and defaults to1 only on a new line. An identical item
selector is not a master refresh. Supplying the same selector/override does not
implicitly reset sibling origins. Exempt-line snapshots retain resolved component rules when available; if no
applicable tax item/group exists (or an unused inactive default cannot supply
rules), the captured rules are null, not invented historical facts. Removing
exemption then requires an explicit valid sales_tax_item or an explicit
use_defaults request for that field; otherwise return a field error. Do not
silently choose today's tax default during an unrelated edit.
Tax-rule refresh never changes explicit exemption. New tax groups capture their
ordered member rules; old group changes do not rewrite existing components.

Header `use_defaults` accepts only billing_address, shipping_address, terms,
due_date, ship_method, sales_rep, class_id, customer_tax_code, sales_tax_item,
price_level and payment_method; invoice/receipt applicability still holds. Line
`use_defaults` accepts only description, unit, unit_price, class_id, tax_code and
price_level. Reject unknown/duplicate names. Header refresh_defaults=true applies
to header and every retained line; a line refresh_defaults=true refreshes that
line as well, never excludes it from a header refresh. False is not an exclusion.
Simultaneously supplied explicit values take precedence over refreshed defaults;
use_defaults plus an explicit value for the same field is a contradiction. All
changed default rules propagate through the dependency table to default-origin
dependent values; explicit prices/text/addresses/exemptions remain untouched.
Refreshing a selected reference keeps its explicit identity but reloads its
derived label/rule; those derived facts are default-origin, not a manually entered
value. New lines always resolve from the document's currently selected header
facts and their own inputs. Custom-field refresh keeps the existing journal
semantics: refresh displayed definition/choice facts, not stored values.

A selected shipping_address_id is a captured owned-child reference, distinct
from a standalone manual address. Changing customer must revalidate that selected
id against the new effective collection. If it is not present, reject with a
shipping_address_id field error; choose a new id, supply a manual address, or
return shipping_address to defaults. A manual address survives customer changes.
Returning shipping_address to defaults also clears the old selected-id override.

Capture sales_tax_enabled and sales_tax_liability_basis in the document profile.
An ordinary correction uses those captured settings for retained and new lines,
including a quantity change after company tax preferences change. It does not
silently remove historical tax or reinterpret its liability date. New documents
and header refresh_defaults use current settings; preview makes a change visible.
Under refreshed disabled tax, default-origin tax treatment is removed; explicit
taxable code/item selections require explicit clearing or a field error. A
refresh to an unsupported liability policy rejects taxable sales. Unchanged
captured tax references may be inactive; actual replacement liability accounts
must remain active. The same captured-versus-current distinction does not grant
permission to use a different company's references or bypass closed dates.

Every preview returns `facts_fingerprint`, a SHA256 digest of canonical resolved
commercial content, original input origins, company id and source document
version, excluding generated ids/timestamps/audit ids. Post/update optionally
accept `expected_facts_fingerprint`; writer replanning must match it or return
`E_PREVIEW_STALE`, with no writes, so the caller can preview again. Browser Preview
retains that fingerprint for Save; editing fields clears it and visibly requires
a fresh preview before the subsequent Save. Direct post without a prior preview
is valid and documents that defaults resolve atomically at execution. This is
the same optional contract through Python/CLI/HTTP; no adapter-specific pricing.
Authenticated idempotency lookup precedes replanning, document-version and
fingerprint checks: a matching committed request returns its stored result even
after that write changed the version, or later master data changed. Reuse of a
key with different input/fingerprint remains E_IDEMPOTENCY_MISMATCH under the
existing dispatcher contract. A new key performs all current-state validations.

Update and void use optional positive `expected_version` under the existing
journal aggregate-concurrency rule: every sale field belongs to one indivisible
`sale` conflict group. No disjoint financial merge; a stale version returns
`E_VERSION_CONFLICT` with current writer/name, age and actual changed field paths. Conflict detection
uses the indivisible group; conflict explanation separately compares saved
revisions/origins and reports paths such as lines.<line_id>.quantity, customer or
billing_address, not merely the word sale. A blind
write follows existing recent-write warning behavior; the browser always supplies
the displayed version. Update of a voided sale is `E_VALIDATION`; it cannot revive
the document. An existing no-op may be inspected despite inactive references;
an actual replacement validates active posting accounts, while unchanged item,
party, class, term and tax display references may remain inactive. New reference
selection or explicit refresh requires active referenced masters. The saved item
type governs retained lines, even if its master has subsequently changed type.

### Preferences, stale defaults and term metadata

With `units_of_measure_mode=disabled`, new lines have no unit and factor1; item
set assignments remain stored but unused. Explicit unit selection is rejected.
Single-unit mode fixes entry to the set's base unit. Multiple-related mode uses
default sales unit or base and permits another active member. Historical lines
preserve their chosen unit across preference changes until explicitly changed
or refreshed. Any factor used by any posted sale revision remains immutable,
including voided/replaced history: unit update and undo both reject factor changes
with `E_ACTIVE_DEPENDENTS`. Adding new units or deactivating unused ones remains
possible. Selected-unit price rounding is an explicit commercial convention:
base-price1minor times factor0.5 rounds to0; preview warns that a nonzero default
rounded to zero and shows both facts. An explicit price resolves that case.

With classes disabled, suppress new default classes and reject explicit non-null
class entry; retain historical captured values on unchanged lines. With classes
enabled, `prompt_for_class` produces a visible warning naming lines without an
effective class, not a hard requirement. Explicit null clears a line class and
prevents fallback; returning it to defaults restores the ordinary precedence.

An inactive inherited/default reference that is actually needed returns
`E_INACTIVE_REFERENCE` identifying the field, record and inheritance source. Do
not silently fall through to a different commercial rule. The form retains all
entered content and permits an explicit replacement, or explicit null when the
field is optional. Inactive unused references (disabled feature, overridden price
level, invoice-only terms on a receipt) do not block. A new receipt requires a resolved active payment method; if no default exists
the user selects one. A correction can retain its captured inactive method,
just as it retains an inactive saved tax item/group or agency. Newly selecting
or explicitly refreshing any of those references requires them active; posting
account activity is separately required for every replacement effect. There is no
implied cash method or fabricated processor confirmation.

Reuse the same term date rules as `term show`. An expired computed discount date
remains visible as expired, with `discount_available=false` when before issue date
or after due date; no promised discount or accounting deduction is created. An
explicit due-date override changes availability, not the computed rule date.

### Remaining shared command details

`customer_message` is free text (max2000), `customer_message_item` selects an active
message master and captures its text; both together are a field error. Add
`shipping_address_id` to choose any active member of the effective customer
ship-to collection; it is mutually exclusive with typed shipping_address override.
Memo/message/description max2000, purchase/payment reference max128, number max64.
The document selector field is `invoice` or `sales_receipt` for its respective
noun; `class_id` is the public class selector field, avoiding a Python keyword.
Other master selectors are nonempty strings up to1004, resolved by existing list
rules. Lines also accept price_level and price_basis_amount, with header fallback.
`custom_field_kinds` retains the existing definition-id-to-expected-kind contract.

Query returns typed summaries ordered ascending accounting date then stable id,
with exact customer id filter (no implicit descendants), optional inclusive date
range, status and number substring. History orders revision_number ascending and
returns bounded summaries plus current header/version; both pages expose count,
has_more, next_cursor and audit_watermark. Show/write return header/current version,
selected revision and ordered commercial lines, captured profile/tax components,
document subtotal/tax/gross, separate batch totals and provenance. Write adds the
existing changed/warnings/changed_fields metadata plus facts_fingerprint. No read
or preview produces an audit event. Error contracts include E_VALIDATION,
E_RECORD_NOT_FOUND, E_VERSION_CONFLICT, E_PREVIEW_STALE, E_DUPLICATE_NUMBER,
E_PERIOD_CLOSED, E_INACTIVE_REFERENCE, E_VALUE_RANGE, E_AMOUNT_PRECISION,
E_REASON_REQUIRED, E_QUERY_STALE and the ordinary permission/idempotency errors.

Custom fields use the existing journal contract in full: required/default values
on creation; unrelated edits retain values and captured labels even after master
changes; explicit null clears; false/zero/empty are distinct; invalid scope/kind
fails atomically; inactive definitions remain renderable. Browser post/update and
preview/history preserve these distinctions and make at least46 fields reachable.

### Customer balance projections

Customer show and list/query derive signed home-currency AR debit-minus-credit
from every posting effect for that exact customer/job. Do not double count jobs
in parent rows. `current_balance` and `open_balance` both represent this net AR
control balance, including customer-tagged journals, reversals and future payments;
label them as net balances, not invoice-aging totals. `balances_available=true`.
Sorting/filtering uses these derived values, not persisted zero placeholders.
Expose a separate explicitly labelled family balance on customer details summing
the customer and descendants exactly once. Customer transaction links filter
invoice/receipt queries by exact party and link AR detail to GL. Open-balance
filters become operational; overdue invoice filters and aging remain explicitly
staged with applications, not falsely equated to net GL balances. Copied companies
recompute identically. Test parent/job, negative journal credit, correction and
void cases independently of the sales form's output.

Invoice preview/post/update warns, without blocking, when proposed net AR exposure
exceeds the nearest non-null customer/job/ancestor credit_limit. Exposure is the
limit owner's family net AR plus the proposed invoice, minus the old invoice's
AR effect on update if its old customer belongs to that same family. Existing
credits reduce net exposure. No limit means no warning; zero is a real limit.
The warning names the limit owner, limit and proposed exposure in home money.
A receipt does not add an AR obligation or trigger this invoice warning. The
broader in-form credit/collections panel remains staged in the inventory.

### Evidence additions

At both1280/390 the browser witness must correct and void a posted sale, recover
from stale version/default-fingerprint errors while preserving unsaved lines,
and retry an ambiguous save with the same idempotency key and one sale. Exercise
custom values false/zero/empty/default/clear and historical renamed definitions.
MCP support here means registry readiness only; Row9 owns actual MCP execution
and error parity. UI/help state that paying an invoice requires the upcoming
customer-payment operation and never suggest creating a sales receipt for it.
The first increment rejects taxable sales receipts too under payment_receipt
tax policy; it does not claim full tax-policy coverage.

### Table contract

Company migration co0009 extends transactions.type to journal_entry, invoice and
sales_receipt. Existing history is retained byte-for-byte. document_lines becomes
a shared immutable line identity/position/currency/dimension envelope: journal
kind retains all existing required entered-account/side/positive-amount checks;
sale kind has those journal-only fields null and a one-to-one sales line profile.
No journal line is reinterpreted or rewritten as a sale.

New immutable sales_profiles are keyed by revision_id, referencing that revision
and transaction; fields include type, customer_id, control_account_id, due_date,
subtotal_minor_units, tax_minor_units and bounded typed snapshot JSON for the
resolved form facts. sales_line_profiles are keyed by document_line_id, referencing
its exact transaction/revision/line; fields include item_id, quantity_microunits, unit_id,
unit_factor_nanounits, base_quantity_microunits, unit_price_minor_units, net_minor_units,
tax_minor_units, gross_minor_units and typed snapshot JSON for resolved item,
account, price, unit and tax-code facts. Immutable sales_tax_components have ids,
exact owner revision/document_line_id, tax_item_id, agency_id, liability_account_id,
rate_percent_millionths, taxable_minor_units, tax_minor_units and captured labels.
All new rows carry creation provenance and are audited in the same event. JSON
snapshots have strict versioned Pydantic shapes, not untyped escape hatches.

posting_line_sources adds nullable tax_component_id with same-document/revision/
line composite ownership. A source without component attributes line net; a
source with component attributes its tax. Positive source amounts sum exactly
to each posting leg. No source may attribute a different line or amount than its
validated commercial facts. New immutable tables have update/delete guards.
Schema docs describe the actual new profile tables and constraints. Migration
rebuilds preserve all existing columns/values/indexes/immutability triggers,
foreign-key-check before commit; failure restores original schema and data.

Invoice: Dr AR per line gross, Cr line income for net, Cr each component's tax
liability. Sales receipt: same credits, Dr deposit_to per line gross. Customer/job
and line class attribution accompany each leg. No double revenue when a payment
later settles an invoice. Batch debit/credit totals are independently computed;
document gross is not derived from summed debit legs. Shared journal reporting
continues to count every original/reversal/replacement effect of every type.

Update appends a full revision, old-date exact reversal, and new-date replacement
under one audit event; both dates must be open. Void reverses the current business
batch at its original date. Every historical posting/source fact and inverse link
is retained. Application/deposit/reconciliation dependencies do not exist yet;
their owning increments must add edit/void protection before exposing those writes.
No temporary use of memo matching or direct journal edits as a dependency system.

The writer rebuilds from original typed intent under BEGIN IMMEDIATE and validates
commercial arithmetic, original input consistency, ownership, type/account rules,
complete posting attribution and exact reversals before persistence. A coherent
tamper of derived document plus posting amounts must still fail against original
input. Reuse common immutable posting/reversal primitives where they are actually
generic; journal-specific validation must remain journal-specific. Journal/register
show/update/void paths must reject invoice and receipt ids. Existing journal custom
field snapshot code may gain an explicit record_type parameter preserving its
default; sales must enforce invoice/sales_receipt scopes and protect their slots.

## Browser, documentation and demonstration

Both nouns appear under Customers and sales. Use existing typed forms, active
selectors and structured line rows, with clear quantity/unit/price/description/
tax controls. Preview and details render issuer/customer addresses, document
number/date/due/payment facts, ordered commercial lines, subtotal/tax/total and
captured tax components, plus history/void status. Accounting details remain an
inspectable secondary section. Existing notes/files/activity attach to the stable
transaction. A readonly user can inspect but cannot see executable write actions.
Show history by selected revision; current master names must not replace saved
printed facts. Desktop1280 and phone390 remain usable without text escaping cards.

Generated CLI/help/schema docs and examples cover every new command and staged
limitations. Examples use ordinary phrases: make an invoice; record a paid sale;
correct invoice; void invoice. Sending remains visibly unavailable until delivery
exists. Both seeds exercise every command in read or intentionally disposable
write scenarios, including taxed service and a receipt; fixed-year reference
expectations are updated from explicit accounting effects. Preserve existing demo
records and refresh the LAN demo only after a reviewed increment and closed backup.

## Acceptance evidence

Use a nonstock counterpart of accounting-contract-fixtures1/4/5/6: service10000,
tax800, invoice10800: AR10800; revenue10000; tax800; profit10000. A paid-sale branch
puts10800 in Undeposited Funds with zero AR. Revision adding nontaxable2000 yields
12800 with original facts still renderable; void nets that document to zero.
Test multiple tax components, exempt customer, rounding half cents, zero-price
lines, unit conversion, explicit/default prices, price-level modes, inherited job
defaults, standard/date-driven terms and month/year limits. Cover inactive saved
facts versus changed references and refresh, line identity reuse, stale versions,
duplicate numbers, idempotent replay, closed-date corrections, invalid item types,
foreign inputs, integer overflow including components/batch totals, plan tampering,
injected mid-write rollback, annotations/custom scopes and journal-id type fences.
Actual migration from populated co0008 preserves journals/custom/foreign facts,
audit/idempotency/history and copied-company reopen; test failure rollback and
direct update/delete guards. Public Python/CLI/HTTP results and error parity;
actual Chrome entry/preview/post/history at1280/390; readonly and sibling-company
isolation. Existing focused journal/register/report checks stay green. Fresh plan
review before code and independent artifact review with meaningful mutations in
its own exact-commit checkout precede closure. Subsequent implementation is governed by its own spec, with the approved
customer-work-and-billing workflow providing the next target.
