"""Back up the company and restore a backup: the anchor's File menu pair, on the shared commands.

*Back up the company* runs `company backup`, which saves the verified archive in the company
folder's backups/ on the machine running Bookflow and reports its name, size and fingerprint.
It is not downloaded: on a host serving a network, a link that hands the whole company file --
every record, attachment and the audit trail -- to a browser would be the one route that moves
the complete books off the host, so the archive stays where the host's own backups are kept and
the page says where that is (the full path is shown to installation administrators).

The same page shows the company's backups -- scheduled ones in the schedule's destination and
ones taken here -- with the schedule and its last success and failure (`backup list`), and runs
`backup verify` (the live audit trails against the newest checkpoint) and `backup rehearse` (the
newest backup restored into a scratch folder, checked, and removed).

*Restore a backup* takes an uploaded .bookflow-backup file, saves it to a private temporary
folder, and runs `company restore` on it; the command is what verifies, refuses and registers.
"""
from __future__ import annotations

import os
import re
import shutil
import tempfile
from pathlib import Path

from fastapi import Request
from starlette.concurrency import run_in_threadpool

from bookflow.core.errors import BookflowError

_UNSAFE = re.compile(r"[^A-Za-z0-9 ._()-]+")


def _upload_name(filename: str | None) -> str:
    base = _UNSAFE.sub("_", Path(filename or "").name).strip(" .") or "backup"
    return base if base.endswith(".bookflow-backup") else base + ".bookflow-backup"


def mount(app, *, render, run, credential, page_error):
    def company(request: Request, company_id: str):
        return run(request, "company show", {}, company_id)

    def backup_page(request, company_id, *, result=None, error=None, status_code=200, check=None, check_kind=None):
        show = company(request, company_id)
        try:
            listing = run(request, "backup list", {}, company_id)
        except BookflowError as e:
            if e.code in ("E_UNAUTHENTICATED", "E_WORKBENCH_HEADER"):
                raise
            listing = None
        return render("company_backup.html", request, status_code=status_code, company=show, company_id=show["company_id"],
                      result=result, error=error, hub_admin=credential(request).hub_admin, listing=listing,
                      check=check, check_kind=check_kind)

    @app.get("/c/{company_id}/company/backup")
    def company_backup_page(request: Request, company_id: str):
        try:
            return backup_page(request, company_id)
        except BookflowError as e:
            return page_error(request, e)

    def backup_submit(request, company_id):
        try:
            out = run(request, "company backup", {}, company_id)
        except BookflowError as e:
            if e.code in ("E_UNAUTHENTICATED", "E_WORKBENCH_HEADER"):
                return page_error(request, e)
            return backup_page(request, company_id, error=e.to_dict(), status_code=400)
        return backup_page(request, company_id, result=out)

    @app.post("/c/{company_id}/company/backup")
    async def company_backup_submit(request: Request, company_id: str):
        return await run_in_threadpool(backup_submit, request, company_id)

    def check_submit(request, company_id, kind):
        try:
            out = run(request, f"backup {kind}", {}, company_id)
        except BookflowError as e:
            if e.code in ("E_UNAUTHENTICATED", "E_WORKBENCH_HEADER"):
                return page_error(request, e)
            return backup_page(request, company_id, error=e.to_dict(), status_code=400)
        return backup_page(request, company_id, check=out, check_kind=kind)

    @app.post("/c/{company_id}/company/backup/verify")
    async def company_backup_verify(request: Request, company_id: str):
        return await run_in_threadpool(check_submit, request, company_id, "verify")

    @app.post("/c/{company_id}/company/backup/rehearse")
    async def company_backup_rehearse(request: Request, company_id: str):
        return await run_in_threadpool(check_submit, request, company_id, "rehearse")

    def restore_page(request, company_id, *, values=None, result=None, error=None, status_code=200):
        show = company(request, company_id)
        hub_admin = credential(request).hub_admin
        organizations = run(request, "organization list", {}, None)["items"] if hub_admin else []
        return render("company_restore.html", request, status_code=status_code, company=show, company_id=show["company_id"],
                      organizations=organizations, values=values or {}, result=result, error=error, hub_admin=hub_admin)

    @app.get("/c/{company_id}/company/restore")
    def company_restore_page(request: Request, company_id: str):
        try:
            return restore_page(request, company_id)
        except BookflowError as e:
            return page_error(request, e)

    def restore_submit(request, company_id, form, upload_dir):
        values = {k: v for k, v in form.items() if k != "backup"}
        try:
            upload = form.get("backup")
            if upload is None or isinstance(upload, str) or not getattr(upload, "filename", None):
                raise BookflowError("E_VALIDATION", message="Choose the .bookflow-backup file to restore.",
                                    details={"fields": [{"field": "backup", "problem": "choose a file"}]})
            path = upload_dir / _upload_name(upload.filename)
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as out:
                upload.file.seek(0)
                shutil.copyfileobj(upload.file, out, 1 << 20)
            raw = {"archive": str(path), "as_copy": values.get("as_copy") == "yes"}
            for key in ("organization", "name"):
                if (values.get(key) or "").strip():
                    raw[key] = values[key].strip()
            out = run(request, "company restore", raw, None)
        except BookflowError as e:
            if e.code in ("E_UNAUTHENTICATED", "E_WORKBENCH_HEADER"):
                return page_error(request, e)
            return restore_page(request, company_id, values=values, error=e.to_dict(), status_code=400)
        return restore_page(request, company_id, result=out)

    @app.post("/c/{company_id}/company/restore")
    async def company_restore_submit(request: Request, company_id: str):
        form = await request.form(max_files=1, max_fields=16)
        upload_dir = Path(tempfile.mkdtemp(prefix="bookflow-restore-"))
        try:
            return await run_in_threadpool(restore_submit, request, company_id, form, upload_dir)
        finally:
            await form.close()
            shutil.rmtree(upload_dir, ignore_errors=True)
