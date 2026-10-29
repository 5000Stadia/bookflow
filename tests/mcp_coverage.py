"""Executable coverage ledger; pending rows are explicit, never counted as parity."""
from html.parser import HTMLParser

from bookflow.core import registry
from bookflow.core.publication_inventory import inventory
from bookflow.documentation.examples import _SUPPORTING_CREATE_INPUTS
from bookflow.documentation.introspection import model_fields
from bookflow.adapters.workbench import forms

# Reviewed payment base 15cb601 adds these command identities. They remain
# pending execution scenarios until actual cross-interface witnesses exist.
PAYMENT_COMMANDS = frozenset("""application history
application show
invoice settlement
payment apply
payment calculate
payment history
payment invoices
payment operation items
payment operation show
payment preview items
payment query
payment receive
payment selection clear
payment selection create
payment selection items
payment selection query
payment selection show
payment selection update
payment settlement
payment settlement changes
payment show
payment suggest
payment unapply
payment update
payment void""".splitlines())

# The deletion, receiving and permission-setup work of 2026-09-14/15 added these command
# identities. Each is dispositioned below, mapped through the witness test's own COMMANDS set.
#
# The label no longer has to be distrusted: `four_surface_scenario` is derived from the witness
# module's own `SURFACES` and is reserved for one that declares all four. A witness that declares
# fewer, or declares nothing, is `transport_scenario` -- real executed evidence over at least one
# real transport, which is a weaker claim honestly made. Adding `SURFACES` to a witness that does
# drive all four is mechanical and moves its rows up. This paragraph used to say to read the
# witness before trusting the word "four", which was the right advice while the label was a
# guess; it is now a measurement.
#
# `bill delete` and `payment delete` now hold witnesses of their own, each driving cli, http and
# mcp and asserting the books the deletion leaves behind. Until those existed their only executed
# evidence was HTTP and real Chrome, and they sat in a pending row rather than being counted as
# parity they did not have.
BATCH_2026_09_COMMANDS = frozenset("""bill delete
card-charge delete
check delete
invoice delete
item-receipt history
item-receipt post
item-receipt query
item-receipt show
item-receipt update
item-receipt void
membership effective
payment delete
permission activate
permission show
sales-receipt delete""".splitlines())

# Spec row 4, job time, which the human pinned post-v1: they want it with a calendar, each job
# assigned to an employee, and time tracked per employee per job. What exists is the capture half.
# It reached integration on 2026-09-16 only because the frozen-ancestor catalog repair had to
# carry it to get the three deletion families out.
#
# These nine therefore ship with no transport witness ON PURPOSE, and the ledger records the
# reason rather than claiming evidence or going quietly green. What exists for them:
# `tests/test_job_time.py` is entirely in-process, and `tests/test_job_time_browser.py` drives
# real Chrome at 1280 and 390 -- genuine evidence of the workbench, and NOT a transport witness,
# because it exercises the browser rather than the CLI or a source-bound MCP child. There is no
# workbench route either: `time-activity` appears in `work.py` and nowhere in `pages.py`.
#
# The distinction this records is the one that matters. An unwitnessed command in a deferred
# feature is honest; an unwitnessed command in a shipped one is a lie. `payment delete` was the
# second kind -- in scope, declared done, reviewed, and unreachable over every transport.
JOB_TIME_COMMANDS = frozenset("""time-activity billing
time-activity create
time-activity history
time-activity invoice
time-activity query
time-activity sales-receipt
time-activity show
time-activity update
time-activity void""".splitlines())

# Why a command ships without an executed transport witness, dated. A row may be pending only
# with an entry here; a row with neither a witness nor a reason is the failure this ledger
# exists to catch, and `test_registry_execution_ledger_has_no_unclassified_commands` fails on it.
PENDING_REASONS = {name: ('2026-09-16',
    'Spec row 4 (job time), pinned post-v1 by the human: the capture half only, with no '
    'workbench route. In-process and real-Chrome evidence exist; neither is a transport '
    'witness. Transport evidence lands with the rest of row 4, not before it.')
    for name in JOB_TIME_COMMANDS}

# The three verbs that closed the deletion set at nine families on 2026-09-16. Each arrived with
# an in-process deletion suite and no transport evidence of any kind -- the position `payment
# delete` was in when it turned out to be unreachable over every transport but Python. Each now
# has a witness that drives all four surfaces and asserts what the deletion did to the books.
DELETION_CLOSE_COMMANDS = frozenset("""credit-memo delete
deposit delete
journal delete""".splitlines())

# R166, the move-in from the old books. Its witness drives all four surfaces over identical copies
# of one fresh company: the same plan, the same seventy-five writes and the same tie-out on each.
CUTOVER_COMMANDS = frozenset(('cutover apply', 'cutover plan', 'cutover tie-out'))
# R151, restoring a deleted document. Its witness restores through Python and over HTTP, and an
# agent is refused over HTTP.
RESTORE_COMMANDS = frozenset(('invoice restore', 'journal restore'))

# R117, merging duplicate customers and vendors. Its witness drives all four surfaces over
# identical copies of the demo: preview, merge, the same merge again, A/R aging, and undo.
MERGE_COMMANDS = frozenset(('customer merge', 'customer unmerge', 'vendor merge', 'vendor unmerge'))

# R176, recording a returned customer check. Its witness drives all four surfaces over identical copies
# of the demo: preview, record it, record it again (nothing written), read the receipt, the new fee
# invoice and A/R aging back.
BOUNCE_COMMANDS = frozenset(('payment bounce',))

# R179, the rest of the old books at the move-in: what a 1099 vendor was paid before the books began
# here. Its witness sets, repeats and clears it through all four surfaces over identical demo copies.
VENDOR_1099_COMMANDS = frozenset(('vendor 1099-opening',))

# R170 blind July: a small text attachment read as text (a statement's summary box). Its witness reads one
# and refuses a picture through all four surfaces over identical demo copies, and pages a statement over MCP.
ATTACHMENT_TEXT_COMMANDS = frozenset(('attachment text',))

# R138: find any transaction. Its witness finds one check and refuses an unknown name through all four surfaces.
TRANSACTION_FIND_COMMANDS = frozenset(('transaction find',))

# R154: a non-inventory part or other charge converted to an inventory part, one way. Its witness previews,
# converts, is refused converting again and reads the item back through all four surfaces over identical demo copies.
ITEM_CONVERT_COMMANDS = frozenset(('item convert',))

# R120: finance charges assessed by the company policy. Its witness sets the policy, previews, assesses and
# assesses again through all four surfaces over identical demo copies.
FINANCE_CHARGE_COMMANDS = frozenset(('finance-charge assess',))

# R149: a vendor's refund check for an open credit. Its witness enters a credit and records a partial refund
# through all four surfaces over identical demo copies.
VENDOR_REFUND_COMMANDS = frozenset(('vendor-credit refund',))

# R109: budgets, the budget and comparison reports, and memorized reports. Its witness builds a budget, fills,
# sets, reads it in every report, memorizes a report and runs it through all four surfaces over identical demo copies.
BUDGET_COMMANDS = frozenset(('budget clear', 'budget create', 'budget delete', 'budget fill', 'budget list', 'budget rename',
                             'budget set', 'budget show', 'memorized-report create', 'memorized-report delete',
                             'memorized-report list', 'memorized-report run', 'memorized-report show', 'memorized-report update',
                             'report balance-sheet-comparison', 'report budget-overview', 'report budget-vs-actual',
                             'report profit-and-loss-comparison'))

# R124: customer prepayments. Its witness receives, attaches, lists, applies, unapplies, refunds, shows and
# reports one deposit through all four surfaces over identical demo copies.
PREPAYMENT_COMMANDS = frozenset(('prepayment receive', 'prepayment attach', 'prepayment apply', 'prepayment unapply',
                                 'prepayment refund', 'prepayment show', 'prepayment query', 'report customer-prepayments'))

# R117 accounts and items: merge and unmerge. Their witness previews, merges, merges again, reads the trial
# balance and undoes, for an account and an item, through all four surfaces over identical small companies.
LIST_MERGE_COMMANDS = frozenset(('account merge', 'account unmerge', 'item merge', 'item unmerge'))

# R148 the rest of customer collections: moving credit between jobs. Its witness previews a transfer,
# records it, records it again (nothing written), reads both jobs' aging and voids it, on all four surfaces.
COLLECTIONS_COMMANDS = frozenset(('customer-credit transfer', 'customer-credit transfer-void'))

# R101: the sales order. Its witness quotes, converts (and replays), creates, revises, closes, copies,
# voids and reads orders back through all four surfaces over identical demo copies.
SALES_ORDER_COMMANDS = frozenset(('estimate sales-order', 'sales-order copy', 'sales-order create', 'sales-order history',
                                  'sales-order query', 'sales-order show', 'sales-order update', 'sales-order void'))

# R119 fixed assets and loans. Their witness sets up an asset, previews and posts depreciation, reads the
# schedule and list, disposes of it, creates and updates a loan, pays it and reads it through all four surfaces.
FIXED_ASSET_COMMANDS = frozenset(('fixed-asset depreciate', 'fixed-asset dispose', 'fixed-asset list', 'fixed-asset schedule',
                                  'fixed-asset setup', 'loan create', 'loan list', 'loan pay', 'loan show', 'loan update'))

# R103: assembly builds. Its witness builds, edits, marks pending, finalizes, voids, deletes, shows and lists
# through all four surfaces over identical demo copies.
ASSEMBLY_BUILD_COMMANDS = frozenset(('assembly build', 'assembly update', 'assembly finalize', 'assembly void',
                                     'assembly delete', 'assembly show', 'assembly list'))

# R102: committed stock. Its witness reads report open-sales-orders by customer and by item through all four
# surfaces over identical demo copies.
COMMITTED_STOCK_COMMANDS = frozenset(('report open-sales-orders',))

# R118: mileage. Its witness adds and renames a vehicle, sets, lists and removes rates, records, corrects, voids,
# reads and queries trips, bills one by invoice and one by sales receipt, reads the billing window, pays the
# driver back by check and runs both mileage reports through all four surfaces over identical demo copies.
MILEAGE_COMMANDS = frozenset(('mileage-rate delete', 'mileage-rate list', 'mileage-rate set', 'report mileage-by-job',
                              'report mileage-by-vehicle', 'trip billing', 'trip create', 'trip invoice', 'trip query',
                              'trip reimburse', 'trip sales-receipt', 'trip show', 'trip update', 'trip void',
                              'vehicle create', 'vehicle list', 'vehicle show', 'vehicle update'))

# R110: price rules. Its witness sets the conflict preference, adds, changes, shows, lists, previews, deactivates
# and reactivates a rule through all four surfaces over identical demo copies.
PRICING_RULE_COMMANDS = frozenset(('price-rule activate', 'price-rule create', 'price-rule deactivate', 'price-rule list',
                                   'price-rule preferences', 'price-rule preview', 'price-rule set-preferences',
                                   'price-rule show', 'price-rule update'))

# R101 increment 4: invoicing from orders. Its witness invoices the demo order by its default, replays the key
# and reads the order's billing through all four surfaces over identical demo copies.
ORDER_INVOICING_COMMANDS = frozenset(('sales-order invoice', 'sales-order billing'))

# R101 increment 5: shortfall coverage. Its witness orders a demo order's shortfall, is refused ordering it
# again, and orders spares for a work order through all four surfaces over identical demo copies.
SHORTFALL_COMMANDS = frozenset(('sales-order purchase-order', 'work-order purchase-order'))

# R153 billing part of a purchase order. Its witness orders ten, bills four against the order, reads the
# line tracking and the open purchase orders report, and closes the rest through all four surfaces.
PARTIAL_PO_COMMANDS = frozenset(('purchase-order close-lines',))

# R121: accountant review. Its witness designates and removes an external accountant, exports an accountant's
# copy, works in it, writes and imports the change file, reads the checklist and report, and cancels a second copy.
ACCOUNTANT_REVIEW_COMMANDS = frozenset((
    'accountant add', 'accountant list', 'accountant mark-entry', 'accountant reclassify', 'accountant remove',
    'accountant reviewed-through', 'accountant-copy cancel', 'accountant-copy changes', 'accountant-copy export',
    'accountant-copy import', 'accountant-copy preview', 'accountant-copy status', 'report accountant-changes',
    'report accountant-review'))
