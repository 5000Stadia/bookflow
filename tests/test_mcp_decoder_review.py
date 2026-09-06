"""Malformed-terminal and file-sink heap fault witnesses for F11/F12."""

import hashlib
import io
import json
import tempfile
import tracemalloc

import pytest

from bookflow.adapters.mcp.framing import Decoder, MAGIC, encode, record
from bookflow.core.errors import BookflowError


@pytest.mark.parametrize('mode', [[], {}, ['retained'], {'retained': True}, None, 3])
def test_untyped_recovery_mode_is_structured_postsubmission_uncertainty(mode):
    data = b''.join(encode({'ok': True}, check=lambda: None, operation_ref='ref'))
    position = data.index(b'{"version":')
    terminal = json.loads(data[position:])
    terminal['recovery']['mode'] = mode
    damaged = data[:position - 5] + record(b'T', json.dumps(terminal).encode())
    with pytest.raises(BookflowError) as caught:
        Decoder(io.BytesIO(), None, operation_ref='ref').feed(damaged)
    assert caught.value.code == 'E_IO'
    assert caught.value.details['operation'] == 'mcp_result'
    assert caught.value.details['stage'] == 'post_submission'
    assert caught.value.details['outcome'] == 'unknown'


@pytest.mark.timeout(90)
@pytest.mark.parametrize('key', [False, True])
def test_file_sink_decoder_does_not_materialize_large_scalar_or_key(tmp_path, key):
    piece = b'x' * 65536
    prefix, suffix = (b'{"', b'":true}') if key else (b'{"memo":"', b'"}')
    digest, size = hashlib.sha256(), 0
    with tempfile.TemporaryFile(dir=tmp_path) as sink:
        tracemalloc.start()
        try:
            decoder = Decoder(sink, None, operation_ref='ref')
            decoder.feed(MAGIC)
            for chunk in [prefix]:
                decoder.feed(record(b'J', chunk)); digest.update(chunk); size += len(chunk)
            for _ in range(192):
                decoder.feed(record(b'J', piece)); digest.update(piece); size += len(piece)
            decoder.feed(record(b'J', suffix)); digest.update(suffix); size += len(suffix)
            terminal = {'version': 1, 'operation_ref': 'ref', 'is_error': False, 'binary': False,
                'recovery': {'mode': 'unavailable', 'retained_until': None, 'receipt_available': False, 'inspection_available': False},
                'channels': {'J': {'bytes': size, 'sha256': digest.hexdigest()},
                             'B': {'bytes': 0, 'sha256': hashlib.sha256(b'').hexdigest()}}}
            decoder.feed(record(b'T', json.dumps(terminal).encode()))
            assert decoder.finish()['channels']['J']['bytes'] == size
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        assert sink.tell() == size > 12 * 1024 * 1024
    print('decoder file sink', 'key' if key else 'value', 'bytes', size, 'peak', peak)
    assert peak < 1024 * 1024, peak


@pytest.mark.parametrize('payload', [
    b'{}', b'{"x":[]}', b'{"x":[true,false,null,0,-0,1.25,-2e+40,3E-2]}',
    b'{"x":[{},[],{"a":{"b":[1]}}]}', b'{"x":"\\b\\f\\n\\r\\t\\\\\\/\\\"\\u0000"}',
    '{"é🚿":"水","":""}'.encode(), b'{"\\u0063ode":"\\u0045_IO","message":"failure","details":{"x":[1]}}',
    b'{"code":"E_IO","message":"ok","details":[],"extra":true}',
    b'{"code":"OK","message":"ok","details":{}}',
    b'{"nested":{"code":"E_IO","message":"failure","details":{}}}',
    b'{"verylongkeythatmustnotbeassembled":1}', b'{"x":1.7976931348623157e308}',
])
def test_incremental_syntax_matches_standard_json_for_valid_split_tokens(payload):
    from bookflow.adapters.mcp.json_validation import JsonDocumentValidator
    document = json.loads(payload)
    is_error = (set(document) == {'code', 'message', 'details'} and isinstance(document['code'], str)
                and document['code'].startswith('E_') and isinstance(document['message'], str)
                and isinstance(document['details'], dict))
    for stride in (1, 2, 7, 65536):
        validator = JsonDocumentValidator()
        for offset in range(0, len(payload), stride):
            validator.feed(payload[offset:offset + stride])
        validator.finish(is_error)


@pytest.mark.parametrize('payload', [
    b'', b'[]', b'null', b'{', b'{"x"}', b'{"x":}', b'{"x":true false}', b'{"x":tru}',
    b'{"x":falsee}', b'{"x":nul}', b'{"x":00}', b'{"x":01}', b'{"x":-01}', b'{"x":+1}',
    b'{"x":1.}', b'{"x":1e}', b'{"x":1e+}', b'{"x":.1}', b'{"x":--1}', b'{"x":1e2e3}',
    b'{"x":[,1]}', b'{"x":[1,]}', b'{"x":[1 2]}', b'{"x":1,}', b'{"x":[}}',
    b'{"x":"\\u12zz"}', b'{"x":"\\q"}', b'{"x":"\n"}', b'{"x":"\xff"}',
    b'{"x":"\xc0\xaf"}', b'{"x":"\xf0\x9f"}', b'{"x":"unterminated}', b'{}{}', b'{}junk',
])
def test_invalid_json_never_acquires_a_complete_terminal(payload):
    from bookflow.adapters.mcp.json_validation import JsonDocumentValidator
    with pytest.raises((ValueError, UnicodeError)):
        # The two non-object roots are legal JSON but forbidden business envelopes.
        if not isinstance(json.loads(payload), dict):
            raise ValueError('root')
    for stride in (1, 7):
        validator = JsonDocumentValidator()
        with pytest.raises(BookflowError) as caught:
            for offset in range(0, len(payload), stride):
                validator.feed(payload[offset:offset + stride])
            validator.finish(False)
        assert caught.value.details['outcome'] == 'unknown'


def test_seeded_json_corpus_and_character_faults_match_standard_parser():
    import random
    from bookflow.adapters.mcp.json_validation import JsonDocumentValidator
    rng = random.Random(11012)
    atoms = [None, True, False, 0, -13, 9007199254740993, 0.125, -1.25e30, '', 'é🚿水', '\x00\"\\\n', 'details']
    def value(depth=0):
        if depth == 4 or rng.randrange(3) == 0:
            return rng.choice(atoms)
        if rng.randrange(2):
            return [value(depth + 1) for _ in range(rng.randrange(4))]
        return {str(rng.choice(atoms)): value(depth + 1) for _ in range(rng.randrange(4))}
    for _ in range(120):
        encoded = json.dumps({'data': value()}, ensure_ascii=rng.choice([True, False]),
                             separators=(',', ':')).encode()
        variants = [encoded]
        for _ in range(6):
            offset = rng.randrange(len(encoded))
            variants.append(encoded[:offset] + rng.choice([b'', b',', b'"', b']', b'\\', b'0', b'\xff']) + encoded[offset + 1:])
        for payload in variants:
            try:
                valid = isinstance(json.loads(payload), dict)
            except (ValueError, UnicodeError):
                valid = False
            validator = JsonDocumentValidator()
            try:
                for offset in range(0, len(payload), 3):
                    validator.feed(payload[offset:offset + 3])
                validator.finish(False)
                accepted = True
            except BookflowError:
                accepted = False
            assert accepted == valid, payload
