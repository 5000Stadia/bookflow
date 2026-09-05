"""Bounded transfer metadata and framing; terminal frames convey body EOF only.

Transport objects may supply recv/send or binary read/write. Cooperative streams
cannot be interrupted while blocked. Each helper invocation owns its deadline;
callers can share a transfer-wide deadline through ``check``. No sockets are closed.
"""

from __future__ import annotations

import base64
import binascii
import json
import math
import re
import time
from typing import BinaryIO, Callable

from .errors import BookflowError

FRAME_SIZE = 65_536
INPUT_SIZE = 6_144
HEADER_SIZE = 8_192
MAX_BODY_SIZE = 100_000_000


def _noop():
    pass


def _fail(code, message):
    raise BookflowError(code, message)


def _limit(value, maximum=None):
    if type(value) is not int or value < 0 or (maximum is not None and value > maximum):
        _fail("E_VALIDATION", "Limit must be a nonnegative integer within its ceiling.")
    return value


def _encode(value, limit):
    """Encode incrementally, including individual strings, within a byte cap."""
    _limit(limit)
    if type(value) is not dict:
        _fail("E_VALIDATION", "JSON root must be an object.")
    output = bytearray()
    active = set()

    def emit(text):
        data = text.encode("utf-8")
        if len(data) > limit - len(output):
            _fail("E_VALIDATION", "JSON exceeds its byte limit.")
        output.extend(data)

    def string(text):
        emit('"')
        for start in range(0, len(text), 1024):
            emit(json.dumps(text[start:start + 1024], ensure_ascii=False)[1:-1])
        emit('"')

    def visit(item, depth=0):
        if depth > 128:
            _fail("E_VALIDATION", "JSON nesting is too deep.")
        kind = type(item)
        if kind is str:
            string(item)
        elif item is None or kind is bool:
            emit(json.dumps(item))
        elif kind in (int, float):
            if kind is float and not math.isfinite(item):
                _fail("E_VALIDATION", "JSON numbers must be finite.")
            if kind is int and item.bit_length() > (limit + 1) * 4:
                _fail("E_VALIDATION", "JSON number exceeds its byte limit.")
            emit(json.dumps(item, allow_nan=False))
        elif kind in (dict, list):
            if id(item) in active:
                _fail("E_VALIDATION", "Circular JSON value.")
            active.add(id(item))
            emit("{" if kind is dict else "[")
            for index, key in enumerate(item):
                if index:
                    emit(",")
                if kind is dict:
                    if type(key) is not str:
                        _fail("E_VALIDATION", "JSON keys must be strings.")
                    string(key)
                    emit(":")
                    visit(item[key], depth + 1)
                else:
                    visit(key, depth + 1)
            emit("}" if kind is dict else "]")
            active.remove(id(item))
        else:
            _fail("E_VALIDATION", "Unsupported JSON value.")

    try:
        visit(value)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise BookflowError("E_VALIDATION", "Invalid JSON value.") from exc
    return bytes(output)


def _decode(data):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                _fail("E_VALIDATION", "Duplicate JSON key.")
            result[key] = value
        return result

    def constant(value):
        _fail("E_VALIDATION", "JSON numbers must be finite.")

    try:
        result = json.loads(data.decode("utf-8"), object_pairs_hook=pairs,
                            parse_constant=constant)
        # Apply the same finite-number, Unicode and depth guards as encoding.
        _encode(result, max(len(data) * 6, 2))
        return result
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise BookflowError("E_VALIDATION", "Invalid UTF-8 JSON object.") from exc


def encode_input(value: dict) -> str:
    """Return unpadded base64url metadata (6144 decoded/8192 encoded bytes)."""
    return base64.urlsafe_b64encode(_encode(value, INPUT_SIZE)).rstrip(b"=").decode("ascii")


def decode_input(value: str) -> dict:
    """Decode canonical unpadded base64url containing a strict JSON object."""
    if not isinstance(value, str):
        _fail("E_VALIDATION", "Input header must be text.")
    if len(value) > HEADER_SIZE:
        _fail("E_VALIDATION", "Input header exceeds its byte limit.")
    if not re.fullmatch(r"[A-Za-z0-9_-]*", value) or len(value) % 4 == 1:
        _fail("E_VALIDATION", "Invalid unpadded base64url.")
    try:
        data = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
    except (ValueError, binascii.Error) as exc:
        raise BookflowError("E_VALIDATION", "Invalid base64url.") from exc
    if len(data) > INPUT_SIZE:
        _fail("E_VALIDATION", "Decoded input exceeds its byte limit.")
    if base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii") != value:
        _fail("E_VALIDATION", "Noncanonical base64url.")
    return _decode(data)