# R152: check printing. Its witness sets up the printer, prints an alignment sample, queues two checks, prints them,
# confirms one misprint, cancels and reprints a run, reprints a check under a new number and reads the run, its PDF
# and the queue through all four surfaces over identical demo copies.
# R104 increment 3: barcodes. Its witness looks a code up, numbers new parts and posts an invoice naming a code
# as `item` through all four surfaces over identical demo copies.
BARCODE_COMMANDS = frozenset(('barcode generate', 'barcode lookup'))

CHECK_PRINTING_COMMANDS = frozenset(('check print', 'check print-alignment', 'check print-cancel', 'check print-confirm',
                                     'check print-pdf', 'check print-queue', 'check print-run', 'check print-setup',
                                     'check reprint'))


# R104 increment 1: inventory sites, transfers and Quantity on Hand by Site. Its witness runs the first shared
# scenario (sites, a retried transfer, a correction and a void) through all four surfaces over identical demo copies.
INVENTORY_SITE_COMMANDS = frozenset(('inventory-site activate', 'inventory-site create', 'inventory-site deactivate',
    'inventory-site list', 'inventory-site show', 'inventory-site update', 'inventory-transfer history',
    'inventory-transfer post', 'inventory-transfer query', 'inventory-transfer show', 'inventory-transfer update',
    'inventory-transfer void', 'report quantity-by-site'))

# R111: to-dos, overdue notices and the Reminders list. Its witness creates, updates, completes, shows and lists a
# to-do, generates, lists, shows, marks sent and dismisses overdue notices, and reads reminders through all four
# surfaces over identical demo copies.
SCHEDULING_COMMANDS = frozenset(('todo create', 'todo update', 'todo complete', 'todo show', 'todo list',
                                 'notice generate', 'notice list', 'notice show', 'notice mark-sent', 'notice dismiss',
                                 'reminders'))

# R180: performance tracking. Its witness reads the timings report and the store's settings through all four
# surfaces over identical demo copies; the three operator tools are standalone and local.
PERF_COMMANDS = frozenset(('perf report', 'perf settings', 'perf generate', 'perf bench', 'perf compare'))

BROWSING_COMMANDS = frozenset(('account query options', 'class query options', 'custom-field query children', 'custom-field query options', 'customer query options', 'customer-message query options', 'customer-type query options', 'employee query options', 'item query children', 'item query options', 'item-category query options', 'job-type query options', 'other-name query options', 'payment-method query options', 'price-level query children', 'price-level query options', 'sales-rep query options', 'sales-tax-code query options', 'ship-method query options', 'term query options', 'unit-of-measure query children', 'unit-of-measure query options', 'vendor query children', 'vendor query options', 'vendor-type query options'))

# R106: timekeeping. Its witness sets the preferences, saves, shows and copies a weekly timesheet, clocks a person
# in and out and cancels a clock, reads the status, unlinks a login, schedules, moves, cancels and shows entries and
# reads the calendar by employee and by job, through all four surfaces over identical demo copies.
TIMEKEEPING_COMMANDS = frozenset(('clock cancel', 'clock in', 'clock link', 'clock out', 'clock status',
                                  'schedule calendar', 'schedule cancel', 'schedule create', 'schedule show',
                                  'schedule update', 'timesheet copy-last', 'timesheet preferences', 'timesheet save',
                                  'timesheet set-preferences', 'timesheet show'))

# R104 increment 2: physical counts and the worksheet and valuation-detail reports. Its witness counts the van,
# retries an entry, records the count from its preview, and derives it again after a same-day transfer, through all
# four surfaces over identical demo copies.
INVENTORY_COUNT_COMMANDS = frozenset(('inventory-count cancel', 'inventory-count create', 'inventory-count enter',
    'inventory-count post', 'inventory-count query', 'inventory-count sheet', 'inventory-count show',
    'inventory-count void', 'report inventory-valuation-detail', 'report physical-inventory-worksheet'))

# R104 increment 4: serial and lot numbers. Its witness sells a numbered heater, unassigns and assigns a number
# (retried), updates a lot and a serial number and reads them back through every show, list and report.
SERIAL_LOT_COMMANDS = frozenset(('inventory assign', 'lot list', 'lot show', 'lot update', 'report lot-trace',
    'report serial-lot-history', 'report serial-lot-stock', 'serial-number list', 'serial-number show',
    'serial-number update'))

# R104 increment 5: bins and cycle counts. Its witness adds a bin (retried), relabels it, moves stock between two
# bins, counts a bin, reads items by bin, adds and updates a cycle class, classifies and reads what is due.
# R104 increment 6: landed cost. Its witness enters a freight bill, allocates it (previewed, retried, refused when
# nothing is left), shows and lists it, voids it and allocates it again, on every surface.
# R104 increment 7: FIFO costing. Its witness switches a demo copy to FIFO (previewed, retried, refused again),
# schedules and withdraws a later switch, and reads show and the layers report, on every surface.
FIFO_COMMANDS = frozenset(('costing-method set', 'costing-method withdraw', 'costing-method show',
    'report inventory-layers'))
# R180 stage 2: the verified history watermark. Its witness verifies a reconciled demo copy whole and one account,
# on every surface.
VERIFY_COMMANDS = frozenset(('company verify',))
AGENT_SETUP_COMMANDS = frozenset(('setup',))
# R181 increment 1: shipments. Its witness stocks a part, orders six, ships four (previewed, retried), records
# tracking, reads show, query, history and the Shipped Not Invoiced report, invoices it and is refused a void.
# R183 setup-trial fixes: owner-link (local bootstrap), agent run (standalone), reconcile discard.
SETUP_TRIAL_COMMANDS = frozenset(('owner-link', 'agent run', 'reconcile discard'))
SHIPMENT_COMMANDS = frozenset(('shipment post', 'shipment update', 'shipment void', 'shipment show', 'shipment query',
    'shipment history', 'shipment invoice', 'report shipped-not-invoiced',
    # R181 increment 2: a return of one (retried), read back through show and query, and voided.
    'shipment return', 'shipment-return show', 'shipment-return query', 'shipment-return void',
    # R181 increment 3: picking on, a list made, a pick retried, read back, shipped from; another closed, cancelled.
    'pick-list create', 'pick-list enter', 'pick-list show', 'pick-list query', 'pick-list close', 'pick-list cancel',
    # R181 increment 4: a draft made, packed (retried), shipped; another cancelled.
    'shipment create', 'shipment pack', 'shipment cancel',
    # R181 increment 5: the worksheet and the shipments report read after the draft ships.
    'report order-fulfillment', 'report shipments'))

DELIVERY_COMMANDS = frozenset(('delivery draft', 'delivery update', 'delivery send', 'delivery cancel', 'delivery mark-sent',
    'delivery export', 'delivery show', 'delivery query', 'delivery history', 'delivery-template create',
    'delivery-template update', 'delivery-template deactivate', 'delivery-template show', 'delivery-template query',
    'delivery-template fields', 'delivery-template preview',
    # R112 increment 2: the queue and the transport seam.
    'delivery approve', 'delivery retry', 'delivery run', 'delivery-connection add', 'delivery-connection update',
    'delivery-connection enable', 'delivery-connection disable', 'delivery-connection remove',
    'delivery-connection check', 'delivery-connection show', 'delivery-connection query',
    # R112 increment 3: flags, the queue and batches.
    'delivery flag', 'delivery unflag', 'delivery queue', 'delivery batch', 'delivery batch-show', 'delivery batch-query',
    'delivery batch-cancel',
    # R112 increment 4: recurring schedules.
    'delivery-schedule create', 'delivery-schedule update', 'delivery-schedule live', 'delivery-schedule pause',
    'delivery-schedule resume', 'delivery-schedule run', 'delivery-schedule show', 'delivery-schedule query'))

# R107 increments 0 to 2: a file checked, previewed, decided, run, read back, its batch read and reversed; its mapping
# saved and next month's file run from it; another cancelled.
IMPORT_COMMANDS = frozenset(('import kinds', 'import template', 'import preview', 'import run', 'import show',
    'import query', 'import rows', 'import errors', 'import reverse', 'import cancel', 'batch show', 'batch query',
    # R107 increment 2 and Kabe's rules: a file checked, a reference decided, a mapping saved and used next month.
    'import check', 'import decide', 'import-mapping save', 'import-mapping show', 'import-mapping history',
    'import-mapping query', 'import-mapping match', 'import-mapping deactivate'))

# R108 increment 1: a statement previewed and imported into the bank inbox, refused twice, read back.
BANK_FEED_COMMANDS = frozenset(('bank-feed import', 'bank-feed show', 'bank-feed query', 'bank-line query',
                                'bank-feed reverse', 'bank-feed move',  # R108 increment 2: moved, then undone
                                # R108 increment 3: sorting the inbox.
                                'bank-line match', 'bank-line unmatch', 'bank-line ignore', 'bank-line add',
                                'bank-line transfer', 'bank-line undo'))

# R114 increment 1: an item's picture uploaded, chosen from its files, read by a reader of items, and removed.
ITEM_PICTURE_COMMANDS = frozenset(('item picture set', 'item picture use', 'item picture get', 'item picture clear'))

LANDED_COST_COMMANDS = frozenset(('landed-cost setup', 'landed-cost charges', 'landed-cost post', 'landed-cost void',
    'landed-cost show', 'landed-cost query'))

BIN_COMMANDS = frozenset(('bin activate', 'bin create', 'bin deactivate', 'bin list', 'bin show', 'bin update',
    'cycle-class activate', 'cycle-class create', 'cycle-class deactivate', 'cycle-class list', 'cycle-class show',
    'cycle-class update', 'cycle-count classify', 'cycle-count due', 'report items-by-bin'))

# R191: each person's Home, set, shown and cleared.
USER_HOME_COMMANDS = frozenset(('user home set', 'user home show', 'user home clear'))

# R107 increment 6: grid edits, previewed, applied, shown and reversed.
BULK_EDIT_COMMANDS = frozenset(('bulk-edit columns', 'bulk-edit grid', 'bulk-edit preview', 'bulk-edit apply',
                                'bulk-edit reverse', 'bulk-edit show', 'bulk-edit query'))

