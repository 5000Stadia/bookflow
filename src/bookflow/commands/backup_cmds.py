"""`company backup` and `company restore`: the portable, verified copy of one company (blueprint 3.4)."""

from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from bookflow.commands.common import Empty
from bookflow.core.context import Context
from bookflow.core.errors import BookflowError
from bookflow.core.lazy import lazy
from bookflow.core.models import WriteOutput
from bookflow.core.registry import Applied, Plan, Touched, command
from bookflow.core.session import Session

sa = lazy("sqlalchemy")
archive = lazy("bookflow.company.backup_archive")
migrate = lazy("bookflow.storage.migrate")
engine = lazy("bookflow.storage.engine")
co = lazy("bookflow.hub.companies")
info = lazy("bookflow.company.info")


class BackupFile(BaseModel):
    name: str = Field(description="Member path inside the archive: company.db or attachments/<xx>/<sha256>")
    sha256: str = Field(description="SHA-256 of the member's bytes")
    size_bytes: int


class CompanyBackupOutput(WriteOutput):
    backup_id: str = Field(description="Stable id of this backup, recorded in its manifest and the company audit trail")
    company_id: str
    display_name: str
    file_name: str = Field(description="Archive file name, in the company folder's backups/ on the machine running Bookflow")
    path: str | None = Field(None, description="Full path of the archive on that machine; shown to installation administrators only")
    size_bytes: int | None = Field(None, description="Archive size; null on a dry run")
    sha256: str | None = Field(None, description="SHA-256 of the whole archive file; null on a dry run")
    schema_revision: str = Field(description="Company schema revision the backup holds")
    created_at: str | None = Field(None, description="When the backup was taken (UTC); null on a dry run")
    attachment_count: int = Field(description="Attachment files the backup holds")
    files: list[BackupFile] = Field(default_factory=list, description="Every member with its fingerprint, as the manifest lists them; empty on a dry run")
    missing_attachments: list[str] = Field(default_factory=list, description="SHA-256 of referenced attachment files that were absent or damaged on disk and are not in the backup")


company_backup = command(
    "company backup", scope="company",
    description=("Write a verified, portable backup of the selected company: one .bookflow-backup archive holding its database "
                 "(a consistent copy), its attachment files and a manifest of fingerprints, saved in the company folder's backups/. "
                 "The archive is reopened and checked before success is reported. Copy it anywhere; `company restore` opens it."),
    input_model=Empty, output_model=CompanyBackupOutput, writes={"company"}, required_role="admin", capability="company",
    error_codes=["E_IO", "E_BACKUP_INVALID"])


@company_backup
def plan_company_backup(inp: Empty, ctx: Context, s: Session) -> Plan:
    from bookflow.company import schema as c
    from bookflow.core.ids import new_id
    from bookflow.storage.migrate import current_revision_open
    row = s.company_row
    folder = s.company.path.parent
    count = s.company.conn.execute(sa.select(sa.func.count(sa.distinct(c.attachments.c.sha256))).where(
        c.attachments.c.collected_at.is_(None))).scalar_one()
    name = archive.archive_name(row["display_name"], datetime.now(timezone.utc))
    backup_id = new_id()
    preview = CompanyBackupOutput(backup_id=backup_id, company_id=row["id"], display_name=row["display_name"], file_name=name,
                                  path=str(folder / "backups" / name), schema_revision=current_revision_open(s.company),
                                  attachment_count=count)
    return Plan(preview=preview, data={"backup_id": backup_id})


@company_backup.applier
def apply_company_backup(plan: Plan, ctx: Context, s: Session) -> Applied:
    from bookflow.core.context import client_version
    row = s.company_row
    result = archive.create(s.company.path.parent, company_id=row["id"], display_name=row["display_name"],
                            created_by={"user_id": s.actor.id, "username": s.actor.username, "display_name": s.actor.display_name},
                            bookflow_version=client_version(), backup_id=plan.data["backup_id"])
    m = result["manifest"]
    warnings = []
    if m["missing_attachments"]:
        warnings.append(f"{len(m['missing_attachments'])} attachment file(s) the company refers to were missing or damaged on disk "
                        "and are not in the backup; run `company verify` on the company")
    out = CompanyBackupOutput(backup_id=m["backup_id"], company_id=row["id"], display_name=row["display_name"],
                              file_name=result["file_name"], path=str(result["path"]), size_bytes=result["size_bytes"],
                              sha256=result["sha256"], schema_revision=m["schema_revision"], created_at=m["created_at"],
                              attachment_count=len(m["files"]) - 1, files=[BackupFile(**f) for f in m["files"]],
                              missing_attachments=m["missing_attachments"], warnings=warnings)
    snapshot = {"file_name": out.file_name, "sha256": out.sha256, "size_bytes": out.size_bytes, "schema_revision": out.schema_revision,
                "attachment_count": out.attachment_count, "created_at": out.created_at}
    return Applied(out, [Touched("company_backup", m["backup_id"], "create", None, None, snapshot, db="company")],
                   f"backed up company {row['display_name']} to {out.file_name}")


