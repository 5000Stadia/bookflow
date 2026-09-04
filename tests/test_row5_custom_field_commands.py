"""Custom-field definitions expose their full routed lifecycle."""

from pathlib import Path

import pytest
import sqlalchemy as sa

from bookflow import BookflowError
from bookflow.company import schema
from bookflow.storage.engine import open_database


def _database(client) -> Path:
    return Path(client.company.show(company="Demo Plumbing Co")["path"]) / "company.db"


def test_custom_field_lifecycle_is_preview_safe_aggregate_audited_and_idempotent(client):
    payload = {
        "id": "01ARZ3NDEKTSV4RRFFQ69G5FAD",
        "name": "Service zone",
        "kind": "choice",
        "scopes": ["customer", "vendor"],
        "choices": [
            {"id": "01ARZ3NDEKTSV4RRFFQ69G5FAB", "value": "North"},
            {"id": "01ARZ3NDEKTSV4RRFFQ69G5FAC", "value": "South"},
        ],
        "required": False,
        "default": "North",
    }
    preview = client.run(
        "custom-field create",
        payload,
        company="Demo Plumbing Co",
        dry_run=True,
    )
    with open_database(_database(client), writable=False) as db:
        assert db.conn.execute(sa.select(sa.func.count()).select_from(schema.custom_field_defs)).scalar_one() == 0

    created = client.run(
        "custom-field create",
        payload,
        company="Demo Plumbing Co",
        idempotency_key="service-zone-field",
    )
    replay = client.run(
        "custom-field create",
        payload,
        company="Demo Plumbing Co",
        idempotency_key="service-zone-field",
    )
    assert replay == {**created, "idempotent_replay": True}
    assert created["id"] == preview["id"]
    assert created["target_types"] == ["customer", "vendor"]
    assert [choice["value"] for choice in created["choices"]] == ["North", "South"]

    shown = client.run(
        "custom-field show",
        {"custom_field": "Service zone"},
        company="Demo Plumbing Co",
    )
    assert shown["id"] == created["id"] and shown["default"] == "North"
    listed = client.run(
        "custom-field list",
        {"query": "south", "filter": ["target_type=vendor", "kind=choice"]},
        company="Demo Plumbing Co",
    )
    assert [item["id"] for item in listed["items"]] == [created["id"]]

    updated = client.run(
        "custom-field update",
        {
            "custom_field": created["id"],
            "expected_version": 1,
            "name": "Dispatch zone",
            "position": 4,
        },
        company="Demo Plumbing Co",
    )
    assert updated["version"] == 2
    assert updated["changed_fields"] == ["name", "position"]

    deactivated = client.run(
        "custom-field deactivate",
        {"custom_field": created["id"], "expected_version": 2},
        company="Demo Plumbing Co",
    )
    assert deactivated["changed"] and not deactivated["active"]
    assert client.run(
        "custom-field list",
        {"query": "Dispatch zone"},
        company="Demo Plumbing Co",
    )["items"] == []
    assert client.run(
        "custom-field list",
        {"query": "Dispatch zone", "include_inactive": True},
        company="Demo Plumbing Co",
    )["count"] == 1

    activated = client.run(
        "custom-field activate",
        {"custom_field": created["id"], "expected_version": 3},
        company="Demo Plumbing Co",
    )
    assert activated["changed"] and activated["active"] and activated["version"] == 4

    events = client.audit.list(
        company="Demo Plumbing Co",
        record_type="custom_field",
        record_id=created["id"],
    )["items"]
    assert [event["command"] for event in events] == [
        "custom-field activate",
        "custom-field deactivate",
        "custom-field update",
        "custom-field create",
    ]


def test_custom_field_definition_writes_require_admin_and_version_match(client, root):
    from tests.conftest import as_user, make_actor

    company_id = client.company.list()["items"][0]["company_id"]
    make_actor(root, "standard-fields", company_role=(company_id, "standard"))
    with pytest.raises(BookflowError) as denied:
        as_user(root, "standard-fields").run(
            "custom-field create",
            {"name": "Denied", "kind": "text", "scopes": ["customer"]},
            company=company_id,
        )
    assert denied.value.code == "E_PERMISSION"

    created = client.run(
        "custom-field create",
        {"name": "Versioned", "kind": "text", "scopes": ["customer"]},
        company=company_id,
    )
    client.run(
        "custom-field update",
        {"custom_field": created["id"], "expected_version": 1, "name": "Versioned once"},
        company=company_id,
    )
    with pytest.raises(BookflowError) as stale:
        client.run(
            "custom-field update",
            {"custom_field": created["id"], "expected_version": 1, "name": "Stale"},
            company=company_id,
        )
    assert stale.value.code == "E_VERSION_CONFLICT"
