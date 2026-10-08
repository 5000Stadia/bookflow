"""Portable company backups: one verified archive file per backup (blueprint 3.4).

An archive is a zip file named ``<Company> <YYYY-MM-DD-HHMMSS>.bookflow-backup`` holding exactly:

- ``company.db`` -- a consistent copy of the company database taken with the SQLite backup API
  from a read-only connection while the command holds the company's write lock, with the
  write-ahead log folded in and the journal mode set to ``delete`` so the copy is one file;
- ``attachments/<first two hex>/<sha256>`` -- every attachment body the copied database still
  references (``collected_at`` is null), each checked against its name before it is written;
- ``manifest.json`` -- format, company id and names, schema revision, when and by whom, and
  the sha256 and size of every other member.

``verify`` is the single reader: it refuses anything that is not exactly such an archive before
a caller acts on it, and optionally writes the verified members into a destination folder.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import tempfile
import zipfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from bookflow.core.durability import sync_directory, sync_file
from bookflow.core.errors import BookflowError
from bookflow.storage.engine import io_error, sqlite_uri

FORMAT = "bookflow-company-backup"
FORMAT_VERSION = 1
SUFFIX = ".bookflow-backup"
MANIFEST = "manifest.json"
DATABASE = "company.db"
CHUNK = 1 << 20
MANIFEST_LIMIT = 64 << 20
_ATTACHMENT = re.compile(r"attachments/([0-9a-f]{2})/([0-9a-f]{64})")
_HEX64 = re.compile(r"[0-9a-f]{64}")


def invalid(check: str, message: str | None = None, **details: Any) -> BookflowError:
    return BookflowError("E_BACKUP_INVALID", message=message, details={"check": check, **details})


def _sha256_file(path: Path) -> tuple[str, int]:
    digest, size = hashlib.sha256(), 0
    with open(path, "rb") as fh:
        while chunk := fh.read(CHUNK):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def archive_name(display_name: str, when: datetime) -> str:
    from bookflow.storage.paths import derive_folder_name
    return f"{derive_folder_name(display_name)} {when.strftime('%Y-%m-%d-%H%M%S')}{SUFFIX}"


def check_database(path: Path, *, company_id: str | None = None, revision: str | None = None) -> dict[str, Any]:
    """Integrity, foreign keys, the company row and the schema revision of one database file."""
    try:
        with closing(sqlite3.connect(sqlite_uri(path, "ro"), uri=True)) as conn:
            if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise invalid("database_integrity", "The backup's database fails SQLite's integrity check.")
            if conn.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise invalid("database_foreign_keys", "The backup's database has broken references between records.")
            try:
                rev = conn.execute("SELECT version_num FROM alembic_version").fetchone()
                row = conn.execute("SELECT id, display_name, legal_name, home_currency FROM company_info").fetchall()
            except sqlite3.DatabaseError:
                raise invalid("database_company", "The backup's database is not a Bookflow company database.")
    except sqlite3.DatabaseError:
        raise invalid("database", "The backup's database cannot be read.")
    if len(row) != 1 or rev is None:
        raise invalid("database_company", "The backup's database is not a Bookflow company database.")
    found = {"id": row[0][0], "display_name": row[0][1], "legal_name": row[0][2], "home_currency": row[0][3], "revision": rev[0]}
    if company_id is not None and found["id"] != company_id:
        raise invalid("company_id", "The backup's database belongs to a different company than its manifest says.")
    if revision is not None and found["revision"] != revision:
        raise invalid("schema_revision", "The backup's database schema differs from its manifest.")
    return found


def create(folder: Path, *, company_id: str, display_name: str, created_by: dict[str, Any], bookflow_version: str,
           backup_id: str, backups_dir: Path | None = None, inspect: Callable[[Path], Any] | None = None,
           read_only: bool = False) -> dict[str, Any]:
    """Write and verify one archive in ``folder/backups`` (or ``backups_dir``); return its manifest plus file facts.

    ``inspect`` is called with the path of the consistent database copy before it is archived,
    and its result is returned as ``inspected`` (the checkpoint reads the backup's own copy).
    ``read_only`` leaves the archive at mode 0444.
    """
    backups = folder / "backups" if backups_dir is None else backups_dir
    store = folder / "attachments"
    now = datetime.now(timezone.utc)
    name = archive_name(display_name, now)
    target = backups / name
    n = 1
    while target.exists():
        n += 1
        target = backups / name.replace(SUFFIX, f" ({n}){SUFFIX}")
    work = None
    partial = None
    try:
        backups.mkdir(mode=0o700, exist_ok=True)
        work = Path(tempfile.mkdtemp(prefix=".backup-", dir=backups))
        snapshot = work / DATABASE
        # The command holds the company's write transaction, so no other writer can commit
        # while this read-only connection copies; the copy is the committed state, whole.
        with closing(sqlite3.connect(sqlite_uri(folder / DATABASE, "ro"), uri=True)) as src, \
                closing(sqlite3.connect(sqlite_uri(snapshot, "rwc"), uri=True)) as dst:
            src.backup(dst)
            dst.execute("PRAGMA journal_mode=DELETE")
        facts = check_database(snapshot, company_id=company_id)
        inspected = inspect(snapshot) if inspect is not None else None
        with closing(sqlite3.connect(sqlite_uri(snapshot, "ro"), uri=True)) as conn:
            bodies = sorted({(r[0], r[1]) for r in conn.execute(
                "SELECT sha256, size_bytes FROM attachments WHERE collected_at IS NULL")})
        db_sha, db_size = _sha256_file(snapshot)
        files = [{"name": DATABASE, "sha256": db_sha, "size_bytes": db_size}]
        missing = []
        for sha, size in bodies:
            path = store / sha[:2] / sha
            try:
                st = os.lstat(path)
                ok = stat.S_ISREG(st.st_mode) and _sha256_file(path) == (sha, size)
            except OSError:
                ok = False
            if ok:
                files.append({"name": f"attachments/{sha[:2]}/{sha}", "sha256": sha, "size_bytes": size})
            else:
                missing.append(sha)
        manifest = {
            "format": FORMAT, "format_version": FORMAT_VERSION, "backup_id": backup_id,
            "company_id": company_id, "display_name": display_name, "legal_name": facts["legal_name"],
            "home_currency": facts["home_currency"], "schema_revision": facts["revision"],
            "bookflow_version": bookflow_version, "created_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "created_by": created_by, "files": files, "missing_attachments": missing,
        }
        fd, partial_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".partial", dir=backups)
        os.close(fd)
        partial = Path(partial_name)
        with zipfile.ZipFile(partial, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
            zf.writestr(MANIFEST, json.dumps(manifest, indent=2, sort_keys=True))
            zf.write(snapshot, DATABASE)
            for entry in files[1:]:
                zf.write(store / entry["name"].split("/", 1)[1], entry["name"], compress_type=zipfile.ZIP_STORED)
        sync_file(partial)
        verify(partial)
        if read_only:
            os.chmod(partial, 0o444)
        partial.replace(target)
        partial = None
        sync_directory(backups)
        sync_directory(folder)
    except BookflowError:
        raise
    except (sqlite3.Error, OSError, zipfile.BadZipFile) as e:
        raise io_error("backup", e, target)
    finally:
        if partial is not None:
            partial.unlink(missing_ok=True)
        if work is not None:
            shutil.rmtree(work, ignore_errors=True)
    sha, size = _sha256_file(target)
    return {"manifest": manifest, "path": target, "file_name": target.name, "sha256": sha, "size_bytes": size,
            "inspected": inspected}


def read_manifest(archive: Path) -> dict[str, Any]:
    """Open the archive and return its validated manifest, without reading the members."""
    with _open(archive) as zf:
        return _manifest(zf)


def _open(archive: Path) -> zipfile.ZipFile:
    try:
        st = os.stat(archive)
    except OSError:
        raise invalid("archive_missing", "No backup file at that path.", path=str(archive))
    if not stat.S_ISREG(st.st_mode):
        raise invalid("archive_missing", "No backup file at that path.", path=str(archive))
    try:
        return zipfile.ZipFile(archive, "r")
    except (zipfile.BadZipFile, OSError, ValueError):
        raise invalid("archive", "The file is not a Bookflow backup archive.")


def _manifest(zf: zipfile.ZipFile) -> dict[str, Any]:
    names = zf.namelist()
    if len(names) != len(set(names)) or MANIFEST not in names:
        raise invalid("manifest", "The file is not a Bookflow backup archive.")
    info = zf.getinfo(MANIFEST)
    if info.file_size > MANIFEST_LIMIT:
        raise invalid("manifest")
    try:
        manifest = json.loads(zf.read(MANIFEST))
    except (zipfile.BadZipFile, ValueError, OSError):
        raise invalid("manifest", "The backup's manifest is damaged.")
    if not isinstance(manifest, dict) or manifest.get("format") != FORMAT:
        raise invalid("manifest", "The file is not a Bookflow backup archive.")
    if manifest.get("format_version") != FORMAT_VERSION:
        raise invalid("format_version", "This backup was written in a format this version of Bookflow does not read; upgrade Bookflow.",
                      format_version=manifest.get("format_version"))
    for key in ("company_id", "display_name", "schema_revision", "created_at"):
        if not isinstance(manifest.get(key), str) or not manifest[key]:
            raise invalid("manifest", "The backup's manifest is incomplete.", field=key)
    files = manifest.get("files")
    if not isinstance(files, list) or not files or not isinstance(files[0], dict) or files[0].get("name") != DATABASE:
        raise invalid("manifest", "The backup's manifest is incomplete.", field="files")
    listed = set()
    for entry in files:
        if not isinstance(entry, dict) or set(entry) != {"name", "sha256", "size_bytes"}:
            raise invalid("manifest", "The backup's manifest is incomplete.", field="files")
        member, sha, size = entry["name"], entry["sha256"], entry["size_bytes"]
        if not isinstance(sha, str) or not _HEX64.fullmatch(sha) or type(size) is not int or size < 0 or member in listed:
            raise invalid("manifest", "The backup's manifest is incomplete.", field="files")
        if member != DATABASE:
            m = _ATTACHMENT.fullmatch(member) if isinstance(member, str) else None
            if m is None or m.group(2) != sha or m.group(1) != sha[:2]:
                raise invalid("member_name", "The backup holds a file Bookflow does not put in backups.")
        listed.add(member)
    if set(names) != listed | {MANIFEST}:
        raise invalid("members", "The backup's files do not match its manifest.")
    return manifest


def verify(archive: Path, dest: Path | None = None) -> dict[str, Any]:
    """Refuse anything but an intact archive; with ``dest``, write its verified members there.

    Every member is read in full and compared with the manifest (sha256 and size; zip's own
    CRC is checked on the way). The database is then opened: integrity check, foreign keys,
    the one company row and the schema revision must all agree with the manifest.
    """
    with _open(archive) as zf:
        manifest = _manifest(zf)
        scratch = None
        try:
            if dest is None:
                scratch = Path(tempfile.mkdtemp(prefix="bookflow-verify-"))
            for entry in manifest["files"]:
                out_root = dest if dest is not None else (scratch if entry["name"] == DATABASE else None)
                _copy_member(zf, entry, out_root)
            db = (dest if dest is not None else scratch) / DATABASE
            check_database(db, company_id=manifest["company_id"], revision=manifest["schema_revision"])
        finally:
            if scratch is not None:
                shutil.rmtree(scratch, ignore_errors=True)
    return manifest


def _copy_member(zf: zipfile.ZipFile, entry: dict[str, Any], out_root: Path | None) -> None:
    info = zf.getinfo(entry["name"])
    if info.file_size != entry["size_bytes"]:
        raise invalid("member_size", "A file in the backup is not the size its manifest says.", member=entry["name"])
    out = None
    target = None
    if out_root is not None:
        target = out_root / entry["name"]
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        out = open(target, "xb")
    digest, size = hashlib.sha256(), 0
    try:
        with zf.open(info) as src:
            while chunk := src.read(CHUNK):
                size += len(chunk)
                if size > entry["size_bytes"]:
                    raise invalid("member_size", "A file in the backup is not the size its manifest says.", member=entry["name"])
                digest.update(chunk)
                if out is not None:
                    out.write(chunk)
        if out is not None:
            out.flush()
            os.fsync(out.fileno())
    except (zipfile.BadZipFile, OSError, EOFError, ValueError) as e:
        if isinstance(e, OSError) and not isinstance(e, zipfile.BadZipFile) and out is not None and getattr(e, "errno", None):
            raise io_error("restore", e, target)
        raise invalid("member_damaged", "A file in the backup is damaged.", member=entry["name"])
    finally:
        if out is not None:
            out.close()
    if (digest.hexdigest(), size) != (entry["sha256"], entry["size_bytes"]):
        raise invalid("member_hash", "A file in the backup does not match its recorded fingerprint.", member=entry["name"])
