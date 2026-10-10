# Customer payment workflows and control coverage

Payments record new cash against invoices and retain unapplied cash as customer
credit. Receive Payments is separate from a paid sales receipt. A parent-customer
remittance may cover descendant jobs: its selected amounts establish each job's AR
source ownership once. Subsequent application can use that source only for the
same party, AR account and currency. Remaining new cash belongs to the payer.

## Human and agent cooperation

Open **Receive customer payment** from a customer or invoice, or **Customer payments**
from the company navigation. Choose customer/job, received date, method and deposit
destination. With the Undeposited Funds preference enabled, the ordinary destination
chooser is hidden and the untouched request uses the company default. Preview shows
Undeposited Funds. **Choose another destination** opens an explicit override;
**Use company default destination** returns to omission. The form shows authoritative selected-party and family net AR,
separately from the selected invoice totals. Partial payments leave invoice due;
unapplied cash remains available credit and is not additional income.

A shared payment selection retains the received amount's origin and every row's
entered/calculated/unresolved origin. An agent-created selection opens through
**Saved selections**; **Reload shared selection** picks up another interface's
changes. Entered cash is never overwritten by invoice selection. With $150 entered
and two $100 invoices, calculated rows are $100/$50. Removing the first recalculates
the other to $100 and leaves $50 unapplied. If the other row was explicitly fixed
at $50, it stays $50 and leaves $100 unapplied.

**Calculate selected amounts** uses the shared calculation command. **Auto Apply**
uses its matching/oldest suggestions. Both prepare a shared draft. **Clear draft
selections** removes proposed choices without unapplying recorded payments.
Editable fields pause while an action is running so delayed reads cannot erase new
input. Action buttons serialize their requests; resume editing after completion.
**Preview payment** loads every proposed effect page before enabling Save.
**Save & Close** displays the saved receipt; **Save & New** starts a fresh payment
only after confirmed success, retaining customer/date/method/destination.

Open a saved payment to **Apply available credit**, **Correct receipt**, **Unapply
recorded applications**, or **Void unapplied receipt**. Unapply reverses whole
recorded applications at their original dates; it does not post cash again. Void
requires applications to be unapplied first, and refuses with `E_HAS_REFUND` while a
refund still stands on the receipt's unapplied cash.

**Sending an overpayment back.** A cheque larger than the invoices it settles leaves
cash standing on the receipt. The **Customer payments** list shows that figure and the
row carries **Refund**, which opens `customer-refund post` with that receipt and that
exact amount already named. The refund debits Accounts Receivable and credits the
funding account and posts nothing else, and it consumes the overage: the receipt stops
offering that money to any later invoice, and a second refund of it is refused with
`E_CREDIT_UNAVAILABLE`. Correcting or voiding the refund releases the overage exactly,
so it is available again. Writing a cheque against Accounts Receivable instead posts
the same two legs and consumes nothing, so the overage would keep reporting as
available for ever; that is not a refund. Receipt correction preserves job
ownership and changes only the payer's residual capacity when its amount changes.
Applied invoices remain editable subject to current versions, due capacity and
complete settlement restatement. The ordinary invoice editor previews these
settlement effects before saving.

After a stale error, **Review current amounts and versions** preserves entered
commercial values while explicitly reviewing the current dependency baseline.
Repreview before saving. After an unconfirmed response, recover the exact submitted
request. Do not invent a replacement key. **History → Original operation** recovers
the original request and previews its recorded effect without a new financial write.
The receipt displays captured facts separately from current settlement and offers
internal printing and ordinary authorized notes/attachments.

## Captured list labels

`payment query` returns required `payer_label` and `method_label` strings from each
selected receipt's current immutable profile. Renaming a customer or method does
not change an existing receipt's captured labels. Receipt corrections select the
corrected revision's profile. The browser consumes these shared fields directly.

The query authorizes the complete selected payment batch before validating each
selected full profile in the same read snapshot. Historical filtering and
publication checks still apply, including access checks for empty pages. An
invalid selected profile fails the entire query with `E_PAYMENT_PROFILE_INVALID`
(HTTP 500); details contain only authorized payment/revision IDs and the field
name. Valid empty captured labels remain empty. Other payment commands retain
their existing validation behavior.

## Shared command contract

The executable schemas and field names are in the generated [payment](cli/payment.md),
[selection](cli/payment-selection.md), [preview](cli/payment-preview.md) and
[operation](cli/payment-operation.md) references. CLI and authenticated HTTP execute
these same registry commands. HTTP translates spaces to dots, for example
`POST /companies/{company_id}/commands/payment.selection.show` with
`{"selection":"<shared selection ID>"}`. Writes use the ordinary authenticated
execution context and reason rules. Permanent exact recovery is the bounded
read-only exception; it does not grant authority to a new or mismatched request.

