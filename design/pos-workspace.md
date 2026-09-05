# Point of sale workspace — future roadmap

This is future product direction from the human's brainstorming, not an active
implementation plan. Core development remains paused for the roadmap discussion.
It adds no POS code, permissions, payment integration or initial-release obligation.

## Requested experience

Sales receipt entry has a POS mode: a focused checkout workspace with large,
easy-touch item selection across multiple pages. A user can browse/select items
and assemble a sale through this workspace. The experience should support the
familiar visual item selection of dedicated touch POS systems.

User account administration, alongside privileges and menu options, includes a
starting-location selection. A user dedicated to POS can have access limited to
POS and land directly in that workspace after login, ready to enter sales.
Another authorized user can use a different starting location. This is an explicit
future extension to login landing behavior, not a change to the present demo.

A sales-receipt/invoice hybrid mode is a possibility raised by the human. The
precise workflow is open for later discussion; it is not a new accounting document
type or a decided payment policy.

POS uses the shared [print-template system](print-templates.md), including receipt
widths, content-dependent length, repeating item elements and ordered groups.
Invoice and estimate output may use those media too.

## Design input to carry into the later plan

The following are Navigator recommendations for the future plan, distinguished
from the human's requested experience:

- Keep the starting location separate from authorization. Opening POS by default
  does not grant or revoke permissions. A POS-only configuration must enforce its
  permitted operations at the shared command boundary, including direct URLs and
  other interfaces; hiding menu entries alone is insufficient.
- Build the POS experience over the shared sales commands and exact accounting
  facts. A future choice between taking payment now and invoicing a customer must
  have explicit accounting outcomes. Paying an existing invoice continues to
  settle that invoice without creating a second sale.
- Treat “ready for POS” as a complete login journey. The later plan resolves which
  company/workspace opens for a dedicated operator and what happens when access or
  the configured destination changes. It must preserve authorized deep links and
  existing multi-company isolation deliberately, rather than guessing a company.

Exact item-page organization, touch layout, checkout steps and the hybrid workflow
are deferred to the human's later UI design session. Card terminals, scanners,
cash drawers, offline selling and hardware integrations have not been requested
or selected by this brainstorming note. Do not infer a vendor feature inventory
from the visual POS references.

## Future acceptance scenes

- Configure a dedicated operator's POS privileges and starting location; after
  login, the operator reaches the permitted company's POS workspace ready to sell.
- Select items with touch controls across multiple item pages and retain the
  assembled sale while changing pages.
- Print short and long sales using one receipt template, preserving all item
  details and totals while the receipt length follows the content.

Begin with the established core workflows. Introduce POS and its UI after the
required sales/item/payment capabilities exist; no speculative foundation work is
authorized now. The possible invoice hybrid remains a later design question.
