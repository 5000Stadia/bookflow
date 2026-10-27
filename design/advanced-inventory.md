# Advanced inventory, barcodes and counts

This contract covers two roadmap items: advanced inventory (R104) — sites and bins, transfers, serial
and lot numbers, physical counts, a choice of costing method, and allocating a later freight bill to
received stock — and barcodes and cycle counts (R139). It fills what blueprint §11.7 and §11.17 leave
to "the inventory passes": sites and bins, serial and lot instances, landed cost, costing methods and
stock transfers. Pick, pack and ship, which [sales orders](sales-orders.md) left to these items, is in
neither; §10 asks where it belongs.

**What exists.** One site. Weighted-average cost by replay of an append-only movement ledger
(`company/inventory_costing.py`, `inventory_schema.py`): receipt, issue, value, vendor_return, recost
and reversal, plus build receipts and issues (`assembly_builds.py`). A backdated change owes each
later issue a correction at that issue's date and is refused whole if any such date is closed; a
bill priced differently from its receipt is walked at the receipt's position the same way
(`bills.py`, `receipt_billing.py`). Stock may go below zero at a provisional cost, trued up at the
receipt that fills it (`inventory_costing.py`). `inventory adjust` moves one item and requires a
value with an increase. An item receipt spreads its `shipping` over received quantities
(`receiving.py`). Items carry an optional, searchable `barcode` that nothing resolves. One
inventory-asset account per company (`uq_accounts_system_role`); both stock reports refuse to print
unless item values total it on the as-of date (`inventory_reports._check_tie`). Committed and
available come from `commitments.py`.

It amends five rules: (1) every quantity a movement moves is also *placed* — at a site, and at a bin,
lot or serial number where used — in a quantity-only ledger with an explicit unassigned bucket,
enforced by the database and tied to the replay's quantity; (2) `inventory adjust` takes many lines,
and an increase with no stated value is valued at the running average instead of refused; (3)
replay gains a dated costing-method timeline, a `found` kind, a `landed` kind recognised forward as a
shortfall true-up is, count differences it derives, and a count position in its order; (4) an `item`
field resolves a barcode after id and name; (5) every stock line may name its site, bin, lot and
serial numbers, and whether it came before or after that day's count.

Sources for the anchor: **[M]** the supplied Missing Manual (2016), printed page numbers; **[E]** the
anchor's Enterprise 24.0 in-depth guide; **[R]** its Enterprise 16.0 reports guide. The guides name
features and rarely their mechanics; where they are silent this document says so, and the rule
given is Bookflow's.

## 1. The anchor's behaviour, and where Bookflow differs

**Matched.**

- **Sites.** Quantity and value are tracked at several locations — warehouses, staging areas inside
  one, service trucks, consignment, goods in transit — each showing on hand, on sales order, on
  purchase order and a reorder point, with stock transferred between them [E 45]. An order is
  filled from a chosen warehouse or split between warehouses [E 36]. Reports: Quantity on Hand by
  Site, Inventory Valuation Summary by Site, Inventory Stock Status by Site, Pending Builds by Site,
  Inventory Sales by Site [R 25].
- **Bins.** Stock is located to a bin in a warehouse or truck in a three-level hierarchy (pallet,
  row, section), and item receipts sort by location [E 46]; Items by Bin Location [R 26].
- **Serial and lot numbers.** A serial number per unit matches a customer to the unit for warranty
  [E 45]; lots track a batch for recalls [E 46]; expiration dates can be printed on sales orders,
  invoices and other customer documents [E 41]. Reports: Transaction List by Serial/Lot Number
  (purchases, adjustments, transfers, builds and sales of a lot, for recall), Serial/Lot Numbers in
  Stock, Quantity on Hand by Serial/Lot Number [R 26]; Inventory Stock Status by Lot Numbers [E 43].
- **Costing.** Average cost, or FIFO instead, switchable at any time [E 39], chosen in the
  company preferences' Advanced Inventory settings [M 660], so one method for the company. Without
  that edition FIFO is a hand calculation entered as a value adjustment [M 549–550], and the same
  pages advise choosing a method and keeping it [M 550].
- **Landed cost.** Freight, duty, insurance and other charges are allocated to items by quantity,
  value, weight or volume; landed-cost accounts are cost-of-goods-sold or other-current-asset
  accounts; old item bills in closed periods can still receive landed cost; bills print with or
  without it [E 39].
- **Counts.** The Physical Inventory Worksheet lists each item with on hand and a blank count
  column [M 544; R 25, 44]. Nothing freezes stock while counting: count at quiet times and hold
  deliveries until done [M 545]. Variances go in through Adjust Quantity/Value on Hand: quantity,
  value or both, New Quantity or Qty Difference, many items in one adjustment with one adjustment
  account, quantity changes valued at average cost [M 546–549]. Cycle counts run while the warehouse
  works, from one or several locations, and discrepancies are found and adjusted [E 41].
- **Barcodes.** Scanners receive, pick and cycle-count; items use existing codes or generated ones;
  labels can carry prices [E 39]. Without the Enterprise edition, scanning is a third-party add-on
  [M 541].

**Deliberate differences.**

- **One cost per item across all sites.** The guides report value by site [R 25; E 45] without
  saying whether cost differs by site. Bookflow keeps one average, or one FIFO queue, per item: a
  transfer moves quantity, never value, and posts nothing; a site's value is its share of the
  item's (§2). Per-site cost would turn every van restock into a costed issue feeding another site's
  receipt, the build cascade again, with the van and warehouse trading stock back and forth.
- **Where units are, and which numbers they carry, is a quantity-only ledger beside the cost.** Lot
  and serial numbers never decide cost. A unit whose number is not known is held as unassigned and
  never guessed. The guides do not say how tracking is chosen; Bookflow chooses it per item.
- **A count is a recorded input.** Its counted quantities are kept, and its difference is derived at
  the count's place in the day, so paperwork entered after the count lands on the right side of it
  (§6). The anchor instead holds deliveries while counting [M 545]. Counts can be blind, and cycle
  counting has ABC classes and rotation, which the guide does not describe [E 41].
- **Landed cost is recognised forward.** The charge is walked at its receipt and every correction it
  owes is dated at the allocation, in an open period (§5); this is the anchor's allowance for old
  bills in closed periods [E 39], made exact. Charges wait in one clearing account rather than in an
  account of the bookkeeper's choosing.
- **A costing-method switch is a dated policy event** after the closing date, made by a person; it
  revalues nothing (§3).
- **Transfers are instant**; nothing is held in transit (question 4).
- **Scanning** is a browser field fed by any scanner in keyboard mode; no device app or camera.

