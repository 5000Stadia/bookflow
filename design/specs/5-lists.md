# Lists

## Boundary

This row adds persistent company master data, its lifecycle invariants, the shared browser list shell, and generated documentation for every current list command. It includes account chart templates, all list nouns in blueprint sections 10.1 and 11, typed runtime custom fields, composite audit events, and compensating list-event undo.

The row does not post opening balances or inventory quantities, calculate ledger balances, build assemblies, apply sales pricing or tax, store provider credentials, merge records, import spreadsheets, or build bespoke per-list centers. Those operations remain in their named later sections of the blueprint. Derived fields whose source does not exist yet return the unavailable/zero state declared there and are never accepted as input.

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
- `custom_field_defs`, `custom_field_choices`, and `custom_field_values`.

Every primary record uses the common provenance/version columns. Every list record has `active`. Normalized owned child rows have stable ids, owner id, position, active state, and their specific columns, but no independent version; they are versioned and audited through the owner. Foreign keys, partial unique indexes, and check constraints enforce structural integrity. The SQLAlchemy metadata carries non-empty table and column descriptions and matches the frozen migration exactly.

Migration creates structure only. It never chooses a chart or invents business records for an upgraded company. A legacy company remains chartless and may receive an explicit `chart apply`. New-company rollout and demo reset use the ordinary Row 5 services to create chart/profile data with actor provenance. A new hub migration projects the expanded frozen capability set without modifying prior migration files.

The migration path is tested from an empty database and a populated `co0002` database. Failure restores the pre-migration backup and leaves no partial revision. Reopening a successfully upgraded copied company preserves every Row 5 row and reference.

## Exact values and aggregates

Money inputs use the blueprint's decimal-string form and reject JSON floats and booleans. Storage uses integer minor units plus currency with an all-null-or-all-present constraint. Output uses amount string, currency, and minor units.

Percentages use signed integer millionths of one percentage point, quantities use signed integer micro-units, and unit factors use positive integer nano-units. Shared validators accept canonical decimal strings within each field's range and precision, return canonical strings, and never convert through float.

Owned child collections are strict ordered objects in command input and normalized rows in storage. Omission preserves the collection; a supplied list replaces it; an empty list clears it. Retained child ids retain identity. Unknown keys, duplicate positions, duplicate semantic keys, inactive references, and wrong record types fail before mutation. The deterministic aggregate owner snapshot includes child collections and custom values, while derived hierarchy projections remain outside snapshots.

## Shared list kernel

`bookflow.company.lists` owns a declarative list definition for every noun: table, selector, hierarchical behavior, input/output projectors, search fields, typed filters, sorts, columns, references, dependents, aggregate fields, custom-field eligibility, and UI group. It provides:

- NFC/case-folded normalized keys and visible-only selector suggestions;
- id-first then canonical-name resolution;
- hierarchy validation and bulk projection maintenance;
- active-reference and dependency validation;
- common create/read/update/activate/deactivate storage;
- version-history and logical-field grouping for disjoint merges;
- deterministic aggregate snapshots and projections;
- list query, typed filtering, stable sorting, and column metadata;
- a dependency registry shared by deactivation and undo.

`bookflow.commands.list_cmds` registers the six lifecycle commands for every declared noun. Each noun keeps strict typed input/output models; complex noun validation remains in its company service. Create accepts idempotency. Update, activate, and deactivate expose `expected_version` and the existing conflict, disjoint-merge, blind-write warning, clear, dry-run, reason, and directive behavior. No-op writes preserve version and create no audit event.

Hierarchical tables materialize `full_name`, `full_name_key`, depth, and an internal ULID path. Root and non-root partial uniqueness indexes enforce sibling uniqueness despite nullable parents. A name containing `:` is rejected. Reparenting prevalidates the complete subtree, updates all derived projections in one bulk operation, increments only the edited record, and returns affected descendant ids. Depth six and cycles fail without writes.

Deactivation refuses active descendants unless `cascade` is true and never cascades across lists. Cascade changes only active rows in the subtree, returns their ids parent-first, increments each changed record, and emits one entry per changed record. Activation requires active ancestors and never activates other rows implicitly. New or changed references require an active target; unchanged historical references remain readable after target deactivation.

## Accounts and charts

`bookflow.company.accounts` enforces the field and type rules in blueprint section 10.1, stable system roles, account hierarchy, number uniqueness, protected bank display metadata, type changes, system-account protection, and derived output placeholders.

