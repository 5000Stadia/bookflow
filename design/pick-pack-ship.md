# Pick, pack and ship

This contract covers roadmap item R181: picking lists, packing slips and shipments recorded apart
from invoicing, so a warehouse can ship before it bills. It fills what [sales orders](sales-orders.md)
left to the inventory passes and what [advanced inventory](advanced-inventory.md) §1 and question 7
left outside R104 and R139: the anchor's sales order fulfillment, its pick lists, packing and
shipping. It builds on what R104 built: sites, bins, serial and lot numbers, counts and their
positions, landed cost, barcodes and the Scan field.

**What exists.** Stock leaves only when a document that bills it posts: an invoice or sales receipt,
an order-made invoice included, issues the base quantity through `inventory_effects.write_movements`
and posts Dr Cost of Goods Sold, Cr Inventory Asset in the same entry as the sale. A sales order line
reads ordered, invoiced, cancelled and open (to bill), and commits its open quantity
(`commitments.py`). Packing slips, pick lists and shipping records do not exist; `ship-method` (the
anchor's Ship Via list) and the ship date and ship method header fields do, as text facts.

It amends four rules: (1) stock may leave on a **shipment**, a stock document of its own, and the
invoice that later bills it moves no stock; (2) the cost of shipped units waits in an owned asset
account, **Shipped Not Invoiced**, until an invoice moves it to cost of goods sold; (3) a sales order
line's committed quantity is what is still **to ship**, not what is still to bill; (4) the default of
`sales-order invoice` bills shipped units first.

Sources for the anchor: **[M]** the supplied Missing Manual (2016), printed page numbers; **[E]** the
anchor's Enterprise 24.0 in-depth guide, page numbers. The guides describe fulfillment in marketing
terms and rarely give its mechanics; where they are silent this document says so, and the rule given
is Bookflow's.

## 1. The anchor's behaviour, and where Bookflow differs

**Matched.**

- **One fulfillment screen.** A Sales Order Fulfillment dashboard shows every step for each open
  order; orders sort by fulfillment status, order date, customer or value; the user sees which
  warehouse should fill an order and may split it between warehouses [E 36]. The worksheet sorts open
  orders by date, shippable amount or customer, allows partial shipment or complete orders only, and
  batch-prints pick lists, packing slips or invoices for the selected orders [E 84]. Once new stock is
  received the user sees which orders can now be filled [E 36].
- **Six steps.** Order received (choose the warehouse), picker assigned, order picked (confirmed by
  scanning), packing, shipping (mark for shipping, print the label), inventory updated "after the
  package is shipped" [E 37]. Picker and packer roles may be combined [E 38].
- **Pick lists and packing slips** come straight from the sales order, to support partial shipments
  [E 49, 84]; pick lists and item receipts sort by bin location [E 46]; units of measure print on pick
  lists and packing slips [E 47]. Scanners pick items off the shelf and update pick, pack and ship
  status [E 39].
- **Backorders.** The order keeps what earlier partial shipments filled in a Backordered column; an
  invoice shows shipped and still-open quantities [E 49–50].
- **Invoicing at shipping.** Marking an item shipped prompts for an invoice, and the worksheet makes
  invoices in a batch [E 66].
- **The packing slip** is an invoice without prices [M 317], printed from a chosen template set by a
  company preference [M 669, 228]. The Ship Via list names carriers and one's own truck [M 146]; a
  Usual Shipping Method and a Usual FOB fill sales forms [M 669].

**Deliberate differences.**

- **A shipment is its own document and moves the stock.** The guides say stock is updated after the
  package ships and an invoice is prompted for [E 37, 66], and do not say what posts when the invoice
  is declined. Bookflow records the shipment as a dated stock document; the invoice that bills it,
  the same day or weeks later, moves nothing again (§3).
- **Cost of goods sold follows the invoice.** Shipped units leave Inventory Asset at the ship date and
  wait at cost in Shipped Not Invoiced; the invoice moves that cost to cost of goods sold beside its
  income. The profit and loss reads as it would have had the invoice shipped the goods itself, and the
  balance sheet no longer counts goods that have left (question K2).
- **Carriers are data.** Carrier, service, tracking numbers, boxes and weights are recorded and
  printed. Rates, labels, pickups and tracking lookups through outside services [E 36; M 239] are not
  built (question K1).
- **Assignment is a field, not a push.** A pick list names its picker; the phone screen is a browser
  page any picker opens. Nothing is sent to a device [E 37].
- **Orders in a batch, invoices one per order.** Batch actions run per order; combining orders on one
  invoice stays an accountant-edition feature, as in [sales orders](sales-orders.md) §1.

**Outside this item:** shipping labels and carrier accounts (K1); drop shipments (vendor ships to the
customer); e-commerce order import [E 38]; shipping from work orders and shipments with no order
(question 3); stock in transit between sites (advanced inventory question 4).

**Prior art.** From knowledge of two open-source systems, not re-checked against current releases:

- *Odoo* models every movement as a picking (`stock.picking`) of stock moves between locations; a
  warehouse chooses one, two or three steps (ship; pick then ship; pick, pack, ship) by routing goods
  through output and packing locations. Validating part of a picking offers a backorder picking for
  the rest. Packages carry contents and weight; carriers are separate connector modules. A product's
  invoicing policy bills ordered or delivered quantity. Under perpetual valuation with the
  Anglo-Saxon option, the delivery credits stock and debits an interim "stock output" account, and the
  invoice moves the interim balance to cost of goods sold.
- *ERPNext* has a Pick List (from sales orders, locations suggested by warehouse and batch), a Packing
  Slip against a draft Delivery Note (case numbers, net and gross weight), the Delivery Note as the
  stock document, and a Sales Invoice made from it that does not update stock. Under perpetual
  inventory the delivery note posts cost of goods sold at delivery. Orders track percent delivered and
  percent billed.
- *Adopted:* a separate stock document for the shipment, billed later (both); Odoo's interim account,
  so cost of goods sold meets its income; ERPNext's packing record tied to the shipment with box
  numbers and weights; pick suggestions by location and earliest expiry (both); delivered and billed
  as two figures per order line (ERPNext).
- *Rejected:* Odoo's mandatory location routes for each step, since R104's placements already say
  where units are; an optional staging bin gives the two-step case (§4). Backorder documents: the
  order already holds what is left. ERPNext's cost of goods sold at delivery, which reports cost in a
  month whose income is billed in the next. Carrier connectors (K1).

## 2. Order figures with shipments

A sales order line gains two derived figures; all are in base units, read from shipments, the billing
ledger and the order's current revision, never stored as counters:

| Figure | Meaning |
|---|---|
| shipped | the line's quantity on posted shipments, less what shipment returns took back (§6) |
| shipped not invoiced | shipped less what invoices have billed from those shipments |
| delivered | invoiced plus shipped not invoiced: every unit that has left, by a shipment or by an invoice that issued stock itself |
| to ship (the anchor's Backordered) | ordered − delivered − cancelled; zero while the order is closed, voided or inactive |
| open (to bill) | ordered − invoiced − cancelled, as in [sales orders](sales-orders.md) §2 |

- **Committed** becomes the to-ship quantity of stocked lines (amending [sales orders](sales-orders.md)
  §3). Shipped units have left the shelf, so they commit nothing; with no shipments, to ship equals
  open and every figure reads as before.
- **Cancelled** may not exceed ordered − delivered: shipped units are billed or returned, never
  cancelled. Closing an order holding shipped-not-invoiced units is refused with E_WORK_DEPENDENCY,
  naming the shipments and `shipment invoice` or `shipment return`.
- **Fulfillment status** of an order, derived: `to_pick` (something to ship, no live pick), `picking`,
  `picked` (picked units not yet shipped), `packed` (a draft shipment with every unit in a box),
  `partly_shipped`, `shipped` (to ship zero), beside its billing progress. The order page, `sales-order
  show` and the worksheet show both.
- **Sites.** One shipment leaves one site. An order line naming a site ships from it; one naming none
  ships from the shipment's site. Splitting an order between warehouses [E 36] is two shipments.

## 3. When stock leaves, and what the invoice does

### The shipment posts the stock

A shipment is a stock-moving document like an inventory adjustment: posting it writes one issue
movement per line through `inventory_effects.write_movements`, with placements at its site, bins, lots
and serial numbers (§4), on its ship date and count position, and one journal entry:

| Event | Posting |
|---|---|
| shipment posts | Dr Shipped Not Invoiced, Cr Inventory Asset, at the replay's cost of each line |
| invoice bills shipped units | its income, tax and receivable as any invoice; plus Dr Cost of Goods Sold, Cr Shipped Not Invoiced for the cost recognised (below) |
| invoice bills unshipped order units | as today: it issues the stock and posts Dr Cost of Goods Sold, Cr Inventory Asset |

**Shipped Not Invoiced** is an other current asset with system role `shipped_not_invoiced`, created on
the first shipment, joined to `journals.OWNED_SYSTEM_ROLES` so the journal editor is refused on it.
Every posting on it names the customer and the shipment line. Inventory valuation keeps tying item
values to Inventory Asset, since the shipment's issue took the units out of the item's value; `report
shipped-not-invoiced` ties the new account the same way (§9).

### Cost recognised, and every later change

The cost of a shipment line is the replay's value of its issue movement with every correction,
shortfall true-up and landed-cost true-up of it. The rule that keeps the account exact through
corrections is one derived balance:

- **The derived balance.** At any date, a shipment line holds in Shipped Not Invoiced its net cost
  (cost less what its returns took back, §6) less the endpoint share of that net cost for the quantity
  billed by live invoices dated by then, over the quantity still out (`core.exact.endpoint_share`,
  cumulative in billing order). Cost of goods sold holds the rest. Fully billed, the line holds zero;
  nothing billed, it holds all.
- **Every event posts the difference at its own date.** An invoice posts the change it makes to the
  balance, as a line of its own entry. A cost change to the issue (a backdated purchase, a price
  correction on the receipt, a provisional cost trued up, a landed-cost allocation) posts at the date
  the existing rules give it: Inventory Asset against Shipped Not Invoiced and Cost of Goods Sold,
  split by the balance at that date. Each invoice dated after that date then owes a correction of its
  recognised cost at its own date, derived, as an issue owes a correction at its date today. The
  existing refusal holds and widens: a change is refused whole if any date it would post at is on or
  before the closing date.
- **Why derived.** The invoice cannot store a cost it recognised and keep it right: the shipment's
  cost moves when late purchases arrive. Deriving the balance makes the account tie by construction,
  so a voided invoice, a return or a recost each leaves it exactly where its definition says.

### Provisional cost, counts and costing method

- **Shipping past on hand** is allowed with the existing below-zero warning, by place (a shipment is
  paperwork after the goods left, and the receipt may not be entered yet). The shipment line takes
  the provisional rate; the receipt that fills it trues it up at the receipt's position, and the
  true-up splits between Shipped Not Invoiced and Cost of Goods Sold by the derived balance at the
  receipt's date.
- **Counts.** A shipment takes `count_position` like every stock document: `before` by default, so a
  van or a bin counted at four and shipped from at five, with the shipment entered that night, needs
  `after`. Shipments re-derive counts through `inventory_counts.settle_in_command` as any document
  does. Picks are §4's concern.
- **FIFO.** The issue takes layers as any issue does; nothing else changes.
- **Cash basis** reads the invoice's Cost of Goods Sold line as it reads a stock-moving invoice's; the
  shipment moves only balance-sheet accounts.

### Billing a shipment

- **Exactly once.** An invoice line bills one shipment line, or issues stock itself; never both. An
  order line needing both gets two invoice lines (the printed invoice notes "Shipped 2026-10-20,
  shipment 1004" under the first). `shipment_billings` records shipment line, invoice and line
  identity, and quantity, live while that invoice revision is current and posted. The order line's
  billing root is consumed as today, so the exactly-once billing ledger is unchanged.
- **`sales-order invoice`.** With no selection, the default bills every shipped-not-invoiced unit of
  the order, oldest shipment first, and moves no stock. Only when the order has none does it fall back
  to [sales orders](sales-orders.md)' what-can-ship, issuing stock as today. Explicit selections bill
  shipped units of a line first, oldest shipment first, then issue the rest. `shipments` (ids) bills
  exactly those shipments.
- **`shipment invoice <shipment>`** bills one shipment: its lines at the order's prices, the order's
  tax and terms resolved on the invoice date as [sales orders](sales-orders.md) §4 does, plus any
  freight to bill (§5). It is `sales-order invoice --shipments` with one id.
- **Date.** An invoice may not bill a shipment dated after it (E_VALIDATION, naming the ship date):
  billing before shipping is the ordinary order-made invoice, or a prepayment.
- **Printed invoice.** A line billed from a shipment shows its ship date, ship method and tracking
  numbers; order quantities (ordered, previously invoiced, backordered) print as R101 built them.
- **Prepayments.** `apply_prepayment` works unchanged.
- **Invoice on ship.** `shipment post --invoice` posts the shipment and its invoice in one
  transaction, the anchor's prompt made explicit [E 66]. Company preference `invoice_when_shipping`
  (`ask` default, `always`, `never`) drives the browser's prompt and the agent hint; the command does
  only what it is told.

## 4. Picking

Picking is optional: a business that ships straight from the order skips it, and `shipment post`
proposes the same places a pick list would.

### Pick lists

`pick-list create` takes site (default the default site), orders (numbers), or `ready: true` for every
open order with something that can ship at the site, `complete_only` (orders that can ship whole
[E 84]), `limit`, picker (a user or free text), date (default today), memo and reason. It writes a
**pick list** (`pick_lists`: number, status `open`, `done` or `cancelled`, site, date, picker,
provenance) of **lines** (`pick_list_lines`: stable identity, order and order-line identity, item,
quantity in base units, unit, bin, suggested lot, sequence).

- **What to pick.** For each order line, to ship limited by what is available to that order at the
  site ([sales orders](sales-orders.md) §4's what-can-ship, by site per advanced inventory §2), less
  what other open pick lists and draft shipments already hold for that line ("on open picks"), so two
  pickers never take the same units for one order. Lines competing for the last units go in order
  date, then number; the others show short.
- **Where from.** The item's default bin when it holds the units, then other bins holding some, in
  pick order, splitting a line over bins; no bin last; nothing at the site holds them: a line for the
  short quantity with no bin, flagged short, and a note naming other sites holding the item.
- **Pick order.** Bins by full name, each level compared naturally ("A2" before "A10"), then item name,
  then order date [E 46]. One list may cover several orders (a wave); each line names its order, and
  the printed list groups by bin with each order's quantity.
- **Lots** are suggested earliest-expiring unexpired first, by bin, as an outgoing line's are (advanced
  inventory §4). **Serial numbers** are not bound: the line lists the numbers the books hold in that
  bin as hints, since the picker takes whichever unit is in front.

### Confirming what was picked

`pick-list enter` takes 1–500 entries, each: line, or item (and order when the list holds the item for
two orders), or `code`; bin, lot, serial number, quantity and mode (`add`, which a scan sends as 1, or
`set`). Entries append (`pick_entries`: line, mode, quantity, bin, lot, serial number, scanned code,
entered at, by and via, audit event) under their own idempotency keys, so two pickers, or a picker and
an agent, work one list at once, as count entries do.

- **Partial picks.** A line picked short stays short; `pick-list close` ends the list, and what was not
  picked stays to ship on the order. The result names other bins and sites holding the item.
- **Over-picking** beyond a line's quantity warns and is held as picked; the shipment takes at most
  what is still to ship, and its preview names the excess to put back.
- **Serial and lot capture.** A serial-tracked line takes one number per unit picked; a number the
  books hold elsewhere warns, naming where (the unit in hand is the fact), and the number travels to
  the shipment; one never on file follows advanced inventory question 3. A lot-tracked line records the
  lot picked; a different lot from the suggestion is accepted. Units whose number cannot be read are
  picked unassigned.
- **Moving nothing, unless staging.** By default an entry moves no stock: the units stay placed in
  their bins until the shipment posts, so a count of a bin with picked, unshipped units lists them in
  its create result and post preview ("3 Widget picked for sales order 1001, list P-4, not shipped"),
  and the counter ships, puts back or counts them knowingly. A site that tracks bins may name a
  **staging bin** (`inventory_sites.staging_bin_id`); then each entry writes placements moving the
  picked units from their bin to staging (source `pick`, netting to zero per item and number, dated
  the entry's date, `after` any count of the site recorded before the entry, else `before`), the
  shipment takes them from staging, and cancelling a list or setting a line lower moves them back. A
  count then finds every unit where it really is: Odoo's two-step route, built from the placements
  R104 already has.
- **Scanning.** The pick screen's Scan field (a fourth mode of `scan.js`): a bin label sets the current
  bin; an item adds 1 picked to the next unfilled line for it in that bin (`6*` sets 6); a serial label
  picks that unit on its item's line; a lot label sets the lot for the next scans; an item not on the
  list says so and adds nothing. It works at 390 pixels with a Bluetooth scanner.

## 5. Packing

### Draft shipments

`shipment create` writes a **draft** shipment from a pick list (its picked units, one shipment per
order), an order (what `shipment post` would propose), or explicit lines. A draft moves nothing and
posts nothing; it holds the lines, places and numbers that will ship, and its boxes. `shipment
cancel` ends a draft. Draft quantities count as "on open picks" (§4), so they are not picked twice.

### Boxes and weights

`shipment pack <shipment>` records packing on a draft or a shipped shipment: packages (box number from
1, package type text, measured weight, dimensions text, tracking number) and contents (shipment line,
quantity, serial numbers). Its inputs are a whole packing (`packages` with `contents`) or a move:
`unit` (item, serial number, lot or `code`), quantity and `box`, which puts that many units of that
line or that numbered unit into box N, making the box if new.

- **Packed** when every unit of every line is in a box; the shipment may post with units unboxed (a
  single carton needs no packing), and the packing slip then lists them unboxed.
- **Weight.** A box's computed weight is the sum of its contents' quantity times each item's
  `unit_weight` (added by R104 increment 6), plus its package's tare when given; the measured weight,
  when entered, is what prints and totals. A line whose item has no weight shows "no weight" and adds
  nothing; the shipment's total says how many units were unweighed. Weights are in the company's one
  weight unit, `company_info.weight_unit` (default `lb`; a label, never converted), shared with
  landed cost's weight basis.
- **Packing changes post nothing.** A revision that changes only packing, carrier, tracking, freight to
  bill or memo writes a new revision and leaves the stock and the entry as they were, on a shipped
  shipment too.

### Printing

Two printed kinds join the document renderer (`documents/model.py` `BUILDERS`; [printed
documents](printed-documents.md)), so they print, preview and batch wherever invoices do:

- **`pick-list`**: site, date, picker, then lines grouped by bin in pick order: bin, item, description,
  unit [E 47], quantity, order and customer, lot suggestion, serial hints, a blank Picked column with a
  tick box, short lines marked.
- **`packing-slip`**: the shipment as the customer sees it, without prices [M 317]: company and
  ship-to, order number, customer PO, ship date, ship method, tracking numbers, then per box its
  contents (item, description, unit, ordered, shipped now, backordered, serial numbers, lot and expiry)
  and weight, and unboxed lines last. One slip per shipment; `per_box: true` prints one page per box.
  Until R113's layout designer, this is the one packing-slip layout; the anchor's packing-slip
  template preference [M 669] arrives with R113.

## 6. Shipping, corrections, voids and returns

### Shipping

`shipment post` takes either a draft (number, expected version) or, to ship in one call, `order`
(number), `pick_list`, or `lines` (order line or item, quantity, bin, lot, serial numbers), plus date
(the ship date, default today), site, count position, ship method (default the order's, else the
customer's preferred), packages, tracking numbers, freight to bill (amount and item, default the
company's shipping item), memo, reason, `invoice` (§3) and the preview fingerprint of its dry run.

- **What it ships by default.** From an order with no lines: everything to ship that is available to
  the order at the site, as the pick would propose; from a pick list: what was picked; from a draft:
  its lines. Nothing to ship is E_VALIDATION with each line's to-ship and available quantities.
- **Partial shipments and backorders.** Whatever is not shipped stays to ship on the order, its
  Backordered [E 49]; no backorder document is made. A later shipment, pick or order-made invoice takes
  it; `close: true` on the order line cancels it, as R101 built.
- **Result.** Shipment number, each line's places and numbers, Shipped Not Invoiced posted, the order's
  remaining to ship and the exact next commands: `shipment invoice <n>` and, when the preference is
  `ask`, a note that the shipment is not yet billed.
- **Ship date** is the shipment's date: the date stock leaves, the date of its entry and the date
  invoices must not precede. The order's own ship date stays the promised date; the worksheet flags
  orders past it.

### Corrections and voids

| Change | Not invoiced | Invoiced (any part) |
|---|---|---|
| packing, carrier, tracking, freight to bill, memo | new revision, nothing reposts | same; freight already billed is fixed |
| quantity down on a line | reposts the line: the units go back to their places at the ship date | refused below the billed quantity (E_WORK_DEPENDENCY, naming the invoice) |
| quantity up, a line added | allowed while the order has that much to ship | same |
| place, lot or serial numbers | reposts the line; R104's count guard on moved places applies | allowed: billing reads quantity only |
| date | reposts the shipment | refused after an invoice billing it if the new date is later than that invoice |
| void (`shipment void`, reason) | reverses every movement and placement and the entry; the order's units are to ship again | refused with E_WORK_DEPENDENCY naming the invoices; void or correct them first |

All are refused on or before the closing date, as any stock document is. Update replaces the
document under its expected version and keeps every revision readable.

**Invoices billing shipments.** Voiding or deleting such an invoice, or removing such a line, returns
those units to shipped not invoiced: its recognised cost goes back to Shipped Not Invoiced by the
derived balance, no stock moves, and `release_to_order` does not apply to them (the result says so).
A price, discount or write-off credit memo changes nothing here.

### Customer returns

- **Units not yet invoiced.** `shipment return <shipment>` takes date, lines (shipment line, quantity,
  serial numbers or lot, place), `reopen` (default true: the units are to ship again; false cancels
  them on the order) and reason. It writes a customer-return movement into stock at its date, worth
  its endpoint share of the shipment line's cost as any return is, posting Dr Inventory Asset, Cr
  Shipped Not Invoiced; units go back where the shipment took them unless the line names a place, and
  serial numbers follow advanced inventory §4's return rules against the shipment. Returns are their
  own small documents (`shipment_returns` and lines) so the shipment's history stays as shipped, and
  each voids with a reason.
- **Units invoiced.** A credit memo returning stock, as today, naming the invoice line; its return
  defaults (place, lot, serial numbers, cost) come from the shipment line that invoice line billed, and
  it posts Dr Inventory Asset, Cr Cost of Goods Sold. The order stays invoiced, as [sales
  orders](sales-orders.md) §4 says of returns.
- A return of more than is out, or of a number the shipment did not take, is refused.

## 7. Agents

An agent ships, picks and packs from plain prompts with the same commands the browser uses, and every
billing path steers it:

- `invoice post`, `sales-order invoice` and `payment receive` previews list the customer's shipments
  not yet invoiced with the exact `shipment invoice <n>` command; an `invoice post` whose stocked lines
  match a shipment's says to bill the shipment instead, so goods are never issued twice. They advise
  and never refuse.
- `sales-order show` gives each line's shipped, shipped not invoiced, to ship and on open picks, and
  the order's fulfillment status.
- The command finder answers "ship", "pick", "pack", "box", "packing slip", "pick ticket",
  "tracking", "carrier", "fulfil", "backorder" and "delivery note".
- `barcode lookup` on a serial number or lot adds where the unit is on a pick list or shipment
  (`fulfillment`: pick list, draft or shipment, box), so "which box is WH-1001 in" is one call.

Draft closing-trial prompts, none naming the feature, each with what the agent should do:

| Prompt | Expected |
|---|---|
| "Ship Acme's order" | `shipment post --order <Acme's open order>`: what is available ships today, the rest stays backordered; no invoice; the result's next command noted back to the person |
| "What's left to pick?" | `report order-fulfillment --status to_pick,picking` (or `pick-list query --open`): orders and lines with quantities still to pick, by site |
| "WH-1001 went in box 2" | `barcode lookup --code WH-1001` finds the draft or shipment holding the heater; `shipment pack <n> --unit WH-1001 --box 2` |
| "Pick everything that can go out today at the warehouse" | `pick-list create --site Warehouse --ready` |
| "The truck took Acme's widgets this morning, tracking 1Z999" | `shipment post` from the picked list or draft with that tracking number, date today |
| "Bill Acme for last week's shipments" | `sales-order invoice <n>` (default bills shipped units) or `shipment invoice` per shipment |
| "Acme sent back one widget from Tuesday's shipment; we hadn't billed it" | `shipment return <n>` for 1, reopened to ship |

## 8. Surfaces

### Storage and migrations

One company migration per increment, numbered after main's head when it merges; each keeps every
existing row, index and trigger, and extends both demo seeds with any oracle move stated.

| Increment | Tables and columns |
|---|---|
| 1 Shipments and the books | `shipments` (number on the `shipment` sequence key, status `draft`, `shipped`, `cancelled` or `voided`, current revision, version), `shipment_revisions` (ship date, count position, site, sales order, customer, ship-to, ship method, freight to bill, memo, reason, transaction, provenance), `shipment_lines` (stable identity, position, order line identity, item, base quantity, unit and factor, bin, lot, description; serial numbers in placements), `shipment_billings`; `inventory_documents` admits `shipment`; `company_info.invoice_when_shipping`; the account and its role, created on first use |
| 2 Returns | `shipment_returns`, `shipment_return_lines`; `inventory_documents` admits `shipment_return` |
| 3 Picking | `pick_lists`, `pick_list_lines`, `pick_entries` (append-only); `inventory_placements.source` admits `pick`; `inventory_sites.staging_bin_id` |
| 4 Packing | `shipment_packages`, `shipment_package_contents` (per revision); `company_info.weight_unit` unless increment 6 of R104 added it |

Shipment, return, pick and packing rows are never updated or deleted; revisions append, as
transfers' do. The placement triggers of R104 apply unchanged: a shipment's placements sum to its
movements, a pick's net to zero. Each increment adds a permission layer (`shipments-v1`,
`shipment-returns-v1`, `picking-v1`, `packing-v1`) chained on the tip at merge.

### Commands

| Command | Does |
|---|---|
| `shipment create`, `pack`, `post`, `update`, `cancel`, `void`, `show`, `query`, `history` | §5, §6 |
| `shipment invoice`; `sales-order invoice` gains `shipments` and the shipped-first default | §3 |
| `shipment return`; `shipment-return show`, `query`, `void` | §6 |
| `pick-list create`, `enter`, `close`, `cancel`, `show`, `query` | §4 |
| `sales-order show`, `billing`, `query` | gain shipped, shipped not invoiced, to ship, on open picks, fulfillment status |
| `inventory-site update` | gains `staging_bin` |
| `report order-fulfillment` | the anchor's Sales Order Fulfillment Worksheet [E 84]: open orders with fulfillment status, to ship, can ship now by site, shippable amount, picked, packed, shipped not invoiced amount, promised ship date and lateness; sorts `date`, `shippable_amount`, `customer`; filters site, customer, status, `complete_only`, `newly_shippable` (became shippable since a date, as receipts arrive [E 36]) |
| `report shipped-not-invoiced` | as-of date, by customer, order, shipment and line: quantity and cost waiting; refuses, as inventory valuation does, if its total differs from the account |
| `report shipments` | date range, customer, site, ship method: shipments with boxes, weights and tracking numbers |
| printing | kinds `pick-list` and `packing-slip` (§5) |

All are registry commands, so the CLI, HTTP, MCP (`bookflow_run`) and the browser reach them
identically, each with a dry run, idempotency on writes and a worked example in its help.

### Permissions

| Commands | Role | Authority |
|---|---|---|
| `pick-list` writes; `shipment create`, `pack`, `cancel` | standard | `item` (stock records that post nothing, as transfers and count entries) and `customer-work` read |
| `shipment post`, `update`, `void`, `return`; `shipment-return void` | standard | `ledger.post` and `customer-work`, both checked |
| `shipment invoice`, `sales-order invoice` | standard | as `sales-order invoice` today |
| `shipment post --invoice` | standard | both of the above |
| reads, `report order-fulfillment`, `shipments`, `shipped-not-invoiced`, printing | member | `item` or `customer-work` read; reports |
| `company_info.invoice_when_shipping`, `weight_unit` | admin | as other company preferences |

An agent with the standard role can do all of it; nothing here is reserved to a person.

### Browser

- **Fulfillment** joins the Inventory menu: the worksheet, opening on orders that can ship now, with
  tabs To pick, Picking, To pack, Shipped not invoiced; sort and filter as the report; select orders to
  Create pick list, Print pick lists, Print packing slips, Ship, or Create invoices (one per order)
  [E 84].
- **The pick screen** is built for a phone like the count screen: lines grouped by bin in pick order,
  the Scan field pinned on top, the current bin shown, a keypad for typed quantities, short lines
  marked, Close list at the foot.
- **The pack screen**: the shipment's lines with a Box column, boxes as cards with computed and
  measured weight, tracking number per box, Scan with a Box field (a scan puts the unit in the current
  box), Print packing slip, Ship.
- **The shipment page**: header, lines with places and numbers, boxes, the invoices billing it, its
  returns, history; actions Pack, Ship, Invoice, Return, Void, Print. After Ship, when the preference is
  `ask`, a prompt offers Create invoice now [E 66].
- **The order form** gains Shipped and To ship columns beside Invoiced and Backordered, its fulfillment
  status, and Ship and Create pick list buttons; the Create invoice dialog shows which quantities come
  from which shipments. The customer page shows shipped-not-invoiced amount at order prices. Every
  screen works at 1440 and 390 pixels.

### An agent's first time

"Ship Acme's order":

```
bookflow shipment post --order 1001 --reason "Shipped Acme's order" --json
```

The dry run shows each line's quantity, site, bin, lot or serial numbers, the cost to post to Shipped
Not Invoiced, and what stays backordered; the post returns shipment 1004 and `shipment invoice 1004`
as the next command. "WH-1001 went in box 2":

```
bookflow barcode lookup --code WH-1001 --json
bookflow shipment pack 1004 --expected-version 1 --unit WH-1001 --box 2 --reason "Heater in box 2" --json
```

The lookup says WH-1001 is on shipment 1004, line 1, unboxed; the pack returns box 2's contents and
weight and posts nothing.

## 9. Measures and acceptance criteria

What R181 is for: a warehouse ships before it bills, and the books stay right when the invoice comes
later. Each criterion below is tested in the increment that names it, and the closing trial runs the
whole.

1. **Ship before bill.** An order ships today with no invoice: on hand, available and committed fall at
   the ship date; the order reads shipped, not invoiced; nothing reaches income or receivables.
2. **The invoice later equals the invoice at once.** For any sequence of public commands, a company
   that ships on day S and bills on day B ends with the same profit and loss for every period, the
   same receivables and the same stock quantities and value on every date from B on, as a twin that
   billed and shipped on day B by an order-made invoice; between S and B the only differences are the
   stock (less, by the shipped units) and Shipped Not Invoiced (more, by their cost). Checked on
   generated sequences with backdated purchases, provisional stock, landed cost, counts and FIFO.
3. **The account ties.** `report shipped-not-invoiced` equals the account on every date and is zero for
   a line once fully billed or returned; posted value equals the replay's value; placements equal the
   replay's quantity (the three properties of advanced inventory §3, under both methods).
4. **Nothing is issued twice.** Billing a shipment moves no stock; retried posts, simultaneous
   invoices and a shipment racing an order-made invoice for the last units each issue and bill every
   unit once.
5. **Corrections land right.** Every row of §6's correction table, and voiding an invoice that billed a
   shipment, leaves stock, Shipped Not Invoiced and Cost of Goods Sold where the derived balance says.
6. **The floor works it.** A person picks a wave of three orders on a phone by scanning, short-picks a
   line, packs two boxes and ships; the order screens and the worksheet read right at every step.

**Measure** (for the roadmap): closing-trial prompts done correctly on the first attempt, out of the
prompts in §7, read from the trial record.

## 10. Build increments

Ordered by what a warehouse needs first: shipping apart from billing with books that stay right, then
returns, then the floor work, picking and packing, then the worksheet that ties them. Every increment
keeps a company that never ships separately reading exactly as before.

1. **Shipments and the books.** Migration 1; Shipped Not Invoiced and its tie; `shipment post` (direct,
   from an order or lines), `update`, `void`, `show`, `query`, `history`; issue movements with places,
   numbers and count position; the derived balance and its postings at invoices and cost changes;
   order figures and committed as to ship; `shipment invoice`, `sales-order invoice` shipped-first and
   `shipments`; invoice void and update effects; steering notes; `report shipped-not-invoiced`; the
   shipment page and order columns; demo: an order shipped in part and billed a week later. *Done
   when* 10 ordered, 6 shipped on the 20th and billed on the 27th leaves committed 4 from the 20th, the
   cost of 6 in Shipped Not Invoiced from the 20th to the 27th and in Cost of Goods Sold from the 27th;
   a purchase backdated before the 20th recosts the shipment at the 20th and the invoice at the 27th;
   voiding the invoice moves the cost back and the order reads shipped not invoiced; voiding a billed
   shipment is refused naming the invoice; criteria 1–4 hold. *Tests:* hand-worked postings for each
   event; the twin property on generated sequences under average and FIFO; provisional stock filled
   after shipping; landed cost allocated between ship and bill; a recost refused when an invoice date
   it reaches is closed; retries and races; migration up and down on a populated company.
2. **Returns and corrections.** Migration 2; `shipment return` and its void; the correction table;
   credit-memo return defaults from the billing shipment line. *Done when* one of 6 shipped and unbilled
   comes back to its bin and is to ship again, or cancelled when `reopen` is false, and the account
   falls by its share; a returned serial number is back in stock; an invoiced unit returned by credit
   memo leaves the order invoiced; every correction row behaves as written. *Tests:* each row against
   hand-worked figures; a return after a recost; returns of numbers the shipment did not take refused.
3. **Picking.** Migration 3; pick lists, suggestions and pick order, entries, partial picks, serial and
   lot capture, the staging bin, count interaction, the pick screen with Scan, the printed pick list;
   `shipment post --pick-list`; demo: a wave of two orders over bins A1–A3. *Done when* two orders
   needing a lot-tracked sealant and a serial heater give one list in A1, A2, A3 order with the
   earliest-expiring lot suggested; scanning the bin label then the item three times picks 3; a line
   picked short ships short and the rest stays backordered; two pickers entering at once both land; a
   second list never re-picks units on the first; with a staging bin, a count of A1 after picking finds
   the picked units gone from A1 and present in staging, and without one the count lists them.
   *Tests:* pick order with natural bin names; competition for the last units; serial warnings;
   staging placements netting to zero and reversing on cancel; a browser test scanning at 1440 and 390.
4. **Packing and shipping facts.** Migration 4; drafts, `shipment pack` (whole and by unit or code),
   weights, tracking, freight to bill, the packing slip (whole and per box), invoice on ship and the
   preference; the pack screen. *Done when* "WH-1001 went in box 2" moves exactly that heater; a box's
   weight is its contents' weight, or the measured one; packing a shipped shipment reposts nothing; the
   packing slip prints without prices, with backorders and serial numbers; `shipment post --invoice`
   writes both documents in one transaction and a retry writes neither again. *Tests:* packing
   revisions leaving the entry untouched; weights with an unweighed item; the slip on a multi-page
   shipment.
5. **The fulfillment worksheet.** `report order-fulfillment`, its sorts and filters, `newly_shippable`,
   batch actions in the browser, `report shipments`. *Done when* the worksheet's can-ship-now figures
   match what `shipment post` would ship for every order, a receipt makes a short order newly
   shippable, and batch printing and invoicing make one document per order. *Tests:* the worksheet
   against hand-worked figures by site; worksheet time on the demo and on 1,000 open order lines,
   measured and recorded.

**Closing trial.** A fresh agent with only Bookflow's help and MCP, given §7's prompts in a company
with an open order for a widget, a lot-tracked sealant and serial water heaters WH-1001 and WH-1002,
does each correctly on its first attempt. Checked afterwards as the person: the order's shipped, to
ship and invoiced figures; the heater's box; Shipped Not Invoiced zero once everything shipped is
billed; profit and loss equal to the twin of criterion 2. One more run starts on a phone, where a
person picks and packs by scanning, and an agent ships and bills from the same prompts.

## 11. Open questions

Each default stands if nobody decides. The first two are the person's to decide.

- **K1. Carrier integration** (rates, labels, pickups and tracking through UPS, FedEx or USPS
  accounts), which would send company and customer data to outside services and may cost money.
  *Default:* none; carriers, services and tracking numbers are data only, printed, never fetched or
  linked. A later item could add it per carrier on the person's word.
- **K2. Where the cost of shipped, unbilled goods sits.** *Default:* in Shipped Not Invoiced until the
  invoice moves it to cost of goods sold, so cost meets income and the profit and loss reads as the
  anchor's would; a month-end with unbilled shipments shows the asset and `report shipped-not-invoiced`
  lists it, and no income is accrued for it. The alternative posts cost of goods sold at the ship
  date, which needs no new account but shows cost in a month whose income bills in the next. The
  choice shapes every company's postings once shipments exist, so changing it later means reposting.

For the builder, defaults standing unless review finds otherwise:

1. **Shipments without a sales order, or from work orders.** *Default:* not in these increments; a
   warehouse ships orders, and a work order's parts go out on its invoice as today.
2. **Holding an invoice until shipping.** *Default:* none; an order-made invoice still issues stock
   itself, so a business that bills first and ships later keeps today's path.
3. **A serial number picked that the books hold elsewhere.** *Default:* warn and ship it from where the
   books hold it, as a sale does (advanced inventory §4); a count or transfer puts the books right.
4. **Picked units by default.** *Default:* they stay in their bins until shipped unless the site names
   a staging bin; the alternative moves them on every pick, which needs a staging bin at every site.
5. **Tracking links.** *Default:* the tracking number prints as text; a per-ship-method link template
   waits on K1.

## Decided (2026-10-26)

- K1 carrier integration: none. Carrier and tracking are recorded and printed as text only (Kabe, gate gba01e0).
- K2 cost of shipped-not-invoiced goods: held in Shipped Not Invoiced until the invoice, so cost and income land in the same period (Kabe, gate gae9f98).
