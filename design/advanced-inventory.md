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
later issue a correction at that issue's date and is refused whole if any such date is closed. Stock
may go below zero at a provisional cost, trued up by the receipt that fills it. `inventory adjust`
moves one item and requires a value with an increase. An item receipt spreads its `shipping` over
received quantities (`receiving.py`); a bill priced differently from its receipt posts a
receipt-targeted `recost` at the receipt's date (`receipt_billing.py`). Items carry an optional,
searchable `barcode` that nothing resolves. One inventory-asset account per company
(`uq_accounts_system_role`); both stock reports refuse to print unless item values total it on the
as-of date (`inventory_reports._check_tie`). Committed and available come from `commitments.py`.

It amends five rules: (1) every quantity a movement moves is also *placed* at a site, and at a bin,
lot or serial number where used, in a second, quantity-only ledger; (2) `inventory adjust` takes many
lines, and an increase with no stated value is valued at the running average instead of refused;
(3) replay follows a dated costing-method timeline and gains a `found` kind and value movements
linked to a receipt; (4) an `item` field resolves a barcode after id and name; (5) every stock line
may name its site, bin, lot and serial numbers.

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
  transfer moves quantity, never value, and posts nothing, and a site's value is its share of the
  item's (§2). The replay, the single inventory-asset account and the asset tie are unchanged
  (question 1).
- The guides do not say how tracking is chosen; Bookflow chooses it per item: none, serial or lot.
- An outgoing line for a lot-tracked item that names no lot takes the earliest-expiring lot in
  stock there and says which (question 5).
- A count is a document that several people and agents can fill at once; it posts the anchor's
  adjustment. Counts can be blind, and cycle counting has ABC classes and rotation, which the guide
  does not describe [E 41].
- Landed cost posts at its own date by an exact rule (§5): what the books would hold had the charge
  been on the receipt, less what they hold, split between stock and what has already left it.
- A costing-method switch is dated, takes effect only after the closing date, and is a person's act.
- Scanning is a browser field fed by any scanner in keyboard mode; there is no device app or camera.

