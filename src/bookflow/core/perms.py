"""umask 077 for the duration of a command (blueprint 3.2)."""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterator


@contextmanager
def private_umask() -> Iterator[None]:
    old = os.umask(0o077)
    try:
        yield
    finally:
        os.umask(old)


def is_private_dir(path) -> bool:
    if os.name != "posix":  # pragma: no cover
        return True
    return (os.stat(path).st_mode & 0o077) == 0