FROZEN_COMMANDS = USER_HOME_COMMANDS | BULK_EDIT_COMMANDS | ITEM_PICTURE_COMMANDS | BANK_FEED_COMMANDS | IMPORT_COMMANDS | DELIVERY_COMMANDS | SETUP_TRIAL_COMMANDS | SHIPMENT_COMMANDS | AGENT_SETUP_COMMANDS | VERIFY_COMMANDS | FIFO_COMMANDS | LANDED_COST_COMMANDS | BIN_COMMANDS | SERIAL_LOT_COMMANDS | PERF_COMMANDS | SCHEDULING_COMMANDS | INVENTORY_COUNT_COMMANDS | BARCODE_COMMANDS | CHECK_PRINTING_COMMANDS | ACCOUNTANT_REVIEW_COMMANDS | TIMEKEEPING_COMMANDS | INVENTORY_SITE_COMMANDS | PARTIAL_PO_COMMANDS | ORDER_INVOICING_COMMANDS | SHORTFALL_COMMANDS | COLLECTIONS_COMMANDS | FIXED_ASSET_COMMANDS | ASSEMBLY_BUILD_COMMANDS | COMMITTED_STOCK_COMMANDS | MILEAGE_COMMANDS | PRICING_RULE_COMMANDS | BROWSING_COMMANDS | PAYMENT_COMMANDS | BATCH_2026_09_COMMANDS | DELETION_CLOSE_COMMANDS | JOB_TIME_COMMANDS | CUTOVER_COMMANDS | RESTORE_COMMANDS | MERGE_COMMANDS | BOUNCE_COMMANDS | VENDOR_1099_COMMANDS | ATTACHMENT_TEXT_COMMANDS | TRANSACTION_FIND_COMMANDS | ITEM_CONVERT_COMMANDS | FINANCE_CHARGE_COMMANDS | VENDOR_REFUND_COMMANDS | BUDGET_COMMANDS | PREPAYMENT_COMMANDS | LIST_MERGE_COMMANDS | SALES_ORDER_COMMANDS | frozenset("""account activate
account create
account deactivate
account list
account query
account show
account uncategorized
account update
activity
agent activate
agent assign
agent authorize
agent create
agent deactivate
agent list
agent show
agent unassign
attachment add
attachment get
attachment link
attachment list
attachment unlink
audit list
audit show
audit tail
backup list
backup rehearse
backup schedule
backup verify
bill history
bill pay
bill payment apply
bill payment history
bill payment query
bill payment show
bill payment unapply
bill payment void
bill post
bill query
bill show
bill update
bill void
card-charge history
card-charge post
card-charge query
card-charge show
card-charge update
card-charge void
card-credit history
card-credit post
card-credit query
card-credit show
card-credit update
card-credit void
chart apply
chart list
chart show
check history
check post
check query
check show
check update
check void
class activate
class create
class deactivate
class list
class query
class show
class update
company attach
company backup
company compact
company detach
company list
company new
company rename
company restore
company show
company update
company use
credit-memo history
credit-memo post
credit-memo query
credit-memo show
credit-memo update
credit-memo void
custom-field activate
custom-field create
custom-field deactivate
custom-field list
custom-field query
custom-field show
custom-field update
customer activate
customer create
customer deactivate
customer link-vendor
customer list
customer query
customer show
customer unlink-vendor
customer update
customer-credit apply
customer-credit unapply
customer-message activate
customer-message create
customer-message deactivate
customer-message list
customer-message query
customer-message show
customer-message update
customer-type activate
customer-type create
customer-type deactivate
customer-type list
customer-type query
customer-type show
customer-type update
customer-refund history
customer-refund post
customer-refund query
customer-refund show
customer-refund update
customer-refund void
demo reset
deposit history
deposit items
deposit post
deposit query
deposit show
deposit sources
deposit update
deposit void
directive add
directive deactivate
directive list
directive show
docs generate
employee activate
employee create
employee deactivate
employee list
employee query
employee show
employee update
estimate billing
estimate copy
estimate create
estimate history
estimate invoice
estimate query
estimate sales-receipt
estimate show
estimate update
estimate void
estimate work-order
hub audit list
hub audit show
hub audit tail
init
inventory adjust
inventory show
inventory void
invoice history
invoice post
invoice query
invoice show
invoice update
invoice void
item activate
item create
item deactivate
item list
item query
item show
item update
item-category activate
item-category create
item-category deactivate
item-category list
item-category query
item-category show
item-category update
job-type activate
job-type create
job-type deactivate
job-type list
job-type query
job-type show
job-type update
journal history
journal post
journal query
journal show
journal update
journal void
mcp
membership grant
membership list
membership revoke
memorized create
memorized delete
memorized enter
memorized list
memorized process
memorized retry
memorized show
memorized skip
memorized update
memorized-group create
memorized-group delete
memorized-group enter
memorized-group list
memorized-group show
memorized-group update
note add
note edit
note list
note show
organization list
organization new
organization rename
organization show
other-name activate
other-name convert
other-name create
other-name deactivate
other-name list
other-name query
other-name show
other-name update
payment recovery abort
payment recovery apply
payment recovery begin
payment recovery compare
payment recovery compare-items
payment recovery items
payment recovery query
payment recovery replace
payment recovery seal
payment recovery show
payment recovery upload
payment-method activate
payment-method create
payment-method deactivate
payment-method list
payment-method query
payment-method show
payment-method update
presence clear
presence set
price-level activate
price-level create
price-level deactivate
price-level list
price-level query
price-level show
price-level update
profile apply
profile list
profile show
purchase-order history
purchase-order post
purchase-order query
purchase-order show
purchase-order update
purchase-order void
proposal copy
proposal create
proposal estimate
proposal history
proposal query
proposal show
proposal update
rate query
rate set
rate show
reconcile candidates
reconcile finish
reconcile import
reconcile mark
reconcile opening start
reconcile preview
reconcile start
register calculate
register post
register query
register update
report ap-aging
report ar-aging
report balance-sheet
report cash-flows
report collections
report customer-balance-detail
report customer-balance-summary
report deposit-detail
report entries-to-review
report expenses-by-vendor
report export
report general-ledger
report income-tax-summary
report inventory-valuation
report missing-checks
report open-invoices
report open-purchase-orders
report prior-balances
report profit-and-loss
report profit-and-loss-by-class
report profit-and-loss-by-job
report purchases-by-item
report purchases-by-vendor
report reconciliation-discrepancy
report sales-by-customer
report sales-by-item
report sales-by-rep
report statement
report stock-status
report transaction-detail
report transaction-list-by-date
report trial-balance
report unbilled-costs
report unpaid-bills
report vendor-1099-summary
report vendor-balance-detail
report vendor-balance-summary
review mark
sales-receipt history
sales-receipt post
sales-receipt query
sales-receipt show
sales-receipt update
sales-receipt void
sales-rep activate
sales-rep create
sales-rep deactivate
sales-rep list
sales-rep query
sales-rep show
sales-rep update
sales-tax adjust
sales-tax adjustment query
sales-tax adjustment show
sales-tax adjustment void
sales-tax liability
sales-tax pay
sales-tax payment query
sales-tax payment show
sales-tax payment void
sales-tax-code activate
sales-tax-code create
sales-tax-code deactivate
sales-tax-code list
sales-tax-code query
sales-tax-code show
sales-tax-code update
serve
statement-charge post
statement-charge query
statement-charge show
statement-charge void
ship-method activate
ship-method create
ship-method deactivate
ship-method list
ship-method query
ship-method show
ship-method update
term activate
term create
term deactivate
term list
term query
term show
term update
token issue
token list
token revoke
transfer history
transfer post
transfer query
transfer show
transfer update
transfer void
undo
unit-of-measure activate
unit-of-measure create
unit-of-measure deactivate
unit-of-measure list
unit-of-measure query
unit-of-measure show
unit-of-measure update
upgrade
user activate
user add
user deactivate
user list
user set-password
vendor activate
vendor create
vendor deactivate
vendor list
vendor query
vendor show
vendor update
vendor-credit apply
vendor-credit history
vendor-credit post
vendor-credit query
vendor-credit show
vendor-credit unapply
vendor-credit update
vendor-credit void
vendor-type activate
vendor-type create
vendor-type deactivate
vendor-type list
vendor-type query
vendor-type show
vendor-type update
work-order billing
work-order complete
work-order copy
work-order create
work-order history
work-order invoice
work-order query
work-order sales-receipt
work-order show
work-order update
billing-group add
billing-group create
billing-group delete
billing-group list
billing-group remove
billing-group rename
billing-group show
batch-invoice post
batch-invoice query
batch-invoice retry
batch-invoice show
""".splitlines())
LISTS = {n + ' ' + v for n in _SUPPORTING_CREATE_INPUTS for v in ('create', 'show', 'list', 'query', 'update', 'activate', 'deactivate')}
FINANCIAL = {n + ' ' + v for n in ('journal', 'invoice', 'sales-receipt') for v in ('post', 'show', 'history', 'query', 'update', 'void')}


LOCAL_VALID_WITNESSES = {
    'init': ['tests/test_mcp_local_bootstrap.py::test_installed_cli_bootstrap_preview_create_reopen_and_conflict',
             'tests/test_row1_flow.py::test_init_edges'],
    'company use': ['tests/test_row1_flow.py::test_company_selection',
                    'tests/test_row3_host.py::test_a_forwarded_company_use_writes_the_callers_login_table'],
    'docs generate': ['tests/test_docs_command.py::test_docs_generate_cli_needs_no_initialized_data_root'],
    'setup': ['tests/test_agent_setup.py::test_install_setup_connect_and_post_an_invoice_as_the_agent_for_the_owner'],
    'owner-link': ['tests/test_agent_setup.py::test_owner_link_and_agent_run_from_a_relative_root_rerun'],
    'agent run': ['tests/test_agent_setup.py::test_owner_link_and_agent_run_from_a_relative_root_rerun'],
    'perf generate': ['tests/test_perf_tracking.py::test_generate_and_bench_a_tiny_company'],
    'perf bench': ['tests/test_perf_tracking.py::test_generate_and_bench_a_tiny_company'],
    'perf compare': ['tests/test_perf_tracking.py::test_compare_flags_a_planted_regression'],
    'mcp': ['tests/test_mcp_hosted.py::test_real_stdio_discovery_help_and_attributed_host_write',
            'tests/test_mcp_installed_guide.py::test_literal_installed_mcp_guide'],
    'serve': ['tests/test_row3_host.py::test_the_host_migrates_and_records_the_descriptor',
              'tests/test_row3_host.py::test_sigint_wakes_a_live_stream_and_cleans_the_host'],
}


