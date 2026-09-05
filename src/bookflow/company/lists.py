"""Authoritative Row 5 list definitions and dependency-light query helpers.

The registry, command factory, services, documentation, and workbench project
these immutable definitions.  This module deliberately imports neither the
database schema nor command models, keeping command discovery cheap.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata
from typing import Any, Literal, Mapping, Sequence

from bookflow.core.errors import BookflowError


MAX_DISPLAY_NAME = 200
MAX_DISPLAY_KEY = 400
MAX_HIERARCHY_DEPTH = 5
MAX_FULL_NAME = 1004
MAX_FULL_NAME_KEY = 2004

FilterKind = Literal["boolean", "identifier", "integer", "text"]
ReferencePolicy = Literal["hard", "soft", "explicit"]
SortDirection = Literal["asc", "desc"]

_INTEGER_RE = re.compile(r"^-?(?:0|[1-9]\d*)$")


def _list_filter(problem: str, **details: Any) -> BookflowError:
    return BookflowError("E_LIST_FILTER", details={"problem": problem, **details})


@dataclass(frozen=True)
class FilterDefinition:
    field: str
    kind: FilterKind = "text"

    def parse(self, raw: str) -> bool | int | str:
        if self.kind == "boolean":
            if raw == "true":
                return True
            if raw == "false":
                return False
            raise _list_filter("boolean filters accept only true or false", field=self.field, value=raw)
        if self.kind == "integer":
            if not _INTEGER_RE.fullmatch(raw):
                raise _list_filter("integer filter is malformed", field=self.field, value=raw)
            return int(raw)
        if not raw:
            raise _list_filter("filter value is empty", field=self.field)
        return raw


@dataclass(frozen=True)
class SortTerm:
    field: str
    direction: SortDirection = "asc"
    nulls_last: bool = False


@dataclass(frozen=True)
class ReferenceDefinition:
    field: str
    target: str
    policy: ReferencePolicy = "soft"
    many: bool = False
    child_units: bool = False

    @property
    def target_nouns(self) -> tuple[str, ...]:
        """Return every noun accepted by this reference descriptor."""
        return tuple(self.target.split("|"))


@dataclass(frozen=True)
class HierarchyDefinition:
    parent_field: str = "parent_id"
    display_field: str = "full_name"
    separator: str = ":"
    maximum_depth: int = MAX_HIERARCHY_DEPTH


@dataclass(frozen=True)
class ParsedFilter:
    field: str
    value: bool | int | str


@dataclass(frozen=True)
class ListDefinition:
    noun: str
    table: str
    route_slug: str
    singular_label: str
    plural_label: str
    record_type: str
    identifier: str
    selector_fields: tuple[str, ...]
    display_field: str
    editable_output_path: tuple[str, ...]
    hierarchy: HierarchyDefinition | None
    search_fields: tuple[str, ...]
    filters: tuple[FilterDefinition, ...]
    sorts: tuple[str, ...]
    default_sort: tuple[SortTerm, ...]
    default_columns: tuple[str, ...]
    additional_columns: tuple[str, ...]
    references: tuple[ReferenceDefinition, ...]
    dependents: tuple[str, ...]
    aggregate_fields: tuple[str, ...]
    custom_fields: bool
    ui_group: str
    ui_order: int
    primary_collection_action: str
    collection_actions: tuple[str, ...]
    record_actions: tuple[str, ...]
    runtime_field_provider: str | None = None

    @property
    def query_command(self) -> str:
        return f"{self.noun} query"

    @property
    def summary_columns(self) -> tuple[str, ...]:
        """Bounded query fields, named identically to the complete show output."""
        aliases = {
            "parent": "parent_id", "base": "base_unit",
            "purchase": "default_purchase_unit", "sales": "default_sales_unit",
            "shipping": "default_shipping_unit",
        }
        return tuple(aliases.get(field, field) for field in self.default_columns)

    @property
    def filter_map(self) -> Mapping[str, FilterDefinition]:
        return {item.field: item for item in self.filters}

    @property
    def all_columns(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys((*self.default_columns, *self.additional_columns)))

    def parse_filters(self, entries: Sequence[str]) -> tuple[ParsedFilter, ...]:
        definitions = self.filter_map
        parsed: list[ParsedFilter] = []
        for entry in entries:
            if not isinstance(entry, str) or "=" not in entry:
                raise _list_filter("filters use field=value", value=entry)
            field, raw = entry.split("=", 1)
            definition = definitions.get(field)
            if definition is None:
                raise _list_filter("unknown filter field", field=field, allowed=sorted(definitions))
            parsed.append(ParsedFilter(field, definition.parse(raw)))
        return tuple(parsed)

    def resolve_sort(self, field: str | None, direction: str = "asc") -> tuple[SortTerm, ...]:
        if field is None:
            terms = list(self.default_sort)
        else:
            if field not in self.sorts:
                raise _list_filter("unknown sort field", field=field, allowed=list(self.sorts))
            if direction not in ("asc", "desc"):
                raise _list_filter("sort direction accepts only asc or desc", direction=direction)
            terms = [SortTerm(field, direction)]
        if not any(term.field == self.identifier for term in terms):
            terms.append(SortTerm(self.identifier))
        return tuple(terms)


def normalize_display_name(value: Any, *, field: str = "name") -> tuple[str, str]:
    """Return a trimmed NFC display value and its case-folded uniqueness key."""
    if not isinstance(value, str):
        raise BookflowError("E_VALIDATION", details={"fields": [{"field": field, "problem": "must be text"}]})
    display = unicodedata.normalize("NFC", value.strip())
    if not display:
        raise BookflowError("E_VALIDATION", details={"fields": [{"field": field, "problem": "must not be empty"}]})
    if ":" in display:
        raise BookflowError("E_VALIDATION", details={"fields": [{"field": field, "problem": "must not contain a colon"}]})
    key = unicodedata.normalize("NFC", display.casefold())
    if len(display) > MAX_DISPLAY_NAME or len(key) > MAX_DISPLAY_KEY:
        raise BookflowError("E_VALIDATION", details={"fields": [{"field": field, "problem": "exceeds the list-name bound"}]})
    return display, key


def hierarchy_projection(name: Any, parent_full_name: str | None, *, depth: int) -> tuple[str, str]:
    """Validate and return a materialized colon-separated name and key."""
    display, _ = normalize_display_name(name)
    if isinstance(depth, bool) or not isinstance(depth, int) or not 1 <= depth <= MAX_HIERARCHY_DEPTH:
        raise BookflowError("E_HIERARCHY_DEPTH", details={"depth": depth, "maximum": MAX_HIERARCHY_DEPTH})
    full_name = f"{parent_full_name}:{display}" if parent_full_name else display
    full_name = unicodedata.normalize("NFC", full_name)
    full_name_key = unicodedata.normalize("NFC", full_name.casefold())
    if len(full_name) > MAX_FULL_NAME or len(full_name_key) > MAX_FULL_NAME_KEY:
        raise BookflowError("E_VALIDATION", details={"fields": [{"field": "full_name", "problem": "exceeds the hierarchy-name bound"}]})
    return full_name, full_name_key


_BOOLEAN_FILTERS = frozenset({
    "active", "base_unit", "converted", "eligible_1099", "is_system", "is_tax_agency",
    "profile_complete", "purchase_enabled", "released", "required", "sales_enabled",
    "taxable", "track_reimbursable_expenses", "unconverted", "unreleased",
})
_INTEGER_FILTERS = frozenset({"display_order", "current_balance", "open_balance"})


def _filter(field: str) -> FilterDefinition:
    if field in _BOOLEAN_FILTERS:
        return FilterDefinition(field, "boolean")
    if field in _INTEGER_FILTERS:
        return FilterDefinition(field, "integer")
    if field == "parent_id" or field.endswith("_id"):
        return FilterDefinition(field, "identifier")
    return FilterDefinition(field)


def _sort_term(value: str) -> SortTerm:
    parts = value.split(":")
    return SortTerm(parts[0], "desc" if "desc" in parts[1:] else "asc", "nulls-last" in parts[1:])


_UI_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Customers and sales", ("customer", "customer-type", "job-type", "sales-rep", "price-level", "ship-method", "customer-message")),
    ("Vendors and purchases", ("vendor", "vendor-type")),
    ("Employees", ("employee", "other-name")),
    ("Items", ("item", "item-category", "unit-of-measure")),
    ("Accounting", ("account", "class", "term", "payment-method", "sales-tax-code")),
    ("Settings", ("custom-field",)),
)
_UI_LOCATION = {
    noun: (group, order)
    for group, nouns in _UI_GROUPS
    for order, noun in enumerate(nouns)
}


def _refs(*values: tuple[str, str, ReferencePolicy] | tuple[str, str, ReferencePolicy, bool] | tuple[str, str, ReferencePolicy, bool, bool]) -> tuple[ReferenceDefinition, ...]:
    return tuple(ReferenceDefinition(*value) for value in values)


def _define(
    noun: str,
    table: str,
    labels: tuple[str, str],
    display_field: str,
    *,
    selector: tuple[str, ...],
    hierarchical: bool,
    search: tuple[str, ...],
    filters: tuple[str, ...],
    sorts: tuple[str, ...],
    default_sort: tuple[str, ...],
    default_columns: tuple[str, ...],
    additional_columns: tuple[str, ...],
    references: tuple[ReferenceDefinition, ...] = (),
    dependents: tuple[str, ...] = (),
    aggregates: tuple[str, ...] = (),
    custom_fields: bool = False,
) -> ListDefinition:
    group, order = _UI_LOCATION[noun]
    commands = tuple(f"{noun} {verb}" for verb in ("create", "show", "list", "update", "activate", "deactivate"))
    return ListDefinition(
        noun=noun,
        table=table,
        route_slug=noun,
        singular_label=labels[0],
        plural_label=labels[1],
        record_type=noun.replace("-", "_"),
        identifier="id",
        selector_fields=selector,
        display_field=display_field,
        editable_output_path=(),
        hierarchy=HierarchyDefinition() if hierarchical else None,
        search_fields=search,
        filters=tuple(_filter(field) for field in filters),
        sorts=sorts,
        default_sort=tuple(_sort_term(value) for value in default_sort),
        default_columns=default_columns,
        additional_columns=additional_columns,
        references=references,
        dependents=dependents,
        aggregate_fields=aggregates,
        custom_fields=custom_fields,
        ui_group=group,
        ui_order=order,
        primary_collection_action=commands[0],
        collection_actions=(commands[0], commands[2]),
        record_actions=(commands[1], commands[3], commands[4], commands[5]),
        runtime_field_provider="custom-fields" if custom_fields else None,
    )


LIST_DEFINITIONS: dict[str, ListDefinition] = {}


def _add(definition: ListDefinition) -> None:
    if definition.noun in LIST_DEFINITIONS:
        raise ValueError(f"duplicate list definition {definition.noun}")
    LIST_DEFINITIONS[definition.noun] = definition


_add(_define(
    "account", "accounts", ("Account", "Accounts"), "full_name", selector=("id", "full_name"), hierarchical=True,
    search=("name", "full_name", "number", "description", "institution_name", "institution_account_last4", "note"),
    filters=("active", "type", "parent_id", "currency", "tax_line", "is_system", "default_class_id", "track_reimbursable_expenses"),
    sorts=("full_name", "number", "type", "balance", "updated_at", "hierarchy_order"),
    default_sort=("number:asc:nulls-last", "full_name:asc", "id:asc"),
    default_columns=("number", "full_name", "type", "balance", "currency", "active"),
    additional_columns=("parent", "description", "currency", "tax_line", "institution_name", "next_check_number", "default_class_id", "track_reimbursable_expenses", "reimbursable_income_account_id", "system_role", "is_system", "available_balance", "updated_at", "$common"),
    references=_refs(("parent_id", "account", "hard"), ("default_class_id", "class", "soft"), ("reimbursable_income_account_id", "account", "hard")),
    dependents=("account", "item", "vendor"),
))
_add(_define(
    "customer", "customers", ("Customer or job", "Customers and jobs"), "full_name", selector=("id", "full_name"), hierarchical=True,
    search=("name", "full_name", "company_name", "effective-contact-names", "effective-contact-points", "effective-billing-address", "effective-shipping-addresses", "account_number", "customer_type", "job_type", "sales_rep", "notes", "$custom-searchable"),
    filters=("active", "parent_id", "customer_or_job", "customer_type_id", "job_type_id", "job_status", "sales_rep_id", "price_level_id", "sales_tax_code_id", "preferred_payment_method_id", "linked_vendor_id", "current_balance", "open_balance"),
    sorts=("full_name", "company_name", "primary_contact", "phone", "current_balance", "open_balance", "customer_type", "sales_rep", "job_status", "updated_at"),
    default_sort=("full_name", "id"),
    default_columns=("full_name", "company_name", "primary_contact", "phone", "current_balance", "open_balance", "customer_type", "sales_rep", "active"),
    additional_columns=("account_number", "postal_code", "email", "payment_method", "terms", "credit_limit", "price_level", "tax_code", "tax_item", "job_status", "job_dates", "delivery_method", "ship_method", "linked_vendor", "$custom", "$common"),
    references=_refs(("parent_id", "customer", "hard"), ("customer_type_id", "customer-type", "soft"), ("job_type_id", "job-type", "soft"), ("sales_rep_id", "sales-rep", "soft"), ("job_sales_rep_id", "sales-rep", "soft"), ("price_level_id", "price-level", "soft"), ("sales_tax_code_id", "sales-tax-code", "soft"), ("sales_tax_item_id", "item", "soft"), ("preferred_payment_method_id", "payment-method", "soft"), ("terms_id", "term", "soft"), ("preferred_ship_method_id", "ship-method", "soft"), ("default_class_id", "class", "soft"), ("linked_vendor_id", "vendor", "explicit"), ("customer", "customer", "explicit"), ("vendor", "vendor", "explicit")),
    dependents=("customer",), aggregates=("addresses", "contacts", "custom_fields", "vendor_link"), custom_fields=True,
))
_add(_define(
    "vendor", "vendors", ("Vendor", "Vendors"), "name", selector=("id", "name"), hierarchical=False,
    search=("name", "company_name", "person_fields", "contact_fields", "address", "account_number", "print_name_on_check_as", "vendor_type", "item_vendor_identifiers", "notes", "$custom-searchable"),
    filters=("active", "vendor_type_id", "terms_id", "eligible_1099", "is_tax_agency", "default_class_id", "linked_customer_id"),
    sorts=("name", "company_name", "primary_contact", "phone", "open_balance", "terms", "vendor_type", "updated_at"), default_sort=("name", "id"),
    default_columns=("name", "company_name", "primary_contact", "phone", "open_balance", "terms", "vendor_type", "active"),
    additional_columns=("account_number", "check_name", "credit_limit", "tax_id_last4", "eligible_1099", "is_tax_agency", "email", "postal_code", "default_accounts", "billing_rate", "linked_customer", "$custom", "$common"),
    references=_refs(("vendor_type_id", "vendor-type", "soft"), ("terms_id", "term", "soft"), ("default_class_id", "class", "soft"), ("linked_customer_id", "customer", "explicit"), ("expense_account_ids", "account", "soft", True), ("expense_accounts.account_id", "account", "soft")),
    dependents=("item",), aggregates=("contacts", "expense_accounts", "custom_fields", "customer_link"), custom_fields=True,
))
_add(_define(
    "employee", "employees", ("Employee", "Employees"), "name", selector=("id", "name"), hierarchical=False,
    search=("name", "job_title", "address", "phone", "email", "emergency_contact", "notes", "$custom-searchable"),
    filters=("active", "released", "unreleased", "employment_type", "profile_complete", "default_class_id"),
    sorts=("name", "hire_date", "release_date", "profile_complete", "updated_at"), default_sort=("name", "id"),
    default_columns=("name", "phone", "email", "hire_date", "release_date", "profile_complete", "active"),
    additional_columns=("job_title", "print_name_on_check_as", "employment_type", "address", "emergency_contact", "default_class", "tax_id_last4", "$custom", "$common"),
    references=_refs(("default_class_id", "class", "soft")), dependents=("sales-rep",), aggregates=("custom_fields",), custom_fields=True,
))
_add(_define(
    "other-name", "other_names", ("Other name", "Other names"), "name", selector=("id", "name"), hierarchical=False,
    search=("name", "company_name", "person_fields", "contact_fields", "address", "account_number", "notes", "$custom-searchable"),
    filters=("active", "converted", "unconverted", "conversion_target", "default_class_id"), sorts=("name", "updated_at"), default_sort=("name", "id"),
    default_columns=("name", "company_name", "contact", "phone", "email", "active"), additional_columns=("address", "account_number", "conversion_target", "default_class", "updated_at", "$custom", "$common"),
    references=_refs(("default_class_id", "class", "soft")), aggregates=("custom_fields",), custom_fields=True,
))
_add(_define(
    "item", "items", ("Item", "Items"), "full_name", selector=("id", "full_name"), hierarchical=True,
    search=("name", "full_name", "description", "purchase_description", "manufacturer_part_number", "barcode", "category", "vendor_item_identifiers", "notes", "$custom-searchable"),
    filters=("active", "type", "parent_id", "category_id", "sales_enabled", "purchase_enabled", "sales_tax_code_id", "preferred_vendor_id", "unit_of_measure_set_id", "default_class_id"),
    sorts=("type", "full_name", "price", "cost", "quantity_on_hand", "preferred_vendor", "category", "updated_at"), default_sort=("type", "full_name", "id"),
    default_columns=("type", "full_name", "price", "cost", "quantity_on_hand", "preferred_vendor", "active"),
    additional_columns=("category", "tax_code", "all-account-references", "reorder_points", "unit_set", "manufacturer_part_number", "barcode", "default_class", "average_cost", "inventory_value", "inventory_values_available", "$custom", "$common", "$stored"),
    references=_refs(("parent_id", "item", "hard"), ("category_id", "item-category", "soft"), ("sales_tax_code_id", "sales-tax-code", "soft"), ("preferred_vendor_id", "vendor", "hard"), ("payment_method_id", "payment-method", "soft"), ("tax_agency_vendor_id", "vendor", "hard"), ("vendor_id", "vendor", "soft"), ("unit_of_measure_set_id", "unit-of-measure", "hard"), ("default_class_id", "class", "soft"), ("account_ids", "account", "hard", True), ("member_item_ids", "item", "hard", True), ("members.component_item_id", "item", "hard"), ("members.unit_id", "unit-of-measure", "hard", False, True), ("vendor_profiles.vendor_id", "vendor", "soft")),
    dependents=("item", "price-level"), aggregates=("members", "vendor_profiles", "custom_fields"), custom_fields=True,
))


def _simple(
    noun: str,
    table: str,
    labels: tuple[str, str],
    display_field: str,
    *,
    hierarchical: bool,
    search: tuple[str, ...],
    filters: tuple[str, ...],
    sorts: tuple[str, ...],
    default_sort: tuple[str, ...],
    default_columns: tuple[str, ...],
    additional_columns: tuple[str, ...],
    references: tuple[ReferenceDefinition, ...] = (),
    dependents: tuple[str, ...] = (),
    aggregates: tuple[str, ...] = (),
) -> None:
    _add(_define(noun, table, labels, display_field, selector=("id", display_field), hierarchical=hierarchical,
                 search=search, filters=filters, sorts=sorts, default_sort=default_sort,
                 default_columns=default_columns, additional_columns=additional_columns,
                 references=references, dependents=dependents, aggregates=aggregates))


for _noun, _table, _labels, _dependents in (
    ("item-category", "item_categories", ("Item category", "Item categories"), ("item-category", "item")),
    ("class", "classes", ("Class", "Classes"), ("class", "account", "customer", "vendor", "employee", "other-name", "item")),
    ("customer-type", "customer_types", ("Customer type", "Customer types"), ("customer-type", "customer")),
    ("vendor-type", "vendor_types", ("Vendor type", "Vendor types"), ("vendor-type", "vendor")),
    ("job-type", "job_types", ("Job type", "Job types"), ("job-type", "customer")),
):
    _simple(_noun, _table, _labels, "full_name", hierarchical=True, search=("name", "full_name"), filters=("active", "parent_id"),
            sorts=("full_name", "updated_at"), default_sort=("full_name", "id"), default_columns=("full_name", "parent", "active"),
            additional_columns=(("usage_count", "$common") if _noun in ("item-category", "class") else ("$common",)),
            references=_refs(("parent_id", _noun, "hard")), dependents=_dependents)

_simple("term", "terms", ("Term", "Terms"), "name", hierarchical=False, search=("name",), filters=("active", "kind"),
        sorts=("name", "kind", "updated_at"), default_sort=("name", "id"), default_columns=("name", "kind", "due_rule_summary", "discount_rule_summary", "active"), additional_columns=("$stored", "$common"), dependents=("customer", "vendor"))
_simple("payment-method", "payment_methods", ("Payment method", "Payment methods"), "name", hierarchical=False, search=("name",), filters=("active", "kind"),
        sorts=("name", "kind", "updated_at"), default_sort=("name", "id"), default_columns=("name", "kind", "active"), additional_columns=("$common",), dependents=("customer",))
_simple("sales-tax-code", "sales_tax_codes", ("Sales tax code", "Sales tax codes"), "code", hierarchical=False, search=("code", "description"), filters=("active", "taxable"),
        sorts=("code", "taxable", "updated_at"), default_sort=("code", "id"), default_columns=("code", "description", "taxable", "active"), additional_columns=("$common",), dependents=("customer", "item"))
_simple("sales-rep", "sales_reps", ("Sales representative", "Sales representatives"), "name", hierarchical=False, search=("name", "initials", "source_name"), filters=("active", "source_type"),
        sorts=("name", "initials", "updated_at"), default_sort=("name", "id"), default_columns=("initials", "source_name", "source_type", "active"), additional_columns=("name", "$common"),
        references=_refs(("name_id", "employee|vendor|other-name", "soft")), dependents=("customer",))
_simple("ship-method", "ship_methods", ("Ship method", "Ship methods"), "name", hierarchical=False, search=("name",), filters=("active",),
        sorts=("display_order", "name", "updated_at"), default_sort=("display_order", "name", "id"), default_columns=("display_order", "name", "active"), additional_columns=("$common",), dependents=("customer",))
_simple("customer-message", "customer_messages", ("Customer message", "Customer messages"), "name", hierarchical=False, search=("name", "text"), filters=("active",),
        sorts=("display_order", "name", "updated_at"), default_sort=("display_order", "name", "id"), default_columns=("display_order", "name", "text", "active"), additional_columns=("$common",))
_simple("price-level", "price_levels", ("Price level", "Price levels"), "name", hierarchical=False, search=("name", "per_item_item_names"), filters=("active", "kind", "currency"),
        sorts=("name", "kind", "percent", "item_count", "currency", "updated_at"), default_sort=("name", "id"), default_columns=("name", "kind", "fixed_percent_or_item_count", "currency", "rounding_summary", "active"), additional_columns=("$stored", "per_item_summary", "$common"),
        references=_refs(("item_ids", "item", "soft", True), ("items.item_id", "item", "soft")), dependents=("customer",), aggregates=("per_item_prices",))
_simple("unit-of-measure", "units_of_measure", ("Unit of measure set", "Units of measure"), "name", hierarchical=False, search=("name", "unit_names", "unit_abbreviations"), filters=("active", "base_unit"),
        sorts=("name", "base_unit", "default_purchase_unit", "default_sales_unit", "default_shipping_unit", "related_unit_count", "updated_at"), default_sort=("name", "id"), default_columns=("name", "base", "purchase", "sales", "shipping", "related_unit_count", "active"), additional_columns=("every_conversion", "$common"),
        dependents=("item",), aggregates=("units",))
_simple("custom-field", "custom_field_defs", ("Custom field", "Custom fields"), "name", hierarchical=False, search=("name", "target_type", "choice_labels"), filters=("active", "target_type", "kind", "required"),
        sorts=("position", "name", "kind", "target_type", "updated_at"), default_sort=("position", "name", "id"), default_columns=("position", "name", "target_types", "kind", "required", "active"), additional_columns=("$stored", "choice_summary", "$common"),
        dependents=("customer", "vendor", "employee", "other-name", "item"), aggregates=("scopes", "choices"))


def get_list_definition(noun: str) -> ListDefinition | None:
    return LIST_DEFINITIONS.get(noun)


def all_list_definitions() -> tuple[ListDefinition, ...]:
    return tuple(sorted(LIST_DEFINITIONS.values(), key=lambda item: (tuple(group for group, _ in _UI_GROUPS).index(item.ui_group), item.ui_order)))


def validate_list_definitions() -> None:
    """Fail at startup/tests if projected registry and workbench metadata is incomplete."""
    expected = {noun for _, nouns in _UI_GROUPS for noun in nouns}
    if set(LIST_DEFINITIONS) != expected:
        raise ValueError(f"list definitions disagree with UI assignment: {sorted(set(LIST_DEFINITIONS) ^ expected)}")
    routes: set[str] = set()
    tables: set[str] = set()
    for noun, definition in LIST_DEFINITIONS.items():
        if noun != definition.noun or not definition.route_slug or definition.route_slug in routes:
            raise ValueError(f"{noun}: invalid or duplicate route slug")
        if not definition.table or definition.table in tables:
            raise ValueError(f"{noun}: invalid or duplicate table")
        routes.add(definition.route_slug)
        tables.add(definition.table)
        if definition.identifier not in definition.selector_fields:
            raise ValueError(f"{noun}: selector omits identifier")
        if not definition.search_fields or not definition.sorts or not definition.default_columns:
            raise ValueError(f"{noun}: search, sorts, and default columns are required")
        if not {term.field for term in definition.default_sort} <= set((*definition.sorts, definition.identifier)):
            raise ValueError(f"{noun}: default sort is not declared")
        if definition.resolve_sort(None)[-1] != SortTerm(definition.identifier):
            raise ValueError(f"{noun}: stable id sort is missing")
        filter_names = [item.field for item in definition.filters]
        if len(filter_names) != len(set(filter_names)):
            raise ValueError(f"{noun}: duplicate filter")
        expected_commands = {f"{noun} {verb}" for verb in ("create", "show", "list", "update", "activate", "deactivate")}
        placed = set((*definition.collection_actions, *definition.record_actions))
        if placed != expected_commands or definition.primary_collection_action not in definition.collection_actions:
            raise ValueError(f"{noun}: lifecycle action placement is incomplete")
        for reference in definition.references:
            unknown_targets = set(reference.target_nouns) - set(LIST_DEFINITIONS)
            if unknown_targets:
                raise ValueError(f"{noun}: unknown reference target {sorted(unknown_targets)}")
        for dependent in definition.dependents:
            if dependent not in LIST_DEFINITIONS:
                raise ValueError(f"{noun}: unknown dependent noun {dependent}")


validate_list_definitions()
