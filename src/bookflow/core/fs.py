"""Filesystem locality. Bookflow refuses to open databases on network filesystems."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from bookflow.core.errors import BookflowError

LOCAL_TYPES = frozenset({
    "ext2", "ext3", "ext4", "xfs", "btrfs", "f2fs", "zfs", "tmpfs", "overlay",
    "apfs", "hfs", "hfsplus", "ntfs", "ntfs3", "fuseblk", "exfat", "vfat", "fat32", "refs", "msdos",
    "ecryptfs", "bcachefs", "jfs", "reiserfs",
})


def _unescape_mountinfo(field: str) -> str:
    return (field.replace("\\040", " ").replace("\\011", "\t")
            .replace("\\012", "\n").replace("\\134", "\\"))


def _linux_fs_type(path: Path, mountinfo_text: str | None = None) -> str | None:
    if mountinfo_text is None:
        try:
            mountinfo_text = Path("/proc/self/mountinfo").read_text(encoding="utf-8")
        except OSError:
            return None
    best: tuple[int, str] | None = None
    target = str(path)
    for line in mountinfo_text.splitlines():
        parts = line.split(" ")
        if len(parts) < 7 or "-" not in parts:
            continue
        mount_point = _unescape_mountinfo(parts[4])
        sep = parts.index("-")
        if sep + 1 >= len(parts):
            continue
        fs_type = parts[sep + 1]
        if target == mount_point or target.startswith(mount_point.rstrip("/") + "/") or mount_point == "/":
            if best is None or len(mount_point) > best[0]:
                best = (len(mount_point), fs_type)
    return best[1] if best else None


def _macos_fs_type(path: Path) -> str | None:  # pragma: no cover - not exercised on Linux
    import ctypes
    import ctypes.util

    libc = ctypes.CDLL(ctypes.util.find_library("c"))

    class StatFS(ctypes.Structure):
        _fields_ = [
            ("f_bsize", ctypes.c_uint32), ("f_iosize", ctypes.c_int32),
            ("f_blocks", ctypes.c_uint64), ("f_bfree", ctypes.c_uint64),
            ("f_bavail", ctypes.c_uint64), ("f_files", ctypes.c_uint64),
            ("f_ffree", ctypes.c_uint64), ("f_fsid", ctypes.c_int32 * 2),
            ("f_owner", ctypes.c_uint32), ("f_type", ctypes.c_uint32),
            ("f_flags", ctypes.c_uint32), ("f_fssubtype", ctypes.c_uint32),
            ("f_fstypename", ctypes.c_char * 16), ("f_mntonname", ctypes.c_char * 1024),
            ("f_mntfromname", ctypes.c_char * 1024), ("f_flags_ext", ctypes.c_uint32),
            ("f_reserved", ctypes.c_uint32 * 7),
        ]

    buf = StatFS()
    if libc.statfs(str(path).encode(), ctypes.byref(buf)) != 0:
        return None
    return buf.f_fstypename.decode(errors="replace")


def _windows_fs_type(path: Path) -> str | None:  # pragma: no cover - not exercised on Linux
    import ctypes

    drive = str(path.resolve())
    if drive.startswith("\\\\"):
        return "unc"
    root = os.path.splitdrive(drive)[0] + "\\"
    kind = ctypes.windll.kernel32.GetDriveTypeW(root)
    if kind == 4:
        return "remote"
    return "ntfs" if kind in (2, 3) else None


def fs_type_of(path: Path) -> str | None:
    """Best-effort filesystem type name of the nearest existing ancestor."""
    p = path.resolve()
    while not p.exists() and p.parent != p:
        p = p.parent
    if sys.platform.startswith("linux"):
        return _linux_fs_type(p)
    if sys.platform == "darwin":
        return _macos_fs_type(p)
    if sys.platform == "win32":
        return _windows_fs_type(p)
    return None


def check_local(path: Path, fs_type: str | None = None) -> str:
    """Return the filesystem type or raise E_NETWORK_SHARE / E_FS_UNKNOWN."""
    t = fs_type if fs_type is not None else fs_type_of(path)
    if t is None:
        raise BookflowError("E_FS_UNKNOWN", details={"path": str(path)})
    if t.lower() not in LOCAL_TYPES:
        raise BookflowError("E_NETWORK_SHARE", message=f"Filesystem type {t!r} is not a supported local type; Bookflow refuses network and unknown filesystems.", details={"path": str(path), "fs_type": t})
    return t
