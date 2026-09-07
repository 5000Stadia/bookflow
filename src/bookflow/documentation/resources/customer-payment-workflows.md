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

## Recover an interrupted or stale shared selection

Recovery keeps the original selection identity. Open **Shared payment selections → Pending recovery** to continue an uploading or ready-for-review attempt. The browser saves the complete attempted edits locally before sharing them. Until the server has received the whole attempt and you confirm its complete comparison, every interface blocks ordinary editing and recording of that selection. Closing a tab does not release this block.

**Resume complete saved attempt** uploads the remaining edits from the original browser. A different browser can inspect every received edit and the missing count, but cannot reconstruct unsent edits. If the original browser data is unavailable, **Discard entire attempt** explicitly abandons all edits, including missing ones, while retaining the evidence. **Replace with my complete attempted edits** supersedes the whole prior attempt atomically. No recovery command records cash.

Read the full comparison before **Confirm complete recovery**. Entered amounts remain exact. A header calculated from selected invoices follows the final rows; an unresolved row keeps that derived header unresolved. New calculation requests show their independently calculated proposal; a previous local display is only a comparison hint. After confirmation, preview and record the ordinary payment. Save & New starts a blank new remittance only after recording is confirmed. A consumed selection links to its original payment operation and cannot be recovered into another cash receipt.

Agents use the same eleven `payment recovery` commands. `show` accepts exactly one recovery ID or original recovery key, resolving a lost begin acknowledgement. `query` discovers attempts and `items` pages received entries, immutable chunk acknowledgements or missing ranges. `begin`, `upload`, `seal`, `compare`, `compare-items`, `apply`, `abort` and `replace` follow the same lifecycle. Every exact write retry requires current authority and the original normalized reason identity, returns its immutable original receipt plus separately refreshed `current`, and creates no new audit or generic retry row. A first successful unchanged apply or abort does append its own history.

For `begin`, hash UTF-8 canonical JSON with sorted object keys, compact separators, unescaped Unicode and no nonfinite numbers. The object is `{domain:"bookflow.payment.recovery.intent",format:1,selection:S,local_baseline_revision:A,anchor_revision:B,attempt_generation:G,header_intent:H,entries:E}`. A and B are actual immutable revision IDs from shared selection reads; G is a canonical UUID. E is the entire list of final edits sorted by binary invoice ID, preserving explicit null versus absent keys. Money is integer minor units; do not pass it through floating-point JavaScript numbers. Upload deterministic 200-entry chunks (only the last may be shorter), then read the recovery version and seal. Compare and every comparison page bind the generation, complete hash and current facts. Apply needs that exact fingerprint; a stale page or relevant invoice change requires a fresh whole comparison.

The active Demo and Reference companies each include three `DEMO-PAY-REC-` / `REF-PAY-REC-` selections for Payment Example Customer: completed recovery with cash150 cents, selected50 and unapplied100; an interrupted recovery with0 of1 edits uploaded; and a complete sealed recovery awaiting review. These examples add no financial effects. The completed example also has an explicitly audited first unchanged recovery publication and immutable retries. Original invoice due remains1000 cents. The seed-only `recovery_intent` descriptor derives a hash from actual prior captured revision IDs and the complete declared entries; it does not create a public command or invent IDs.
