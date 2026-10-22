# Sales orders, committed stock and customer prepayments

This contract covers three roadmap items: the stock sales order (R101), stock status that counts
what open orders and work orders have committed (R102), and customer prepayments held as a
liability until the work bills (R124). It fills the sales-order row of blueprint §10.3 and the
"separate planned document" of §13.1, the order, availability, commitment and backorder parts of
CW46 and the deposit part of CW48 ([customer-work inventory](inventories/customer-work.md)), and
W14 of the service-sales inventory. Pick, pack and ship, sites and bins stay with R104 and R139;
assembly builds with R103.

It amends four rules: stocked items become allowed on proposals, estimates, work orders and sales
orders ([customer work](customer-work.md) and `sales_defaults.resolve_line` refuse them on every
non-posting kind today); an estimate gains a second kind of operational destination; stock status
stops reporting available as on hand; and every `<kind> invoice` conversion gains
`apply_prepayment`.

Sources for the anchor: **[M]** the supplied Missing Manual (2016), printed page numbers;
**[E]** the anchor's Enterprise 24.0 in-depth guide; **[R]** its Enterprise 16.0 reports guide.

## 1. The anchor's behaviour, and where Bookflow differs

**Matched.**

- A sales order is a non-posting form shaped like an invoice with an "Ordered" column, enabled by
  a preference with options to warn about duplicate numbers and to leave zero-amount items off
  invoices made from it [M 260–261, 669]. An invoice is made from the whole order or selected
  items, "To Invoice" filled with on hand or, by a checkbox, available [M 262–263]; the order
  keeps a running Backordered quantity [E 49–50, 84]; a new invoice offers the customer's open
  orders [M 224].
- "Create Purchase Order" from an order proposes the quantities needed to fill the backorder,
  fills in each item's preferred vendor, and makes one purchase order per vendor [M 263–264; E 84].