# ---------------------------------------------------------------- restore

class RestoreInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    archive: str = Field(min_length=1, max_length=4096, json_schema_extra={"x-bookflow-local-path": True},
                         description="Full path of the .bookflow-backup file on the machine running Bookflow (the command line accepts a relative path)")
    organization: str | None = Field(None, description="Organization id or name to restore into; defaults when exactly one is visible")
    name: str | None = Field(None, max_length=200, description="Display name for the restored company; defaults to the name in the backup, with \" (restored)\" added for a copy")
    as_copy: bool = Field(False, description="Give the restored company a new id, so it can sit beside the company it was backed up from; required when that company is registered here")


class CompanyRestoreOutput(WriteOutput):
    company_id: str
    source_company_id: str = Field(description="Company id recorded in the backup; differs from company_id for a copy")
    organization_id: str
    display_name: str
    path: str | None = Field(None, description="The restored company's folder; shown to installation administrators only")
    as_copy: bool
    backup_id: str | None = Field(None, description="The backup's id, from its manifest")
    backup_created_at: str
    backup_created_by: str | None = Field(None, description="Display name of whoever took the backup, from its manifest")
    backup_schema_revision: str = Field(description="Company schema revision the backup holds")
    schema_revision: str = Field(description="Schema revision the restored company is at; newer than the backup's when it was migrated")
    migrated: bool = Field(description="True when the backup was from an older Bookflow and its database was migrated on restore")
    attachment_count: int


company_restore = command(
    "company restore", scope="hub",
    description=("Restore a .bookflow-backup archive as a new company in an organization, verified before it is registered, "
                 "with the restoring user as its owner. Restore never replaces a company: when the backed-up company is "
                 "registered here, it refuses unless --as-copy gives the restored company a new id. A backup from an older "
                 "Bookflow is migrated on restore; one from a newer Bookflow is refused."),
    input_model=RestoreInput, output_model=CompanyRestoreOutput, writes={"hub", "company"}, required_role="hub_admin",
    capability="company", positional=["archive"],
    error_codes=["E_BACKUP_INVALID", "E_SCHEMA_UNKNOWN", "E_ALREADY_ATTACHED", "E_NAME_TAKEN", "E_ORGANIZATION_REQUIRED",
                 "E_ORGANIZATION_NOT_FOUND", "E_VALIDATION", "E_MIGRATION_FAILED", "E_IO"])


def _read(inp: RestoreInput, s: Session) -> tuple[Path, dict]:
    path = Path(inp.archive).expanduser()
    if not path.is_absolute():
        raise BookflowError("E_VALIDATION", message="Give the backup file's full path.",
                            details={"fields": [{"field": "archive", "problem": "must be an absolute path on the machine running Bookflow"}]})
    manifest = archive.verify(path) if s.dry_run else archive.read_manifest(path)
    revision = manifest["schema_revision"]
    if migrate.classify("company", revision) == "unknown":
        raise BookflowError("E_SCHEMA_UNKNOWN", details={"revision": revision},
                            message=f"This backup was made by a newer version of Bookflow (company schema {revision}); upgrade Bookflow, then restore it.")
    return path, manifest


@company_restore
def plan_company_restore(inp: RestoreInput, ctx: Context, s: Session) -> Plan:
    from bookflow.commands.hub_cmds import _resolve_org_for_new
    from bookflow.core.ids import new_id
    from bookflow.storage.paths import choose_folder_name, name_key, normalize_display_name
    path, manifest = _read(inp, s)
    orow = _resolve_org_for_new(s, inp.organization)
    existing = co.get(s, manifest["company_id"])
    if existing is not None and not inp.as_copy:
        raise BookflowError("E_ALREADY_ATTACHED", details={"company_id": manifest["company_id"], "display_name": existing["display_name"]},
                            message=(f"The company in this backup is registered here as {existing['display_name']}; restore never replaces a company. "
                                     "Add --as-copy to restore it beside that one as a separate company with a new id."))
    if inp.name is not None and "/" in inp.name:
        raise BookflowError("E_VALIDATION", details={"fields": [{"field": "name", "problem": "must not contain '/'"}]})
    default = manifest["display_name"] + " (restored)" if inp.as_copy else manifest["display_name"]
    display = normalize_display_name(inp.name or default, field="name")
    if co.name_taken(s, orow["id"], name_key(display)):
        raise BookflowError("E_NAME_TAKEN", details={"name": display}, message=f"{display} is already a company in {orow['display_name']}; give --name.")
    cid = new_id() if inp.as_copy else manifest["company_id"]
    org_folder = s.abs_path(orow["path"])
    by = manifest.get("created_by") if isinstance(manifest.get("created_by"), dict) else {}
    preview = CompanyRestoreOutput(
        company_id=cid, source_company_id=manifest["company_id"], organization_id=orow["id"], display_name=display,
        path=str(org_folder / choose_folder_name(org_folder, display)), as_copy=inp.as_copy, backup_id=manifest.get("backup_id"),
        backup_created_at=manifest["created_at"], backup_created_by=by.get("display_name"),
        backup_schema_revision=manifest["schema_revision"], schema_revision=migrate.HEADS["company"],
        migrated=manifest["schema_revision"] != migrate.HEADS["company"], attachment_count=len(manifest["files"]) - 1)
    return Plan(preview=preview, data={"path": path, "manifest": manifest, "org": orow, "display": display, "company_id": cid})