Versioned packaged manifests named `general`, `service`, `product`, `contractor`, `retail`, and `nonprofit` contain deterministic parent-first account definitions and exactly one account for every required system role. `chart list` and `chart show` are authenticated routed hub reads. `chart apply` is an idempotent company admin write that validates the whole manifest before mutation and atomically writes accounts plus company chart identity. It fails on any existing account or chart. Chart and rollout events are not undoable.

`company new` defaults to `general`; `--chart none` is an explicit chartless rollout. Profile-list seed manifests are separate from chart manifests, use stable seed keys, and are installed during new rollout without duplication. Demo reset selects the contractor chart and adds ordinary demo records through their registered commands.

## Parties and relationships

`bookflow.company.parties` owns customers/jobs, contacts and addresses, vendors and their ordered expense accounts, employees, other names, links, inheritance projections, completeness output, and conversions.

Customer/job inheritance resolves the nearest stored override without copying future ancestor changes into child rows. Show output distinguishes stored, effective, and source values. Customer/vendor linking uses one active link row with unique customer and vendor constraints; link and unlink version both endpoints and the link in one transaction and audit event.

Other-name conversion creates one new customer, vendor, or employee, deactivates the source, and records the stable conversion target in one event. It never overwrites a target or rewrites historical records. Payroll and protected credential values are not accepted.

## Items, pricing, and units

`bookflow.company.items` owns item/category hierarchies, exact scalar conversions, type-discriminated validation, group and assembly graphs, item-vendor profiles, derived stock placeholders, and item reference rules. Every item type in blueprint section 11.7 has a strict complete valid profile and rejects fields belonging to another type. Membership validation rejects self-reference and indirect cycles before writes.

`bookflow.company.pricing` owns fixed-percent and per-item price levels, exact rounding inputs, ordered entries, and the company feature setting. It stores definitions and assignments only; no sales price engine runs in this row.

`bookflow.company.units` owns unit sets, normalized related units, exact factors, same-set defaults, company mode, and exact conversion projection. Items reference a set id rather than free text.

## Supporting lists and custom fields

`bookflow.company.profiles` owns item categories, classes, terms, payment methods, sales-tax codes, customer/vendor/job types, sales representatives, ship methods, and customer messages. The shared kernel supplies ordinary lifecycle behavior; the service supplies kind-specific and polymorphic-reference validation. Seed manifests contain the blueprint's default terms, payment methods, tax codes, shipping choices, and messages as ordinary editable rows.

`bookflow.company.custom_fields` owns definition scope, stable choices, typed value parsing, applicability, active-state behavior, search/filter projection, and aggregate owner snapshots. Static command schemas expose `custom_fields` as a strict mapping. Values are keyed internally by definition id; show output includes id, current label, kind, and typed value. Renaming retains values. Inactive definitions retain readable historical values and reject new or changed values. Custom value logical paths use the definition id so edits to different definitions merge independently.

The command registry permits a runtime form-field provider. Workbench form descriptions join static Pydantic leaves with current definitions for the selected company and noun. Stable definition ids form HTML control keys; labels remain mutable display text. The form adapter translates controls to the static mapping, while the service performs every existence, activity, type, choice, and conflict check.

## Compensating undo

`undo <event>` is an idempotent company write over eligible Row 5 audit events. An undo-handler registry maps record types/actions to inverse planners and the dependency registry validates the assembled inverse before any write.

The inverse restores only logical fields changed by the original event. Later disjoint changes remain. A later overlapping change, name collision, hierarchy violation, active-reference violation, system rule, or dependent record produces deterministic `E_UNDO_CONFLICT` details and no mutation. Undo-create deactivates rather than deletes. Composite link, conversion, cascade, hierarchy, child-collection, and custom-value events reverse atomically in dependency-safe order.

An undo event records `reason = "undo of <event>"` and `undo_of_event_id`. The unique link prevents a second compensation. Undo events, chart/rollout events, migration/baseline/system events, ledger events, and non-list events return `E_NOT_UNDOABLE`. Authorization requires the undo capability and every capability represented by the original event.

## Browser product shell

The workbench metadata adds noun UI group, display name, primary action, hierarchy presentation, declared columns, filters, and runtime fields. The shared base template provides persistent selected-company context and grouped navigation for Company, Customers and sales, Vendors and purchases, Employees, Items, Accounting, Settings, and Audit.

List pages provide query, typed filters, active/inactive selection, sortable headings, hierarchy indentation, column selection/reordering/reset, stable empty states, and row links. Detail pages provide readable field groups, child collections, provenance, audit context, and capability-filtered actions. Forms provide field groups, reference selectors, exact-value inputs, nested collection entry, preview, conflicts, clear controls, success/error feedback, and runtime custom fields. Layout remains usable at narrow and wide browser widths. Shared templates and CSS own the visual language; noun modules do not add bespoke page logic.