MCP publication and the actual unbriefed payment exercise remain a separate Row22
acceptance gate dependent on Row9 integration. This document does not invent an
MCP endpoint or claim that a registry-only test establishes that gate.

## Reference control traceability

CP numbers identify the authorized receive-payments inventory. This is coverage
and follow-up ownership, not a whole-form or comprehensive-goal completion claim.
Required follow-ups remain required under the active project goal.

| Controls | Row22 behavior or required owning follow-up |
|---|---|
| CP01 | Selected customer/job and descendants for new cash; immutable exact-party source ownership thereafter. |
| CP02 | Authoritative payer and family net AR visibly labelled separately from selected totals. |
| CP03 | Statement charges and statements require their own document/report increment. |
| CP04 | Explicit cash and selection-derived totals are two supported paths; shared origins survive handoff. |
| CP05 | Received date and explicit application dates; corrected-history cutoff reads remain distinct from current capacity. |
| CP06 | Captured payment method and reference, customer default. Card data/provider execution belongs to the provider increment. |
| CP07 | UF-default mode hides the destination chooser and shows resolved UF in preview; Choose another destination deliberately overrides it. Preference-off mode requires the bank/UF chooser. |
| CP08 | Memo and internal receipt printing now; no delivery/provider confirmation claim. |
| CP09 | Paged invoice date/job/number/original/applied/due/payment grid and complete preview. Discount/credit-memo/aging columns depend on their owning increments. |
| CP10 | Independent automatic-application preference plus explicit matching/oldest suggestions; draft preparation precedes preview/save. |
| CP11 | Explicit partial per-invoice amounts; unapplied remainder is permitted. |
| CP12 | Remaining invoice due stays open until paid or written off: a bad debt is written off with a receipt of 0.00 (see Bad-debt write-offs). |
| CP13 | Unapplied cash remains payer-owned credit, and `customer-refund post` sends it back: name the receipt as a `payment` source and the refund debits Accounts Receivable, credits the funding account, and consumes that overage so it stops reporting as available. |
| CP14 | Explicit immutable unapply and same-party reapply; no mutation of old applications. |
| CP15–CP16 | Available payment credit can partially settle exact-party invoices. Unified credit-source selection and credit memos remain required follow-ups. |
| CP17 | Credit memo document, corrections and application are a required sales-credit increment. |
| CP18 | Refund/check/provider execution is a required dependent increment. |
| CP19 | Existing cross-job credit transfer requires its own explicit balanced operation; new family receipt never implicitly transfers old credit. |
| CP20 | Existing captured terms facts remain readable on invoices; no fabricated discount settlement. |
| CP21 | Typed discount account/class, eligibility and settlement are required follow-up work. |
| CP22 | Explicit unapply/void for wrong-payer correction; banking/deposit correction is a dependency. D104 Delete ships as `payment delete`: an explicit family grant separate from `ledger.post`, immutable history and number retention, and balanced cancellation of the cash and receivable postings at their original dates. |
| CP23 | Applied-invoice correction with complete deterministic restatement and dependency diagnostics; explicit unapply before void. |
| CP24 | Payment list filters method/date/status/text/available credit before pagination; original/current history and lookup. |
| CP25 | Receipt debits bank/UF and credits AR; later deposit movement must not recognize cash twice. |
| CP26 | Payments-to-deposit, deposit documents, fees and reconciliation require the banking increment. |
| CP27 | Liability deposits/retainers require typed liability documents; unapplied AR credit does not substitute. |
| CP28 | Bad debt is written off by `payment receive` with `amount` 0.00 and the balance taken as a discount to an expense account such as Bad Debt, the anchor's way; a customer check the bank returns is recorded by `payment bounce`. |
| CP29 | Finance-charge assessment/preferences and statements remain required follow-ups. |
| CP30 | Provider/card/online collection and external delivery remain separate authorized work. |
| CP31 | Full aging summary/detail and graph remain required report work; dated settlement is not an aging report. |
| CP32 | Open invoice discovery and posting-derived balances support payment entry. Collections, average days and complete report family remain required follow-ups. |
| CP33 | Customer/invoice receive-payment entry points and received-payment lookup now; full tracker/dashboard/report parity remains required follow-up work. |

## Active examples and independent totals

