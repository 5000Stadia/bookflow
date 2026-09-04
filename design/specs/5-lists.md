# Lists

## Boundary

This row adds persistent company master data, its lifecycle invariants, the shared browser list shell, and generated documentation for every current list command. It includes account chart templates, all list nouns in blueprint sections 10.1 and 11, typed runtime custom fields, composite audit events, and compensating list-event undo.

The row does not post opening balances or inventory quantities, calculate ledger balances, build assemblies, apply sales pricing or tax, store provider credentials, merge records, import spreadsheets, or build bespoke per-list centers. Those operations remain in their named later sections of the blueprint. Derived fields whose source does not exist yet return the unavailable/zero state declared there and are never accepted as input.

Before `co0003` is written, a neutral machine-readable inventory is completed from the available anchor list documentation. It enumerates every Row 5 field, option, default, validation, filter, sort, column, lifecycle action, and explicitly deferred behavior, then maps each entry to the blueprint and its planned schema/model/service/output/docs/form/test witness. Storage does not freeze while an entry is absent or unmapped.

## Migration and storage

Company migration `co0003_lists` adds the structures below in one frozen revision:

- Row 5 settings and `default_chart_version` on `company_info`;
- unique nullable `undo_of_event_id` on company `audit_events`;
- `accounts`;
- `customers`, `customer_addresses`, and `customer_contacts`;
- `vendors`, `vendor_contacts`, and `vendor_expense_accounts`;
- `customer_vendor_links`;
- `employees` and `other_names`;
- `items`, `item_categories`, `item_members`, and `item_vendor_profiles`;
- `classes`, `terms`, `payment_methods`, and `sales_tax_codes`;
- `customer_types`, `vendor_types`, `job_types`, `sales_reps`, `ship_methods`, and `customer_messages`;
- `price_levels` and `price_level_items`;
- `units_of_measure` and `unit_conversions`;
- `custom_field_defs`, `custom_field_scopes`, `custom_field_choices`, and `custom_field_values`.

Every primary record uses the common provenance/version columns, `active`, and a nullable unique `seed_key` that only chart or profile seeding may set. Normalized owned child rows have stable ids, owner id, array-derived position, active state, and their specific columns, but no independent version; they are versioned and audited through the owner. `custom_field_values` are the explicit exception: they have a stable id and active state but no position because definition plus owner identifies their semantic slot. Foreign keys, partial unique indexes, and check constraints enforce structural integrity. The SQLAlchemy metadata carries non-empty table and column descriptions and matches the frozen migration exactly.

Migration creates structure only. It never chooses a chart or invents business records for an upgraded company. A legacy company remains chartless and may receive an explicit `chart apply`. New-company rollout and demo reset use the ordinary Row 5 services to create chart/profile data with actor provenance. A new hub migration projects the expanded frozen capability set without modifying prior migration files.

The migration path is tested from an empty database and a populated, genuinely old `co0002` database whose tables have the historical column set. Prior migration modules remain unchanged. Because `co0002` imports selected live metadata, `co0003` conditionally adds `undo_of_event_id` when absent and validates the converged shape when a fresh chain already created it. Failure restores the pre-migration backup and leaves no partial revision. Copying a complete company folder to another data root, attaching it, and reopening it preserves every Row 5 row, relationship, custom value, audit snapshot, and principal rendering.

### Frozen physical contracts

