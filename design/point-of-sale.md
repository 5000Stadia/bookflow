# Point of sale, camera scanning and each person's Home

This contract covers three linked roadmap items: **R114** point of sale, **R190** camera scanning and **R191** each
person's own Home. They are designed together because a POS clerk scans with a phone, lands on the POS when they sign
in, and sees nothing else. It replaces the brainstorm in [pos-workspace.md](pos-workspace.md), keeping its starting
location, its "permissions decide, hiding is not enough" rule and its acceptance scenes; the commission prompt there is
R189's and stays parked.

**Kabe's words (2026-10-28).** "the point-of-sale screen should function very similarly to the sales receipt pieces,
of course. However, it should be able to have its own conceptual home screen with customizable boxes that can be tapped
on they can either represent a group of menu items that we navigate into or a singular item that is rung up." "We should
be able to attach an associate a picture per item here, which visually presents itself as the item." "we should have a
quick option to do a look up in all inventory items." "within the group navigation presentation, we should be able to
change and have automatically saved the default view style for that group of items which initially should be list and
block and possibly a third or fourth one ... This view option should be able to be applied to the home point-of-sale
screen as well, although it will certainly default initially to the box format ... present the simplicity option first."
"If the picture that we are applying here is the same picture option that we can apply to the inventory item when we are
looking at the inventory item outside of the point of sale system, this would allow pictures that have been taken of the
items in the past to just automatically show up in point of sale." "The point of sale similar to sales receipt should
allow a transaction to happen without a customer name. [A] customer name should be [an] available option." On scanning:
"the phone may even make a beep sound and automatically progress to the next line item option for scanning ...
equivalent to selecting the item via barcode." On Home: "each user to have the option to set the location that
automatically loads as their first page so that ... a point of sale user ... automatically loads the point of sale when
they login ... and don't see any other accounting pages ... if they ever exited that screen, we should always have an
easy button to return that user to the home screen." On the decoder: "don't gate work on that [benchmark] result, just
wire up what would be method agnostic and build the rest of it."

**Reading.** A POS sale is a sales receipt entered through a touch screen: the same command posts it, so the books are
identical to one typed into the sales receipt form. Everything POS adds (the tap-box home, registers, held tickets, the
drawer count) is working state around that posting, never a second kind of accounting. The item picture is one fact on
the item, seen everywhere. The camera is one more way to type into the existing Scan field. Home is a per-person start
page; what a person may *do* is still decided by capabilities at the command boundary (R187), and the menus are made to
follow those capabilities so a clerk sees only what they can use.

**What exists.** `sales-receipt post` takes a required customer, lines (item, quantity, unit, price, price level,
discount and subtotal items, site, bin, lot, serial numbers), one `payment_method`, one `deposit_to` (Undeposited Funds
or a bank account), sales tax by code and policy, and issues stock; price levels and R110 price rules resolve inside it,
and its dry run returns the exact totals. Refunds are `credit-memo post` then `customer-refund post`, which pays out of
an active **bank-type** account only (`refund_validation.py:208`). `deposit post` banks payments and sales receipts out
of Undeposited Funds, with other-money rows and one cash-back row. Attachments link to any list record, items included;
items have no picture. `barcode lookup` resolves a code to an item; the Scan field (`templates/scan_field.html`,
`static/scan.js`, modes `lines`, `rows`, `count`, `pick`) takes keyboard-mode scanners today. Menus are filtered by
**role rank only** (`pages.py:932` `company_permits` → `_role_allows`), never by a member's grants and denies; login
always lands on the Overview; there is no per-user preference beyond timezone. Sales receipts use the same `ledger.post`
capability as invoices and journal entries, so no deny set can allow one without the others.

## 1. The anchor's behaviour, and where Bookflow differs

The anchor desktop product has no touch POS of its own; its separate point-of-sale product exports a day's sales to the
books as sales receipts and deposits. The desktop rules Bookflow keeps: a cash sale is a **sales receipt**; money waits
in **Undeposited Funds** until **Make Deposits** banks it, grouped by payment method; a return is a **credit memo** and
money handed back is a **refund** from a bank account; a sales receipt carries **one payment method**, so a sale paid two
ways is entered as an invoice with one payment per method; cash over or short is a line on the deposit.

**Deliberate differences.** (1) A sale with no customer named posts to the register's **walk-in customer**, a normal
customer record ("Walk-in"), because Bookflow's sales receipt, credit memo and refund all need a party; the screen and the
printed receipt show no name (question K2). (2) POS holds working state the desktop never had: registers, tap-box layouts,
held tickets and drawer counts. None of it posts.

## 2. The POS layer and the register

- **The switch.** `company_info.pos_enabled`, off by default, set with `company update` (admin), shown in Company
  settings under *Sales* ("Use point of sale"), following the pick-and-pack switch (pick-pack-ship.md, as built inc 3).
  While off, every `pos*` command refuses with `E_FEATURE_DISABLED` (`setting`, `enable_command`), no POS surface shows,
  and nothing else changes. Turning it off is refused (`E_WORK_DEPENDENCY`) while a ticket is held or a shift is open.
- **Registers.** Turning POS on makes register **Main**. A register (`pos-register create/update/show/list`) holds: name;
  inventory site; deposit-to account (default Undeposited Funds); the payment methods offered, in order (default Cash,
  then the company's card and check methods); the walk-in customer (default **Walk-in**, created on first use with the
  company's default tax code); the cash refund account (a bank-type account, default the company's main checking
  account); receipt width (80 mm or 58 mm, default 80); receipt message; whether shifts are counted (default off); and
  the decoder order override (§7), normally empty. A device remembers its register in `localStorage`; with one register
  nobody chooses.
- **What a register is not.** It is not a bank account or a drawer account. Cash sits in Undeposited Funds like every
  other cash receipt, so Make Deposits, reconciliation and the reports work unchanged.

## 3. The POS screen

### The home of tap boxes

The POS opens on its **home**: a grid of boxes. Each box is either a **group** (tap to go in; it holds more boxes) or an
**item** (tap to ring it up). Groups nest to any depth; a breadcrumb row shows the path and Back. An item may sit in many
groups. A box shows the item's picture (or the group's), its name and, for an item, its price at the register's price
level; a box without a picture shows a coloured tile with the name.

- **A starting layout.** The first time the POS opens with no boxes, it offers *Start from item categories*: one group
  per top-level item category, holding its sales items and subcategories, and *Start empty*. Either is then edited freely.
- **Editing** (`pos.layout`): an Edit switch turns the grid into the editor: drag to reorder, Add item (from the lookup),
  Add group (name, colour, optional picture), Rename, Move to group, Remove (removes the box, never the item). Each change
  is one command (`pos-box create`, `update`, `move`, `remove`) so an agent can arrange the layout too.
- **One layout per company** in these increments; a register may later name its own (question 4).

### View styles

Four styles, each a way of drawing the same boxes:

| Style | Draws | Phone (390 px) | Tablet |
|---|---|---|---|
| **Block** (default) | picture tile, name, price | 2 per row | 4–5 per row |
| **List** | row: small picture, name, price, on hand | one per row, 56 px | one per row |
| **Compact** | text tile, name and price, no picture | 3 per row | 6–8 per row |
| **Photo** | large picture, name under it | 1 per row | 3 per row |

The style switcher shows Block and List first and Compact and Photo under *More*, so the simple choice comes first. A
change is saved at once, with no Save button, as that group's default (`pos-box view`, held by `pos.sell` so a clerk
can do it); the home has its own style, Block until changed. Everyone at every register sees a group in its saved style.

### Lookup

A search field pinned at the top of every POS view searches **all active sales items**, in boxes or not, by name, SKU,
barcode and description, as the item reference picker does, with results in List style; tapping a result rings it up.
The same field is the Scan field (§7): Enter on a whole barcode rings the item up at once, `6*` first rings up six, and a
camera button sits at its right end.

### Ringing up

- **The cart.** A tap or scan adds a line or adds one to that item's last line, instantly, from the item data already on
  the page. Tapping a line opens its quantity keypad, price (if `pos.price-override`) and Remove. An item with no price
  opens the keypad for one. A serial-tracked item asks for the serial number (scan or type; Skip leaves the unit
  unassigned, as the sales receipt allows).
- **Exact totals.** After each change (debounced 250 ms) the page asks `pos sale preview`, which is the sales receipt's
  dry run, for the lines, price levels, R110 rules, tax and total; the total shown is always the server's, so what posts
  equals what was shown. Until the answer arrives the total reads "updating".
