"""OS-login mappings and defaults, with recoverable hub-committed file projection."""

from __future__ import annotations
from bookflow.core.commit_hooks import CommitHooks

import os
import sqlite3
import sys
import tomllib
from pathlib import Path
from typing import Any

from bookflow.core.durability import write_metadata
from bookflow.core.errors import BookflowError


def os_login() -> str:
    if sys.platform == "win32":  # pragma: no cover
        import ctypes
        buf = ctypes.create_unicode_buffer(257)
        size = ctypes.c_uint(257)
        ctypes.windll.advapi32.GetUserNameW(buf, ctypes.byref(size))
        return buf.value
    import pwd
    return pwd.getpwuid(os.getuid()).pw_name


def _toml_str(v: str) -> str:
    return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'


def dump(data: dict[str, Any]) -> str:
    lines: list[str] = []
    client = data.get("client", {})
    if client:
        lines.append("[client]")
        for k, v in client.items():
            lines.append(f"{k} = {_toml_str(str(v))}")
        lines.append("")
    for login, table in data.get("users", {}).items():
        lines.append(f"[users.{_toml_str(login)}]")
        for k, v in table.items():
            if v is not None:
                lines.append(f"{k} = {_toml_str(str(v))}")
        lines.append("")
    return "\n".join(lines)


class Config:
    def __init__(self, path: Path):
        self.path = path
        self.data: dict[str, Any] = {"client": {}, "users": {}}

    @classmethod
    def load(cls, path: Path) -> "Config":
        cfg = cls(path)
        pending = _pending_from_disk(path.parent / "hub.db")
        if pending is not None:
            cfg.data = _parse(pending["contents"], path)
            return cfg
        if not path.exists():
            return cfg
        try:
            contents = path.read_text(encoding="utf-8")
        except OSError as e:
            raise BookflowError("E_CONFIG_INVALID", details={"path": str(path), "problem": str(e)})
        cfg.data = _parse(contents, path)
        return cfg

    def user_table(self, login: str) -> dict[str, Any] | None:
        return self.data["users"].get(login)

    def set_user(self, login: str, user_id: str, default_company: str | None = None) -> None:
        table = self.data["users"].setdefault(login, {})
        table["user_id"] = user_id
        if default_company is not None:
            table["default_company"] = default_company

    def set_default_company(self, login: str, company_id: str | None) -> None:
        table = self.data["users"].setdefault(login, {})
        if company_id is None:
            table.pop("default_company", None)
        else:
            table["default_company"] = company_id

    def clear_default_everywhere(self, company_ids: set[str]) -> None:
        for table in self.data["users"].values():
            if table.get("default_company") in company_ids:
                table.pop("default_company", None)

    def save(self) -> None:
        write_metadata(self.path, dump(self.data))

    def stage_pending(self, hub, *, request_id: str) -> None:
        """Record the desired file contents inside the caller's audited hub transaction."""
        if not hub.writable or not hub.raw.in_transaction:
            raise RuntimeError("config intent requires an active writable hub transaction")
        from bookflow.core.ids import new_id
        from bookflow.hub.schema import pending_config

        hub.conn.execute(pending_config.delete())
        hub.conn.execute(pending_config.insert().values(
            id=1, token=new_id(), request_id=request_id, contents=dump(self.data),
        ))

    def flush_pending(self, hub, *, commits: CommitHooks | None = None) -> bool:
        """Project committed settings, then clear their intent in a separate transaction.

        A failed file replacement or intent deletion retains recoverable committed
        state. Read-only Config.load overlays that state without changing either
        database or file. The caller must serialize writers and hold no transaction.
        """
        commits = commits if commits is not None else CommitHooks()
        with commits.operation("config.flush", hub):
            if not hub.writable or hub.raw.in_transaction:
                raise RuntimeError("config projection requires an idle writable hub handle")
            pending = _pending_row(hub.raw)
            if pending is None:
                return False
            from bookflow.hub.schema import pending_config
            from sqlalchemy.exc import DBAPIError

            try:
                self.data = _parse(pending["contents"], self.path)
                commits.publishing("config.flush")
                self.save()
                hub.raw.execute("BEGIN IMMEDIATE")
                hub.conn.execute(pending_config.delete().where(pending_config.c.token == pending["token"]))
                commits.commit(hub, "config.flush")
            except BaseException as e:
                try:
                    if hub.raw.in_transaction:
                        hub.raw.rollback()
                except sqlite3.Error:
                    pass
                if not isinstance(e, (BookflowError, OSError, sqlite3.Error, DBAPIError)):
                    raise
                raise BookflowError(
                    "E_PARTIAL_WRITE",
                    message="The settings change was committed; its config.toml copy is pending. Reads use the committed settings; the next writable command retries the file update.",
                    details={"durable": ["config"], "projection_pending": True, "request_id": pending["request_id"], "cause": getattr(e, "code", "E_IO")},
                ) from e
            return True


def _parse(contents: str, path: Path) -> dict[str, Any]:
    try:
        raw = tomllib.loads(contents)
    except tomllib.TOMLDecodeError as e:
        raise BookflowError("E_CONFIG_INVALID", details={"path": str(path), "problem": str(e)})
    users = raw.get("users", {})
    client = raw.get("client", {})
    if not isinstance(users, dict) or not isinstance(client, dict) or any(not isinstance(v, dict) for v in users.values()):
        raise BookflowError("E_CONFIG_INVALID", details={"path": str(path), "problem": "unexpected shape"})
    return {"client": dict(client), "users": {k: dict(v) for k, v in users.items()}}


def _pending_row(conn: sqlite3.Connection) -> dict[str, str] | None:
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='pending_config'").fetchone() is None:
        return None
    row = conn.execute("SELECT token, request_id, contents FROM pending_config WHERE id = 1").fetchone()
    return dict(zip(("token", "request_id", "contents"), row)) if row is not None else None


def _pending_from_disk(hub_path: Path) -> dict[str, str] | None:
    """Read a committed intent before config-based actor/default selection, with no repair."""
    if not hub_path.exists():
        return None
    from bookflow.core.fs import check_local
    from bookflow.storage.engine import io_error, sqlite_uri

    check_local(hub_path)
    try:
        conn = sqlite3.connect(sqlite_uri(hub_path, "ro"), uri=True, isolation_level=None)
        try:
            conn.execute("BEGIN")
            return _pending_row(conn)
        finally:
            conn.close()
    except sqlite3.Error as e:
        raise io_error("read", e, hub_path) from e
