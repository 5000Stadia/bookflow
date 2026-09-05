# Invoice and sales receipt controls

This inventory maps the eventual sales forms to the first service-sales slice in
[the draft plan](../specs/15-service-sales.md). **First slice** means specified in
that draft, not implemented or independently verified. **Staged** means outside
that slice; it does not imply a completed design. A row can contain both. The
blueprint's transaction, application, customer, item, pricing, unit, custom-field,
delivery and reporting sections remain the owning contracts.

The first slice sells services, nonstock goods and fixed-amount additional charges
in home currency. Quantities are positive, unit prices nonnegative, and the whole
sale positive. It includes configured invoice-date sales tax, commercial previews,
posting, correcting edits, voids and captured history. Stock costing, aggregate
and payment lines, settlements, conversions, templates and delivery remain staged.
A paid sale records payment received for that sale. Paying an existing invoice
requires a customer payment; another sales receipt would duplicate the sale.

## Customer and header

| Key | Eventual field, option or behavior | First slice and remaining coverage |
|---|---|---|
| H1 | Customer or job selection; distinguish jobs from customers; begin a sale from the customer workspace | First slice: customer/job selector and inherited business defaults. Staged: complete contextual workspace and creating or editing a customer directly from the sales form. |
| H2 | Customer identity, billing address and contact facts | First slice: captured customer/job identity, business/person names, billing address, email/phone and issuer identity/address. Saved commercial facts survive master changes. |
| H3 | Shipping destination; choose among saved locations or enter a transaction-specific address | First slice: effective default shipping address, active saved-address selection and typed override. Address ownership and inheritance follow the customer contract. |
| H4 | Defaults and overrides for addresses, terms, representative, shipping, payment method, price level, tax and class | First slice: field origins distinguish defaults, explicit overrides and explicit clearing; return-to-default and refresh actions are visible in preview. Customer/item changes recalculate dependent defaulted facts. Staged: an explicit offer to update the customer/job master from an edited form. Ordinary form overrides do not change masters. |
| H5 | Invoice or receipt date; current-date and remembered-date entry preferences | First slice: entered date and period validation. Staged: current-date/last-used-date preference and its entry shortcuts. |
| H6 | Invoice number or sale number; automatic next number and manual entry | First slice: independent per-form sequences, manual numbers, duplicate checks and retention of voided numbers. Staged: keyboard number stepping and separate numbering by receivable account. The draft does not promise that entering a manual number resets the next automatic number. |
| H7 | Invoice receivable account | First slice: select an active receivable account; omission is allowed only when exactly one is eligible. Staged: specialized contribution/pledge account presentations and associated numbering conventions. |
| H8 | Customer purchase order reference | First slice: captured reference. Staged: placement choices in templates and automatic inclusion in delivery subjects. |
| H9 | Payment terms, due date, offered discount percentage and deadline | First slice: standard day-based and monthly-date rules, customer defaults, overrides, month-end handling, and captured discount availability. The discount is information until a settlement applies it. Sales receipts reject invoice terms and due dates. |
| H10 | Sales representative; header and line classes; missing-class reminder | First slice: captured representative, class defaults/overrides and feature-aware missing-class warnings. Staged: sales and commission reporting beyond captured attribution. |
| H11 | Shipment date, shipping method and location where shipment responsibility transfers | First slice: date and method. Staged: shipment-responsibility location, its company default, and automatic current ship-date behavior. |
| H12 | Customer account panel: net balance, open obligations, credit limit, recent transactions, notes, contact edit and drill-down | First slice: exact-party net receivable balance, separately labelled family balance, sales links, account detail and advisory invoice credit-limit/exposure warnings as specified in the draft. Staged: invoice-aging/open-obligation measures, broader in-form credit-limit display, recent activity panel and collapsible layout. A net account balance is not an aging total. The blueprint makes credit limits advisory. |
| H13 | Available estimates, orders, unbilled time and costs offered on customer selection | Staged: source-document and billable-work selection, including declining to add available work to this sale. |
| H14 | Custom header fields and customer/item facts carried to forms | First slice: typed sales-document custom values, defaults, required/clear behavior, historical labels and definition identities. Staged: arbitrary customer/item custom-field projection into printed headers or line columns; a document custom-field input alone does not supply those layouts. |

