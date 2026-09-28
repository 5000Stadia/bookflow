"""R29: an event stream releases its host resources promptly when its connection ends.

Served through the production AdmissionProtocol. Each case is driven by a commit or
by a barrier inside the stream's own publication check, never by waiting out a timer;
the only clocks are the bounds being asserted.
"""
import socket
import struct
import threading
import time

import pytest
import uvicorn

from tests.test_row3_host import hosted  # noqa: F401  (fixture)

PROMPT = 1.5  # seconds: "within about a second" plus scheduling slack on a loaded machine


@pytest.fixture
def served(hosted):
    from bookflow.adapters.http.admission import AdmissionProtocol
    sock = socket.socket()
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    sock.listen(64)
    server = uvicorn.Server(uvicorn.Config(hosted.handle.app, http=AdmissionProtocol, loop="asyncio",
                                           log_level="critical", access_log=False))
    thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
    thread.start()
    deadline = time.monotonic() + 30
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.02)
    assert server.started
    try:
        yield sock.getsockname()[1], server
    finally:
        server.should_exit = True
        thread.join(10)
        sock.close()


def _open(port, path, secret):
    """A raw stream connection, returned once the host reports its first idle wait."""
    conn = socket.create_connection(("127.0.0.1", port))
    conn.sendall(f"GET {path} HTTP/1.1\r\nHost: t\r\nAuthorization: Bearer {secret}\r\n\r\n".encode())
    conn.settimeout(60)
    seen = b""
    while b": ready" not in seen:
        chunk = conn.recv(65536)
        assert chunk, seen
        seen += chunk
    return conn


def _released(host, bound):
    began = time.monotonic()
    while (host._subscriptions or host._readers_attached) and time.monotonic() - began < bound:
        time.sleep(0.01)
    return not host._subscriptions and not host._readers_attached


def _read_to_end(conn):
    """Everything after the ready comment, and how long the host took to close."""
    began, rest = time.monotonic(), b""
    try:
        while chunk := conn.recv(65536):
            rest += chunk
    except OSError:
        pass
    return rest, time.monotonic() - began


@pytest.mark.parametrize("scope", ["hub", "company"])
def test_revoked_stream_closes_without_data_and_releases_at_once(hosted, served, scope):
    port, _ = served
    host = hosted.handle.host
    path = "/hub-events" if scope == "hub" else f"/companies/{hosted.company_id}/events"
    issued = hosted.ok("token.issue", {"label": "stream-" + scope})
    conn = _open(port, path, issued["secret"])
    try:
        assert host._subscriptions
        hosted.ok("token.revoke", {"token": issued["token_id"]})  # the only commit: nothing else wakes it
        rest, took = _read_to_end(conn)
    finally:
        conn.close()
    # The c116267 contract: a revoked stream closes with no further data.
    assert b"data:" not in rest and b"event: audit" not in rest, rest
    assert took < PROMPT, f"closed after {took:.2f}s"
    assert _released(host, PROMPT), (host._subscriptions, host._readers_attached)


def test_peer_gone_during_a_frame_send_releases_the_stream(hosted, served, monkeypatch):
    """The peer leaves while a frame is inside its publication check; the send then fails."""
    from bookflow.adapters.http import execution
    port, server = served
    host = hosted.handle.host
    conn = _open(port, f"/companies/{hosted.company_id}/events", hosted.secret)
    entered, release = threading.Event(), threading.Event()
    real = execution.PublishedDocument.check

    def held(self, **kwargs):
        # Only the stream's woken batch: the write's own receipt passes untouched.
        if self.permit.cmd.name == "audit tail" and self.get("items") and not release.is_set():
            entered.set()
            assert release.wait(30)
        return real(self, **kwargs)

    monkeypatch.setattr(execution.PublishedDocument, "check", held)
    writer = threading.Thread(target=lambda: hosted.ok("company.update", {"fax": "555-2929"},
                                                       company=hosted.company_id))
    writer.start()
    try:
        assert entered.wait(60), "the woken stream never reached its frame's publication check"
        conn.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
        conn.close()
        deadline = time.monotonic() + 30
        while server.server_state.connections and time.monotonic() < deadline:
            time.sleep(0.01)  # until the host has processed the peer's reset
        assert not server.server_state.connections
    finally:
        release.set()
    began = time.monotonic()
    assert _released(host, PROMPT), (host._subscriptions, host._readers_attached)
    assert time.monotonic() - began < PROMPT
    writer.join(60)


def test_idle_stream_peer_close_releases_the_stream(hosted, served):
    port, _ = served
    host = hosted.handle.host
    conn = _open(port, "/hub-events", hosted.secret)
    assert host._subscriptions
    conn.close()
    assert _released(host, PROMPT), (host._subscriptions, host._readers_attached)
