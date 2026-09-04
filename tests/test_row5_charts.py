"""Packaged chart manifests are complete, frozen, and atomically applicable."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import sqlalchemy as sa

from bookflow import BookflowError
from bookflow.company import charts, schema
from bookflow.storage.engine import open_database


def _database(client) -> Path:
    return Path(client.company.show(company="Demo Plumbing Co")["path"]) / "company.db"


def _ids():
    current = 1
    while True:
        yield f"{current:026d}"
        current += 1


def test_packaged_catalog_matches_the_frozen_inventory():
    root = Path(__file__).parents[1]
    inventory = json.loads((root / "design" / "inventories" / "5-lists.json").read_text())["chart_manifests"]
    summaries = charts.list_manifests()
    assert [item.template_id for item in summaries] == inventory["template_ids"]
    assert charts.default_template_id() == inventory["selection_default"] == "general"
    assert charts.required_system_roles() == frozenset(inventory["required_system_roles"])

    common = inventory["common_accounts"]
    for template_id, expected_specific in inventory["template_accounts"].items():
        manifest = charts.get_manifest(template_id)
        assert manifest.version == inventory["version"]
        assert len(manifest.accounts) == len(common) + len(expected_specific)
        assert [[row.number, row.name, row.type] for row in manifest.accounts[len(common):]] == expected_specific
        roles = [row.system_role for row in manifest.accounts if row.system_role]
        assert set(roles) == charts.required_system_roles() and len(roles) == len(set(roles))
        assert len({row.number for row in manifest.accounts}) == len(manifest.accounts)
        assert len({row.name.casefold() for row in manifest.accounts}) == len(manifest.accounts)


def test_manifest_validation_rejects_duplicate_or_incomplete_roles():
    manifest = charts.get_manifest("general")
    duplicate = manifest.model_copy(update={"accounts": (*manifest.accounts, manifest.accounts[0])})
    with pytest.raises(BookflowError) as exc:
        charts.validate_manifest(duplicate, charts.required_system_roles())
    assert exc.value.code == "E_CHART_INVALID"

    missing = manifest.model_copy(update={"accounts": tuple(row for row in manifest.accounts if row.system_role != "retained_earnings")})
    with pytest.raises(BookflowError) as exc:
        charts.validate_manifest(missing, charts.required_system_roles())
    assert exc.value.code == "E_CHART_INVALID"
    assert exc.value.details["missing"] == ["retained_earnings"]


def test_chart_plan_is_write_free_and_apply_is_one_complete_set(client):
    ids = _ids()
    with open_database(_database(client), writable=True) as db:
        db.raw.execute("BEGIN IMMEDIATE")
        company = db.conn.execute(sa.select(schema.company_info)).mappings().one()
        plan = charts.plan_chart_application(
            db, "contractor", actor_id=company["created_by"], via="python",
            id_factory=lambda: next(ids), at="2026-09-04T00:00:00.000+00:00",
        )
        assert db.conn.execute(sa.select(sa.func.count()).select_from(schema.accounts)).scalar_one() == 0
        after, rows = charts.apply_chart_application(db, plan)
        db.raw.execute("COMMIT")

    assert after["default_chart"] == "contractor" and after["default_chart_version"] == 1
    assert len(rows) == len(charts.get_manifest("contractor").accounts)
    with open_database(_database(client), writable=False) as db:
        stored = db.conn.execute(sa.select(schema.accounts).order_by(schema.accounts.c.number)).mappings().all()
        info = db.conn.execute(sa.select(schema.company_info)).mappings().one()
        assert len(stored) == len(rows)
        assert {row["system_role"] for row in stored if row["system_role"]} == charts.required_system_roles()
        assert info["default_chart"] == "contractor" and info["default_chart_version"] == 1

    with open_database(_database(client), writable=True) as db:
        with pytest.raises(BookflowError) as exc:
            charts.plan_chart_application(db, "general", actor_id=after["updated_by"], via="python")
    assert exc.value.code == "E_CHART_EXISTS"


def test_existing_account_conflict_rejects_before_any_chart_write(client):
    ids = _ids()
    with open_database(_database(client), writable=True) as db:
        db.raw.execute("BEGIN IMMEDIATE")
        company = db.conn.execute(sa.select(schema.company_info)).mappings().one()
        prospective = charts.plan_chart_application(
            db, "general", actor_id=company["created_by"], via="python", id_factory=lambda: next(ids),
        )
        ordinary = dict(prospective.account_rows[0])
        ordinary_id = next(ids)
        ordinary.update(id=ordinary_id, seed_key=None, system_role=None, path=f"/{ordinary_id}/")
        db.conn.execute(schema.accounts.insert().values(**ordinary))
        before = db.conn.execute(sa.select(sa.func.count()).select_from(schema.accounts)).scalar_one()
        with pytest.raises(BookflowError) as exc:
            charts.plan_chart_application(db, "general", actor_id=company["created_by"], via="python")
        after = db.conn.execute(sa.select(sa.func.count()).select_from(schema.accounts)).scalar_one()
        db.raw.execute("ROLLBACK")
    assert exc.value.code == "E_CHART_INVALID"
    assert exc.value.details["conflicts"][0]["fields"]
    assert before == after == 1


def test_unknown_chart_uses_nonrevealing_record_not_found_shape():
    with pytest.raises(BookflowError) as exc:
        charts.get_manifest("missing")
    assert exc.value.code == "E_RECORD_NOT_FOUND"
    assert exc.value.details == {"record_type": "chart", "selector": "missing", "suggestions": []}
