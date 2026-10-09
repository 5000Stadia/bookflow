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


def unsafe(problem, path, fix, *, facts=True):
    """The refusal for a local path that is not safe to read through or write into, saying what is wrong and the fix.

    The blind July trial's launcher refused a group-writable input folder with only "The acting user may not
    run this command here"; nothing said which part of the path was unsafe or what to change.
    """
    where = path or "the path"
    details = {"stage": "local_file", "reason": "unsafe_file"}
    if facts:
        details.update(path=path, problem=problem, fix=fix)
    return BookflowError("E_PERMISSION", message=f"{where} is not safe for MCP file transfer: {problem}. {fix}", details=details)


def _private_directory(fd, path=None):
    info = os.fstat(fd)
    if info.st_uid != os.getuid():
        raise unsafe(f"the directory is owned by uid {info.st_uid}, not the account running this server (uid {os.getuid()})",
                     path, "Change its owner to that account (chown), or start the launcher as the directory's owner.")
    if info.st_mode & 0o022:
        who = "group- and world-writable" if info.st_mode & 0o022 == 0o022 else (
            "group-writable" if info.st_mode & 0o020 else "world-writable")
        raise unsafe(f"the directory is {who} (mode {oct(info.st_mode & 0o777)[2:]})", path,
                     f"Run `chmod go-w {path or 'DIR'}` (a folder made with umask 002 is group-writable), then restart "
                     "the launcher.")


def _open_directory(parts, start=None, lineage=None, base=""):
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY) if start is None else os.dup(start)
    try:
        if start is not None:
            _private_directory(fd)
        walked = []
        for part in parts:
            walked.append(part)
            nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = nxt
            if lineage is not None:
                lineage.append(_identity(os.fstat(fd)))
            if start is not None:
                _private_directory(fd, base + "/" + "/".join(walked))
        return fd
    except BaseException:
        os.close(fd)
        raise