**Outside these items:** pick, pack and ship and its fulfillment dashboard [E 36–38]; barcode label
printing (R113's label profiles); e-commerce sync [E 38]; item images [E 36]; lot or expiry data
encoded inside GS1 barcodes.

## 2. Sites and bins

### The lists

- **`inventory_sites`**: the list columns, `name` (unique), `description`, `address`, `is_default`
  (exactly one active default) and `tracks_bins` (default false). The migration creates "Main" as
  the default; renaming it is an ordinary update. Deactivating a site that holds any quantity today
  is refused with E_RECORD_IN_USE, naming the items; the default site cannot be deactivated.
- **`inventory_bins`**: the list columns, `site_id`, `name`, `parent_id` (same site), `full_name`
  ("A:3:Top", unique per site), depth 1–3 [E 46], an optional `barcode` unique among active bins,
  and `description`. Bins are used only at a site that tracks them; deactivating a bin that holds
  stock is refused.
- **`item_site_settings`**: one versioned row per item and site with reorder minimum and maximum
  and a default bin, set with `item update --site-settings`.

Site and bin fields appear on forms only when the company has more than one active site, or the
site tracks bins. A one-site company sees nothing change.

### Where stock is: placements

`inventory_movements` stays the ledger of quantity and value per item, one row per inventory-asset
posting line. Where the units are is a second fact with another shape — one sale line may take
from two lots, and a transfer moves units with no value and no posting — so it has its own
append-only ledger, **`inventory_placements`**: id, item, site, bin (null), lot (null), serial
number (null; never both lot and serial), signed quantity (nonzero; exactly one base unit either
way for a serial), effective date, `sequence`, source (`movement`, `transfer` or `assignment`, with
`movement_id`, `transfer_revision_id` and `transfer_line_id`, or `assignment_id`),
`reverses_placement_id` (unique), created at, by and via, and audit event. A trigger rejects update
and delete.

- **Invariants.** A movement's placements sum to its quantity; a reversal movement's placements are
  the exact negatives of those of the movement it retires; a transfer revision's or an assignment's
  placements net to zero per item, lot and serial number. So on every date an item's placements sum
  to its on hand. Every reading by site, bin, lot or serial asserts this **site tie** and refuses
  with E_INTERNAL naming the item and the gap, as the stock reports assert the asset tie.
- **Sequence.** A movement's placements carry its sequence; transfer and assignment placements take
  numbers from the same company counter (`inventory.next_sequence` reads both tables).
- **Writers.** Placements are written beside movements by the three movement writers that exist —
  the document seam `inventory_effects.write_movements`, adjustments and builds — from the place
  each `Entry` carries, and by transfers and assignments. No document writes them itself.
- **Migration.** One placement at the default site for every existing movement that moves quantity.

**A line's place.** Site: the line's `site`, else the document's `site`, else the source line's (an
invoice made from an order line, a receipt against a purchase-order line), else the default site.
Bin: the line's `bin`, else the item's default bin at that site when the site tracks bins, else none
("no bin" is a place). Lots and serial numbers: §4. A customer return goes back where its issue took
from unless the line names another place. Every result that moves stock states each stocked line's
place.

**Below zero.** A site, bin or lot may go below zero; the write is saved with a warning that says
where else the item is ("Van 1 now shows −2 Widget; Warehouse holds 40"). Costing still reads only
the item's total, so the shortfall and true-up rules do not change. Serial numbers follow §4.

### Transfers

The anchor's transfer between locations is the document **`inventory-transfer`**. Tables:
`inventory_transfers` (id, number, status `posted` or `voided`, current revision, version, created
and updated), `inventory_transfer_revisions` (date, from site, to site, memo, reason, provenance) and
`inventory_transfer_lines` (stable line identity, position, item, quantity in base units, unit and
factor, from bin, to bin, lot, description; serial numbers live in the placements).

- **Post** takes date (default today), an optional number (sequence key `inventory_transfer`), from
  and to sites (the same site when moving between its bins), 1–200 lines (item, quantity, unit, from
  bin, to bin, lot, serial numbers), memo and reason. Each line writes −q at its from place and +q at
  its to place, same lot or serial numbers, on the transfer's date. There is no journal entry, no
  movement and no value: cost and the inventory asset are untouched.
- **Update** replaces the document under its expected version: the old revision's placements are
  reversed and the new ones written in one transaction. **Void**, with a reason, reverses them and is
  final. **History** lists every revision.
- A transfer dated on or before the closing date, or an update or void reaching such a date, is
  refused, so site history and the counts posted against it stay as reported.
- A serial number that is not at the from place on that date is refused; taking the from place
  below zero warns (question 4).

### Figures by site

- **On hand** at a site, bin, lot or serial number is the sum of its placements dated by the as-of
  date.
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
- **Value** by site: cumulative endpoint shares of the item's value by site quantity, sites in name
  order, so the sites sum exactly to the item and the asset tie is untouched. An item holding
  nothing, or less, shows its whole value at the default site.

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
| bought receipt, with its receipt-targeted corrections | fills open shortfalls first, then adds the rest to the pool | fills open shortfalls first, as today; the rest is a new layer |
| customer return | worth its endpoint share of the issue | the same worth, as a new layer |
| `found` (§6), a receipt whose worth replay decides | round(V·q/Q); at or below zero the last average, else the captured purchase cost, else zero | round(V·q/Q) over all layers, with the same fallbacks; after shortfalls, a new layer |
| issue | takes round(V·q/Q), or all of V when it empties the stock | takes units from the oldest layers; each gives round(W·u/U), or all of W when emptied |
| `value`, unlinked | added to V | spread over the layers by remaining units, cumulative endpoint shares in layer order |
| `value` linked to a receipt (§5) | added to V | added to that receipt's layer; spread as unlinked when the layer is gone |
| vendor return | Q falls by its quantity and V by the credit | takes units from the newest layers; the credit leaves, and the difference from those layers' worth is spread over the rest as a value change (question 2); refused, as today, when nothing is left to carry it |
| shortfall rate | the running average, else the last one, else the captured purchase cost, else zero | the newest layer's unit cost, else the last one seen, then the same fallbacks |
| recost, reversal | outputs and retirements, never inputs | unchanged |

Emptying a layer takes all of its value, so the zero-residual rule holds per layer, and every
division is exact integer arithmetic rounded half-even once. A build's component issues take FIFO
cost and its assembly receipt becomes a layer; partner corrections work as today. Lots and serial
numbers carry no cost of their own: a tracked item is costed by the company's method like any other.

**Switching.** When the walk reaches the switch date, before any movement dated on or after it, the
state converts: average to FIFO makes the pool one layer; FIFO to average merges the layers into one
pool. On hand and value are equal either side, so the switch itself posts nothing, and open
shortfalls carry over. Movements dated on or after the switch are costed by the new method; each
whose cost then differs from what is posted gets its own correction at its own date, every one
after the closing date, so no closed period moves.

**What it changes.** Cost of goods sold from the switch date on, by dated corrections. Stock value is
the sum of the layers, and the asset tie holds because every value change is still a movement on an
asset posting line. `average_cost` in reports keeps its meaning, value over quantity on hand. `report
inventory-layers` lists each item's layers on a date (source document and date, remaining quantity,
value and unit cost; one row under average). `report inventory-valuation-detail` (the anchor's
Inventory Valuation Detail [R 24]) lists the movements with running quantity and value under either
method. LIFO is not offered; the anchor has none [M 549–550].