class _IO:
    def __init__(self, lifetime_seconds, idle_seconds, check, clock):
        for value, maximum in ((lifetime_seconds, 300), (idle_seconds, 30)):
            if type(value) not in (int, float) or not 0 < value <= maximum:
                _fail("E_VALIDATION", "Invalid transfer time bound.")
        self.clock, self.check = clock, check
        self.deadline = clock() + lifetime_seconds
        self.idle = idle_seconds
        self.last = clock()

    def remaining(self):
        self.check()
        now = self.clock()
        remaining = min(self.deadline - now, self.idle - (now - self.last))
        if remaining <= 0:
            _fail("E_IO", "Transfer deadline expired.")
        return remaining

    def call(self, target, method, argument):
        wait = self.remaining()
        timed = hasattr(target, "gettimeout") and hasattr(target, "settimeout")
        old = target.gettimeout() if timed else None
        try:
            if timed:
                target.settimeout(min(wait, old) if old is not None else wait)
            result = getattr(target, method)(argument)
            self.remaining()
            self.last = self.clock()
            return result
        except (OSError, ValueError) as exc:
            raise BookflowError("E_IO", "Transfer I/O failed.") from exc
        finally:
            if timed:
                try:
                    target.settimeout(old)
                except OSError:
                    pass

    def read(self, target, n):
        data = self.call(target, "recv" if hasattr(target, "recv") else "read", n)
        if not isinstance(data, bytes) or len(data) > n:
            _fail("E_IO", "Binary source violated bounded read.")
        return data

    def exact(self, target, n):
        data = bytearray()
        while len(data) < n:
            part = self.read(target, min(n - len(data), FRAME_SIZE))
            if not part:
                _fail("E_IO", "Premature transfer EOF.")
            data.extend(part)
        return bytes(data)

    def write(self, target, data):
        view = memoryview(data)
        while view:
            count = self.call(target, "send" if hasattr(target, "send") else "write", view[:FRAME_SIZE])
            if type(count) is not int or not 0 < count <= min(len(view), FRAME_SIZE):
                _fail("E_IO", "Binary destination made invalid progress.")
            view = view[count:]


class FramedReader:
    """Read at most n bytes (and one frame) per call; read(-1) is forbidden."""

    def __init__(self, socket, limit, lifetime_seconds=300, idle_seconds=30,
                 check: Callable[[], None] = _noop, clock=time.monotonic):
        self.socket = socket
        self.limit = _limit(limit, MAX_BODY_SIZE)
        self._io = _IO(lifetime_seconds, idle_seconds, check, clock)
        self._remaining = 0
        self._total = 0
        self._eof = False

    def read(self, n: int) -> bytes:
        if type(n) is not int or n < 0:
            _fail("E_VALIDATION", "A bounded nonnegative read size is required.")
        if n == 0 or self._eof:
            return b""
        if not self._remaining:
            size = int.from_bytes(self._io.exact(self.socket, 4), "big")
            if size > FRAME_SIZE:
                _fail("E_VALIDATION", "Body frame exceeds 65536 bytes.")
            if size > self.limit - self._total:
                _fail("E_VALUE_RANGE", "Body exceeds its byte limit.")
            if size == 0:
                self._eof = True
                return b""
            self._remaining = size
            self._total += size
        data = self._io.exact(self.socket, min(n, self._remaining))
        self._remaining -= len(data)
        return data


def send_body(socket, stream: BinaryIO, limit, lifetime_seconds=300, idle_seconds=30,
              check: Callable[[], None] = _noop, clock=time.monotonic) -> None:
    """Send bounded body frames, emitting terminal zero only after source EOF."""
    remaining = _limit(limit, MAX_BODY_SIZE)
    io = _IO(lifetime_seconds, idle_seconds, check, clock)
    while True:
        data = io.read(stream, min(FRAME_SIZE, remaining + 1))
        if len(data) > remaining:
            _fail("E_VALUE_RANGE", "Body exceeds its byte limit.")
        io.write(socket, len(data).to_bytes(4, "big"))
        if not data:
            return
        io.write(socket, data)
        remaining -= len(data)


def send_json(socket, value: dict, limit=FRAME_SIZE, lifetime_seconds=300,
              idle_seconds=30, check: Callable[[], None] = _noop,
              clock=time.monotonic) -> None:
    """Send one length-prefixed JSON object; use limit=8192 for envelopes."""
    io = _IO(lifetime_seconds, idle_seconds, check, clock)
    data = _encode(value, _limit(limit, FRAME_SIZE))
    io.write(socket, len(data).to_bytes(4, "big"))
    io.write(socket, data)


def recv_json(socket, limit=FRAME_SIZE, lifetime_seconds=300, idle_seconds=30,
              check: Callable[[], None] = _noop, clock=time.monotonic) -> dict:
    """Receive one bounded length-prefixed strict JSON object."""
    _limit(limit, FRAME_SIZE)
    io = _IO(lifetime_seconds, idle_seconds, check, clock)
    size = int.from_bytes(io.exact(socket, 4), "big")
    if size > limit:
        _fail("E_VALIDATION", "JSON frame exceeds its byte limit.")
    result = _decode(io.exact(socket, size))
    io.remaining()
    return result
