# Workbench UI inventory and redesign

Scope: existing V1 functions, September 18, 2026. This inventory covers 72 templates and their shared controls; it is not a claim that every business state has been visually tested. Verification evidence lists the actual screens and journeys exercised. QuickBooks Desktop's compact task centers and transaction layouts, and Online's section navigation and create actions, informed the design. Bookflow retains its own behavior and branding.

| Area / templates | Existing problem | Element disposition and intended result |
| --- | --- | --- |
| `base`, `home`, `index`, `login` | Dense wrapping navigation, repeated action explanations, future features mixed with usable actions | Persistent desktop sidebar; labelled mobile menu; company/account utilities in header; live action shortcuts and workflow groups; unavailable work remains on the future-features page. Command links use human titles. |
| `master_list`, `list`, `picker`, `browse_value` | Search and configuration consume the first screen; raw metadata competes with records | Search and apply remain primary; column/filter customization is expandable. Phone records show compact summaries with expandable selected details and a sort menu. Keep selected criteria, sorting, pagination, inactive records and exact reference identity. |
| `master_detail`, `record` | Technical payloads and command verbs compete with record content | Human headings/actions, readable existing sections, expandable technical results. Preserve related records and status. |
| `form`, `sales_document_form` | Generated descriptions and optional metadata delay entry; technical words leak into labels | Common fields and lines first; optional details/help disclosed; retain controls and wire values. Browser validation opens enclosing sections. |
| `sales_detail`, `bill_detail`, `receipt_detail`, `document_nav`, delete forms | Navigation boilerplate before document identity; inconsistent actions | Document identity leads, compact previous/next/find controls; concise actions. Keep deleted/voided status, posting consequences and recovery. |
| `payments`, `pay_bills`, `credit_apply`, refund history | Repeated implementation instructions obscure selection and amounts | Short task guidance; retain balances, allocation controls, previews, validation and retry behavior. |
| `deposit_items`, `deposit_detail`, `register`, reconciliation | Dense tables and technical status text | Shared table hierarchy, contained scrolling or existing mobile cards, meaningful totals and status. Keep all monetary columns and reconciliation controls. |
| `work_detail`, `billing*`, `receipt_costs`, inventory reports | Internal descriptions compete with the next business action | Concise work/billing/receiving instructions, recognizable action names, preserved source selections and inventory-cost consequences. |
| `basic_report`, `statement`, `dimensional_statement`, summaries, cash flow, tax and aging | Long schema descriptions, oversized export explanations, totals push content down on phone | Human report names; filters above output, open initially and on errors; concise export/print actions, optional report explanation, consistent totals and table styling. Preserve basis/date context, full export/print and pagination. |
| Company/user settings, `permissions`, `audit`, annotations | Schema details presented as everyday instructions | Company information first, settings-section navigation, Yes/No labels with unchanged wire values, and optional help/technical sections; all configuration, security decisions and audit records remain available. |

## Shared elements

- Retain URLs, field names, reference IDs, omission/clear semantics, preview/save contracts, warnings, errors, printing and CSV behavior.
- Move optional explanation to disclosure; do not hide transaction consequences or required controls.
- Keep company identity visible. The menu indicates the current section and supports keyboard dismissal.
- Desktop tables retain comparable columns. Phone entry uses existing stacked line cards; wide comparison tables stay inside their own scroll containers. No forced orientation.
- Use consistent typography, borders, spacing, button hierarchy and focus indicators. No external fonts or decorative assets are required.
- Human visual acceptance remains necessary; reference comparison is a design input, not a claim of objective superiority.

## Verification of this increment

Independent plan, code and bounded visual review passed. Verification includes actual live-action/menu destinations; 41 generated-form/runtime checks; nine existing browser checks for line entry and dates; company reporting defaults/export/full printing and actual phone invoice saving; keyboard navigation and unsaved invoice rotation; and four desktop/phone list journeys using visible controls. The latter covers exact amounts, selected columns, sorting, filters, pagination and stale recovery.

Representative visual inspection includes desktop, portrait desktop, 320/390px phones and 844px landscape. Screens include overview, lists, invoice entry/detail, report output, register, deposits and settings, with additional representative workflow captures. This is scoped UI evidence, not a claim that every accounting state or the entire repository test suite was exercised. No accounting, storage, schema or permission policy was changed.

The next acceptance step is a human walkthrough on desktop and phone. Record visual preferences and specific workflow friction as follow-up items; the QuickBooks references do not substitute for that judgment.