## Lines, prices, quantities and tax

| Key | Eventual field, option or behavior | First slice and remaining coverage |
|---|---|---|
| L1 | Ordered item selection; item name/code, description, quantity, unit, rate, amount, class and taxable status | First slice: structured rows for the three supported sale item types; item-derived description, accounts and prices with permitted overrides; calculated net/tax/gross. Unsupported item types receive field errors. |
| L2 | Flat-fee entry with omitted quantity/rate and directly entered line amount | First slice: a fixed charge can be expressed as quantity one and explicit unit price. Staged: independent amount entry, blank-quantity semantics and amount-only layouts. Fractional service quantity alone is not linked progress billing. |
| L3 | Quantity expressed in product or time units; selected sales unit; conversion to base quantity | First slice: quantity to six decimal places, captured conversion and selected-unit price, rounded base quantity, disabled/base-only/related-unit modes, and preserved historical factors. Overflow or a positive base quantity rounding to zero is rejected; a derived nonzero price rounding to zero warns. Staged: purchasing/stock/shipping unit workflows and printed-unit layout. No time-entry conversion is implied. |
| L4 | Catalog price, customer price level, individual line price level and manual rate | First slice: visible price origin, header default and per-line selection/clear, explicit price precedence, fixed-percent and per-item levels, explicit custom-price basis, and configured rounding. Disabled pricing does not erase assignments or silently apply them. |
| L5 | Conditional pricing: quantity breaks, date-limited promotions, customer/item/vendor/class/custom-field conditions, categories and locations; exclusive or combined rules; transaction overrides and pricing guardrails | Staged: advanced pricing and explicit precedence/stacking decisions. Basic price levels do not establish these rules. |
| L6 | Insert/remove rows, change order, copy/paste or duplicate a line, extend the visible table and continue onto more pages | First slice: ordered bounded line collections and correcting replacement with stable retained line identities. Staged: dedicated insert/copy/paste/reorder gestures, equivalent keyboard shortcuts, automatic blank-row growth and multipage printed output. Removing a line in a correction preserves its prior revision. |
| L7 | Stock and assembly sale quantities, availability warnings, on-hand/committed/on-order amounts and fulfillment dates | Staged: inventory movements, cost of sales, availability, partial shipments and backorders. Catalog definitions and nonstock sales provide none of these measures. |
| L8 | Subtotal lines covering the preceding block; visible discounts by amount or percentage; additional percentage charges | Staged: line-order-dependent calculations. A percentage applies to the preceding eligible line or subtotal; later excluded lines remain outside that base. First slice's document subtotal is not a subtotal item. Tax treatment and account attribution need the owning calculation design. |
| L9 | Group item insertion with member quantities/descriptions/prices; show or hide component detail; fixed-price package presentation | Staged: expansion and editable members, group quantity/price behavior and customer-facing detail suppression. A group heading must not duplicate member revenue or quantities. |
| L10 | Line taxable status, customer exemption and chosen tax item/group | First slice: item/customer defaults, line override, customer exemption precedence, tax feature gate and captured tax components with rates, agencies and liability accounts. Missing required mappings are errors. |
| L11 | Tax rate and tax amount display; tax groups; rounding and taxable base | First slice: captured component bases and separately rounded line components, plus document totals. Aggregate taxable-base rounding is a distinct eventual calculation convention; equivalence across rounding boundaries is not established. Tax item definitions alone do not prove calculation parity. |
| L12 | Tax liability timing, exempt-sale classifications, remittance and jurisdiction-specific treatment | First slice: invoice-date liability policy; taxable invoices and receipts under payment-receipt policy are rejected. Staged: settlement-date liability, discount/return tax adjustments, remittance/adjustment forms and jurisdiction-specific tax automation. |
| L13 | Payment lines, negative fee lines, returns, refunds and cash adjustments | Staged: separate line roles and financial effects; the first slice rejects negative quantities/prices and payment items. Fixed additional charges cannot stand in for expense-mapped merchant fee deductions. |