The main/reference seeds preserve their exact 297/201 command prefixes and append
33 commands each. Payment Example Customer and Jobs A/B have invoice dues of
$10/$20/$0; P1 retains $10 payer credit and P2 retains $10 Job B credit. The family
net AR is $10. P1's original $125 receipt is corrected to $130 with retained
ownership; Job A is explicitly unapplied and reapplied. A separate cancelled
invoice/payment branch ends voided with zero net effect.

Each company's new examples add bank $170, AR $10 and income $180. The independent
minor-unit and row-count oracles in repository file
`src/bookflow/demo/payment-expected.json` retain
gross originals, reversals and replacements as well as final balances. Existing
financial records remain unchanged. The reference annual trial balance is
8,048,600 cents; income is 6,457,000 and net assets 7,457,000. These are active
examples for continuation, not a reason to cancel every new record.

## Early-payment discounts

Terms with a discount (for example "2% 10 Net 30") make an invoice or bill discountable
through its discount date. `payment invoices` rows carry `discount_date` and
`suggested_discount_minor_units` for the context date; `bill query` rows carry
`discount_date` and `early_discount_minor_units` (what the terms still offer, date-free).

- The suggested discount is the terms percentage of the document total, sales tax
  included, rounded half-up to the cent, less any discount already taken on that
  document, and at most what is still open. It is zero after the discount date.
- A discount is taken only when named: `payment receive` `discounts` (one row per
  invoice) or a `bill pay` row's `discount`.