The browser acceptance fixture logs in, lands inside the demo company, traverses every group, performs a representative account/customer/vendor/item lifecycle, uses search/filter/sort/hierarchy, observes a stale conflict without overwrite, sees an undo in audit, and verifies that no visible control points at an unavailable route. The Row 5 candidate runs on an isolated demo root and separate loopback port for the human browser checkpoint; it does not replace or mutate the existing live host.

## Registry, demo, and documentation integration

The registry's noun metadata declares list record type, selector, UI group, columns, and optional runtime form provider. Its lazy module index remains exact and cold root help stays below the current budget. The hub capability migration is generated from a frozen literal corresponding to the expanded registry.

Documentation examples are synthesized from list definitions for the six ordinary verbs and explicitly supplied for charts, links, conversion, and undo. Every example validates against its input and output model. Documentation is regenerated only after schema descriptions, registry coverage, examples, and browser metadata are complete. The generated tree and wheel include every new command/schema page and every chart/profile resource.

Demo reset seeds every primary table, every item type, a three-level hierarchy, customer/jobs, linked customer/vendor profiles, an employee, an other-name conversion candidate, price levels, a unit set, and every custom-field kind. Seeds use real commands so their audit history and versions match production behavior.

## Build sequence and ownership

1. Land the expanded blueprint and this plan after focused plan review.
2. Build the exclusive foundation: migrations and metadata, exact scalars, list definitions/kernel, shared errors, capability projection, aggregate snapshots, command factory, nested input coercion, and foundational tests.
3. On that fixed foundation, build non-overlapping packages for accounts/charts, parties, catalog/pricing/units, profiles, custom fields, and undo. Each package owns its company module, command module, resource subtree where applicable, and focused test file; it does not edit shared integration files.
4. Assemble through the captain-owned registry index, rollout/demo, examples, workbench shell, documentation, error matrix, architecture, and cross-family tests.
5. Run the human browser checkpoint on an isolated root while mechanical verification continues. Subjective findings change the shared shell; command and data behavior remains governed by the specification.
6. Run one focused assembled artifact review over migration/data integrity, hierarchy/dependencies, authorization, composite audit/undo, exact numeric handling, and browser route/action completeness. Generated noun repetition receives parametrized self-check rather than separate review cycles.

## Verification

- Frozen migration and current metadata match on fresh and upgraded databases; injected failures recover without partial rows or capability drift.
- Every declared noun has exactly six correctly permissioned lifecycle commands, typed examples, Python/CLI/HTTP routes, generated workbench routes, and fresh docs.
- All nouns pass a parametrized create/show/list/update/deactivate/hidden/include-inactive/activate lifecycle and idempotent-create replay.
- Normalized collisions, ambiguous selectors, cross-company ids, inactive and wrong-type references, cycles, depth six, invalid reparenting, and unsafe system-account changes fail without writes.
- Three- and five-level trees render correctly. Rename/reparent/cascade use bounded indexed access, deterministic outputs, correct versions, and correct audit cardinality. List/show meet the existing 10,000-record local performance budget.
- Same-field stale writes conflict; disjoint fields and separate custom definitions merge; blind writes warn; nested aggregate changes have stable logical paths; no-ops do not increment versions.
- Money, percent, quantity, and unit values round-trip exactly on Python, CLI, HTTP, and forms; floats, excess precision, and invalid ranges fail.
- Every item type and seed validates; account-role, tax, vendor, unit, group/assembly, and type-change violations fail before mutation.
- Link, unlink, conversion, cascade, chart apply, and aggregate edits each create one event with the exact expected entries and no partial state under injected failure.
- Undo covers create, update with later disjoint and overlapping changes, activation, deactivation, cascade, link/unlink, conversion, hierarchy moves, nested children, and custom values; duplicate, non-list, system, chart, migration, dependency, and permission cases are rejected atomically.
- Every runtime custom-field kind renders and submits from the workbench; rename/deactivation and stale forms preserve identity and enforce server authority.
- The browser journey, OpenAPI/registry parity, public-term scan, docs freshness, wheel contents, cold-start budget, audit footprint budget, and complete suite pass.

## Edges

- Existing active references are not invalidated by deactivation, but no new or changed reference may target an inactive row.
- Derived values and hierarchy projections are returned but do not become editable snapshot fields.
- Record collisions never merge implicitly. Link and conversion remain explicit, separate operations.
- No adapter contains accounting, reference, custom-field, or undo rules.
- No test or preview writes migrations, idempotency rows, list rows, audit rows, or browser result state.
