"""Supporting profile nouns share the complete routed lifecycle."""

from pathlib import Path

import sqlalchemy as sa

from bookflow.company import schema
from bookflow.core import registry
from bookflow.core.session import now_iso
from bookflow.storage.engine import open_database


PAYLOADS = {
    "item-category": {"name": "Command Category"},
    "class": {"name": "Command Class"},
    "term": {"name": "Command Net 45", "kind": "standard", "due_days": 45},
    "payment-method": {"name": "Command Wallet", "kind": "other"},
    "sales-tax-code": {"code": "CMD", "description": "Command tax", "taxable": True},
    "customer-type": {"name": "Command Customer Type"},
    "vendor-type": {"name": "Command Vendor Type"},
    "job-type": {"name": "Command Job Type"},
    "sales-rep": {"name": "Command Rep", "initials": "CR", "name_type": "employee"},
    "ship-method": {"name": "Command Courier", "display_order": 40},
    "customer-message": {"name": "Command Message", "text": "Command message text", "display_order": 40},
}

UPDATES = {
    "item-category": {"name": "Edited Command Category"},
    "class": {"name": "Edited Command Class"},
    "term": {"due_days": 46},
    "payment-method": {"name": "Edited Command Wallet"},
    "sales-tax-code": {"description": "Edited command tax"},
    "customer-type": {"name": "Edited Command Customer Type"},
    "vendor-type": {"name": "Edited Command Vendor Type"},
    "job-type": {"name": "Edited Command Job Type"},
    "sales-rep": {"name": "Edited Command Rep"},
    "ship-method": {"display_order": 41},
    "customer-message": {"text": "Edited command message text"},
}


def test_generated_update_models_keep_create_field_constraints():
    create = registry.get("term create").input_model.model_json_schema()["properties"]
    update = registry.get("term update").input_model.model_json_schema()["properties"]
    assert update["name"]["anyOf"][0]["minLength"] == create["name"]["minLength"] == 1
    assert update["name"]["anyOf"][0]["maxLength"] == create["name"]["maxLength"] == 200
    assert update["due_days"]["anyOf"][0]["minimum"] == create["due_days"]["anyOf"][0]["minimum"] == 0
    assert update["due_days"]["anyOf"][0]["maximum"] == create["due_days"]["anyOf"][0]["maximum"] == 365


def _insert_employee_source(client) -> str:
    company = client.company.show(company="Demo Plumbing Co")
    path = Path(company["path"]) / "company.db"
    with open_database(path, writable=True) as db:
        info = db.conn.execute(sa.select(schema.company_info)).mappings().one()
        identifier = "01J00000000000000000000001"
        at = now_iso()
        db.raw.execute("BEGIN IMMEDIATE")
        db.conn.execute(
            schema.employees.insert().values(
                id=identifier,
                version=1,
                created_at=at,
                created_by=info["created_by"],
                created_via="system",
                updated_at=at,
                updated_by=info["created_by"],
                updated_via="system",
                active=True,
                seed_key=None,
                name="Command Employee",
                name_key="command employee",
            )
        )
        db.raw.execute("COMMIT")
    return identifier


def test_every_supporting_noun_has_the_same_routed_lifecycle(client):
    employee_id = _insert_employee_source(client)
    company = "Demo Plumbing Co"

    for noun, base_payload in PAYLOADS.items():
        payload = dict(base_payload)
        if noun == "sales-rep":
            payload["name_id"] = employee_id
        created = client.run(
            f"{noun} create",
            payload,
            company=company,
            idempotency_key=f"create-{noun}",
        )
        replay = client.run(
            f"{noun} create",
            payload,
            company=company,
            idempotency_key=f"create-{noun}",
        )
        assert replay == {**created, "idempotent_replay": True}

        selector = noun.replace("-", "_")
        shown = client.run(f"{noun} show", {selector: created["id"]}, company=company)
        assert shown["id"] == created["id"] and shown["active"] is True

        listed = client.run(
            f"{noun} list",
            {"query": created.get("full_name", created.get("name", created.get("code")))},
            company=company,
        )
        assert [item["id"] for item in listed["items"]] == [created["id"]]

        updated = client.run(
            f"{noun} update",
            {selector: created["id"], "expected_version": created["version"], **UPDATES[noun]},
            company=company,
        )
        assert updated["version"] == created["version"] + 1
        assert updated["changed_fields"]

        deactivated = client.run(
            f"{noun} deactivate",
            {selector: created["id"], "expected_version": updated["version"]},
            company=company,
        )
        assert deactivated["changed"] is True and deactivated["active"] is False
        hidden = client.run(
            f"{noun} list",
            {"query": deactivated.get("full_name", deactivated.get("name", deactivated.get("code")))},
            company=company,
        )
        assert hidden["items"] == []
        visible = client.run(
            f"{noun} list",
            {
                "query": deactivated.get("full_name", deactivated.get("name", deactivated.get("code"))),
                "include_inactive": True,
            },
            company=company,
        )
        assert [item["id"] for item in visible["items"]] == [created["id"]]

        activated = client.run(
            f"{noun} activate",
            {selector: created["id"], "expected_version": deactivated["version"]},
            company=company,
        )
        assert activated["changed"] is True and activated["active"] is True


def test_term_show_accepts_a_transaction_date_and_blind_update_warns(client):
    created = client.term.create(
        name="Command Net 12",
        kind="standard",
        due_days=12,
        company="Demo Plumbing Co",
    )
    shown = client.term.show(
        term=created["id"],
        transaction_date="2026-09-04",
        company="Demo Plumbing Co",
    )
    assert shown["calculated_due_date"] == "2026-09-16"

    updated = client.term.update(
        term=created["id"],
        due_days=13,
        company="Demo Plumbing Co",
    )
    assert updated["version"] == 2
    assert updated["warnings"] == [
        "Blind write: version 1 and fields due_days were not compared."
    ]
