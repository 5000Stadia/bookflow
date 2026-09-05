# Print templates and form editor — future roadmap

This is a recorded product direction for a later UI/output phase. It is not an
active implementation plan. No editor, renderer, print integration, schema or
dependency is introduced by recording it. Current work remains paused for the
human's roadmap discussion.

## Coverage and editor

The company can maintain multiple printable templates and edit them visually.
Receipts, invoices and reports are the first coverage; work orders, job proposals
and company-defined forms follow. The extended editor is a full form builder for
the company's own business forms, letters and proposals, including entered text
and content beyond the built-in document layouts.

The editor is WYSIWYG at the selected physical paper size. Layout and preview
represent the printed page, including page boundaries and multipage output.
Paper profiles cover standard sizes such as Letter and Ledger, envelopes and
labels. Label support includes sheet layouts with defined label dimensions and
spacing, and a suitable path for dedicated label printers. Exact stock catalogs,
printer integrations and editor technology are chosen in the later owning plan.

Every supported built-in document/report has a supplied basic default template
with the familiar field arrangement of the accounting anchor. Users can choose
another template without first designing one. The layout bar is conventional
bookkeeping output; no anchor-layout parity is claimed by this roadmap entry.

## Dynamic reference elements

Templates combine user-authored content with references resolved from the selected
business context. Reference elements can insert customer details, inventory/item
facts, document or report values, company details such as its phone number, and
other useful supported business fields. The same template can produce different
customers' forms without retyping those facts.

The form builder must support company-defined content, rather than only rearranging
the fixed fields of an invoice. Layout customization does not itself change an
accounting record. Existing rules for internal notes, cost/markup disclosure and
company access still apply to referenced data.

The owning implementation plan will define reference discovery, selection of
related records, required context, and missing-value behavior. It will also define
which values come from a saved document revision and which deliberately use current
master data, so changing the company phone number does not silently reprice an
issued invoice. These are unresolved implementation details, not restrictions to
one document section.

## Ordered template groups

The template list in the editor supports both individual templates and named group
items. A group contains multiple templates in a user-controlled order. Its first
item prints first, then the remaining items in that order.

At the point of use, such as an invoice's print action, the user can select an
individual template or a template group. An individual selection prints that
template; a group selection prints all its members in the saved order.

Templates and groups are not required to belong exclusively to one application
section. A two-item group containing an invoice and a report is supported. A group
containing a customer letter followed by an envelope is supported. Cross-section
reuse must preserve each member's data requirements: the later print flow resolves
or requests the report parameters and other context needed by the selected group.
It must not silently omit a member because it comes from another section.

## Paper changes and batch printing

Each template has the option **“prompt separate print dialogue for different paper
tray”**. A template needing another tray, paper size, envelope stock or label stock
can create a separate print step. The user can select the appropriate printer/tray
or pause to swap paper before continuing. The setting travels with that template
when it is used inside a group.

Batch printing is a later capability of the same system. A batch applies the
selected individual template or ordered group to multiple selected records or
customers, retaining the correct recipient's dynamic data and the declared order.
The key acceptance scene is a letter-plus-envelope group for 200 customers: each
letter and corresponding envelope remain correctly associated and ordered, with
the envelope's separate-dialog option respected. A deliberate paper-change pause
is acceptable where automatic tray handling is unavailable.

The later implementation plan must specify batch collation and print-step
boundaries, especially when a manual tray change would otherwise repeat for every
recipient. It must distinguish preparing output, handing it to a print dialog and
confirmed physical printing. This roadmap does not select a browser, operating
system or printer-driver mechanism or promise unattended hardware tray control.

## Future acceptance examples

- Choose among several invoice layouts, with a usable basic default available.
- Build a custom proposal with authored scope text, customer references, selected
  item details and the company phone number; preview it at its selected paper size.
- Reorder an invoice-plus-report group and obtain both outputs in that order from
  the invoice print flow, with the report's inputs resolved explicitly.
- Batch a letter-plus-envelope group for 200 customers with correct customer data,
  stable association/order and a separate paper-selection step where configured.
- Design and preview a label layout against its actual sheet or roll dimensions.

Template grouping, data references and paper profiles are part of the future
system's design from the outset; expanded document coverage and batch execution
can arrive in later increments. No speculative foundation work is authorized now.