def execution_map():
    from tests.test_mcp_registry_demo_upgrade import COMMANDS as DEMO_UPGRADE_COMMANDS
    from tests.test_mcp_local_boundary import COMMANDS as LOCAL_COMMANDS
    from tests.test_mcp_registry_undo import COMMANDS as UNDO_COMMANDS
    from tests.test_mcp_registry_rollout import COMMANDS as ROLLOUT_COMMANDS
    from tests.test_mcp_registry_company_maintenance import COMMANDS as MAINTENANCE_COMMANDS
    from tests.test_mcp_registry_compact import COMMANDS as COMPACT_COMMANDS
    from tests.test_mcp_registry_audit_reads import COMMANDS as AUDIT_COMMANDS
    from tests.test_mcp_registry_attachments import COMMANDS as ATTACHMENT_COMMANDS
    from tests.test_mcp_registry_register import COMMANDS as REGISTER_COMMANDS
    from tests.test_mcp_registry_credentials import COMMANDS as CREDENTIAL_COMMANDS
    from tests.test_mcp_registry_presence import COMMANDS as PRESENCE_COMMANDS
    from tests.test_mcp_registry_work import FAMILIES as WORK_FAMILIES
    from tests.test_mcp_registry_work_billing import FAMILIES as BILLING_FAMILIES
    from tests.test_mcp_registry_hub_reads import FAMILIES as HUB_FAMILIES
    from tests.test_mcp_registry_supporting import FAMILIES
    from tests.test_mcp_registry_payment_preparation import FAMILIES as PAYMENT_FAMILIES
    from tests.test_mcp_registry_payments import COMMANDS as PAYMENT_FINANCIAL
    from tests.test_mcp_registry_deposits import COMMANDS as DEPOSIT_COMMANDS
    from tests.test_mcp_registry_identity import COMMANDS as IDENTITY_COMMANDS
    from tests.test_mcp_registry_agent import COMMANDS as AGENT_COMMANDS
    from tests.test_money_out_documents import COMMANDS as MONEY_OUT_COMMANDS
    from tests.test_card_credit_surfaces import COMMANDS as CARD_CREDIT_COMMANDS
    from tests.test_company_backup import COMMANDS as BACKUP_COMMANDS
    from tests.test_bill_entry import COMMANDS as BILL_COMMANDS
    from tests.test_bill_payment import COMMANDS as BILL_PAYMENT_COMMANDS
    from tests.test_credit_memo import COMMANDS as CREDIT_MEMO_COMMANDS
    from tests.test_credit_memo_lifecycle import COMMANDS as CREDIT_LIFECYCLE_COMMANDS
    from tests.test_credit_settlement import COMMANDS as CREDIT_SETTLEMENT_COMMANDS
    from tests.test_customer_refund import COMMANDS as REFUND_COMMANDS
    from tests.test_customer_refund_history_surfaces import COMMANDS as REFUND_HISTORY_COMMANDS
    from tests.test_vendor_credit import COMMANDS as VENDOR_CREDIT_COMMANDS
    from tests.test_batch_invoicing_interfaces import COMMANDS as BATCH_COMMANDS
    from tests.test_inventory_interfaces import COMMANDS as INVENTORY_COMMANDS
    from tests.test_purchase_order import COMMANDS as PURCHASE_ORDER_COMMANDS
    from tests.test_statement_charge import COMMANDS as STATEMENT_CHARGE_COMMANDS
    from tests.test_sales_tax_remittance import COMMANDS as SALES_TAX_COMMANDS
    from tests.test_sales_tax_adjustment import COMMANDS as SALES_TAX_ADJUSTMENT_COMMANDS
    from tests.test_transfer_funds import COMMANDS as TRANSFER_COMMANDS
    from tests.test_payment_recovery_interfaces import COMMANDS as RECOVERY_COMMANDS
    from tests.test_memorized_transactions import COMMANDS as MEMORIZED_COMMANDS
    from tests.test_reconciliation_commands import RECONCILE_COMMANDS
    from tests.test_reconcile_import import COMMANDS as RECONCILE_IMPORT_COMMANDS
    from tests.test_purchase_deletion_transports import COMMANDS as PURCHASE_DELETE_COMMANDS
    from tests.test_sales_deletion_transports import COMMANDS as SALES_DELETE_COMMANDS
    from tests.test_receiving_surfaces import COMMANDS as RECEIVING_COMMANDS
    from tests.test_permission_setup_surfaces import COMMANDS as SETUP_SURFACE_COMMANDS
    from tests.test_bill_deletion_transports import COMMANDS as BILL_DELETE_COMMANDS
    from tests.test_payment_deletion_transports import COMMANDS as PAYMENT_DELETE_COMMANDS
    from tests.test_customer_refund_correction_surfaces import COMMANDS as REFUND_CORRECTION_COMMANDS
    from tests.test_vendor_credit_correction_surfaces import COMMANDS as VENDOR_CREDIT_CORRECTION_COMMANDS
    from tests.test_credit_memo_deletion_transports import COMMANDS as CREDIT_DELETE_COMMANDS
    from tests.test_deposit_deletion_transports import COMMANDS as DEPOSIT_DELETE_COMMANDS
    from tests.test_journal_deletion_transports import COMMANDS as JOURNAL_DELETE_COMMANDS
    from tests.test_report_export_surfaces import COMMANDS as REPORT_EXPORT_COMMANDS
    from tests.test_backup_schedule import COMMANDS as BACKUP_SCHEDULE_COMMANDS
    from tests.test_entry_review import COMMANDS as ENTRY_REVIEW_COMMANDS
    from tests.test_cutover_surfaces import COMMANDS as CUTOVER_SURFACE_COMMANDS
    from tests.test_restore_deleted import COMMANDS as RESTORE_SURFACE_COMMANDS
    from tests.test_party_merge_surfaces import COMMANDS as MERGE_SURFACE_COMMANDS
    from tests.test_payment_bounce_surfaces import COMMANDS as BOUNCE_SURFACE_COMMANDS
    from tests.test_vendor_1099_opening_surfaces import COMMANDS as VENDOR_1099_SURFACE_COMMANDS
    from tests.test_attachment_text import COMMANDS as ATTACHMENT_TEXT_SURFACE_COMMANDS
    from tests.test_transaction_find import COMMANDS as TRANSACTION_FIND_SURFACE_COMMANDS
    from tests.test_item_convert_surfaces import COMMANDS as ITEM_CONVERT_SURFACE_COMMANDS
    from tests.test_finance_charge_surfaces import COMMANDS as FINANCE_CHARGE_SURFACE_COMMANDS
    from tests.test_vendor_refund_surfaces import COMMANDS as VENDOR_REFUND_SURFACE_COMMANDS
    from tests.test_budget_surfaces import COMMANDS as BUDGET_SURFACE_COMMANDS
    from tests.test_customer_prepayment_surfaces import COMMANDS as PREPAYMENT_SURFACE_COMMANDS
    from tests.test_list_merge_surfaces import COMMANDS as LIST_MERGE_SURFACE_COMMANDS
    from tests.test_sales_order_surfaces import COMMANDS as SALES_ORDER_SURFACE_COMMANDS
    from tests.test_fixed_asset_surfaces import COMMANDS as FIXED_ASSET_SURFACE_COMMANDS
    from tests.test_assembly_build_surfaces import COMMANDS as ASSEMBLY_BUILD_SURFACE_COMMANDS
    from tests.test_committed_stock_surfaces import COMMANDS as COMMITTED_STOCK_SURFACE_COMMANDS
    from tests.test_mileage_surfaces import COMMANDS as MILEAGE_SURFACE_COMMANDS
    from tests.test_timekeeping_surfaces import COMMANDS as TIMEKEEPING_SURFACE_COMMANDS
    from tests.test_credit_transfer_surfaces import COMMANDS as COLLECTIONS_SURFACE_COMMANDS
    from tests.test_pricing_rule_surfaces import COMMANDS as PRICING_RULE_SURFACE_COMMANDS
    from tests.test_order_invoicing_surfaces import COMMANDS as ORDER_INVOICING_SURFACE_COMMANDS
    from tests.test_shortfall_surfaces import COMMANDS as SHORTFALL_SURFACE_COMMANDS
    from tests.test_purchase_order_partial_surfaces import COMMANDS as PARTIAL_PO_SURFACE_COMMANDS
    from tests.test_inventory_site_surfaces import COMMANDS as INVENTORY_SITE_SURFACE_COMMANDS
    from tests.test_accountant_review_surfaces import COMMANDS as ACCOUNTANT_REVIEW_SURFACE_COMMANDS
    from tests.test_check_printing_surfaces import COMMANDS as CHECK_PRINTING_SURFACE_COMMANDS
    from tests.test_barcode_surfaces import COMMANDS as BARCODE_SURFACE_COMMANDS
    from tests.test_serial_lot_surfaces import COMMANDS as SERIAL_LOT_SURFACE_COMMANDS
    from tests.test_bins_surfaces import COMMANDS as BIN_SURFACE_COMMANDS
    from tests.test_landed_cost_surfaces import COMMANDS as LANDED_COST_SURFACE_COMMANDS
    from tests.test_fifo_surfaces import COMMANDS as FIFO_SURFACE_COMMANDS
    from tests.test_verify_surfaces import COMMANDS as VERIFY_SURFACE_COMMANDS
    from tests.test_shipment_surfaces import COMMANDS as SHIPMENT_SURFACE_COMMANDS
    from tests.test_import_surfaces import COMMANDS as IMPORT_SURFACE_COMMANDS
    from tests.test_bank_feed_surfaces import COMMANDS as BANK_FEED_SURFACE_COMMANDS
    from tests.test_item_pictures_surfaces import COMMANDS as ITEM_PICTURE_SURFACE_COMMANDS
    from tests.test_bulk_edit_surfaces import COMMANDS as BULK_EDIT_SURFACE_COMMANDS
    from tests.test_user_home_surfaces import COMMANDS as USER_HOME_SURFACE_COMMANDS
    from tests.test_inventory_count_surfaces import COMMANDS as INVENTORY_COUNT_SURFACE_COMMANDS
    from tests.test_scheduling_surfaces import COMMANDS as SCHEDULING_SURFACE_COMMANDS
    from tests.test_perf_surfaces import COMMANDS as PERF_SURFACE_COMMANDS
    from tests.test_delivery_surfaces import COMMANDS as DELIVERY_SURFACE_COMMANDS
    registry.load_all()
    commands = registry.all_commands(include_standalone=True)
    assert {c.name for c in commands} == FROZEN_COMMANDS, 'New or removed command needs a deliberate coverage disposition'
    permissions = {row['command']: row for row in inventory()}
    result = []
    for cmd in commands:
        witness = ('tests/test_mcp_registry_browsing.py::test_list_browsing_four_interfaces' if cmd.name in BROWSING_COMMANDS else
                   'tests/test_mcp_registry_lists.py::test_each_list_lifecycle_valid_rejected_and_preview_parity' if cmd.name in LISTS else
                   'tests/test_mcp_registry_financial.py::test_financial_lifecycle_full_documents_and_ledger_parity' if cmd.name in FINANCIAL else
                   'tests/test_mcp_registry_supporting.py::test_supporting_family_full_documents_and_rejections' if any(cmd.name in names for names in FAMILIES.values()) else
                   'tests/test_mcp_registry_payment_preparation.py::test_payment_preparation_four_surface_documents_context_and_rejections' if any(cmd.name in names for names in PAYMENT_FAMILIES.values()) else
                   'tests/test_mcp_registry_payments.py::test_payment_financial_lifecycle_full_documents_and_exact_ledger' if cmd.name in PAYMENT_FINANCIAL else
                   'tests/test_mcp_registry_deposits.py::test_deposit_lifecycle_full_documents_and_exact_ledger' if cmd.name in DEPOSIT_COMMANDS else
                   'tests/test_reconciliation_commands.py::test_the_commands_certify_a_statement_that_ties_to_the_ledger' if cmd.name in RECONCILE_COMMANDS else
                   'tests/test_reconcile_trial_fixes.py::test_discard_drops_an_open_draft_from_review_and_keeps_its_history' if cmd.name == 'reconcile discard' else
                   'tests/test_reconcile_import.py::test_the_same_import_through_python_cli_http_and_mcp' if cmd.name in RECONCILE_IMPORT_COMMANDS else  # all four
                   'tests/test_mcp_registry_identity.py::test_identity_lifecycle_full_documents_owned_password_and_rejected_state' if cmd.name in IDENTITY_COMMANDS else
                   'tests/test_mcp_registry_agent.py::test_agent_administration_full_documents_on_four_actual_surfaces' if cmd.name in AGENT_COMMANDS else
                   'tests/test_money_out_documents.py::test_the_same_check_and_card_charge_through_python_cli_http_and_mcp' if cmd.name in MONEY_OUT_COMMANDS else
                   'tests/test_card_credit_surfaces.py::test_the_same_card_credit_through_python_cli_http_and_mcp' if cmd.name in CARD_CREDIT_COMMANDS else
                   'tests/test_company_backup.py::test_the_same_backup_and_restore_through_python_cli_http_and_mcp' if cmd.name in BACKUP_COMMANDS else
                   'tests/test_bill_entry.py::test_the_same_bill_through_python_cli_http_and_mcp' if cmd.name in BILL_COMMANDS else
                   'tests/test_bill_payment.py::test_the_same_bill_payment_through_python_cli_http_and_mcp' if cmd.name in BILL_PAYMENT_COMMANDS else
                   'tests/test_vendor_credit.py::test_the_same_vendor_credit_through_python_cli_http_and_mcp' if cmd.name in VENDOR_CREDIT_COMMANDS else
                   'tests/test_batch_invoicing_interfaces.py::test_the_same_batch_of_invoices_through_python_cli_http_and_mcp' if cmd.name in BATCH_COMMANDS else
                   'tests/test_inventory_interfaces.py::test_the_same_inventory_adjustment_through_python_cli_http_and_mcp' if cmd.name in INVENTORY_COMMANDS else
                   'tests/test_purchase_order.py::test_the_same_purchase_order_through_python_cli_http_and_mcp' if cmd.name in PURCHASE_ORDER_COMMANDS else
                   'tests/test_statement_charge.py::test_the_same_statement_charge_through_python_cli_http_and_mcp' if cmd.name in STATEMENT_CHARGE_COMMANDS else
                   'tests/test_credit_memo.py::test_the_same_credit_memo_through_python_cli_http_and_mcp' if cmd.name in CREDIT_MEMO_COMMANDS else
                   'tests/test_credit_memo_lifecycle.py::test_the_same_credit_memo_lifecycle_through_python_cli_http_and_mcp' if cmd.name in CREDIT_LIFECYCLE_COMMANDS else
                   'tests/test_credit_settlement.py::test_the_same_credit_application_through_python_cli_http_and_mcp' if cmd.name in CREDIT_SETTLEMENT_COMMANDS else
                   'tests/test_customer_refund_history_surfaces.py::test_retained_refund_history_on_four_actual_surfaces' if cmd.name in REFUND_HISTORY_COMMANDS else
                   'tests/test_customer_refund.py::test_the_same_refund_through_python_cli_http_and_mcp' if cmd.name in REFUND_COMMANDS else
                   'tests/test_sales_tax_remittance.py::test_the_same_sales_tax_remittance_through_python_cli_http_and_mcp' if cmd.name in SALES_TAX_COMMANDS else
                   'tests/test_sales_tax_adjustment.py::test_the_same_sales_tax_adjustment_through_python_cli_http_and_mcp' if cmd.name in SALES_TAX_ADJUSTMENT_COMMANDS else  # all four
                   'tests/test_transfer_funds.py::test_the_same_transfer_through_python_cli_http_and_mcp' if cmd.name in TRANSFER_COMMANDS else
                   'tests/test_payment_recovery_interfaces.py::test_complete_recovery_contract_on_all_four_interfaces' if cmd.name in RECOVERY_COMMANDS else
                   'tests/test_memorized_transactions.py::test_the_same_memorized_transaction_through_python_cli_http_and_mcp' if cmd.name in MEMORIZED_COMMANDS else
                   'tests/test_mcp_registry_hub_reads.py::test_hub_read_full_documents_and_scope_boundaries' if any(cmd.name in names for names in HUB_FAMILIES.values()) else
                   'tests/test_mcp_registry_work.py::test_nonposting_work_lifecycle_full_documents_and_lineage' if any(cmd.name in names for names in WORK_FAMILIES.values()) else
                   'tests/test_mcp_registry_work_billing.py::test_work_billing_full_documents_retries_and_exact_batches' if any(cmd.name in names for names in BILLING_FAMILIES.values()) else
                   'tests/test_mcp_registry_register.py::test_register_calculate_post_correct_query_and_rejection_parity' if cmd.name in REGISTER_COMMANDS else
                   'tests/test_mcp_registry_credentials.py::test_credentials_full_documents_owned_hashes_and_rejected_state' if cmd.name in CREDENTIAL_COMMANDS else
                   'tests/test_mcp_registry_presence.py::test_advisory_presence_exact_documents_and_no_business_mutation' if cmd.name in PRESENCE_COMMANDS else
                   'tests/test_mcp_registry_audit_reads.py::test_audit_activity_full_documents_historical_attribution_and_rejections' if cmd.name in AUDIT_COMMANDS else
                   'tests/test_mcp_registry_attachments.py::test_registered_binary_and_link_lifecycle_complete_parity' if cmd.name in ATTACHMENT_COMMANDS else
                   'tests/test_mcp_registry_compact.py::test_compaction_preview_collection_replay_and_rejection_parity' if cmd.name in COMPACT_COMMANDS else
                   'tests/test_mcp_registry_company_maintenance.py::test_company_maintenance_valid_preview_rejections_and_owned_move' if cmd.name in MAINTENANCE_COMMANDS else
                   'tests/test_mcp_registry_rollout.py::test_rollout_chart_profile_detach_reattach_full_documents' if cmd.name in ROLLOUT_COMMANDS else
                   'tests/test_mcp_registry_undo.py::test_undo_preview_compensation_replay_and_rejected_state' if cmd.name in UNDO_COMMANDS else
                   'tests/test_mcp_registry_demo_upgrade.py::test_owned_demo_replacement_and_current_schema_upgrade' if cmd.name in DEMO_UPGRADE_COMMANDS else
                   'tests/test_purchase_deletion_transports.py::test_cli_and_source_bound_mcp_delete_preview_refusal_replay_and_history' if cmd.name in PURCHASE_DELETE_COMMANDS else  # cli, mcp
                   'tests/test_sales_deletion_transports.py::test_cli_and_source_bound_mcp_delete_preview_refusal_replay_and_history' if cmd.name in SALES_DELETE_COMMANDS else  # cli, http, mcp
                   'tests/test_receiving_surfaces.py::test_receiving_and_linked_bill_cross_all_four_actual_transports' if cmd.name in RECEIVING_COMMANDS else  # all four
                   'tests/test_permission_setup_surfaces.py::test_permission_setup_crosses_all_four_actual_transports' if cmd.name in SETUP_SURFACE_COMMANDS else  # all four
                   'tests/test_bill_deletion_transports.py::test_bill_deletion_crosses_cli_http_and_source_bound_mcp_with_exact_books' if cmd.name in BILL_DELETE_COMMANDS else  # cli, http, mcp
                   'tests/test_payment_deletion_transports.py::test_payment_deletion_crosses_cli_http_and_source_bound_mcp_with_exact_books' if cmd.name in PAYMENT_DELETE_COMMANDS else  # cli, http, mcp
                   'tests/test_customer_refund_correction_surfaces.py::test_correcting_a_refund_crosses_all_four_actual_transports' if cmd.name in REFUND_CORRECTION_COMMANDS else  # all four
                   'tests/test_vendor_credit_correction_surfaces.py::test_correcting_and_reading_a_vendor_credit_crosses_all_four_actual_transports' if cmd.name in VENDOR_CREDIT_CORRECTION_COMMANDS else  # all four
                   'tests/test_credit_memo_deletion_transports.py::test_credit_memo_deletion_crosses_all_four_actual_transports' if cmd.name in CREDIT_DELETE_COMMANDS else
                   'tests/test_deposit_deletion_transports.py::test_deposit_deletion_crosses_all_four_actual_transports' if cmd.name in DEPOSIT_DELETE_COMMANDS else
                   'tests/test_journal_deletion_transports.py::test_journal_deletion_crosses_all_four_actual_transports' if cmd.name in JOURNAL_DELETE_COMMANDS else
                   'tests/test_report_export_surfaces.py::test_report_csv_is_the_same_file_on_every_surface' if cmd.name in REPORT_EXPORT_COMMANDS else  # all four
                   'tests/test_backup_schedule.py::test_schedule_list_verify_and_rehearse_through_python_cli_http_and_mcp' if cmd.name in BACKUP_SCHEDULE_COMMANDS else  # all four
                   'tests/test_entry_review.py::test_the_study_scenario_warns_the_agent_and_lands_on_the_owners_list' if cmd.name in ENTRY_REVIEW_COMMANDS else  # http
                   'tests/test_cutover_surfaces.py::test_the_same_move_in_through_python_cli_http_and_mcp' if cmd.name in CUTOVER_SURFACE_COMMANDS else  # all four
                   'tests/test_restore_deleted.py::test_an_agent_is_refused_over_the_transport' if cmd.name in RESTORE_SURFACE_COMMANDS else  # python, http
                   'tests/test_party_merge_surfaces.py::test_the_same_merge_through_python_cli_http_and_mcp' if cmd.name in MERGE_SURFACE_COMMANDS else  # all four
                   'tests/test_payment_bounce_surfaces.py::test_the_same_bounce_through_python_cli_http_and_mcp' if cmd.name in BOUNCE_SURFACE_COMMANDS else  # all four
                   'tests/test_vendor_1099_opening_surfaces.py::test_the_same_opening_1099_amount_through_python_cli_http_and_mcp' if cmd.name in VENDOR_1099_SURFACE_COMMANDS else  # all four
                   'tests/test_attachment_text.py::test_the_same_text_attachment_through_python_cli_http_and_mcp' if cmd.name in ATTACHMENT_TEXT_SURFACE_COMMANDS else  # all four
                   'tests/test_transaction_find.py::test_the_same_find_through_python_cli_http_and_mcp' if cmd.name in TRANSACTION_FIND_SURFACE_COMMANDS else  # all four
                   'tests/test_item_convert_surfaces.py::test_the_same_conversion_through_python_cli_http_and_mcp' if cmd.name in ITEM_CONVERT_SURFACE_COMMANDS else  # all four
                   'tests/test_finance_charge_surfaces.py::test_the_same_finance_charges_through_python_cli_http_and_mcp' if cmd.name in FINANCE_CHARGE_SURFACE_COMMANDS else  # all four
                   'tests/test_vendor_refund_surfaces.py::test_the_same_vendor_refund_through_python_cli_http_and_mcp' if cmd.name in VENDOR_REFUND_SURFACE_COMMANDS else  # all four
                   'tests/test_budget_surfaces.py::test_the_same_budget_through_python_cli_http_and_mcp' if cmd.name in BUDGET_SURFACE_COMMANDS else  # all four
                   'tests/test_customer_prepayment_surfaces.py::test_the_same_prepayment_through_python_cli_http_and_mcp' if cmd.name in PREPAYMENT_SURFACE_COMMANDS else  # all four
                   'tests/test_list_merge_surfaces.py::test_the_same_list_merge_through_python_cli_http_and_mcp' if cmd.name in LIST_MERGE_SURFACE_COMMANDS else  # all four
                   'tests/test_sales_order_surfaces.py::test_the_same_orders_through_python_cli_http_and_mcp' if cmd.name in SALES_ORDER_SURFACE_COMMANDS else  # all four
                   'tests/test_fixed_asset_surfaces.py::test_the_same_fixed_assets_and_loan_through_python_cli_http_and_mcp' if cmd.name in FIXED_ASSET_SURFACE_COMMANDS else  # all four
                   'tests/test_assembly_build_surfaces.py::test_the_same_assembly_builds_through_python_cli_http_and_mcp' if cmd.name in ASSEMBLY_BUILD_SURFACE_COMMANDS else  # all four
                   'tests/test_committed_stock_surfaces.py::test_the_same_open_orders_report_through_python_cli_http_and_mcp' if cmd.name in COMMITTED_STOCK_SURFACE_COMMANDS else  # all four
                   'tests/test_mileage_surfaces.py::test_the_same_mileage_through_python_cli_http_and_mcp' if cmd.name in MILEAGE_SURFACE_COMMANDS else  # all four
                   'tests/test_timekeeping_surfaces.py::test_the_same_timekeeping_through_python_cli_http_and_mcp' if cmd.name in TIMEKEEPING_SURFACE_COMMANDS else  # all four
                   'tests/test_credit_transfer_surfaces.py::test_the_same_credit_transfer_through_python_cli_http_and_mcp' if cmd.name in COLLECTIONS_SURFACE_COMMANDS else  # all four
                   'tests/test_pricing_rule_surfaces.py::test_the_same_price_rules_through_python_cli_http_and_mcp' if cmd.name in PRICING_RULE_SURFACE_COMMANDS else  # all four
                   'tests/test_order_invoicing_surfaces.py::test_the_same_order_invoice_through_python_cli_http_and_mcp' if cmd.name in ORDER_INVOICING_SURFACE_COMMANDS else  # all four
                   'tests/test_shortfall_surfaces.py::test_the_same_shortfall_purchase_through_python_cli_http_and_mcp' if cmd.name in SHORTFALL_SURFACE_COMMANDS else  # all four
                   'tests/test_purchase_order_partial_surfaces.py::test_the_same_partial_order_billing_through_python_cli_http_and_mcp' if cmd.name in PARTIAL_PO_SURFACE_COMMANDS else  # all four
                   'tests/test_inventory_site_surfaces.py::test_the_same_sites_and_transfers_through_python_cli_http_and_mcp' if cmd.name in INVENTORY_SITE_SURFACE_COMMANDS else  # all four
                   'tests/test_accountant_review_surfaces.py::test_the_same_accountant_review_through_python_cli_http_and_mcp' if cmd.name in ACCOUNTANT_REVIEW_SURFACE_COMMANDS else  # all four
                   'tests/test_check_printing_surfaces.py::test_the_same_check_printing_through_python_cli_http_and_mcp' if cmd.name in CHECK_PRINTING_SURFACE_COMMANDS else  # all four
                   'tests/test_barcode_surfaces.py::test_the_same_barcodes_through_python_cli_http_and_mcp' if cmd.name in BARCODE_SURFACE_COMMANDS else  # all four
                   'tests/test_serial_lot_surfaces.py::test_the_same_numbers_through_python_cli_http_and_mcp' if cmd.name in SERIAL_LOT_SURFACE_COMMANDS else  # all four
                   'tests/test_bins_surfaces.py::test_the_same_bins_and_cycle_counts_through_python_cli_http_and_mcp' if cmd.name in BIN_SURFACE_COMMANDS else  # all four
                   'tests/test_landed_cost_surfaces.py::test_the_same_landed_cost_through_python_cli_http_and_mcp' if cmd.name in LANDED_COST_SURFACE_COMMANDS else  # all four
                   'tests/test_fifo_surfaces.py::test_the_same_costing_method_switch_through_python_cli_http_and_mcp' if cmd.name in FIFO_SURFACE_COMMANDS else  # all four
                   'tests/test_verify_surfaces.py::test_the_same_verification_through_python_cli_http_and_mcp' if cmd.name in VERIFY_SURFACE_COMMANDS else  # all four
                   'tests/test_shipment_surfaces.py::test_the_same_shipment_through_python_cli_http_and_mcp' if cmd.name in SHIPMENT_SURFACE_COMMANDS else  # all four
                   'tests/test_import_surfaces.py::test_the_same_import_through_python_cli_http_and_mcp' if cmd.name in IMPORT_SURFACE_COMMANDS else  # all four
                   'tests/test_bank_feed_surfaces.py::test_the_same_bank_import_through_python_cli_http_and_mcp' if cmd.name in BANK_FEED_SURFACE_COMMANDS else  # all four
                   'tests/test_item_pictures_surfaces.py::test_the_same_item_picture_through_python_cli_http_and_mcp' if cmd.name in ITEM_PICTURE_SURFACE_COMMANDS else  # all four
                   'tests/test_bulk_edit_surfaces.py::test_the_same_grid_edit_through_python_cli_http_and_mcp' if cmd.name in BULK_EDIT_SURFACE_COMMANDS else  # all four
                   'tests/test_user_home_surfaces.py::test_the_same_home_through_python_cli_http_and_mcp' if cmd.name in USER_HOME_SURFACE_COMMANDS else  # all four
                   'tests/test_inventory_count_surfaces.py::test_the_same_count_through_python_cli_http_and_mcp' if cmd.name in INVENTORY_COUNT_SURFACE_COMMANDS else  # all four
                   'tests/test_scheduling_surfaces.py::test_the_same_to_dos_notices_and_reminders_through_python_cli_http_and_mcp' if cmd.name in SCHEDULING_SURFACE_COMMANDS else  # all four
                   'tests/test_perf_surfaces.py::test_the_same_timings_report_through_python_cli_http_and_mcp' if cmd.name in PERF_SURFACE_COMMANDS else  # all four
                   'tests/test_delivery_surfaces.py::test_the_same_delivery_through_python_cli_http_and_mcp' if cmd.name in DELIVERY_SURFACE_COMMANDS else  # all four
                   'tests/test_mcp_local_boundary.py::test_installed_local_boundaries_are_explicit_and_do_not_execute' if cmd.name in LOCAL_COMMANDS else None)
        mode = ('standalone_protocol' if cmd.protocol_stdout else 'standalone_local' if cmd.standalone else
                'local_lifecycle' if cmd.local_only else 'binary_' + cmd.transfer.direction if cmd.transfer else
                'advisory' if cmd.kind == 'advisory' else 'finite_poll_with_local_follow' if cmd.streams else 'routed_json')
        result.append({'command': cmd.name, 'mode': mode, 'scope': cmd.scope, 'kind': cmd.kind,
            'preview': cmd.is_write, 'idempotency_key': cmd.accepts_idempotency_key,
            'clearable': cmd.clearable, 'execution_witness': witness,
            'coverage': _coverage(cmd, witness, LOCAL_COMMANDS),
            'surfaces': _surfaces(witness),
            'pending_reason': PENDING_REASONS.get(cmd.name),
            'local_valid_witnesses': LOCAL_VALID_WITNESSES.get(cmd.name, []),
            'publication': permissions[cmd.name]})
    return result


