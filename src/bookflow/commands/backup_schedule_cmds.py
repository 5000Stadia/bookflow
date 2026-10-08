"""Scheduled backups, their tamper evidence and a rehearsed restore (blueprint 3.4.1).

`backup schedule` sets or turns off a company's daily backup (host configuration; a person who
is an installation administrator). `backup list` shows the schedule, the last success and
failure, and the backups kept. `backup verify` compares the live audit trails with the newest
checkpoint. `backup rehearse` restores the newest backup into a scratch folder, checks it and
removes the folder; the live data root is never touched.
"""

from __future__ import annotations

import shutil
import sqlite3
import tempfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from bookflow.commands.common import Empty
from bookflow.core.context import Context
from bookflow.core.errors import BookflowError
from bookflow.core.lazy import lazy
from bookflow.core.models import WriteOutput
from bookflow.core.registry import Applied, Plan, Touched, command
from bookflow.core.session import Session

sched = lazy("bookflow.company.backup_schedule")
checkpoint = lazy("bookflow.company.backup_checkpoint")
archive = lazy("bookflow.company.backup_archive")

SCOPE_NOTE = ("Bookflow writes each backup read-only with a checkpoint beside it, which makes later changes visible; "
              "keeping the copies out of reach of anything that can write the books (another OS user, a removable "
              "drive, another machine) is set up by a person, outside Bookflow.")


# ---------------------------------------------------------------- schedule

class ScheduleInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    company: str = Field(description="Company id, Organization/Company, or display name")
    daily_at: str | None = Field(None, pattern=r"^([01][0-9]|2[0-3]):[0-5][0-9]$",
                                 description="Time of day for the backup, HH:MM in the company's timezone")
    destination: str | None = Field(None, min_length=1, max_length=4096, json_schema_extra={"x-bookflow-local-path": True},
                                    description="Existing folder on the machine running Bookflow that receives the backups (the command line accepts a relative path)")
    keep: int | None = Field(None, ge=1, le=365, description="How many scheduled backups to keep in the destination; older ones are removed, the newest never")
    off: bool = Field(False, description="Turn the schedule off; backups already written stay")


class BackupScheduleOut(BaseModel):
    daily_at: str
    timezone: str
    destination: str | None = Field(None, description="Destination folder; shown to installation administrators only")
    keep: int
    since: str | None = Field(None, description="When the schedule was last set (UTC); no backup is due for an earlier time")
    next_at: str | None = Field(None, description="When the next scheduled backup is due (UTC)")


class ScheduleOutput(WriteOutput):
    company_id: str
    display_name: str
    schedule: BackupScheduleOut | None = Field(None, description="The schedule now in force; null when off")
    note: str = Field(SCOPE_NOTE, description="What Bookflow's part of keeping backups safe is, and what is the person's")


backup_schedule = command(
    "backup schedule", scope="hub",
    description=("Set when a company is backed up by the running host (daily at a time in the company's timezone), the "
                 "folder that receives the backups and how many to keep; --off turns it off. Each scheduled backup is the "
                 "verified archive `company backup` writes, left read-only with an audit checkpoint beside it. Only a "
                 "person who is an installation administrator changes the schedule."),
    input_model=ScheduleInput, output_model=ScheduleOutput, writes={"hub", "config"}, required_role="hub_admin",
    capability="company", positional=["company"], error_codes=["E_VALIDATION", "E_COMPANY_NOT_FOUND", "E_COMPANY_AMBIGUOUS"])


def _out_schedule(entry: dict[str, Any] | None, *, hub_admin: bool, now: datetime) -> BackupScheduleOut | None:
    if entry is None:
        return None
    return BackupScheduleOut(daily_at=entry["daily_at"], timezone=entry["timezone"],
                             destination=entry["destination"] if hub_admin else None, keep=entry["keep"],
                             since=entry.get("since"), next_at=sched._iso(sched.next_occurrence(entry, now)))


def _company_timezone(s: Session, row: dict[str, Any]) -> str:
    from bookflow.storage.engine import sqlite_uri
    try:
        with closing(sqlite3.connect(sqlite_uri(s.abs_path(row["path"]) / "company.db", "ro"), uri=True)) as conn:
            return conn.execute("SELECT timezone FROM company_info").fetchone()[0] or "UTC"
    except (sqlite3.Error, TypeError):
        return "UTC"


