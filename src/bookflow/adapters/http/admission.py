"""uvicorn h11's actual transport boundary, with no await inside admission."""
from contextvars import ContextVar
from uvicorn.protocols.http.h11_impl import H11Protocol
from bookflow.core.publication_admission import AdmissionCancelled, FRAME_BYTES

_release = ContextVar('bookflow_transport_release', default=None)
MAX_HEADERS = 65536


class _Transport:
    def __init__(self, transport):
        self.raw = transport

    def __getattr__(self, name):
        return getattr(self.raw, name)

    def write(self, data):
        release = _release.get()
        if release is None:
            # Only protocol-owned 100/400/500/503 writes reach this path: the
            # ASGI callable below refuses application sends without a frame.
            return self.raw.write(data)
        gate, frame = release
        try:
            for offset in range(0, len(data), FRAME_BYTES):
                gate.transport_write(frame, self.raw, data[offset:offset + FRAME_BYTES])
                # The queued bytes are already transport-owned. No callback or
                # selector registration occurs under the admission mutex.
                self.raw._loop._add_writer(self.raw._sock_fd, self.raw._write_ready)
                self.raw._maybe_pause_protocol()
        except BaseException:
            if not self.raw.is_closing():
                self.raw.abort()  # never synthesize a successful chunk terminator
            raise


class AdmissionProtocol(H11Protocol):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        app = self.app

        async def bound_app(scope, receive, send):
            scope['bookflow.transport_admission'] = True

            async def bound_send(message):
                if _release.get() is None:
                    raise RuntimeError('application response missing admission frame')
                await send(message)
            await app(scope, receive, bound_send)
        self.app = bound_app

    def connection_made(self, transport):
        super().connection_made(transport)
        self.transport = _Transport(transport)


async def send_frame(send, message, gate, generation):
    """Called after guard validation. A suspended uvicorn drain holds no mutex."""
    frame = gate.admit(generation, message['type'])
    token = _release.set((gate, frame))
    try:
        await send(message)
        gate.finish(frame)
    finally:
        _release.reset(token)


def bounded_messages(message):
    if message['type'] == 'http.response.start':
        # Header values are already bytes; account for wire punctuation/status.
        if sum(len(k) + len(v) + 4 for k, v in message.get('headers', ())) + 256 > MAX_HEADERS:
            raise ValueError('response headers exceed transport bound')
        yield message
    elif message['type'] == 'http.response.body':
        data = message.get('body', b'')
        for offset in range(0, max(1, len(data)), FRAME_BYTES):
            final = offset + FRAME_BYTES >= len(data)
            yield {**message, 'body': data[offset:offset + FRAME_BYTES],
                   'more_body': message.get('more_body', False) if final else True}
    else:
        raise ValueError('unsupported response frame')
