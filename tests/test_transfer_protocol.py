import base64
import io
import socket
from concurrent.futures import ThreadPoolExecutor

import pytest

from bookflow.core.errors import BookflowError
from bookflow.core.transfer_protocol import (
    FRAME_SIZE, FramedReader, decode_input, encode_input, recv_json, send_body, send_json,
)


def frame(data):
    return len(data).to_bytes(4, 'big') + data


def header(data):
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode('ascii')


def fails(code, function, *args, **kwargs):
    with pytest.raises(BookflowError) as caught:
        function(*args, **kwargs)
    assert caught.value.code == code


class Fragmented(io.BytesIO):
    def __init__(self, data=b'', width=1):
        super().__init__(data)
        self.width = width
        self.requests = []

    def read(self, n):
        assert 0 < n <= FRAME_SIZE
        self.requests.append(n)
        return super().read(min(n, self.width))

    def write(self, data):
        assert len(data) <= FRAME_SIZE
        return super().write(data[:self.width])


@pytest.mark.parametrize('size', [0, 1, FRAME_SIZE, FRAME_SIZE + 17])
def test_socketpair_roundtrip(size):
    body = bytes(range(256)) * (size // 256) + bytes(range(size % 256))
    left, right = socket.socketpair()
    with left, right, ThreadPoolExecutor(max_workers=1) as pool:
        left.settimeout(2)
        right.settimeout(2)

        def sender():
            send_json(left, {'ready': True}, limit=8192)
            send_body(left, io.BytesIO(body), size)
            send_json(left, {'done': True})

        future = pool.submit(sender)
        assert recv_json(right, limit=8192) == {'ready': True}
        reader = FramedReader(right, size)
        chunks = []
        while chunk := reader.read(197):
            assert type(chunk) is bytes and len(chunk) <= 197
            chunks.append(chunk)
        assert b''.join(chunks) == body
        assert reader.read(1) == b''
        assert recv_json(right) == {'done': True}
        future.result(timeout=3)
        assert left.gettimeout() == right.gettimeout() == 2


def test_fragmented_headers_payloads_and_bounded_reads():
    source = Fragmented(frame(b'abc') + frame(b'defg') + frame(b''))
    reader = FramedReader(source, 7)
    assert reader.read(0) == b'' and source.tell() == 0
    assert reader.read(2) == b'ab'
    assert reader.read(1000000000) == b'c'
    assert reader.read(1000000000) == b'defg'
    assert reader.read(1) == b''
    fails('E_VALIDATION', reader.read, -1)


@pytest.mark.parametrize('wire', [b'', b'\0', b'\0\0\0', frame(b'abc')[:-1], frame(b'abc'), frame(b'abc') + b'\0\0'])
def test_truncations(wire):
    reader = FramedReader(Fragmented(wire), 100)

    def drain():
        while reader.read(10):
            pass

    fails('E_IO', drain)


@pytest.mark.parametrize('size,limit,code', [(65537, 100000, 'E_VALIDATION'), (4, 3, 'E_VALUE_RANGE'), (0xffffffff, 1, 'E_VALIDATION')])
def test_announced_size_rejected_before_payload(size, limit, code):
    source = io.BytesIO(size.to_bytes(4, 'big') + b'untouched')
    fails(code, FramedReader(source, limit).read, 1)
    assert source.tell() == 4


def test_cumulative_limit():
    source = io.BytesIO(frame(b'ab') + frame(b'cd') + frame(b''))
    reader = FramedReader(source, 3)
    assert reader.read(2) == b'ab'
    fails('E_VALUE_RANGE', reader.read, 2)
    assert source.tell() == 10


@pytest.mark.parametrize('body,limit', [(b'', 0), (b'abc', 3), (b'x' * 65537, 65537)])
def test_send_partial_writes(body, limit):
    destination = Fragmented(width=997)
    send_body(destination, Fragmented(body, width=1024), limit)
    reader = FramedReader(io.BytesIO(destination.getvalue()), limit)
    assert b''.join(iter(lambda: reader.read(65536), b'')) == body


def test_over_limit_and_failed_source_have_no_terminal():
    destination = io.BytesIO()
    fails('E_VALUE_RANGE', send_body, destination, Fragmented(b'abcd'), 3)
    assert destination.getvalue() == frame(b'a') + frame(b'b') + frame(b'c')

    class Broken:
        def read(self, n):
            raise OSError('broken')

    destination = io.BytesIO()
    fails('E_IO', send_body, destination, Broken(), 3)
    assert destination.getvalue() == b''


@pytest.mark.parametrize('raw', [b'[]', b'null', b'1', b'{', b'{}{}', b'{"x":1,"x":2}', b'{"x":{"a":1,"a":2}}', b'{"x":NaN}', b'{"x":Infinity}', b'{"x":-Infinity}', b'{"x":1e9999}', b'{"x":"\xff"}', b'{"x":"\\ud800"}', b'{"x":' + b'[' * 1500 + b'0' + b']' * 1500 + b'}'])
def test_strict_json(raw):
    fails('E_VALIDATION', decode_input, header(raw))
    fails('E_VALIDATION', recv_json, io.BytesIO(frame(raw)))


@pytest.mark.parametrize('value', ['', '====', 'e30=', 'e30\n', 'e+0', 'e/0', 'é', 'a', 'e31'])
def test_bad_base64(value):
    fails('E_VALIDATION', decode_input, value)


def test_metadata_unicode_and_exact_limits():
    value = {'name': 'résumé 日本 😀', 'nested': [True, None, -2, 1.5]}
    assert decode_input(encode_input(value)) == value
    value = {'x': 'a' * (6144 - 8)}
    encoded = encode_input(value)
    assert len(encoded) == 8192
    assert decode_input(encoded) == value
    fails('E_VALIDATION', encode_input, {'x': 'a' * (6144 - 7)})
    fails('E_VALIDATION', decode_input, 'a' * 8193)
    fails('E_VALIDATION', encode_input, {'x': '😀' * 2000})


@pytest.mark.parametrize('value', [[], {1: 'bad'}, {'x': float('nan')}, {'x': float('inf')}, {'x': b'bytes'}, {'x': '\ud800'}])
def test_invalid_encoding(value):
    fails('E_VALIDATION', encode_input, value)
    destination = io.BytesIO()
    fails('E_VALIDATION', send_json, destination, value)
    assert destination.getvalue() == b''


def test_circular_and_deep_encoding():
    value = {}
    value['x'] = value
    fails('E_VALIDATION', encode_input, value)
    value = {}
    for _ in range(1500):
        value = {'x': value}
    fails('E_VALIDATION', encode_input, value)


@pytest.mark.parametrize('limit', [8192, 65536])
def test_json_caps_and_fragmentation(limit):
    value = {'x': 'a' * (limit - 8)}
    destination = Fragmented(width=1024)
    send_json(destination, value, limit=limit)
    assert recv_json(Fragmented(destination.getvalue(), width=17), limit=limit) == value
    fails('E_VALIDATION', send_json, io.BytesIO(), {'x': 'a' * (limit - 7)}, limit=limit)
    source = io.BytesIO((limit + 1).to_bytes(4, 'big'))
    fails('E_VALIDATION', recv_json, source, limit=limit)
    assert source.tell() == 4


@pytest.mark.parametrize('wire', [b'', b'\0\0', frame(b'{}')[:-1]])
def test_json_truncation(wire):
    fails('E_IO', recv_json, Fragmented(wire))


class Clock:
    now = 0

    def __call__(self):
        return self.now


class SlowSocket(Fragmented):
    def __init__(self, clock, data=b'', step=0.6):
        super().__init__(data)
        self.clock, self.step = clock, step
        self.timeout = 7
        self.waits = []

    def gettimeout(self):
        return self.timeout

    def settimeout(self, value):
        self.timeout = value
        self.waits.append(value)

    def recv(self, n):
        self.clock.now += self.step
        return super().read(n)

    def send(self, data):
        self.clock.now += self.step
        return super().write(data)


@pytest.mark.parametrize('operation', ['read_header', 'read_payload', 'send_body', 'send_json', 'recv_json'])
def test_absolute_deadline_with_partial_progress(operation):
    clock = Clock()
    sock = SlowSocket(clock, frame(b'abcdefgh') + frame(b''))
    kwargs = dict(lifetime_seconds=2, idle_seconds=1, clock=clock)
    if operation == 'read_header':
        action = lambda: FramedReader(sock, 8, **kwargs).read(8)
    elif operation == 'read_payload':
        sock.step = 0.3
        action = lambda: FramedReader(sock, 8, **kwargs).read(8)
    elif operation == 'send_body':
        action = lambda: send_body(sock, io.BytesIO(b'abc'), 3, **kwargs)
    elif operation == 'send_json':
        action = lambda: send_json(sock, {'x': 1}, **kwargs)
    else:
        action = lambda: recv_json(sock, **kwargs)
    fails('E_IO', action)
    assert sock.timeout == 7
    assert all(0 < wait <= 1 for wait in sock.waits[::2])
    assert sock.waits[-2] < 1


def test_idle_timeout_real_socket_and_restore():
    left, right = socket.socketpair()
    with left, right:
        right.settimeout(4)
        fails('E_IO', FramedReader(right, 1, idle_seconds=0.01).read, 1)
        assert right.gettimeout() == 4


def test_socket_disconnect_without_terminal_and_broken_send():
    left, right = socket.socketpair()
    with left, right:
        left.sendall(frame(b'abc') + b'\0\0')
        left.shutdown(socket.SHUT_WR)
        reader = FramedReader(right, 3)
        assert reader.read(3) == b'abc'
        fails('E_IO', reader.read, 1)
    left, right = socket.socketpair()
    right.close()
    with left:
        fails('E_IO', send_body, left, io.BytesIO(b'abc'), 3)


def test_existing_shorter_timeout_is_preserved():
    clock = Clock()
    sock = SlowSocket(clock, frame(b''), step=0.01)
    sock.timeout = 0.1
    assert FramedReader(sock, 0, clock=clock).read(1) == b''
    assert sock.waits == [0.1] * 8


def test_idle_elapsed_and_cooperative_source_deadline():
    clock = Clock()
    reader = FramedReader(io.BytesIO(frame(b'ab') + frame(b'')), 2, idle_seconds=1, clock=clock)
    assert reader.read(1) == b'a'
    clock.now = 1
    fails('E_IO', reader.read, 1)

    class SlowSource:
        def read(self, n):
            clock.now += 301
            return b''

    output = io.BytesIO()
    fails('E_IO', send_body, output, SlowSource(), 1, clock=clock)
    assert output.getvalue() == b''


def test_check_callback_and_invalid_streams():
    calls = []
    send_body(Fragmented(), Fragmented(b'abc'), 3, check=lambda: calls.append(1))
    assert len(calls) >= 12

    def cancel():
        raise BookflowError('E_IO', 'cancelled')

    output = io.BytesIO()
    fails('E_IO', send_body, output, io.BytesIO(b'x'), 1, check=cancel)
    assert output.getvalue() == b''

    class BadSource:
        def read(self, n):
            return b'x' * (n + 1)

    fails('E_IO', send_body, output, BadSource(), 1)
    fails('E_IO', send_body, output, io.StringIO('bad'), 10)

    class BadSink:
        def write(self, data):
            return 0

    fails('E_IO', send_json, BadSink(), {})


@pytest.mark.parametrize('kwargs', [{'limit': -1}, {'limit': True}, {'limit': 1, 'lifetime_seconds': 301}, {'limit': 1, 'idle_seconds': 31}, {'limit': 1, 'idle_seconds': 0}, {'limit': 1, 'lifetime_seconds': float('nan')}])
def test_invalid_bounds(kwargs):
    fails('E_VALIDATION', FramedReader, io.BytesIO(), **kwargs)


@pytest.mark.parametrize("limit", [65537, 10**100, True])
def test_json_limits_cannot_expand_the_protocol_ceiling(limit):
    fails("E_VALIDATION", send_json, io.BytesIO(), {}, limit=limit)
    fails("E_VALIDATION", recv_json, io.BytesIO(frame(b"{}")), limit=limit)


@pytest.mark.parametrize("limit", [100000001, 10**100, True])
def test_body_limits_cannot_expand_the_ingress_ceiling(limit):
    fails("E_VALIDATION", FramedReader, io.BytesIO(), limit)
    fails("E_VALIDATION", send_body, io.BytesIO(), io.BytesIO(), limit)