@company_restore.applier
def apply_company_restore(plan: Plan, ctx: Context, s: Session) -> Applied:
    from bookflow.core.audit import write_event_to
    from bookflow.core.durability import sync_directory
    from bookflow.storage.paths import reserve_folder, write_company_marker
    path, planned, orow = plan.data["path"], plan.data["manifest"], plan.data["org"]
    display, cid = plan.data["display"], plan.data["company_id"]
    org_folder = s.abs_path(orow["path"])
    with s.commits.operation("hub.company_restore", s.hub, s.company):
        folder = reserve_folder(org_folder, display)
        try:
            write_company_marker(folder, company_id=cid, state="creating", display_name=display)
            for sub in ("attachments", "backups", "exports"):
                (folder / sub).mkdir(mode=0o700, exist_ok=True)
            manifest = archive.verify(path, dest=folder)
            if (manifest["company_id"], manifest["schema_revision"], manifest.get("backup_id")) != (
                    planned["company_id"], planned["schema_revision"], planned.get("backup_id")):
                raise archive.invalid("changed", "The backup file changed while it was being restored.")
            with engine.open_database(folder / "company.db", writable=True) as db:
                if cid != manifest["company_id"]:
                    db.raw.execute("BEGIN IMMEDIATE")
                    db.raw.execute("UPDATE company_info SET id = ?", (cid,))
                    s.commits.commit(db, "hub.company_restore")
                before, after = migrate.migrate_company(s, ctx, db, folder, None)
                facts = db.raw.execute("SELECT legal_name, home_currency FROM company_info").fetchone()
                db.raw.execute("BEGIN IMMEDIATE")
                info.write_display_name_copy(db, display)
                info.upsert_principal(db, user_id=s.actor.id, username=s.actor.username, display_name=s.actor.display_name, kind=s.actor.kind)
                snapshot = {"backup_id": manifest.get("backup_id"), "file_name": path.name, "source_company_id": manifest["company_id"],
                            "company_id": cid, "as_copy": cid != manifest["company_id"], "backup_created_at": manifest["created_at"],
                            "backup_schema_revision": manifest["schema_revision"], "schema_revision": after}
                write_event_to(db, ctx, "company restore", f"restored company {display} from backup {path.name}",
                               [Touched("company_backup", manifest.get("backup_id") or cid, "restore", None, None, snapshot, db="company")],
                               actor_id=s.actor.id, actor_kind=s.actor.kind)
                s.commits.commit(db, "hub.company_restore")
            for shard in (folder / "attachments").iterdir():
                sync_directory(shard)
            for sub in ("attachments", "backups", "exports"):
                sync_directory(folder / sub)
            sync_directory(folder)
            sync_directory(org_folder)
            write_company_marker(folder, company_id=cid, state="ready", display_name=display, schema_revision=after)
            row, touched = co.register(s, company_id=cid, organization_id=orow["id"], display_name=display, rel_path=s.rel_path(folder),
                                       legal_name=facts[0], home_currency=facts[1], schema_revision=after, via=ctx.interface.value)
        except BaseException:
            # Nothing is registered yet (the hub transaction has not committed): the folder
            # this restore made is referred to by nothing, and goes.
            shutil.rmtree(folder, ignore_errors=True)
            raise
    by = manifest.get("created_by") if isinstance(manifest.get("created_by"), dict) else {}
    out = CompanyRestoreOutput(
        company_id=cid, source_company_id=manifest["company_id"], organization_id=orow["id"], display_name=display, path=str(folder),
        as_copy=cid != manifest["company_id"], backup_id=manifest.get("backup_id"), backup_created_at=manifest["created_at"],
        backup_created_by=by.get("display_name"), backup_schema_revision=manifest["schema_revision"], schema_revision=after,
        migrated=before != after, attachment_count=len(manifest["files"]) - 1)
    return Applied(out, touched, f"restored company {display} in {orow['display_name']} from backup {path.name}")