- Hierarchical records store `parent_id`, `full_name`, `full_name_key`, `depth`, and a binary-collated internal path encoded as `/ULID/ULID/`. Root and sibling partial indexes enforce name uniqueness and `full_name_key` is list-unique. Primary list names are at most 200 characters, normalized keys at most 400 characters, and `:` is rejected from hierarchy leaves.
- All foreign keys use restrictive deletion and are indexed. Polymorphic references are checked by the company service in the same transaction; SQLite triggers do not duplicate that rule.
- `custom_field_scopes` stores stable child id, definition id, array-derived position, record type, active state, and synchronized definition name/active projections. A partial unique index on active `(record_type, definition_name_key)` enforces overlapping-scope uniqueness. Definition rename or activation updates every scope projection atomically.
- Every unit, including the base unit, is a `unit_conversions` child with a stable id. Exactly one active child is marked `is_base` and has factor exactly one. Purchase, sales, shipping, item-member, and later transaction defaults all reference child ids from the same set.
- A job stores `address_mode` and `contact_mode`, each `inherit` or `own`. `inherit` resolves the nearest ancestor collection; `own` may contain an explicitly empty collection. Top-level customers always use `own`. Search, filters, sorts, list columns, and convenience outputs use effective values and `show` also returns stored values and their source ids.
- Provider, payment, bank-feed, and tax-profile references are nullable opaque columns without Row 5 foreign keys. Row 5 inputs reject non-null values; safe display projections return null until the owning protected store exists. `vendor.billing_rate_level_id` is likewise staged-null until the time-billing rate list exists. `account.order_printable_checks` is a nullable record override whose inherited company default is false.
- `required_employee_profile_fields` is canonical JSON containing an ordered array of requirements; each requirement is a nonempty array of alternative registered, non-secret field paths. Every requirement needs at least one populated alternative. Its default ends with `["phone", "email"]`, so either satisfies that requirement.
- Primary contact roles `primary` and `alternate` are each unique among an owner's active contacts. Convenience contact fields project deterministically from `primary`, then `alternate`, then child order. Shortcut updates edit or create the corresponding contact; supplying a shortcut and collection value for the same logical field with different values is rejected.
- Array order is the canonical owned-child position and callers never submit a separate position. Update items may carry their retained stable id. Retained ids preserve identity, omitted active children become inactive, and new elements receive ids. Ordinary input cannot reactivate an inactive child by id; undo may restore one after dependency validation. Semantic keys are unique among active siblings and may be reused only while the prior child remains inactive.
- Link events include canonical `counterparty_link` before/after values in both endpoint snapshots. Unlink deactivates the existing link; a later link of the same pair reactivates that row if it has not been superseded. A conflicting new pair is rejected.
- Fixed-asset item fields are `asset_number`, `purchase_date`, original-cost money, `vendor_id`, `location`, `serial_number`, `warranty_expiration`, `disposal_status` (`in_service`, `sold`, `disposed`), `disposal_date`, proceeds and disposal-cost money, accumulated-depreciation, depreciation-expense, and gain/loss account ids, `depreciation_method` (`none`, `straight_line`, `declining_balance`, `sum_of_years_digits`, `units_of_production`, `other`), positive nullable `useful_life_months`, and exact book-basis and tax-basis money. Disposal date is required outside `in_service`; proceeds apply only to `sold`.
- Fixed-percent and per-item price-level rounding increment and offset are money pairs in the resolved level currency. Increment is at least one minor unit; offset is signed. `preferred_vendor_id` is derived only from the lowest active unique vendor-profile rank.
- Contains-search uses escaped parameterized matching over normalized declared search projections; child searches use indexed owner/target joins. Stable id is the last sort key. The 10,000-record witness is authoritative; no FTS table is introduced unless that witness fails.
- Seed manifests carry immutable keys persisted in `seed_key`. Rerunning a manifest resolves by seed key, preserves user edits and inactive state, and inserts only absent keys. Profile manifests freeze the terms, payment methods, and tax codes in the blueprint plus ship methods `Delivery`, `Federal Express`, `UPS`, `USPS`, and `Other`, and customer messages `Thank you for your business.`, `We appreciate your prompt payment.`, and `Please remit payment at your earliest convenience.`

## Exact values and aggregates

Money inputs use the blueprint's decimal-string form and reject JSON floats and booleans. Storage uses integer minor units plus currency with an all-null-or-all-present constraint. Output uses amount string, currency, and minor units.

Percentages use signed integer millionths of one percentage point, quantities use signed integer micro-units, and unit factors use positive integer nano-units. Shared validators accept canonical decimal strings within each field's range and precision, return canonical strings, reject values outside signed 64-bit storage, and never convert through float. Conversion results round once to six quantity decimals with decimal half-even; bill-of-material extensions round each component's money product to the currency minor unit with half-even before exact summation. Overflow returns `E_VALUE_RANGE` without writes.