## 4. Serial and lot numbers

Inventory parts and assemblies gain `tracking`: `none` (default), `serial` or `lot`. Two tables hold
the numbers: **`lots`** (item, number unique per item, `expiry_date`, memo) and **`serial_numbers`**
(item, number unique per item, memo). A number is created by the first line that names it; where it
is and how many there are come only from placements.

**Turning tracking on.** With nothing on hand, at once. With stock, `item update --tracking serial
--opening-assignment …` must name exactly what is on hand at each place (site, bin, and lots with
quantities or serial numbers); it is written as an assignment dated today, whose placements move the
untracked quantity onto the numbers. Turning tracking off keeps every number and its history.

**Serial numbers.** Quantities are whole base units and `serial_numbers` lists that many distinct
numbers; it is required.

- In (receipt, found, customer return, transfer in): refused when the number is in stock anywhere.
- Out (sale, vendor return, transfer out, build component): the unit leaves from where the books hold
  it, and the result says so when that differs from the line's site or bin. A number that was in
  stock and has left is refused, naming the document that took it. A number never on file is
  recorded with a warning and shows −1 until a receipt brings it in (question 3).
- A customer return may give back only numbers its issue took.

**Lots.** An incoming line must name `lot`, with `lot_expiry` when the lot is new (`lot update`
changes it later). An outgoing line, a transfer's included, names `lot` or leaves it out to take
the earliest-expiring unexpired lots in stock at that place, oldest first on equal expiry, split
across lots as needed with one placement each; the result names the lots taken. An expired lot is never taken by default, and
naming one warns. A lot may go below zero at a place, with a warning.

**Builds, counts and printing.** Components follow the same rules; a tracked assembly's receipt names
its own lot or serial numbers, and the build links them for tracing. Counts count tracked items by
lot or serial number (§6). Printed sales orders, invoices, sales receipts and credit memos show serial
numbers, lot and expiry on their lines [E 41].

**Reports.** `report serial-lot-stock` (Serial/Lot Numbers in Stock, Quantity on Hand by Serial/Lot
Number, Stock Status by Lot Numbers): item, number, site, bin, quantity and expiry, filtered by
item, site or `expiring_by`. `report serial-lot-history` (Transaction List by Serial/Lot Number):
every placement of one number in date order with document, customer or vendor, place, in, out and
balance. `report lot-trace`: that history plus, through builds, what the lot went into and who
bought it, and what went into it, for a recall. `serial-number show` and `lot show` say where a
number is now.

## 5. Landed cost

**Charges.** An account of type cost of goods sold or other current asset can be marked
`landed_cost` [E 39]. A charge is an expense line of a bill, check or card charge on such an account;
its unallocated amount is its amount less its live allocations. `landed-cost charges` lists
unallocated charges with vendor, document, date and amount.

**Targets.** Received stock: item-receipt lines and the item lines of bills, checks and card charges
that brought stock in (bought receipts, not returns).

**Allocation.** `landed-cost post` takes charges (line and amount, default the whole unallocated
amount), targets (documents or lines), `basis`, date, memo and reason.

