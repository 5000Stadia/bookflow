"""Bounded query contracts, projection parity and continuation safety."""

from __future__ import annotations

import pytest
import sqlalchemy as sa

from bookflow.core import registry
from bookflow.company.lists import LIST_DEFINITIONS

COMPANY = "Demo Plumbing Co"


@pytest.mark.parametrize("noun", LIST_DEFINITIONS)
def test_query_from_fresh_cli_matches_shared_contract(cli, client, noun):
    for projection in ("summary", "reference"):
        actual = cli.json(noun, "query", "--company", COMPANY, "--limit", "2", "--projection", projection)
        expected = client.run(f"{noun} query", {"limit": 2, "projection": projection}, company=COMPANY)
        assert actual == expected
        assert actual["count"] == len(actual["items"]) <= 2


def _db_path(client):
    from pathlib import Path
    return Path(client.company.show(company=COMPANY)["path"]) / "company.db"


def _bulk_customers(client, count):
    """Synthetic, disposable read workload with one contact per customer."""
    from bookflow.company import schema
    from bookflow.core.ids import new_id
    from bookflow.storage.engine import open_database
    seed = client.customer.create(name="Query workload seed", contacts=[{
        "role": "primary", "display_name": "Workload Contact", "work_phone": "555-0100",
    }], company=COMPANY)
    with open_database(_db_path(client), writable=True) as db:
        owner = dict(db.conn.execute(sa.select(schema.customers).where(schema.customers.c.id == seed["id"])).mappings().one())
        contact = dict(db.conn.execute(sa.select(schema.customer_contacts).where(schema.customer_contacts.c.customer_id == seed["id"])).mappings().one())
        owners, contacts = [], []
        for index in range(count):
            record_id = new_id()
            name = f"Workload {index:05}"
            owners.append({**owner, "id": record_id, "name": name, "name_key": name.casefold(),
                "full_name": name, "full_name_key": name.casefold(), "path": f"/{record_id}/", "seed_key": None})
            contacts.append({**contact, "id": new_id(), "customer_id": record_id})
        db.raw.execute("BEGIN IMMEDIATE")
        db.conn.execute(schema.customers.insert(), owners)
        db.conn.execute(schema.customer_contacts.insert(), contacts)
        db.raw.execute("COMMIT")


def test_cursor_rejects_changed_contract_and_audited_change(client):
    from bookflow import BookflowError
    first = client.customer.query(limit=1, company=COMPANY)
    for changes in ({"limit": 2}, {"query": "x"}, {"projection": "reference"}, {"direction": "desc"}):
        with pytest.raises(BookflowError, match="E_VALIDATION"):
            client.customer.query(**{ "limit": 1, "cursor": first["next_cursor"], **changes}, company=COMPANY)
    with pytest.raises(BookflowError, match="E_VALIDATION"):
        client.vendor.query(limit=1, cursor=first["next_cursor"], company=COMPANY)
    for cursor in ("!not-base64", "e30", "a" * 2049):
        with pytest.raises(BookflowError, match="E_VALIDATION"):
            client.customer.query(limit=1, cursor=cursor, company=COMPANY)
    client.customer.create(name="Invalidate query", company=COMPANY)
    with pytest.raises(BookflowError, match="E_QUERY_STALE"):
        client.customer.query(limit=1, cursor=first["next_cursor"], company=COMPANY)


def test_cursor_is_company_scoped_but_presence_does_not_invalidate(client):
    from bookflow import BookflowError
    first = client.customer.query(limit=1, company=COMPANY)
    company_id = client.company.show(company=COMPANY)["company_id"]
    client.presence.set(record_type="company_info", record_id=company_id, company=COMPANY)
    assert client.customer.query(limit=1, cursor=first["next_cursor"], company=COMPANY)["count"] == 1
    other = client.company.new(legal_name="Query scope company", home_currency="USD", timezone="America/Los_Angeles")
    with pytest.raises(BookflowError, match="E_VALIDATION"):
        client.customer.query(limit=1, cursor=first["next_cursor"], company=other["company_id"])


