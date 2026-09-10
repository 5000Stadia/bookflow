# Printed documents

The four documents a customer receives are produced as PDF: **invoice**, **sales
receipt**, **estimate** and **customer statement**. Printing is producing that PDF and
handing it to the browser, which prints it or saves it. There is no separate print-only
HTML page, and no second layout to keep in step.

The default layouts here are the supplied basic templates named by
[print templates and form editor](print-templates.md). Choosing among layouts, editing
one, template groups, paper profiles and batch printing remain future roadmap work.

## Where a document prints from

| Document | Print route | Opened from |
|---|---|---|
| Invoice | `GET /c/{company}/invoice/{id}/print` | the invoice record page |
| Sales receipt | `GET /c/{company}/sales-receipt/{id}/print` | the sales-receipt record page |
| Estimate | `GET /c/{company}/estimate/{id}/print` | the estimate record page |
| Customer statement | `GET /c/{company}/report/statement/print?customer=&date_from=&date_to=` | the statement report, on each customer's opening-balance row |

Each route answers `application/pdf` with `Content-Disposition: inline` and a filename
of `invoice-<number>.pdf`, `sales-receipt-<number>.pdf`, `estimate-<number>.pdf` or
`statement-<customer>-<date_to>.pdf`, and `Cache-Control: no-store`.

Every route reads through the ordinary command path as the acting principal, so a member
who may not read the document may not print it, and no route reaches another company.
A missing document answers the ordinary `E_RECORD_NOT_FOUND` error page, not a PDF. A
statement print without a customer, `date_from` and `date_to` answers `E_VALIDATION`
naming the missing fields; a statement is one customer's account over one period, and
those three values are its identity.

The on-screen affordance is a **Print / save PDF** link on the record page of the first
three documents, and on the opening-balance row of each customer on the statement
report. It opens in a new tab and never appears on a printed page.

## What each document says

Every figure is a string a command already returned. Nothing in the renderer recomputes
an amount, resolves a default or reprices a line.

All four carry the company block and its phone, email and website; the addressed block
for the customer; a boxed bar of header fields; a table of rows; and the totals.

- **Invoice** — date, terms, due date, purchase-order number, rep, ship date and ship
  method; item, description, quantity, rate and amount per line, with a tax column only
  when a line carries tax; subtotal, sales tax, total, payments and credits, and the
  balance due from the invoice's current settlement. A voided invoice prints its
  historical amounts under a `VOIDED` line naming the reason.
- **Sales receipt** — payment method and reference in place of terms and due date, and
  no balance: a receipt is settled by definition.
- **Estimate** — its title, status and expiry; the scope, inclusions, exclusions, timing
  and commercial terms it captured; subtotal, sales tax and total, and no amount owed.
  An expired or closed estimate says so above the lines. Proposals and work orders are
  internal and do not print.
- **Customer statement** — the statement date and period; balance forward, then every
  entry in date order with its date, kind, number, due date, memo, amount and running
  balance, then the balance due; the aging box of Current, 1-30, 31-60, 61-90, Over 90
  and Total. Rows follow the report's own cursor to the end of the account, so a long
  period prints complete rather than stopping at one page of rows.

### Captured facts and current facts

The company name and address on an invoice, sales receipt or estimate come from that
revision's own issuer snapshot, so a reprint of an old document shows the company as it
was when the document was issued. Phone, email and website are not captured on a
revision, so they are the company's current ones. A customer statement is a current
reading of the books and carries the company's current name and address throughout.

### Pagination

Letter, half-inch margins. A line item is never split across a page boundary. A page
carrying line items repeats the column headings. Every page carries the document's own
identity, currency and `Page X of Y`.

## The renderer

One callable produces the bytes, and it takes no request:

```python
from bookflow.documents import render

rendered = render(read, company_id, kind, identity)
rendered.kind        # "invoice" | "sales-receipt" | "estimate" | "statement"
rendered.filename    # "invoice-DOC-100.pdf"
rendered.media_type  # "application/pdf"
rendered.content     # PDF bytes
rendered.title       # "Invoice DOC-100"
```

`read(command_name, input, company_id)` runs a registered command as whoever is asking.
`identity` is `{"document": <id>}` for the first three kinds and
`{"customer": <id>, "date_from": <date>, "date_to": <date>}` for a statement.

A future command that attaches a document to its record, or a mail sender, calls this
and gets the identical bytes; it never lays a document out again.

`documents/model.py` turns command output into a `PrintedDocument` — parties, header
fields, columns, rows, totals, grids, notes — and knows nothing about PDF.
`documents/pdf.py` draws a `PrintedDocument` and is the single source of truth for how a
document looks. `documents/render.py` joins them and names the file.

## Dependency

`reportlab` (BSD) draws the pages. It is pure Python plus prebuilt wheels for its
`pillow` and `charset-normalizer` dependencies, needs no system libraries and no font
files, and is installed by pip like every other runtime dependency, which keeps a LAN
workstation install unchanged in kind. Its flowable tables are what repeat column
headings across pages and keep a line item whole. `pypdf` is a development dependency
only: the tests read the text back out of the produced PDF.

## Not built here

No template selection or editor, no template groups, no paper profiles beyond Letter, no
receipt-width output, no labels or envelopes, no logos or uploaded images, no batch
printing or print queues, no check printing, no purchase-side documents, and no email or
other sending. Nothing on any page offers to send a document, because nothing can.