- `quantity` uses base quantity; `value` the receipt's value, its own shipping and price corrections
  included; `weight` and `volume` base quantity times the item's new `unit_weight` or `unit_volume`,
  overridable per target and refused naming any target without one; `amounts` takes an amount per
  target, summing exactly to the charges.
- Shares are cumulative endpoint shares in target order, the rule receiving already uses for
  shipping, so they sum exactly to the charges.

**Posting at the allocation date.** The date defaults to the latest charge or target date, moved to
the day after the closing date when that is later; a date before any target or charge, or in a closed
period, is refused. So a closed-period receipt's freight posts in the first open period and nothing
earlier moves [E 39]; a receipt-targeted `recost`, which a bill's price difference uses, is dated at
the receipt and would reach back into that period. The split is exact: each target's share is added to its
receipt as if it had been on it from the start, the item is replayed to the allocation date both
ways, and the document posts the difference on its own date:

- the change in the item's stock value: a debit to Inventory Asset carried by a `value` movement
  linked to the receipt (`revalues_movement_id`);
- the change in each movement whose value replay decides (issues with their true-ups, customer
  returns, found units) dated by then: to that movement's offset account (cost of goods sold for a
  sale or return, the adjustment account for an adjustment), grouped by account and class; a share
  that went into a build is carried to the build's assembly receipt and split again for the assembly;
- a credit to each charge's landed-cost account for its share, naming the charge's vendor.

Targets are added one at a time in order and each takes the change it makes, so per-target figures
are exact and sum to the charge. Under average the stock share is what the running average would
still hold; under FIFO it is what remains of that receipt's layer. An item holding nothing, or less,
on that date keeps no stock share; that part goes to its cost-of-goods-sold account. Movements
already posted after the allocation date then get ordinary dated corrections, as any backdated value
input causes; allocating at today's date causes none. The entry is a journal entry owned by
inventory and marked `landed_cost` in `inventory_documents`; `landed_cost_charges` and
`landed_cost_targets` record charge lines, targets, measures, shares and each target's split.

**Guards.** A charge is never allocated beyond its amount. Voiding or reducing a charge line, or
voiding or changing a target receipt, is refused with E_WORK_DEPENDENCY while a live allocation names
it, pointing to `landed-cost void`, which reverses the entry and its movements at its own date. A
later backdated change that leaves the item with nothing on hand on the allocation date is refused,
naming the allocation. Receipt and bill pages show each line's landed cost and landed unit cost; a
printed bill never includes it [E 39]. An item receipt's own `shipping` is unchanged.

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

Tables: **`inventory_counts`** (number, version, status `open`, `posted`, `cancelled` or `voided`,
site, count date, `blind`, `cutoff`, the selection, adjustment account, memo, the adjustment and
transfers it posted, provenance); **`inventory_count_lines`** (item, bin, lot, serial number,
position, origin `sheet` or `found`); **`inventory_count_entries`**, append-only (line, mode `set`
or `add`, quantity, scanned code, entered at, by and via, the inventory sequence at entry, audit
event).

- **Create** takes site (default the default site), date (default today), a selection — `bins`,
  `items`, `category`, `cycle` with `limit`, or none for every stocked item with quantity or history
  at the site — `blind`, `cutoff`, memo and reason. It writes one line per item and place the books
  hold, and zero lines for places named explicitly.
- **Enter** takes 1–500 entries: item or `code` (§7), bin, lot or serial number, quantity and mode
  (`set` by default; a browser scan sends `add` 1). Entries append, each under its own idempotency
  key, so two people, or a person and an agent, can count one count at once. An entry for a place
  not on the sheet adds a `found` line.
- **Sheet** gives item, description, place, unit, expected, counted and difference, and prints as
  the Physical Inventory Worksheet [M 544]; `report physical-inventory-worksheet` gives the same
  without a count. A blind count's sheet omits expected and difference, which appear only in the
  posting preview. Blind shapes the sheet; it does not hide stock reports from those allowed to read
  them.

### Posting

