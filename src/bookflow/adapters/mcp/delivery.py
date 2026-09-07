"""Continuous verified delivery, with no SQLite session held across socket writes."""

import hashlib
import time

import anyio
from starlette.concurrency import run_in_threadpool
from starlette.responses import StreamingResponse

from .catalog import BRIDGE_VERSION
from .framing import encode, invalid
from .intents import MIB


class Delivery(StreamingResponse):
    def __init__(self, runtime, intent, document, *, binary=None, recovery=False, rejection=False):
        self.runtime, self.intent = runtime, intent
        self.original_response = not recovery
        self.started_at = time.monotonic()
        if rejection:
            runtime.intents.reject_delivery(intent)
        elif not recovery:
            runtime.intents.delivery(intent)
        self.frames = self._frames(document, binary)
        super().__init__(self.frames, media_type="application/vnd.bookflow.mcp-records",
            headers={"Cache-Control": "no-store", "X-Bookflow-MCP-Version": str(BRIDGE_VERSION)})

    def _frames(self, document, binary):
        cache = bytearray()
        cacheable = True

        def capture(chunk):
            nonlocal cacheable
            if cacheable and len(cache) + len(chunk) <= MIB:
                cache.extend(chunk)
            else:
                cacheable = False
                cache.clear()

        def check():
            if self.intent.abandoned or time.monotonic() - self.started_at >= self.runtime.json_seconds:
                raise invalid("delivery_abandoned")
            if self.intent.completed is not None and self.runtime.intents.clock() - self.intent.completed >= 300:
                raise invalid("receipt_expired")
            document.check(original_response=self.original_response)

        def recovery():
            return self.runtime.intents.reserve_receipt(self.intent, bytes(cache) if cacheable else None,
                                                       document.permit.retained())

        yield from encode(document, check=check, operation_ref=self.intent.reference,
                          is_error=set(document) == {"code", "message", "details"},
                          binary=binary, recovery=recovery, on_json=capture)

    async def __call__(self, scope, receive, send):
        state = scope.get("bookflow.response_release")
        if state is not None:
            deadline = self.started_at + self.runtime.json_seconds
            state.deadline = deadline if state.deadline is None else min(state.deadline, deadline)
        with self.runtime.intents.lock:
            if self.runtime.intents.active.get(self.intent.reference) is not self.intent or self.intent.abandoned:
                raise invalid("delivery_abandoned")
            self.intent.workers += 1
        async def timed_send(message):
            remaining = self.runtime.json_seconds - (time.monotonic() - self.started_at)
            if remaining <= 0:
                raise invalid("delivery_deadline")
            with anyio.fail_after(min(30, remaining)):
                await send(message)
        try:
            await super().__call__(scope, receive, timed_send)
        finally:
            # Cancellation abandons delivery, not an executing host writer. This
            # owner exists only after run_hosted returned and released its session.
            with anyio.CancelScope(shield=True):
                await run_in_threadpool(self.frames.close)
                with self.runtime.intents.lock:
                    self.intent.workers -= 1
                await run_in_threadpool(self.runtime.intents.finish, self.intent)


def binary_chunks(transfer):
    digest, size = hashlib.sha256(), 0
    info = transfer.resource.info
    while True:
        transfer.check_output()
        chunk = transfer.reader.read(min(65536, info.size_bytes - size + 1))
        if not chunk:
            break
        size += len(chunk)
        if size > info.size_bytes:
            raise invalid("download_size")
        digest.update(chunk)
        yield chunk
    if size != info.size_bytes or digest.hexdigest() != info.sha256:
        raise invalid("download_digest")
