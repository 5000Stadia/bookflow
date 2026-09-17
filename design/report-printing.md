# Full filtered report printing

After running a report, **Print full filtered report** opens
`/c/{company}/report/{verb}/print-all` with the same typed filters. Pagination
cursor and page size are excluded. **Print / save PDF** opens the browser print
dialog only when clicked. **Back to report filters** retains the query.

The printable view uses the registered report command and its existing browser
presenter. It traverses the same continuations as CSV export. Rows, totals,
columns and metadata come from one successful complete traversal; no financial
values are recomputed for printing. An audited change that invalidates a cursor
restarts the whole traversal, up to three attempts. Exhausted staleness produces
the normal error instead of a mixed report.

The shared traversal allows an initial 200 rows and up to 50 continuation pages:
at most 10,200 rows. If a continuation remains, printing refuses the incomplete
result and asks for narrower dates or filters. It does not print partial rows
beside full-report totals. Unsupported bases, invalid filters and authorization
failures retain the normal command errors. Every read uses the acting principal's
normal command authorization; the response is not cached.

The view identifies the company, report, period, basis, currency, selected filters,
generation time, audit watermark and complete row count. It is a new reading of
the current books, not a saved snapshot of the earlier on-screen page. Existing
report-specific totals and limitations remain visible. Paper repeats table column
headings and omits application navigation, forms, buttons and technical disclosures.

The ordinary paginated page still supports native browser printing as a clearly
labelled displayed-page copy. Use its full-report link to print all matching rows.
Customer-facing document PDFs and individual customer-statement PDFs retain their
existing print routes; no template editor is provided.

Automated browser evidence covers Chrome on desktop and a 390px emulated phone,
including browser-generated multipage PDFs from both viewports. Native mobile
print dialogs, other browser engines and physical printers are not certified by
that evidence.