`inventory-count post` takes the expected version, the adjustment account (the preview suggests the
previous count's) and the preview fingerprint from its dry run; an entry or movement since then is
E_PREVIEW_STALE. A line's expected quantity is on hand at its place on the count date, read when
posting:

- `cutoff: end_of_date` (default): every movement dated by the count date, whenever it was entered.
  A plumber who counts the van at five and has the agent bill the day's jobs at nine gets it right.
- `cutoff: at_entry`: only movements entered before the line's latest `set` entry (or its first
  entry), for counting while stock keeps moving that day.

Within one count, a shortage at one place and a surplus of the same item, lot or serial number at
another are misplaced stock: the matched quantity is moved by transfers posted with the count, one
per source site, not lost and found. A serial number counted here that the books hold at another
site is moved here the same way, never found a second time. The remaining differences become one
multi-line `inventory adjust` dated the count date, an issue for less and `found` for more. Unchanged lines post nothing; uncounted lines are named
in the preview and left out, as a blank worksheet cell is (enter 0 to count a place empty). The
preview lists movements entered after a line was counted but dated by the count date, so late
paperwork is seen before it is counted twice [M 545]. A count date in a closed period is refused. A
posted count is read-only; `inventory-count void` voids its adjustment and transfers, and `cancel`
ends an open count with nothing posted.

### Cycle counts

- **Classes.** List `cycle-class` (name, interval in days), seeded on first use as A 30, B 90 and
  C 365; items gain `cycle_class`.
- **`cycle-count classify`** proposes classes from each item's cost of goods issued over a period
  (default the last 12 months): the items making the first 80% of that cost are A, the next 15% B,
  the rest C, the thresholds being inputs. The dry run lists the changes; applying sets them.
- **Due.** An item is due at a site when its class interval has passed since its last posted count
  there, or it has never been counted there; unclassified items are never due. `cycle-count due
  --site` lists them, most overdue first, A before B before C.
- **Rotation.** `inventory-count create --cycle --limit 25` counts the next due items; posting moves
  their due dates on, so the next count takes the next ones. Nothing runs by itself; R111's scheduler
  may later create cycle counts.

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
| 1 Sites and transfers | `inventory_sites` with "Main"; `inventory_bins`, `lots` and `serial_numbers`, empty until 4 and 5 but created now so placements reference them from the start; `item_site_settings`; `inventory_placements`, backfilled; the three transfer tables; `site_id` on `work_lines`, `purchase_order_lines` and `assembly_builds` |
| 2 Counts | the three count tables; `inventory_movements` rebuilt to admit `found` (which may capture a fallback cost) and to add `revalues_movement_id`, allowed only on `value`; `inventory_documents` rebuilt to admit `landed_cost`, so each table is rebuilt once |
| 3 Barcodes | indexes on `items.barcode` and `inventory_bins.barcode`; the code counter |
| 4 Serial and lot numbers | `items.tracking`; `inventory_tracking_assignments` |
| 5 Bins and cycle counts | `cycle_classes`; `items.cycle_class_id` |
| 6 Landed cost | `accounts.landed_cost`; `items.unit_weight_microunits` and `unit_volume_microunits`; `landed_cost_charges`, `landed_cost_targets` |
| 7 FIFO | `costing_methods` |

Each increment adds a permission layer named after it (`inventory-sites-v1` and so on), chained on
the tip at merge. The people-only list gains `costing-method set` and `costing-method withdraw`.

### Commands

| Command | Does |
|---|---|
| `inventory-site`, `bin`, `cycle-class` create, update, show, list, activate, deactivate | the lists (§2, §6) |
| `item update` | gains `site_settings`, `tracking` with `opening_assignment`, `cycle_class`, `unit_weight`, `unit_volume` |
| every stock-moving document | lines gain `site`, `bin`, `lot`, `lot_expiry`, `serial_numbers`; headers gain `site`; sales-order, work-order and purchase-order lines and `assembly build` gain `site` |
| `inventory-transfer post`, `update`, `void`, `show`, `query`, `history` | §2 |
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
| `inventory-transfer` writes; `inventory-count create`, `enter`, `cancel`; tracking assignments | standard | `inventory`, new: stock records that post nothing |
| `inventory adjust`; `inventory-count post`, `void`; `landed-cost post`, `void` | standard | `ledger.post` (count post and void also `inventory`) |
| `cycle-count classify` | standard | `item` |
| `costing-method set`, `withdraw` | admin, a person only | `company` |
| reads, lookups, sheets and reports | member | `ledger.read` or reports |

