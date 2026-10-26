"""A sealed on-disk copy of the fully verified permission catalog chain (R180, lane D).

A new process otherwise imports every catalog layer and re-validates the accumulated
catalog at each one (about 3.8 s CPU). The first process of a code version that verifies
the chain in full writes what it verified here; later processes of the same code version
read it back instead. design/performance.md ("Increment 2, lane D") says exactly what the
full check proves and what this copy does and does not stand in for.

- The key hashes every input the verification depends on: the source bytes of every
  catalog layer module, of permission_catalog, permission_runtime, permission_snapshot,
  permission_policy, core/deletion_families and this module; the pinned descriptor hashes;
  the Bookflow version; the Python version. Never mtimes.
- The file is sealed: a sha256 over its index and body, re-hashed on every load.
- Every failure reading, parsing, sealing or decoding falls back to full verification.
  Nothing here can make a catalog acceptable that full verification would refuse.
- BOOKFLOW_CATALOG_VERIFY=full forces full verification (and rewrites the copy).

This module imports nothing from the catalog at import time: dispatch configures it
before any catalog is built.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path

FORMAT = 'bookflow-permission-catalog-cache/1'
ENV = 'BOOKFLOW_CATALOG_VERIFY'
_HERE = Path(__file__).resolve().parent
_EXTRA_SOURCES = ('permission_runtime.py', 'permission_snapshot.py', 'permission_policy.py',
                  '../core/deletion_families.py')
_root: Path | None = None


def configure(root) -> None:
    """Name the data root whose cache/ folder this process may read and write."""
    global _root
    _root = Path(root) if root is not None else None


def forced_full() -> bool:
    return os.environ.get(ENV, '').strip().lower() == 'full'


def cache_dir() -> Path | None:
    return None if _root is None else _root / 'cache'


def _bookflow_version() -> str:
    # A checkout carries its pyproject beside src/; an installed build carries metadata.
    pyproject = _HERE.parent.parent.parent / 'pyproject.toml'
    try:
        found = re.search(r'(?m)^version\s*=\s*"([^"]+)"', pyproject.read_text('utf-8'))
        if found:
            return found.group(1)
    except OSError:
        pass
    try:
        from importlib.metadata import version
        return version('bookflow')
    except Exception:
        return 'unknown'


def key_sources() -> list[Path]:
    """Every source file whose bytes the verification depends on."""
    layers = sorted(_HERE.glob('permission_*catalog*.py'))
    return [*layers, *(_HERE / name for name in _EXTRA_SOURCES)]


def compute_key(pins: dict, ancestor: str) -> str:
    digest = hashlib.sha256()

    def part(label, data: bytes):
        digest.update(label.encode() + b'\0' + str(len(data)).encode() + b'\0' + data)

    part('format', FORMAT.encode())
    for path in key_sources():
        part('source:' + path.name, path.read_bytes())
    part('pins', json.dumps(sorted(pins.items()), separators=(',', ':')).encode())
    part('ancestor', ancestor.encode())
    part('bookflow', _bookflow_version().encode())
    part('python', ('%s|%s|%s' % (sys.version, sys.implementation.cache_tag, sys.byteorder)).encode())
    return digest.hexdigest()


def _path(key: str) -> Path | None:
    folder = cache_dir()
    return None if folder is None else folder / ('permission-catalog-%s.bin' % key[:40])


def _canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                      allow_nan=False).encode('utf-8')


def encode(key: str, entries: dict) -> bytes:
    """entries: version -> dict(catalog=<asdict JSON str>, meta=<JSON-able dict>)."""
    body = bytearray()
    index = {}
    for version in sorted(entries):
        blob = entries[version]['catalog'].encode('utf-8')
        index[version] = dict(entries[version]['meta'], offset=len(body), length=len(blob))
        body += blob
    head = _canonical(dict(format=FORMAT, key=key, versions=index))
    seal = hashlib.sha256(head + b'\n' + bytes(body)).hexdigest()
    return seal.encode() + b'\n' + head + b'\n' + bytes(body)


class Sealed:
    """A file whose seal and key verified; blobs are sliced out per version on demand."""
    def __init__(self, index: dict, body: bytes):
        self.index, self._body = index, body

    def blob(self, version: str) -> str:
        entry = self.index[version]
        offset, length = entry['offset'], entry['length']
        if type(offset) is not int or type(length) is not int or offset < 0 or offset + length > len(self._body):
            raise ValueError('index out of range')
        return self._body[offset:offset + length].decode('utf-8')


def decode(data: bytes, key: str) -> Sealed:
    """Raise on anything but an intact file of exactly this key."""
    seal, rest = data[:64], data[65:]
    if len(data) < 66 or data[64:65] != b'\n' or not re.fullmatch(rb'[0-9a-f]{64}', seal):
        raise ValueError('no seal')
    if hashlib.sha256(rest).hexdigest().encode() != seal:
        raise ValueError('seal mismatch')
    newline = rest.index(b'\n')
    head = json.loads(rest[:newline])
    if type(head) is not dict or head.get('format') != FORMAT or head.get('key') != key:
        raise ValueError('wrong format or key')
    index = head.get('versions')
    if type(index) is not dict or not all(type(v) is dict for v in index.values()):
        raise ValueError('bad index')
    return Sealed(index, rest[newline + 1:])


def load(key: str) -> Sealed | None:
    """The sealed copy for this key, or None (missing, partial, corrupt, forced full)."""
    path = _path(key)
    if path is None or forced_full():
        return None
    try:
        return decode(path.read_bytes(), key)
    except Exception:
        return None


def store(key: str, entries: dict) -> Path | None:
    """Write atomically (temp file, then rename); best effort, never raises."""
    path = _path(key)
    if path is None:
        return None
    tmp = None
    try:
        if not _root.is_dir():
            return None  # never create a data root; `init` does that
        folder = path.parent
        folder.mkdir(mode=0o700, exist_ok=True)
        data = encode(key, entries)
        fd, tmp = tempfile.mkstemp(prefix='.permission-catalog-', suffix='.tmp', dir=folder)
        with os.fdopen(fd, 'wb') as out:
            out.write(data)
        os.replace(tmp, path)
        tmp = None
        for old in folder.glob('permission-catalog-*.bin'):
            if old.name != path.name:
                try:
                    old.unlink()
                except OSError:
                    pass
        return path
    except Exception:
        return None
    finally:
        if tmp is not None:
            try:
                os.unlink(tmp)
            except OSError:
                pass