class Directories:
    """Capabilities never authorize a bookkeeping command or a remote host path."""

    def __init__(self, paths, *, flag=None):
        self.roots = []
        self.flag = flag
        if len(paths) > 16:
            raise BookflowError("E_USAGE", message="At most 16 directories may be configured per file direction.")
        if paths and (os.name != "posix" or not hasattr(os, "O_NOFOLLOW") or os.open not in os.supports_dir_fd):
            raise BookflowError("E_USAGE", message="File capabilities require supported POSIX directory handles; use Linux, macOS or WSL.")
        try:
            for value in paths:
                parts = _parts(value)
                lineage = []
                fd = _open_directory(parts, lineage=lineage)
                info = os.fstat(fd)
                try:
                    _private_directory(fd, value)
                except BaseException:
                    os.close(fd)
                    raise
                self.roots.append((parts, fd, _identity(info), lineage))
        except OSError as exc:
            self.close()
            if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
                raise unsafe("a part of the path is a symbolic link or not a directory", value,
                             "Configure the real directory (no symbolic link in its path) as the launcher's directory.", facts=False) from None
            raise BookflowError("E_IO", details={"operation": "file_configuration", "errno": exc.errno}) from None
        except BaseException:
            self.close()
            raise

    def close(self):
        while self.roots:
            os.close(self.roots.pop()[1])

    def outside(self, field=None, **extra):
        """The refusal for a local path no configured directory holds, saying which ones do."""
        allowed = ["/" + "/".join(root[0]) for root in self.roots]
        details = {"stage": "local_file", "reason": "outside_allowed_directory", "allowed_directories": allowed, **extra}
        if field:
            details["field"] = field
        if allowed:
            message = f"That local file path is not allowed: it must be inside {', '.join(allowed)}."
        else:
            # The cutover trial's agent asked to save a result to a file and was told only where it
            # must be; nothing said that no directory was configured at all (v1.6 fix batch).
            which, does = {"--input-dir": ("input", "reads no local file")}.get(self.flag, ("output", "saves no file"))
            details["configured"] = False
            message = (f"No {which} directory is configured, so this MCP server {does}. Whoever starts it can allow "
                       f"one by adding {self.flag or '--output-dir'} DIR (an absolute directory; repeatable) to the "
                       "`bookflow mcp` launcher command.")
        return BookflowError("E_PERMISSION", message=message + " This is a refusal of the file, not of the command.",
                             details=details)

    def check(self, value, field):
        """Refuse a path outside every configured directory before anything is submitted."""
        parts = _parts(value)
        if not any(len(parts) > len(root) and parts[:len(root)] == root for root, *_ in self.roots):
            raise self.outside(field, outcome="not_submitted")

    @contextmanager
    def parent(self, value):
        parts = _parts(value)
        matches = [(root, fd, identity, lineage) for root, fd, identity, lineage in self.roots
                   if len(parts) > len(root) and parts[:len(root)] == root]
        if not matches:
            raise self.outside()
        root, fd, identity, root_lineage = max(matches, key=lambda item: len(item[0]))
        parent = None
        try:
            observed_root = []
            check = _open_directory(root, lineage=observed_root)
            try:
                if _identity(os.fstat(check)) != identity or observed_root != root_lineage:
                    raise OSError(errno.ESTALE, "Directory changed")
            finally:
                os.close(check)
            relative = parts[len(root):-1]
            parent_lineage = []
            parent = _open_directory(relative, fd, lineage=parent_lineage, base="/" + "/".join(root))
            parent_identity = _identity(os.fstat(parent))

            def verify():
                observed_root = []
                check_root = _open_directory(root, lineage=observed_root)
                try:
                    if _identity(os.fstat(check_root)) != identity or observed_root != root_lineage:
                        raise OSError(errno.ESTALE, "Directory changed")
                    observed_parent = []
                    check_parent = _open_directory(relative, check_root, lineage=observed_parent)
                    try:
                        if _identity(os.fstat(check_parent)) != parent_identity or observed_parent != parent_lineage:
                            raise OSError(errno.ESTALE, "Directory changed")
                    finally:
                        os.close(check_parent)
                finally:
                    os.close(check_root)

            yield parent, parts[-1], verify
        except OSError as exc:
            if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
                raise unsafe("a part of the path, or the file itself, is a symbolic link or not what it should be",
                             value, "Give the real file's path: no symbolic link in it, a regular file in a folder.", facts=False) from None
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
                    raise unsafe("it is not a regular file with a single name" if stat.S_ISREG(before.st_mode)
                                 else "it is not a regular file",
                                 value, "Copy the content into a plain file (no hard link, pipe or device) in the input folder.", facts=False)
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
                with os.fdopen(fd, "wb", closefd=False) as stream:
                    yield stream
                    stream.flush()
                    os.fsync(stream.fileno())
                verify()
                if _identity(os.stat(temporary, dir_fd=parent, follow_symlinks=False)) != identity:
                    raise OSError(errno.ESTALE, "Output changed")
                os.link(temporary, name, src_dir_fd=parent, dst_dir_fd=parent, follow_symlinks=False)
                published = True
                if _identity(os.stat(name, dir_fd=parent, follow_symlinks=False)) != _identity(os.fstat(fd)):
                    raise OSError(errno.ESTALE, "Published output changed")
                os.fsync(parent)
            except BaseException:
                if published and _identity(os.stat(name, dir_fd=parent, follow_symlinks=False)) == identity:
                    os.unlink(name, dir_fd=parent)
                raise
            finally:
                os.close(fd)
                try:
                    if _identity(os.stat(temporary, dir_fd=parent, follow_symlinks=False)) == identity:
                        os.unlink(temporary, dir_fd=parent)
                except FileNotFoundError:
                    pass

    def destination(self):
        if not self.roots:
            raise self.outside("transport.output_file", outcome="not_submitted")
        return str(Path("/", *self.roots[0][0], "bookflow-" + secrets.token_hex(16)))
