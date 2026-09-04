"""Data root resolution, folder naming, and marker files."""

from __future__ import annotations

import os
import re
import tomllib
import unicodedata
from pathlib import Path
from typing import Any

from bookflow.core.durability import write_metadata
from bookflow.core.errors import BookflowError

_BAD = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
_WS = re.compile(r"\s+")
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
MAX_BYTES = 90


def resolve_data_root(option: str | None = None) -> Path:
    raw = option or os.environ.get("BOOKFLOW_DATA_ROOT") or os.path.join(os.path.expanduser("~"), ".bookflow")
    return Path(raw).expanduser()


def normalize_display_name(name: str, field: str = "name") -> str:
    """Trim, collapse whitespace, NFC. Raises E_VALIDATION on empty or '/'."""
    n = _WS.sub(" ", unicodedata.normalize("NFC", name)).strip()
    if not n:
        raise BookflowError("E_VALIDATION", details={"fields": [{"field": field, "problem": "must not be empty"}]})
    if "/" in n:
        raise BookflowError("E_VALIDATION", details={"fields": [{"field": field, "problem": "must not contain '/'"}]})
    return n


def name_key(name: str) -> str:
    return unicodedata.normalize("NFC", _WS.sub(" ", name).strip()).casefold()


def derive_folder_name(display_name: str) -> str:
    """Blueprint 3.1 derivation, without the collision suffix."""
    n = unicodedata.normalize("NFC", display_name)
    n = _BAD.sub(" ", n)
    n = _WS.sub(" ", n).strip(" .")
    b = n.encode("utf-8")
    if len(b) > MAX_BYTES:
        cut = b[:MAX_BYTES]
        n = cut.decode("utf-8", errors="ignore").strip(" .")
    if not n:
        n = "Company"
    stem = n.split(".", 1)[0]
    if stem.upper() in _RESERVED:
        n = n + " Co"
    return n


def _fold(name: str) -> str:
    return unicodedata.normalize("NFC", name).casefold()


def choose_folder_name(parent: Path, display_name: str, exclude: str | None = None) -> str:
    """Pick a folder name not already present in ``parent`` after NFC and case folding."""
    base = derive_folder_name(display_name)
    taken = {_fold(p) for p in os.listdir(parent)} if parent.exists() else set()
    if exclude is not None:
        taken.discard(_fold(exclude))
    candidate, n = base, 1
    while _fold(candidate) in taken:
        n += 1
        suffix = f" ({n})"
        allowed = MAX_BYTES - len(suffix.encode())
        stem = base.encode("utf-8")[:allowed].decode("utf-8", errors="ignore").rstrip(" .")
        candidate = stem + suffix
    return candidate


def reserve_folder(parent: Path, display_name: str) -> Path:
    """Choose and atomically create the folder; retry on a lost race."""
    for _ in range(1000):
        name = choose_folder_name(parent, display_name)
        target = parent / name
        try:
            target.mkdir(mode=0o700)
            return target
        except FileExistsError:
            continue
    raise BookflowError("E_IO", details={"operation": "mkdir", "errno": "EEXIST", "path": str(parent)})


COMPANY_MARKER = "bookflow-company.toml"
ORG_MARKER = "bookflow-organization.toml"


def _write_toml(path: Path, data: dict[str, Any]) -> None:
    lines = []
    for k, v in data.items():
        if isinstance(v, bool):
            lines.append(f"{k} = {'true' if v else 'false'}")
        elif v is None:
            continue
        else:
            s = str(v).replace("\\", "\\\\").replace('"', '\\"')
            lines.append(f'{k} = "{s}"')
    write_metadata(path, "\n".join(lines) + "\n")


def write_company_marker(folder: Path, *, company_id: str, state: str, display_name: str | None = None, schema_revision: str | None = None) -> None:
    _write_toml(folder / COMPANY_MARKER, {"company_id": company_id, "state": state, "display_name": display_name, "schema_revision": schema_revision})


def read_company_marker(folder: Path) -> dict[str, Any]:
    p = folder / COMPANY_MARKER
    try:
        data = tomllib.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise BookflowError("E_ATTACH_INVALID", details={"check": "marker", "path": str(folder)})
    except (OSError, tomllib.TOMLDecodeError):
        raise BookflowError("E_ATTACH_INVALID", details={"check": "marker", "path": str(folder)})
    if "company_id" not in data or "state" not in data:
        raise BookflowError("E_ATTACH_INVALID", details={"check": "marker", "path": str(folder)})
    return data


def write_org_marker(folder: Path, *, organization_id: str) -> None:
    _write_toml(folder / ORG_MARKER, {"organization_id": organization_id})


def read_org_marker(folder: Path) -> dict[str, Any] | None:
    p = folder / ORG_MARKER
    if not p.exists():
        return None
    try:
        return tomllib.loads(p.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return None