- **Customer.** A customer chip at the top reads *No customer*. Tapping it searches customers by name, phone or email
  (`pos customer search`) or adds one with name, phone and email (`pos customer add`); choosing one re-prices with their
  price level and tax code. Clear returns to the walk-in.
- **Seams for parked work.** Each ticket line carries an empty `attributes` object, and the ring-up path has one hook
  after an item is added; R189's commission picker would prompt there. The preview has one hook before tax; R188's codes
  and memberships would add their lines there. Neither is designed here.

### Taking payment (the cash drawer)

Charge opens the tender screen: the total, large; one button per payment method the register offers. **Cash** shows
quick amounts (exact, the next whole unit, and the next 5, 10, 20, 50 and 100 above the total) and a keypad;
*Change due* updates as amounts are entered, and Complete is live once the amount tendered covers the total. **Card** and
others take an optional reference (last four digits, approval code), stored as the receipt's payment reference. Complete
runs `pos sale complete`; the screen then shows the change due in large type, Print receipt, and New sale. A drawer
connected to a receipt printer opens through the printer driver's "open drawer on print" setting; Bookflow sends no
drawer command.

### Split tender

*Add another payment* on the tender screen takes a second method for part of the total. Following the anchor, a sale
paid two or more ways posts as an **invoice** (terms Due on receipt, same customer, lines and tax) with **one customer
payment per method**, all in one transaction and deposited to the register's deposit-to account; a single-method sale is
always a sales receipt. Make Deposits then sees each payment under its own method (question 6).

### Hold and resume

Hold stores the cart as a held ticket (`pos ticket hold`, optional label; default the time and first item) and starts a
new sale. *Held (n)* lists the company's held tickets, newest first, with register, clerk and total; Resume reopens one
on this device, at today's prices. Held tickets post nothing and commit no stock. Discard asks once. Tickets held more
than 7 days are listed in the day summary.

### Voids and refunds

- **Before Complete**, Clear sale discards the cart; nothing was posted.
- **A completed sale** is voided with `pos sale void` (`pos.void`), which runs `sales-receipt void` (or voids the split
  invoice and its payments together) with a required reason, under the same refusals: a deposited or reconciled sale is
  refused, naming the deposit.
- **A refund** (`pos.refund`) follows the anchor: `pos refund` finds the original sale by scanning or typing the receipt
  number (the printed receipt carries it as a Code 128 barcode), or rings up returned items with no receipt; it posts a
  **credit memo** to the sale's customer (walk-in included) with the returned lines, putting stock back at the
  register's site, and a **customer refund** of that credit, both in one transaction. Cash goes out of the register's
  cash refund account; a card refund records its reference. Store credit instead of money: the credit memo alone.

## 4. Posting: one sale, the same books

`pos sale complete` takes the ticket (lines, customer or none, register, tenders, operation key) and, in one transaction,
calls the **same core function** `sales-receipt post` calls, with `customer` = the chosen customer or the walk-in,
`deposit_to` = the register's account, `payment_method` and `payment_reference` from the tender, `site` = the register's
site, `date` = today in the company's timezone, `source_ref` = `pos:<register>:<ticket>`; then writes one `pos_sales` row
(ticket, register, shift, clerk, receipt or invoice, tendered, change, tenders). The audit entry names the command
`pos sale complete`, the clerk and the interface. The operation key is the ticket's, so a retry after a dropped
connection returns the sale already posted and never posts twice.

Tendered and change are facts on `pos_sales`, not postings: the receipt debits Undeposited Funds for the sale total.

## 5. Day close, shifts and deposits

- **The day summary** (`report pos-day`, an X-report while open): per register and date, sales count, gross sales,
  discounts, tax collected, by payment method (count and amount), refunds by method, voids, held tickets left, and
  *cash to deposit* = cash sales − cash refunds paid from the drawer.
- **Counted shifts** (register setting): `pos shift open` records the opening float; `pos shift close` (the Z-report)
  records the counted cash and shows *expected in drawer* = float + cash tendered − change given − cash refunds, and
  over/short = counted − expected. A closed shift is read-only. One open shift per register.
