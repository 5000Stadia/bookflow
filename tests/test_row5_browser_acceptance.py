"""Real-browser acceptance for the responsive Row 5 workbench journey."""

from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import threading
import time
from types import SimpleNamespace
from urllib.parse import urlsplit
from urllib.request import urlopen

import pytest
import uvicorn

import bookflow
from bookflow.commands.host_cmds import start_serving
from bookflow.core.config import os_login
from bookflow.core.context import client_version


PASSWORD = "correct-horse-battery"
CHROME = Path(os.environ.get("BOOKFLOW_TEST_CHROME", "/opt/google/chrome/chrome"))


class _Cdp:
    """The small subset of the Chrome DevTools Protocol used by this witness."""

    def __init__(self, profile: Path):
        self._process = subprocess.Popen(
            [
                str(CHROME),
                "--headless=new",
                "--no-sandbox",
                "--disable-gpu",
                "--disable-dev-shm-usage",
                "--disable-background-networking",
                "--disable-default-apps",
                "--disable-extensions",
                "--disable-sync",
                "--metrics-recording-only",
                "--no-default-browser-check",
                "--no-first-run",
                "--remote-debugging-port=0",
                f"--user-data-dir={profile}",
                "--window-size=1280,800",
                "about:blank",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        active_port = profile / "DevToolsActivePort"
        deadline = time.monotonic() + 10
        while not active_port.exists() and time.monotonic() < deadline:
            if self._process.poll() is not None:
                raise AssertionError(f"Chrome exited during startup ({self._process.returncode})")
            time.sleep(0.02)
        assert active_port.exists(), "Chrome did not publish its debugging port"
        port = int(active_port.read_text().splitlines()[0])

        target = None
        deadline = time.monotonic() + 5
        while target is None and time.monotonic() < deadline:
            try:
                with urlopen(f"http://127.0.0.1:{port}/json/list", timeout=1) as response:
                    targets = json.load(response)
                target = next((entry for entry in targets if entry.get("type") == "page"), None)
            except (OSError, ValueError):
                time.sleep(0.02)
        assert target is not None, "Chrome did not expose a page target"

        endpoint = urlsplit(target["webSocketDebuggerUrl"])
        self._socket = socket.create_connection((endpoint.hostname, endpoint.port), timeout=5)
        self._buffer = bytearray()
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        request = (
            f"GET {endpoint.path} HTTP/1.1\r\n"
            f"Host: {endpoint.hostname}:{endpoint.port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        ).encode("ascii")
        self._socket.sendall(request)
        response = self._read_through(b"\r\n\r\n")
        head, rest = response.split(b"\r\n\r\n", 1)
        assert head.startswith(b"HTTP/1.1 101"), head.decode("latin-1", "replace")
        expected = base64.b64encode(
            hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()
        ).decode()
        assert f"sec-websocket-accept: {expected}".lower() in head.decode().lower()
        self._buffer.extend(rest)
        self._next_id = 0
        self.call("Page.enable")
        self.call("Runtime.enable")
        self.call("Network.enable")

    def _read_through(self, marker: bytes) -> bytes:
        data = bytearray()
        while marker not in data:
            chunk = self._socket.recv(65536)
            if not chunk:
                raise AssertionError("Chrome closed the debugging connection")
            data.extend(chunk)
        return bytes(data)

    def _read_exact(self, length: int) -> bytes:
        while len(self._buffer) < length:
            chunk = self._socket.recv(max(65536, length - len(self._buffer)))
            if not chunk:
                raise AssertionError("Chrome closed the debugging connection")
            self._buffer.extend(chunk)
        result = bytes(self._buffer[:length])
        del self._buffer[:length]
        return result

    def _send_frame(self, payload: bytes, *, opcode: int = 1) -> None:
        first = 0x80 | opcode
        length = len(payload)
        if length < 126:
            header = struct.pack("!BB", first, 0x80 | length)
        elif length < 65536:
            header = struct.pack("!BBH", first, 0x80 | 126, length)
        else:
            header = struct.pack("!BBQ", first, 0x80 | 127, length)
        mask = os.urandom(4)
        masked = bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
        self._socket.sendall(header + mask + masked)

    def _receive_message(self) -> bytes:
        message = bytearray()
        while True:
            first, second = self._read_exact(2)
            final, opcode = bool(first & 0x80), first & 0x0F
            length = second & 0x7F
            if length == 126:
                length = struct.unpack("!H", self._read_exact(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", self._read_exact(8))[0]
            mask = self._read_exact(4) if second & 0x80 else None
            payload = self._read_exact(length)
            if mask:
                payload = bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
            if opcode == 8:
                raise AssertionError("Chrome closed the debugging target")
            if opcode == 9:
                self._send_frame(payload, opcode=10)
                continue
            if opcode in (1, 2, 0):
                message.extend(payload)
                if final:
                    return bytes(message)

    def call(self, method: str, params: dict | None = None, *, timeout: float = 15) -> dict:
        self._next_id += 1
        call_id = self._next_id
        self._send_frame(json.dumps({"id": call_id, "method": method, "params": params or {}}).encode())
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self._socket.settimeout(max(0.1, deadline - time.monotonic()))
            message = json.loads(self._receive_message())
            if message.get("id") != call_id:
                continue
            if "error" in message:
                raise AssertionError(f"CDP {method} failed: {message['error']}")
            return message.get("result", {})
        raise AssertionError(f"CDP {method} timed out")

    def evaluate(self, expression: str, *, await_promise: bool = False, timeout: float = 15):
        result = self.call(
            "Runtime.evaluate",
            {
                "expression": expression,
                "awaitPromise": await_promise,
                "returnByValue": True,
            },
            timeout=timeout,
        )
        assert "exceptionDetails" not in result, result.get("exceptionDetails")
        return result["result"].get("value")

    def wait_for(self, expression: str, *, timeout: float = 15):
        deadline = time.monotonic() + timeout
        last_error = None
        while time.monotonic() < deadline:
            try:
                value = self.evaluate(expression)
                if value:
                    return value
            except (AssertionError, OSError, socket.timeout) as error:
                last_error = error
            time.sleep(0.04)
        try:
            state = self.evaluate(
                "({href: location.href, ready: document.readyState, text: document.body?.innerText.slice(0, 500)})"
            )
        except (AssertionError, OSError, socket.timeout):
            state = None
        raise AssertionError(
            f"browser condition timed out: {expression}; last error={last_error}; state={state}"
        )

    def viewport(self, width: int, height: int) -> None:
        self.call(
            "Emulation.setDeviceMetricsOverride",
            {
                "width": width,
                "height": height,
                "deviceScaleFactor": 1,
                "mobile": width <= 700,
            },
        )

    def navigate(self, url: str) -> None:
        self.call("Page.navigate", {"url": url})
        self.wait_for(f"document.readyState === 'complete' && location.href === {json.dumps(url)}")

    def close(self) -> None:
        try:
            self.call("Browser.close", timeout=3)
        except (AssertionError, OSError, socket.timeout):
            pass
        try:
            self._socket.close()
        except OSError:
            pass
        try:
            self._process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self._process.terminate()
            self._process.wait(timeout=5)


@pytest.fixture
def browser_site(tmp_path, monkeypatch):
    """Serve a fresh demo root on an OS-selected loopback port."""
    data_root = tmp_path / "browser-root"
    monkeypatch.setenv("BOOKFLOW_DATA_ROOT", str(data_root))
    monkeypatch.delenv("BOOKFLOW_COMPANY", raising=False)
    client = bookflow.connect(data_root=str(data_root))
    client.init()
    client.demo.reset()
    login = os_login()
    client.run("user set-password", {"username": login, "password": PASSWORD})
    company_id = client.company.list()["items"][0]["company_id"]

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(64)
    port = listener.getsockname()[1]
    handle = start_serving(
        data_root,
        client_version(),
        bind=f"127.0.0.1:{port}",
        secure_cookies=False,
        publish_descriptor=False,
    )
    server = uvicorn.Server(
        uvicorn.Config(handle.app, log_level="warning", access_log=False)
    )
    thread = threading.Thread(target=lambda: server.run(sockets=[listener]), daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.02)
    assert server.started, "the browser-acceptance host did not start"
    try:
        yield SimpleNamespace(
            base_url=f"http://127.0.0.1:{port}",
            company_id=company_id,
            login=login,
        )
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        try:
            listener.close()
        except OSError:
            pass
        handle.stop()


def _assert_navigation_contained(browser: _Cdp) -> None:
    overflow = browser.evaluate(
        """
        (() => {
          const failures = [];
          for (const card of document.querySelectorAll('.nav-group')) {
            const bounds = card.getBoundingClientRect();
            for (const element of card.querySelectorAll('h2,h3,a')) {
              for (const box of element.getClientRects()) {
                if (box.left < bounds.left || box.right > bounds.right
                    || box.top < bounds.top || box.bottom > bounds.bottom
                    || box.left < 0 || box.right > innerWidth) {
                  failures.push({text: element.textContent.trim(), width: innerWidth});
                }
              }
            }
          }
          return failures;
        })()
        """
    )
    assert overflow == [], overflow


def _assert_rendered_page(
    browser: _Cdp, label: str, *, viewport: tuple[int, int]
) -> None:
    layout = browser.evaluate(
        """
        (() => {
          const shown = element => {
            const style = getComputedStyle(element);
            return !element.hidden && style.display !== 'none' && style.visibility !== 'hidden'
              && element.getClientRects().length > 0;
          };
          const describe = element => {
            const name = element.getAttribute('name') || element.textContent.trim().slice(0, 50);
            return `${element.tagName.toLowerCase()}${name ? `[${name}]` : ''}`;
          };
          const controls = Array.from(document.querySelectorAll(
            'a[href],button,input:not([type="hidden"]),select,textarea,[role="button"]'
          )).filter(shown);
          const outside = controls.filter(element => {
            const box = element.getBoundingClientRect();
            return box.left < -1 || box.right > innerWidth + 1;
          }).map(describe);
          const unavailable = controls.filter(element =>
            element.matches('button:disabled,a[aria-disabled="true"],[role="button"][aria-disabled="true"]')
          ).map(describe);
          return {
            href: location.href,
            title: document.title,
            width: innerWidth,
            height: innerHeight,
            bodyOverflow: Math.max(document.body.scrollWidth, document.documentElement.scrollWidth) - innerWidth,
            outside,
            unavailable,
            errors: Array.from(document.querySelectorAll('.error')).filter(shown).map(element => element.textContent.trim()),
          };
        })()
        """
    )
    assert (layout["width"], layout["height"]) == viewport, (label, layout)
    assert layout["bodyOverflow"] <= 1, (label, layout)
    assert layout["outside"] == [], (label, layout)
    assert layout["unavailable"] == [], (label, layout)
    assert layout["errors"] == [], (label, layout)
    _assert_navigation_contained(browser)

    broken = browser.evaluate(
        """
        (async () => {
          const shown = element => {
            const style = getComputedStyle(element);
            return !element.hidden && style.display !== 'none' && style.visibility !== 'hidden'
              && element.getClientRects().length > 0;
          };
          const urls = [...new Set(Array.from(document.querySelectorAll('a[href]'))
            .filter(shown)
            .map(element => new URL(element.href, location.href))
            .filter(url => url.origin === location.origin && ['http:', 'https:'].includes(url.protocol))
            .map(url => url.href))];
          const failures = [];
          for (const url of urls) {
            try {
              const response = await fetch(url, {credentials: 'same-origin', redirect: 'follow'});
              if (!response.ok) failures.push({url, status: response.status});
            } catch (error) {
              failures.push({url, error: String(error)});
            }
          }
          return failures;
        })()
        """,
        await_promise=True,
        timeout=30,
    )
    assert broken == [], (label, broken)


@pytest.mark.skipif(not CHROME.is_file(), reason="real Chrome is not installed")
@pytest.mark.parametrize(("width", "height"), [(360, 800), (1280, 800)])
def test_row5_login_list_detail_form_preview_and_audit_in_real_chrome(
    browser_site, tmp_path, width, height
):
    browser = _Cdp(tmp_path / f"chrome-{width}")
    try:
        browser.viewport(width, height)
        viewport = (width, height)
        login_url = f"{browser_site.base_url}/login"
        browser.navigate(login_url)
        _assert_rendered_page(browser, "login", viewport=viewport)

        browser.evaluate(
            f"""
            (() => {{
              document.querySelector('[name="username"]').value = {json.dumps(browser_site.login)};
              document.querySelector('[name="password"]').value = {json.dumps(PASSWORD)};
              document.querySelector('form[hx-post="/login"]').requestSubmit();
            }})()
            """
        )
        company_home = f"{browser_site.base_url}/c/{browser_site.company_id}/"
        browser.wait_for(f"location.href === {json.dumps(company_home)}")
        browser.wait_for("document.readyState === 'complete' && !!document.querySelector('.group-grid')")
        _assert_rendered_page(browser, "company home", viewport=viewport)
        if width == 1280:
            for home in (company_home, f"{browser_site.base_url}/hub/"):
                browser.navigate(home)
                browser.wait_for("document.readyState === 'complete' && !!document.querySelector('.group-grid')")
                for card_width in (280, 320, 390, 700, 701, 820, 1024, 1280):
                    browser.viewport(card_width, height)
                    _assert_navigation_contained(browser)
            browser.viewport(width, height)
            browser.navigate(company_home)
            browser.wait_for("document.readyState === 'complete' && !!document.querySelector('.group-grid')")

        customer_list = f"/c/{browser_site.company_id}/customer"
        browser.evaluate(
            f"document.querySelector('a[href={json.dumps(customer_list)}]').click()"
        )
        browser.wait_for("document.readyState === 'complete' && !!document.querySelector('.list-tools')")
        _assert_rendered_page(browser, "customer list", viewport=viewport)

        browser.evaluate(
            """
            (() => {
              const form = document.querySelector('.list-tools');
              form.querySelector('[name="query"]').value = 'Riverside';
              form.requestSubmit();
            })()
            """
        )
        browser.wait_for("location.search.includes('query=Riverside') && document.body.innerText.includes('Riverside Apartments')")
        _assert_rendered_page(browser, "searched customer list", viewport=viewport)

        browser.evaluate("document.querySelector('.table-wrap table a').click()")
        browser.wait_for("document.readyState === 'complete' && document.querySelector('h1')?.textContent.includes('Riverside Apartments')")
        _assert_rendered_page(browser, "customer detail", viewport=viewport)

        browser.evaluate("document.querySelector('.actions a[href$=\"/update\"]').click()")
        browser.wait_for("document.readyState === 'complete' && !!document.querySelector('[data-generated-form]')")
        _assert_rendered_page(browser, "customer update form", viewport=viewport)

        update_url = browser.evaluate("location.href")
        audit_path = f"/c/{browser_site.company_id}/audit"
        browser.evaluate(f"document.querySelector('header a[href={json.dumps(audit_path)}]').click()")
        browser.wait_for("document.readyState === 'complete' && document.querySelector('h1')?.textContent.trim() === 'Audit'")
        _assert_rendered_page(browser, "company audit", viewport=viewport)

        browser.navigate(update_url)
        browser.wait_for("!!document.querySelector('[data-generated-form]')")
        browser.evaluate("document.querySelector('button[value=\"preview\"]').click()")
        browser.wait_for("document.body.innerText.includes('Preview (nothing written)')")
        _assert_rendered_page(browser, "customer update preview", viewport=viewport)
    finally:
        browser.close()