def _witness_surfaces(witness):
    """The surfaces a witness test says it drives, or None when it does not say.

    Read from the witness module's own `SURFACES`, beside the `COMMANDS` it declares, because the
    test is the only thing that knows. A witness that does not declare is not assumed to drive all
    four -- it is labelled for what is known about it, which is that it drives the product over
    some transport.
    """
    import importlib
    module = importlib.import_module(witness.split('::')[0].removesuffix('.py').replace('/', '.'))
    declared = getattr(module, 'SURFACES', None)
    return frozenset(declared) if declared else None


ALL_SURFACES = frozenset(('python', 'cli', 'http', 'mcp'))


def _surfaces(witness):
    if witness is None:
        return None
    try:
        declared = _witness_surfaces(witness)
    except Exception:
        return None
    return tuple(sorted(declared)) if declared else None


def _coverage(cmd, witness, local_commands):
    """What is actually known about this command's executed evidence.

    `four_surface_scenario` is reserved for a witness that declares all four surfaces. Everything
    else with a witness is `transport_scenario`: real executed evidence over at least one real
    transport, which is not the same claim. The label used to be `four_surface_scenario` for every
    witness, which overstated every two- and three-surface row in the generated ledger -- and a
    comment telling a reader not to trust the label does not fix a generated artifact.
    """
    if cmd.name in local_commands:
        return 'local_lifecycle_scenario'
    if witness is None:
        return 'pending_four_surface_or_local_lifecycle'
    return 'four_surface_scenario' if _surfaces(witness) and frozenset(_surfaces(witness)) == ALL_SURFACES \
        else 'transport_scenario'