- Available is on hand less quantity on sales orders by a preference ("When calculating Quantity
  Available, deduct: Quantity on Sales Orders / Quantity reserved for Pending Builds"), turned on
  automatically when sales orders are used [M 660]. Selling past available shows on hand, on other
  orders and available, and proceeds [M 232, 261]. An order line shows on hand, committed
  elsewhere, pending build and on order [E 47]; stock status shows reorder points, on hand, on PO
  and on sales orders [M 541; R 25]; Open Sales Orders by Customer and by Item are reports [R 13].
- Customer prepayments are native in the current anchor: recorded on a sales order, applied
  automatically when it becomes an invoice, optionally held as a current liability [E 10, 65]. The
  older practice is an Other Current Liability "Customer Prepayments" and a prepayment item: a
  sales receipt takes the money, the item deducts it on the invoice, a credit memo plus a refund
  returns it, and an invoice turns a kept deposit into income [M 243–248].

**Deliberate differences.**

- Once a line has been invoiced, its quantity and price are fixed, because the exactly-once
  billing ledger measures every invoice against the line's original basis: less is ordered by
  cancelling quantity, more by adding a line. The anchor edits the quantity in place.
- With no selection, an invoice from an order bills what can ship to that order (on hand less
  other documents' commitments); the anchor fills on hand. The invoice carries only the lines
  being invoiced, with backordered quantities read from the order, so the anchor's zero-amount
  lines and their print preference have no counterpart.
- Work orders, a Bookflow extension, may carry stocked lines and commit them as orders do.
- A prepayment is always a liability; the anchor's receivable option is not offered. It is drawn
  by a credit memo on the prepayment item, applied or refunded, not by a negative invoice line,
  because settlement, aging and cash basis already handle an applied credit and no negative
  lines. It may sit on an accepted estimate, a work order or a sales order.
- A sales order is not fenced by the closing date, like estimates and purchase orders today; the
  anchor warns by default [M 644]. One order per invoice: combining orders is an
  accountant-edition feature [E 51].

## 2. The sales order document

### A kind in the customer-work family

A sales order is a fifth customer-work kind, `sales_order`, stored in `work_documents`,
`work_revisions`, `work_line_identities` and `work_lines`, and billed through
`work_billing_conversions` and `work_billing_allocations`. The reasons are in the code:

- The family's content is sell-side: customer or job, selling items, prices, quoted tax and the
  captured commercial profile. `purchase_order_schema` gives exactly this as the reason purchase
  orders stand apart; for a sales order the same reason points the other way.
- Invoicing part of a line exactly once is already built: quantity selections allocate exact spans
  of a line's billing root, active only on a posted invoice's current revision.
- Estimate to sales order inherits billing roots as estimate to work order does, so the quote and
  the order can never both bill the same goods.
- A new kind has precedent: co0029 and co0055 rebuilt the family's tables to admit a voided
  estimate and recorded time. `custom_field_scopes` already admits `sales_order`, and the work and
  billing command registries generate their commands per kind.

Blueprint §13.1's "separate document" is a separate kind with its own lifecycle. Completion still
moves no stock: on an order or a work order, stock moves only when an invoice posts.

What is specific to the kind: no acceptance, completion, schedule, priority or assignee; lines are
always billable; invoicing is by quantity (§4); and the order converts to an invoice, never a
sales receipt (the anchor offers none). Every `sales-order` command names the order by number or id.

### Header and lines

Create requires customer or job and date; number is optional on the `sales_order` sequence key;
title is optional and defaults to the customer's name, as recorded time derives its own. Every
other header field is the family's: customer PO, billing and shipping address, ship date, ship
method, terms, sales rep, class, tax code and tax item, price level, customer message, memo,
captured tax policy and typed custom fields.

Lines number 1–200 and take any selling item: service, non-inventory part, other charge,
inventory part and inventory assembly, plus subtotal, discount, percentage charge and group
expansion as on sales. Ordered quantity is positive; units, price modes and estimated cost are the
family's; a stocked line captures the COGS and inventory-asset accounts its invoice will post to.
`completed_quantity` and `billable` are refused.

The same resolver change admits stocked lines on proposals and estimates, which reserve nothing,
and on work orders, which commit them (§3); `estimate work-order` carries them. A stocked line is
always billable. A part given free is a zero-price stocked line, and an invoice whose only charge
is such a line may total zero, as ordinary invoices already may when they move stock.

### Quantities and states

All derived, never stored as counters. Sales order and work order lines share them:

| Figure | Meaning |
|---|---|
| ordered | the line's quantity in the current revision |
| invoiced | ordered less the free quantity of the line's billing root, read from active allocations |
| cancelled | `cancelled_quantity`, a revision fact the person sets |
| open (the anchor's Backordered) | ordered − invoiced − cancelled; zero while the document is closed, cancelled, voided or inactive |
| open amount | the exact net of the open quantity, by the billing ledger's endpoint rule |

Cancelled quantity is carved from the top of the line's free quantity and invoicing takes the
lowest free spans, so the two never meet. Saving requires cancelled ≤ ordered − invoiced, and
releasing an invoice only frees more. The line input `close: true` sets cancelled to the current
open quantity (the anchor's "Clsd"); setting cancelled back to zero reopens the line. A work
order completes when each line's completed quantity equals ordered less cancelled.

Line state: `open`, `partly_invoiced`, `invoiced` (open zero, nothing cancelled), `closed` (open
zero, some cancelled). Order progress: `open`, `partly_invoiced`, `invoiced` (the anchor's
"Invoiced in full"), `closed`, `voided`. An order is **open** when its stored status is `open`, it
is active and some line has open quantity.

### Lifecycle

Stored status holds only what a person decided:

| From | To | Rule |
|---|---|---|
| (create) | `open` | — |
| `open` | `closed` | reason required; every line's open quantity reads zero; a held prepayment is warned about, not refused |
| `closed` | `open` | reason required; cancelled quantities are as they were |
| `open`, `closed` | `voided` | `sales-order void`, reason ≤ 140; refused with E_WORK_DEPENDENCY while an active invoice consumes any root or a prepayment is held on it; terminal |

Closing is a status change on `sales-order update`, as on purchase orders; void is its own
command, as on estimates. A sales order is never deleted; every revision stays readable.

Update replaces the whole document under its expected version and appends a revision. Header
facts and unbilled lines edit freely. A consumed line keeps its item, unit, quantity and price and
cannot be removed (E_WORK_DEPENDENCY), and the customer stays fixed while any root is consumed;
memo, ship date and method, tax code, custom values, cancelled quantity and added lines stay
editable. `sales-order copy` makes an independent open order with new roots.

`estimate sales-order <estimate>` takes expected version, a permanent conversion key, date,
optional number, ship date and method, and destination custom values. It requires the group's
accepted estimate and carries its customer, commercial profile and lines with their billing
roots, so anything already invoiced from the estimate reads as invoiced on the order. An estimate
has at most one operational destination, a work order or a sales order, permanently, and its
billing then directs there. An order's inherited lines may be repriced or re-quantified while no
invoice consumes them: the order is the customer's order, and the estimate keeps its revision.

## 3. Committed stock and availability

**Committing documents** are open sales orders and work orders that are active and not cancelled,
dated on or before the report date; a complete work order still commits until it is invoiced.
**Committed** per item is the open quantity of their stocked lines in the item's base unit. One
function reads the documents' current revisions and the allocation ledger and every reader calls
it, as `receiving.order_remaining` owns on-order; nothing is stored. As with on-order, a past
report date selects documents dated by then and measures what remains open now. A line for an
assembly commits the assembly item itself.

**Available.** `company_info` gains `available_deducts_sales_orders` (default true, the anchor's
automatically-on setting; it governs work-order commitments too) and `sales_orders_enabled`
(default true; off refuses new orders and new estimate conversions with E_FEATURE_DISABLED and
leaves existing orders readable and invoiceable, as `estimates_enabled` does). Available is on
hand less committed while the preference is on, on hand while it is off. **Available to a
document** leaves out its own commitment: on hand less what every other document commits (on hand
while the preference is off). R103 adds pending builds as a second source with its own preference,
`available_deducts_pending_builds`; the function returns its sources separately.

**Stock status and items.** `report stock-status` rows gain `quantity_committed` (on sales orders
and work orders), `quantity_for_assemblies` ("0" until R103) and `next_delivery_date` (the earliest
expected date of a live purchase order with quantity to arrive); `quantity_available` becomes the
figure above; `below_reorder_point` compares available, not on hand, with the reorder minimum.
With nothing committed the two are equal, so today's companies see no change. On order stays
beside the flag, not in it. `item show` and item lists return real `quantity_committed`,
`quantity_on_order` (today a fixed "0") and `quantity_available`.

**Warnings.** An order or work order preview gives each stocked line its on hand, committed
elsewhere, on order and available to it, and warns when the item ends short. An ordinary invoice
or sales receipt that takes committed stock warns and names the documents. Neither refuses.

## 4. Invoicing from an order

`sales-order invoice` and `sales-order billing` join the billing registry by adding `sales_order`
to `BILLING_KINDS`, without the sales-receipt verb. Inputs are the existing conversion inputs
(expected version, conversion key, date, number, AR account, terms, due date, memo, custom values,
preview fingerprint) plus `apply_prepayment` (§6).

**What it bills.**

- With no selection, what can ship: each stocked line bills min(open, available to this order),
  shared in line order among the order's lines for one item; other lines bill their open quantity
  when anything ships or the order has no open stocked line. If nothing can ship, E_VALIDATION
  gives each line's open quantity and availability and the explicit choices. When orders compete
  for the last units, each sees the others' commitments first and ships none by default; the
  person chooses which order gets them.
- `all_open: true` bills every open quantity whatever the stock; `line_ids` bills whole open
  remainders; `selections` take a quantity per line, and an entry may name `item` instead of
  `line_id` when exactly one open line has that item.
- The browser's Create invoice dialog is filled from this command's dry-run preview, so the two
  cannot drift apart.
- Percent, net-amount and rebill selections are refused; the progress-billing preference is not
  consulted. A quantity whose base quantity is not a whole number of microunits is refused, as are
  percent and net selections on stocked estimate and work-order lines.

**Tax.** The conversion uses up quantity and net only. The invoice is taxed as an ordinary invoice
of its own date: header and line tax selections the order took by default are resolved again,
explicit ones carry, and the company's current policy and the tax items' rates apply as written
that day. Bookflow keeps no dated rates and picks no tax item by address, so the ship-to's tax is
the selected tax item. The preview shows, per line and in total, any difference from the order's
quoted tax. Estimates and work orders keep billing their captured tax, the agreed quote.

**Stock and commitment.** Posting the invoice issues the base quantity and consumes the line in
one company transaction, so on hand and committed fall together. Conversions on one order
serialize on its version; a retried key returns the original invoice with no second issue or
release. Selling past on hand goes through with the existing below-zero warning.

**Corrections.** A financial correction reopens order quantity only when it takes back what an
invoice billed, and always says so in its preview and result, naming the order lines:

| Correction to an order-made invoice | Open quantity and commitment | Billable again |
|---|---|---|
| void or delete | the billed quantity reopens; with `release_to_order: cancel` it is cancelled on the order in the same transaction | yes when reopened, no when cancelled |
| update removing an order-made line | that line's quantity, with the same choice | as above |
| other update (date, terms, memo, independent lines) | unchanged | unchanged |
| credit memo for price, discount or write-off | unchanged | unchanged |
| credit memo returning stock | unchanged: stock returns to on hand and the line stays invoiced; a replacement is a new line | no |

The same rules hold for invoices made from work orders. Changing an order-made line's quantity in
place stays refused; remove the line or void, then invoice again.

**Agents bill from invoices.** The preview and result of `invoice post`, `sales-receipt post`,
`payment receive` and every `<kind> invoice` list the customer's open sales orders (number, open
lines, open amount) and held prepayments, each with the exact next command: `sales-order invoice
<n> --expected-version <v> --conversion-key <suggested> --date <d>` when the lines match an open
order; `prepayment apply` for held money; and, when a payment would sit unapplied while the
customer has an open order, work order or accepted estimate, `prepayment receive --document <n>`.
They advise and never refuse.

The invoice's show output and printed form give each order-made line its ordered, previously
invoiced and backordered quantities, read from the order.

## 5. Shortfall coverage

`sales-order purchase-order <order>` and `work-order purchase-order <work order>` take date,
optional vendor, optional `lines` (line or item, quantity), expected date and memo, and write an
ordinary purchase order through the `purchase-order post` service plus one
`purchase_coverage_links` row per purchase-order line (document line, purchase-order line,
quantity), in one transaction. One call makes one purchase order: default lines are the short
stocked lines whose preferred vendor is the vendor; with none given and several vendors among them,
E_VALIDATION lists each vendor with its lines. The rate is the purchase order's own cost resolution.

**Coverage** of a link is min(its quantity, the purchase-order line's quantity still to arrive,
from `receiving.order_remaining`); links never change, coverage is derived:

- amended up: the extra is unlinked incoming; amended down: coverage falls to what is left;
- received by item receipt or bill: coverage falls by the quantity received, which is now on hand
  and open to every document, as on-hand stock is;
- purchase-order line removed, or the purchase order closed or voided: coverage is zero and the
  shortfall shows again.

**Default quantity.** For each committing document X and item, residual(X) = max(0, open(X) −
coverage(X)). The shared pool is on hand plus incoming not covered by any link. Then

shortfall(D) = max(0, residual(D) − max(0, pool − Σ residual of every other document))

shared in line order across D's lines. A document's linked coverage serves only it, other
documents' uncovered demand has first claim on the pool, so no incoming unit is counted twice, and
a retry, or a second run before anything changes, orders nothing. An explicit quantity may be any
positive amount (spares). The output lists short lines left for other vendors. Each document line
shows its links with quantity to arrive and expected date; each purchase order shows what it covers.

## 6. Customer prepayments

### The ledger shape

- **Account.** "Customer Prepayments", other current liability, system role
  `customer_prepayments`, created on first use. Every posting on it names the customer or job.
  `journals.OWNED_SYSTEM_ROLES` gains the role, so the journal editor is refused on it as on
  inventory asset, and the account always ties to the prepayment records.
- **Item.** "Customer Prepayment", other charge, non-taxable, crediting that account, created with
  it. Ordinary items still cannot name a balance-sheet account another ledger owns
  (`items.sold_account_problem`); this item is that ledger's own. It is refused on invoices and
  on customer-work documents.
- **Documents.** Each effect is an ordinary document with its own number; `prepayment_links` and
  `prepayment_draws` record what each is for, as a bounced check is ordinary documents tied by its
  own record:

| Effect | Ordinary document | Posting |
|---|---|---|
| receive | sales receipt with one Customer Prepayment line | Dr Undeposited Funds or bank; Cr Customer Prepayments |
| draw | credit memo with one Customer Prepayment line per receipt drawn | Dr Customer Prepayments; Cr Accounts Receivable |
| apply | `customer-credit apply` of the draw to an invoice | none (settlement) |
| refund | `customer-refund post` against the draw | Dr Accounts Receivable; Cr bank |

A draw is applied or refunded in the transaction that writes it, so the money never sits as a
stray receivable credit.

### Where it is held

A receipt is held for its customer, or for one customer-work document: an accepted estimate, a work
order or a sales order. Money held on an estimate serves the estimate's billing owner, its work
order or sales order once it has one. `prepayment attach <receipt> --document <n>` moves a
receipt's undrawn remainder to a document (or back to the customer) by appending a link that
supersedes the old one. Held per receipt is its amount less its posted draws; per document, the
sum over the receipts linked to it or to its source estimate; per customer, the account balance.

### Using a prepayment

- **On invoicing.** Every `<kind> invoice` takes `apply_prepayment` (default true). It draws
  min(held for the source document, the new invoice's balance), dated the invoice date, oldest
  receipt first, and applies it to the invoice in the conversion's transaction; preview and result
  report `prepayment_applied` and `balance_due`. Anything left stays held for the next invoice
  [M 246].
- **By hand.** `prepayment apply` takes invoice, amount, date and the document, customer or
  receipts drawn from, within the caps below.
- **Undo.** `prepayment unapply <draw>` unapplies the draw and voids its credit memo in one
  transaction, returning the amount to its receipts. `invoice void` on an invoice holding a draw is
  refused with the existing E_HAS_APPLICATIONS, and the hint names `prepayment unapply`.
- **Refund.** `prepayment refund` takes document, customer or receipts, amount, date, bank account,
  method and check number, and writes draw plus refund.
- **Keeping part.** Only an explicit invoice turns held money into income: the fee is invoiced and
  `prepayment apply` pays it [M 247–248]. Closing a document never does; money left after a
  final invoice stays held, shown on the customer and the document, until applied or refunded.
- `prepayment receive` takes document or customer, date (default today), amount, payment method,
  reference, deposit-to (the sales-receipt default, Undeposited Funds), class and memo. The receipt
  deposits, prints as the customer's receipt, corrects, voids and deletes as any sales receipt,
  under the guards. Help lists "deposit", "down payment" and "retainer" as synonyms, and
  `deposit post` help points a customer's deposit to `prepayment receive`.

### Guards and guarantees

Checked under the company writer lock before any write that posts, corrects, voids or deletes a
receipt, draw, application or refund:

- **Attribution.** Every draw line names the receipt it draws from (`prepayment_draws`); a draw is
  dated on or after its receipts and never exceeds a receipt's undrawn amount, and refunds, reports
  and undo all follow that attribution.
- **Caps.** A draw never exceeds what its source holds nor the invoice's open balance, across any
  number of partial invoices (E_APPLIED_EXCEEDS_TOTAL, with both amounts); the credit application
  itself refuses over-application; a customer's running balance on the account is never below
  zero on any date.
- **Holders.** A receipt whose money is drawn cannot be voided, deleted or reduced below what is
  drawn, and a document holding money cannot be voided or cancelled until it is applied, refunded
  or attached elsewhere (E_HAS_APPLICATIONS, naming the draws or receipts).
- **Explicit income.** No path but an invoice and a draw applied to it moves held money to income.
- **Closed periods.** Draws, applications, unapplies and voids are dated documents and rows under
  the closing date: an unapply uses the original application date and the void the credit memo's
  own date, so a draw inside a closed period is corrected by a later-period credit memo or refund,
  never unapplied.
- **Cash basis.** A receipt moves only balance-sheet accounts. A draw applied to an invoice settles
  it as any credit does, recognised at the latest of the invoice, credit memo and application
  dates, so a January prepayment for a February invoice is February income; unapplies and voids
  are consulted as inverses, as for any credit.

### Reports and balances

The balance sheet shows Customer Prepayments under other current liabilities. Documents show held
money with each receipt and draw. Customer balances, aging and statements stay receivable reports
and exclude held money; the customer page and `customer show` add `prepayment_held`, and the
statement notes it below the balance. `report customer-prepayments` (as-of date, by customer,
document and receipt) lists receipts and draws with running held balances and refuses, as
inventory valuation does, if its total differs from the account. Sales reports read income
postings and the sales-tax liability report reads tax postings, so a prepayment is neither a sale
nor taxable.

## 7. Surfaces

### Storage and migrations

Two company migrations, each numbered next after main's head when it merges.

**Prepayments:** new immutable tables, update and delete rejected by trigger:

- `prepayment_links`: id, receipt transaction id, customer id, work document id (nullable; an
  accepted estimate, a work order or a sales order), supersedes link id (nullable, unique),
  created at/by/via, audit event. A receipt's current link is the one nothing supersedes.
- `prepayment_draws`: id, credit memo transaction id and line identity (unique together), receipt
  transaction id, work document id (nullable), invoice id or customer refund id (exactly one),
  created at/by/via, audit event. A draw counts while its credit memo is posted; its amount is that
  line's amount in the current revision.

**Orders**, preserving every row, index and trigger as co0055 did:

- `work_documents`: `ck_work_kind` admits `sales_order`; `ck_work_status` adds `(kind =
  'sales_order' AND status IN ('open','closed','voided'))`; trigger `work_revision_kind` likewise.
- `work_links`: the relation check admits `estimate_sales_order`; the partial unique index
  `uq_work_estimate_order` covers `relation IN ('estimate_work_order','estimate_sales_order')`.
- `work_billing_conversions`: the relation check and trigger `work_billing_conversion_birth`
  admit `sales_order_invoice`.
- `work_lines` gains `cancelled_quantity_microunits` (integer, not null, default 0, at most
  `quantity_microunits`, nonzero only on sales-order and work-order lines).
- `company_info` gains `sales_orders_enabled` and `available_deducts_sales_orders`, default true.
- `purchase_coverage_links`: id, work document id (a sales order or work order), line identity,
  purchase order id, purchase-order line identity (unique), quantity, created at/by/via, audit event.

`custom_field_scopes` already admits `sales_order`. The account and item are created on first use.
Both demo seeds gain an order invoiced in part with a backorder, a purchase order covering it, a
work order with a stocked part, a deposit on an accepted estimate drawn by its work order's
invoice, and a refunded prepayment; existing oracles keep their figures or move by stated amounts.

### Commands

| Command | Does |
|---|---|
| `sales-order create`, `update`, `copy`, `show`, `query`, `history`, `void` | the document (§2) |
| `estimate sales-order` | conversion with inherited roots |
| `sales-order invoice`, `sales-order billing` | §4; billing shows per line ordered, invoiced, cancelled, open, stock, invoices, coverage and prepayments |
| `sales-order purchase-order`, `work-order purchase-order` | §5 |
| `prepayment receive`, `attach`, `apply`, `unapply`, `refund`, `show`, `query` | §6 |
| `invoice void`, `delete`, `update` | gain `release_to_order` (`reopen` default, `cancel`) for order- and work-order-made lines |
| `report open-sales-orders` | `by` customer (default) or item; filters customer, item, order dates, `short_only`; rows of date, number, customer PO, ship date, item, ordered, invoiced, cancelled, backordered, open amount; by item adds on hand, available and on order |
| `report customer-prepayments` | §6 |

All are registry commands, so the CLI, HTTP, MCP (`bookflow_run`) and the browser reach them
identically, each with dry-run, idempotency on writes and a worked example in its help.

### Permissions

| Commands | Role | Authority |
|---|---|---|
| sales-order writes, `estimate sales-order` | standard | `customer-work` |
| sales-order reads, `report open-sales-orders` | member | `customer-work`, reports |
| `sales-order invoice` | standard | `ledger.post` and `customer-work`, both checked, as `estimate invoice` |
| `<kind> purchase-order` | standard | `customer-work` and what `purchase-order post` requires |
| `prepayment` writes | standard | what each written document requires (sales receipt; credit memo with customer-credit apply or unapply; customer refund), all checked before preview; `attach` needs `customer-work` |
| `prepayment show`, `query`, `report customer-prepayments` | member | ledger read, reports |
| the two company preferences | admin | as other company preferences |

An agent with the standard role can do all of it; nothing here is reserved to a person.

### Browser

- Customers gains **Sales orders**, opening on open orders (number, date, customer, ship date,
  total, open amount, progress) with + New. The order form is the estimate form with Ordered,
  Invoiced, Backordered and Close columns, a stock panel per stocked line (on hand, committed
  elsewhere, on order, available to this order) and a prepayment panel (held, receipts, draws,
  Receive, Refund); actions Save, Create invoice, Create purchase order, Copy, Void, Print. Work
  orders gain the stock and prepayment panels and Create purchase order; estimates gain Receive
  prepayment and Make sales order.
- **Create invoice** shows the preview's quantities, editable, with a "show available instead of
  on hand" switch, the tax difference from the quote and the prepayment to apply. **Create
  purchase order** lists short lines with vendor and proposed quantities, editable.
- Invoice, sales receipt and payment forms show the commands' open-order and prepayment notes,
  each with its button; the item page shows on hand, committed, on order and available, linked to
  the documents. Every screen works at 1440 and 390 pixels.

### An agent's first order

"Take an order for 10 widgets for Acme; 6 ship today":

```
bookflow sales-order create --customer "Acme" --date 2026-10-22 \
  --lines '[{"item":"Widget","quantity":"10"}]' --reason "Acme ordered 10 widgets" --json
bookflow sales-order invoice 1001 --expected-version 1 --conversion-key acme-1001-ship1 \
  --date 2026-10-22 --reason "6 widgets shipped today" --json
```

The first returns the order number, version 1 and, for the Widget line, on hand 6, committed
elsewhere 0 and the warning "4 Widget short; order them with sales-order purchase-order". The
second, with no selection, bills what can ship: 6 invoiced, 6 issued, 4 backordered. An agent that
starts from `invoice post --customer Acme` is shown order 1001 and this exact command.
`sales-order purchase-order 1001` orders the 4; when they arrive, the same invoice command bills
them. A dry run of each shows the same figures before anything is written.

## 8. Build increments

Prepayments come first, alongside the order document: they depend only on today's estimates, work
orders and billing conversions, serve contractors' deposits at once, and their tables name any
work document, so the order kind later becomes one more target with no change. Committed stock
follows the document; invoicing and shortfall coverage then run side by side.

1. **Customer prepayments.** Its migration; account and item; `prepayment` commands; links,
   attachment and attributed draws; `apply_prepayment` on every `<kind> invoice`; prepayment
   notes on invoice post, sales-receipt post and payment receive; `report customer-prepayments`;
   panels. *Done when* a deposit on an accepted estimate is drawn by its work order's invoice; 200
   held across two receipts is drawn 150 then 50 by two partial invoices, oldest receipt first;
   a customer-level receipt attached later is drawn; unapply and refund restore and pay out; a kept
   fee reaches income only through its invoice; every guard refuses with its hint. *Tests:* those
   cases; closed-period unapply refused; both bases against a hand-worked example; the report
   tying to the account on any date; a draw racing a receipt void; the receipt banked by `deposit post`.
2. **The order document.** Its migration; `sales-order` create, update, copy, show, query,
   history, void; stocked lines on all four kinds; cancelled quantity; `estimate sales-order`;
   the preferences; list, form and print; orders as prepayment targets once 1 is in; demo seeds.
   *Done when* an order with stocked, service and discount lines saves, revises, closes, reopens
   and voids with every revision readable, an estimate with stocked lines becomes an order or a
   work order, and a second destination is refused. *Tests:* migration up and down on a populated company keeping
   local objects; the write commands on library, CLI and HTTP; conversion replay and
   different-intent key; void refused after invoicing or while a prepayment is held.
3. **Committed stock.** The commitment function over orders and work orders, available to a
   document, stock-status and item figures, warnings, `report open-sales-orders`. *Done when*
   committed equals the open stocked quantity across documents (a complete, uninvoiced work order
   included), switching the preference moves available and nothing else, and a company with
   nothing committed reports exactly as before. *Tests:* a fixture of two orders and a work order
   against hand-worked figures for every state; paging by customer and item; stock-status time on
   the demo and on 1,000 open lines, measured and recorded.
4. **Invoicing from orders.** `sales-order invoice` and `billing`, what-can-ship default and the
   explicit choices, invoice-day tax, corrections with `release_to_order`, open-order notes on
   ordinary billing and payment commands, the dialog filled from the preview. *Done when* 10
   ordered with 6 on hand bills 6 by default and 4 backordered; voiding reopens or cancels as
   chosen and says which; a return leaves the order invoiced. *Tests:* a tax rate changed between
   two partial invoices, each taxed at its own day's rate with the difference shown; simultaneous
   partial invoices and retried keys, each quantity billed, issued and released once; two orders
   competing for the last units; every row of the corrections table.
5. **Shortfall coverage.** Both `purchase-order` commands, coverage links, the default quantity,
   both displays. *Done when* a 4-short order makes a 4-unit purchase order from the preferred
   vendor and a retry orders nothing; two orders never count the same incoming units; amending
   down, receiving and voiding the purchase order bring the shortfall back by exactly what each
   removed. *Tests:* those cases, mixed vendors, spares, a hand-made unlinked purchase order.

**Closing trial.** A fresh agent with only Bookflow's help and MCP, given prompts that never name
the feature ("take an order for 10 widgets for Acme, 6 ship today", "Acme sent $200 toward their
order", "bill Acme for what shipped today", "the rest came in; bill it"), does each correctly on
its first attempt. One more run starts in the browser, where a person enters the order and the
deposit, and an agent finishes it from the same prompts.

## 9. Open questions

Each default stands if nobody decides.

1. **Changing an order-made invoice line's quantity in place**, as the anchor allows. *Default:*
   keep the linked-line rule; remove the line or void the invoice and invoice again.
2. **The reorder flag's basis.** The guides show the Order column and On PO side by side without
   stating the rule. *Default:* available at or below the reorder minimum, on order not counted.
3. **Open orders, work orders and held deposits at move-in.** *Default:* not in these increments;
   the move-in gains them as a later item.
4. **Deleting a sales order**, which the anchor allows. *Default:* void only, as for estimates.
