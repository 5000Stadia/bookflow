"""config.toml: OS-login mappings and per-user default company. Written atomically."""

from __future__ import annotations

import os
import sys
import tomllib
from pathlib import Path
from typing import Any

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
        if not path.exists():
            return cfg
        try:
            raw = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError) as e:
            raise BookflowError("E_CONFIG_INVALID", details={"path": str(path), "problem": str(e)})
        users = raw.get("users", {}) if isinstance(raw, dict) else None
        client = raw.get("client", {}) if isinstance(raw, dict) else None
        if users is None or not isinstance(users, dict) or not isinstance(client, dict) or any(not isinstance(v, dict) for v in users.values()):
            raise BookflowError("E_CONFIG_INVALID", details={"path": str(path), "problem": "unexpected shape"})
        cfg.data = {"client": dict(client), "users": {k: dict(v) for k, v in users.items()}}
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
        tmp = self.path.with_suffix(".toml.tmp")
        tmp.write_text(dump(self.data), encoding="utf-8")
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.path)
