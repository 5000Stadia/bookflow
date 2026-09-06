"""SQL-first bounded projections for company lists; no per-record reads."""

from __future__ import annotations

import json
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Callable

import sqlalchemy as sa

from bookflow.company import schema, list_service
from bookflow.company.lists import get_list_definition
from bookflow.company.query import QueryInput, page_state, continuation
from bookflow.company.query_sql import execute as execute_query
from bookflow.core.money import Money

# Python str.strip() population semantics, including non-ASCII whitespace.
_WHITESPACE = "\t\n\v\f\r \x1c\x1d\x1e\x1f\x85\xa0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000"


@dataclass
class Provider:
    table: sa.Table
    search: dict = field(default_factory=dict)
    filters: dict = field(default_factory=dict)
    sorts: dict = field(default_factory=dict)
    columns: dict = field(default_factory=dict)
    extra: dict = field(default_factory=dict)
    visible: Any = None
    ordinary_filters: list[str] | None = None
    transform: Callable | None = None
    batch: Callable | None = None
    search_handled: bool = False
    parameters: dict = field(default_factory=dict)


def _label(table, identifier):
    display = table.c.full_name if "full_name" in table.c else table.c.name
    return sa.select(display).where(table.c.id == identifier).scalar_subquery()


def contains_any(needle, *values):
    """Literal Unicode containment within a field, never across field boundaries."""
    for value in values:
        if value is None:
            continue
        if not isinstance(value, str):
            continue
        folded = value.casefold() if value.isascii() else unicodedata.normalize("NFC", unicodedata.normalize("NFC", value).casefold())
        if needle in folded:
            return 1
    return 0


def _exact_search(noun, p, needle):
    """Declared scalar fields and set-based owned matches without concatenation."""
    if noun in {"vendor", "employee", "other-name"}:
        from bookflow.commands import party_cmds as command
    table = p.table
    matches = lambda *columns: sa.func.bookflow_query_contains(needle, *columns) == 1
    direct, predicates = [], []

    def owned(owner, columns, *conditions, source=None):
        statement = sa.select(owner).where(*conditions, matches(*columns))
        if source is not None:
            statement = statement.select_from(source)
        predicates.append(table.c.id.in_(statement))

    for field in get_list_definition(noun).search_fields:
        if field in table.c:
            direct.append(table.c[field])
        elif field == "$custom-searchable":
            values, definitions = schema.custom_field_values, schema.custom_field_defs
            owned(values.c.record_id, (values.c.canonical_text,), values.c.record_type == noun.replace("-", "_"),
                values.c.active.is_(True), definitions.c.kind.in_(("text", "choice")),
                source=values.join(definitions, definitions.c.id == values.c.def_id))
        elif field == "person_fields":
            direct.extend(table.c[name] for name in command._PERSON_FIELDS)
        elif field == "address":
            direct.extend(table.c[f"address_{leaf}"] for leaf in command.AddressOutput.model_fields)
        elif field == "emergency_contact":
            direct.extend(table.c[f"emergency_contact_{leaf}"] for leaf in ("name", "relationship", "phone", "email"))
        elif field == "contact_fields" and noun == "other-name":
            direct.extend((table.c.contact, table.c.phone, table.c.email))
        elif field == "contact_fields" and noun == "vendor":
            contacts, points = schema.vendor_contacts, schema.vendor_contact_points
            owned(contacts.c.vendor_id, tuple(contacts.c[name] for name in command.ContactOutput.model_fields
                if name not in {"id", "role", "points"}), contacts.c.active.is_(True))
            owned(contacts.c.vendor_id, (points.c.custom_label, points.c.value),
                contacts.c.active.is_(True), points.c.active.is_(True),
                source=points.join(contacts, contacts.c.id == points.c.contact_id))
        elif field in {"item_vendor_identifiers", "vendor_item_identifiers"}:
            profiles = schema.item_vendor_profiles
            columns = (profiles.c.vendor_item_name, profiles.c.manufacturer_part_number)
            if noun == "vendor":
                owned(profiles.c.vendor_id, (*columns, schema.items.c.full_name),
                    profiles.c.active.is_(True), schema.items.c.active.is_(True),
                    source=profiles.join(schema.items, schema.items.c.id == profiles.c.item_id))
            else:
                owned(profiles.c.item_id, columns, profiles.c.active.is_(True))
        elif field in {"unit_names", "unit_abbreviations"}:
            child = schema.unit_conversions
            owned(child.c.unit_of_measure_id, (child.c.name if field == "unit_names" else child.c.abbreviation,), child.c.active.is_(True))
        elif field == "per_item_item_names":
            child, item = schema.price_level_items, schema.items
            owned(child.c.price_level_id, (item.c.full_name,), child.c.active.is_(True),
                source=child.join(item, item.c.id == child.c.item_id))
        elif field in p.search:
            # Remaining expressions are single resolved reference labels.
            direct.append(p.search[field])
        else:
            raise ValueError(f"{noun}: missing exact query search field {field}")
    return sa.or_(matches(*direct), *predicates)