@backup_schedule
def plan_backup_schedule(inp: ScheduleInput, ctx: Context, s: Session) -> Plan:
    from bookflow.core import clock
    from bookflow.core.dispatch import resolve_company
    if s.actor is None or s.actor.kind != "human":
        raise BookflowError("E_PERMISSION", details={"capability": "company", "required_role": "human"},
                            message="Only a person may change where and when backups are written; an agent cannot.")
    row = resolve_company(s, inp.company, "option")
    current = sched.entry(s.config.data, row["id"])
    now = clock.now()
    warnings: list[str] = []
    if inp.off:
        if any(v is not None for v in (inp.daily_at, inp.destination, inp.keep)):
            raise BookflowError("E_VALIDATION", message="--off takes no other schedule option.",
                                details={"fields": [{"field": "off", "problem": "give it alone"}]})
        new = None
    else:
        missing = [f for f, v in (("daily_at", inp.daily_at), ("destination", inp.destination), ("keep", inp.keep))
                   if v is None and current is None]
        if missing:
            raise BookflowError("E_VALIDATION", message="A new schedule needs --daily-at, --destination and --keep.",
                                details={"fields": [{"field": f, "problem": "required for a new schedule"} for f in missing]})
        new = dict(current or {})
        if inp.destination is not None:
            dest = Path(inp.destination).expanduser()
            problem = sched.destination_problem(dest, s.data_root)
            if problem:
                raise BookflowError("E_VALIDATION", message=f"The backup destination {problem}.",
                                    details={"fields": [{"field": "destination", "problem": problem}]})
            new["destination"] = str(dest)
        if inp.daily_at is not None:
            new["daily_at"] = inp.daily_at
        if inp.keep is not None:
            new["keep"] = inp.keep
        new["timezone"] = _company_timezone(s, row)
        new["since"] = sched._iso(now)
        new["set_by"] = s.actor.id
        if sched.inside(Path(new["destination"]), s.data_root):
            warnings.append("The destination is inside Bookflow's data folder: anything that can write the books can "
                            "reach these copies too. Prefer another disk, another OS user's folder or another machine.")
    out = ScheduleOutput(company_id=row["id"], display_name=row["display_name"],
                         schedule=_out_schedule(new, hub_admin=True, now=now), warnings=warnings)
    return Plan(preview=out, data={"row": row, "before": current, "after": new})


@backup_schedule.applier
def apply_backup_schedule(plan: Plan, ctx: Context, s: Session) -> Applied:
    row, before, after = plan.data["row"], plan.data["before"], plan.data["after"]
    table = s.config.data.setdefault("backups", {})
    if after is None:
        table.pop(row["id"], None)
    else:
        table[row["id"]] = {k: after[k] for k in ("daily_at", "timezone", "destination", "keep", "since", "set_by")}
    s.pending_config = True
    action = "delete" if after is None else ("create" if before is None else "update")
    touched = [Touched("backup_schedule", row["id"], action, None, None, after, before=before)]
    words = "turned off scheduled backups" if after is None else f"scheduled daily backups at {after['daily_at']}"
    return Applied(plan.preview, touched, f"{words} for company {row['display_name']}")


# ---------------------------------------------------------------- list

class BackupEntry(BaseModel):
    file_name: str
    created_at: str | None
    size_bytes: int
    location: str = Field(description="scheduled (the schedule's destination) or company (the company folder's backups/)")
    read_only: bool = Field(description="True when the file is not writable (mode 0444)")
    has_checkpoint: bool
    path: str | None = Field(None, description="Full path; shown to installation administrators only")


class BackupRun(BaseModel):
    at: str
    file_name: str | None = None
    code: str | None = None
    message: str | None = None


class BackupListOutput(BaseModel):
    company_id: str
    schedule: BackupScheduleOut | None
    last_attempt_at: str | None = None
    last_success: BackupRun | None = None
    last_failure: BackupRun | None = None
    items: list[BackupEntry]
    count: int
    note: str = SCOPE_NOTE


backup_list = command(
    "backup list", scope="company", capability="company", required_role="admin",
    description="List the selected company's backups, scheduled and taken by hand, with the schedule and its last success and failure.",
    input_model=Empty, output_model=BackupListOutput)


def _all_backups(s: Session) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    cid = s.company_row["id"]
    entry = sched.entry(s.config.data, cid)
    found: list[dict[str, Any]] = []
    seen: set[Path] = set()
    folders = []
    if entry is not None:
        folders.append(("scheduled", Path(entry["destination"])))
    folders.append(("company", s.company.path.parent / "backups"))
    for location, folder in folders:
        try:
            key = folder.resolve()
        except OSError:
            continue
        if key in seen:
            continue
        seen.add(key)
        for b in sched.backups_in(folder, cid):
            found.append({**b, "location": location})
    found.sort(key=lambda b: (b["order"], b["file_name"]), reverse=True)
    return entry, found