## Footer and form actions

| Key | Eventual field, option or behavior | First slice and remaining coverage |
|---|---|---|
| F1 | Customer message chosen from reusable messages or entered for this sale; add a reusable message while entering | First slice: free text or selected message master, captured text and mutually exclusive inputs. Staged: create-message action inside the form. |
| F2 | Internal memo separate from customer-facing message | First slice: captured memo. Staged: print/statement/report visibility rules; an internal memo must not accidentally become customer-facing through a template or statement. |
| F3 | Subtotal, tax selection/rate/amount, total, payments and credits applied, remaining amount due | First slice: commercial subtotal/tax/gross and component breakdown. Staged: applied-payment/credit and remaining-open-amount calculations and footer controls. Invoice total and customer net balance must not be labelled as settled open balance. |
| F4 | Save, save then close, save then begin another, new form, clear/revert unsaved entry | First slice: entry, preview and post/update through shared commands; browser save checks the preview's facts fingerprint and requires a fresh preview after field changes. Staged: each named convenience action's exact browser behavior and unsaved-change handling. A posted correction is different from reverting unsaved typing. |
| F5 | Locate previous/next transaction, find by details, open a selected document or historical revision | First slice: type-specific show/query/history with date, customer, number and status filters and bounded paging. Staged: previous/next form navigation, broader text/amount search and configurable table sorting. |
| F6 | Copy a sale; memorize a reusable transaction; mark a sale pending and later finalize it | Staged: copying, reusable templates and pending/non-posting sale lifecycle. The first slice's posted/voided states do not cover pending sales. Copying must create a new identity; it must not copy settlement or delivery success. |
| F7 | Attach files; inspect notes/activity and transaction reports | First slice: notes/files/activity on the stable sale and inspectable accounting effects. Staged: form-specific report shortcuts and complete sales reports. |
| F8 | Proofread descriptions and messages | Staged: spelling control and spelling preferences. |
| F9 | Correct or void a saved sale; retain creator/editor identities, timestamps and related records | First slice: immutable revisions and exact reversal/replacement effects, audit provenance, closed-period protection, version checks, preview freshness and repeat-safe saves/voids. Staged: payment, deposit, conversion and reconciliation dependency handling before those workflows ship. |

## Templates, printing and delivery

| Key | Eventual field, option or behavior | First slice and remaining coverage |
|---|---|---|
| D1 | Choose product, service, professional, fixed-fee, time/expense or progress layouts; print a packing slip from the sale | Staged: reusable form templates. Variants differ in shipping/purchase-order fields, column order, amount-only versus quantity/rate entry, hours/rate presentation, and estimated/prior/current billing columns. |
| D2 | Remember last template; change layout without losing entered facts; preview print-only formatting | Staged: template selection/default scope, preserving hidden values, preview of logo/fonts and separate screen/print field visibility. A sales preview in the first slice is a commercial preview, not a print-layout engine. |
| D3 | Customize headings, fields, columns, logo, fonts and layout; reusable receipt formats and defaults | Staged: template editor, customer-specific output choices and saved formats. Payment-receipt output for invoice settlement is distinct from a paid-sale receipt. |
| D4 | Print immediately; queue printing; email immediately; queue email; select both queues | Staged: independent queue flags, customer delivery preference, batch selection, print settings, preview and delivery outcomes. Printing an edited sale must use an identified saved revision and must not silently bypass posting validation. |
| D5 | Delivery recipients from saved contacts; subject/body; customer purchase reference in subject; multiple invoices in one message | Staged: explicit recipient and artifact selection, bundled delivery and per-document/per-recipient outcomes. Capturing an email address does not send a document. |
| D6 | Invoice timeline for creation, sending, viewing, payment and deposit | First slice: creation/correction/void history. Staged: delivery and settlement events, viewing evidence and drill-down. A queued or handed-off message is not sent; recording payment does not establish deposit or recipient viewing. |
| D7 | Online payment link and processing options; receipt delivery and payment-provider confirmation | Staged: explicit provider actions and protected connections. The first slice records supplied payment facts without charging a card, moving money or sending email. |
| D8 | Shipment labels, pickup scheduling, tracking and copied destination details | Staged: fulfillment and carrier integration. Shipping date/method/address fields alone do not dispatch goods. |