def employee_completeness(session):
    """One role-aware AND-of-OR predicate for selection, sorting and display."""
    from bookflow.commands.party_cmds import _can_reveal_tax
    from bookflow.company.info import EMPLOYEE_PROFILE_BUILTIN_PATHS
    requirements = session.company_info_row["required_employee_profile_fields"]
    if isinstance(requirements, str):
        requirements = json.loads(requirements)
    table = schema.employees
    reveal_suffix = _can_reveal_tax(session)

    def populated(path):
        if path.startswith("custom_fields."):
            values, definitions = schema.custom_field_values, schema.custom_field_defs
            scopes = schema.custom_field_scopes
            # Canonical bool/number values remain populated even when false/0.
            return sa.exists(sa.select(1).select_from(values.join(definitions, definitions.c.id == values.c.def_id)).where(
                values.c.record_id == table.c.id, values.c.record_type == "employee",
                values.c.def_id == path.split(".", 1)[1], values.c.active.is_(True),
                definitions.c.active.is_(True), sa.func.trim(values.c.canonical_text, _WHITESPACE) != "",
                sa.exists(sa.select(1).where(scopes.c.definition_id == values.c.def_id,
                    scopes.c.record_type == "employee", scopes.c.active.is_(True), scopes.c.definition_active.is_(True))),
            )).correlate(table)
        if path not in EMPLOYEE_PROFILE_BUILTIN_PATHS:
            raise ValueError("unregistered stored employee requirement")
        if path == "tax_id_last4" and not reveal_suffix:
            return sa.false()
        value = table.c[path.replace("address.", "address_")]
        return sa.and_(value.is_not(None), sa.func.trim(value, _WHITESPACE) != "")

    return sa.and_(sa.true(), *(sa.or_(*(populated(path) for path in group)) for group in requirements))


