"""Private host/launcher delivery records; MCP framing remains owned by the SDK.

Sinks are operation-owned partial outputs. They may receive bytes before validation;
only finish() grants permission to publish them as complete artifacts.
"""

import hashlib
import json
import struct

from bookflow.core.errors import BookflowError
from bookflow.core.transfers import CHUNK_BYTES

MAGIC = b"BFMC1\n"
HEADER = struct.Struct("!cI")


def invalid(reason):
    return BookflowError("E_IO", details={"operation": "mcp_result", "reason": reason,
                                        "stage": "post_submission", "outcome": "unknown"})


def record(channel, payload):
    if channel not in (b"J", b"B", b"T") or not 0 < len(payload) <= CHUNK_BYTES:
        raise invalid("invalid_frame")
    return HEADER.pack(channel, len(payload)) + payload


def json_chunks(document):
    """No full serialized-result copy and no business-document size ceiling."""
    def string(value):
        yield '"'
        for offset in range(0, len(value), 4096):
            yield json.dumps(value[offset:offset + 4096], ensure_ascii=False)[1:-1]
        yield '"'

    def tokens(value):
        if isinstance(value, str):
            yield from string(value)
        elif isinstance(value, dict):
            yield "{"
            for number, (key, item) in enumerate(value.items()):
                if not isinstance(key, str):
                    raise invalid("invalid_json_key")
                if number:
                    yield ","
                yield from string(key)
                yield ":"
                yield from tokens(item)
            yield "}"
        elif isinstance(value, (list, tuple)):
            yield "["
            for number, item in enumerate(value):
                if number:
                    yield ","
                yield from tokens(item)
            yield "]"
        else:
            yield json.dumps(value, allow_nan=False)

    pending = bytearray()
    for text in tokens(document):
        for offset in range(0, len(text), 4096):
            pending.extend(text[offset:offset + 4096].encode("utf-8"))
            if len(pending) >= CHUNK_BYTES:
                yield bytes(pending[:CHUNK_BYTES])
                del pending[:CHUNK_BYTES]
    if pending:
        yield bytes(pending)


def encode(document, *, check, operation_ref, is_error=False, binary=None, recovery=None):
    """Check current publication authority before each record, including terminal."""
    if type(is_error) is not bool or not isinstance(operation_ref, str) or not 1 <= len(operation_ref) <= 128:
        raise invalid("invalid_completion")
    check()
    yield MAGIC
    totals = {}
    for channel, source in ((b"J", json_chunks(document)), (b"B", binary)):
        digest, size = hashlib.sha256(), 0
        if source is not None:
            for chunk in source:
                if not isinstance(chunk, bytes) or not 0 < len(chunk) <= CHUNK_BYTES:
                    raise invalid("invalid_channel_chunk")
                digest.update(chunk)
                size += len(chunk)
                check()
                yield record(channel, chunk)
        totals[channel.decode()] = {"bytes": size, "sha256": digest.hexdigest()}
    recovery = recovery or {"mode": "unavailable", "retained_until": None,
                            "receipt_available": False, "inspection_available": False}
    _recovery(recovery)
    terminal = {"version": 1, "operation_ref": operation_ref, "is_error": is_error,
                "recovery": recovery,
                "binary": binary is not None, "channels": totals}
    check()
    yield record(b"T", json.dumps(terminal, separators=(",", ":")).encode())


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _recovery(value):
    if not isinstance(value, dict) or set(value) != {"mode", "retained_until", "receipt_available", "inspection_available"}:
        raise invalid("invalid_recovery")
    if value["mode"] not in {"retained", "unavailable"} or any(type(value[key]) is not bool for key in ("receipt_available", "inspection_available")):
        raise invalid("invalid_recovery")
    if value["mode"] == "unavailable":
        if value["retained_until"] is not None or value["receipt_available"] or value["inspection_available"]:
            raise invalid("invalid_recovery")
    else:
        from datetime import datetime
        try:
            deadline = value["retained_until"]
            if not isinstance(deadline, str) or len(deadline) > 40 or not deadline.endswith("Z"):
                raise ValueError
            datetime.fromisoformat(deadline)
        except ValueError:
            raise invalid("invalid_recovery") from None


