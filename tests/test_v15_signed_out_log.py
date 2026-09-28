"""V1.5 trials: a signed-out request redirected to the login page is not an internal failure.

A client that has the whole 303 once its headers arrive (content-length 0, or a HEAD) may close
or move on before the host writes the empty body frame. There is nothing left to deliver, so
the host must not log that as an internal failure. Served through the production
AdmissionProtocol, the way `bookflow serve` runs.
"""
import logging
import socket
import threading
import time

import pytest
import uvicorn

from tests.test_row3_host import hosted  # noqa: F401  (fixture)


@pytest.fixture
def served(hosted):
    from bookflow.adapters.http.admission import AdmissionProtocol
    sock = socket.socket()
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    sock.listen(64)
    server = uvicorn.Server(uvicorn.Config(hosted.handle.app, http=AdmissionProtocol, loop="asyncio",
                                           log_level="warning", access_log=False))
    thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
    thread.start()
    deadline = time.monotonic() + 30
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.02)
    assert server.started
    try:
        yield sock.getsockname()[1]
    finally:
        server.should_exit = True
        thread.join(10)
        sock.close()


def _headers_then_close(port, method, path):
    """Read only the status and headers, then hang up, as curl -I or a redirect-follower may."""
    conn = socket.create_connection(("127.0.0.1", port))
    conn.sendall(f"{method} {path} HTTP/1.1\r\nHost: t\r\n\r\n".encode())
    conn.settimeout(30)
    seen = b""
    while b"\r\n\r\n" not in seen:
        chunk = conn.recv(65536)
        assert chunk, seen
        seen += chunk
    conn.close()
    return seen


def test_signed_out_redirects_log_no_internal_failure(served, caplog):
    caplog.set_level(logging.WARNING)
    for method in ("HEAD", "GET") * 4:
        reply = _headers_then_close(served, method, "/")
        if method == "GET":
            assert reply.startswith(b"HTTP/1.1 303"), reply[:200]
            assert b"location: /login" in reply.lower()
    time.sleep(0.5)
    failures = [r for r in caplog.records if r.levelno >= logging.ERROR or "internal failure" in r.getMessage().lower()]
    assert not failures, [r.getMessage() + (logging.Formatter().formatException(r.exc_info) if r.exc_info else "") for r in failures]