def test_compatibility_enumeration_is_not_silently_bounded(client):
    _bulk_customers(client, 205)
    listed = client.customer.list(query="Workload", company=COMPANY)
    assert listed["count"] == 206
    assert all("contacts" in row for row in listed["items"])
    first = client.customer.query(query="Workload", limit=200, company=COMPANY)
    assert first["count"] == 200 and first["next_cursor"]
    second = client.customer.query(query="Workload", limit=200, cursor=first["next_cursor"], company=COMPANY)
    assert second["count"] == 6 and second["next_cursor"] is None


def test_employee_filter_sort_precedes_paging_and_keeps_id_ascending(client):
    client.company.update(required_employee_profile_fields=[["first_name"], ["phone", "email"]], company=COMPANY)
    made = [client.employee.create(name=f"Paging employee {index}", first_name="Pat" if index > 2 else None,
        phone="555-1000", company=COMPANY) for index in range(6)]
    expected = sorted([row["id"] for row in made if row["profile_complete"]])
    found, cursor = [], None
    while True:
        page = client.employee.query(query="Paging employee", filter=["profile_complete=true"],
            sort="profile_complete", direction="desc", limit=1, cursor=cursor, company=COMPANY)
        found.extend(row["id"] for row in page["items"])
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert found == expected


@pytest.mark.parametrize("kind,value,complete", [("bool", False, True), ("number", "0", True),
    ("text", "\t\n\u00a0", False), ("text", "", False), ("text", "yes", True), ("text", None, False)])
def test_employee_custom_population_matches_show(client, kind, value, complete):
    definition = client.run("custom-field create", {"name": "Query requirement", "kind": kind, "scopes": ["employee"]}, company=COMPANY)
    client.company.update(required_employee_profile_fields=[[f"custom_fields.{definition['id']}"]], company=COMPANY)
    row = client.employee.create(name="Query employee", custom_fields={definition["id"]: value}, company=COMPANY)
    assert row["profile_complete"] is complete
    page = client.employee.query(query="Query employee", filter=[f"profile_complete={str(complete).lower()}"], limit=1, company=COMPANY)
    assert page["items"][0]["id"] == row["id"]
    assert page["items"][0]["profile_complete"] is complete


def test_employee_suffix_completeness_does_not_leak_to_readonly(client, root):
    from bookflow.company import schema
    from bookflow.storage.engine import open_database
    from tests.conftest import as_user, make_actor
    company = client.company.show(company=COMPANY)
    make_actor(root, "query-reader", company_role=(company["company_id"], "readonly"))
    row = client.employee.create(name="Protected completeness", company=COMPANY)
    with open_database(_db_path(client), writable=True) as db:
        db.raw.execute("BEGIN IMMEDIATE")
        db.conn.execute(schema.employees.update().where(schema.employees.c.id == row["id"]).values(tax_id_last4="1234"))
        db.raw.execute("COMMIT")
    client.company.update(required_employee_profile_fields=[["tax_id_last4"]], company=COMPANY)
    reader = as_user(root, "query-reader")
    for who, complete in ((client, True), (reader, False)):
        page = who.employee.query(query="Protected completeness", company=COMPANY)
        shown = who.employee.show(employee=row["id"], company=COMPANY)
        assert page["items"][0]["profile_complete"] is shown["profile_complete"] is complete


def test_permission_change_invalidates_cursor_without_company_write(client, root):
    from bookflow import BookflowError
    from bookflow.hub import schema
    from bookflow.storage.engine import open_database
    from tests.conftest import as_user, make_actor
    company = client.company.show(company=COMPANY)
    actor = make_actor(root, "query-role-change", company_role=(company["company_id"], "admin"))
    reader = as_user(root, "query-role-change")
    first = reader.customer.query(limit=1, company=COMPANY)
    with open_database(root / "hub.db", writable=True) as db:
        db.raw.execute("BEGIN IMMEDIATE")
        db.conn.execute(schema.memberships.update().where(schema.memberships.c.user_id == actor).values(role="readonly"))
        db.raw.execute("COMMIT")
    with pytest.raises(BookflowError, match="E_VALIDATION"):
        reader.customer.query(limit=1, cursor=first["next_cursor"], company=COMPANY)
    assert reader.customer.query(limit=1, company=COMPANY)["count"] == 1