- A document may take its discount with no cash beside it: a `discounts` row for an
  invoice the applications do not pay (it then needs the invoice's `expected_version`),
  or a `bill pay` row with `"amount": "0.00"` and a `discount`. A receipt's `amount` is
  positive unless the whole receipt is a bad-debt write-off (see below), and each
  vendor's bill payment pays that vendor some money. The edge is then all discount.
  A discount named after the discount date, or on a document whose terms offer none,
  is taken and the write returns a warning.
- The document is settled by the cash plus the discount. A customer discount posts
  debit discount account, credit Accounts Receivable, on the receipt; a vendor discount
  posts debit Accounts Payable, credit discount account, on the bill payment. The receipt
  or check total stays the cash.
- The discount account is `discount_account` on the command, else the company
  `customer_discount_account_id` / `vendor_discount_account_id`, else the active account
  named "Discounts Given" / "Discounts Taken", else that account is created as an income
  account in the same write.
- `payment unapply` / `bill payment unapply` of a discounted settlement leaves the
  discount with the payment as unapplied credit (customer) or unapplied debit (vendor).
  A void or deletion reverses the discount posting with the rest of the payment. A
  receipt correction restates the discount unchanged.
- Cash basis treats the discount as payment: the document's income or expense is
  recognized in full at the payment date, and the discount account carries the discount.
- Settlement reads name the discount: `invoice settlement` applications carry
  `discount_minor_units`; bill settlement `sources` list `early_discount` beside
  `bill_payment` and `vendor_credit`.

## Bad-debt write-offs

A debt that will never be paid is written off the way the anchor writes it off: a receipt for
0.00 whose open balance is taken as a discount to a Bad Debt account. `payment receive` with
`amount` `"0.00"`, each invoice written off named in `discounts` (invoice, amount,
`expected_version`), `discount_account` an expense account (type expense or other expense;
the company's "Discounts Given" is an income account and is refused), and a reason.

- No cash: the receipt posts debit Bad Debt, credit Accounts Receivable and nothing to the
  bank or Undeposited Funds; Make Deposits never offers it. It carries no payment method of
  its own (the list's "Other" is recorded) and `applications` must be empty.
- The invoices are settled through the same edge any discount uses, so open invoices, aging,
  statements and customer balances all read them as paid.
- Sales tax is left as it stood. The anchor's discount path does not reduce the sales tax
  owed, and neither does this: the whole balance, tax included, goes to the expense account,
  and the Sales Tax Payable liability is untouched. A business that may reclaim tax on a bad
  debt does it with `sales-tax adjust`.
- A customer who has been made inactive can be written off without being reactivated; the
  customer stays inactive.
- A partial write-off is a receipt with some cash and a discount to an expense account, or a
  0.00 receipt for part of an invoice; the rest stays due.
- To reverse a write-off, `payment unapply` it and `payment void` it, as any receipt. It is
  not corrected in place.
- A write-off an agent made is listed on `report entries-to-review` as `write_off`, and the
  agent's result warns that it will be.

## A customer's check comes back

`payment bounce` is the anchor's Record Bounced Check. Name the deposited receipt, the date
the bank returned it, the bank's fee (`bank_fee`: amount and an expense account such as Bank
Service Charges) and, if the business charges for it, a fee for the customer (`customer_fee`:
amount and an Other Charge `item`, or the income `account` that an item posts to, such as
Returned Check Charges). A reason is required.

- The invoices the receipt paid are owed again, with their balances.
- The bank account the deposit went into shows two lines on the return date: the returned
  amount (a customer refund, "Returned check ...") and the bank's fee (a register entry), so a
  statement import finds both. The original deposit and the receipt are not touched.
- The customer fee is a new open invoice for the customer. Its number is `customer_fee.number`
  or the next one.
- `payment show` carries `bounce` ("bounced on ...") and `payment query` rows carry
  `bounced_on` while the return stands.
- A receipt that took an early-payment discount returns only its cash; the discount stays as
  the customer's credit.
- Everything is written together or nothing is. There is no un-bounce: void the returned-check
  refund (`customer-refund void`) and the receipt is an ordinary unapplied receipt again; void
  the fee entry and the fee invoice as any entry and invoice are. That refund is not corrected
  in place, and a deposit holding a bounced receipt is not deleted until the refund is voided.
- Not recordable: a receipt still in Undeposited Funds (unapply and void it), one applied
  inside a closed period (reopening an invoice there would change a closed period), one
  already partly refunded, and one that paid more than one customer or job.

## Recover an interrupted or stale shared selection

Recovery keeps the original selection identity. Open **Shared payment selections → Pending recovery** to continue an uploading or ready-for-review attempt. The browser saves the complete attempted edits locally before sharing them. Until the server has received the whole attempt and you confirm its complete comparison, every interface blocks ordinary editing and recording of that selection. Closing a tab does not release this block.

**Resume complete saved attempt** uploads the remaining edits from the original browser. A different browser can page through **Received edits**, expand each attempted amount/action, inspect original **Chunk acknowledgements**, and read the exact **Missing ranges**. It cannot reconstruct unsent edits; missing ranges explicitly identify values the server has not received. If the original browser data is unavailable, **Discard entire attempt** explicitly abandons all edits, including missing ones, while retaining the evidence. **Replace with my complete attempted edits** supersedes the whole prior attempt atomically. No recovery command records cash.

Read the full comparison before **Confirm complete recovery**. Entered amounts remain exact. A header calculated from selected invoices follows the final rows; an unresolved row keeps that derived header unresolved. New calculation requests show their independently calculated proposal; a previous local display is only a comparison hint. After confirmation, preview and record the ordinary payment. Save & New starts a blank new remittance only after recording is confirmed. A consumed selection links to its original payment operation and cannot be recovered into another cash receipt.

Agents use the same eleven `payment recovery` commands. `show` accepts exactly one recovery ID or original recovery key, resolving a lost begin acknowledgement. `query` discovers attempts and `items` pages received entries, immutable chunk acknowledgements or missing ranges. `begin`, `upload`, `seal`, `compare`, `compare-items`, `apply`, `abort` and `replace` follow the same lifecycle. Every exact write retry requires current authority and the original normalized reason identity, returns its immutable original receipt plus separately refreshed `current`, and creates no new audit or generic retry row. A first successful unchanged apply or abort does append its own history.

For `begin`, hash UTF-8 canonical JSON with sorted object keys, compact separators, unescaped Unicode and no nonfinite numbers. The object is `{domain:"bookflow.payment.recovery.intent",format:1,selection:S,local_baseline_revision:A,anchor_revision:B,attempt_generation:G,header_intent:H,entries:E}`. A and B are actual immutable revision IDs from shared selection reads; G is a canonical UUID. E is the entire list of final edits sorted by binary invoice ID, preserving explicit null versus absent keys. Money is integer minor units; do not pass it through floating-point JavaScript numbers. Upload deterministic 200-entry chunks (only the last may be shorter), then read the recovery version and seal. Compare and every comparison page bind the generation, complete hash and current facts. Apply needs that exact fingerprint; a stale page or relevant invoice change requires a fresh whole comparison.

The active Demo and Reference companies each include three `DEMO-PAY-REC-` / `REF-PAY-REC-` selections for Payment Example Customer: completed recovery with cash150 cents, selected50 and unapplied100; an interrupted recovery with 0 of 1 edits uploaded; and a complete sealed recovery awaiting review. These examples add no financial effects. The completed example also has an explicitly audited first unchanged recovery publication and immutable retries. Original invoice due remains 1000 cents. The seed-only `recovery_intent` descriptor derives a hash from actual prior captured revision IDs and the complete declared entries; it does not create a public command or invent IDs.