Owned child collections are strict ordered objects in command input and normalized rows in storage. Omission preserves the collection; a supplied list replaces it; an empty list clears it. Unknown keys, duplicate retained ids, duplicate semantic keys, inactive references, and wrong record types fail before mutation. Each whole collection is one concurrency logical path; every custom-field value is a separate `custom_fields.<definition-id>` path. Contact shortcuts fold into the contact-collection path and preferred-vendor shortcuts are output-only. The deterministic aggregate owner snapshot includes active and inactive child identity, active collections, custom values, and endpoint link values, while hierarchy projections remain outside editable snapshots.

## Shared list kernel

`bookflow.company.lists` owns the authoritative declarative list definition for every noun: table, selector, hierarchical behavior, editable-output path, input/output projectors, search fields, typed filters, sorts, columns, references, dependents, aggregate fields, custom-field eligibility, action placement, and UI group. Registry noun metadata references and projects this definition; it never duplicates list behavior. It provides:

- NFC/case-folded normalized keys and visible-only selector suggestions;
- id-first then canonical-name resolution;
- hierarchy validation and bulk projection maintenance;
- active-reference and dependency validation;
- common create/read/update/activate/deactivate storage;
- version-history and logical-field grouping for disjoint merges;
- deterministic aggregate snapshots and projections;
- list query, typed filtering, stable sorting, and column metadata;
- a dependency registry shared by deactivation and undo.

`bookflow.commands.list_cmds` registers the six lifecycle commands for every declared noun. Each noun keeps strict typed input/output models; complex noun validation remains in its company service. Create accepts idempotency. Update, activate, and deactivate expose `expected_version` and the existing conflict, disjoint-merge, clear, dry-run, reason, and directive behavior. A missing `expected_version` is an explicit blind write and always returns a visible warning naming the current version and fields that were not compared; generated browser forms always submit the version they rendered. No-op writes preserve version and create no audit event.

Link and unlink require expected customer, vendor, and link versions when the link exists. Conversion requires the source version. Custom-field definition/value changes use the definition version and owner version respectively. Company-setting references use the company-info version. Chart application requires the chartless state rather than a record version and remains protected by idempotency. Every composite validates all supplied versions before mutation and reports every blocking record in deterministic order.

Hierarchical tables materialize `full_name`, `full_name_key`, depth, and an internal ULID path. Root and non-root partial uniqueness indexes enforce sibling uniqueness despite nullable parents. A name containing `:` is rejected. Reparenting prevalidates the complete subtree, updates all derived projections in one bulk operation, increments only the edited record, and returns affected descendant ids. Depth six and cycles fail without writes.

Deactivation refuses active descendants unless `cascade` is true and never cascades across lists. Cascade changes only active rows in the subtree, returns their ids parent-first, increments each changed record, and emits one entry per changed record. Activation requires active ancestors and never activates other rows implicitly. New or changed references require an active target; unchanged historical references remain readable after target deactivation.

## Accounts and charts

`bookflow.company.accounts` enforces the field and type rules in blueprint section 10.1, stable system roles, account hierarchy, number uniqueness, protected bank display metadata, type changes, system-account protection, and derived output placeholders.

Versioned packaged manifests named `general`, `service`, `product`, `contractor`, `retail`, and `nonprofit` contain deterministic parent-first account definitions and exactly one account for every required system role. `chart list` and `chart show` are authenticated routed hub reads. `chart apply` is an idempotent company admin write that validates the whole manifest before mutation and atomically writes accounts plus company chart identity. It fails on any existing account or chart. Chart and rollout events are not undoable.

`company new` defaults to `general`; `--chart none` is an explicit chartless rollout. Profile-list seed manifests are separate from chart manifests, use stable seed keys, and are installed during new rollout without duplication. Demo reset selects the contractor chart and adds ordinary demo records through their registered commands.

## Parties and relationships

`bookflow.company.parties` owns customers/jobs, contacts and addresses, vendors and their ordered expense accounts, employees, other names, links, inheritance projections, completeness output, and conversions.

Customer/job inheritance resolves the nearest stored override without copying future ancestor changes into child rows. Scalar null means inherit for a job; address and contact modes distinguish inheritance from an explicit empty collection. Show output distinguishes stored, effective, and source values, while list/search/filter/sort use effective values. Customer/vendor linking uses one active link row with unique customer and vendor constraints; link and unlink version both endpoints and the link in one transaction and audit event.

