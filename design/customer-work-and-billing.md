# Customer work and billing

This workflow extends blueprint sections 10.3, 13.1 and 13.4. Proposals, estimates
and work orders have shared commands and browser forms. [Linked work billing](work-billing.md)
connects source lines to invoices and receipts. The [progress allocation contract](specs/18-progress-billing.md)
supports quantity, net amount, original-scope percentage and exact rebilling.
Settlement and delivery remain subsequent increments.

The later [print-template system](print-templates.md) supplies editable output
layouts, dynamic custom proposals/forms and ordered groups that can combine these
documents with reports, customer letters, envelopes or labels. It remains future
roadmap work, separate from the active accounting implementation sequence.

## Customer workspace

The browser provides a connected section for proposals, estimates, work orders
and billing. A contractor can start with an introductory proposal or statement
of work, describing the customer, job/site, scope, inclusions, exclusions, timing
and commercial terms. A statement of work is distinct from a customer account
statement, which lists balances and activity.

The normal path is proposal/statement of work → estimate → work order → invoice
or sales receipt. A user may begin at the document appropriate to the job; the
interface does not require every preceding document. Customer/job identity,
scope, item lines, quantities, prices and relevant notes or attachments carry
forward without retyping. The conversion preview identifies the source revision
and the facts being carried into the new document.

Completing a work order offers an invoice when payment is owed and a sales receipt
when payment has been received. A paid sale records the payment method and
deposit destination. Completion alone does not claim payment or create ledger
entries. Estimates and work orders remain non-posting operational documents.
Only the chosen accounting transaction posts the financial effects.

If work has already been invoiced, receiving its payment settles that invoice
through a customer payment. It does not create a second sale or replace the
invoice with a sales receipt. For partially billed work, completion bills only
the unbilled remainder; receipts and payments remain linked to their actual
obligations. The workflow shows existing billing before offering its next action.

Conversion creates a linked destination document and retains the source and its
history. Source and destination show their relationship and status. Retrying a
conversion cannot duplicate billing. Partial/progress billing retains the billed
and remaining quantities or amounts against source lines under the allocation
contract. A source correction does not
silently rewrite an issued invoice or its accounting history.

## Agent and command vocabulary

The agent speaks in the same business nouns and actions that the command surface
exposes. The following language is an acceptance target for the later workflow,
not a list of currently registered commands:

| User request | Explicit operation and visible result |
|---|---|
| Make an estimate for this job | Create an estimate for the named customer/job and return its document number and total. |
| Turn this estimate into a work order | Create a work order linked to the selected estimate revision. |
| This work is finished; make an invoice | Complete the work and create the linked invoice, showing the billed lines and amount owed. |
| They paid; make a sales receipt | Create the linked paid-sale document using the supplied payment facts and deposit account. |
| They paid invoice 1043 | Record a customer payment against that invoice and show its remaining balance. |
| Make and send an invoice | Create/post the invoice and request its delivery to the resolved recipient; return both accounting and delivery outcomes. |
| Send invoice 1043 | Deliver the existing invoice without creating another invoice or ledger posting. |
| Write the initial statement of work | Create a proposal/scope document for the customer and job, available for review and later conversion. |

Canonical commands keep the established singular noun plus ordinary verb grammar:
estimate, work-order, invoice, sales-receipt and delivery. Creation, conversion,
posting and sending have explicit documented effects. Compound requests use these
same typed operations; they do not require an unrelated command vocabulary or
adapter-specific accounting behavior. Help and examples include common phrases
so an unfamiliar agent can discover the intended action.

Sending identifies the saved document revision, rendered artifact, channel and
recipient. Preview shows those facts without sending. A failed delivery leaves
the already-created invoice visible and retryable; a retry never posts it again.
Queued or handed-off delivery is not reported as sent. An authorized request or
standing directive can cover creating and sending together, without a redundant
confirmation at each internal step. A request only to create a document does not
implicitly authorize sending it.

## Acceptance scenes

A contractor creates a scope letter, estimates the work, carries it into a work
order, marks the job complete, and produces either an invoice or a paid sales
receipt from the same customer workspace on desktop and phone. No customer or
line information needs to be re-entered, and every source remains inspectable.

An unfamiliar agent given "make and send an invoice" discovers the documented
operations, previews the selected customer, lines, amount and recipient, and
executes the authorized request. The browser then shows one accounting document
and an accurate delivery status. A simulated delivery failure and retry leave
exactly one invoice and one set of financial effects.
