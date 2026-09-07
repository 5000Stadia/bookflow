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
requires applications to be unapplied first. Receipt correction preserves job
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
| CP12 | Remaining invoice due stays open. Typed write-offs remain required follow-up work. |
| CP13 | Unapplied cash remains payer-owned credit. Refund is a separate required document. |
| CP14 | Explicit immutable unapply and same-party reapply; no mutation of old applications. |
| CP15–CP16 | Available payment credit can partially settle exact-party invoices. Unified credit-source selection and credit memos remain required follow-ups. |
| CP17 | Credit memo document, corrections and application are a required sales-credit increment. |
| CP18 | Refund/check/provider execution is a required dependent increment. |
| CP19 | Existing cross-job credit transfer requires its own explicit balanced operation; new family receipt never implicitly transfers old credit. |
| CP20 | Existing captured terms facts remain readable on invoices; no fabricated discount settlement. |
| CP21 | Typed discount account/class, eligibility and settlement are required follow-up work. |
| CP22 | Explicit unapply/void for wrong-payer correction; banking/deposit correction is a dependency. D104 Delete remains required with permission, immutable audit and balanced cancellation. |
| CP23 | Applied-invoice correction with complete deterministic restatement and dependency diagnostics; explicit unapply before void. |
| CP24 | Payment list filters method/date/status/text/available credit before pagination; original/current history and lookup. |
| CP25 | Receipt debits bank/UF and credits AR; later deposit movement must not recognize cash twice. |
| CP26 | Payments-to-deposit, deposit documents, fees and reconciliation require the banking increment. |
| CP27 | Liability deposits/retainers require typed liability documents; unapplied AR credit does not substitute. |
| CP28 | Bad-debt/write-off documents and components remain required follow-ups. |
| CP29 | Finance-charge assessment/preferences and statements remain required follow-ups. |
| CP30 | Provider/card/online collection and external delivery remain separate authorized work. |
| CP31 | Full aging summary/detail and graph remain required report work; dated settlement is not an aging report. |
| CP32 | Open invoice discovery and posting-derived balances support payment entry. Collections, average days and complete report family remain required follow-ups. |
| CP33 | Customer/invoice receive-payment entry points and received-payment lookup now; full tracker/dashboard/report parity remains required follow-up work. |

## Active examples and independent totals

The main/reference seeds preserve their exact 295/201 command prefixes and append
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