Other-name conversion creates one new customer, vendor, or employee, deactivates the source, and records the stable conversion target in one event. It maps company/person names, address, contact points, account number, class, notes, and applicable custom fields by stable definition scope; customer/vendor defaults remain null and employee hire/emergency fields remain null. Inapplicable source values remain on the inactive source and the preview names them as retained, never discarded. Conversion accepts source `expected_version` and idempotency, never overwrites a target, and never rewrites historical records. Payroll and protected credential values are not accepted.

## Items, pricing, and units

`bookflow.company.items` owns item/category hierarchies, exact scalar conversions, type-discriminated validation, group and assembly graphs, item-vendor profiles, derived stock placeholders, and item reference rules. Every stored item field, nullability, default, enum, input/output name, permitted type, and transition is enumerated in one discriminated model registry consumed by SQL metadata, commands, docs, forms, and tests. Every item type in blueprint section 11.7 has a strict complete valid profile and rejects fields belonging to another type. Membership validation rejects self-reference and indirect cycles before writes.

`bookflow.company.pricing` owns fixed-percent and per-item price levels, exact rounding inputs, ordered entries, and the company feature setting. It stores definitions and assignments only; no sales price engine runs in this row.

`bookflow.company.units` owns unit sets, normalized related units, exact factors, same-set defaults, company mode, and exact conversion projection. Items reference a set id rather than free text.

## Supporting lists and custom fields

`bookflow.company.profiles` owns item categories, classes, terms, payment methods, sales-tax codes, customer/vendor/job types, sales representatives, ship methods, and customer messages. The shared kernel supplies ordinary lifecycle behavior; the service supplies kind-specific and polymorphic-reference validation. Seed manifests contain the blueprint's default terms, payment methods, tax codes, shipping choices, and messages as ordinary editable rows.

`bookflow.company.custom_fields` owns normalized definition scopes, stable choices, typed value parsing, applicability, active-state behavior, search/filter projection, capacity, and aggregate owner snapshots. Static command schemas expose `custom_fields` as a strict mapping keyed canonically by stable definition id on every surface; names are labels and unique selectors only. Values are keyed by definition id and owner; show output includes id, current label, kind, activity, and typed value. Null clears by deactivating the stable value row. Renaming retains values, kind and scopes become immutable after the first value, in-use choices cannot be removed, and inactive definitions retain readable values while rejecting new or changed values. Custom value logical paths use the definition id so edits to different definitions merge independently. A definition service and schema witness permit at least 45 active definitions per record type.

The command registry permits a runtime form-field provider. Workbench form descriptions join static Pydantic leaves with current definitions for the selected company and noun. Stable definition ids form HTML control keys; labels remain mutable display text. The form adapter translates controls to the static mapping, while the service performs every existence, activity, type, choice, and conflict check.

## Compensating undo

`undo <event>` is an idempotency-capable company write over eligible Row 5 audit events. An undo-handler registry maps record types/actions to inverse planners and the dependency registry validates the assembled inverse before any write.

The inverse restores only logical fields changed by the original event. Later disjoint changes remain. A later overlapping change, name collision, hierarchy violation, active-reference violation, system rule, or dependent record produces deterministic `E_UNDO_CONFLICT` details and no mutation. Undo-create deactivates rather than deletes. Composite link, conversion, cascade, hierarchy, child-collection, and custom-value events reverse atomically in dependency-safe order.

An undo event records `reason = "undo of <event>"` and `undo_of_event_id`. Replaying the same idempotency key and input returns the stored successful result; any new request for an already compensated event returns `E_ALREADY_UNDONE` and writes nothing. Undo events, chart/rollout events, migration/baseline/system events, ledger events, and non-list events return `E_NOT_UNDOABLE`. Authorization requires the undo capability and every capability represented by the original event.

### Dependency and authorization matrix

