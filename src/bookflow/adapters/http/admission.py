"""uvicorn h11's actual transport boundary, with no await inside admission."""
from contextvars import ContextVar
import asyncio
import contextvars
import sys
import time
from dataclasses import dataclass
from urllib.parse import unquote
import h11
from uvicorn.protocols.http.h11_impl import (
    H11Protocol, RequestResponseCycle, HIGH_WATER_LIMIT, CLOSE_HEADER,
    STATUS_PHRASES, service_unavailable, get_client_addr,
)
from bookflow.core.publication_admission import ResponseRelease

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
        raise RuntimeError('business wire bypassed owned send cycle')


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
            try:
                await app(scope, receive, bound_send)
            except AdmissionCancelled:
                self.cycle.release_state.aborted = True
                self.transport.abort()
                raise
        self.app = bound_app

    def handle_events(self) -> None:
        while True:
            try:
                event = self.conn.next_event()
            except h11.RemoteProtocolError:
                msg = "Invalid HTTP request received."
                self.logger.warning(msg)
                self.send_400_response(msg)
                return

            if event is h11.NEED_DATA:
                break

            elif event is h11.PAUSED:
                # This case can occur in HTTP pipelining, so we need to
                # stop reading any more data, and ensure that at the end
                # of the active request/response cycle we handle any
                # events that have been buffered up.
                self.flow.pause_reading()
                break

            elif isinstance(event, h11.Request):
                self.headers = [(key.lower(), value) for key, value in event.headers]
                raw_path, _, query_string = event.target.partition(b"?")
                path = unquote(raw_path.decode("ascii"))
                full_path = self.root_path + path
                full_raw_path = self.root_path.encode("ascii") + raw_path
                self.scope = {
                    "type": "http",
                    "asgi": {"version": self.asgi_version, "spec_version": "2.3"},
                    "http_version": event.http_version.decode("ascii"),
                    "server": self.server,
                    "client": self.client,
                    "scheme": self.scheme,  # type: ignore[typeddict-item]
                    "method": event.method.decode("ascii"),
                    "root_path": self.root_path,
                    "path": full_path,
                    "raw_path": full_raw_path,
                    "query_string": query_string,
                    "headers": self.headers,
                    "state": self.app_state.copy(),
                }
                if self._should_upgrade():
                    self.handle_websocket_upgrade(event)
                    return

                # Handle 503 responses when 'limit_concurrency' is exceeded.
                if self.limit_concurrency is not None and (
                    len(self.connections) >= self.limit_concurrency or len(self.tasks) >= self.limit_concurrency
                ):
                    app = service_unavailable
                    message = "Exceeded concurrency limit."
                    self.logger.warning(message)
                else:
                    app = self.app

                # When starting to process a request, disable the keep-alive
                # timeout. Normally we disable this when receiving data from
                # client and set back when finishing processing its request.
                # However, for pipelined requests processing finishes after
                # already receiving the next request and thus the timer may
                # be set here, which we don't want.
                self._unset_keepalive_if_required()

                self.cycle = AdmissionCycle(
                    scope=self.scope,
                    conn=self.conn,
                    transport=self.transport,
                    flow=self.flow,
                    logger=self.logger,
                    access_logger=self.access_logger,
                    access_log=self.access_log,
                    default_headers=self.server_state.default_headers,
                    message_event=asyncio.Event(),
                    on_response=self.on_response_complete,
                )
                if self.config.reset_contextvars:
                    # Opt-in workaround for https://github.com/python/cpython/issues/140947:
                    # asyncio can leak context vars between tasks. Hides context set in the
                    # lifespan or by external instrumentation.
                    if sys.version_info >= (3, 11):  # pragma: py-lt-311
                        task = self.loop.create_task(self.cycle.run_asgi(app), context=contextvars.Context())
                    else:  # pragma: py-gte-311
                        task = contextvars.Context().run(self.loop.create_task, self.cycle.run_asgi(app))
                else:
                    task = self.loop.create_task(self.cycle.run_asgi(app))
                task.add_done_callback(self.tasks.discard)
                self.tasks.add(task)

            elif isinstance(event, h11.Data):
                if self.conn.our_state is h11.DONE:
                    continue
                self.cycle.body += event.data
                if len(self.cycle.body) > HIGH_WATER_LIMIT:
                    self.flow.pause_reading()
                self.cycle.message_event.set()

            elif isinstance(event, h11.EndOfMessage):
                if self.conn.our_state is h11.DONE:
                    self.transport.resume_reading()
                    self.conn.start_next_cycle()
                    continue
                self.cycle.more_body = False
                self.cycle.message_event.set()
                if self.conn.their_state == h11.MUST_CLOSE:
                    break

    def connection_made(self, transport):
        super().connection_made(transport)
        self.transport = _Transport(transport)