@lru_cache(maxsize=1)
def _customer_search_predicate():
    """Set-based substring search; each owned relation is scanned once."""
    from bookflow.commands import party_cmds as command
    customers = schema.customers
    needle = sa.bindparam("customer_query_needle")
    matches = lambda *expressions: sa.func.bookflow_query_contains(needle, *expressions)
    # A hierarchical full_name contains its leaf name verbatim, so one lookup
    # covers both declared fields without repeating normalization.
    predicates = [matches(*(customers.c[name] for name in ("full_name", "company_name", "account_number", "notes"))) == 1]

    def owners(effective_owner, table, owner, columns, *conditions, from_clause=None):
        statement = sa.select(owner.label("owner_id")).select_from(table if from_clause is None else from_clause)
        candidates = statement.where(*conditions, matches(*columns) == 1).cte()
        # Empty source matches need no ancestor resolution for each customer.
        predicates.append(sa.and_(sa.exists(sa.select(1).select_from(candidates)),
            effective_owner.in_(sa.select(candidates.c.owner_id))))

    contacts = schema.customer_contacts
    contact_fields = [contacts.c[name] for name in command.ContactOutput.model_fields if name not in {"id", "role", "points"}]
    contact_owner = command._customer_collection_owner("contact_mode")
    owners(contact_owner, contacts, contacts.c.customer_id, contact_fields, contacts.c.active.is_(True))
    points = schema.customer_contact_points
    owners(contact_owner, points, contacts.c.customer_id, (points.c.custom_label, points.c.value),
        points.c.active.is_(True), contacts.c.active.is_(True), from_clause=points.join(contacts, contacts.c.id == points.c.contact_id))
    addresses = schema.customer_addresses
    owners(command._customer_collection_owner("address_mode"), addresses, addresses.c.customer_id,
        (addresses.c.label, *(addresses.c[f"address_{leaf}"] for leaf in command.AddressOutput.model_fields)),
        addresses.c.active.is_(True))
    billing = customers.alias("query_billing_source")
    def billing_owner(ancestor):
        leaves = [ancestor.c[f"billing_{leaf}"] for leaf in command.AddressOutput.model_fields]
        return sa.case((sa.or_(*(leaf.is_not(None) for leaf in leaves)), ancestor.c.id))
    billing_leaves = tuple(billing.c[f"billing_{leaf}"] for leaf in command.AddressOutput.model_fields)
    owners(command._customer_inherited_expression("query_billing_owner", billing_owner), billing, billing.c.id,
        billing_leaves, sa.or_(*(leaf.is_not(None) for leaf in billing_leaves)))
    effective_rep = sa.func.coalesce(command._customer_effective("job_sales_rep_id"), command._customer_effective("sales_rep_id"))
    for table, foreign_key in ((schema.customer_types, command._customer_effective("customer_type_id")),
            (schema.job_types, customers.c.job_type_id), (schema.sales_reps, effective_rep)):
        display = table.c.full_name if "full_name" in table.c else table.c.name
        owners(foreign_key, table, table.c.id, (display,))
    values, definitions = schema.custom_field_values, schema.custom_field_defs
    owners(customers.c.id, values, values.c.record_id, (values.c.canonical_text,),
        values.c.record_type == "customer", values.c.active.is_(True),
        definitions.c.kind.in_(("text", "choice")), from_clause=values.join(definitions, definitions.c.id == values.c.def_id))
    return sa.or_(*predicates)


def _party(noun, inp, session):
    from bookflow.commands import party_cmds as command
    table = getattr(schema, {"customer": "customers", "vendor": "vendors", "employee": "employees", "other-name": "other_names"}[noun])
    search, filters, sorts = command._expressions(noun)
    p = Provider(table, dict(search), dict(filters), dict(sorts))
    if noun in {"customer", "vendor"}:
        customer = noun == "customer"
        contacts = schema.customer_contacts if customer else schema.vendor_contacts
        owner = command._customer_collection_owner("contact_mode") if customer else table.c.id
        p.columns.update(
            primary_contact=command._contact_sort(table, contacts, f"{noun}_id", owner, "display_name", fold=False),
            phone=command._contact_sort(table, contacts, f"{noun}_id", owner, "work_phone", primary_only=True, fold=False),
        )
        if customer:
            if inp.query and inp.query.strip():
                needle = list_service.normalize_lookup_key(inp.query)
                p.visible = _customer_search_predicate()
                p.parameters["customer_query_needle"] = needle
                p.search_handled = True
            effective_type = command._customer_effective("customer_type_id")
            effective_rep = sa.func.coalesce(command._customer_effective("job_sales_rep_id"), command._customer_effective("sales_rep_id"))
            p.columns.update(customer_type=_label(schema.customer_types, effective_type), sales_rep=_label(schema.sales_reps, effective_rep))
        else:
            p.columns.update(terms=_label(schema.terms, table.c.terms_id), vendor_type=_label(schema.vendor_types, table.c.vendor_type_id))
        balance = "current_balance" if customer else "open_balance"
        if customer:
            from bookflow.company.customer_balances import register_functions
            from bookflow.core.exact import _require_i64
            register_functions(session.company)
            p.columns.update(current_balance=p.sorts["current_balance"], open_balance=p.sorts["current_balance"])
            def transform(row):
                for name in ("current_balance", "open_balance"):
                    if name in row:
                        row[name] = Money(_require_i64(int(row[name]), field=name),
                            session.company_info_row["home_currency"]).to_dict()
                return row
            p.transform = transform
        else:
            p.columns[balance] = sa.literal(0)
            p.transform = lambda row: {**row, balance: Money(0, session.company_info_row["home_currency"]).to_dict()}
    elif noun == "employee":
        complete = employee_completeness(session)
        p.columns["profile_complete"] = p.filters["profile_complete"] = p.sorts["profile_complete"] = complete
    return p