## Billable work, payments and adjacent controls

| Key | Eventual field, option or behavior | First slice and remaining coverage |
|---|---|---|
| W1 | Add unbilled time and reimbursable costs to invoices or paid sales; include or exclude available work; select a billing period | Staged: source selection and dated allocation from time, expenses, items and mileage, with billed/unbilled state. Employee and subcontractor time/rates, markup and grouping options need detailed form inventory. Prevent duplicate billing and retain source revisions/lines. |
| W2 | Proposal/scope, estimate, order and work-order conversion; full, partial/progress or selected-line billing; remaining work | Staged under [customer work and billing](../customer-work-and-billing.md): linked source facts and previously billed amounts, estimates versus actuals, change orders and remaining quantities. Completing work does not itself prove payment. |
| W3 | Invoice selected portions of multiple orders; backorders, consignment and fulfillment-to-invoice prompts | Staged: order allocation and stock/cost dependencies. General service entry cannot substitute for this lifecycle. |
| W4 | Batch invoices for selected customers or saved billing groups | Staged: customer search/multiselection, add/remove members, create/save/rename/delete groups, one-off group changes, shared date/template/items/message, customer-specific terms/tax/delivery, review/back, creation summary and print/email/unmarked counts. Dynamic customer groups and billable-work batches are additional variants. |
| W5 | Individual paid sales or daily summarized sales; generic customer; receipt date/number and sold-to information | First slice: individual paid service/nonstock sales with a selected customer/job. Staged: daily-summary workflow, reusable daily layouts, control totals and reconciliation. A manually entered aggregate does not establish those controls. |
| W6 | Receipt payment method, check or other reference, and deposit destination | First slice: resolved active payment method, optional reference and explicit bank or undeposited-funds destination. Staged: method-specific entry labels/icons and the preference that hides destination and automatically selects undeposited funds. No payment method is inferred merely from using a receipt. |
| W7 | Multiple tenders on a daily sale; cash remainder; merchant settlement account and fee deductions | Staged: payment allocations by method, separate deposits by method, gross/fee/net reconciliation and provider-account transfers. One receipt payment-method field is not split tender. |
| W8 | Cash overage or shortage adjustments with nontaxable items and dedicated account | Staged: positive/negative cash differences and daily reconciliation, including a negative adjustment receipt. The first slice's positive-total rule excludes the complete workflow. |
| W9 | Customer payment for one or many invoices/jobs: amount, received date, method, reference, destination, customer balance and invoice lookup | Staged: dedicated settlement form. Invoice grid includes selection, date, job, number, original amount, amount due, applied payment, discounts, credits and totals. Payment credentials remain in a future protected provider workflow. |
| W10 | Full/partial payments; automatic total calculation; automatic or manual allocation; unapply/reapply | Staged: independently controlled automatic amount and allocation preferences, matching amounts/oldest-invoice suggestions, editable per-invoice amounts and over/underpayment displays. Allocations require explicit application history, eligible party/account and amount limits. |
| W11 | Available and previously applied credits; credit selection, amount used and remaining balance; cross-job credit transfer | Staged: credit creation/application, retaining unused remainder, allocation correction and explicit job-transfer rules. Available credit and money actually received are separate amounts. |
| W12 | Early-payment discount: invoice amount, discount date, suggested and chosen discount, discount account/class, resulting balance | First slice: offered term facts only. Staged: eligibility at receipt date, partial-payment discount, discretionary override, paid-invoice credit and accounting application. Promotional line discounts are separate from settlement discounts. |
| W13 | Overpayment retained as credit or refunded; underpayment/write-off; customer return or credit memo | Staged: refunds, credit memos, write-offs and linked invoice conversion. An internal correction reversal is not a customer credit memo or cash refund. |
| W14 | Advance payment held as liability, receipt proof, application on invoicing, unused balance and refund | Staged: customer deposit lifecycle and partial application. Neither ordinary service revenue nor an unapplied receivable credit automatically represents a liability deposit. |
| W15 | Deposit selected receipts/payments: filter by method, sort by method/date/name, select individual/all payments | Staged: deposit membership and eligibility, avoiding redeposit of the same receipt. Posting to undeposited funds in the first slice does not implement a deposit-selection queue. |
| W16 | Deposit account/date/memo, incoming source and account, check reference, amount, subtotal, cash-back account/amount and fees | Staged: deposit slip, additional noncustomer funds, cash withdrawal and merchant fee reconciliation. Moving previously recorded receipts to bank must not recognize revenue again. |
| W17 | Correct a payment allocated to the wrong invoice or wrong customer, including deposited or reconciled payments; returned checks | Staged: safe unapply/reapply and party/deposit corrections, reconciliation safeguards and returned-payment handling. Posted records are never deleted to perform a correction. |
| W18 | Finance charges: rate/minimum/grace/timing preferences; assessment date; customer selection; unapplied-credit warning; editable charge and collection history; print selection | Staged: administrator-configured policy, recalculation through the assessment date, select/unselect all or individual customers, generated charge invoices and statements. Exact preference and calculation rules still require their owning inventory. |
| W19 | Receivables workspace: estimates, open orders, unbilled work, open/overdue invoices and recent payments | First slice: bounded sale queries and separately labelled net balances. Staged: dashboard counts/totals, optional panels, customer/type/status/date filters, column sorting, selected-row edit/payment/conversion, and selected/all print/email actions. Potential work, earned unbilled work, receivables and paid sales remain distinct. |
| W20 | Collections and customer reports: aging, open items, balances, transaction detail, overdue contacts, average payment time and unbilled costs | Staged: dated application-backed measures, aging bands, customer/job totals, phone/due-date/days-overdue detail, transaction drill-down, reminder selection and statement output. An account statement is distinct from a statement of work. |
| W21 | Sales reports by customer/job, ship-to, item, representative and pending status; quantities, amounts and sales shares; payment-method trends | Staged: operational reports, charts and open-order/progress/job-profitability variants. Sales quantities come from commercial lines, not repeated accounting legs; gross margin also needs cost attribution. |

## Intentional differences and remaining evidence

Posted documents and audit history are never deleted. Correcting edits append
history; a void preserves original commercial amounts and records exact reversing
effects. Closed periods have no form-level bypass. Form overrides do not silently
edit list records. Payment fields do not accept raw credentials. Provider, print
and delivery outcomes require their own evidence. These are project constraints,
not missing destructive or implicit actions to reproduce.

This is detailed coverage of ordinary invoice entry and the receipt, payment,
collection and deposit workflows, with selected pictured controls inspected.
It is not exhaustive acceptance of every template or edition. Billable-cost tab
columns and markup/grouping controls, estimate/order/progress forms, credit/refund
forms, print settings and template-editor tabs, recurrence dialogs, detailed
finance-charge preferences, advanced pricing and specialized reports still need
their owning field-level inventories. Online-only supplementary material was not
available. Pictured pending/copy/returned-payment controls establish their presence,
not all their state transitions. Tax rounding, early-discount cutoff boundaries,
and account-specific numbering must not be described as equivalent without further
evidence. The first slice's exact rules remain in its plan.