def test_fifth_level_query_inheritance_tracks_ancestor_changes(client):
    kind = client.run("customer-type create", {"name": "Inherited Kind"}, company=COMPANY)
    row = client.customer.create(name="Query Ancestor", customer_type_id=kind["id"], contacts=[
        {"role": "primary", "display_name": "Ancestor Person", "work_phone": "555-1234"}
    ], company=COMPANY)
    ancestor = row
    for depth in range(2, 6):
        row = client.customer.create(name=f"Level {depth}", parent_id=row["id"], company=COMPANY)
    def projected():
        return client.customer.query(query="Level 5", company=COMPANY)["items"][0]
    assert projected()["customer_type"] == "Inherited Kind"
    assert projected()["primary_contact"] == "Ancestor Person"
    other = client.run("customer-type create", {"name": "Changed Kind"}, company=COMPANY)
    client.customer.update(customer=ancestor["id"], expected_version=1, customer_type_id=other["id"], company=COMPANY)
    shown = client.customer.show(customer=row["id"], company=COMPANY)
    assert projected()["customer_type"] == shown["customer_type"] == "Changed Kind"
    assert client.customer.query(query="Changed Kind", company=COMPANY)["count"] == 5


def test_set_based_customer_search_preserves_each_source_and_literal_matching(client):
    definition = client.run("custom-field create", {"name": "Search annotation", "kind": "text", "scopes": ["customer"]}, company=COMPANY)
    parent = client.customer.create(name="SetBased %_ Parent", notes="Private test memo marker", account_number="AccountRemark",
        billing_address={"line1": "BillingRemark"}, shipping_addresses=[{"label": "ShippingRemark", "city": "Salem"}],
        contacts=[{"role": "primary", "display_name": "ContactRemark", "points": [{"kind": "other_1", "custom_label": "PointRemark", "value": "555-0123"}]}],
        custom_fields={definition["id"]: "CustomRemark"}, company=COMPANY)
    client.customer.create(name="Inherited child", parent_id=parent["id"], company=COMPANY)
    client.customer.create(name="Own empty child", parent_id=parent["id"], contact_mode="own", contacts=[], address_mode="own", shipping_addresses=[], company=COMPANY)
    for query in ("%_", "BillingRemark", "ShippingRemark", "ContactRemark", "PointRemark", "CustomRemark", "memo marker", "AccountRemark", "No such marker"):
        before = client.customer.list(query=query, company=COMPANY)
        after = client.customer.query(query=query, company=COMPANY)
        assert [item["id"] for item in after["items"]] == [item["id"] for item in before["items"]], query


def test_custom_definition_search_and_multi_scope_filter_are_before_limit(client):
    for index in range(4):
        client.run("custom-field create", {"name": f"Query field {index}", "kind": "text", "scopes": ["customer"]}, company=COMPANY)
    desired = client.run("custom-field create", {"name": "Query field last", "kind": "choice",
        "scopes": ["vendor", "employee"], "choices": [{"value": "Distinguishing label"}]}, company=COMPANY)
    for payload in ({"query": "Distinguishing"}, {"filter": ["target_type=vendor", "target_type=employee"]}):
        page = client.run("custom-field query", {**payload, "limit": 1}, company=COMPANY)
        assert page["items"][0]["id"] == desired["id"]
    listed = client.run("custom-field list", {"sort": "target_type"}, company=COMPANY)
    queried = client.run("custom-field query", {"sort": "target_type"}, company=COMPANY)
    assert [row["id"] for row in queried["items"]] == [row["id"] for row in listed["items"]]