def _profiles(noun, inp, session):
    from bookflow.company import profiles
    p = Provider(profiles.TABLES[noun])
    if noun == "sales-rep":
        from bookflow.commands.profile_cmds import _sales_rep_expressions
        p.search, p.filters = _sales_rep_expressions()
        t = p.table
        p.columns["source_type"] = t.c.name_type
        p.columns["source_name"] = sa.case(
            (t.c.name_type == "employee", _label(schema.employees, t.c.name_id)),
            (t.c.name_type == "vendor", _label(schema.vendors, t.c.name_id)),
            else_=_label(schema.other_names, t.c.name_id),
        )
    elif noun == "term":
        p.extra = {name: column for name, column in p.table.c.items()}
        p.columns.update(due_rule_summary=sa.literal(""), discount_rule_summary=sa.literal(""))
        def transform(row):
            due, discount = profiles.term_rule_summaries(profiles._payload_from_row(noun, row))
            return {**row, "due_rule_summary": due, "discount_rule_summary": discount}
        p.transform = transform
    return p


def _account(inp, session):
    from bookflow.company.accounts import balance_expression
    from bookflow.core.exact import _require_i64
    table = schema.accounts
    balance = balance_expression(session.company)
    return Provider(table, filters={"is_system": table.c.system_role.is_not(None)},
        sorts={"balance": balance, "hierarchy_order": table.c.path},
        columns={"balance": balance},
        transform=lambda row: {**row, "balance": Money(_require_i64(int(row["balance"]), field="balance"),
            session.company_info_row["home_currency"]).to_dict()} if 'balance' in row else row)


def _from_options(table, options):
    return Provider(table, search=options.get("search_expressions", {}),
        filters=options.get("filter_expressions", {}), sorts=options.get("sort_expressions", {}),
        visible=options.get("visible"), ordinary_filters=options.get("filters"))


def _item(inp, session):
    from bookflow.company import items
    table = schema.items
    p = _from_options(table, items.item_query_options(session.company, filters=inp.filter))
    p.columns.update(price=sa.null(), cost=sa.null(), quantity_on_hand=sa.literal("0"),
        preferred_vendor=_label(schema.vendors, table.c.preferred_vendor_id))
    p.extra = {name: table.c[name] for name in ("price_minor_units", "price_currency", "cost_minor_units", "cost_currency")}
    def transform(row):
        for field in ("price", "cost"):
            money = items._money_output(row, field)
            row[field] = None if money is None else money.model_dump()
        return row
    p.transform = transform
    return p


def _units(inp, session):
    from bookflow.company import units
    table, child = schema.units_of_measure, schema.unit_conversions
    p = _from_options(table, units.unit_query_options())
    base = sa.select(child.c.id).where(child.c.unit_of_measure_id == table.c.id,
        child.c.active.is_(True), child.c.is_base.is_(True)).scalar_subquery()
    names = ("default_purchase_unit", "default_sales_unit", "default_shipping_unit")
    p.columns.update({"base_unit": sa.null(), **{name: sa.null() for name in names},
        "related_unit_count": p.sorts["related_unit_count"]})
    p.extra = {"_base_id": base, **{f"{name}_id": table.c[f"{name}_id"] for name in names}}
    def batch(rows):
        wanted = {row[key] for row in rows for key in ("_base_id", *(f"{name}_id" for name in names)) if row[key] is not None}
        children = {row["id"]: units._child_output(row).model_dump() for row in session.company.conn.execute(
            sa.select(child).where(child.c.id.in_(wanted), child.c.active.is_(True))
        ).mappings()} if wanted else {}
        for row in rows:
            row["base_unit"] = children[row["_base_id"]]
            for name in names:
                row[name] = children.get(row[f"{name}_id"])
        return rows
    p.batch = batch
    return p