@dataclass
class ReleaseAttempt:
    gate: object
    generation: object
    response: ResponseRelease
    validate: object
    cancelled: object
    before_wait: object = None
    after_wait: object = None

    async def refresh(self):
        self.response.retry()
        while True:
            if self.before_wait is not None:
                await self.before_wait()
            await self.gate.wait_open(self.response, self.cancelled)
            try:
                generation = self.gate.begin_validation()
                if self.after_wait is not None:
                    await self.after_wait()
                await self.validate()
                self.response.retry()
                self.gate.finish(self.gate.admit(generation, 'revalidated'))
                self.generation = generation
                return
            except AdmissionCancelled:
                self.response.retry()

    async def drain(self, flow):
        while flow.write_paused:
            if self.cancelled():
                raise ConnectionAbortedError('response disconnected or host stopping')
            try:
                self.gate.finish(self.gate.admit(self.generation, 'http readiness'))
            except AdmissionCancelled:
                if self.validate is None:
                    raise
                await self.refresh()
            try:
                await asyncio.wait_for(flow.drain(), .05)
            except TimeoutError:
                pass

    async def wire(self, transport, data):
        for offset in range(0, max(1, len(data)), FRAME_BYTES):
            chunk = data[offset:offset + FRAME_BYTES]
            while True:
                try:
                    if self.cancelled():
                        raise ConnectionAbortedError('response disconnected or host stopping')
                    frame = self.gate.admit(self.generation, 'http wire', self.response)
                    self.gate.transport_write(frame, transport.raw, chunk)
                    transport.raw._loop._add_writer(transport.raw._sock_fd, transport.raw._write_ready)
                    transport.raw._maybe_pause_protocol()
                    self.gate.finish(frame)
                    break
                except AdmissionCancelled:
                    if self.validate is None:
                        raise
                    await self.refresh()


async def send_frame(send, message, gate, generation, *, response=None, validate=None, cancelled=lambda: False, before_wait=None, after_wait=None):
    response = response or ResponseRelease()
    response.start()
    release = ReleaseAttempt(gate, generation, response, validate, cancelled, before_wait, after_wait)
    token = _release.set(release)
    try:
        await send(message)
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


class AdmissionCycle(RequestResponseCycle):
    """One pending bounded h11 part; completion follows admitted wire, never encoding."""
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.release_state = ResponseRelease()
        self.scope['bookflow.response_release'] = self.release_state
        self.scope['bookflow.disconnected'] = lambda: self.disconnected

    async def send(self, message):
        release = _release.get()
        if release is None:
            # An expired/cancelled business release cannot acquire a replacement
            # terminal via uvicorn's exception handler, even before encoding.
            if self.release_state.aborted:
                return
            # Only uvicorn's fixed protocol-owned500/503 bypass bound_app.
            return await super().send(message)
        try:
            if self.flow.write_paused and not self.disconnected:
                await release.drain(self.flow)
            if self.disconnected:
                raise ConnectionAbortedError('response disconnected')
            if not self.response_started:
                if message['type'] != 'http.response.start':
                    raise RuntimeError('Expected response start')
                headers = self.default_headers + list(message.get('headers', []))
                if CLOSE_HEADER in self.scope['headers'] and CLOSE_HEADER not in headers:
                    headers = headers + [CLOSE_HEADER]
                # Includes default headers and status-line allowance before h11
                # mutates state, unlike the application-only header check.
                if sum(len(k) + len(v) + 4 for k, v in headers) + 256 > MAX_HEADERS:
                    raise ValueError('response headers exceed transport bound')
                status = message['status']
                output = self.conn.send(h11.Response(status_code=status, headers=headers, reason=STATUS_PHRASES[status]))
                self.response_started = True
                self.waiting_for_100_continue = False
                await release.wire(self.transport, output)
                if self.access_log:
                    self.access_logger.info('%s - "%s %s HTTP/%s" %d', get_client_addr(self.scope),
                        self.scope['method'], self.scope['path'], self.scope['http_version'], status)
            elif not self.response_complete:
                if message['type'] != 'http.response.body':
                    raise RuntimeError('Expected response body')
                body = message.get('body', b'')
                if len(body) > FRAME_BYTES:
                    raise ValueError('response body exceeds transport bound')
                output = self.conn.send(h11.Data(data=b'' if self.scope['method'] == 'HEAD' else body))
                await release.wire(self.transport, output)
                if not message.get('more_body', False):
                    output = self.conn.send(h11.EndOfMessage())
                    await release.wire(self.transport, output)
                    self.response_complete = True
                    self.message_event.set()
            else:
                raise RuntimeError('Response already completed')
            if self.response_complete:
                if self.conn.our_state is h11.MUST_CLOSE or not self.keep_alive:
                    self.conn.send(h11.ConnectionClosed())
                    self.transport.close()
                self.on_response()
        except BaseException:
            self.release_state.aborted = True
            self.transport.abort()
            raise

    async def receive(self):
        if self.waiting_for_100_continue and not self.transport.is_closing():
            output = self.conn.send(h11.InformationalResponse(status_code=100, headers=[], reason='Continue'))
            self.transport.raw.write(output)
            self.release_state.interim_accepted += len(output)
            self.waiting_for_100_continue = False
        return await super().receive()