An agent with the standard role can do everything here except change the costing method.

### Browser

- The Inventory menu gains **Sites** (each site's stock by item, bin and lot, its bins and
  transfers), **Transfers**, **Counts** (open and posted counts, each site's due list with "Count the
  next N") and **Landed cost** (unallocated charges; an allocation form whose preview shows each
  target's share split into stock and cost of goods sold). Settings gains **Costing method**, for a
  person: the current method, scheduled switches and a switch with its preview.
- The **count screen** is built for a phone: lines grouped by bin, the Scan field pinned on top, a
  keypad for typed quantities, expected hidden when blind, and Post opening the preview.
- Forms gain Site, Bin, Lot and Serial columns where they apply (§2); stock reports gain a site
  filter and a by-site view; the item page shows stock by site and bin and its lots or serial
  numbers; receipt and bill pages show landed cost per line. Every screen works at 1440 and 390
  pixels.

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
each item's variance and its value at the average — before anything is written.

## 9. Build increments

Ordered by what a plumber with a van and a warehouse gains first: knowing what is on the van, counting
it, scanning, serial numbers for warranties, then the warehouse's bins and rotations, freight, and a
costing method that only some companies want. Every increment keeps a one-site, average-cost company
reading exactly as before.

1. **Sites and transfers.** Migration 1; `inventory-site`; `site` on stock lines, headers, order and
   purchase-order lines and builds; placements at every writer and the site tie; `inventory-transfer`;
   figures and reports by site; below-zero warnings by place; site settings; the browser's sites,
   transfers, site column and filters; the agent guide's paragraph on sites; demo: Main renamed
   Warehouse, Van 1, a transfer and a van sale. *Done when* receiving 20 at Warehouse, moving 5 to
   Van 1 and selling 3 from it leaves 15 and 2 with value and the general ledger untouched by the
   transfer; sites sum to the item on every date; a van sale past its stock warns and names the
   warehouse quantity; voiding the transfer restores both sites; a closed-period transfer is refused.
   *Tests:* migration up and down on a populated company, placements equal to on hand per item and
   date; transfer commands on library, CLI and HTTP; corrections and voids of a sale, a receipt, a
   returning credit memo and a build moving placements exactly; committed, on order and available by
   site against hand-worked figures; stock-status time on the demo and on 1,000 items across five
   sites, measured and recorded.
2. **Counts.** Migration 2; multi-line `inventory adjust` with `new_quantity`, `new_value` and
   `found`; `inventory-count`; blind sheets; both cutoffs; misplaced stock as a transfer; the
   worksheet and valuation-detail reports; the count screen; demo: a posted van count with one item
   short, one over and one misplaced. *Done when* counting the van with two items short and one over
   posts one adjustment dated the count date, every difference at the average and unchanged lines
   nothing; a blind sheet never shows expected; two counters entering at once both land; uncounted
   lines are named and skipped; a purchase backdated before the count date recosts the found units.
   *Tests:* hand-worked differences and values; `found` replay cases (stock on hand, at zero with a
   last average, never stocked with a purchase cost, no basis); entry retries; both cutoffs with an
   invoice entered after counting; E_PREVIEW_STALE; the single-item adjust form unchanged.
3. **Barcodes.** Barcodes in `item` fields, duplicate refusal, `barcode lookup` and `generate`, `code`
   on count entries, the Scan field on six screens; demo items carry codes. *Done when* scanning one
   code three times on the invoice form makes one line of 3; each scan on the count screen adds 1; an
   unknown code says so and keeps the form; `invoice post` with a code as `item` resolves it;
   generated codes are valid, unique EAN-13s. *Tests:* resolution order and ambiguity; check digits;
   a browser test typing a code and Enter into the invoice and count screens at 1440 and 390.
4. **Serial and lot numbers.** Migration 4; tracking and the opening assignment; numbers on every
   stock line, return defaults and the earliest-expiry default; tracked transfers and counts; the
   three reports and the two show commands; printed lines; demo: a serial-tracked water heater and a
   lot-tracked sealant with two lots. *Done when* water heater WH-1 received, moved to Van 1, sold to
   Acme and returned shows that history in order; selling WH-1 twice is refused naming the first
   sale; a sealant sale naming no lot takes the earliest-expiring lot there and says which; an expired
   lot is never taken by default; the lot trace lists the receipt, the transfer and every customer.
   *Tests:* those cases; whole units for serial items; a return of a number its sale did not take
   refused; an invoice correction moving serial numbers back; tracking turned on with stock.
5. **Bins and cycle counts.** Bins in three levels, `tracks_bins`, default bins, bins on lines,
   bin-to-bin transfers, `report items-by-bin`, counts by bin, bin labels in scanning; `cycle-class`,
   `classify`, `due` and the cycle selection; the due list; demo: bins A1–A3 and classes. *Done when*
   "count bin A3" creates a count of exactly what A3 holds; scanning a bin label switches the current
   bin; counting the due items in weekly rounds reaches every item within its interval, and an item
   counted today is not due tomorrow; classify proposes A for the items making 80% of issued cost.
   *Tests:* bin depth and uniqueness per site; bins summing to their site; due dates on a fixture with
   hand-worked dates; classify on a fixture with a known split.
6. **Landed cost.** Migration 6; the account flag, item weight and volume, `landed-cost`, the five
   bases, the exact split, the guards, receipt and bill pages; demo: a freight bill over two receipts,
   one partly sold. *Done when* $90 by quantity over receipts of 10 and 20 units gives 30 and 60;
   when the 10 were the item's only stock and 5 were sold before the allocation date, 15 of its 30
   goes into stock and 15 to cost of goods sold; value and weight bases match hand-worked examples;
   an allocation to a closed-period receipt posts on its own open date and moves nothing earlier;
   voiding restores everything; the asset tie holds on every date. *Tests:* exact shares; the split
   against hand-worked cases, one through a build; a charge reduced below its allocation refused;
   retries.
7. **FIFO costing.** Migration 7; FIFO replay; switching both ways; `costing-method` for people only;
   `report inventory-layers`; the switch preview; a fixture company switches, the demo stays on
   average. *Done when* a hand-worked FIFO example — three receipts at different costs, two sales, a
   customer return, a vendor return, a landed cost and a backdated purchase — matches to the cent; a
   switch dated after the closing date corrects only dates on or after it; switching back restores
   average from the second date; the asset tie holds on every date; an agent is refused
   `costing-method set`. *Tests:* every movement kind under FIFO; an issue on the switch date; a FIFO
   shortfall and its true-up; builds under FIFO; FIFO equal to average when every receipt has the same
   unit cost.

**Closing trial.** A fresh agent with only Bookflow's help and MCP, given prompts that never name the
features ("move 5 widgets from the van to the warehouse", "I counted bin A3: 12 widgets and 3 elbows",
"the freight bill for last week's delivery came in at $90; spread it over what came in", "which
customer got water heater WH-1?", "what should we count this week?"), does each correctly on its first
attempt. One more run starts on a phone, where a person scans a count, and an agent posts it.

## 10. Open questions

Each default stands if nobody decides.

1. **Cost per site.** The guides report value by site without saying whether cost differs. *Default:*
   one cost per item across sites; a transfer posts nothing. Reversing this after transfers exist
   would make them post value and need a replay per site.
2. **Which FIFO layers a return to a vendor takes.** *Default:* the newest, the goods most recently
   bought, with the difference from the credit spread over the rest. A vendor credit that named its
   receipt could take that layer exactly; no such link exists today.
3. **Selling a serial number never on file.** *Default:* recorded with a warning, showing −1 until a
   receipt brings it in, as stock sold before its paperwork is. The alternative is to refuse it.
4. **A transfer that takes its from place below zero.** *Default:* saved with a warning, as a sale
   is. The alternative is to refuse it.
5. **An outgoing lot line that names no lot.** *Default:* the earliest-expiring lot in stock there,
   named in the result. The alternative, which keeps recall records exact at the cost of a refusal, is
   to require the lot on every outgoing line.
6. **A technician's own site.** *Default:* the document's `site`, else the company default; a
   per-user default site (each technician's van) is not in these increments.
7. **Pick, pack and ship.** Neither item includes it, though [sales orders](sales-orders.md) left it
   here. *Default:* not built in these increments; it needs its own place on the roadmap.