def _prices(inp, session):
    from bookflow.company import pricing
    from bookflow.core.exact import format_percentage_millionths
    table = schema.price_levels
    p = _from_options(table, pricing.price_query_options())
    p.columns.update(fixed_percent_or_item_count=sa.literal(""), rounding_summary=sa.literal(""))
    p.extra = {name: table.c[name] for name in ("percent_millionths", "rounding_mode", "rounding_increment_minor_units",
        "rounding_increment_currency", "rounding_offset_minor_units", "rounding_offset_currency")}
    p.extra["_item_count"] = p.sorts["item_count"]
    def transform(row):
        percent = row["percent_millionths"]
        row["fixed_percent_or_item_count"] = format_percentage_millionths(percent) if percent is not None else str(row["_item_count"])
        increment = Money(row["rounding_increment_minor_units"], row["rounding_increment_currency"])
        offset = Money(row["rounding_offset_minor_units"], row["rounding_offset_currency"])
        row["rounding_summary"] = f"{row['rounding_mode']} {increment.to_dict()['amount']} offset {offset.to_dict()['amount']} {increment.currency}"
        return row
    p.transform = transform
    return p


def _custom_field(inp, session):
    table, scopes, choices = schema.custom_field_defs, schema.custom_field_scopes, schema.custom_field_choices
    definition = get_list_definition("custom-field")
    parsed = definition.parse_filters(inp.filter)
    visible = []
    p = Provider(table)
    p.ordinary_filters = [value for value in inp.filter if not value.startswith("target_type=")]
    for item in parsed:
        if item.field == "target_type":
            visible.append(sa.exists(sa.select(1).where(scopes.c.definition_id == table.c.id,
                scopes.c.active.is_(True), scopes.c.record_type == item.value)).correlate(table))
    # Scope ordering is the ordered active scope tuple (position then stable id),
    # encoded with a separator below all characters permitted in record types.
    ordered = sa.select(scopes.c.record_type).where(scopes.c.definition_id == table.c.id,
        scopes.c.active.is_(True)).order_by(scopes.c.position, scopes.c.id).correlate(table).subquery()
    p.sorts["target_type"] = sa.select(sa.func.coalesce(sa.func.group_concat(ordered.c.record_type, "\x1f"), "")).scalar_subquery()
    if inp.query and inp.query.strip():
        needle = list_service.normalize_lookup_key(inp.query)
        contains = lambda column: sa.func.bookflow_query_contains(needle, column) == 1
        visible.append(sa.or_(contains(table.c.name),
            sa.exists(sa.select(1).where(scopes.c.definition_id == table.c.id, scopes.c.active.is_(True), contains(scopes.c.record_type))).correlate(table),
            sa.exists(sa.select(1).where(choices.c.definition_id == table.c.id, choices.c.active.is_(True), contains(choices.c.value))).correlate(table)))
    p.search_handled = True
    p.visible = sa.and_(*visible) if visible else None
    p.columns["target_types"] = sa.null()
    def batch(rows):
        owners = {row["id"]: [] for row in rows}
        if owners:
            for owner, record_type in session.company.conn.execute(sa.select(scopes.c.definition_id, scopes.c.record_type).where(
                scopes.c.definition_id.in_(owners), scopes.c.active.is_(True)).order_by(scopes.c.position, scopes.c.id)):
                owners[owner].append(record_type)
        return [{**row, "target_types": owners[row["id"]]} for row in rows]
    p.batch = batch
    return p


def _provider(noun, inp, session):
    if noun in {"customer", "vendor", "employee", "other-name"}:
        return _party(noun, inp, session)
    if noun == "account":
        return _account(inp, session)
    if noun == "item":
        return _item(inp, session)
    if noun == "custom-field":
        return _custom_field(inp, session)
    if noun == "unit-of-measure":
        return _units(inp, session)
    if noun == "price-level":
        return _prices(inp, session)
    return _profiles(noun, inp, session)


