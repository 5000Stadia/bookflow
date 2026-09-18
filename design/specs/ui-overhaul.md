# Human-directed UI overhaul

Status: authorized 2026-09-18; independent plan review passed; implementation and browser verification underway.

Inventory: `design/UI-INVENTORY.md`. Optional visual preference unanswered; proceeding with modern layout and Desktop-style efficiency.

## Outcome
User: "random explanations in the interface" and elements "organized in a way that is a bit nonsensical". Inventory every existing workbench page family, redesign the interface around daily bookkeeping, using QuickBooks Desktop and Online as references. Phone portrait and landscape must both work. Functional scope is existing Bookflow; no new accounting features. Human judgment remains the final aesthetic acceptance, not an unsupported claim of objective superiority to QuickBooks.

## Design
A calm, compact accounting workspace: persistent left navigation on desktop, company identity and account utilities in a slim header, clear page titles, one obvious main action and quieter secondary actions. Modern neutral surfaces with a restrained green accent, strong readable typography, aligned numerical columns. Desktop efficiency without squeezing controls on touch devices. No external fonts or assets.

Navigation separates work (sales/customers, purchases/vendors, banking, inventory, reports) from company administration and advanced tools. Preserve permission-filtered existing routes. Show current section; phone navigation opens via an explicit labelled control, works by keyboard, and closes by Escape/outside click. No hover-only navigation. No dead or invented feature buttons. Planned functionality belongs on its existing separate page, not in the daily home workspace.

Home presents available workflow actions in coherent groups, without duplicated labels, implementation dependencies or paragraphs per tile. Section pages show human-readable titles/actions. Lists put search, filters and creation together; details put identity/status and relevant actions first. Forms keep common fields and totals prominent, optional settings grouped, and preserve all actual inputs, validation, warnings, unsaved data, posting previews and save/confirm behavior. Technical explanations/schema descriptions/raw JSON move to deliberately expandable help/technical areas. Do not blanket-hide error messages, transaction consequences, required inputs or selection instructions. Reports keep filters together and output visually distinct; preserve CSV/print and basis meaning. Administration and audit remain accessible with clear labels.

Shared styling must cover all families: login/company selection, home/section navigation, customer/vendor/item/account lists and details, sales/purchase documents, payments/credits/refunds, deposits/reconciliation/register, work/billing, reports, company/user settings, audit and uncommon generated command screens. Inventory names exact templates and defects per family. Shared changes are not evidence that every family has been visually checked.

## Phone/tablet
At390x844 and844x390, viewport does not scroll sideways; wide financial tables scroll inside labelled containers. Preserve column relationships for ledgers, use stacked form fields and existing mobile transaction rows, keep monetary values intact. Touch targets at least44px on phone, visible keyboard focus,16px phone inputs, no fixed actions covering fields/soft keyboard. Desktop1440x1000 with compact rows and generous working area. Also check320px width,200%zoom-equivalent narrow viewport, keyboard-only navigation and print styles; do not force landscape.

## Verification and release
Inventory plus bounded references before design; independent plan and artifact review. Run a disposable authenticated preview from a copy of demo data, no live passwords/data changes. Capture before/after screenshots across representative populated and empty families on desktop/phone portrait/landscape; exercise navigation, create/edit/validation, picker/date interactions, primary actions, report filters/CSV/print. Record family coverage, real defects and dispositions. Preserve business-command and permission contracts. Relevant existing UI behavior tests, meaningful new responsive/keyboard tests, no snapshots that merely duplicate CSS. Iterate visual defects. Push reviewed milestone to existing integration destination; deploy only completed verified UI under existing human direction, preserving data. Keep remaining work explicit; do not claim whole UI complete after a shell-only reskin.

## References
- Supplied QuickBooks Enterprise24.0 In-Depth Guide, User Interface Basics/Home/Customer/VendorCenter (pages12–18).
- Supplied QuickBooks2016 Missing Manual for transaction and list conventions.
- Intuit Online welcome guide with navigation, create menu, invoice/payment/contact screenshots: https://media.intuit.com/en_US/QBO_welcome_guide_en_US/Content/Topics/guides/qbo_welcome_guide.htm
- Current Online navigation reference: https://quickbooks.intuit.com/learn-support/en-au/help-article/bookkeeping-processes/understand-navigation-menu-quickbooks-intuit/L310NeoHY_AU_en_AU
References inform hierarchy and workflows, not a requirement to copy branding, promotions, AI panels or subscribed features.