def test_customer_query_unicode_and_field_boundaries(client):
    row = client.customer.create(name="Unicode search customer", billing_address={"line1": "Straße"},
        contacts=[{"role": "primary", "display_name": "ÉLODIE", "first_name": "BoundaryAlpha", "last_name": "BoundaryBeta"}],
        company=COMPANY)
    for query in ("élodie", "e\u0301lodie", "STRASSE", "BoundaryAlpha", "BoundaryBeta"):
        assert row["id"] in {item["id"] for item in client.customer.query(query=query, company=COMPANY)["items"]}
    # Separate source fields never manufacture a phrase absent from each field.
    assert client.customer.query(query="BoundaryAlpha BoundaryBeta", company=COMPANY)["count"] == 0
    assert client.customer.show(customer=row["id"], company=COMPANY)["contacts"][0]["display_name"] == "ÉLODIE"


def test_query_normalizes_before_casefolding_combining_marks():
    from bookflow.company.list_service import normalize_lookup_key
    from bookflow.company.query_providers import contains_any
    decomposed = "α\u0345\u0301"
    assert contains_any(normalize_lookup_key(decomposed), decomposed) == 1


def test_noncustomer_query_unicode_scalar_and_owned_fields(client):
    vendor = client.vendor.create(name="Unicode vendor", notes="Straße",
        contacts=[{"role": "primary", "display_name": "ÉLODIE", "first_name": "BoundaryAlpha", "last_name": "BoundaryBeta"}],
        company=COMPANY)
    for query in ("STRASSE", "élodie"):
        assert vendor["id"] in {item["id"] for item in client.vendor.query(query=query, company=COMPANY)["items"]}
    assert client.vendor.query(query="BoundaryAlpha BoundaryBeta", company=COMPANY)["count"] == 0
    definition = client.run("custom-field create", {"name": "Unicode choices", "kind": "choice", "scopes": ["customer"],
        "choices": [{"value": "Straße"}, {"value": "SeparateAlpha"}, {"value": "SeparateBeta"}]}, company=COMPANY)
    assert client.run("custom-field query", {"query": "STRASSE"}, company=COMPANY)["items"][0]["id"] == definition["id"]
    assert client.run("custom-field query", {"query": "SeparateAlpha SeparateBeta"}, company=COMPANY)["count"] == 0


def test_ten_thousand_customer_query_work_is_bounded(client, root):
    import statistics
    import time
    from fastapi.testclient import TestClient
    from bookflow.commands.host_cmds import start_serving
    from bookflow.core.context import client_version
    _bulk_customers(client, 10_000)
    company_id = client.company.show(company=COMPANY)["company_id"]
    secret = client.token.issue(label="query-budget")["secret"]
    handle = start_serving(root, client_version(), bind="127.0.0.1:8765", secure_cookies=False)
    api = TestClient(handle.app)
    statements = []
    def capture(connection, cursor, statement, parameters, context, many):
        statements.append(statement)
    def run(payload):
        response = api.post(f"/companies/{company_id}/commands/customer.query", json=payload,
            headers={"Authorization": f"Bearer {secret}"})
        assert response.status_code == 200, response.text
        return response.json()
    try:
        run({"limit": 10})
        sa.event.listen(sa.engine.Engine, "before_cursor_execute", capture)
        small = run({"limit": 10})
        small_count = len(statements)
        statements.clear()
        large = run({"limit": 200})
        assert len(statements) == small_count
        assert small["count"] == 10 and large["count"] == 200
        sa.event.remove(sa.engine.Engine, "before_cursor_execute", capture)
        measurements = {}
        for name, payload in (("summary", {}), ("reference", {"projection": "reference"}),
                              ("broad_search", {"query": "Workload"}), ("contact_search", {"query": "Contact"}),
                              ("miss_search", {"query": "NoSearchMatch"}), ("late_match", {"query": "Workload 09999"})):
            run(payload)
            elapsed = []
            for _ in range(3):
                started = time.perf_counter()
                page = run(payload)
                elapsed.append(time.perf_counter() - started)
                assert page["count"] <= 50
            measurements[name] = round(statistics.median(elapsed) * 1000, 2)
        print(f"10k customer query milliseconds: {measurements}; statements per page: {small_count}")
        assert all(milliseconds < 100 for milliseconds in measurements.values()), measurements
    finally:
        if sa.event.contains(sa.engine.Engine, "before_cursor_execute", capture):
            sa.event.remove(sa.engine.Engine, "before_cursor_execute", capture)
        handle.stop()