def query_page(noun: str, inp: QueryInput, session, *, principal_id: str | None = None) -> dict:
    from bookflow.company.query_catalog import selected_descriptors, error
    from bookflow.company.query_projection import configure, custom_predicates
    definition = get_list_definition(noun)
    if inp.columns is not None and inp.projection == 'reference':
        raise error(noun, 'Reference projections do not accept selected columns')
    descriptors = selected_descriptors(noun, inp.columns, session) if inp.columns is not None else None
    state = page_state(session, noun, inp, principal_id)
    if inp.query and inp.query.strip():
        session.company.raw.create_function("bookflow_query_contains", -1, contains_any, deterministic=True)
    p = _provider(noun, inp, session)
    if inp.custom_filters:
        predicate = custom_predicates(noun, inp.custom_filters, session, p.table)
        p.visible = predicate if p.visible is None else sa.and_(p.visible, predicate)
    if inp.query and inp.query.strip() and not p.search_handled:
        predicate = _exact_search(noun, p, list_service.normalize_lookup_key(inp.query))
        p.visible = predicate if p.visible is None else sa.and_(p.visible, predicate)
        p.search_handled = True
    selected = dict(id=p.table.c.id, version=p.table.c.version, active=p.table.c.active)
    label = p.table.c[definition.display_field]
    if noun == "account":
        if session.company_info_row["show_lowest_subaccount_only"]:
            label = p.table.c.name
        if session.company_info_row["use_account_numbers"]:
            label = sa.case((p.table.c.number.is_not(None), p.table.c.number + " · " + label), else_=label)
    selected["label"] = label
    decoders = configure(noun, descriptors, p, session) if descriptors is not None else {}
    if inp.projection == "summary":
        names = [descriptor.key for descriptor in descriptors] if descriptors is not None else (definition.display_field, *definition.summary_columns)
        for name in dict.fromkeys(names):
            selected[name] = p.columns.get(name, p.table.c.get(name))
            if selected[name] is None:
                raise ValueError(f"{noun}: missing query projection {name}")
        selected.update(p.extra)
    statement = list_service.list_statement(
        p.table, definition, query=None if p.search_handled else inp.query, filters=inp.filter if p.ordinary_filters is None else p.ordinary_filters,
        sort=inp.sort, direction=inp.direction, include_inactive=inp.include_inactive,
        search_expressions=p.search, filter_expressions=p.filters, sort_expressions=p.sorts, visible=p.visible,
    ).with_only_columns(*(value.label(name) for name, value in selected.items()), maintain_column_froms=True)
    count_statement = statement.order_by(None).with_only_columns(sa.func.count(), maintain_column_froms=True)
    shared_matches = descriptors is not None and bool(inp.custom_filters)
    if shared_matches:
        # Evaluate exact custom predicates once for both rows and total. Materialize
        # only matching IDs, never all selected values or owned collections.
        matched = statement.order_by(None).with_only_columns(p.table.c.id, maintain_column_froms=True).cte('browse_matches').prefix_with('MATERIALIZED')
        total_expression = sa.select(sa.func.count()).select_from(matched).scalar_subquery()
        statement = sa.select(*(value.label(name) for name, value in selected.items()),
            total_expression.label('__matching_total')).select_from(p.table).where(
                p.table.c.id.in_(sa.select(matched.c.id))).order_by(*statement._order_by_clauses)
    total = execute_query(session.company.conn, count_statement, p.parameters).scalar_one() if descriptors is not None and not shared_matches else None
    rows = [dict(row) for row in execute_query(session.company.conn, statement.limit(inp.limit + 1).offset(state.offset), p.parameters).mappings()]
    if shared_matches:
        total = rows[0]['__matching_total'] if rows else (
            execute_query(session.company.conn, count_statement, p.parameters).scalar_one() if state.offset else 0)
    more = len(rows) > inp.limit
    rows = rows[:inp.limit]
    if inp.projection == "summary":
        if p.batch is not None:
            rows = p.batch(rows)
        if p.transform is not None:
            rows = [p.transform(row) for row in rows]
        if descriptors is not None:
            rows = [{**{name: row[name] for name in ('id', 'version', 'label', 'active')},
                'values': {descriptor.key: decoders[descriptor.key](row[descriptor.key], row) if descriptor.key in decoders else row[descriptor.key]
                           for descriptor in descriptors}} for row in rows]
        else:
            allowed = {"id", "version", "label", "active", definition.display_field, *definition.summary_columns}
            rows = [{name: value for name, value in row.items() if name in allowed} for row in rows]
    output = {"projection": inp.projection, "items": rows, "count": len(rows), "next_cursor": continuation(state, len(rows), more)}
    if descriptors is not None:
        output.update(columns=descriptors, matching_total=total)
    return output
