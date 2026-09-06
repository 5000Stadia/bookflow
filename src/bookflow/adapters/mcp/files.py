"""Calling-machine directory capabilities and verified, no-replace file publication."""

import errno
import os
import secrets
import stat
from contextlib import contextmanager
from pathlib import Path

from bookflow.core.errors import BookflowError


def _identity(info):
    return info.st_dev, info.st_ino


def _parts(value):
    if not isinstance(value, str) or not value.startswith("/") or "\x00" in value or "\\" in value:
        raise BookflowError("E_VALIDATION", details={"reason": "invalid_file_path"})
    parts = value.split("/")[1:]
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise BookflowError("E_VALIDATION", details={"reason": "invalid_file_path"})
    return parts


def _open_directory(parts, start=None):
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY) if start is None else os.dup(start)
    try:
        for part in parts:
            nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = nxt
        return fd
    except BaseException:
        os.close(fd)
        raise


class Directories:
    """Capabilities never authorize a bookkeeping command or a remote host path."""

    def __init__(self, paths):
        self.roots = []
        if len(paths) > 16:
            raise BookflowError("E_USAGE", message="At most16 directories may be configured per file direction.")
        if paths and (os.name != "posix" or not hasattr(os, "O_NOFOLLOW") or os.open not in os.supports_dir_fd):
            raise BookflowError("E_USAGE", message="File capabilities require supported POSIX directory handles; use Linux, macOS or WSL.")
        try:
            for value in paths:
                parts = _parts(value)
                fd = _open_directory(parts)
                info = os.fstat(fd)
                if info.st_uid != os.getuid() or info.st_mode & 0o022:
                    os.close(fd)
                    raise BookflowError("E_PERMISSION", details={"reason": "unsafe_directory"})
                self.roots.append((parts, fd, _identity(info)))
        except OSError as exc:
            self.close()
            raise BookflowError("E_IO", details={"operation": "file_configuration", "errno": exc.errno}) from None
        except BaseException:
            self.close()
            raise

    def close(self):
        while self.roots:
            os.close(self.roots.pop()[1])

    @contextmanager
    def parent(self, value):
        parts = _parts(value)
        matches = [(root, fd, identity) for root, fd, identity in self.roots
                   if len(parts) > len(root) and parts[:len(root)] == root]
        if not matches:
            raise BookflowError("E_PERMISSION", details={"stage": "local_file", "reason": "outside_allowed_directory"})
        root, fd, identity = max(matches, key=lambda item: len(item[0]))
        parent = None
        try:
            check = _open_directory(root)
            try:
                if _identity(os.fstat(check)) != identity:
                    raise OSError(errno.ESTALE, "Directory changed")
            finally:
                os.close(check)
            relative = parts[len(root):-1]
            parent = _open_directory(relative, fd)
            parent_identity = _identity(os.fstat(parent))

            def verify():
                check_root = _open_directory(root)
                try:
                    if _identity(os.fstat(check_root)) != identity:
                        raise OSError(errno.ESTALE, "Directory changed")
                    check_parent = _open_directory(relative, check_root)
                    try:
                        if _identity(os.fstat(check_parent)) != parent_identity:
                            raise OSError(errno.ESTALE, "Directory changed")
                    finally:
                        os.close(check_parent)
                finally:
                    os.close(check_root)

            yield parent, parts[-1], verify
        except OSError as exc:
            raise BookflowError("E_IO", details={"operation": "local_file", "errno": exc.errno}) from None
        finally:
            if parent is not None:
                os.close(parent)

    @contextmanager
    def input(self, value):
        with self.parent(value) as (parent, name, verify):
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
            try:
                before = os.fstat(fd)
                if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                    raise BookflowError("E_PERMISSION", details={"stage": "local_file", "reason": "unsafe_file"})
                with os.fdopen(fd, "rb", closefd=False) as stream:
                    yield stream
                after = os.fstat(fd)
                if (_identity(before), before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (_identity(after), after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                    raise OSError(errno.ESTALE, "Input changed")
                verify()
            finally:
                os.close(fd)

    @contextmanager
    def output(self, value):
        with self.parent(value) as (parent, name, verify):
            try:
                os.stat(name, dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise FileExistsError(errno.EEXIST, "Output exists")
            temporary = ".bookflow-mcp-" + secrets.token_hex(16)
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
            identity = _identity(os.fstat(fd))
            published = False
            try:
                with os.fdopen(fd, "wb") as stream:
                    yield stream
                    stream.flush()
                    os.fsync(stream.fileno())
                verify()
                if _identity(os.stat(temporary, dir_fd=parent, follow_symlinks=False)) != identity:
                    raise OSError(errno.ESTALE, "Output changed")
                os.link(temporary, name, src_dir_fd=parent, dst_dir_fd=parent, follow_symlinks=False)
                published = True
                os.fsync(parent)
            except BaseException:
                if published and _identity(os.stat(name, dir_fd=parent, follow_symlinks=False)) == identity:
                    os.unlink(name, dir_fd=parent)
                raise
            finally:
                try:
                    if _identity(os.stat(temporary, dir_fd=parent, follow_symlinks=False)) == identity:
                        os.unlink(temporary, dir_fd=parent)
                except FileNotFoundError:
                    pass

    def destination(self):
        if not self.roots:
            raise BookflowError("E_PERMISSION", details={"stage": "local_file", "reason": "outside_allowed_directory"})
        return str(Path("/", *self.roots[0][0], "bookflow-" + secrets.token_hex(16)))