- Structural parentage and required system roles block deactivation; hierarchy cascade follows descendants only.
- Active item posting-account, tax-agency, unit-set, group/assembly-member, and required child dependencies block target deactivation. Active chart-system accounts are always protected.
- Classification and form-default references—types, classes, terms, payment methods, sales reps, ship methods, customer messages, price levels, tax codes, and optional defaults—are soft: existing references remain readable and render their inactive target, while new or changed references require an active target.
- Link endpoints and custom definitions with active values block deactivation unless their explicit unlink or definition rules are used. Inactive dependent owners never block a target.
- Ordinary list reads require member access; ordinary writes require standard access and the noun capability. Chart apply requires admin plus account capability. Link/unlink require customer and vendor capabilities. Conversion requires other-name and target-noun capabilities. Undo requires its own capability and every capability represented by the original event. Runtime form metadata uses the same read authorization as its backing list command.

## Browser product shell

The workbench projects noun metadata from the authoritative list definition: route slug, labels, selector, editable-output path, display field, ordered UI group, primary collection action, allowed collection/record actions, hierarchy presentation, declared columns, filters, reference descriptors, and runtime fields. Registry validation rejects incomplete metadata or misplaced actions. The shared base template provides persistent selected-company context and grouped navigation for Company, Customers and sales, Vendors and purchases, Employees, Items, Accounting, Settings, and Audit. Every authorized company page refreshes the last-company cookie.

List pages take query, typed filters, active/inactive selection, sort field/direction, and stable tie ordering through command inputs; URL-only column selection/reordering/reset never leaks into commands. Declared columns render even when no row exists, and row links always use the declared selector. Detail pages provide readable field groups, child collections, provenance, audit context, and capability-filtered actions. Forms use one typed descriptor for rendering and decoding, structured JSON path tuples, exact-string numeric codecs, bounded same-company reference suggestions, repeated controls, nested child ids/order, preview, conflicts, clear controls, and stable-id runtime custom fields. Attempted values survive preview and validation failure separately from the authoritative original/version snapshot.

The base template declares the viewport; grouped navigation collapses without hiding company identity; form grids become single-column at narrow widths; and only table containers scroll horizontally. Automated browser checks run at 360×800 and 1280×800 and reject body overflow or controls outside the viewport. Shared templates and CSS own the visual language; noun modules do not add bespoke page logic.

The browser acceptance fixture logs in, lands inside the demo company, traverses every group, performs a representative account/customer/vendor/item lifecycle, uses search/filter/sort/hierarchy, observes a stale conflict without overwrite, sees an undo in audit, and verifies that no visible control points at an unavailable route. The Row 5 candidate runs on an isolated demo root and separate loopback port for the human browser checkpoint; it does not replace or mutate the existing live host.

## Registry, demo, and documentation integration

The registry's noun metadata references its list definition and declares only routing/integration fields plus the optional runtime form provider. Its lazy module index remains exact and cold root help stays below the current budget. The hub capability migration is generated from a frozen literal corresponding to the expanded registry.

Documentation examples are synthesized from list definitions for the six ordinary verbs and explicitly supplied for charts, links, conversion, and undo. Every example validates against its input and output model. A source-blind fresh-agent trial installs the wheel, starts with a fresh root, follows the packaged Python/CLI/HTTP guide verbatim, discovers custom fields, performs a nested write, handles a stale conflict, and runs each special-command family without source access. MCP remains the Row 9 adapter; it consumes these same registry schemas and parity corpus when it exists. Documentation is regenerated only after schema descriptions, registry coverage, examples, and browser metadata are complete. The generated tree and wheel include every new command/schema page and every chart/profile resource.

Demo reset seeds every primary and child table, every item type, active and inactive states, a three-level hierarchy, customer/jobs with both collection modes, linked customer/vendor profiles, an employee, an other-name conversion candidate, price levels, a unit set including base/default units, and every custom-field kind and value. Seeds use real commands so their audit history and versions match production behavior.

## Build sequence and ownership

