"""Packaged chart/profile commands are routed, atomic, and idempotent."""

from pathlib import Path

import pytest

from bookflow import BookflowError
from bookflow.company import charts
from bookflow.storage.engine import open_database


def test_packaged_reads_are_available_without_company_context(client):
    chart_list = client.chart.list()
    assert chart_list["count"] == 6
    assert chart_list["items"][0]["template_id"] == "general"
    chart = client.chart.show(template_id="service")
    assert chart["template_id"] == "service"
    assert len(chart["ordered_account_tree"]) == len(charts.get_manifest("service").accounts)

    profile_list = client.profile.list()
    assert profile_list == {
        "items": [
            {
                "profile_id": "standard",
                "version": 1,
                "lists": [
                    "term",
                    "payment-method",
                    "sales-tax-code",
                    "ship-method",
                    "customer-message",
                ],
                "record_count": 27,
            }
        ],
        "count": 1,
    }
    profile = client.profile.show(profile_id="standard")
    assert profile["profile_id"] == "standard" and len(profile["records"]["term"]) == 6

    for command, selector in (
        (client.chart.show, {"template_id": "missing"}),
        (client.profile.show, {"profile_id": "missing"}),
    ):
        with pytest.raises(BookflowError) as exc:
            command(**selector)
        assert exc.value.code == "E_RECORD_NOT_FOUND"


def test_chart_apply_previews_then_commits_one_audited_idempotent_set(client):
    made = client.company.new(
        legal_name="Chart Command LLC",
        home_currency="USD",
        organization="Demo Holdings LLC",
        timezone="UTC",
        chart="none",
    )
    company = made["company_id"]
    preview = client.chart.apply(template_id="retail", company=company, dry_run=True)
    assert preview["dry_run"] is True
    assert preview["created_account_count"] == len(charts.get_manifest("retail").accounts)

    applied = client.chart.apply(
        template_id="retail",
        company=company,
        idempotency_key="retail-chart",
    )
    replay = client.chart.apply(
        template_id="retail",
        company=company,
        idempotency_key="retail-chart",
    )
    assert replay == {**applied, "idempotent_replay": True}
    assert len(applied["prospective_or_created_ids"]) == len(preview["prospective_or_created_ids"])

    event = client.audit.list(company=company, command="chart apply")["items"]
    assert len(event) == 1
    shown = client.audit.show(event=event[0]["id"], company=company)
    assert shown["entry_count"] == applied["created_account_count"] + 1

    with pytest.raises(BookflowError) as exists:
        client.chart.apply(template_id="general", company=company)
    assert exists.value.code == "E_CHART_EXISTS"


def test_profile_apply_restores_only_absent_seed_keys_and_preserves_existing_rows(client):
    made = client.company.new(
        legal_name="Profile Command LLC",
        home_currency="USD",
        organization="Demo Holdings LLC",
        timezone="UTC",
        chart="none",
    )
    company = made["company_id"]
    path = Path(made["path"]) / "company.db"
    with open_database(path, writable=True) as db:
        db.raw.execute("BEGIN IMMEDIATE")
        db.raw.execute("DELETE FROM terms WHERE seed_key = 'term.net-60'")
        db.raw.execute("COMMIT")

    applied = client.profile.apply(
        profile_id="standard",
        company=company,
        idempotency_key="standard-profile-repair",
    )
    assert applied["inserted_by_list"]["term"] == 1
    assert sum(applied["inserted_by_list"].values()) == 1
    assert len(applied["prospective_or_created_ids"]) == 1
    assert applied["preserved_by_list"]["term"] == 5

    replay = client.profile.apply(
        profile_id="standard",
        company=company,
        idempotency_key="standard-profile-repair",
    )
    assert replay == {**applied, "idempotent_replay": True}
    event = client.audit.list(company=company, command="profile apply")["items"]
    assert len(event) == 2  # rollout plus the one-record repair
    assert sorted(item["entry_count"] for item in event) == [1, 27]
