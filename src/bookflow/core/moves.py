"""No-replace directory rename per platform (blueprint 3.1)."""

from __future__ import annotations

import ctypes
import errno
import os
import sys
from pathlib import Path

from bookflow.core.durability import sync_move_parents
from bookflow.core.errors import BookflowError


def _linux_noreplace(src: Path, dst: Path) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    RENAME_NOREPLACE = 1
    AT_FDCWD = -100
    if hasattr(libc, "renameat2"):
        rc = libc.renameat2(AT_FDCWD, os.fsencode(str(src)), AT_FDCWD, os.fsencode(str(dst)), RENAME_NOREPLACE)
        if rc != 0:
            e = ctypes.get_errno()
            if e in (errno.EINVAL, errno.ENOSYS, errno.ENOTSUP, errno.EOPNOTSUPP):
                _fallback(src, dst)
                return
            raise OSError(e, os.strerror(e), str(src), None, str(dst))
        return
    _fallback(src, dst)


def _fallback(src: Path, dst: Path) -> None:
    """Best effort where no no-replace primitive exists: check then rename."""
    if dst.exists() or dst.is_symlink():
        raise FileExistsError(errno.EEXIST, "target exists", str(dst))
    os.rename(src, dst)


def rename_noreplace(src: Path, dst: Path) -> None:
    """Move directory ``src`` to ``dst``; never replaces an existing entry."""
    if sys.platform.startswith("linux"):
        _linux_noreplace(src, dst)
    elif sys.platform == "win32":  # pragma: no cover
        MOVEFILE_WRITE_THROUGH = 0x8
        ok = ctypes.windll.kernel32.MoveFileExW(str(src), str(dst), MOVEFILE_WRITE_THROUGH)
        if not ok:
            raise ctypes.WinError()
    else:  # pragma: no cover - macOS: renamex_np with RENAME_EXCL
        libc = ctypes.CDLL(None, use_errno=True)
        if hasattr(libc, "renamex_np"):
            rc = libc.renamex_np(os.fsencode(str(src)), os.fsencode(str(dst)), 4)
            if rc != 0:
                e = ctypes.get_errno()
                raise OSError(e, os.strerror(e), str(src), None, str(dst))
        else:
            _fallback(src, dst)


def move_dir(src: Path, dst: Path, company_id: str | None = None) -> None:
    """Move with a temporary hop for case-only changes on case-insensitive filesystems.

    Raises BookflowError E_IO on failure; FileExistsError on an occupied target
    is reported as E_IO with operation ``rename`` and errno ``EEXIST``.
    """
    try:
        if src.parent == dst.parent and src.name != dst.name and src.name.casefold() == dst.name.casefold() and src.resolve() == dst.resolve():
            tmp = dst.with_name(f"{dst.name}.moving-{company_id or 'x'}")
            rename_noreplace(src, tmp)
            sync_move_parents(src, tmp)
            rename_noreplace(tmp, dst)
            sync_move_parents(tmp, dst)
        else:
            rename_noreplace(src, dst)
            sync_move_parents(src, dst)
    except OSError as e:
        raise BookflowError("E_IO", details={"operation": "rename", "errno": errno.errorcode.get(e.errno or 0, str(e.errno)), "path": str(dst)})
