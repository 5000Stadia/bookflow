"""Staged company creation (plan section Rollout)."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from bookflow.company import charts, profiles, schema as c
from bookflow.company.info import logical_info_values, upsert_principal
from bookflow.core.context import Context
from bookflow.core.durability import sync_directory
from bookflow.core.errors import BookflowError
from bookflow.core.registry import Touched
from bookflow.core.session import Session
from bookflow.hub.users import common
from bookflow.storage.engine import open_database
from bookflow.storage.migrate import HEADS, migrate_to_head
from bookflow.storage.paths import read_company_marker, reserve_folder, write_company_marker


def create_company_folder(
    s: Session,
    org_folder: Path,
    company_id: str,
    display_name: str,
    info: dict[str, Any],
    via: str,
    ctx: Context,
    *,
    chart: str,
) -> Path:
    """Stages 2-4. Returns the folder. Removes it on failure in 2 or 3."""
    folder = reserve_folder(org_folder, display_name)
    try:
        write_company_marker(folder, company_id=company_id, state="creating", display_name=display_name)
        for sub in ("attachments", "backups", "exports"):
            (folder / sub).mkdir(mode=0o700)
        with open_database(folder / "company.db", writable=True, create=True) as db:
            migrate_to_head(db, "company", None)
            row = {"id": company_id, **common(s.actor.id, via), **info, "display_name": display_name}
            from bookflow.core.audit import write_event_to
            db.raw.execute("BEGIN IMMEDIATE")
            try:
                db.conn.execute(c.company_info.insert().values(**row))
                upsert_principal(
                    db,
                    user_id=s.actor.id,
                    username=s.actor.username,
                    display_name=s.actor.display_name,
                    kind=s.actor.kind,
                )
                snapshot = logical_info_values({k: v for k, v in row.items() if k != "display_name"})
                write_event_to(
                    db,
                    ctx,
                    "company new",
                    f"created company {display_name}",
                    [Touched("company_info", company_id, "create", None, 1, snapshot, db="company")],
                    actor_id=s.actor.id,
                    actor_kind=s.actor.kind,
                )

                if chart != "none":
                    chart_plan = charts.plan_chart_application(
                        db,
                        chart,
                        actor_id=s.actor.id,
                        via=via,
                    )
                    company_after, accounts = charts.apply_chart_application(db, chart_plan)
                    company_before_snapshot = logical_info_values(chart_plan.company_before)
                    company_after_snapshot = logical_info_values(company_after)
                    company_before_snapshot.pop("display_name", None)
                    company_after_snapshot.pop("display_name", None)
                    chart_touched = [
                        Touched(
                            "company_info",
                            company_id,
                            "update",
                            chart_plan.company_before["version"],
                            company_after["version"],
                            company_after_snapshot,
                            company_before_snapshot,
                            db="company",
                        ),
                        *[
                            Touched("account", account["id"], "create", None, 1, account, db="company")
                            for account in accounts
                        ],
                    ]
                    write_event_to(
                        db,
                        ctx,
                        "chart apply",
                        f"applied chart {chart}",
                        chart_touched,
                        actor_id=s.actor.id,
                        actor_kind=s.actor.kind,
                    )

                manifest, profile_plan, _preserved = profiles.plan_standard_profile(
                    db,
                    actor_id=s.actor.id,
                    via=via,
                )
                for mutation in profile_plan:
                    profiles.persist_profile_mutation(db, mutation)
                write_event_to(
                    db,
                    ctx,
                    "profile apply",
                    "applied profile standard",
                    [
                        Touched(
                            mutation.noun.replace("-", "_"),
                            str(mutation.after["id"]),
                            "create",
                            None,
                            1,
                            dict(mutation.after),
                            db="company",
                        )
                        for mutation in profile_plan
                    ],
                    actor_id=s.actor.id,
                    actor_kind=s.actor.kind,
                )
                db.raw.execute("COMMIT")
            except BaseException:
                if db.raw.in_transaction:
                    db.raw.execute("ROLLBACK")
                raise
    except BaseException:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    try:
        # Persist the complete folder before its marker can advertise readiness,
        # and its parent before a hub registration can acknowledge the folder.
        for sub in ("attachments", "backups", "exports"):
            sync_directory(folder / sub)
        sync_directory(folder)
        sync_directory(org_folder)
        write_company_marker(folder, company_id=company_id, state="ready", display_name=display_name, schema_revision=HEADS["company"])
    except OSError as e:
        # Directory synchronization can fail after replacing the ready marker.
        # Report the state a retry will actually observe rather than claiming
        # that the previous creating marker is still present.
        state = "incomplete"
        try:
            if read_company_marker(folder)["state"] == "ready":
                state = "unregistered"
        except BookflowError:
            pass
        raise BookflowError("E_ROLLOUT_INCOMPLETE", details={"state": state, "path": str(folder), "problem": str(e)})
    return folder