@backup_list
def plan_backup_list(inp: Empty, ctx: Context, s: Session) -> Plan:
    from bookflow.core import clock
    entry, found = _all_backups(s)
    hub_admin = bool(s.actor and s.actor.hub_admin)
    status = sched.read_status(s.company.path.parent)
    items = [BackupEntry(file_name=b["file_name"], created_at=b["created_at"], size_bytes=b["size_bytes"], location=b["location"],
                         read_only=b["read_only"], has_checkpoint=b["checkpoint"] is not None,
                         path=str(b["path"]) if hub_admin else None) for b in found]
    run = lambda d: BackupRun(**{k: d.get(k) for k in ("at", "file_name", "code", "message")}) if isinstance(d, dict) and d.get("at") else None
    return Plan(preview=BackupListOutput(
        company_id=s.company_row["id"], schedule=_out_schedule(entry, hub_admin=hub_admin, now=clock.now()),
        last_attempt_at=status.get("last_attempt_at"), last_success=run(status.get("last_success")),
        last_failure=run(status.get("last_failure")), items=items, count=len(items)))


# ---------------------------------------------------------------- verify

class ChainCheck(BaseModel):
    status: str = Field(description="ok; changed (an audit row at or before the checkpoint was altered or removed); "
                                    "rollback (the database ends before the checkpoint); or not_recorded")
    recorded_seq: int | None = None
    live_seq: int | None = None
    from_seq: int | None = Field(None, description="For changed: the first seq of the stretch that differs")
    to_seq: int | None = Field(None, description="For changed: the last seq of the stretch that differs")


class CheckpointLink(BaseModel):
    checkpoint: str
    previous: str | None
    state: str = Field(description="ok, pruned (the previous checkpoint was removed by keep), or broken (it was changed)")


class BackupVerifyOutput(BaseModel):
    company_id: str
    ok: bool = Field(description="True when every check passed; false also when there is no checkpoint yet")
    checkpoint: str | None = Field(None, description="File name of the newest checkpoint compared against")
    checkpoint_created_at: str | None = None
    company_audit: ChainCheck | None = None
    hub_audit: ChainCheck | None = None
    archive_intact: bool | None = Field(None, description="Whether the archive beside the checkpoint still has the recorded SHA-256; null when it is gone")
    links: list[CheckpointLink] = Field(default_factory=list)
    problems: list[str] = Field(default_factory=list, description="Each failed check, in a plain sentence")


backup_verify = command(
    "backup verify", scope="company", capability="company", required_role="admin",
    description=("Compare the live company and hub audit trails with the newest backup checkpoint: any audit row "
                 "changed or removed up to the checkpoint, or a database put back to before it, is reported; also "
                 "checks the archive's fingerprint and each checkpoint's link to the one before."),
    input_model=Empty, output_model=BackupVerifyOutput)


def _newest_checkpoint(s: Session) -> dict[str, Any] | None:
    _, found = _all_backups(s)
    with_cp = [b for b in found if b["checkpoint_data"] is not None]
    return with_cp[0] if with_cp else None


def _problem(name: str, check: dict[str, Any]) -> str | None:
    if check["status"] == "rollback":
        return (f"The {name} audit trail ends at seq {check['live_seq']}, before the checkpoint's {check['recorded_seq']}: "
                "the database was put back to an earlier state.")
    if check["status"] == "changed":
        return (f"An {name} audit row between seq {check['from_seq']} and {check['to_seq']} was changed or removed "
                "after the checkpoint was written.")
    return None


def verify_against(cp: dict[str, Any], company_raw, hub_raw) -> tuple[dict | None, dict | None, list[str]]:
    problems = []
    co = checkpoint.compare_chain(company_raw, cp["company_audit"]) if company_raw is not None else None
    hb = checkpoint.compare_chain(hub_raw, cp["hub_audit"]) if hub_raw is not None and cp.get("hub_audit") else None
    for name, check in (("company", co), ("hub", hb)):
        if check is not None and (p := _problem(name, check)):
            problems.append(p)
    return co, hb, problems


@backup_verify
def plan_backup_verify(inp: Empty, ctx: Context, s: Session) -> Plan:
    cid = s.company_row["id"]
    newest = _newest_checkpoint(s)
    if newest is None:
        return Plan(preview=BackupVerifyOutput(company_id=cid, ok=False, problems=[
            "There is no backup checkpoint yet; take a backup (`company backup` or a schedule) first."]))
    cp = newest["checkpoint_data"]
    co, hb, problems = verify_against(cp, s.company.raw, s.hub.raw if s.hub is not None else None)
    try:
        intact = checkpoint.file_sha256(newest["path"]) == cp.get("archive_sha256")
    except OSError:
        intact = None
    if intact is False:
        problems.append(f"The backup {newest['file_name']} no longer has the fingerprint its checkpoint recorded.")
    links = checkpoint.chain_links(newest["path"].parent, cid)
    for link in links:
        if link["state"] == "broken":
            problems.append(f"The checkpoint {link['previous']} was changed after {link['checkpoint']} recorded it.")
    return Plan(preview=BackupVerifyOutput(
        company_id=cid, ok=not problems, checkpoint=newest["checkpoint"].name, checkpoint_created_at=cp.get("created_at"),
        company_audit=ChainCheck(**co) if co else None,
        hub_audit=ChainCheck(**hb) if hb else ChainCheck(status="not_recorded"),
        archive_intact=intact, links=[CheckpointLink(**x) for x in links], problems=problems))