class JsonDocumentValidator:
    """Validate the complete JSON channel incrementally, without retaining its rows."""

    def __init__(self):
        import ijson
        self.json_error = ijson.JSONError
        self.started = self.complete = False
        self.count = 0
        self.error_fields = set()

        def events():
            while True:
                prefix, event, value = yield
                if not self.started:
                    if prefix != "" or event != "start_map":
                        raise ValueError("business document must be an object")
                    self.started = True
                if prefix == "" and event == "end_map":
                    self.complete = True
                if prefix == "" and event == "map_key":
                    self.count += 1
                if ((prefix == "code" and event == "string" and value.startswith("E_"))
                    or (prefix == "message" and event == "string")
                    or (prefix == "details" and event == "start_map")):
                    self.error_fields.add(prefix)

        target = events()
        next(target)
        self.parser = ijson.parse_coro(target, use_float=False)

    def feed(self, chunk):
        try:
            self.parser.send(chunk)
        except (ValueError, UnicodeError, self.json_error):
            raise invalid("invalid_json") from None

    def finish(self, is_error):
        try:
            self.parser.close()
        except (ValueError, UnicodeError, self.json_error):
            raise invalid("invalid_json") from None
        error_document = self.count == 3 and self.error_fields == {"code", "message", "details"}
        if not self.complete or error_document != is_error:
            raise invalid("invalid_business_completion")


class Decoder:
    """Incremental decoder retaining at most one bounded record, never a result."""

    def __init__(self, json_sink, binary_sink, *, operation_ref):
        self.sinks = {b"J": json_sink, b"B": binary_sink}
        self.operation_ref = operation_ref
        self.buffer = bytearray()
        self.needed = len(MAGIC)
        self.phase = "magic"
        self.channel = None
        self.last_channel = b"J"
        self.digests = {key: hashlib.sha256() for key in self.sinks}
        self.sizes = {key: 0 for key in self.sinks}
        self.terminal = None
        self.failed = False
        self.document_validator = JsonDocumentValidator()

    def feed(self, data):
        if self.failed:
            raise invalid("decoder_failed")
        try:
            self._feed(data)
        except BaseException:
            self.failed = True
            self.buffer.clear()
            raise

    def _feed(self, data):
        if not isinstance(data, bytes):
            raise invalid("invalid_channel_chunk")
        offset = 0
        while offset < len(data):
            if self.terminal is not None:
                raise invalid("after_terminal")
            count = min(self.needed - len(self.buffer), len(data) - offset)
            self.buffer.extend(memoryview(data)[offset:offset + count])
            offset += count
            if len(self.buffer) != self.needed:
                continue
            payload = bytes(self.buffer)
            self.buffer.clear()
            if self.phase == "magic":
                if payload != MAGIC:
                    raise invalid("incompatible_bridge")
                self.phase, self.needed = "header", HEADER.size
            elif self.phase == "header":
                self.channel, self.needed = HEADER.unpack(payload)
                if self.channel not in (b"J", b"B", b"T") or not 0 < self.needed <= CHUNK_BYTES:
                    raise invalid("invalid_frame")
                if self.channel == b"J" and self.last_channel != b"J":
                    raise invalid("channel_order")
                self.phase = "payload"
            else:
                self._payload(payload)
                self.phase, self.needed = "header", HEADER.size

    def _payload(self, payload):
        channel = self.channel
        if channel == b"T":
            try:
                terminal = json.loads(payload, object_pairs_hook=_unique_object)
                if not isinstance(terminal, dict) or set(terminal) != {"version", "operation_ref", "is_error", "binary", "channels", "recovery"}:
                    raise ValueError
                if type(terminal["version"]) is not int or terminal["version"] != 1 or terminal["operation_ref"] != self.operation_ref:
                    raise ValueError
                if type(terminal["is_error"]) is not bool or type(terminal["binary"]) is not bool:
                    raise ValueError
                if not isinstance(terminal["channels"], dict) or set(terminal["channels"]) != {"J", "B"}:
                    raise ValueError
                for key in self.sinks:
                    total = terminal["channels"][key.decode()]
                    if not isinstance(total, dict) or set(total) != {"bytes", "sha256"} or type(total["bytes"]) is not int:
                        raise ValueError
                    if total != {"bytes": self.sizes[key], "sha256": self.digests[key].hexdigest()}:
                        raise ValueError
                if not self.sizes[b"J"] or (not terminal["binary"] and self.sizes[b"B"]):
                    raise ValueError
                if terminal["binary"] and self.sinks[b"B"] is None:
                    raise ValueError
            except (ValueError, TypeError, KeyError, UnicodeError):
                raise invalid("invalid_completion") from None
            _recovery(terminal["recovery"])
            self.document_validator.finish(terminal["is_error"])
            self.terminal = terminal
            return
        if channel == b"B":
            self.last_channel = channel
        else:
            self.document_validator.feed(payload)
        sink = self.sinks[channel]
        if sink is None:
            raise invalid("unexpected_binary")
        offset = 0
        while offset < len(payload):
            count = sink.write(payload[offset:])
            if type(count) is not int or not 0 < count <= len(payload) - offset:
                raise invalid("delivery_write")
            offset += count
        self.sizes[channel] += len(payload)
        self.digests[channel].update(payload)

    def finish(self):
        if self.failed or self.terminal is None or self.buffer:
            raise invalid("incomplete_delivery")
        return self.terminal