1. Land the expanded blueprint and this plan after focused plan review.
2. Build the exclusive foundation: migrations and metadata, exact scalars, list definitions/kernel, shared errors, capability projection, aggregate snapshots, command factory, nested input coercion, and foundational tests.
3. On that fixed foundation, build non-overlapping packages for accounts/charts, parties, catalog/pricing/units, profiles, custom fields, and undo. Each package owns its company module, command module, resource subtree where applicable, and focused test file; it does not edit shared integration files.
4. Assemble through the captain-owned registry index, rollout/demo, examples, workbench shell, documentation, error matrix, architecture, and cross-family tests.
5. Run the human browser checkpoint and side-by-side list comparison on an isolated root while mechanical verification continues. Usability findings change the shared shell; any missing anchor field, option, or behavior changes the relevant inventory, contract, and implementation before the row closes.
6. Run one focused assembled artifact review over migration/data integrity, hierarchy/dependencies, authorization, composite audit/undo, exact numeric handling, and browser route/action completeness. Generated noun repetition receives parametrized self-check rather than separate review cycles.

## Verification

- Frozen migration and current metadata match on fresh and upgraded databases; injected failures recover without partial rows or capability drift.
- A neutral machine-readable inventory maps every blueprint Row 5 field, option, default, validation, filter, sort, column, and behavior to its schema/model/service/output/docs/form/test witness; no inventory entry is unmapped.
- Every declared noun has exactly six correctly permissioned lifecycle commands, typed examples, Python/CLI/HTTP routes, generated workbench routes, and fresh docs. Chart, link, unlink, conversion, and undo have the same reachability and authorization coverage.
- All nouns pass a parametrized create/show/list/update/deactivate/hidden/include-inactive/activate lifecycle and idempotent-create replay.
- Normalized collisions, ambiguous selectors, cross-company ids, inactive and wrong-type references, cycles, depth six, invalid reparenting, and unsafe system-account changes fail without writes.
- Three- and five-level trees render correctly. Rename/reparent/cascade use bounded indexed access, deterministic outputs, correct versions, and correct audit cardinality. List/show meet the existing 10,000-record local performance budget.
- Same-field stale writes conflict; disjoint fields and separate custom definitions merge; blind writes warn; nested aggregate changes have stable logical paths; no-ops do not increment versions.
- A shared success/error corpus runs through Python, CLI, HTTP, and workbench translation and compares canonical outputs plus stable error code/details. Every money-bearing model field appears in the exact-value corpus and in a float-free storage/output/audit/generated-example check. MCP joins this corpus in Row 9.
- Money, percent, quantity, unit, conversion, and bill-of-material values round-trip and round exactly on every current surface; floats, booleans, excess precision, overflow, and invalid ranges fail.
- Every item type and seed validates; account-role, tax, vendor, unit, group/assembly, and type-change violations fail before mutation.
- Link, unlink, conversion, cascade, chart apply, and aggregate edits each create one event with the exact expected entries and no partial state under injected failure.
- Undo covers create, update with later disjoint and overlapping changes, activation, deactivation, cascade, link/unlink, conversion, hierarchy moves, nested children, and custom values; duplicate, non-list, system, chart, migration, dependency, and permission cases are rejected atomically.
- Every runtime custom-field kind renders and submits from every current surface; required/default, capacity, overlapping scope, rename/deactivation, in-use choice, transaction staging, and stale forms preserve identity and enforce server authority.
- Adversarial two-organization fixtures prove authorization before validation and indistinguishable missing/inaccessible behavior for company selection, selectors, suggestions, list filters, audit, workbench state, and runtime fields on every current surface.
- Audit witnesses assert actor, actor kind, interface, on-behalf-of principal, reason/directive, source reference, client, session, request, principal mirror, week filtering, and composite entry cardinality.
- Every ordinary and special browser form submits mechanically; authorized actions are complete and unauthorized ones absent. The browser journey, viewport checks, OpenAPI/registry parity, public-term scan, docs freshness, wheel contents, cold-start budget, audit footprint budget, copy/attach fidelity, and complete suite pass.

## Edges

- Existing active references are not invalidated by deactivation, but no new or changed reference may target an inactive row.
- Derived values and hierarchy projections are returned but do not become editable snapshot fields.
- Record collisions never merge implicitly. Link and conversion remain explicit, separate operations.
- No adapter contains accounting, reference, custom-field, or undo rules.
- No test or preview writes migrations, idempotency rows, list rows, audit rows, or browser result state.