# ---------------------------------------------------------------- rehearse

class RehearsalCheck(BaseModel):
    name: str
    ok: bool
    detail: str | None = None


class BackupRehearseOutput(BaseModel):
    company_id: str
    ok: bool
    archive: str | None = Field(None, description="File name of the backup rehearsed (the newest with a checkpoint)")
    archive_created_at: str | None = None
    checks: list[RehearsalCheck] = Field(default_factory=list)
    scratch_removed: bool = Field(description="True when the scratch folder the backup was restored into is gone again")
    problems: list[str] = Field(default_factory=list)


backup_rehearse = command(
    "backup rehearse", scope="company", capability="company", required_role="admin",
    description=("Rehearse a restore: open the newest backup into a temporary scratch folder outside the data root, check "
                 "every file, the database, the trial balance against the totals the backup recorded and the audit "
                 "checkpoint, then delete the scratch folder. The live books are not touched."),
    input_model=Empty, output_model=BackupRehearseOutput)


def rehearse(path: Path, cp: dict[str, Any], hub_raw, data_root: Path) -> dict[str, Any]:
    """Restore ``path`` into a scratch root, check it against ``cp``, remove the scratch root."""
    from bookflow.storage.engine import sqlite_uri
    checks: list[dict[str, Any]] = []
    scratch = Path(tempfile.mkdtemp(prefix="bookflow-rehearsal-"))
    if sched.inside(scratch, data_root):  # pragma: no cover - only if TMPDIR points into the data root
        shutil.rmtree(scratch, ignore_errors=True)
        raise BookflowError("E_IO", message="The scratch folder for a rehearsal must be outside the data root; set TMPDIR elsewhere.")
    try:
        try:
            manifest = archive.verify(path, dest=scratch)
            checks.append({"name": "archive", "ok": True, "detail": f"{len(manifest['files'])} file(s) match the manifest"})
        except BookflowError as e:
            checks.append({"name": "archive", "ok": False, "detail": e.message})
            return {"checks": checks}
        db = scratch / archive.DATABASE
        with closing(sqlite3.connect(sqlite_uri(db, "ro"), uri=True)) as conn:
            tb = checkpoint.trial_balance(conn)
            want = cp.get("trial_balance") or {}
            same = tb == want
            checks.append({"name": "trial_balance", "ok": same, "detail": (
                f"debits {tb['debit_minor_units']} and credits {tb['credit_minor_units']} (minor units) over {tb['accounts']} account(s)"
                + ("" if same else "; the backup recorded a different trial balance"))})
            co = checkpoint.compare_chain(conn, cp["company_audit"])
            checks.append({"name": "company_audit", "ok": co["status"] == "ok" and co["live_seq"] == co["recorded_seq"],
                           "detail": f"seq {co['live_seq']} in the backup, {co['recorded_seq']} in the checkpoint: {co['status']}"})
        if hub_raw is not None and cp.get("hub_audit"):
            hb = checkpoint.compare_chain(hub_raw, cp["hub_audit"])
            checks.append({"name": "hub_audit", "ok": hb["status"] == "ok", "detail": f"live hub against the checkpoint: {hb['status']}"})
        return {"checks": checks, "manifest": manifest}
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
        checks.append({"name": "scratch_removed", "ok": not scratch.exists(), "detail": None})


@backup_rehearse
def plan_backup_rehearse(inp: Empty, ctx: Context, s: Session) -> Plan:
    cid = s.company_row["id"]
    newest = _newest_checkpoint(s)
    if newest is None:
        return Plan(preview=BackupRehearseOutput(company_id=cid, ok=False, scratch_removed=True, problems=[
            "There is no backup with a checkpoint to rehearse; take a backup (`company backup` or a schedule) first."]))
    result = rehearse(newest["path"], newest["checkpoint_data"], s.hub.raw if s.hub is not None else None, s.data_root)
    checks = result["checks"]
    removed = next((c["ok"] for c in checks if c["name"] == "scratch_removed"), False)
    checks = [c for c in checks if c["name"] != "scratch_removed"]
    problems = [f"{c['name']}: {c['detail']}" for c in checks if not c["ok"]]
    if not removed:
        problems.append("The scratch folder could not be removed.")
    return Plan(preview=BackupRehearseOutput(
        company_id=cid, ok=not problems, archive=newest["file_name"], archive_created_at=newest["created_at"],
        checks=[RehearsalCheck(**c) for c in checks], scratch_removed=removed, problems=problems))
