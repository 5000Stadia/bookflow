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