@pytest.mark.parametrize("noun", LIST_DEFINITIONS)
def test_all_query_projections_match_show_and_complete_enumeration(client, noun):
    full_list = client.run(f"{noun} list", {"include_inactive": True}, company=COMPANY)
    expected = [item["id"] for item in full_list["items"]]
    for projection in ("summary", "reference"):
        collected = []
        cursor = None
        while True:
            out = client.run(f"{noun} query", {"include_inactive": True, "limit": 2, "projection": projection, "cursor": cursor}, company=COMPANY)
            assert out["count"] == len(out["items"]) <= 2
            for row in out["items"]:
                if projection == "reference":
                    assert set(row) == {"id", "version", "label", "active"}
                else:
                    selector = registry.get(f"{noun} show").positional[0]
                    shown = client.run(f"{noun} show", {selector: row["id"]}, company=COMPANY)
                    for name, value in row.items():
                        if name != "label":
                            assert value == shown[name], (noun, name, value, shown[name])
                collected.append(row["id"])
            cursor = out["next_cursor"]
            if cursor is None:
                break
        assert collected == expected
    empty = client.run(f"{noun} query", {"query": "no-such-record-9a3972"}, company=COMPANY)
    assert empty == {"projection": "summary", "items": [], "count": 0, "next_cursor": None}


@pytest.mark.parametrize("noun", LIST_DEFINITIONS)
def test_all_query_declared_sort_and_search_matches_legacy_selection(client, noun):
    definition = LIST_DEFINITIONS[noun]
    for sort in definition.sorts:
        if sort == "profile_complete":  # explicit ascending-id correction has its own witness
            continue
        payload = {"sort": sort, "direction": "desc", "include_inactive": True}
        old = client.run(f"{noun} list", payload, company=COMPANY)
        page = client.run(f"{noun} query", {**payload, "limit": 200}, company=COMPANY)
        assert [row["id"] for row in page["items"]] == [row["id"] for row in old["items"]], (noun, sort)
        if old["items"]:
            text = old["items"][0][definition.display_field]
            old_search = client.run(f"{noun} list", {"query": text, "include_inactive": True}, company=COMPANY)
            new_search = client.run(f"{noun} query", {"query": text, "include_inactive": True, "limit": 200}, company=COMPANY)
            assert [row["id"] for row in new_search["items"]] == [row["id"] for row in old_search["items"]], (noun, text)


def test_customer_query_is_bounded_and_matches_show(client):
    client.use_company("Demo Plumbing Co")
    page = client.customer.query(limit=1)
    assert page["count"] == 1
    assert page["next_cursor"]
    row = page["items"][0]
    full = client.customer.show(customer=row["id"])
    assert {key: value for key, value in row.items() if key != "label"} == {
        key: full[key] for key in row if key != "label"
    }
    assert "contacts" not in row
    second = client.customer.query(limit=1, cursor=page["next_cursor"])
    assert second["items"][0]["id"] != row["id"]
    refs = client.customer.query(projection="reference", limit=1)
    assert set(refs["items"][0]) == {"id", "version", "label", "active"}


def test_query_models_are_explicit():
    registry.load_all()
    from bookflow.company.lists import all_list_definitions
    for definition in all_list_definitions():
        command = registry.get(definition.query_command)
        assert command is not None
        assert command.kind == "read"
        assert command.capability == registry.get(f"{definition.noun} show").capability
        assert command.input_model.model_fields["limit"].default == 50
    from bookflow.documentation.introspection import model_fields, sample_model
    command = registry.get("customer query")
    fields = {field.path: field for field in model_fields(command.output_model)}
    assert fields["items[].id"].required
    assert not fields["items[].phone"].required
    assert "CustomerSummary" in fields["items[].phone"].description
    assert sample_model(command.output_model)["count"] == 0


@pytest.mark.parametrize("limit", [True, "1", 1.0, 0, 201, -1])
def test_query_rejects_non_strict_or_unbounded_limit(client, limit):
    from bookflow.core.errors import BookflowError
    with pytest.raises(BookflowError, match="E_VALIDATION"):
        client.customer.query(limit=limit)
