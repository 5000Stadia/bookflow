"""Fault witnesses for complete transport delivery, independent of SDK serialization."""

import io
import json

import pytest

from bookflow.adapters.mcp.framing import Decoder, HEADER, MAGIC, encode, record
from bookflow.core.errors import BookflowError
from bookflow.core.transfers import CHUNK_BYTES


def wire(document=None, binary=None, check=lambda: None):
    return b"".join(encode(document or {"amount": 9007199254740993}, check=check,
                           operation_ref="intent", binary=binary))


def decode(data, stride=7):
    js, body = io.BytesIO(), io.BytesIO()
    decoder = Decoder(js, body, operation_ref="intent")
    for offset in range(0, len(data), stride):
        decoder.feed(data[offset:offset + stride])
        assert len(decoder.buffer) <= CHUNK_BYTES
    terminal = decoder.finish()
    return json.loads(js.getvalue()), body.getvalue(), terminal


def test_full_large_result_and_binary_are_exact_without_collection_cap():
    original = {"amount": 9007199254740993, "memo": "\U0001f6bf" * 2_100_000,
                "rows": [{"index": index} for index in range(300)]}
    blob = bytes(range(256)) * 1024
    data = wire(original, (blob[i:i + CHUNK_BYTES] for i in range(0, len(blob), CHUNK_BYTES)))
    result, body, terminal = decode(data, stride=33333)
    assert result == original and body == blob
    assert terminal["binary"] is True and terminal["is_error"] is False


def test_large_escaped_scalar_encoder_has_bounded_additional_heap():
    import tracemalloc
    from bookflow.adapters.mcp.framing import json_chunks
    # The authoritative core value exists before measuring transport allocations.
    document = {"memo": "\x00\n\"\\" * 3_000_000}
    tracemalloc.start()
    total = 0
    try:
        for chunk in json_chunks(document):
            total += len(chunk)
            assert len(chunk) <= CHUNK_BYTES
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert total > 30_000_000
    assert peak < 2 * 1024 * 1024, peak


@pytest.mark.parametrize("payload,is_error", [(b'{"unfinished":', False), (b'[]', False),
    (b'{"ok":true}', True), (b'{"code":"E_IO","message":"failed","details":{}}', False)])
def test_correctly_hashed_invalid_business_document_is_not_complete(payload, is_error):
    import hashlib
    terminal = {"version": 1, "operation_ref": "intent", "is_error": is_error, "binary": False,
                "recovery": {"mode": "unavailable", "retained_until": None,
                             "receipt_available": False, "inspection_available": False},
                "channels": {"J": {"bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()},
                             "B": {"bytes": 0, "sha256": hashlib.sha256(b'').hexdigest()}}}
    data = MAGIC + record(b'J', payload) + record(b'T', json.dumps(terminal).encode())
    with pytest.raises(BookflowError) as caught:
        decode(data)
    assert caught.value.details["outcome"] == "unknown"


def test_completion_carries_independent_receipt_and_inspection_availability():
    recovery = {"mode": "retained", "retained_until": "2026-09-06T09:00:00Z",
                "receipt_available": False, "inspection_available": True}
    data = b''.join(encode({"large_result": True}, check=lambda: None,
                         operation_ref="intent", recovery=recovery))
    assert decode(data)[2]["recovery"] == recovery


def test_every_truncation_is_unknown_not_a_complete_or_unsubmitted_result():
    data = wire(binary=[b"receipt"])
    for length in range(len(data)):
        with pytest.raises(BookflowError) as caught:
            decode(data[:length])
        assert caught.value.details["outcome"] == "unknown"
    assert decode(data)[0]["amount"] == 9007199254740993


@pytest.mark.parametrize("fault", ["digest", "body", "extra", "duplicate_terminal", "order", "version", "oversized", "empty"])
def test_invalid_records_never_publish(fault):
    data = wire(binary=[b"receipt"])
    if fault == "digest":
        data = data.replace(b'"bytes":7', b'"bytes":8')
    elif fault == "body":
        data = data.replace(b"receipt", b"receipX")
    elif fault == "extra":
        data += b"x"
    elif fault == "duplicate_terminal":
        index = data.index(b'{"version"') - HEADER.size
        data += data[index:]
    elif fault == "order":
        data = MAGIC + record(b"B", b"receipt") + record(b"J", b"{}")
    elif fault == "version":
        data = b"BFMC2\n" + data[len(MAGIC):]
    elif fault == "oversized":
        data = MAGIC + HEADER.pack(b"J", CHUNK_BYTES + 1)
    elif fault == "empty":
        data = MAGIC + HEADER.pack(b"J", 0)
    with pytest.raises(BookflowError) as caught:
        decode(data)
    assert caught.value.details["outcome"] == "unknown"


def test_revocation_between_business_bytes_and_terminal_cannot_complete():
    permitted = True

    def check():
        if not permitted:
            raise BookflowError("E_UNAUTHENTICATED")

    source = encode({"secret": "authorized-result"}, check=check, operation_ref="intent")
    decoder = Decoder(io.BytesIO(), None, operation_ref="intent")
    decoder.feed(next(source))
    decoder.feed(next(source))
    permitted = False
    with pytest.raises(BookflowError, match="valid credential"):
        next(source)
    with pytest.raises(BookflowError) as caught:
        decoder.finish()
    assert caught.value.details["outcome"] == "unknown"


def test_short_sink_writes_are_completed_and_zero_progress_is_rejected():
    class Short(io.BytesIO):
        def write(self, data):
            return super().write(data[:2])

    sink = Short()
    decoder = Decoder(sink, None, operation_ref="intent")
    decoder.feed(wire())
    decoder.finish()
    assert json.loads(sink.getvalue()) == {"amount": 9007199254740993}

    class Stalled:
        def write(self, data):
            return 0

    decoder = Decoder(Stalled(), None, operation_ref="intent")
    with pytest.raises(BookflowError):
        decoder.feed(wire())
    with pytest.raises(BookflowError):
        decoder.finish()