**Outside these items:** pick, pack and ship and its fulfillment dashboard [E 36–38]; barcode label
printing (R113's label profiles); e-commerce sync [E 38]; item images [E 36]; lot or expiry data
encoded inside GS1 barcodes.

## 2. Sites and bins

### The lists

- **`inventory_sites`**: the list columns, `name` (unique), `description`, `address`, `is_default`
  (exactly one active default) and `tracks_bins` (default false). The migration creates "Main" as
  the default; renaming it is an ordinary update. A site may be a warehouse, a van, a consignment
  location or goods on the water [E 45]; the stock at any site is the company's own, and stock owned
  by others is not tracked. Deactivating a site that holds any quantity today is refused with
  E_RECORD_IN_USE, naming the items; the default site cannot be deactivated.
- **`inventory_bins`**: the list columns, `site_id`, `name`, `parent_id` (same site), `full_name`
  ("A:3:Top", unique per site), depth 1–3 [E 46], an optional `barcode` unique among active bins,
  and `description`. Bins are used only at a site that tracks them; deactivating a bin that holds
  stock is refused.
- **`item_site_settings`**: one versioned row per item and site with reorder minimum and maximum
  and a default bin, set with `item update --site-settings`.

Site and bin fields appear on forms only when the company has more than one active site, or the
site tracks bins. A one-site company sees nothing change.

### Where stock is: placements

`inventory_movements` stays the ledger of quantity and value per item. Where the units are and which
lot or serial number they carry is a second fact with another shape — one sale line may take from
two lots, and a transfer moves units with no value — so it has its own append-only ledger,
**`inventory_placements`**: id, item, site, bin (null), lot or serial number (both null on a tracked
item is the **unassigned** bucket), signed quantity (nonzero; one base unit either way for a serial
number), effective date, count position (§6), `sequence`, source (`movement`, `transfer`,
`assignment` or `count`, with its id and line), `reverses_placement_id`, and provenance.

- **The quantity tie.** A movement's placements sum to its quantity. A transfer's placements net to
  zero per item, lot and serial number; an assignment's per item and place, since it only changes
  numbers; a count's own per item, since they only move stock between places. So on every date an
  item's placements sum to the quantity the replay holds. Every reading by site, bin, lot or
  serial number asserts this **quantity tie** beside the asset tie and refuses with E_INTERNAL,
  naming the item and the gap.
- **Enforced by the database.** A movement's placements are written before it, under a deferred
  foreign key; a trigger refuses the movement unless they sum to its quantity, and refuses a
  placement added to a movement already written. A transfer, assignment or count revision is checked
  the same way when its header row is written. Serial numbers are unique per item, and a trigger
  refuses any placement that would take a serial number's running quantity, in replay order, outside
  −1 to 1 (−1 is a unit sold before its receipt is recorded, §4). Placements are never updated or
  deleted.
- **Order.** A movement's placements carry its date, count position and sequence; transfers,
  assignments and counts take sequence numbers from the same company counter
  (`inventory.next_sequence` reads both tables).
- **Writers.** The three movement writers that exist — the document seam
  `inventory_effects.write_movements`, adjustments and builds — write placements from the place each
  `Entry` carries; transfers, assignments and counts write their own. No document writes them itself.
- **Migration.** One unassigned placement at the default site for every existing movement that moves
  quantity.

**A line's place.** Site: the line's `site`, else the document's `site`, else the source line's (an
invoice made from an order line, a receipt against a purchase-order line), else the default site.
Bin: the line's `bin`, else the item's default bin at that site when the site tracks bins, else none
("no bin" is a place). Lots and serial numbers: §4. A customer return goes back where its issue took
from unless the line names another place. Every result that moves stock states each stocked line's
place.

**Below zero.** A site, bin, lot or unassigned bucket may go below zero; the write is saved with a
warning that says where else the item is ("Van 1 now shows −2 Widget; Warehouse holds 40"). Costing
still reads only the item's total, so the provisional-cost and true-up rules do not change. Serial
numbers follow §4.

### Transfers

The anchor's transfer between locations is the document **`inventory-transfer`**. Tables:
`inventory_transfers` (id, number, status `posted` or `voided`, current revision, version, created
and updated), `inventory_transfer_revisions` (date, count position, from site, to site, memo, reason,
provenance) and `inventory_transfer_lines` (stable line identity, position, item, quantity in base
units, unit and factor, from bin, to bin, lot, description; serial numbers live in the placements).

- **Post** takes date (default today), an optional number (sequence key `inventory_transfer`), from
  and to sites (the same site when moving between its bins), 1–200 lines (item, quantity, unit, from
  bin, to bin, lot, serial numbers), memo and reason. Each line writes −q at its from place and +q at
  its to place, same lot or serial numbers, on the transfer's date. There is no journal entry, no
  movement and no value: cost and the inventory asset are untouched.
- **Instant.** Stock leaves and arrives on the transfer's date. Dispatch, stock in transit and
  partial receipt are a later option (question 4).
- **Update** replaces the document under its expected version: the old revision's placements are
  reversed and the new ones written in one transaction. **Void**, with a reason, reverses them and is
  final. **History** lists every revision.
- A transfer dated on or before the closing date, or an update or void reaching such a date, is
  refused: it could change what a posted count found (§6).
- A serial number that is not at the from place on that date is refused; taking the from place
  below zero warns (question 5).

### Figures by site

- **On hand** at a site, bin, lot, serial number or unassigned bucket is the sum of its placements
  dated by the as-of date.
- **Committed.** Sales-order and work-order lines gain an optional `site`. An open stocked line
  commits at its site, or unassigned when it names none; a pending build commits its components at
  its site (builds gain `site`). Company figures stay as they are: the sites plus unassigned.
- **Available** at a site is on hand there less committed there, under the preferences of
  [sales orders](sales-orders.md) §3; an order-made invoice's what-can-ship default reads the
  order line's site when it names one.
- **On order.** Purchase-order lines gain `site` (default the order's `site`, else the default site);
  `receiving.order_remaining` returns it, and an item receipt against the line defaults to it.
- **Reorder** by site compares available there with the site's minimum; the company flag keeps the
  item's own reorder points.
- **Value** by site: the item's value divided by `endpoint_share` over its sites' quantities, sites
  in name order, so the sites sum exactly to the item and Valuation by Site adds up to the account.
  An item holding nothing, or less, shows its whole value at the default site.

Reports: `report stock-status --site` (Inventory Stock Status by Site); `report inventory-valuation`
with `site` or `by: site` (Valuation Summary by Site); `report quantity-by-site` (Quantity on Hand by
Site, item by site, with unassigned committed); `report sales-by-item --site` (Inventory Sales by
Site, by the site the stock left); `assembly list --site` (Pending Builds by Site).

## 3. Costing method

### The timeline

`costing_methods` is append-only: id, method (`average` or `fifo`), `effective_from`, reason and
provenance. No row means average from the beginning, which is every company today. The method on a
date is the row with the latest `effective_from` on or before it, for every stocked item, as the
anchor's one company setting is [M 660].

`costing-method set --method fifo --from 2027-01-01` is a person's act (the people-only list, R168).
`from` must be after the closing date and after the last row's date. The preview gives, per item and
in total, the change in cost of goods sold and in stock value from that date to today, and the
correction entries it will post. `costing-method withdraw` removes a switch whose date has not
come. The help warns that a method is meant to be kept and changed only on the accountant's advice
[M 550].

### FIFO by replay

FIFO is derived from the same immutable movements in the same order. Corrections, true-ups, returns
and reversals work exactly as they do today; only the state differs: a queue of **layers**, oldest
first, each a small pool of remaining units and value, instead of one pool.

| Movement | Average (today) | FIFO |
|---|---|---|
| bought receipt, with its receipt-targeted price corrections | fills open shortfalls first, then adds the rest to the pool | fills open shortfalls first; the rest is a new layer |
| customer return | worth its endpoint share of the issue | the same worth, as a new layer |
| `found` (§6) | round(V·q/Q); at or below zero the last average, else the captured purchase cost, else zero | round(V·q/Q) over all layers, the same fallbacks; after shortfalls, a new layer |
| issue | takes round(V·q/Q), or all of V when it empties the stock | takes units oldest layer first; each gives round(W·u/U), or all of W when emptied |
| `value`, a hand write-up or write-down | added to V | spread over the layers by remaining units, cumulative endpoint shares in layer order |
| `landed` (§5) | added to the pool at its receipt's position | added to its receipt's layer at the receipt's position |
| vendor return | Q falls by its quantity and V by the credit | takes the layer of the receipt it names, else the oldest layers; the credit leaves, and any difference from those units' worth stays in stock, spread like `value` (question 1); refused, as today, when nothing is left to carry it |
| shortfall rate | the running average, else the last, else the captured purchase cost, else zero | the newest layer's unit cost, else the last seen, then the same fallbacks |
| recost, reversal | outputs and retirements, never inputs | unchanged |

Under both methods a `landed` input is recognised at its own date: what was consumed before then
owes forward true-ups (§5). A count's differences are issues and found units like any other (§6).
Emptying a layer takes all of its value, so the zero-residual rule holds per layer, and every
division is exact integer arithmetic, rounded half-even once, in base units. A build's component
issues take FIFO cost and its assembly receipt becomes a layer; partner corrections work as today.
A vendor credit's item line may name the receipt line it sends back; under average the link is
recorded and changes nothing.

**Switching.** Where the walk reaches the switch date, before any movement dated on or after it, the
item's on-hand quantity and value become one opening FIFO layer; switching back, the layers merge
and continue as the average. Nothing is revalued, the switch itself posts nothing, and history
before it replays unchanged. Open shortfalls carry over, and the receipt that fills one trues it up
from its own cost whichever method is in force. Movements dated on or after the switch are costed by
the new method; any already posted get their own corrections at their own dates, all after the
closing date.

**What it changes.** Cost of goods sold from the switch date on, by dated corrections. Stock value is
the sum of the layers, and the asset tie holds because every value change is still a movement on an
asset posting line. `average_cost` in reports keeps its meaning, value over quantity on hand. `report
inventory-layers` lists each item's layers on a date (source document and date, remaining quantity,
value and unit cost; one row under average). `report inventory-valuation-detail` (the anchor's
Inventory Valuation Detail [R 24]) lists the movements with running quantity and value. LIFO is not
offered; the anchor has none [M 549–550].

**Properties.** Under both methods and any sequence of public commands: posted value equals the
replay's value; placements equal the replay's quantity; and no write dated after the closing date
produces a row dated on or before it. A derived snapshot of each item's layers at the closing date
is always valid, since nothing may be dated on or before it, and may later be cached to shorten
replay once its cost is measured; it is never the authority.

## 4. Serial and lot numbers

Inventory parts and assemblies gain `tracking`: `none` (default), `serial` or `lot`. Two tables hold
the numbers: **`lots`** (item, number unique per item, `expiry_date`, memo) and **`serial_numbers`**
(item, number unique per item, memo). A number is created by the first line or assignment that names
it; where it is comes only from placements.

**The unassigned bucket.** On a tracked item, units a line does not number are placed unassigned: an
agent that does not know a water heater's serial number leaves it out and never makes one up.
Turning tracking on leaves the stock already on hand unassigned. **`inventory assign`** numbers units
later and posts nothing: it moves units at a place between unassigned and lots or serial numbers,
either units on hand on a date, or the units one document line moved (which water heater a sale
took), dated with that line. It is refused on or before the closing date, as transfers are.
Unassigned units of tracked items show on the item page and among the Overview's items needing
attention.

**Serial numbers.** Whole base units; a line lists up to its quantity of distinct numbers, and the
rest are unassigned.

- In (receipt, found, customer return, transfer in): naming a number already in stock is refused.
- Out (sale, vendor return, transfer out, build component): a numbered unit leaves from where the
  books hold it, and the result says so when that differs from the line's place. A number that was
  in stock and has left is refused, naming the document that took it. A number never on file is
  accepted with a warning and shows −1 until a receipt or an assignment brings it in (question 3).
- A customer return may give back only numbers its issue took; units its issue took unassigned come
  back unassigned.

**Lots.** An incoming line names its `lot`, and `lot_expiry` when the lot is new (`lot update`
changes it later). An outgoing line, a transfer's included, names its lot or leaves the units
unassigned; its preview suggests the earliest-expiring unexpired lot in stock there (question 2).
Naming an expired lot warns.

**Provisional stock and tracking.** Tracking never blocks a sale. Stock sold before it is received
keeps its provisional cost on the item's total (§3); by place it shows as negative unassigned units,
or as a serial number at −1 when the line named one. The receipt that arrives places what it names:
a serial number at −1 comes back to 0, and unnamed units bring the unassigned bucket back up. A lot or
the unassigned bucket below zero at a place warns, as a site does.

**Builds, counts and printing.** Components follow the same rules; a tracked assembly's receipt may
name its own lot or serial numbers, and the build links them for tracing. Counts count tracked items
by lot or serial number, and units whose number cannot be read as unassigned (§6). Printed sales
orders, invoices, sales receipts and credit memos show serial numbers, lot and expiry on their lines
[E 41].

**Reports.** `report serial-lot-stock` (Serial/Lot Numbers in Stock, Quantity on Hand by Serial/Lot
Number, Stock Status by Lot Numbers): item, number or unassigned, site, bin, quantity and expiry,
filtered by item, site or `expiring_by`. `report serial-lot-history` (Transaction List by Serial/Lot
Number): every placement of one number in date order, assignments included, with document, customer
or vendor, place, in, out and balance. `report lot-trace`: that history plus, through builds, what
the lot went into and who bought it, and what went into it, for a recall. `serial-number show` and
`lot show` say where a number is now.

## 5. Landed cost

**Charges and the clearing account.** Charges post to **Landed Cost Clearing**, an other-current-asset
account with system role `landed_cost_clearing`, created on first use. The role joins
`journals.OWNED_SYSTEM_ROLES`, so the journal editor is refused on it, and the expense-line roles of
bills, checks, card charges and vendor credits (`bills.LINE_ROLES`), so charges are the only way in:
an expense line on it is a charge, and a vendor credit's line is a freight credit, a negative charge.
A charge line's unallocated amount is its amount less its live allocation lines. `landed-cost
charges` lists unallocated charges by vendor, document, date and amount, ties their total to the
account's balance on every date, and refuses as the stock reports do when they differ. A charge that
should not be capitalised is allocated to an expense or cost-of-goods account instead of receipts,
which covers the anchor's cost-of-goods landed-cost accounts [E 39].

**Allocation records.** `landed-cost post` takes charges (line and amount, default all unallocated),
targets (item receipts and the item lines of bills, checks and card charges that brought stock in,
whole or by line), basis, date, memo and reason. It writes a `landed_cost_allocations` document
(number, date, status, reason, provenance) and immutable `landed_cost_allocation_lines` (charge line,
target receipt movement, basis, measure, signed amount). `landed-cost void` writes reversing lines and
reverses its entries; nothing is updated or deleted. A charge may be allocated in parts across
documents, a retry under the same key returns the first answer, and a freight credit allocates
negative amounts but may not take a receipt's landed total below zero.

- **Bases.** `quantity`: base units. `value`: the receipt line's own value, price corrections
  included, before any landed cost, so the order of allocations never changes the shares. `weight`
  and `volume`: base quantity times the item's new `unit_weight` or `unit_volume`, overridable per
  target and refused naming any target without one. `amounts`: given per target, summing exactly to
  the charges.
- **Exact shares.** Cumulative endpoint shares in target order, so the targets sum exactly to the
  charges.
- **Receiving-time shipping.** An item receipt's `shipping` goes through the same allocator (basis
  chosen on the receipt, quantity by default as today) and into the same records, as a charge that
  never passes through the clearing account. Dated with the receipt, it owes nothing forward and stays
  inside the receipt's own posting and movement, as today. The migration records allocation lines
  for existing receipts' stored shipping shares, so every landed amount reads from one place.

**The forward true-up.** Each other allocation line writes a `landed` movement: value only, linked to
its receipt movement (`landed_on_movement_id`), dated at the allocation date, debiting Inventory
Asset and crediting Landed Cost Clearing (the reverse for a credit). Replay walks it at its receipt's
position, as if the charge had been on the receipt from the start, but recognises it at its own
date, as a shortfall true-up is recognised at the receipt that fills it:

- a movement whose value replay decides (an issue with its shortfall true-ups, a customer return, a
  found unit) dated before the allocation keeps its own cost and owes a **forward true-up**: a
  `recost` naming it in `corrects_movement_id` and the `landed` movement in `filled_by_movement_id`,
  dated at the allocation and posted against its own offset account (cost of goods sold for a sale
  or a return, the adjustment account for an adjustment), as a correction entry of that date;
- a true-up of a build's component issue carries a `landed` partner of equal value on the build's
  assembly receipt, at the same date, and the assembly is replayed in turn, so the build cascade is
  dated at the allocation too;
- a movement dated on or after the allocation carries the charge in its own cost; one already posted
  gets an ordinary correction at its own, open, date.

So the share already sold reaches cost of goods sold, and the share on hand stays in stock: under
average what the running average still holds, under FIFO what remains of the receipt's layer. The
date defaults to the latest charge date, never before a target receipt, and moves to the first open
date when that is later, so a freight bill for stock received and partly sold before the close posts
wholly in the open period and nothing earlier moves [E 39]. A receipt-targeted price correction, by
contrast, is dated at the receipt and reaches back; freight never uses it. True-ups are derived: a
sale later backdated before the allocation changes them, and the change is dated at the allocation,
as a shortfall true-up's is dated at its receipt.

**Guards.** A charge is never allocated beyond its amount. Voiding or reducing a charge line, or
voiding or changing a target receipt, is refused with E_WORK_DEPENDENCY while a live allocation line
names it, pointing to `landed-cost void`. Receipt and bill pages show each line's landed cost and
landed unit cost; a printed bill never includes it [E 39].

## 6. Physical counts and cycle counts

### Adjusting many items

`inventory adjust` gains `lines` (1–200), each with item, `quantity_change` or `new_quantity` (the
quantity at that place on the date, the difference worked out when written: the anchor's New Quantity
[M 548]), `value_change` or `new_value` [M 549], place fields and class, under one date, one
adjustment account [M 547], number and memo. The single-item fields stay as the one-line form. An
increase with no value is valued at the running average [M 546] as a new input kind, **`found`**,
whose value replay computes as it does an issue's, so a later backdated purchase recosts it; at or
below zero it takes the last average, else the purchase cost captured on it, else zero with a
warning. A line worth nothing moves quantity with no journal lines; an adjustment worth nothing in
total is refused, as today.

### The count document

Tables: **`inventory_counts`** (number, version, status `open`, `recorded`, `cancelled` or `voided`,
site, count date, `blind`, the selection, adjustment account, memo, provenance);
**`inventory_count_lines`** (item, bin, lot, serial number or unassigned, origin `sheet` or `found`,
and once recorded its counted quantity, fixed); **`inventory_count_entries`**, append-only (line,
mode `set` or `add`, quantity, scanned code, entered at, by and via, audit event).

- **Create** takes site (default the default site), date (default today), a selection — `bins`,
  `items`, `category`, `cycle` with `limit`, or none for every stocked item with quantity or history
  at the site — `blind`, memo and reason. It writes one line per item and place the books hold, and
  zero lines for places named explicitly.
- **Enter** takes 1–500 entries: item or `code` (§7), bin, lot or serial number, quantity and mode
  (`set` by default; a browser scan sends `add` 1). Entries append, each under its own idempotency
  key, so two people, or a person and an agent, can count one count at once. An entry for a place
  not on the sheet adds a `found` line; units whose number cannot be read are counted unassigned.
- **Sheet** gives item, description, place, unit, expected, counted and difference, and prints as
  the Physical Inventory Worksheet [M 544]; `report physical-inventory-worksheet` gives the same
  without a count. A blind count's sheet omits expected and difference, which appear only in the
  post preview. Blind shapes the sheet; it does not hide stock reports from those allowed to read
  them.

### Recording a count

`inventory-count post` takes the expected version, the adjustment account and the preview fingerprint
from its dry run (E_PREVIEW_STALE otherwise), and records the count as an input: each line's counted
quantity is fixed at its place, at the count's **position** — its date, after that date's other
documents unless they say otherwise.

- **Derived differences.** As an issue's cost is derived, a line's difference is derived: counted
  less what the placements hold at that place at the count's position. Per item, the net of its
  lines' differences posts as one movement — an issue for less, `found` at the average for more —
  whose placements carry each place's difference. Where the places cancel, the placements alone move
  the stock and nothing posts, so misplaced stock is moved, never lost and found. The movements post
  as one inventory adjustment dated the count date. Unchanged lines post nothing, and uncounted places
  are no part of the input (enter 0 to count a place empty), as a blank worksheet cell is.
- **Later changes.** A movement, transfer or assignment later placed before the count changes what
  the place held, so the difference is derived again and the shelf stays as counted. The count is
  owed a correction at its own date and position — quantities, placements and their value at the
  average there — as a backdated purchase owes an issue a cost correction. A count dated on or before
  the closing date is refused, and so is any change that would derive one again, since that change
  is itself dated on or before it.
- **Same day.** Every stock-moving document, transfer and assignment takes `count_position`:
  `before` (the default) or `after`, ordering it against the counts of its sites on its date. Replay
  orders by date, count position, then sequence; existing rows are `before`, so nothing moves. The
  plumber who counts the van at four and has the agent bill the day's jobs at nine keeps the
  default; a sale made after the count says `after`. Results name the count a document was placed
  before or after, and the post preview lists the documents entered after counting began and dated
  the count date, with their position.
- **Void** retires the input: its movements and placements are reversed and nothing is derived for
  it again. **Cancel** ends an open count with nothing recorded.

### Cycle counts

- **Classes.** List `cycle-class` (name, interval in days), seeded on first use as A 30, B 90 and
  C 365; items gain `cycle_class`.
- **`cycle-count classify`** proposes classes from each item's cost of goods issued over a period
  (default the last 12 months): the items making the first 80% of that cost are A, the next 15% B,
  the rest C, the thresholds being inputs. The dry run lists the changes; applying sets them.
- **Due.** An item is due at a site when its class interval has passed since its last recorded count
  there, or it has never been counted there; unclassified items are never due. `cycle-count due
  --site` lists them, most overdue first, A before B before C.
- **Rotation.** `inventory-count create --cycle --limit 25` counts the next due items; recording
  moves their due dates on, so the next count takes the next ones. Nothing runs by itself; R111's
  scheduler may later create cycle counts.

## 7. Barcodes

A code may name an item (`items.barcode`), a bin (`inventory_bins.barcode`), a serial number or a
lot, looked up in that order; a code naming more than one record is ambiguous and the answer lists
them.

- **Items.** A new or changed barcode that another active item carries is refused (E_VALIDATION on
  `barcode`, naming that item); existing duplicates stay, and a lookup lists both. Every `item` field
  on every surface resolves id, then name, then barcode, so a scanned code works wherever an item is
  named, with no new syntax.
- **`barcode lookup --code`** returns the kind, the record, where it is (on hand by site, a serial
  number's place, a lot's quantities) and the line fields to use.
- **`barcode generate`** gives items or bins without a code an EAN-13 in the restricted-circulation
  range (prefix 2) from a company counter, with its check digit. Existing codes never change.
- **The browser.** A Scan field on the invoice, sales receipt, item receipt, bill, transfer and count
  screens. A scanner in keyboard mode types the code and Enter; the field keeps focus and treats
  Enter as look-up, never as submit. An item adds a line or adds 1 to the last line for it (typing
  `6*` first makes it 6); a serial number adds its item with that number; a lot sets the lot for the
  next scans of its item; a bin sets the current bin. An unknown code shows a message with "Assign to
  an item" and loses nothing typed. It works at 390 pixels with a Bluetooth scanner.
- **Agents** pass a code as `item`, or as `code` on count entries, and use `barcode lookup` for
  anything else.

## 8. Surfaces

### Storage and migrations

One company migration per increment, numbered after main's head when it merges (co0083 at the time
of writing; co0082 and co0084–co0086 are held by branches in flight). Each keeps every existing row,
index and trigger, and each extends both demo seeds, with any oracle move stated.

| Increment | Tables and columns |
|---|---|
| 1 Placements, sites and transfers | `inventory_sites` with "Main"; `inventory_bins`, `lots` and `serial_numbers`, empty until 4 and 5 but created now so placements reference them from the start; `item_site_settings`; `inventory_placements` with its triggers, backfilled; the three transfer tables; `site_id` on `work_lines`, `purchase_order_lines` and `assembly_builds` |
| 2 Counts | the three count tables; `inventory_movements` rebuilt with kinds `found` and `landed` (used from 6), `count_position`, `landed_on_movement_id`, a captured fallback cost on `found`, and `returns_movement_id` allowed on a vendor return to name its receipt (used from 7); `inventory_documents` rebuilt to admit `count` and `landed_cost`; each table rebuilt once |
| 3 Barcodes | indexes on `items.barcode` and `inventory_bins.barcode`; the code counter |
| 4 Serial and lot numbers | `items.tracking`; `inventory_assignments` |
| 5 Bins and cycle counts | `cycle_classes`; `items.cycle_class_id` |
| 6 Landed cost | `landed_cost_allocations`, `landed_cost_allocation_lines`; `items.unit_weight_microunits` and `unit_volume_microunits`; allocation lines for existing receipts' shipping; the clearing account, created on first use |
| 7 FIFO | `costing_methods` |

Each increment adds a permission layer named after it (`inventory-sites-v1` and so on), chained on
the tip at merge. The people-only list gains `costing-method set` and `costing-method withdraw`.

### Commands

| Command | Does |
|---|---|
| `inventory-site`, `bin`, `cycle-class` create, update, show, list, activate, deactivate | the lists (§2, §6) |
| `item update` | gains `site_settings`, `tracking`, `cycle_class`, `unit_weight`, `unit_volume` |
| every stock-moving document | lines gain `site`, `bin`, `lot`, `lot_expiry`, `serial_numbers`; headers gain `site` and `count_position`; sales-order, work-order and purchase-order lines and `assembly build` gain `site`; vendor-credit item lines gain `receipt_line` |
| `inventory-transfer post`, `update`, `void`, `show`, `query`, `history` | §2 |
| `inventory assign` | §4 |
| `inventory adjust` | gains `lines`, `new_quantity`, `new_value` and place fields (§6) |
| `inventory-count create`, `enter`, `sheet`, `show`, `query`, `post`, `cancel`, `void` | §6 |
| `cycle-count classify`, `cycle-count due` | §6 |
| `barcode lookup`, `barcode generate` | §7 |
| `lot show`, `list`, `update`; `serial-number show`, `list`, `update` | §4 |
| `landed-cost charges`, `post`, `void`, `show`, `query` | §5 |
| `costing-method set`, `withdraw`, `show` | §3 |
| reports | `stock-status --site`, `inventory-valuation` by site, `quantity-by-site`, `sales-by-item --site`, `items-by-bin`, `physical-inventory-worksheet`, `inventory-valuation-detail`, `inventory-layers`, `serial-lot-stock`, `serial-lot-history`, `lot-trace` |

All are registry commands, so the CLI, HTTP, MCP (`bookflow_run`) and the browser reach them
identically, each with a dry run, idempotency on writes and a worked example in its help. The help of
`transfer post`, which moves money between accounts, points to `inventory-transfer post`, and a
`transfer post` that names an item or a site is refused with that hint. The command finder answers
"move stock", "van", "stocktake", "physical inventory", "freight", "duty", "serial", "batch",
"expiry" and "scan".

### Permissions

| Commands | Role | Authority |
|---|---|---|
| site, bin and cycle-class list writes | standard | the list's own noun, as other lists |
| `inventory-transfer` writes; `inventory assign`; `inventory-count create`, `enter`, `cancel` | standard | `inventory`, new: stock records that post nothing |
| `inventory adjust`; `inventory-count post`, `void`; `landed-cost post`, `void` | standard | `ledger.post` (count post and void also `inventory`) |
| `cycle-count classify` | standard | `item` |
| `costing-method set`, `withdraw` | admin, a person only | `company` |
| reads, lookups, sheets and reports | member | `ledger.read` or reports |

An agent with the standard role can do everything here except change the costing method.

### Browser

- The Inventory menu gains **Sites** (each site's stock by item, bin and lot, its bins and
  transfers), **Transfers**, **Counts** (open and recorded counts, each site's due list with "Count
  the next N") and **Landed cost** (unallocated charges; an allocation form whose preview shows each
  target's share split into stock and cost of goods sold). Settings gains **Costing method**, for a
  person: the current method, scheduled switches and a switch with its preview.
- The **count screen** is built for a phone: lines grouped by bin, the Scan field pinned on top, a
  keypad for typed quantities, expected hidden when blind, and Post opening the preview.
- Forms gain Site, Bin, Lot and Serial columns where they apply (§2), and a before/after choice when
  a count exists on the document's date. Stock reports gain a site filter and a by-site view; the item
  page shows stock by site and bin, its lots or serial numbers and its unassigned units; receipt and
  bill pages show landed cost per line. Every screen works at 1440 and 390 pixels.

### An agent's first time

"Move 5 widgets from the van to the warehouse":

```
bookflow inventory-transfer post --from "Van 1" --to "Warehouse" \
  --lines '[{"item":"Widget","quantity":"5"}]' --reason "Unloaded 5 widgets from the van" --json
```

The result gives the transfer number and each site's Widget on hand after it, and says nothing was
posted. Date defaults to today; `item` takes a name or a scanned code; an unknown site is refused
with the company's sites listed. If Van 1 held only 3, the result warns and names the warehouse
quantity.

"Count bin A3" (bins arrive with increment 5; before then the same calls count a whole site):

```
bookflow inventory-count create --site Warehouse --bins A3 --reason "Count bin A3" --json
bookflow inventory-count enter 1007 --reason "Counted bin A3" --json \
  --entries '[{"item":"Widget","quantity":"12"},{"code":"0012345678905","quantity":"3"}]'
bookflow inventory-count post 1007 --expected-version 1 --adjustment-account "Inventory Shrinkage" \
  --preview-fingerprint <from the dry run> --reason "Bin A3 count" --json
```

Create returns the count number and its sheet; enter returns, per line, counted, expected (unless
blind) and difference, and the lines still uncounted; the dry run of post shows the adjustment —
each item's difference and its value at the average — before anything is written. Jobs billed later
that day need nothing more: they are placed before the count unless they say `after`.

## 9. Build increments

Ordered by what a plumber with a van and a warehouse gains first: where stock is and moving it,
counting it, scanning, serial numbers for warranties, then the warehouse's bins and rotations,
freight, and a costing method only some companies want. Every increment keeps a one-site,
average-cost company reading exactly as before.

**Shared scenarios and properties.** From increment 1, public-command scenarios interleave receipts,
sales, transfers, counts and retries: every write under an idempotency key, some retried after a lost
response, some same-day documents entered after a count. Each scenario runs twice, with a person
doing its browser steps and an agent its MCP steps, then the other way round, and both runs must end
with the same quantities by place on every date, the same valuation and the same audit history apart
from who and how. Generated sequences check the three properties of §3 under average, and under FIFO
from increment 7. Each increment extends both.

1. **Placements, sites and transfers.** Migration 1 with its triggers; `inventory-site`; `site` on
   stock lines, headers, order and purchase-order lines and builds; placements at every writer and
   the quantity tie; `inventory-transfer`; figures and reports by site; below-zero warnings by place;
   site settings; the browser's sites, transfers, site column and filters; the agent guide's paragraph
   on sites; demo: Main renamed Warehouse, Van 1, a transfer and a van sale. *Done when* receiving 20
   at Warehouse, moving 5 to Van 1 and selling 3 from it leaves 15 and 2, with value and the general
   ledger untouched by the transfer; placements equal the replay's quantity on every date; the
   database refuses a movement whose placements do not sum to it, even when the application check is
   bypassed; a van sale past its stock warns and names the warehouse quantity; voiding the transfer
   restores both sites; a closed-period transfer is refused. *Tests:* migration up and down on a
   populated company; transfer commands on library, CLI and HTTP; corrections and voids of a sale, a
   receipt, a returning credit memo and a build moving placements exactly; committed, on order and
   available by site against hand-worked figures; the first shared scenarios; stock-status time on
   the demo and on 1,000 items across five sites, measured and recorded.
2. **Counts.** Migration 2; multi-line `inventory adjust` with `new_quantity`, `new_value` and
   `found`; `inventory-count`; blind sheets; derived differences, `count_position` and re-derivation;
   the worksheet and valuation-detail reports; the count screen; demo: a recorded van count with one
   item short, one over and one misplaced. *Done when* the van counted at four with two items short
   and one over, then the day's jobs entered at nine with the default position, leaves the van
   exactly as counted and posts only the true shrinkage; a sale stated `after` takes the van below
   the count; a transfer backdated before the count turns its "missing" units into moved ones and
   reverses that loss; a blind sheet never shows expected; two counters entering at once both land;
   found units are recosted by a purchase backdated before the count. *Tests:* hand-worked
   differences and values; `found` replay cases (stock on hand, at zero with a last average, never
   stocked with a purchase cost, no basis); entry retries; E_PREVIEW_STALE; the single-item adjust
   form unchanged; scenarios with counts and late paperwork.
3. **Barcodes.** Barcodes in `item` fields, duplicate refusal, `barcode lookup` and `generate`, `code`
   on count entries, the Scan field on six screens; demo items carry codes. *Done when* scanning one
   code three times on the invoice form makes one line of 3; each scan on the count screen adds 1; an
   unknown code says so and keeps the form; `invoice post` with a code as `item` resolves it;
   generated codes are valid, unique EAN-13s. *Tests:* resolution order and ambiguity; check digits;
   a browser test typing a code and Enter into the invoice and count screens at 1440 and 390.
4. **Serial and lot numbers.** Migration 4; tracking; numbers and the unassigned bucket on every stock
   line, transfer and count; `inventory assign`; return defaults; the three reports and the two show
   commands; printed lines; demo: a serial-tracked water heater and a lot-tracked sealant with two
   lots. *Done when* water heater WH-1 received, moved to Van 1, sold to Acme and returned shows that
   history in order; selling WH-1 twice is refused naming the first sale, and the database refuses a
   second WH-1 in stock even when the application check is bypassed; a water heater sold with no
   number leaves unassigned and `inventory assign` later numbers the unit that sale took; turning
   tracking on leaves existing stock unassigned and on the item page; a serial number sold before its
   receipt shows −1 until the receipt; a sealant sale naming no lot suggests the earliest-expiring
   lot; the lot trace lists the receipt, the transfer and every customer. *Tests:* those cases; whole
   units for serial items; a return of a number its sale did not take refused; an invoice correction
   moving serial numbers back; provisional stock with tracking; scenarios with numbered stock.
5. **Bins and cycle counts.** Bins in three levels, `tracks_bins`, default bins, bins on lines,
   bin-to-bin transfers, `report items-by-bin`, counts by bin, bin labels in scanning; `cycle-class`,
   `classify`, `due` and the cycle selection; the due list; demo: bins A1–A3 and classes. *Done when*
   "count bin A3" creates a count of exactly what A3 holds; scanning a bin label switches the current
   bin; counting the due items in weekly rounds reaches every item within its interval, and an item
   counted today is not due tomorrow; classify proposes A for the items making 80% of issued cost.
   *Tests:* bin depth and uniqueness per site; bins summing to their site; due dates on a fixture with
   hand-worked dates; classify on a fixture with a known split.
6. **Landed cost.** Migration 6; the clearing account and its tie; item weight and volume;
   `landed-cost`; the five bases; allocation records; `landed` movements and forward true-ups through
   builds; receiving-time shipping through the allocator; guards; receipt and bill pages; demo: a
   freight bill over two receipts, one partly sold. *Done when* $90 by quantity over receipts of 10
   and 20 units gives 30 and 60; when the 10 were the item's only stock and 5 were sold before the
   allocation date, forward true-ups dated at the allocation take 15 to cost of goods sold and 15
   stays in stock; a freight bill dated after the close, for stock received and partly sold before
   it, posts wholly in the open period, and a part that went into a build reaches the assembly's cost
   of goods sold on that date too; a freight credit reverses its share exactly; one charge allocated
   in two parts plus a retried post leaves the clearing account equal to the unallocated charges;
   allocating two charges in either order gives the same shares; receipts with shipping read exactly
   as today. *Tests:* exact shares; forward true-ups against hand-worked cases, including a shortfall
   filled by the target receipt and a sale backdated before the allocation; scenarios with freight.
7. **FIFO costing.** Migration 7; FIFO replay for every movement kind; switching both ways;
   `costing-method` for people only; `report inventory-layers`; the switch preview; vendor credits
   naming their receipt line; a fixture company switches, the demo stays on average. *Done when* a
   hand-worked FIFO example — three receipts at different costs, two sales, a customer return, a
   vendor return naming its receipt and one naming none, a landed cost, a count and a backdated
   purchase — matches to the cent; a switch dated after the closing date corrects only dates on or
   after it and revalues nothing; switching back continues at the average; an agent is refused
   `costing-method set`. *Tests:* every movement kind under FIFO; an issue on the switch date; a
   switch while a shortfall is open, filled after it; switching back and forth; builds under FIFO;
   FIFO equal to average when every receipt has the same unit cost; the three properties under both
   methods.

**Closing trial.** A fresh agent with only Bookflow's help and MCP, given prompts that never name the
features ("move 5 widgets from the van to the warehouse", "I counted bin A3: 12 widgets and 3 elbows",
"the freight bill for last week's delivery came in at $90; spread it over what came in", "which
customer got water heater WH-1?", "what should we count this week?"), does each correctly on its first
attempt. One more run starts on a phone, where a person scans a count, and an agent posts that day's
jobs at night; the van ends as counted.

## As built: increment 1

- **Value by site ignores sites below zero.** An item's value is shared by quantity over the sites holding
  a positive quantity, in name order, with `endpoint_share`; a site below zero shows no value (its units are
  owed, not held), and an item no site holds any of shows its whole value at the default site. The sites
  still add up exactly to the item, and Valuation by Site to the account.
- **Order lines naming no site commit as Unassigned.** `report quantity-by-site` prints them on an
  Unassigned row per item; they are not committed at any site, so `report stock-status --site` does not
  deduct them, while the company figures (the sites plus Unassigned) are unchanged.
- **A line keeps its site.** A correction that does not restate a line's site keeps the site that line
  identity had, and a change of site alone is a change (the document is reposted and its placements move).
  The "has anything changed" comparisons carry a line's site only when it is not the default site, so a
  company that never names one compares and fingerprints exactly as before.
- **Sites and transfers use the `item` capability** (standard to write, member to read) rather than a new
  `inventory` capability.
- **A site with open purchase-order lines to arrive at it cannot be deactivated**; the refusal names the
  site and the lines, as it does for stock still held there.
- **Inventory Sales by Site** (`report sales-by-item --site`) lists stocked item lines by the site their
  stock left; discounts and other adjusting lines are not split by site.
- Transfer lines are in base units; moving between bins of one site arrived with bins (increment 5).

## As built: increment 2

- **The count's own position is `count`.** Movements and placements carry `count_position` `before`, `count`
  or `after`; a count's differences sit at `count`, between that date's documents placed before it and those
  stated after. Replay orders by date, this rank, then sequence. Every stored row is `before`.
- **A count's differences post in journal entries of at most 99 items** (a journal entry holds 200 lines), marked
  `count`. Deriving them again corrects only the entries holding an item whose difference moved, by a new revision
  (its old movements reversed exactly in its own transaction, its items restated at the count's date and
  position), as a sale's correction does; an entry left with no difference is voided, and an item that had none
  when recorded posts in a new entry. So "reverses that loss" is exact, not a found quantity at a rounded
  average. Voiding a count voids its entries. Measured on a 500-item count (334 differences, four entries): create
  0.18 s, enter 500 entries 0.79 s, post preview 0.52 s, post 2.07 s, a late same-day sale that derives one entry
  again 0.98 s, a sale after the count date 0.28 s, sheet 0.10 s.
- **Re-derivation runs inside every company write.** Dispatch takes the stock sequence before a command
  applies and, after it, derives again every recorded count whose site and items the command's placements
  reached on or before its date (`inventory_counts.settle_in_command`), in date order so a re-derived count
  reaches a later one. The result says "Placed before count C-3 of Van 1 on ..." and "Count C-3 ... was
  derived again". A dry run does not show the re-derivation; the saved result does.
- **`count_position` on documents.** Invoices, sales receipts, credit memos, bills, item receipts, vendor
  credits, transfers and `inventory adjust` take it; checks, card charges and assembly builds place before
  (their headers gained no field). A correction that states no position keeps the one its stock stood at; one
  that changes only `count_position` is a change, and each document's `show` says its position. A return that
  names its sale takes the sale's position when dated the sale's day, so it never replays ahead of it; stating a
  position that would put it first is refused.
- **A count line is the item at the site, unassigned,** until bins, lots and serial numbers arrive; `bins`,
  `cycle` and place fields on entries are refused with that said. Lines and placements already carry bin, lot
  and serial columns, and a count whose places cancel moves stock by placements alone.
- **Two counts of one place on one date** cannot both be recorded; the second is refused naming the first.
- **Differences worth nothing** (an item with no average and no purchase cost) record their quantity with no
  journal line, riding in an entry of the count that has one, with a warning naming the count. A count with
  nothing else to post leaves them unrecorded, says so, and is derived again by the next stock change it reaches;
  neither a post nor a re-derivation refuses anyone's write for it. A blind count's re-derivation names the
  count and the items, never quantities.
- **A count whose only differences are worth nothing** is recorded but moves no stock until the next stock change
  its date reaches; the post warns, and it heals at that change.
- **Found units fill a shortfall** as a receipt does, owing the short sale its true-up dated at the found
  quantity; co0093 widens co0059's true-up guard to admit them.
- **Permissions:** create, enter and cancel are `item` (standard), post and void `ledger.post` (standard);
  reads are `item` member and the two reports `reports` member (layer `inventory-counts-v1`).
- **`inventory adjust`**: `lines` (1-200) with `new_quantity` (the quantity at the line's site on the date,
  before any count that day) and `new_value` (the item's whole value on the date); a line already holding
  what it says is skipped. An increase with no value is `found`; its basis (average, purchase cost or none)
  is on each line of the result.
- **Entries resolve `code` by `items.barcode`** only; the full barcode lookup is increment 3.
- **The count screen's keypad is the phone's number keypad** (`inputmode="decimal"`), and the item field is
  pinned on top; scanning proper (Enter as look-up) is increment 3.

## As built: increment 3

- **One resolver.** Every `item` field already went through `list_service.resolve_selector`; a list
  definition now names an optional code column (`code_field`, items: `barcode`) that the selector tries
  after id and name. A code matches exactly (trimmed). One active holder wins; with none active, the one
  inactive holder is returned for the caller's own inactive rule; two active holders refuse with
  E_VALIDATION listing both (`details.candidates`).
- **Duplicates.** A create or a changed barcode another active item carries is refused (E_VALIDATION on
  `barcode`, `details.item`); an item saved without changing its code is not re-checked, so kept
  duplicates stay editable. A blank barcode is stored as none.
- **`barcode generate`** numbers from the `sequences` row `barcode` (co0094): `2` + an 11-digit serial +
  the check digit, skipping any code already on an item or bin. With no `items` it numbers every active
  inventory part, assembly and non-inventory part without a code; named items that have one are skipped
  and listed. Each assignment is an ordinary item update in the item's history.
- **`barcode lookup`** answers an unknown code without an error (`found: false`, a message saying how to
  assign it). Its look-up list (`barcodes._KINDS`) holds items only; bins, serial numbers and lots slot in
  there in their increments.
- **The Scan field** is one partial (`scan_field.html`) and one script (`scan.js`), with three modes:
  document grids (invoice, sales receipt, bill, item receipt), fixed rows (the transfer form) and the count
  screen's entry form. It asks `GET /c/{company}/barcode/lookup` (a barcode, then an item id, then a name).
  A repeat scan adds 1 to the last line for that item; `6*` before a code sets that line to 6. "Assign to
  an item" opens the item list in a new window, so nothing typed is lost. The field has no form name and
  never travels with the form.
- **Coming back active.** An item becoming active again (`item activate`, an undone deactivation or
  cascade) with a code another active item now holds is refused with E_VALIDATION naming the holder (an
  undo surfaces it as E_UNDO_CONFLICT); `item unmerge` refuses with its own E_MERGE_REFUSED. Clear one code
  first.
- **The Scan route reads codes first:** barcode, then id, then name (a scanner sends codes), unlike an
  `item` field. Where a code and another item's name collide it takes the code and says "matched code X
  on <item>; an item named X also exists". Enter takes the typed text and clears the field at once; scans
  queue and resolve in order, focus is never pulled back, and `0*` is refused.
- **UPC-A and EAN-13 are not equated:** a 12-digit UPC-A scan matches only if it is stored as those 12
  digits (not as the 13-digit EAN with a leading 0).
- **Demo codes.** Copper Coupling 0012345678905, Two-Valve Kit 2000000000015 (so the generator's first
  code is 2000000000022), Service Call Kit 0036000291452, Received Shipping Kit 4006381333931; the Brass
  Shutoff Valve keeps its 100000000050. No oracle moves: codes change no quantity or amount.

## As built: increment 4

- **Migration co0095** adds `items.tracking` (`none`, `serial`, `lot`; every existing item `none`), the
  `inventory_assignments` table and two placement indexes (serial number, lot). No existing row changes.
- **The number tie in the database.** Triggers refuse: a serial-number placement of anything but one whole
  unit; a placement naming a lot or serial number of another item, or of a kind its item does not track
  (reversals excepted); a serial number whose running quantity in replay order (date, count position,
  sequence, a sequence group taken as one step) leaves -1 to 1 -- checked when a movement or a transfer
  revision row is written (its placements are all in by then) and, for assignments and counts, as each
  placement is written; an assignment whose placements do not net to zero at its item, site and bin on its
  own date, or a placement added to one already written. A number's placements plus the unassigned bucket
  sum to the place's quantity by construction; the seeded checker asserts it at every place on every date.
- **Only standing placements count** for the serial range, in the database as in the application: a placement
  a reversal retires, and the reversal (which keeps its original's date and position under a later sequence),
  are left out together, so a correction or void dated around another document never reads as two steps. A
  reversal is checked with the change it belongs to, not on its own row. A count derived again retires its old
  loose placements before it posts, and places its new ones after, so a unit now posted as found never meets the
  loose placement it replaces.
- **Late paperwork can split a serial number over two sites** (a sale backdated before the transfer that moved
  the unit: one site shows -1, the other +1). The quantity and number ties hold; the write warns, naming both
  sites, and a transfer or a count of where the unit really is puts it right.
- **The application check is stricter than the database.** Reversed pairs left out, a serial number may
  stand at -1 only before anything has brought it in; once it has been in stock and left, taking it again is
  refused naming the document that took it (`serial_lots.check`). It runs on every document, transfer,
  assignment, count post and count re-derivation.
- **Where a numbered unit goes.** Out: from the site the books hold it at (the result says so when that is
  not the line's site). In: where it stands at -1 when sold before its receipt, so it comes back to 0 there;
  else the line's site. A transfer refuses a serial number that is not at the from site on its date.
- **Assignments** are one item at one site: serial numbers, or a lot and a quantity, `assign` or `unassign`.
  With `document` (and `line` when it has two lines of the item) they number the units that line moved, at its
  date, position and site; a correction or void of that document retires them with the line's movement (the
  reversal's placements include the assignment's), so a voided sale takes its numbering back. On hand, they are
  refused beyond the unassigned units the site holds.
- **Corrections keep numbers.** A line that restates nothing about numbers keeps the ones its stock carried
  (and an assignment made against it); `serial_numbers: []` takes them off. A change of numbers alone is a change.
- **Return defaults.** A return naming no numbers takes back its sale's serial numbers when they are exactly
  the units returned, or the sale's one lot; otherwise the units come back unassigned. Naming a number the sale
  did not take is refused.
- **Whole units.** Every stock line of a serial-tracked item moves whole base units.
- **Tracking** turns on freely (stock on hand stays unassigned and shows on the item page and the Overview); it
  turns off or changes kind only while no unit carries a number of the old kind.
- **Counts** create lines per lot and serial number the books hold, take `lot` or `serial_number` on entries
  (a number not on file is made; a serial number the books hold at another site is refused, pointing to the
  transfer), and refuse a serial line counted other than 0 or 1.
- **Builds** take `lot`, `lot_expiry` and `serial_numbers` for the assembly and `components` (item, lot, serial
  numbers) for tracked components; `report lot-trace` links them through the build's own transaction. A build
  update restates no numbers yet (it keeps none); later.
- **Barcode lookup** answers a serial or lot label after items (`kind` `serial_number` or `lot`, `line_fields`
  naming the item and the number); the Scan route gives the item.
- **Shown and printed.** Invoice, sales receipt and credit memo lines show `lot`, `lot_expiry` and
  `serial_numbers` (absent when none), and the printed line adds "SN ..." or "Lot ..., expires ...". Sales
  orders carry no numbers (they move no stock), so their printed lines do not either.
- **Permissions** (layer `serial-lot-v1`): `inventory assign`, `lot update` and `serial-number update` are
  `item` standard; shows and lists `item` member; the three reports `reports` member.
- **Browser.** Sale and bill item grids gain Serial numbers and Lot columns for a company with a tracked item
  (the controls are in each row's panel otherwise); the item page lists its numbers and unassigned units with
  Assign numbers; the Overview lists unassigned units of tracked items.

## As built: increment 5

- **Migration co0097** adds `cycle_classes` and `items.cycle_class_id` (null everywhere: nothing is due), and
  guards that each read only their own row and the bin it names, never anything in replay order: a placement
  names only a bin of its own site; a bin sits in a bin of its own site one level up and never changes site,
  parent or level; bins and cycle classes are never deleted; an item's default bin at a site is a bin of that
  site. Bins sum to their site by construction (a placement carries both); the seeded checker asserts it, with
  the number tie at every bin, on every date. No trigger that counts placements was added or widened.
- **A correction that moves a line's stock to another bin or site** is refused (E_VALIDATION, naming the count)
  when a recorded count at or after the line counted the item at the old place: the count keeps that shelf as
  counted, so the moved units would be counted twice. Record a transfer dated after the count instead. Changes of
  quantity or price, and voids, go through and derive the count again; a refusal raised while a count is derived
  again names that count ("Count C-1, derived again: ...").
- **"(no bin)"** names the units outside every bin on any line, transfer side or count entry. An outgoing line or
  transfer side that names no bin (and keeps none from a correction or a sale) takes its units from the item's
  default bin when it holds them all, else from no bin, else from the one bin that does, else from each place
  holding some (default bin first); only what nothing at the site holds goes below zero, at the default bin,
  with a warning. A transfer line records the place most of its units left from.
- **A serial number split over two bins of one site** by late paperwork warns, as one split over two sites does.
- **A line's bin** is resolved where its site is (`inventory_effects.plan`, `inventory adjust`, transfers,
  assignments): the line's `bin`, else -- on a correction -- the bin its stock stood in, else a return's sale's
  bin, both only when at the same site; else the item's default bin at the site (`item update --site-settings
  [{site, default_bin}]`), else no bin. Naming a bin at a site that does not track bins is refused. A change
  of bin alone is a change: bills, credits and vendor credits carry the bin in the line's numbers semantic, a
  sale through `numbers_changed`, and fingerprints carry it only when a line names one, so nothing else moves.
- **Bins are found** by id, full name at the site (`A3:Top`) or label code; with no site, a name only one site
  has. A site stops tracking bins only while none of its bins holds stock; a bin is deactivated only while it
  and the bins inside it hold nothing and no active bin sits inside it. Renaming a bin renames those inside it.
- **Numbered units in bins.** A serial number leaves from the bin the books hold it in (the result says so when
  that is not the line's bin), and comes back where it is owed; a transfer of a serial number takes it from its
  bin at the from site. Lots stay site-wide for the earliest-expiry suggestion.
- **Transfers** take `from_bin` and `to_bin` per line; the same site as from and to moves between its bins (it
  must track bins, and each line's bins must differ). A bin taken below zero warns, naming the site's other
  bins that hold the item.
- **Counts by bin.** `bins` counts exactly what those bins (and the bins inside them) hold; an entry naming no
  bin counts in the one bin named, else the one bin the sheet lists the item in, else no bin. Count lines carry
  `bin` beside `place` (unassigned, lot or serial), so place labels read as before.
- **Cycle classes are stored on first use** (the first write that names one: `item update --cycle-class`,
  `cycle-class create`, `cycle-count classify`); `cycle-class list` shows the defaults with empty ids until then.
  Classify ranks by issued cost (issues net of their reversals and cost corrections, leaving out count
  differences and inventory adjustments); an item is A while the items before it make less than `a_share`
  percent, B while less than `a_share + b_share`, and C after or when it issued nothing.
- **Due** is exactly the interval passed since the last recorded count at the site, among items with stock
  history there; a never-counted item is due since its first stock date there. A count of bins counts an item
  only when every bin holding it at the count was counted (a site-wide count needs one counted line). The list
  ranks by days overdue over the class interval. `inventory-count create --cycle` takes the first `limit`
  (default 25) and fills places left over with items falling due within the next six days, soonest first: a
  simulation showed that ranking alone lets a cohort of A items fill a round and push B items a round late
  every quarter, and the lookahead keeps a 30-item weekly rotation within each interval plus one round.
- **Classify** over a period with no issued cost proposes no change and says why (`message`).
- **Barcodes.** `barcode lookup` answers bin labels (kind `bin`, after items, before serial numbers and lots,
  `line_fields` site and bin); the Scan field takes a bin label as the current bin, which the count screen's Bin
  field keeps for the next scans. `barcode generate` does not yet number bins (later).
- **Inventory adjust's one-item form** now passes its own `lot`, `lot_expiry`, `serial_numbers` and `bin` (the
  one-line form had dropped its lot and serial numbers).
- **Permissions** (layer `bins-v1`): bin and cycle-class writes and `cycle-count classify` are `item` standard;
  their reads and `cycle-count due` `item` member; `report items-by-bin` `reports` member.
- **Browser.** The Sites page lists bins, adds one, turns bin tracking on and off per site, and its transfer form
  has From bin and To bin per line. The Counts page counts named bins or the next due items, and lists each
  site's due items with "Count the next N". The count screen groups lines by bin and has a Bin field a scanned
  bin label fills. Sale and bill item grids gain a Bin column once a site tracks bins.
- **Demo:** the warehouse tracks bins A1-A3 with labels; WH-1002 and lot S-2702 are put away into A1 and A2 by a
  bin-to-bin transfer; the heater's home bin is A1 and the sealant's A2; the heater is class A and the sealant B.
  No oracle moves: nothing posts.

## As built: increment 6

- **Migration co0098** (on co0097) adds `landed_cost_allocations`, `landed_cost_allocation_lines` and
  `items.unit_weight_microunits` / `unit_volume_microunits`; it uses co0093's `landed` kind, `landed_cost` document
  kind and `landed_on_movement_id` unchanged. Guards: allocation lines are immutable, a header moves posted -> voided
  once, a reversing line mirrors a live line of its own allocation, and a share and a `landed` movement land on a
  bought receipt of their own item dated on or before them. co0093's true-up guard is widened so a landed cost may own
  forward true-ups of issues, customer returns and found quantities. Each guard reads one row or standing rows only;
  the clearing-account tie is not a trigger (it is checked by `landed-cost charges` and the seeded checker).
  Existing receipts' stored shipping shares become `receipt_shipping` allocations (basis quantity), so no row moves.
- **The clearing account is made by `landed-cost setup`** (and is listed as missing by `landed-cost charges`): the
  account has to exist before a freight bill can be charged to it, so "first use" is the person's setup, not the post.
  It is owned by the inventory ledger (`journals.OWNED_SYSTEM_ROLES`), so bill, check, card charge and vendor credit
  expense lines (which post as that owner) may charge it and the journal editor may not.
- **A charge is a document's Landed Cost Clearing lines taken together**, named by the document's id (what
  `landed-cost charges` lists as `charge`): an edit that gives a line a new identity, reorders it or adds another
  line leaves the charge the same charge. Its amount on a date is the sum of its clearing postings dated by then
  (read through `posting_line_sources`). Only an edit that takes it below what live allocations took of it is
  refused; raising it, adding other lines or moving its date passes.
- **Replay** walks a `landed` movement at its receipt's position and recognises it at its own date by walking once
  more per live landed movement of the item, in (date, position, sequence) order, each pass adding the next: every
  replay-decided value (an issue, a return, a found quantity, and a shortfall true-up recognised at its filler's date)
  keeps what the pass holding the landed costs dated on or before its own date says, and each later landed cost owes
  it the difference from the pass before -- a forward true-up keyed (movement, landed), dated at the landed cost. The
  passes telescope, so the parts sum exactly to the full walk. Only the full pass refuses. Cost: one more walk per
  landed movement of the item; a later increment may cache it if an item ever carries many.
- **One landed movement per target receipt per allocation**, carrying every charge's share of it; the allocation
  lines (one per charge and target) all name it. A build component's forward true-up carries a `landed` partner on
  the assembly receipt (partners are keyed by (issue, filler)), so the assembly's own forward true-ups follow.
- **Shares** use the receipt's existing rule -- cumulative endpoints rounded half up (`receiving_intervals.cumulative`),
  sign-symmetric for a credit -- so a receipt's shipping reads exactly as before; each charge is shared on its own, so
  order never matters. `amounts` lays charges and stated target amounts end to end; it is the only basis that may
  send a share to an expense or cost-of-goods `account`. A post mixing charges and freight credits is refused.
- **`value`** reads the receipt movement's value plus its receipt-targeted price corrections, less its receipt's own
  shipping share. Weight and volume measures are base microunits times the per-unit figure in microunits.
- **Guards** run where the change happens. A target receipt is refused a void, or any change to its stock -- its
  line, item, quantity, cost, date, count position, accounts, class, or places (site, bin, lot, serial numbers) -- in
  `inventory_effects.plan` (`landed_costs.check_targets`). An edit that touches no stock (a memo, a supplier
  reference) passes (Kabe's decision, 2026-10-26): the document reposts, and the restated receipt carries the landed
  cost on through `landed_cost_carries` (co0098), which every movement read follows, so replay and every allocation
  read see the cost on the receipt standing now. A charge voided or reduced below its live allocations is refused
  after every company write (a posting-line watermark in dispatch, cheap when the company has no clearing account).
  Voiding an allocation that a later freight credit needs is refused.
- **Voiding a build** whose components owe forward true-ups retires its assembly receipt with the landed partners
  on it: each is backed out by an opposite partner in the same change, so landed costs on a retired receipt net to
  nothing; replay refuses only landed costs that do not.
- **Item receipts** gain `shipping_basis` (quantity, the default and unchanged; value; weight; volume), and every
  change of a receipt retires its old shipping allocation and records the new one.
- **Browser.** Inventory > Landed cost lists unallocated charges with an allocation form (Preview shows each target's
  share split into stays in stock / issued before) and the allocations with Void; bill, item receipt, check and card
  charge pages show each stock line's landed cost and landed unit cost (unit cost to four places, assuming a
  two-decimal home currency).
- **Demo:** copper pipe on DEMO-PIPE-1 (10) and DEMO-PIPE-2 (20), five sold (DEMO-PIPE-INV-1), freight DEMO-FREIGHT-1
  30.00 spread as DEMO-LC-1 (10.00 and 20.00), a 5.00 forward true-up. Oracle moves are stated in `demo_oracle.py`.
- **Permissions** (layer `landed-cost-v1`): post, void and setup `ledger.post` standard; charges, show, query
  `ledger.read` member. Nothing is people-only.

## As built: increment 7

- **Migration co0100** (on co0098) adds `costing_methods` (method, `effective_from`, status `standing` or
  `withdrawn`, reason and provenance; a standing date is unique). Guards: never deleted; a row moves standing ->
  withdrawn once and nothing else about it changes. Neither reads another row. No existing row changes; the demo
  and every company today have no row, so average from the beginning.
- **co0100 also widens co0079's receipt-correction dimensions guard**, which required a correction of a customer
  return to carry the return's own date: a return's forward true-up (increment 6) is dated at its landed cost, so
  a freight allocation reaching a sale that was partly returned before it was refused by the guard. A correction
  naming its filler (co0098's true-up guard dates it there) may now carry the filler's date.
- **The timeline reaches every replay.** `costing_methods.timeline(s)` is passed to `inventory_costing.replay` at
  every call with a session (sales, credits, adjustments, counts, builds, landed cost, validators), so FIFO decides
  every value replay decides. A switch being previewed stands in for the stored timeline while its corrections are
  worked out.
- **`costing-method set --method fifo --from 2027-01-01`** (the input field is `effective_from`; the CLI flag is
  `--from`). Refused: on or before the closing date (E_PERIOD_CLOSED); on or before the last standing switch; the
  method already in force on that date. It records the switch and posts, each at its own date, the corrections
  owed to movements already dated on or after it (only items with a movement on or after the date are replayed).
  The dry run gives per item and in total the change in stock value and in cost of goods sold (corrections whose
  offset is not an inventory-asset account: a build's component corrections move to its assembly instead), and
  each correction entry. `withdraw` takes a standing switch by id or date while its date is after today, with the
  corrections that takes. Both are a person's act (people-only, R168's mechanism, also refused in the service for
  a caller with no actor).
- **The opening layer.** On the switch date, before any movement dated that day, the stock on hand becomes one
  layer dated at the switch; a purchase dated on or after the switch, however late it is entered, is a layer after
  it. Switching back merges the layers; open shortfalls carry over both ways.
- **Vendor credits name a receipt line** with `receipt_line` on an item row: the `line_id` of the bill, check, card
  charge or item receipt line, a standing bought receipt of the same item dated on or before the credit. It is
  captured on the row's facts (`receipt_line_id`, absent when not named, so nothing else fingerprints differently)
  and kept by a correction that leaves it out; `null` takes it off. The `vendor_return` movement carries the
  receipt in `returns_movement_id` (co0093's reserved use); a receipt restated since by an edit of its document is
  followed by its document line identity. Under FIFO the return takes that receipt's layer first and any units
  beyond it from the oldest layers; with no name, the oldest layers. The credit's difference from those units'
  worth stays in stock, spread over what is left by units (question 1's default); emptying the stock still has to
  credit exactly its worth. Under average the link is recorded and changes nothing.
- **A write-down under FIFO** is spread over the layers by units, so a layer much cheaper than the rest can be
  left worth less than nothing while the item's total stays positive; issuing it then credits cost of goods sold.
  The total is what replay refuses on, as under average.
- **Shortfalls under FIFO** are costed at the newest layer's unit cost (else the last seen, then the average's
  fallbacks); `Shortfall.basis` still says `average` for that, and the warning says "at the newest layer's unit
  cost".
- **`report inventory-layers`** (as_of, item; paged like the stock reports) replays each item's movements dated by
  as_of with the switches dated by then: under FIFO each layer's source document and date ("Opening (FIFO from
  ...)" for a switch's opening layer), quantity, value and unit cost to four places; under average one "Average"
  row. It does not refuse on the asset tie: its total is replay's value on that date, which equals the books
  except on a date between a correction and the later-dated cause it answers.
- **Browser.** Settings > Company setup > Costing method: the method today, every switch (Withdraw on scheduled
  ones) and a switch form whose Preview is the dry run (per item, totals, entries). Inventory > Inventory layers
  opens the report at today.
- **Permissions** (layer `fifo-v1`): `costing-method set` and `withdraw` `company` admin and people-only; `show`
  `ledger.read` member; `report inventory-layers` `reports` member.
- **Demo:** stays on average; no oracle moves. The seeded checker's fixture company (`tests/test_fifo_seeded.py`)
  switches both ways at random dates.

## 10. Open questions

Each default stands if nobody decides.

1. **A vendor credit's difference from FIFO layer cost.** *Default:* it stays in stock, spread over
   the remaining layers, as the average keeps it. The alternative sends it to cost of goods sold.
2. **An outgoing lot line that names no lot.** *Default:* the units leave unassigned, with the
   earliest-expiring lot suggested. The alternative takes that lot automatically.
3. **A serial number never on file, named on a sale.** *Default:* accepted with a warning, at −1
   until a receipt or an assignment brings it in. The alternative refuses it and points to
   `inventory assign`.
4. **Stock in transit.** *Default:* transfers are instant, which suits a van and a warehouse.
   Dispatch, in-transit stock held at the company's own transit site, and partial receipt with
   discrepancies are a later option, wanted once a business ships between distant sites.
5. **A transfer that takes its from place below zero.** *Default:* saved with a warning, as a sale
   is. The alternative refuses it.
6. **A technician's own site.** *Default:* the document's `site`, else the company default; a
   per-user default site (each technician's van) is not in these increments.
7. **Pick, pack and ship.** Neither item includes it, though [sales orders](sales-orders.md) left it
   here. *Default:* not built in these increments; it needs its own place on the roadmap.
   Placed as R181; its design is [pick, pack and ship](pick-pack-ship.md).