class Controls(HTMLParser):
    def __init__(self, page):
        super().__init__()
        self.named = {}
        self.ids = {}
        self.feed(page)

    def handle_starttag(self, tag, attributes):
        attributes = dict(attributes)
        if 'id' in attributes:
            self.ids.setdefault(attributes['id'], []).append({'tag': tag, **attributes})
        if tag in {'input', 'select', 'textarea'} and 'name' in attributes:
            self.named.setdefault(attributes['name'], []).append({'tag': tag, **attributes})


def variant_policies():
    """Finite structural cases, with outstanding behavior separate from node count.

    These references locate executable evidence. They are not stored test results
    and never turn an unexecuted or uncovered branch into browser acceptance.
    """
    nested = 'tests/test_mcp_nested_gui_browser.py::test_nested_collection_model_object_null_and_full_error'
    parent = 'tests/test_mcp_nested_gui_browser.py::test_nullable_parent_object_clear_is_distinct_from_omission_and_child_patch'
    boolean = 'tests/test_mcp_workbench_control_browser.py::test_generated_boolean_false_omission_and_null_encoding'
    number = 'tests/test_mcp_rate_gui_browser.py::test_rate_exact_integer_decimal_conflict_destination_noop_and_replay'
    custom = 'tests/test_row8_custom_field_browser.py::test_keyboard_all_kinds_48_fields_splits_restore_clear'
    money = 'tests/test_numeric_entry_browser.py::test_generated_sales_math_preview_and_save'
    payment = 'tests/test_mcp_payment_form_browser.py::test_saved_selection_control_previews_real_receipt'
    return {
        ('anyOf', ('Address', 'null')): ([parent, 'tests/test_mcp_address_payee_browser.py::test_sales_address_default_explicit_null_and_return_to_current_default'], []),
        ('anyOf', ('AddressInput', 'null')): ([parent, 'tests/test_mcp_address_payee_browser.py::test_party_address_create_patch_clear_and_omission'], []),
        ('anyOf', ('RegisterParty', 'null')): ([nested, parent, 'tests/test_mcp_address_payee_browser.py::test_top_level_register_payee_prefill_null_replacement_and_journal_destination'], []),
        ('anyOf', ('CheckParty', 'null')): ([
            'tests/test_money_out_browser.py::test_the_check_window_posts_the_check_the_command_posts',
            'tests/test_money_out_browser.py::test_the_expenses_grid_has_nothing_to_scroll_sideways_at_phone_width'],
            ['the document payee is driven at /pay_to on both nouns — a vendor chosen on the check window, '
             'and the picker left at its opening value at 390px on check and card charge alike; the per-line '
             '/expenses/[]/party alternative has no column in the Expenses grid and no browser case at all']),
        ('anyOf', ('MoneyInput', 'string')): (['tests/test_mcp_nested_gui_browser.py::test_nested_collection_model_object_null_and_full_error', 'tests/test_mcp_money_gui_browser.py::test_structured_integer_money_object_exact_bytes_and_core_rejection'], []),
        ('anyOf', ('MoneyInput', 'null', 'string')): ([
            'tests/test_mcp_money_gui_browser.py::test_structured_integer_money_object_exact_bytes_and_core_rejection',
            'tests/test_bill_payment.py::test_the_same_bill_payment_through_python_cli_http_and_mcp'],
            ['the omitted branch of a bill-payment amount means "everything still open on that bill"; '
             'there is no Pay Bills form, so no browser case reaches it and the four-surface witness '
             'is the only executable evidence']),
        ('anyOf', ('SalesMoneyInput', 'null', 'string')): ([money, 'tests/test_mcp_money_gui_browser.py::test_top_level_money_object_nullable_branch_and_complete_calculation', 'tests/test_mcp_sales_money_origin_browser.py::test_sales_line_money_object_null_rejection_and_current_default_origin'], []),
        # R150: a unit price or cost, exact to three places past the minor unit. The sales-line
        # witness drives its null and integer-object branches; the sub-cent browser case submits
        # 3.459 as text through the real hx-post form.
        ('anyOf', ('SalesPriceInput', 'null', 'string')): ([
            'tests/test_mcp_sales_money_origin_browser.py::test_sales_line_money_object_null_rejection_and_current_default_origin',
            'tests/test_subcent_prices_browser.py::test_a_sub_cent_rate_typed_in_the_invoice_window_posts_exactly'], []),
        ('anyOf', ('SignedMoney', 'string')): ([
            'tests/test_mcp_registry_deposits.py::test_deposit_lifecycle_full_documents_and_exact_ledger'],
            ['the deposit witnesses enter signed decimal strings; the structured signed-money object has no dedicated browser case yet']),
        ('anyOf', ('SalesMoneyInput', 'string')): ([money, 'tests/test_mcp_money_gui_browser.py::test_top_level_money_object_nullable_branch_and_complete_calculation'], []),
        ('anyOf', ('any', 'null')): ([custom, 'tests/test_mcp_definition_kinds_browser.py::test_definition_all_kinds_default_empty_null_omission_and_choice_visibility', 'tests/test_mcp_any_value_browser.py::test_any_money_top_level_and_nested_json_values_and_unchanged_text'], []),
        ('anyOf', ('array', 'null')): ([custom, 'tests/test_mcp_nested_gui_browser.py::test_optional_nested_collection_order_empty_null_and_omission'], []),
        ('anyOf', ('boolean', 'null')): ([boolean], []),
        ('anyOf', ('integer', 'null')): ([number, 'tests/test_mcp_nested_gui_browser.py::test_optional_nested_collection_order_empty_null_and_omission'], []),
        ('anyOf', ('null', 'object')): ([custom, 'tests/test_mcp_payment_form_browser.py::test_generated_payment_json_object_error_preview_and_saved_false'], []),
        ('anyOf', ('null', 'string')): ([boolean], []),
        ('oneOf', ('BoolCriterion', 'ChoiceCriterion', 'DateCriterion', 'NumberCriterion', 'PresenceCriterion', 'TextCriterion')): ([
            'tests/test_list_browsing_browser.py::test_named_columns_filters_and_readable_collections',
            'tests/test_list_browsing_browser.py::test_reorder_reset_zero_missing_paging_and_stale_restart',
            'tests/test_list_browsing_text_date_browser.py::test_text_date_named_criteria_exact_wire_results_and_retained_identity'],
            ['Dedicated list chooser witnesses cover representative Boolean/number/choice/presence/text/date cases; untested operators, malformed-date interactions and all generated-form criterion branches remain open.']),
        ('anyOf', ('CashBackInput', 'null')): ([
            'tests/test_mcp_registry_deposits.py::test_deposit_lifecycle_full_documents_and_exact_ledger'],
            ['the deposit correction witness supplies a cash-back line and the post witness omits it; explicit-null clearing has no dedicated browser case yet']),
        ('oneOf', ('DraftDocument', 'InlineDocument')): ([
            'tests/test_mcp_registry_deposits.py::test_deposit_lifecycle_full_documents_and_exact_ledger'],
            ['durable deposit drafts and their selection dialog are not registered yet, so only the inline branch has a form']),
        ('oneOf', ('DraftDocument', 'ReplacementDocument')): ([
            'tests/test_mcp_registry_deposits.py::test_deposit_lifecycle_full_documents_and_exact_ledger'],
            ['durable deposit drafts and their selection dialog are not registered yet, so only the inline branch has a form']),
        ('oneOf', ('InlineApplications', 'SelectionReference')): ([payment, 'tests/test_mcp_nested_payment_request_browser.py::test_all_six_nested_preview_request_branches_exact_input_results_and_inactive_controls'], []),
        ('oneOf', ('InlineApplications', 'SelectionReference', 'SuggestedApplications')): ([payment, 'tests/test_v15_trial_agent.py::test_actual_mcp_bulk_receipt_arrives_within_budget_with_its_warning'],
            ['payment receive applications mode suggested (V1.5 trials) has an actual-MCP witness; its browser form branch has no dedicated case yet']),
        ('oneOf', ('InlineCalculation', 'SelectionReference')): ([payment, 'tests/test_mcp_calculation_variant_browser.py::test_calculation_inline_null_origin_rejections_and_saved_selection'], ['parent-owned payment-selection workspace/navigation and combined-base acceptance']),
        ('oneOf', ('ApplyPreviewRequest', 'InvoiceUpdatePreviewRequest', 'ReceivePreviewRequest', 'UnapplyPreviewRequest', 'UpdatePreviewRequest', 'VoidPreviewRequest')): (['tests/test_mcp_nested_payment_request_browser.py::test_all_six_nested_preview_request_branches_exact_input_results_and_inactive_controls'], []),
        ('anyOf', ('DiscrepancyAdjustment', 'null')): ([
            'tests/test_reconciliation_discrepancies.py::test_a_person_finishes_with_an_adjustment_for_the_exact_difference',
            'tests/test_reconciliation_discrepancies.py::test_an_agent_is_refused_and_the_draft_stays_open',
            'tests/test_reconciliation_window_browser.py::test_a_person_finishes_a_statement_that_will_not_tie_with_an_adjustment'],
            []),
        ('anyOf', ('StatementMoney', 'string')): ([
            'tests/test_reconciliation_commands.py::test_a_statement_balance_is_money_and_may_be_zero_or_negative',
            'tests/test_reconciliation_commands.py::test_an_overdrawn_statement_reconciles',
            'tests/test_reconciliation_window_browser.py::test_a_person_follows_the_tile_and_ends_holding_a_certificate'],
            []),
        ('oneOf', ('AttachmentEvidence', 'TransactionEvidence')): ([
            'tests/test_reconciliation_commands.py::test_the_commands_certify_a_statement_that_ties_to_the_ledger',
            'tests/test_reconciliation_browser.py::test_every_reconcile_command_has_a_page_that_answers'],
            ['the opening witnesses pass an empty reference list, so neither evidence branch is '
             'entered by any executed case']),
    }


