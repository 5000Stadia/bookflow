# 12 — Customer/job workflow

## Shared browser query consumers

Ordinary company list tables use each ListDefinition's query_command and summary_columns, with projection=summary and a bounded page. Preserve URL query/filter/sort/direction/inactive/column state on next-page links; changing a filter resets the cursor. A stale cursor shows a clear restart action preserving filters, never a false empty list. Do not expose the machine query verb as a second editable form/workflow.

Reference suggestions call query with projection=reference and limit=25. Reference eligibility and labels come from authoritative domain metadata. Child-unit choices remain restricted to the selected component's unit set. Put missing nested and relationship reference declarations in shared descriptors; remove the parallel browser-only noun/path map. A remote reference lookup never loads the full matching collection before discarding all but 25 rows.

## Human-readable references

Use a reusable labeled reference control: visible name search and stable hidden submitted id, plus current-value state and a clear action where permitted. Resolve names by selecting returned options, not by guessing ambiguous free text. Changing visible text invalidates the hidden selection until an eligible option is chosen; validation prevents accidental submission of a stale hidden id. Retain inactive current references for unchanged edits while excluding them from new selections.

Every nested control has a unique selector-safe DOM id independent of its colon-delimited form field name. Collection add/reorder/remove preserves child identity, form names, selected ids and labels. Changing a component invalidates any unit selection that no longer belongs to it. Top-level polymorphic and linked-record references retain their discriminator and version behavior. Add-new uses the existing scoped return token with same-origin validation, returns the new readable label/id/version to the originating control, and preserves the rest of the unsaved form. Shared event handlers must remain correct after HTMX navigation and dynamically added collection entries.

## Customer/job workspace

A presentation module declares field labels/groups and routes; it contains no accounting, authorization, inheritance, or write logic. Customer list/detail/create/update retain their established URLs and core commands. List pages show readable customer/job names and useful scalar contact/status columns. A customer detail groups identity, contacts, addresses, commercial defaults and inheritance sources; its jobs are a bounded parent-filtered query with readable links. Full technical fields remain accessible via an explicit details disclosure or view, and uncommon commands retain generated forms.

An authorized Add job action opens customer create with a validated same-company parent and explains that the new job currently inherits from its parent where the existing core says so. Never change the live-versus-copy-once policy in this row. Editing groups common fields first and keeps less common fields in accessible sections; no field disappears from command coverage. Preview retains the complete attempt and rendered versions. Success makes the saved record visible with readable feedback; conflicts preserve input and explain re-reading/reviewing the conflicting fields without silently replacing the attempted version.

## Verification and human milestone

Real Chrome: login, search/paging, detail/jobs, create job, edit, preview/save, unchanged-inactive reference, name-based reference selection, add-new return, nested item/component/unit/vendor selection, and a two-client conflict preserving attempted input. Keyboard-only common path, phone-width layout with no body overflow, selector validity and no JavaScript errors. Existing form translation/version/role/redaction tests remain; tests that asserted visible raw identifiers are updated to assert hidden wire identity plus readable display.

Give the human the isolated running URL and concrete tasks: find a customer, add/open a job, change a contact, preview/save, select a related value by name, and report confusing labels/navigation or lost input. This is a functionality milestone, not a request for permission to continue independent work. Do not touch the real data root, expose a public demo, reset a checkpoint without need, or stop unrelated processes.