- **Into Make Deposits.** The summary's *Make deposit* button opens Make Deposits with that day's (or shift's) receipts
  and payments for the chosen methods preselected; an overage is an other-money row from the company's **Cash Over and
  Short** account, a shortage the cash-back row to it (account created on first use, as the anchor's users keep one).
  The deposit is posted by whoever holds `ledger.post`; closing a shift posts nothing.
- **Cash refunds** leave from the register's cash refund account (bank type, because a refund must leave a bank), so the
  bank's deposit line for the day is cash sales less cash refunds and reconciliation clears the deposit and the refund
  together. Question 5 offers the alternative.

## 6. The item picture

- **One field.** `items.picture_attachment_id` (nullable, foreign key to `attachments`, RESTRICT) and
  `items.picture_thumbnail_attachment_id` (nullable). The picture is an ordinary attachment linked to the item, so it
  shows in the item's attachments panel, keeps its history and is copied with the company.
- **Set and clear.** `item picture set <item> FILE` (uploads and links, as `attachment add`, then sets the field;
  `--thumbnail FILE` optional), `item picture set <item> --attachment <id>` (an image already linked), and
  `item picture clear`. JPEG, PNG and WebP; HEIC is refused with "choose Most Compatible in the phone's camera settings,
  or share as JPEG". Capability `item` (write) and `attachment`. `item show` and `item query` return `picture` and
  `thumbnail` (id and URL).
- **Pictures already taken show up.** An item with no picture set shows its most recently linked image attachment, read
  at display time; setting a picture overrides it, clearing returns to it. Nothing is written for this.
- **In the browser** the item page shows the picture beside its name with Take photo / Choose picture (a file input with
  `capture="environment"`, which works over plain HTTP) and Remove. The browser scales the upload to 1600 px on its long
  edge and makes a 320 px thumbnail before sending both, so phone photos stay small. A picture set from the CLI or MCP
  with no thumbnail is shown scaled by the browser.
- **Everywhere.** Item page, the item list (a thumbnail column, on by default when any item has a picture), the item
  reference picker's results, POS boxes, the pick screen's lines and the pack screen's lines. Attachment URLs are
  content-addressed, so pictures are served with an immutable cache header and load once per device.
- **Who sees pictures (2026-10-28, Navigator decision after the increment 1 review).** Reading items is enough: the
  picture route serves under `item` read (`item picture get`), but only an attachment that is an item's chosen picture,
  its thumbnail, or the image it falls back to (or inherits through a merge). Every other attachment still needs the
  `attachment` capability. So the POS clerk (`item` and `pos.sell`) sees pictures on the POS boxes and in lookup.

## 7. Camera scanning (R190)

### The decoder seam

`static/camera-scan.js` defines one interface and knows nothing about any particular decoder:

```js
// A Decoder turns one frame into zero or more codes.
// { name, formats: ['ean_13', 'upc_a', 'code_128', 'qr_code', ...],
//   async available() -> boolean,          // runtime check, no download
//   async load() -> void,                  // fetch and compile its files, once
//   async detect(source) -> [{text, format}] }  // source: HTMLVideoElement | ImageBitmap | HTMLCanvasElement
```

Adapters register themselves: `bookflowDecoders.register(decoder)`. The **order** is a list of names; the first whose
`available()` is true is loaded, lazily, the first time the camera opens. Default order: `native, zxing-wasm`.

- `native`: the browser's `BarcodeDetector` (Android Chrome; absent on iOS Safari, desktop Linux and Windows Chrome and
  Firefox), available when the class exists and `getSupportedFormats()` includes EAN-13 or Code 128.
- `zxing-wasm`: zxing-cpp compiled to WebAssembly (`zxing-wasm` 3.1.5, MIT over zxing-cpp's Apache-2.0; reader 37 KB +
  `zxing_reader.wasm` 967 KB). It led the synthetic benchmark (91 % read, 16 ms median; research `r2aa011`).
- Room for `zbar-wasm` (LGPL-2.1+, 239 KB), `zxing-js` (`@zxing/library`, Apache-2.0, 362 KB, no wasm) and `quagga2`
  (MIT, 1D only): each is one adapter file and its vendored files, added only if a benchmark picks it.
- **Where the order comes from**: the host setting `scan_decoders` (`bookflow serve --scan-decoders`, environment
  `BOOKFLOW_SCAN_DECODERS`), rendered into the page; a register's override (§2); and, for testing on one device, a
  `localStorage` override set from the camera overlay's *Decoder* menu. Changing decoders after the benchmark is a
  setting change plus, for a new one, vendoring its files.
- **Vendored, offline.** Files live in `static/vendor/<decoder>/<version>/` with their licence and a `VENDORED.md`
  giving source URL, version and SHA-256 of each file; a test checks the hashes. No CDN: the page works on a LAN with no
  internet. The host serves `.wasm` as `application/wasm`; the adapter falls back to compiling from an ArrayBuffer if
  streaming compile fails. A safety pass reads each vendored file's provenance before it lands.
- **Formats** asked for: EAN-13, EAN-8, UPC-A, UPC-E, Code 128, Code 39, ITF, QR and DataMatrix (bin and lot labels).

### The camera in the Scan field

Every Scan field (document lines, transfer rows, counts, pick, pack, POS lookup) gains a camera button where the device
has a camera (`navigator.mediaDevices` present, or a coarse pointer). The camera never replaces typing: a handheld USB or
Bluetooth scanner in keyboard mode keeps working exactly as today, and both can be used on one screen.

- **Live camera** (secure context: `localhost`, or HTTPS once gate g0acabd's HTTPS exists): the button opens an overlay
  over the lower half of the screen with the rear camera, a framing box and a torch toggle where the track supports it.
  Frames are decoded about 8 times a second. A decoded code goes through **the same path a typed code takes**
  (`scan.js`'s `look(field, input, text)`: one `barcode lookup`, then the mode's own placing), so a camera scan is
  exactly a scanner scan. On a found item: one short beep (Web Audio, 1,000 Hz, 80 ms; no audio file), a 40 ms vibration
  where supported, the item's name flashes in the overlay, and the overlay stays open for the next scan; the screen under
  it has already moved on (the pick screen to the next line, the POS cart to the new total). On an unknown code: two low
  beeps and the field's own "Assign to an item" message. The same code is not taken again until it has left the frame or
  1.5 s have passed, so holding a can still is one scan; scanning it again adds one. Close, or 60 s with no code, stops
  the camera.
- **No secure context** (plain HTTP on the LAN today): the button opens the phone's camera for **one photo per tap**
  (`<input type=file accept="image/*" capture="environment">`), decodes it, and continues the same way. The overlay says
  why once per session: "Live scanning needs a secure connection. Until Bookflow is set up with HTTPS, each tap takes one
  photo."
- **Silence.** Sound follows a per-device toggle in the overlay (default on); a phone on silent still vibrates.

## 8. Each person's Home (R191)

- **The setting.** `users.home_company_id` and `users.home_path` (a workbench path inside that company, such as `pos`,
  `pick-list`, `report/profit-and-loss`, or empty for the Overview). `user home set --page PATH [--company C]` sets one's
  own (any signed-in person); `--user U` sets another's (`user.manage`); `user home show`, `user home clear`. A path is
  accepted only if it names a workbench page, and the person may open it. In the browser, the account menu's
  *Make this page my Home* sets it from wherever they are, and the user's row in Users & permissions sets it for a clerk.
- **Signing in** goes to Home when set and the person may still open it, otherwise to the Overview with one line: "Your
  Home page is not available to you; showing the Overview." A `next=` deep link still wins.
- **The Home button.** A house icon at the left of the header on every page returns to the person's Home. The menu's
  first entry is renamed *Overview* (R184's business picture); it is not Home unless the person chose it.
- **Starting location is not permission.** Home decides only where a person lands. What they may do is decided by their
  capabilities at the command boundary, through every interface and direct URL.

## 9. Permissions and the POS clerk

### Capabilities

| Capability | Gives | Humans hold it at | Agents |
|---|---|---|---|
| `pos.sell` | `pos sale preview`, `complete`; `pos ticket` hold/list/resume/discard; `pos customer search`, `add`; `pos-box view`; `pos shift open`, `close`; `report pos-day` for own register | standard | none until granted |
| `pos.void` | `pos sale void` | standard | none |
| `pos.refund` | `pos refund` | standard | none |
| `pos.price-override` | typing a price on a POS line | standard | none |
| `pos.layout` | `pos-box create`, `update`, `move`, `remove`; `pos-register` writes | admin | none |

`pos sale complete` posts the sales receipt under `pos.sell` alone, with the sale's facts fixed by the register (deposit
account, site, date today, walk-in) so the capability cannot reach other accounts, dates or documents. This is the gap
the menus can't close today: `sales-receipt post` shares `ledger.post` with journal entries. All land in one permission
layer, `pos-v1`, chained on the tip at merge.

### The POS clerk preset

A preset in the R187 sense (an editable list applied in one step): role `readonly`, grants `pos.sell`, and denies of
every company capability the `readonly` role holds except `company` and `item`, computed from the live catalog when
applied, with Home = POS. (Corrected in increment 4: the clerk keeps `company` read, because every page runs `company
show`.) Its effective set is therefore `company` and `item` (read) and `pos.sell`: ring up, look up items and customers
by name or phone, hold, count the drawer. Reads that name customers (shipments, their returns, pick lists) also need
`customer`, and an item read shows costs only with `ledger.read` and vendors only with `vendor`, so `item` alone shows
an item's name, price, stock, barcode and picture. Tests pin the deny list and the exact commands a clerk is admitted
to on the current catalog, so a capability or command added later shows up as a failing test, not as a page a clerk
can read. The membership records the preset (a marker grant, `preset.pos-clerk`), and `permission activate` extends
the denies of every such membership with whatever company capability a newer catalog gives `readonly`, so a clerk
saved before a later layer never gains what it adds. *Clerk with returns* adds `pos.void` and `pos.refund`.

### Menus follow capabilities (closing today's gaps)

Today a role-plus-denies member still sees every menu section, home tile, finder entry, section link, attachment control
and write form their role allows, and learns of a deny only on a refusal page or on submit. One change fixes it at the
source: `company_permits` (`pages.py:932`) decides from the member's **effective capabilities** (what
`membership effective` computes, once per request) instead of role rank. Then:

- side-menu sections, section links, home tiles, the noun grid and the finder list only what the person may run;
- a section with nothing left disappears; with no sections left (the clerk) the side menu and the finder's fixed entries
  (Overview, sections, All commands) disappear and the header shows Home, the company name, Held and Sign out;
- *Users & permissions* shows only to holders of `membership.grant` or `user.manage`, *Advanced tools* only to those
  holding some write capability, and *Administration* and *System activity* only to hub admins;
- write forms for a denied capability answer the refusal page up front, as a too-low role does today;
- attachment and note controls follow `attachment`; Overview figures already drop denied reports.

The command boundary is unchanged and stays the guarantee: a clerk typing `/c/<id>/invoice/new` gets the refusal page,
and the same clerk with a token gets `E_PERMISSION` from the CLI or MCP.

## 10. Agents

POS is a human checkout; an agent posts sales with `sales-receipt post` as today, and nothing about that changes. What an
agent does with POS, each with the capability granted: arrange the layout ("put the coffees in a Drinks group with the
mug picture": `pos-box` commands), set item pictures from files it is given (`item picture set`), and answer questions
from `report pos-day` and `pos_sales` facts on `sales-receipt show` ("how much cash should be in the drawer?", "what did
register 2 take on Saturday?"). The command finder answers "point of sale", "till", "register", "checkout", "Z report",
"end of day", "cash drawer", "float", "over short", "photo", "picture". An agent that holds `pos.sell` can complete a POS
sale, but its guide says to use `sales-receipt post`.

## 11. Offline and poor network

- **Supported:** the cart lives in the page and is mirrored to `localStorage` per register on every change, so a reload,
  a dropped connection or a locked phone loses nothing. Tapping boxes and adding lines works without the host, from the
  item data already loaded; lookups of items not yet seen do not.
- **Not supported:** posting without the host. With no answer, the total reads "not confirmed", Charge is disabled, and
  the bar says "No connection to Bookflow. This sale is kept here; finish it when the connection returns." Complete
  retries with the ticket's operation key, so a sale whose answer was lost is found, not posted twice. There is no
  offline queue of sales: numbering, stock and tax are decided by the host. Card payments are taken on the card terminal,
  which works independently.

## 12. Layout, speed and printing

**Phone (390 px):** header (Home, register, Held badge); lookup with camera button; breadcrumb; boxes; a sticky bar at the
foot, "3 items · $24.50 · Charge". Tapping the bar slides the cart up as a sheet (lines, customer, Hold, Clear, Charge).
**Tablet and desktop (768 px and up):** boxes on the left (about 60 %), the cart always visible on the right, Charge at its
foot. Touch targets at least 44 px; the tender keypad's keys at least 64 px. Every screen is checked at 390, 768 and
1440 px.

**Speed targets, measured on a mid-range Android phone over Wi-Fi to the LAN host, demo company:** POS home with 40
boxes interactive in 1.5 s cold and 0.5 s warm; a tap shows its line within 100 ms; the server total within 400 ms;
a lookup's results within 300 ms; a camera scan to cart line within 500 ms of the code entering the frame; Complete to
the change screen within 1.5 s. Each is measured in the increment that builds it and recorded in As built; a missed
target is reported with its measurement, not hidden.

**The receipt.** The first receipt-width output of the [print templates](print-templates.md) direction, with no editor:
`/c/<id>/pos/receipt/<sale>` renders HTML at the register's width (`@page { size: 80mm auto; margin: 0 }`, or 58 mm), so
the length follows the content and a thermal printer prints it through its ordinary driver: company name, address and
phone, date and time, register and clerk, number (and its Code 128 barcode), customer if named, each line (name,
quantity, price, amount; long names wrap, nothing is cut), subtotal, discounts, tax by rate, total, each tender, change
due, the register's message. Reprint from the sale. A PDF at the same width (reportlab, height from content) serves
saving. Emailing a receipt waits for R112's transport and is not offered.

## 13. Storage

One company migration per increment that needs one, numbered after main's head at merge, extending both demo seeds.

| Increment | Tables and columns |
|---|---|
| 1 Picture | `items.picture_attachment_id`, `items.picture_thumbnail_attachment_id` |
| 3 Home | hub: `users.home_company_id`, `users.home_path` |
| 4 Layer | `company_info.pos_enabled`; `pos_registers` (with revisions, as other lists) |
| 5 Sale | `pos_tickets` (id, register, clerk, label, status `open`, `held`, `completed`, `discarded`, lines JSON, customer, version); `pos_sales` (ticket, register, shift, clerk, document type and id, tendered, change, tenders JSON; insert-only) |
| 6 Layout | `pos_boxes` (id, parent group or home, position, kind `group` or `item`, item, label, colour, picture attachment, view style for groups, version); `company_info.pos_home_view_style` (default `block`) |
| 10 Day close | `pos_shifts` (register, opened by and at, float, closed by and at, counted, expected, over/short; insert then one close) |

Tickets and boxes are working state, updated in place under `expected_version` with an audit entry per write; nothing in
them posts. `pos_sales` and closed shifts are never updated.

## 14. Acceptance criteria

What these items are for: a shop rings up sales on a phone or tablet as easily as a dedicated till, a clerk can do that
and nothing else, and the books are exactly what the sales receipt form would have made.

1. **Same books.** For generated carts (stocked, service, discount and subtotal items, price levels, R110 rules, taxable
   and exempt customers, walk-in and named), a POS sale and the same input given to `sales-receipt post` on a twin
   company leave identical ledgers, stock, tax and Undeposited Funds; a split-tender sale equals the hand-entered
   invoice plus payments.
2. **The clerk.** A person given the POS clerk preset signs in on a phone and lands on the POS; the page shows no
   accounting menu, tile or finder entry; Home returns to the POS from any page reached; `/c/<id>/invoice/new`, a report
   URL and the CLI and MCP equivalents are all refused.
3. **One picture.** A photo taken on the item page appears in POS, the item list, the picker, and on the pick and pack
   screens with no further step; an item whose only picture was attached last year shows it in POS.
4. **Scanning.** On `localhost` (and on HTTPS once it exists) the phone camera scans ten items in a row into the cart
   with a beep each and no taps between; on plain LAN HTTP each tap takes one photo and the overlay says why; a USB
   scanner works unchanged on every Scan field; the pick screen advances line to line from camera scans.
5. **Decoder by setting.** Changing `scan_decoders` from `native,zxing-wasm` to `zxing-wasm` changes which decoder runs
   (shown in the overlay's Decoder menu) with no code change, and works with the host offline from the internet.
6. **The drawer ties.** For a shift with float, cash and card sales, change given, a cash refund and a counted shortage,
   expected cash, the Z-report, and the deposit made from it tie to the ledger, and Undeposited Funds is left holding
   only the card tenders.
7. **Nothing lost, nothing doubled.** A sale held on one device resumes on another; a Complete whose answer was lost
   and retried posts once; a reload mid-sale keeps the cart.
8. **Off means off.** A company that never turns POS on reads exactly as before, with no POS surface anywhere.
9. **Fast enough.** §12's targets are measured and recorded.

**Measure** (for the roadmap): closing-trial scenes passed first time, out of the scenes in §15's closing trial.

## 15. Build increments

Ordered so the pieces pick and pack also gains (pictures, the camera) land first, then the menus and Home a clerk needs,
then the POS itself. Every increment keeps a company without POS reading exactly as before.

1. **Item pictures (M).** Migration; `item picture set/clear`; picture and thumbnail on `item show/query`; the implicit
   latest-image rule; item page photo controls with browser scaling and thumbnail; thumbnails in the item list, picker,
   pick and pack lines; immutable caching. *Done when* a phone photo taken on the item page shows on the item list and
   the pick screen; an item whose only image was attached before this increment shows it; HEIC is refused with the
   message. *Tests:* set, replace, clear and fall back; a CLI picture with no thumbnail; refusal of non-images; a browser
   test of Take photo at 390 px.
2. **Camera scanning seam (L).** `camera-scan.js` with the Decoder interface and registry; `native` and `zxing-wasm`
   adapters; zxing-wasm 3.1.5 vendored with `VENDORED.md` and hash test; `scan_decoders` host setting and overrides;
   `.wasm` media type; the overlay with beep, vibration, de-duplication, timeout, torch and sound toggle; the photo
   fallback with its message; the camera button in every existing Scan field. *Done when* criterion 4's localhost and
   HTTP halves and criterion 5 pass on the pick and count screens. *Tests:* Chrome with
   `--use-fake-device-for-media-stream` and a video of barcodes scans into the pick screen in order; the photo path
   decodes a fixture photo; an unknown code buzzes and changes nothing; the keyboard scanner tests still pass.
3. **Menus follow capabilities, and Home (M).** `company_permits` on effective capabilities and every place listed in §9;
   `users.home_*`; `user home set/show/clear`; login landing and its fallback line; the Home button; *Make this page my
   Home*; Overview renamed in the menu. *Done when* a `readonly` member denied `reports` sees no Reports section and no
   report tiles, and a person whose Home is a report lands there and returns to it from any page. *Tests:* the page and
   finder sets for three members (standard, readonly, readonly with denies); a revoked Home falls back.
4. **The POS layer (M).** `pos_enabled` and its refusals; `pos_registers` and `pos-register` commands; Main register and
   the walk-in customer; the `pos-v1` capabilities; the POS clerk presets in Users & permissions with the catalog test;
   the menu entry *Point of sale* under Customers and its home tile, both only when on. *Done when* criterion 2's
   refusals hold for a clerk and criterion 8 holds. *Tests:* switch on and off with a held ticket; preset effective set.
5. **Ring up and complete (L).** The POS screen at 390 and 1440 px with the starting layout, Block style, lookup and the
   Scan field; cart with `localStorage` mirror; `pos sale preview`; customer chip with search and add; single-tender
   Charge with cash quick amounts and change; `pos sale complete` and `pos_sales`; the change screen. *Done when*
   criterion 1 holds for single tender and criterion 7's retry and reload hold. *Tests:* the twin property on generated
   carts; a retry with the same operation key; a browser test ringing up by tap, lookup and scan.
6. **Layout and view styles (M).** `pos_boxes`, `pos-box` commands, the editor, group pictures and colours, the four
   styles with Block and List first, auto-saved per group and home. *Done when* a group set to List reads List on another
   device after reload, and a box removed leaves its item untouched. *Tests:* nesting, reorder, an item in two groups;
   each style at 390 and 1440 px.
7. **Hold, void and price override (M).** `pos_tickets` hold, list, resume and discard; `pos sale void`;
   `pos.price-override`. *Done when* criterion 7's hold-on-one-device, resume-on-another holds, and a deposited sale's
   void is refused naming the deposit.
8. **Receipt (S).** The receipt route at 80 and 58 mm, its barcode, reprint, and the PDF. *Done when* a 1-line and a
   60-line sale print at 80 mm with every line and the totals after them. *Tests:* PDF height grows with lines.
9. **Refunds (M).** `pos refund` with receipt scan and no-receipt returns; credit memo and refund in one transaction;
   store credit. *Done when* refunding one of three lines returns its stock and cash from the refund account, and its
   books equal a hand-entered credit memo and refund.
10. **Day close (M).** `report pos-day`; shifts with float, count, expected and over/short; Make deposit hand-off with
    Cash Over and Short. *Done when* criterion 6 holds.
11. **Split tender (M).** Tender screen's second payment; invoice plus payments in one transaction; void and refund of a
    split sale. *Done when* criterion 1 holds for split sales.

**Closing trial.** A person on a phone and a tablet over LAN HTTP, and once on `localhost`: sets up a register, takes item
photos, builds a two-level layout, rings up ten sales (walk-in and named, cash with change, card, one split, one held and
resumed), refunds one, closes the day and makes the deposit; a clerk signs in and sees only the POS; an agent arranges a
group and answers "what should be in the drawer?". Checked afterwards: criteria 1–9, against the twin company.

### As built: increment 1 (item pictures)

- **Storage.** co0115 on co0114 (renumbered after main's head at the merge of 2026-10-28; R107 bulk edit renumbers
  after it if it lands later): `items.picture_attachment_id` and `items.picture_thumbnail_attachment_id`, each a
  foreign key to `attachments` with RESTRICT, and an index on each. Neither demo seed carries a picture (a seed has no
  image files). Pixel sizes are checked, not stored: storing them would add columns to `attachments` (whose output
  copies every column) for no reader yet.
- **Commands.** `item picture set <item> PATH [--thumbnail-attachment ID]` (a binary transfer, as `attachment add`),
  `item picture use <item> <attachment> [--thumbnail-attachment ID]` (an image already uploaded; linked to the item if
  it is not), `item picture clear <item> [--keep-file]`, and `item picture get <attachment>` (a binary download).
  The contract's `set --attachment` is `use`, because a transfer command always takes a body. `set`, `use` and `clear`
  are capability `item` (standard) with `attachment` (standard) beside it; `get` is `item` (member) alone and serves
  only a file some item shows as its picture or thumbnail (any other is not found). Layer `item-picture-v1` on
  `capabilities-v1` (838 commands, descriptor fb1968ec). The bytes decide: JPEG, PNG or WebP by their first bytes,
  stored under that type; HEIC (and AVIF) refused with the camera-settings message; a picture is at most 20,000,000
  bytes, a thumbnail 1,000,000; the width and height are read from the header (PNG IHDR, WebP VP8, VP8L or VP8X, a
  JPEG's SOF segment within its first 256 KiB) and a picture over 12,000 px on a side or 50 megapixels in all, or
  whose size cannot be read, is refused. Neither `set` nor `use` takes the picture as its own thumbnail. Replacing a
  picture unlinks the old picture and its thumbnail from the item (bytes and history kept), and `clear` unlinks the
  picture and its thumbnail, so Remove removes and nothing replaced comes back; the item then shows its newest
  remaining image. `attachment unlink` refuses the link of a chosen picture or thumbnail ("remove it with
  `item picture clear`"), so a picture never dangles. A merged-away item's picture changes only by undoing the merge
  (`E_RECORD_IN_USE`, declared on the three writes).
- **Reading.** `item show`, `item list` and every item output carry `picture` and `thumbnail` (attachment id, sha256,
  media type, size, `chosen`, and `url` `/c/<company>/picture/<id>/<sha256>`); `item query` offers both as columns.
  With nothing chosen, `picture` is the newest active linked attachment whose stored type is JPEG, PNG or WebP and that
  is not a thumbnail; with none of those, a merge's survivor shows the chosen picture (and thumbnail) of the newest
  item merged into it (`chosen: false`). A thumbnail is any item's current thumbnail or any file ever linked to an item
  under the caption "Item picture thumbnail": each thumbnail's link carries that caption (an existing link is
  recaptioned when the file becomes one), so a former thumbnail is never a fallback, even linked again. The thumbnail
  shows only beside a chosen or inherited picture.
- **Browser.** The item page shows the picture beside the name with Take photo (`accept="image/*"
  capture="environment"`), Choose picture and Remove, posted through htmx. `static/item-picture.js` decodes with
  `createImageBitmap` (EXIF orientation applied), sends a 1600 px JPEG and a 320 px JPEG thumbnail; a file the browser
  cannot decode goes up as it is so the server's refusal shows; a photo no larger than 320 px goes up with no thumbnail
(the server also keeps none when both bodies are the same bytes); a post htmx does not swap (refused, 404, 5xx, no
  network) shows what happened. The thumbnail goes up first as an ordinary file (`attachment add`, caption "Item
  picture thumbnail", its type from its bytes), then the picture (`item picture set`); if the picture is refused the
  thumbnail is unlinked again. The upload refuses a post over the limits before reading it (Content-Length, then the
  stream capped, then `max_part_size`), and the upload and Remove run their work in the thread pool. Images are
  served at `/c/<company>/picture/<attachment>/<sha256>` through `item picture get`, streamed chunk by chunk, only
  when the bytes are an image and the hash matches, with the sniffed type, `Cache-Control: private,
  max-age=31536000, immutable`, `nosniff` and `Content-Security-Policy: default-src 'none'; sandbox`; an image that
  will not load shows "No picture". Thumbnails show in the item list (a Picture column, present when any item on the
  page has a picture; on phone cards too), the pick screen, the pack screen and the fulfillment worksheet; a picture
  with no thumbnail (set from the CLI or MCP, or a fallback) shows a placeholder there, never the full picture (no
  server-side scaling, so no new dependency). The item reference picker is a datalist, which cannot draw images: each
  item option carries `data-picture` for the POS picker.
- **Imports and bulk edit.** Not importable: `item picture set` and `use` are listed "not-a-record" and `clear`
  "destructive" in `import_kinds.NOT_IMPORTABLE`; a picture is a file, never a cell of a row. Not bulk-editable for
  the same reason (R107 bulk edit takes item fields from the `item update` input, which has no picture).
- **Known limits.** The fallback reads the stored media type, so a non-image uploaded under an image name can be the
  fallback; the workbench refuses to serve it (404) and the page shows "No picture". A JPEG whose frame header lies
  beyond its first 256 KiB is refused as unreadable (save it again). A caption "Item picture thumbnail" typed on an
  ordinary file keeps it from being a fallback.
- **Tests.** `tests/test_item_pictures.py` (set, replace, clear and fall back; a CLI picture with no thumbnail;
  refusals; `item query`), `tests/test_item_pictures_review.py` (the review's fixes: set A, set B, Remove shows no
  picture; pixel caps from PNG, JPEG and WebP headers; own-thumbnail refusal; merge inheritance; the thread pool; an
  oversize post refused before it is read; a reader of items without attachments; the list placeholder; the
  thumbnail's type from its bytes), `tests/test_item_pictures_surfaces.py` (four transports, `get` included), and
  `tests/test_item_pictures_browser.py` (real Chrome at 1440 and 390: Take photo, the HEIC message, scaling to
  1600x1200 and 320x240, headers, the item list, the pick screen, Remove; nothing scrolls sideways; a photo with EXIF
  orientation 6 stored upright; a broken image shows "No picture"; an unswapped failure shows its message).

### As built: increment 2 (camera scanning)

- **The seam.** `static/camera-scan.js` holds the registry (`bookflowDecoders`: `register`, `names`, `order`,
  `choose`, `active`, `setRegisterOrder`, `setDeviceOrder`) and the overlay; it names no decoder. Each adapter is one
  file that registers itself: `scan-decoder-native.js` (BarcodeDetector; available when the class exists and offers
  EAN-13 or Code 128) and `scan-decoder-zxing-wasm.js`. The first name in the order whose `available()` is true is
  loaded the first time the camera opens; one that fails to load is skipped for the next, and when none works the
  overlay says so and names why each was passed over. Formats asked for: EAN-13, EAN-8, UPC-A, UPC-E, Code 128, Code
  39, ITF, QR and DataMatrix, reported in BarcodeDetector's names whichever decoder read them.
- **The order.** First match wins: this device (`localStorage` `bookflow.scan.decoders`, set from the overlay's
  *Decoder* menu: "Automatic (…, from the host)" or one decoder only); the register (`setRegisterOrder`, a seam the
  register increment calls with §2's override; nothing calls it yet); the host (`<meta name="bookflow-scan-decoders">`
  on every workbench page, from `bookflow serve --scan-decoders`, else `BOOKFLOW_SCAN_DECODERS`, else
  `native,zxing-wasm`); then that default. `core/scan_decoders.py` refuses an unknown name, a repeat or an empty list
  (`E_VALIDATION`) when the host starts; `serve` prints the order it carries. No command, permission layer or
  migration: the setting is the host's, like `--public-origin`.
- **Vendored zxing-wasm 3.1.5.** `static/vendor/zxing-wasm/3.1.5/`: the IIFE reader (`zxing-reader.js`, 36,583
  bytes), `zxing_reader.wasm` (966,895 bytes) and the MIT `LICENSE`, with `VENDORED.md` (source, tarball integrity,
  SHA-256 of each file, and the safety pass). The adapter replaces the module's `locateFile` (its default is
  jsDelivr) and `instantiateWasm`: streaming compile from the vendored path, else fetch the bytes and compile from an
  ArrayBuffer. `GET /static/vendor/<package>/<version>/<file>` serves `.js`, `.wasm` (as `application/wasm`), `.md`
  and a licence file, cached as immutable; plain path parts only, no link on the way, regular files only, everything
  else 404. The flat `/static/<name>` route now answers only regular files (a folder was a 500). Frames over 1,280 px on a side are scaled down before
  decoding.
- **The overlay.** Fixed over the lower half of a phone (a 28 rem panel at the lower right from 768 px), appended to
  `<html>` rather than `<body>` so it survives the pick and count screens' whole-body swaps. Rear camera
  (`facingMode: environment`, 1280x720 ideal), a framing box, Torch when the track reports `torch`, about 8 decodes
  a second, Close and Escape. A code read goes to `bookflowScan.scan(field, text)`, which queues it behind any typed
  scans and runs scan.js's own `look` (one `barcode lookup`, then the mode's placing); the only difference from a
  typed scan is that it does not move focus (on a phone that would raise the keyboard over the camera). Found: one
  1,000 Hz 80 ms beep (Web Audio, made on the tap that opened the overlay), a 40 ms vibration, the field's own message
  in the overlay. Unknown or refused (the field's status is not "ok", e.g. "Assign to an item" or "Nothing was
  added"): two 330 Hz 120 ms beeps and a 120-80-120 ms vibration. Sound is a per-device checkbox (default on);
  vibration ignores it. Sixty seconds with no code stops the camera and offers *Start again*. The pack screen, whose
  scan input is a plain form field rather than a Scan field, gets the same button: the code is put in its field and
  the form submitted, as Enter would.
- **De-duplication (settled by the Navigator for Kabe's "holding a can still is one scan; scanning it again adds
  one").** Memory is kept per code: every code in view is taken once, whatever else is in the frame (a bin label beside
  an item, a UPC beside a QR), and is taken again only after that code itself has left the frame, unread for 5 decodes
  in a row (about 0.6 s at 8 a second). There is no time-based re-take while a code stays in view, so a missed frame
  never re-arms it. This settles §7's "left the frame or 1.5 s" in favour of "left the frame". The constants
  (`FRAME_MS`, `LEFT_FRAME_DECODES`, `IDLE_MS`, `BROKEN_DETECTS`) sit at the top of camera-scan.js for the phone trial.
- **Lifecycle.** Each open and close starts a new session: a code is delivered to the field it was read for, and a
  code read before Close is dropped, never delivered (or beeped) after it. Only the newest start owns the camera: a
  start replaced by another (a double tap) or by Close asks for no camera, stops any stream it is handed late and runs
  no decode loop, so once Close returns every track the page opened has ended. If the field the overlay serves has
  gone (a pick list completed, the page left), the overlay says so and stops the camera. A decoder whose `detect()`
  throws 10 times in a row is passed over for the next in the order, with a message.
- **Keyboard scanner beside the camera.** While the overlay is open, Escape closes it, and a keyboard-mode scanner's
  keys that land on no text field are gathered and entered in the Scan field on Enter (scan.js's own look-up) without
  focusing it, so a phone's keyboard never rises over the camera. An unknown camera code is left in the field selected,
  so the next scan replaces it. The pack screen's `autofocus` is stripped from swapped-in pages while the camera is
  open. With the camera open the page gets bottom padding the overlay's height, so its last lines scroll clear of it.
- **No secure context.** `window.isSecureContext` (with `getUserMedia`) decides. Otherwise the button opens the
  phone's camera for one photo (`accept="image/*" capture="environment"`), decoded through the same decoder
  (`createImageBitmap` with EXIF orientation, then `detect(canvas)`), then the same path. The overlay shows "Live
  scanning needs a secure connection. Until Bookflow is set up with HTTPS, each tap takes one photo." once per
  session (`sessionStorage`), adding "Live scanning works at <origin>." when the host has an https public origin
  (`/secure-context`). A photo with no barcode says so and buzzes. The button shows where `navigator.mediaDevices`
  exists or the pointer is coarse (a phone on plain http has no `mediaDevices`).
- **Tests.** `tests/test_camera_scan_browser.py`, real headless Chrome at 390 px, the fake camera playing a Y4M made
  at test time by `tests/barcode_images.py`, Chrome resolving no host but 127.0.0.1, and every tone and camera stream
  the page starts recorded: live on the pick screen, the bin label and the sealant three times fill a two-line list
  line after line with a 1,000 Hz tone each; live on the count screen, two labels in one frame for 4 s are each
  counted once; the photo path on the pick screen (page made insecure) with the message once a session, and an
  unknown code playing two 330 Hz tones, leaving the list unchanged and the code selected in the field; the decoder
  following `BOOKFLOW_SCAN_DECODERS` from host start to page (default runs a stand-in native, `zxing-wasm` runs
  zxing-wasm) and the device override; a decoder that will not load, and one whose detect keeps throwing, passed over;
  close during start-up asking for no camera, a double start and close-and-reopen leaving one live camera and every
  track `ended` after Close, codes queued before Close not delivered, a keyboard scanner entering a bin label with the
  overlay open, Escape closing it. `tests/test_camera_scan_vendored.py`: every file under `static/vendor` sits in a
  folder with a VENDORED.md listing it with its hash; the reader's own wasm hash; no URL in the camera files; the
  setting's resolution and refusals; `bookflow serve --scan-decoders` checked before anything opens; the vendor and
  flat static routes refusing traversal, encoded separators, links and unlisted suffixes. The keyboard tests
  (`test_barcode_scan_browser`, `test_pick_list_browser`, `test_inventory_count_browser`, `test_pack_browser`) pass
  unchanged.
- **For Kabe's phone trial (not verified here).** Real cameras on Android Chrome (native) and iOS Safari
  (zxing-wasm), over `localhost`/HTTPS and plain LAN http; ten items in a row into a cart once the POS exists
  (criterion 4's cart half waits for increment 5); the beep's audibility and the vibration; torch on a real track;
  the de-dup constants; and §12's "camera scan to cart line within 500 ms", which is a phone measurement and is not
  recorded yet. The headless run only shows the path works: the fake camera is a clean synthetic picture.

### As built: increment 3 (menus and Home)

- **Menus follow capabilities.** The workbench reads the reader's effective capabilities once per request, when
  `company show` first runs (`membership effective`; for an agent's token, `permission_policy.execution` per
  requirement, the actor-and-principal intersection the boundary enforces), and reuses the answer across requests
  per (reader, principal, company) only while the hub's authority generation token is unchanged. `_role_allows`
  decides a company command from it: the command's own (capability, threshold) and each resource requirement must be
  admitted, exactly the pairs `core.dispatch.authorize` checks; a legacy root, a hub command or a pair the catalog
  does not list falls back to the role. One predicate then decides every offer: `company_permits` returns a
  `home.Reader`, and the side menu, section links, Overview tiles, the noun grid and All commands, the finder,
  record controls (notes, attachments, activity) and `user home set` all ask it.
- **What a page needs.** A tile or link names its commands; a page often runs more on GET (a form's item and
  custom-field lists, a site page's quantity report). Those are declared per page in `home.PAGE_RUNS`, recorded by
  opening every declared page; `tests/test_menus_and_home.py::test_every_page_runs_only_what_it_declares` opens them
  again and fails on any command a page runs that is not declared, so the declaration cannot drift from the pages
  (chosen over guards in each page, which nothing would check). Guarded reads are declared too: hiding a page is the
  lesser error. A page running no command of its own needs what `home.PAGE_FLAGS` says: Users & permissions needs
  `membership.grant` there or `user.manage` (on section pages and in the finder as in the side menu); All commands
  and Advanced tools need some company write; a `_group/<slug>` link needs that section offered.
- **Sections and the header.** A section shows only while it has something the reader may open (its records, a
  "+ New", list or task link, Banking's registers; Reports and Audit a command of their nouns). With none left the
  side menu, the finder, Find transactions, Switch company and the account menu go, and the header shows Home, the
  company and Sign out (Held comes with the POS): a member holding only `company` (which every page needs for
  `company show`) gets it. Overview panels with no tile, and *Start a transaction* with no quick action, are not
  shown. *Administration*, *System activity*, the finder's Installation entries and the company picker's link show
  only to installation administrators; every signed-in person keeps *Your tokens* in the account menu. A hub page
  filters the last company's links the same way. The command boundary is unchanged.
- **Home.** Storage is hub0017 on hub0016: a new table `user_homes` (user_id primary key, company_id, path, version,
  set_at, set_by, set_via) rather than the `users.home_*` columns sketched in §8, so setting a Home never moves a
  user's version, which the permission audit reads as part of a person's authority, and no existing table changes.
  No company migration. Commands `user home set --page PAGE [--company C] [--user U]`, `user home show [--user U]`,
  `user home clear [--user U]`: hub, capability `user`, threshold `authenticated`; one's own is self-service for any
  signed-in actor; for anyone else `hub.capabilities.require_hub(s, "user.manage")` (the check `user deactivate`
  uses) runs before the name is looked up, so who exists is answered only to those who may manage them. `user.manage`
  may read an agent's Home but not set or clear one. The output's field is `page` (a field named `path` is redacted
  from non-administrators as a filesystem path). Layer `user-home-v1` on `bank-line-details-v1` (850 commands, descriptor
  e3ca5ecf). `--page` is the part after `/c/<company>/` or the whole path (the company then comes from it, and a
  `--company` naming the same company by name agrees). A page is accepted only if `home.page_commands` resolves it
  (the Overview, `_all`, `find`, `_registers`, `audit`, a `_group/<slug>`, any destination a tile or section link
  declares, or a noun's list, `<noun>/<verb>`, record or record-verb page) and the target's `Reader` opens it; the
  same check runs whenever the Home is used.
- **Landing.** `/` (signing in without `next=`, the brand link, the Home button) goes to the Home when it is
  available; otherwise to the Home's company Overview with "Your Home page is not available to you; showing the
  Overview.", or, when that company is gone and the picker shows, "Your Home page is not available to you; choose a
  company."; with no Home set it lands as it always has. `next=` still wins. The Home button is a house at the left
  of the header on every signed-in page; the Overview menu entry keeps its name and has its own icon. *Make this page
  my Home* in the account menu posts the current path through htmx; a person's row in Users & permissions has Set
  Home and Clear Home for `user.manage`.
- **Settled choices.** (1) Every company command page, read or write, answers the refusal page up front when the
  reader's capabilities refuse it. (2) Note controls follow `note`, attachment controls `attachment` (§9 said both
  `attachment`; each follows the capability its command checks). (3) All commands, in the finder and Settings, needs
  a write like Advanced tools (reversing the first build's choice, which kept it while any section stood and so kept
  every member out of the minimal header). (4) Setting another's Home is `user.manage` only. (5) A Home names a page,
  not a record's existence; a page switched off by a company preference is judged by capability alone. (6) Agents may
  set their own Home; nobody may set an agent's.
- **§9 missed.** A POS clerk keeps the Items section while the preset keeps `item` read, so the clerk's header is
  minimal only if increment 4 decides `item` alone does not open Items; the minimal header itself is reached today by
  a member holding only `company`. Pages run more than their tiles name (now declared). Users & permissions appeared
  in section pages and the finder to everyone. The finder's Installation entries were offered to everyone.
- **Tests.** `tests/test_menus_and_home.py` (the page and finder sets for standard, readonly and readonly denied
  `reports`; every offered finder entry, section link and accepted Home answering 200 for those three; the page
  declarations against what pages run; the minimal header; a denied write form refused up front; a report Home
  landed on and returned to, `next=` winning; revoked Homes falling back, including a page that runs an undeclared
  report; a permission change seen on the next request; agents through real tokens; no enumeration; the hub
  address with URL characters; hub0017's frozen DDL), `tests/test_user_home_surfaces.py` (Python, CLI, HTTP and
  MCP alike) and `tests/test_home_browser.py` (Chrome at 390 px: Make this page my Home, then the Home button).

### As built: increment 4 (the POS layer)

- **The switch.** `company_info.pos_enabled` (migration co0118 on opening's co0117),
  off by default, set with `company update --pos-enabled` (admin) and shown in Company settings under a new *Sales*
  group ("Use point of sale"). While it is off every `pos-register` command refuses with E_FEATURE_DISABLED (`feature
  pos`, `setting pos_enabled`, `enable_command company update`), `home.features_offer` and the pages' feature check
  hide every `pos`, `pos-register`, `pos-box` and `pos-ticket` command, `/c/<id>/pos` answers the switch's message,
  `user home set --page pos` is refused, and nothing else changes (criterion 8). Turning it off runs
  `pos_layer.check_turning_off`, which refuses nothing yet: held tickets (increment 7) and open shifts (increment 10)
  each add their check there, so the "off with a held ticket" half of this increment's test moves to increment 7.
- **Registers.** co0118 adds `pos_registers` (common columns; name and name key, unique; site; deposit account;
  payment-method ids as a JSON list in order; walk-in customer; refund account, nullable; receipt width 80 or 58;
  receipt message; count_shifts; decoder_order; never deleted). `pos-register create`, `update` (versioned), `show`
  and `list`. Turning POS on the first time makes **Main** in the same transaction as the switch: the default site,
  Undeposited Funds, the active cash, then card (credit and debit), then check methods, each kind by name, the first
  active bank account by name for refunds (null, with a warning, when there is none), 80 mm, no shifts. Turning it
  off and on again finds the registers where they were. A deposit account must be Undeposited Funds or a bank; a
  refund account a bank; `decoder_order` is checked by `core.scan_decoders.normalize`. The selector field is
  `pos_register` (a field named `register` shadows a pydantic attribute). No register deactivate yet.
- **The walk-in customer (K2 settled: a sale works with or without a customer).** Each register has its own, made
  with it through `parties.plan_party_create`: "Walk-in" for Main, "Walk-in (<register>)" for another, a number added
  when the name is taken; or an existing customer named by `walk_in_customer`. It is an ordinary customer record
  (lists and reports show it, so sales by customer shows one Walk-in line); "hidden" means the POS screen and the
  receipt never show it, which increments 5 and 8 honour. It takes no tax code of its own, so a sale uses the
  company's defaults. A walk-in a register makes carries `customers.pos_walk_in` (co0118): it is never merged (either
  side) and a bulk edit leaves it out of every selection, and reports can group its lines. An existing customer named
  as a walk-in gets no flag; the register-reference blocker guards it. *For increment 9:* store credit given to a
  walk-in pools on that one customer across every unnamed buyer, so a refund to store credit needs a named customer.
  Turning POS on says what it made in `company update`'s warnings ("Made register Main and customer Walk-in ...",
  and the register's own warnings).
- **Capabilities (layer `pos-v1` on `opening-draft-v1`, 866 commands, descriptor 3d55aae9).** `pos.sell`, `pos.void`,
  `pos.refund` and `pos.price-override` at threshold `member`, held by default by standard, admin, owner and
  installation administrators, so `readonly` holds one only by an explicit grant; `pos.layout` at `admin`.
  `pos-register show`/`list` need `pos.sell`, `create`/`update` `pos.layout`. Unlike the capabilities-v1 acts, each
  POS command names its capability itself, so a person is admitted, or refused by a deny, through the policy.
  Agents: `hub.capabilities.capability_for` maps any `pos*` command to its pos capability, so the dispatcher's gate asks
  an agent for its own explicit grant and its principal's holding (`reason: capability_not_granted` until granted);
  `user_homes.effective_in` admits a pos requirement for an agent only with an explicit grant, so its pages agree.
  Before permissions are activated there are no grants, so the roles decide and a seller needs the standard role
  (`pos_layer.legacy_required_role`, asked by the commands, the pages and `user home set` alike). The gate, the
  off-switch and the menus find a POS command by its capability (its own or a resource requirement in `pos.*`),
  never by its noun, so a later `report pos-day` is gated and hidden like the rest.
- **Reads that name customers (review H1).** `pos-v1` also adds `customer` (member) to the shipment, shipment-return
  and pick-list reads (`show`, `query`, `history`), for everyone, since those records name customers. An item read
  (`item show`, `list`, the item list query and its owned collections, each write's echoed record) shows costs and
  values (`cost`, `average_cost`, `inventory_value`, a fixed asset's figures, vendor profiles' costs) only to a holder
  of `ledger.read`, and vendors (preferred vendor, vendor profiles, a fixed asset's vendor, a tax agency) only to a
  holder of `vendor`; otherwise they are null (`company/item_concealment.py`; sorting or filtering the item list by
  cost is not yet refused). Two `company` reads a clerk has no business with also gain the capability of the people
  who use them, at `member` so nobody who used them loses them: `accountant list` and `accountant-copy status` need
  `ledger.read` (the accountant's review is the books'; its other commands are `ledger.post`), and
  `delivery-connection show` and `query` need `customer-work` (a sending connection is the outbox's, and every outbox
  read needs it). So a clerk is admitted to exactly 27 company commands (items, stock, sites, bins, counts, lots,
  serials, transfers, the register reads and `company show`), pinned in `tests/test_pos_layer.py`.
- **An older root (review M3).** Where a requirement meets a root activated before pos-v1, the refusal is
  `E_PERMISSION` `catalog_upgrade_required` naming `permission show` then `permission activate`
  (`permission_access.upgrade_required`), on the pos commands, the `/pos` page and any command needing a capability
  the stored rules lack; `membership grant` naming such a capability says the same (E_VALIDATION). **A live
  installation needs `permission activate` after this merges.**
- **What registers point at (review M4).** Deactivating a customer, account or payment method, deactivating an
  inventory site, or merging away a customer or account that a register uses is refused `E_RECORD_IN_USE`
  (`reason: pos_register_reference`), naming the register and what it uses the record as; `pos-register show` and
  `list` carry `warnings` naming any record a register points at that is inactive, and a missing refund account.
- **The clerk presets** (`hub/pos_presets.py`). *POS clerk*: role `readonly`, grant `pos.sell`; *Clerk with returns*
  adds `pos.void` and `pos.refund`. Denies: every capability `readonly` holds that a company command or company action
  requires, except `company` and `item`, computed from the live catalog. **Correction to §9 (found by the increment 3
  review):** the clerk keeps `company` (read), because every page runs `company show`; a member denied it gets a 403 on
  every page. The installation-level capabilities `readonly` holds (`hub`, `init`, `membership`, `organization`,
  `serve`, `token`, `upgrade`, `user`) are not company permissions and are not denied: signing in, one's tokens and
  one's own Home keep working. In Users & permissions, while POS is on, *Presets* offers both; *Fill from preset* fills
  the role and the grant and deny lists and previews; *Save permissions* runs `membership grant` and then
  `user home set --page pos` for the person (needs `user.manage`; without it the membership is saved and the line says
  "Home not set: ..."). The preset is a filled form: the person's permissions stay editable like any member's, and an
  agent or the CLI applies the same lists with `membership grant` and `user home set`. **The membership records it
  (review H2):** the preset adds a marker grant, `preset.pos-clerk` or `preset.pos-clerk-returns`, two capabilities
  pos-v1 registers that no command requires and no role holds, so they give nothing. When `permission activate` moves
  a root to a catalog under which `readonly` holds a company capability it did not hold before, it adds that
  capability to the denies of every live readonly membership carrying a marker, in the same command, and reports each
  in `presets_extended` (user, scope, preset, added). Moving the person off `readonly` drops the marker in Users &
  permissions, and a non-readonly membership is never extended. If an organization membership gives the person more
  than the preset, the preview warns, naming what they keep (a company deny wins over the organization, so what
  survives is what `readonly` never held).
- **The clerk's pages.** With increment 3's menus, a clerk holding only `company` and `item` (read) and `pos.sell` has
  two sections, Customers (whose only live link is Point of sale) and Items. Settled here (the §9 "missed" note):
  `home.pos_header_links` gives such a reader the **minimal header with Point of sale and Items as its links** and no
  side menu, finder, Find transactions or account menu; Items stays open for looking items up, and an item's own page
  opens (a refused `audit list` there is shown as no activity, review M1). With POS off, a seller with only Items
  keeps the minimal header with Items alone. Any reader with any other section keeps the menu exactly as before.
  Typed directly, `/_all` answers the refusal page to a reader with no company write and lists only pages the reader
  may open; `/find` answers the refusal page without `transaction find`; `perf report` counts a non-administrator's
  calls only on companies whose reports they may run (no catalog change: the filter is the report's own).
- **Point of sale entry, tile and Home.** *Point of sale* is the first task under Customers and an aside tile on the
  Customers panel, both backed by `pos-register list` at `/pos` and shown only while POS is on. `/c/<id>/pos` is a
  placeholder (`pos_pages.py`, `pos.html`) naming the registers and saying the register screen is coming, with a sales
  receipt link for those who may post one; increment 5 replaces its body at the same address, so a Home of `pos` set
  now stays right. A clerk signing in lands there (criterion 2).
- **Agent guide:** a *Point of sale* chapter (record sales with `sales-receipt post`; every POS act is a granted
  capability). `import_kinds.NOT_IMPORTABLE` lists the two register writes. No report reads the new column beyond
  `company show` (deposit read manifest: `company_info.pos_enabled`).
- **Tests.** `tests/test_pos_layer.py` (off by default with every refusal and no surface, Main and its walk-in, a
  second register, name, account and version refusals, off and on again; the preset's pinned deny list and the
  clerk's effective set on the live catalog, applied through Users & permissions, landing on the POS with the minimal
  header, refused a form, a report URL, and the commands over HTTP and MCP; a readonly member with no grant refused;
  an agent refused until granted, then still refused `pos.layout`; the clerk's pinned command list, item concealment
  on show and query, the item page, Items without shipments, `/_all` and `/find` refused, the POS-off header;
  "Walk-in 2" when the name is taken; the register-reference and walk-in merge blockers and bulk-edit's selection;
  the preset extension on `permission activate`; an older root's refusals and their fix),
  `tests/test_pos_layer_surfaces.py` (Python, CLI, HTTP and MCP alike). `test_every_page_runs_only_what_it_declares`
  turns POS on, so `/pos` is declared.
- **Merged with main after opening balances (57d7cd56):** co0118 sits on co0117 and `pos-v1` on `opening-draft-v1`.
  A live installation needs `permission activate` after this merges. The deny pin did its job at the merge of main: automation-v1 added `automation` to `readonly`,
  the pin failed, and the clerk denies it.

## 16. Open questions

Each default stands if nobody decides. K1 and K2 are Kabe's.

- **K1. Card payments through a processor** (Square, Stripe or a terminal integration), which sends sale data to an
  outside service and costs money. *Default:* none. The card terminal runs on its own; Bookflow records the method and a
  typed reference, and processor fees are a deposit line, as the anchor's users do.
- **K2. A sale with no customer.** The anchor lets a sales receipt leave the customer blank, but its credit memos and
  refunds need one. *Default:* a Walk-in customer record per register, never shown on screen or receipt, so refunds and
  reports work and sales by customer shows one Walk-in line. The alternative makes the customer optional on sales
  receipts, a change to the core sale used by every surface.

For the builder, defaults standing unless review finds otherwise:

3. **The clerk as a preset, not a new role.** *Default:* `readonly` plus grants and denies, guarded by the catalog test;
   a fifth role below `readonly` would touch every catalog layer.
4. **Layouts per register.** *Default:* one per company; a register may name its own layout later.
5. **Cash refunds.** *Default:* from a bank-type account per register, because a refund must leave a bank; the
   alternative lets a deposit carry refunds as negative rows from Undeposited Funds, a change to deposits.
6. **Split tender.** *Default:* the anchor's invoice plus payments; the alternative, several tenders on one sales
   receipt, changes the sale, its deposit sources and Make Deposits.
7. **Clerks and returns.** *Default:* the plain clerk preset cannot void or refund; *Clerk with returns* can.
8. **Shifts.** *Default:* off per register (the day summary still works); a cash business turns counting on.
9. **Thumbnails without a new dependency.** *Default:* made in the browser at upload; no Pillow. A picture from the CLI or
   MCP without one shows scaled.
10. **HTTPS.** Out of scope here (gate g0acabd). Until it lands, live camera works on `localhost` and the photo fallback
    elsewhere.
11. **Manager override** (a manager's PIN authorizing a void on the clerk's screen). *Default:* none; the manager voids
    from their own sign-in.