def variant_key(variant):
    return (variant['combination'], tuple(sorted(
        branch.get('$ref', branch.get('type', 'any')).split('/')[-1]
        for branch in variant['branches'])))


def workbench_variant_map(rows):
    policies = variant_policies()
    grouped = {}
    for row in rows:
        if row['url'] is None:
            continue
        for variant in row['schema_variants']:
            key = variant_key(variant)
            assert key in policies, (row['command'], variant['path'], key)
            witnesses, remaining = policies[key]
            group = grouped.setdefault(key, {'combination': key[0], 'branches': key[1],
                'browser_witnesses': witnesses, 'remaining_material_cases': remaining,
                'status': 'material_cases_open' if remaining else 'shared_representation_witnesses_linked',
                'paths': []})
            group['paths'].append({'command': row['command'], 'path': variant['path'],
                                  'nested_alternative': '/oneOf/' in variant['path'],
                                  'surface': row.get('surface', 'generated_form'),
                                  'representation_limit': row.get('schema_variant_limits', 'representative schema witnesses; not individual path acceptance')})
    return list(grouped.values())


def schema_variants(schema):
    found = []
    def visit(value, path, references=()):
        if not isinstance(value, dict):
            return
        if '$ref' in value:
            ref = value['$ref']
            if ref in references:
                return
            target = schema
            for part in ref[2:].split('/'):
                target = target[part.replace('~1', '/').replace('~0', '~')]
            visit(target, path, (*references, ref))
        for branch in ('oneOf', 'anyOf'):
            if branch in value:
                found.append({'path': path, 'combination': branch, 'discriminator': value.get('discriminator'),
                              'branches': value[branch], 'browser_acceptance': 'see_material_variant_map_and_override_requirements'})
                for index, child in enumerate(value[branch]):
                    visit(child, path + '/' + branch + '/' + str(index), references)
        for name, child in value.get('properties', {}).items():
            visit(child, path + '/' + name, references)
        if 'items' in value:
            visit(value['items'], path + '/[]', references)
    visit(schema, '')
    return found


PAYMENT_WORKSPACE_WITNESSES = {
    'receive': 'tests/test_mcp_payment_form_browser.py::test_saved_selection_control_previews_real_receipt',
    'apply': 'tests/test_customer_payment_browser.py::test_receive_shared_origins_preview_unapply_reuse',
    'update': 'tests/test_customer_payment_browser.py::test_correct_receipt_and_applied_invoice_with_customs',
    'unapply': 'tests/test_customer_payment_browser.py::test_receive_shared_origins_preview_unapply_reuse',
    'void': 'tests/test_customer_payment_browser.py::test_lost_response_exact_recovery_locks_draft_and_voids_without_duplicate',
}


def payment_workspace_row(cmd, url, page_text, config):
    """Inventory actual workspace controls/state; registry alternatives stay explicit."""
    assert cmd.noun == 'payment' and cmd.verb in PAYMENT_WORKSPACE_WITNESSES
    assert config['mode'] == cmd.verb
    controls = Controls(page_text).ids
    # Closed mapping: a newly registered input path must acquire an explicit
    # workspace representation or unsupported-alternative disposition.
    mapping = {name: ('control', 'payment-' + identity, 'visible input') for name, identity in {
        'customer': 'customer', 'date': 'date', 'amount': 'amount',
        'payment_method': 'method', 'ar_account': 'ar', 'deposit_to': 'destination',
        'number': 'number', 'reference': 'reference', 'memo': 'memo',
    }.items()}
    mapping.update({
        'payment': ('captured_state', 'payment-config', 'initial.id'),
        'expected_version': ('captured_state', 'payment-config', 'initial.version'),
        'settlement_guard': ('captured_state', 'payment-config', 'initial.settlement_guard'),
        'operation_key': ('generated_state', 'payment-save', 'one retained operation key per intent'),
        'expected_facts_fingerprint': ('preview_state', 'payment-preview', 'complete preview response fingerprint retained for save'),
        'custom_fields': ('runtime_controls', 'payment-custom', 'definition-specific Keep/Set/Clear values'),
        'expected_custom_field_kinds': ('runtime_state', 'payment-custom', 'captured kinds for explicitly set values'),
        'applications.mode': ('selected_branch', 'payment-invoices', 'constant selection; no inline request from workspace'),
        'applications.selection': ('shared_state', 'payment-invoices', 'durable draft.id'),
        'applications.expected_version': ('shared_state', 'payment-invoices', 'durable draft.version'),
        'applications[].application_id': ('selected_rows', 'payment-invoices', 'selected recorded application identity'),
        'applications[].invoice_expected_version': ('captured_state', 'payment-invoices', 'selected application invoice version'),
        # Early-payment discounts (R132): typed on an invoice row of the receive workspace.
        'discounts[].invoice': ('selected_rows', 'payment-invoices', 'the invoice row the discount is typed on'),
        'discounts[].amount': ('row_controls', 'payment-invoices', 'the row discount input, or its Take suggestion'),
        'discounts[].expected_version': ('captured_state', 'payment-invoices', 'the discounted invoice row version'),
        'discount_account': ('registry_alternative_not_emitted', None,
                             "the company's customer discount account preference; the workspace does not choose one"),
        # The anchor's Underpayment choice (R148), shown once a preview finds the cash short: Write off sends
        # `underpayment: write_off` with its account and class; Leave open sends nothing.
        'underpayment': ('control', 'payment-underpayment-write-off', 'Write off the extra amount (else leave open)'),
        'write_off_account': ('control', 'payment-write-off-account', 'visible input under Write off'),
        'write_off_class': ('control', 'payment-write-off-class', 'visible input under Write off'),
        'discount_class': ('registry_alternative_not_emitted', None,
                           'a typed discount posts without a class from the workspace; agents may name one'),
    })
    for path in ('applications.items[].invoice', 'applications.items[].expected_version',
                 'applications.items[].amount', 'invoice_versions[].invoice', 'invoice_versions[].expected_version'):
        mapping[path] = ('registry_alternative_not_emitted', None,
                         'workspace uses durable selection or signed settlement guard; no direct input for this alternative')
    # applications {"mode": "suggested"} (d0620f4) is the agent's one-call alternative; the
    # workspace asks `payment suggest` itself and saves the chosen rows as its durable selection.
    mapping['applications.strategy'] = ('registry_alternative_not_emitted', None,
                                        'workspace suggests through payment suggest and applies a durable selection; it never sends mode "suggested"')
    paths = []
    for field in model_fields(cmd.input_model, leaves_only=True):
        assert field.path in mapping, (cmd.name, field.path)
        representation, identity, meaning = mapping[field.path]
        if identity:
            assert len(controls.get(identity, [])) == 1, (cmd.name, field.path, identity)
        paths.append({'path': field.path, 'control_family': 'payment_workspace',
                      'schema_type': field.type, 'required': field.required, 'nullable': field.nullable,
                      'representation': representation, 'control_id': identity, 'meaning': meaning,
                      'actual_tags': controls.get(identity, []) if identity else [],
                      'browser_witness': PAYMENT_WORKSPACE_WITNESSES[cmd.verb],
                      'browser_acceptance': 'workspace_mode_linked_not_individual_schema_alternative_acceptance'})
    return {'command': cmd.name, 'url': url, 'surface': 'payment_workspace', 'mode': config['mode'],
            'input_paths': paths, 'schema_variants': schema_variants(cmd.input_model.model_json_schema()),
            'schema_variant_limits': 'Registry alternatives are inventoried, not all emitted by this workspace; amounts use exact decimal text, applications use shared selections, update uses signed settlement guard.',
            'form_witness': 'tests/test_row3_host.py::test_every_routed_command_has_a_form_with_one_control_per_input_leaf',
            'context': {'encoding': 'workspace command JSON; reason header from payment-reason', 'fields': ['reason']},
            'output_policy_source': 'src/bookflow/adapters/workbench/static/payments.js:drawPreview/drawRecord',
            'error_policy_source': 'src/bookflow/adapters/workbench/static/payments.js',
            'success_policy_source': 'src/bookflow/adapters/workbench/static/payments.js:save',
            'browser_witness': PAYMENT_WORKSPACE_WITNESSES[cmd.verb]}


def command_variant_witnesses(name):
    """Authored browser branches, not acceptance of every related schema path."""
    groups = [
        ({'customer create', 'customer update'}, 'test_mcp_address_payee_browser', 'test_party_address_create_patch_clear_and_omission'),
        ({'register post', 'register update'}, 'test_mcp_address_payee_browser', 'test_top_level_register_payee_prefill_null_replacement_and_journal_destination'),
        ({'invoice post', 'invoice update'}, 'test_mcp_address_payee_browser', 'test_sales_address_default_explicit_null_and_return_to_current_default'),
        ({'invoice post', 'invoice update'}, 'test_mcp_sales_money_origin_browser', 'test_sales_line_money_object_null_rejection_and_current_default_origin'),
        ({'custom-field update'}, 'test_mcp_definition_kinds_browser', 'test_definition_all_kinds_default_empty_null_omission_and_choice_visibility'),
        ({'payment calculate'}, 'test_mcp_calculation_variant_browser', 'test_calculation_inline_null_origin_rejections_and_saved_selection'),
        ({'customer create', 'customer update', 'item create'}, 'test_mcp_any_value_browser', 'test_any_money_top_level_and_nested_json_values_and_unchanged_text'),
        ({'term create', 'term update', 'account create', 'item create', 'price-level create', 'price-level update'}, 'test_mcp_visibility_browser', 'test_command_visibility_families_exclude_old_controls_and_save_to_exact_record'),
        ({'organization new', 'organization rename', 'company new', 'company detach', 'company attach', 'chart list', 'demo reset'}, 'test_mcp_hub_destination_browser', 'test_hub_organization_and_company_new_attach_destinations_with_complete_receipts'),
    ]
    return ['tests/' + module + '.py::' + test for commands, module, test in groups if name in commands]


def workbench_row(cmd, url, page_text):
    """Actual page controls plus schema paths; interaction acceptance is separate."""
    described = forms.describe_fields(cmd.noun, cmd.verb, cmd.input_model, None, None)
    controls = Controls(page_text).named
    paths = []
    policies = workbench_family_policies()
    for field in model_fields(cmd.input_model, leaves_only=True):
        leaf = next((row for row in described if field.path == row['path'] or field.path.startswith(row['path'] + '[]')), None)
        children = [row for row in described if row['path'].startswith(field.path + '.')]
        if leaf is None and children:
            # Introspection reports a model union as one schema leaf; the
            # renderer supplies each discriminated branch as real controls.
            leaf = {'kind': 'discriminated_model', 'path': field.path}
        assert leaf is not None, (cmd.name, field.path)
        control = leaf['kind']
        actual = controls.get('f:' + field.path, [])
        if any('data-ref-value' in node for node in actual):
            control = 'reference_combobox'
        if field.path.startswith('custom_fields') or field.path.startswith('custom_field_kinds'):
            control = 'runtime_custom_fields' if 'name="cf:' in page_text else leaf['kind']
        elif cmd.noun in ('estimate', 'work-order') and cmd.verb in ('invoice', 'sales-receipt') and field.path.split('[')[0] in ('line_ids', 'selections', 'percent'):
            control = 'billing_selection'
        elif field.path == 'cursor' and cmd.name in ('report balance-sheet', 'report profit-and-loss'):
            control = 'statement_continuation'
        elif leaf.get('definition_default'):
            control = 'typed_definition_default'
        witness, limits = policies[control]
        paths.append({'path': field.path, 'control_family': control, 'schema_type': field.type,
            'required': field.required, 'nullable': field.nullable,
            'visible_when': leaf.get('visible_when'), 'choices': leaf.get('choices'),
            'rendered_children': [{'path': row['path'], 'visibility_cases': row.get('visibility_cases'),
                                   'kind': row['kind'], 'choices': row.get('choices')} for row in children],
            'actual_tags': [{'tag': node['tag'], 'type': node.get('type'), 'structured_alternative': node.get('data-math-structured')} for node in actual],
            'browser_witness': witness,
            'browser_acceptance': 'representative_family_mapped_not_individual_path_acceptance',
            'representative_limits': limits})
    return {'command': cmd.name, 'url': url, 'input_paths': paths,
            'schema_variants': schema_variants(cmd.input_model.model_json_schema()),
            'command_variant_witnesses': command_variant_witnesses(cmd.name),
            'command_variant_limits': 'Authored cases at 1280/390; references are not stored results or exhaustive command acceptance.',
            'form_witness': 'tests/test_row3_host.py::test_every_routed_command_has_a_form_with_one_control_per_input_leaf',
            'context': {'encoding': 'ctx: fields to shared headers; action to dry_run',
                        'fields': [name for name in ('reason', 'source_ref', 'directive', 'idempotency_key') if 'ctx:' + name in controls]},
            'output_policy_source': 'src/bookflow/adapters/workbench/templates/form.html',
            'error_policy_source': 'src/bookflow/adapters/workbench/pages.py:submit and error.html',
            'success_policy_source': 'src/bookflow/adapters/workbench/pages.py:_success_target',
            'output_and_success_interaction': {
                'scope': 'representative shared renderer behavior; not every command success target',
                'preview_save_error': 'tests/test_mcp_workbench_control_browser.py::test_generated_boolean_false_omission_and_null_encoding',
                'read_completion_label': 'tests/test_mcp_money_gui_browser.py::test_top_level_money_object_nullable_branch_and_complete_calculation',
                'secret_output': 'tests/test_mcp_workbench_control_browser.py::test_generated_password_error_preview_and_save_never_echo_secret',
                'sale_detail_history': 'tests/test_service_sales_browser.py::test_generated_sale_preview_correct_history_and_void',
                'file_handoff': 'tests/test_mcp_file_gui_browser.py::test_installed_mcp_file_browser_and_agent_continuation',
                'nested_object_and_company_fallback_receipt': 'tests/test_mcp_nested_gui_browser.py::test_nested_collection_model_object_null_and_full_error',
                'own_detach_redirect_receipt': 'tests/test_mcp_detach_gui_browser.py::test_generated_own_detach_preview_redirect_and_full_receipt' if cmd.name == 'company detach' else None,
                'rate_destination_and_flags': 'tests/test_mcp_rate_gui_browser.py::test_rate_exact_integer_decimal_conflict_destination_noop_and_replay' if cmd.name == 'rate set' else None},
            'specialized_annotation_witness': 'tests/test_row6_workbench.py::test_real_browser_notes_files_conflicts_drafts_and_narrow_keyboard' if cmd.noun in {'note', 'attachment', 'activity'} else None}


def local_workbench_boundaries():
    return [{'command': cmd.name, 'url': None, 'boundary': 'local-only: no routed workbench command',
             'input_paths': [{'path': field.path, 'control_family': 'local_invocation', 'schema_type': field.type,
                              'browser_acceptance': 'not_applicable_local_only'}
                             for field in model_fields(cmd.input_model, leaves_only=True)],
             'schema_variants': schema_variants(cmd.input_model.model_json_schema()),
            'command_variant_witnesses': command_variant_witnesses(cmd.name),
            'command_variant_limits': 'Authored cases at 1280/390; references are not stored results or exhaustive command acceptance.',
             'local_lifecycle_coverage': 'local_lifecycle_scenario',
             'local_execution_witnesses': LOCAL_VALID_WITNESSES[cmd.name],
             'hosted_rejection_witness': 'tests/test_mcp_local_boundary.py::test_installed_local_boundaries_are_explicit_and_do_not_execute'}
            for cmd in registry.all_commands(include_standalone=True) if cmd.local_only or cmd.standalone]


def workbench_family_policies():
    """Representative witnesses, with the semantics each actually exercises."""
    typed = 'tests/test_mcp_workbench_typed_default_browser.py::test_generated_boolean_default_is_a_boolean_on_preview_and_save'
    payment = 'tests/test_mcp_payment_form_browser.py::test_saved_selection_control_previews_real_receipt'
    sale = 'tests/test_service_sales_browser.py::test_generated_sale_preview_correct_history_and_void'
    return {
        'text': (typed, 'custom-field name; ordinary Unicode and error preservation remain per-command checks'),
        'number': ('tests/test_mcp_rate_gui_browser.py::test_rate_exact_integer_decimal_conflict_destination_noop_and_replay', 'generated exact rate entry; captured workspace versions are separate'),
        'choice': (typed, 'definition kind and scopes choices'),
        'reference_combobox': (sale, 'customer, account and sale-line item lookup stores exact IDs'),
        'bool': ('tests/test_mcp_workbench_control_browser.py::test_generated_boolean_false_omission_and_null_encoding', 'actual false, omitted value, rejected nonnullable clear, nullable fax clear; URL encoding, preview and persistence'),
        'collection': (typed, 'primitive scopes collection; structured sale lines have separate sale witness'),
        'typed_definition_default': (typed, 'false/true definition default preview and save'),
        'runtime_custom_fields': ('tests/test_row8_custom_field_browser.py::test_generated_preview_error_retains_attempts_after_inventory_changes', 'generated custom-field values and rejected stale inventory retain attempted values'),
        'billing_selection': ('tests/test_work_billing_browser.py::test_generic_billing_card_selects_source_before_preview', 'source selection and billing preview; selection variants remain separately covered'),
        'discriminated_model': ('tests/test_mcp_nested_payment_request_browser.py::test_all_six_nested_preview_request_branches_exact_input_results_and_inactive_controls', 'served generated nested preview request branches; dedicated payment workspace is separate'),
        'json': ('tests/test_mcp_payment_form_browser.py::test_generated_payment_json_object_error_preview_and_saved_false', 'served generated payment-preview object textarea: malformed JSON retained and false encoded; dedicated workspace separately previews/saves false with exact fingerprint'),
        'statement_continuation': ('tests/test_financial_statements_browser.py::test_statements_from_navigation_paging_and_current_ledger', 'statement paging and current-books drill-down'),
        'secret': ('tests/test_mcp_workbench_control_browser.py::test_generated_password_error_preview_and_save_never_echo_secret', 'owned password form error and preview never echo secret, save changes verified hash; other secret commands retain core permissions'),
        'local_invocation': ('tests/test_mcp_local_boundary.py::test_installed_local_boundaries_are_explicit_and_do_not_execute', 'no workbench route; execution_map links actual per-command local lifecycle witnesses separately from installed MCP/HTTP rejection'),
        'dedicated_page': ('tests/test_company_backup.py::test_the_browser_backs_up_and_restores_from_the_company_menu', 'a command with a page of its own (backing up, restoring) takes its inputs as the controls of that page rather than a generated form'),
        'delete_confirmation': ('tests/test_purchase_deletion_browser.py::test_confirmation_refresh_keeps_draft_and_requires_new_confirmation', 'a Delete is made from its own record: the confirmation page carries the record in its URL and its version, key and reason as controls; preview, confirm and refresh per family'),
    }


def delete_confirmation_row(cmd, url, page_text, *, surface='record_confirmation', family='delete_confirmation'):
    """A Delete has no generic form: it is confirmed on the record's own page (as a command
    with a page of its own, such as a backup, takes its inputs there)."""
    return {'command': cmd.name, 'url': url, 'surface': surface,
            'input_paths': [{'path': field.path, 'control_family': family, 'schema_type': field.type,
                             'required': field.required, 'nullable': field.nullable,
                             'browser_acceptance': surface}
                            for field in model_fields(cmd.input_model, leaves_only=True)],
            'schema_variants': schema_variants(cmd.input_model.model_json_schema()),
            'command_variant_witnesses': command_variant_witnesses(cmd.name),
            'command_variant_limits': 'Authored cases at 1280/390; references are not stored results or exhaustive command acceptance.',
            'form_witness': 'tests/test_row3_host.py::test_every_routed_command_has_a_form_with_one_control_per_input_leaf'}


def workbench_family_map(rows):
    """Map every rendered path to a representative, not individual-path acceptance.

    Actual run evidence is separate. Payment workspace integration and fresh
    blind-agent acceptance are not implied by this shared-control inventory.
    """
    policies = workbench_family_policies()
    grouped = {name: {'family': name, 'paths': [], 'representative_witness': witness,
                      'limits': limits, 'status': 'representative_test_mapped' if witness else 'pending_browser_or_local_witness'}
               for name, (witness, limits) in policies.items()}
    grouped['payment_workspace'] = {'family': 'payment_workspace', 'paths': [],
        'representative_witnesses': PAYMENT_WORKSPACE_WITNESSES,
        'limits': 'Mode-specific controls and captured state, not generated controls or every registry alternative.',
        'status': 'workspace_mode_witnesses_linked'}
    for row in rows:
        for field in row['input_paths']:
            family = field['control_family']
            assert family in grouped, (row['command'], field['path'], family)
            grouped[family]['paths'].append({'command': row['command'], 'path': field['path']})
    return list(grouped.values())
